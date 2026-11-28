"""Step 5 safety: upload limits, rate limits, PUBLIC_DEMO, the demo PIN, CORS and site serving."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from backend import config
from backend.app import create_app
from backend.config import Settings
from backend.extraction import Extractor, GeminiProvider
from backend.seed import FEE_WAIVER, SCHOLARSHIP, seed_database
from backend.web_safety import MAX_UPLOAD_BYTES, PNG_SIGNATURE, RateLimiter, UploadRejected, check_upload, pin_matches
from conftest import FakeGemini, faithful_reply, manifest_doc, sha256_of, upload

TEST_PIN = "test-pin-4821"  # a test value for these tests only
SAMPLE_PATH, SAMPLE_DOC = manifest_doc("seed-approve", "marksheet")


def make_client(settings: Settings, fake: FakeGemini | None = None, *, use_cache: bool = False, **changes) -> TestClient:
    settings = dataclasses.replace(settings, **changes)
    extractor = None
    if fake is not None:
        provider = GeminiProvider(api_key="test-key", model="gemini-test", client=fake)
        extractor = Extractor(provider, use_cache=use_cache, cache_dir=settings.cache_dir, sleep=lambda _s: None)
    return TestClient(create_app(settings, extractor=extractor))


@pytest.fixture
def taught_fake() -> FakeGemini:
    fake = FakeGemini()
    fake.replies[sha256_of(SAMPLE_PATH)] = faithful_reply(SAMPLE_DOC)
    return fake


def seed(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        seed_database(session)
        session.commit()


def post_file(client: TestClient, content: bytes, name: str = "doc.png", media: str = "image/png", prefix: str = "/api"):
    return client.post(f"{prefix}/profiles/p1/documents", data={"document_type": "marksheet"},
                       files={"file": (name, content, media)})


# --- Upload limits ----------------------------------------------------------------------


def test_check_upload_accepts_png_and_jpg_only():
    assert check_upload(PNG_SIGNATURE + b"rest") == "png"
    assert check_upload(b"\xff\xd8\xff\xe0rest") == "jpg"
    for content in (b"GIF89a....", b"RIFF....WEBPVP8 ", b"%PDF-1.7", b"just text"):
        with pytest.raises(UploadRejected) as exc:
            check_upload(content)
        assert exc.value.status_code == 422
        assert "Only PNG or JPG images are accepted" in str(exc.value)


def test_check_upload_size_limit_and_empty_file():
    with pytest.raises(UploadRejected) as exc:
        check_upload(PNG_SIGNATURE + b"0" * MAX_UPLOAD_BYTES)
    assert exc.value.status_code == 413 and "larger than 3 MB" in str(exc.value)
    with pytest.raises(UploadRejected) as exc:
        check_upload(b"")
    assert exc.value.status_code == 422 and "empty" in str(exc.value)


@pytest.mark.parametrize("prefix", ["/api", ""])
def test_api_refuses_oversized_and_unsupported_files_with_clear_messages(settings, prefix):
    with make_client(settings) as client:
        just_over = post_file(client, PNG_SIGNATURE + b"0" * MAX_UPLOAD_BYTES, prefix=prefix)
        assert just_over.status_code == 413
        assert "larger than 3 MB" in just_over.json()["detail"]

        far_over = post_file(client, PNG_SIGNATURE + b"0" * (5 * 1024 * 1024), prefix=prefix)
        assert far_over.status_code == 413  # refused before the body is read

        gif = post_file(client, b"GIF89a" + b"\x00" * 64, name="doc.gif", media="image/gif", prefix=prefix)
        assert gif.status_code == 422
        assert gif.json()["detail"].startswith("Only PNG or JPG images are accepted")

        pdf_named_png = post_file(client, b"%PDF-1.7 ...", name="doc.png", prefix=prefix)
        assert pdf_named_png.status_code == 422  # the content decides, not the file name

        broken_png = post_file(client, PNG_SIGNATURE + b"not really a png", prefix=prefix)
        assert broken_png.status_code == 422
        assert "PNG or JPG" in broken_png.json()["detail"]
        for response in (just_over, far_over, gif, broken_png):
            assert "Traceback" not in response.text


def test_a_real_png_under_the_limit_is_accepted(settings, taught_fake):
    with make_client(settings, taught_fake) as client:
        response = upload(client, "p1", SAMPLE_PATH, "marksheet")
        assert response.status_code == 201, response.text
        assert response.json()["extraction"]["extraction_source"] == "live"


# --- Rate limiting ----------------------------------------------------------------------


def test_rate_limiter_is_a_sliding_window_per_key():
    now = [1000.0]
    limiter = RateLimiter(2, window=60, clock=lambda: now[0])
    assert limiter.hit("a") is None and limiter.hit("a") is None
    assert limiter.hit("a") == 60
    assert limiter.hit("b") is None  # another address has its own allowance
    now[0] += 30
    assert limiter.blocked("a") == 30
    assert limiter.blocked("a") == 30  # checking does not use up the allowance
    now[0] += 30.5
    assert limiter.blocked("a") is None
    assert limiter.hit("a") is None


def test_uploads_are_rate_limited_per_ip_across_both_paths(settings, taught_fake):
    with make_client(settings, taught_fake, uploads_per_minute=2) as client:
        assert upload(client, "p1", SAMPLE_PATH, "marksheet").status_code == 201
        assert post_file(client, SAMPLE_PATH.read_bytes(), prefix="/api").status_code == 201
        limited = post_file(client, SAMPLE_PATH.read_bytes(), prefix="/api")
        assert limited.status_code == 429
        assert int(limited.headers["Retry-After"]) >= 1
        assert "Too many uploads" in limited.json()["detail"]
        assert upload(client, "p1", SAMPLE_PATH, "marksheet").status_code == 429


def test_applications_are_rate_limited_and_reset_after_the_window(settings):
    with make_client(settings, applications_per_minute=2) as client:
        seed(client)
        limiter = client.app.state.limits["apply"]
        now = [0.0]
        limiter.clock = lambda: now[0]

        def apply(scheme):
            return client.post("/api/profiles/seed-below-marks/applications", json={"scheme_id": scheme})

        assert apply(SCHOLARSHIP).status_code == 201
        assert apply(FEE_WAIVER).status_code == 201
        limited = apply(FEE_WAIVER)
        assert limited.status_code == 429 and "Too many applications" in limited.json()["detail"]
        now[0] += 61
        assert apply(FEE_WAIVER).status_code == 201


# --- PUBLIC_DEMO ------------------------------------------------------------------------


@pytest.fixture
def clean_env(monkeypatch):
    """Settings.from_env without the repo's .env file or inherited variables."""
    monkeypatch.setattr(config, "load_env_file", lambda *a, **k: None)
    for name in ("PUBLIC_DEMO", "DEMO_PIN", "EXTRACTION_USE_CACHE", "CORS_ORIGINS",
                 "RATE_LIMIT_UPLOADS_PER_MINUTE", "RATE_LIMIT_APPLICATIONS_PER_MINUTE"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_public_demo_defaults_to_the_cache_unless_told_otherwise(clean_env):
    assert Settings.from_env().public_demo is False
    assert Settings.from_env().extraction_use_cache is False
    clean_env.setenv("PUBLIC_DEMO", "1")
    assert Settings.from_env().extraction_use_cache is True
    clean_env.setenv("EXTRACTION_USE_CACHE", "0")
    assert Settings.from_env().extraction_use_cache is False


def test_pin_and_cors_settings_from_env_never_show_the_pin(clean_env):
    clean_env.setenv("DEMO_PIN", TEST_PIN)
    clean_env.setenv("CORS_ORIGINS", "https://a.example, https://b.example/ ,*")
    clean_env.setenv("RATE_LIMIT_UPLOADS_PER_MINUTE", "not a number")
    settings = Settings.from_env()
    assert settings.demo_pin == TEST_PIN
    assert TEST_PIN not in repr(settings)
    assert settings.cors_origins == ("https://a.example", "https://b.example")  # "*" is ignored
    assert settings.uploads_per_minute == config.DEFAULT_UPLOADS_PER_MINUTE


def test_status_reports_public_demo_flags_but_never_the_pin(settings):
    with make_client(settings, public_demo=True, demo_pin=TEST_PIN) as client:
        body = client.get("/api/status").json()
    assert body["public_demo"] is True
    assert body["officer_pin_required"] is True and body["officer_actions_enabled"] is True
    assert body["own_uploads_need_pin"] is True
    assert body["upload_limits"] == {"max_bytes": 3 * 1024 * 1024, "max_mb": 3, "types": ["png", "jpg"]}
    assert TEST_PIN not in str(body)


def test_public_demo_accepts_cached_samples_without_a_pin_and_labels_them_cache(settings, taught_fake):
    # A real (mocked) run fills the cache, as scripts/prime_cache.py does.
    with make_client(settings, taught_fake) as client:
        assert upload(client, "primer", SAMPLE_PATH, "marksheet").status_code == 201
    calls_before = len(taught_fake.calls)
    with make_client(settings, taught_fake, use_cache=True, public_demo=True, demo_pin=TEST_PIN) as client:
        response = upload(client, "visitor", SAMPLE_PATH, "marksheet")
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["extraction"]["extraction_source"] == "cache"
        assert {f["extraction_source"] for f in body["facts"]} == {"cache"}
        listed = {d["id"]: d["cached"] for d in client.get("/api/demo-documents").json()}
        assert listed["seed-approve/marksheet.png"] is True
    assert len(taught_fake.calls) == calls_before  # no model call


def test_public_demo_refuses_unknown_files_unless_the_pin_is_given(settings, taught_fake):
    with make_client(settings, taught_fake, use_cache=True, public_demo=True, demo_pin=TEST_PIN) as client:
        refused = upload(client, "visitor", SAMPLE_PATH, "marksheet")
        assert refused.status_code == 403
        assert "sample documents only" in refused.json()["detail"]
        assert taught_fake.calls == []

        wrong = client.post("/api/profiles/visitor/documents", data={"document_type": "marksheet"},
                            files={"file": ("m.png", SAMPLE_PATH.read_bytes(), "image/png")},
                            headers={"X-Demo-Pin": "nope"})
        assert wrong.status_code == 403 and "not correct" in wrong.json()["detail"]

        allowed = client.post("/api/profiles/visitor/documents", data={"document_type": "marksheet"},
                              files={"file": ("m.png", SAMPLE_PATH.read_bytes(), "image/png")},
                              headers={"X-Demo-Pin": TEST_PIN})
        assert allowed.status_code == 201, allowed.text
        assert allowed.json()["extraction"]["extraction_source"] == "live"


def _flagged_fact(client: TestClient, application_id: int) -> int:
    """The misspelt name on the income certificate (rejecting it resolves the conflict)."""
    case = client.get(f"/api/officer/cases/{application_id}").json()
    return next(f["fact_id"] for f in case["unresolved_flags"] if f["document_type"] == "income_certificate")


def test_public_demo_officer_actions_need_the_pin_but_viewing_does_not(settings):
    with make_client(settings, public_demo=True, demo_pin=TEST_PIN) as client:
        seed(client)
        app_id = client.post("/api/profiles/seed-name-mismatch/applications",
                             json={"scheme_id": SCHOLARSHIP}).json()["application_id"]
        assert client.get("/api/officer/queue").status_code == 200
        assert client.get(f"/api/officer/cases/{app_id}").status_code == 200

        body = {"fact_id": _flagged_fact(client, app_id), "resolution": "reject",
                "reason": "Typo on the certificate", "officer": "Demo Officer"}
        url = f"/api/officer/cases/{app_id}/evidence"
        missing = client.post(url, json=body)
        assert missing.status_code == 403 and "need the demo PIN" in missing.json()["detail"]
        wrong = client.post(url, json=body, headers={"X-Demo-Pin": "1234"})
        assert wrong.status_code == 403 and "not correct" in wrong.json()["detail"]
        legacy = client.post(f"/officer/cases/{app_id}/evidence", json=body)
        assert legacy.status_code == 403  # the unprefixed path is guarded too
        assert client.post(url, json=body, headers={"X-Demo-Pin": TEST_PIN}).status_code == 201

        decision = {"action": "approve", "reason": "Typo resolved", "officer": "Demo Officer"}
        assert client.post(f"/api/officer/cases/{app_id}/decision", json=decision).status_code == 403
        ok = client.post(f"/api/officer/cases/{app_id}/decision", json=decision, headers={"X-Demo-Pin": TEST_PIN})
        assert ok.status_code == 201 and ok.json()["verdict"] == "approve"


def test_public_demo_without_a_pin_turns_officer_actions_off(settings):
    with make_client(settings, public_demo=True, demo_pin=None) as client:
        seed(client)
        app_id = client.post("/api/profiles/seed-name-mismatch/applications",
                             json={"scheme_id": SCHOLARSHIP}).json()["application_id"]
        response = client.post(f"/api/officer/cases/{app_id}/decision",
                               json={"action": "reject", "reason": "Not allowed", "officer": "X"},
                               headers={"X-Demo-Pin": "anything"})
        assert response.status_code == 403 and "turned off" in response.json()["detail"]
        assert client.get("/api/status").json()["officer_actions_enabled"] is False
        assert client.post("/api/demo-pin", headers={"X-Demo-Pin": "anything"}).status_code == 403


def test_wrong_pin_guesses_are_rate_limited(settings):
    with make_client(settings, public_demo=True, demo_pin=TEST_PIN, pin_attempts_per_minute=3) as client:
        for _ in range(3):
            assert client.post("/api/demo-pin", headers={"X-Demo-Pin": "guess"}).status_code == 403
        blocked = client.post("/api/demo-pin", headers={"X-Demo-Pin": TEST_PIN})
        assert blocked.status_code == 429  # even the right PIN waits once the limit is hit
        assert int(blocked.headers["Retry-After"]) >= 1
        assert "Too many wrong PIN attempts" in blocked.json()["detail"]


def test_pin_check_endpoint_and_constant_time_compare(settings):
    assert pin_matches(TEST_PIN, f" {TEST_PIN} ") is True
    assert pin_matches(TEST_PIN, "other") is False
    assert pin_matches(None, "anything") is False and pin_matches(TEST_PIN, None) is False
    with make_client(settings, demo_pin=TEST_PIN) as client:
        assert client.post("/api/demo-pin", headers={"X-Demo-Pin": TEST_PIN}).json() == {"ok": True}
        assert client.post("/api/demo-pin").status_code == 403


def test_without_public_demo_officer_actions_need_no_pin(seeded_client):
    app_id = seeded_client.post("/api/profiles/seed-below-marks/applications",
                                json={"scheme_id": SCHOLARSHIP}).json()["application_id"]
    response = seeded_client.post(f"/api/officer/cases/{app_id}/decision",
                                  json={"action": "reject", "reason": "Marks are below the minimum", "officer": "O"})
    assert response.status_code == 201


# --- Sample documents -------------------------------------------------------------------


def test_sample_documents_are_listed_and_served_without_path_tricks(client):
    rows = client.get("/api/demo-documents").json()
    assert len(rows) == 16
    row = next(r for r in rows if r["id"] == "seed-low-confidence/marksheet.jpg")
    assert row["document_type"] == "marksheet" and row["url"] == "/api/demo-documents/seed-low-confidence/marksheet.jpg"
    image = client.get(row["url"])
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
    assert client.get("/api/demo-documents/seed-approve/manifest.json").status_code == 404
    assert client.get("/api/demo-documents/..%2F..%2Fbackend/config.py").status_code == 404


# --- CORS, headers, site serving -------------------------------------------------------


def test_cors_is_same_origin_only_by_default(client):
    response = client.get("/api/status", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in response.headers
    preflight = client.options("/api/profiles/p/documents", headers={
        "Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in preflight.headers


def test_cors_allows_only_configured_origins(settings):
    with make_client(settings, cors_origins=("https://site.example",)) as client:
        ok = client.get("/api/status", headers={"Origin": "https://site.example"})
        assert ok.headers["access-control-allow-origin"] == "https://site.example"
        other = client.get("/api/status", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in other.headers


def test_security_headers_on_api_responses(client):
    headers = client.get("/api/health").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "same-origin"


@pytest.fixture
def built_site(tmp_path) -> Iterator:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>VerifyOnce</title><div id=root></div>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('site')", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside the build folder", encoding="utf-8")
    return dist


def test_site_is_served_at_root_with_spa_fallback(settings, built_site):
    with make_client(settings, web_dist_dir=built_site) as client:
        for path in ("/", "/citizen", "/console", "/console/case/3", "/how-it-works"):
            page = client.get(path)
            assert page.status_code == 200 and "<title>VerifyOnce</title>" in page.text, path
            assert page.headers["content-type"].startswith("text/html")
            assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        assert client.get("/assets/app.js").text == "console.log('site')"
        assert client.get("/favicon.svg").status_code == 200
        assert "outside the build folder" not in client.get("/..%2Fsecret.txt").text
        # The API keeps its paths: JSON, not the site.
        assert client.get("/api/health").json()["status"] == "ok"
        assert client.get("/health").json()["status"] == "ok"
        missing = client.get("/api/no-such-endpoint")
        assert missing.status_code == 404 and missing.json() == {"detail": "Not found"}


def test_root_explains_how_to_build_when_the_site_is_missing(settings, tmp_path):
    with make_client(settings, web_dist_dir=tmp_path / "not-built") as client:
        response = client.get("/")
        assert response.status_code == 404
        assert "npm run build" in response.json()["detail"]
        assert client.get("/api/status").status_code == 200


# --- scripts/serve.py ---------------------------------------------------------------------


def test_serve_rebuilds_only_when_the_site_is_missing_or_stale(tmp_path):
    import os
    import sys

    sys.path.insert(0, str(config.REPO_ROOT / "scripts"))
    import serve

    web = tmp_path / "web"
    (web / "src").mkdir(parents=True)
    source = web / "src" / "main.tsx"
    source.write_text("x", encoding="utf-8")
    assert serve.needs_build(web) is True  # nothing built yet

    (web / "dist").mkdir()
    index = web / "dist" / "index.html"
    index.write_text("<!doctype html>", encoding="utf-8")
    os.utime(source, (1_000_000, 1_000_000))
    os.utime(index, (2_000_000, 2_000_000))
    assert serve.needs_build(web) is False  # build is newer than the sources

    os.utime(source, (3_000_000, 3_000_000))
    assert serve.needs_build(web) is True  # a source changed after the build
