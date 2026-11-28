"""prime_cache, preflight and metrics."""

from __future__ import annotations

import dataclasses
import json
import sys

import httpx
from google.genai import errors as genai_errors

from backend.cache_priming import prime_cache
from backend.config import REPO_ROOT, Settings
from backend.demo_assets import DOCUMENTS_DIR, demo_documents
from backend.extraction import ExtractionResult, Extractor, GeminiProvider
from conftest import FakeGemini, faithful_reply, load_manifest, sha256_of

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import metrics  # noqa: E402
import preflight  # noqa: E402


def teach_all(fake: FakeGemini) -> None:
    """The fake model reads every demo document correctly."""
    for case in ["seed-approve", "seed-reject-income", "seed-below-marks", "seed-name-mismatch",
                 "seed-low-confidence", "prompt-injection"]:
        for doc in load_manifest(case)["documents"]:
            fake.replies[sha256_of(DOCUMENTS_DIR / case / doc["file"])] = faithful_reply(doc)


def make_extractor(tmp_path, fake) -> Extractor:
    return Extractor(GeminiProvider(api_key="k", model="gemini-test", client=fake), use_cache=True,
                     cache_dir=tmp_path / "cache", sleep=lambda _s: None)


# --- prime_cache ------------------------------------------------------------------------------


def test_there_are_sixteen_demo_documents():
    docs = demo_documents()
    assert len(docs) == 16
    assert {t for _p, t in docs} == {"identity_card", "income_certificate", "marksheet"}


def test_prime_cache_writes_real_results_then_skips_cached_files(tmp_path):
    fake = FakeGemini()
    teach_all(fake)
    extractor = make_extractor(tmp_path, fake)
    pauses: list[float] = []
    rows = prime_cache(extractor, demo_documents(), root=DOCUMENTS_DIR, pause_seconds=4, sleep=pauses.append)
    assert [r.result for r in rows] == ["ok"] * 16 and all(r.cache_written for r in rows)
    assert len(list((tmp_path / "cache").glob("*.json"))) == 16
    assert len(fake.calls) == 16 and pauses == [4] * 15  # paced for free-tier limits

    again = prime_cache(extractor, demo_documents(), root=DOCUMENTS_DIR, pause_seconds=4, sleep=pauses.append)
    assert [r.result for r in again] == ["cached already"] * 16
    assert not any(r.cache_written for r in again)
    assert len(fake.calls) == 16  # no new model calls


def test_cache_serves_results_labelled_cache_with_the_model(tmp_path):
    fake = FakeGemini()
    teach_all(fake)
    extractor = make_extractor(tmp_path, fake)
    path, doc_type = demo_documents()[0]
    prime_cache(extractor, [(path, doc_type)], root=DOCUMENTS_DIR, pause_seconds=0)
    served = extractor.extract(path.read_bytes(), doc_type)
    assert (served.extraction_source, served.model, served.provider) == ("cache", "gemini-test", "gemini")


def test_failures_are_reported_and_not_cached(tmp_path):
    fake = FakeGemini(default=genai_errors.ServerError(
        503, {"error": {"code": 503, "message": "Overloaded.", "status": "UNAVAILABLE"}}))
    rows = prime_cache(make_extractor(tmp_path, fake), demo_documents()[:2], root=DOCUMENTS_DIR, pause_seconds=0)
    assert all(r.result.startswith("failed: Gemini API error 503") and not r.cache_written for r in rows)
    assert all("Gave up after 3 attempts" in r.result for r in rows)
    assert not (tmp_path / "cache").exists() or not any((tmp_path / "cache").iterdir())


def test_cache_refuses_hand_made_values(tmp_path):
    fake = FakeGemini()
    teach_all(fake)
    extractor = make_extractor(tmp_path, fake)
    path, doc_type = next(d for d in demo_documents() if d[1] == "marksheet")
    real = extractor.extract_live_only(path.read_bytes(), doc_type)
    assert real.ok

    forged = dataclasses.replace(real, readings={**real.readings,
                                                 "marks_percent": {"value": 99.0, "confidence": 1.0}})
    assert extractor.save_to_cache(forged) is False  # values differ from what the model replied
    no_call = dataclasses.replace(real, attempts=0)
    assert extractor.save_to_cache(no_call) is False  # no model call was made
    hand_made = ExtractionResult(doc_type, real.sha256, "live", "ok", "gemini", "gemini-test",
                                 readings=real.readings, raw_responses=[], attempts=1)
    assert extractor.save_to_cache(hand_made) is False  # no model reply at all
    not_live = dataclasses.replace(real, extraction_source="cache")
    assert extractor.save_to_cache(not_live) is False
    assert not (tmp_path / "cache").exists() or not any((tmp_path / "cache").iterdir())

    assert extractor.save_to_cache(real) is True
    # A cache file edited by hand is ignored when read.
    (cache_file,) = (tmp_path / "cache").glob("*.json")
    data = json.loads(cache_file.read_text(encoding="utf-8"))
    data["readings"]["marks_percent"]["value"] = 99.0
    cache_file.write_text(json.dumps(data), encoding="utf-8")
    assert extractor.is_cached(path.read_bytes(), doc_type) is False


# --- preflight --------------------------------------------------------------------------------


def health_ok(url: str) -> httpx.Response:
    return httpx.Response(200, json={"status": "ok", "database": "ok", "profiles": 5})


def health_down(url: str) -> httpx.Response:
    raise httpx.ConnectError("refused")


def test_preflight_with_a_missing_key_fails_that_check_and_prints_no_key(tmp_path, settings):
    no_key = dataclasses.replace(settings, gemini_api_key=None)
    checks = preflight.run_checks(no_key, env_file=tmp_path / ".env", api_url="http://127.0.0.1:8000",
                                  get=health_ok)
    text = preflight.report(checks)
    assert "FAIL  .env file exists: False" in text
    assert "FAIL  GEMINI_API_KEY is set: NOT SET" in text
    assert "PASS  API responds" in text
    assert "NOT READY" in text


def test_preflight_never_prints_the_key(tmp_path, settings):
    secret = "placeholder-test-value-not-a-key"
    (tmp_path / ".env").write_text("placeholder", encoding="utf-8")
    keyed = dataclasses.replace(settings, gemini_api_key=secret)
    text = preflight.report(preflight.run_checks(keyed, env_file=tmp_path / ".env",
                                                 api_url="http://127.0.0.1:8000", get=health_ok))
    assert "PASS  .env file exists: True" in text
    assert "PASS  GEMINI_API_KEY is set: set" in text
    assert secret not in text


def test_preflight_reports_api_down_and_missing_database(tmp_path, settings):
    missing_db = dataclasses.replace(settings, database_url=f"sqlite:///{(tmp_path / 'none.db').as_posix()}")
    text = preflight.report(preflight.run_checks(missing_db, env_file=tmp_path / ".env",
                                                 api_url="http://127.0.0.1:9", get=health_down))
    assert "FAIL  API responds: http://127.0.0.1:9/health unreachable" in text
    assert "FAIL  Database exists:" in text and "run scripts\\demo_reset.py" in text


def test_preflight_counts_the_cache_and_reports_the_mode(tmp_path, settings, seeded_client):
    fake = FakeGemini()
    teach_all(fake)
    cached = dataclasses.replace(settings, gemini_api_key="k", gemini_model="gemini-test", extraction_use_cache=True)
    prime_cache(make_extractor(tmp_path, fake), demo_documents()[:3], root=DOCUMENTS_DIR, pause_seconds=0)
    cached = dataclasses.replace(cached, cache_dir=tmp_path / "cache")
    text = preflight.report(preflight.run_checks(cached, env_file=tmp_path / ".env",
                                                 api_url="http://x", get=health_ok))
    assert "FAIL  Demo cache: 3 of 16 demo documents cached for gemini/gemini-test" in text
    assert "FAIL  Extraction mode: cache first (EXTRACTION_USE_CACHE=1)" in text
    assert "PASS  Database exists:" in text


# --- metrics ----------------------------------------------------------------------------------


def test_metrics_reports_only_what_it_measures(tmp_path, settings, seeded_client):
    rows = metrics.measure_seed_cases(f"sqlite:///{(tmp_path / 'm.db').as_posix()}")
    assert len(rows) == 10 and all(r["ms"] > 0 for r in rows)
    demo = metrics.officer_outcomes(settings.database_url)
    report = metrics.format_report(rows, demo)
    assert "Decided automatically: 7 of 10 (70%)" in report
    assert "Sent to human review: 3 of 10 (30%)" in report
    assert "review cases that ended with an explicit officer decision: 0 of 0" in report
    lowered = report.lower()
    for claim in ("saved", "faster", "manual", "speedup", "hours"):
        assert claim not in lowered


def test_metrics_counts_officer_decisions_in_the_demo_database(settings, seeded_client):
    first = seeded_client.post("/profiles/seed-name-mismatch/applications",
                               json={"scheme_id": "state_student_scholarship"}).json()
    seeded_client.post("/profiles/seed-low-confidence/applications", json={"scheme_id": "state_student_scholarship"})
    seeded_client.post(f"/officer/cases/{first['application_id']}/decision",
                       json={"action": "reject", "reason": "Identity not confirmed", "officer": "Asha"})
    assert metrics.officer_outcomes(settings.database_url) == {
        "database": metrics.make_url(settings.database_url).database, "review_cases": 2, "closed_by_officer": 1}


def old_schema_database(tmp_path):
    """A database from before step 4: decisions lack the explanation columns."""
    import sqlite3

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE profiles (id TEXT PRIMARY KEY, label TEXT, created_at TEXT);
            CREATE TABLE applications (id INTEGER PRIMARY KEY, profile_id TEXT, scheme_id TEXT, created_at TEXT);
            CREATE TABLE decisions (id INTEGER PRIMARY KEY, application_id INTEGER, sequence INTEGER,
                                    verdict TEXT, decided_by TEXT);
            INSERT INTO profiles VALUES ('p1', NULL, NULL);
            INSERT INTO applications VALUES (1, 'p1', 'x', NULL);
            INSERT INTO decisions VALUES (1, 1, 1, 'needs_human_review', 'evaluator');
            INSERT INTO decisions VALUES (2, 1, 2, 'approve', 'officer:Asha');
        """)
    conn.close()
    return f"sqlite:///{path.as_posix()}"


def test_metrics_reads_an_older_database_without_crashing(tmp_path):
    outcome = metrics.officer_outcomes(old_schema_database(tmp_path))
    assert (outcome["review_cases"], outcome["closed_by_officer"]) == (1, 1)


def test_preflight_fails_an_outdated_database_and_an_outdated_api(tmp_path, settings):
    old = dataclasses.replace(settings, database_url=old_schema_database(tmp_path))
    old_api = lambda url: httpx.Response(200, json={"status": "ok", "schemes": []})  # noqa: E731
    text = preflight.report(preflight.run_checks(old, env_file=tmp_path / ".env", api_url="http://x", get=old_api))
    assert "FAIL  Database exists:" in text and "is from an older version" in text
    assert "FAIL  API responds: http://x/health answered, but the API runs older code: restart uvicorn" in text


def test_health_reports_an_outdated_database(tmp_path):
    from fastapi.testclient import TestClient

    from backend.app import create_app

    with TestClient(create_app(Settings(database_url=old_schema_database(tmp_path)))) as client:
        body = client.get("/health").json()
    assert body["status"] == "degraded" and "demo_reset" in body["database"]
    assert any("decisions lacks" in p for p in body["problems"])


def test_unexpected_server_errors_never_return_a_stack_trace(tmp_path):
    from fastapi.testclient import TestClient

    from backend.app import create_app

    app = create_app(Settings(database_url=old_schema_database(tmp_path)))
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/officer/queue")  # reads decisions columns the old database lacks
    assert response.status_code == 500
    assert response.json() == {"detail": "Something went wrong on the server. Details are in the API window's log."}
    assert "Traceback" not in response.text


def test_metrics_without_a_demo_database(tmp_path):
    assert metrics.officer_outcomes(f"sqlite:///{(tmp_path / 'none.db').as_posix()}") is None
    assert "Demo database not found" in metrics.format_report(
        [{"case": "c", "scheme": "s", "verdict": "approve", "ms": 1.0}], None)


def test_settings_reads_explanations_flag(monkeypatch):
    monkeypatch.setenv("EXPLANATIONS_ENABLED", "1")
    assert Settings.from_env().explanations_enabled is True
