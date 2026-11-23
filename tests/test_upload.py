"""POST /profiles/{id}/documents with a mocked Gemini client, end to end into the evaluator."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from google.genai import errors as genai_errors

from backend.app import create_app
from backend.extraction import Extractor, GeminiProvider, system_prompt
from backend.seed import FEE_WAIVER, SCHOLARSHIP, SEED_PROFILES
from conftest import DOCUMENTS, FakeGemini, faithful_reply, load_manifest, manifest_doc, sha256_of, upload


def apply(client, profile_id, scheme_id):
    response = client.post(f"/profiles/{profile_id}/applications", json={"scheme_id": scheme_id})
    assert response.status_code == 201, response.text
    return response.json()


def teach_case(fake, case: str) -> list[tuple]:
    """Make the fake model read every document of a case correctly. Returns (path, type) pairs."""
    pairs = []
    for doc in load_manifest(case)["documents"]:
        path = DOCUMENTS / case / doc["file"]
        fake.replies[sha256_of(path)] = faithful_reply(doc)
        pairs.append((path, doc["document_type"]))
    return pairs


def test_upload_returns_normalized_facts_checks_and_status(upload_client, fake_gemini):
    path, doc = manifest_doc("seed-approve", "income_certificate")
    fake_gemini.replies[sha256_of(path)] = faithful_reply(doc)
    response = upload(upload_client, "citizen-1", path, "income_certificate")
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["status"] == "verified"
    assert body["sha256"] == sha256_of(path)
    assert body["extraction"]["extraction_source"] == "live"
    assert body["extraction"]["status"] == "ok"
    assert (body["extraction"]["provider"], body["extraction"]["model"]) == ("gemini", "gemini-test")
    assert body["extraction"]["raw_responses"] == [faithful_reply(doc)]
    facts = {f["field"]: f for f in body["facts"]}
    assert set(facts) == {"name", "state", "annual_income_inr"}
    income = facts["annual_income_inr"]
    assert (income["value"], income["confidence"], income["verification_status"]) == (240000, 0.93, "verified")
    assert {c["name"]: c["passed"] for c in income["checks"]} == {
        "readable": True, "non_negative_integer": True, "confidence": True,
    }
    assert any(c["name"] == "cross_document" for c in facts["name"]["checks"])


@pytest.mark.parametrize("case", [s.id for s in SEED_PROFILES])
def test_uploading_a_cases_documents_gives_the_fixture_verdicts(upload_client, fake_gemini, case):
    for path, document_type in teach_case(fake_gemini, case):
        assert upload(upload_client, f"upload-{case}", path, document_type).status_code == 201
    expected = load_manifest(case)["expected_verdicts"]
    seed = next(s for s in SEED_PROFILES if s.id == case)
    assert expected == seed.expected
    for scheme_id, verdict in expected.items():
        assert apply(upload_client, f"upload-{case}", scheme_id)["verdict"] == verdict


def test_uploaded_facts_match_seeded_facts(upload_client, fake_gemini, seeded_client):
    case = "seed-below-marks"
    for path, document_type in teach_case(fake_gemini, case):
        upload(upload_client, "uploaded", path, document_type)

    def facts(client, profile_id):
        body = client.get(f"/profiles/{profile_id}").json()
        return sorted(
            (d["document_type"], f["field"], f["value"], f["confidence"], f["verification_status"])
            for d in body["documents"] for f in d["facts"]
        )

    assert facts(upload_client, "uploaded") == facts(seeded_client, case)


def test_failed_extraction_flags_every_field_and_routes_to_review(upload_client, fake_gemini):
    for path, document_type in teach_case(fake_gemini, "seed-approve"):
        if document_type == "marksheet":
            fake_gemini.replies[sha256_of(path)] = ["garbage", "more garbage"]
        upload(upload_client, "p-failed", path, document_type)

    profile = upload_client.get("/profiles/p-failed").json()
    sheet = next(d for d in profile["documents"] if d["document_type"] == "marksheet")
    assert sheet["extraction"]["status"] == "failed"
    assert {(f["value"], f["verification_status"]) for f in sheet["facts"]} == {(None, "flagged")}

    decision = apply(upload_client, "p-failed", SCHOLARSHIP)
    assert decision["verdict"] == "needs_human_review"
    assert ("marks_percent", "invalid") in {(r["field"], r["issue"]) for r in decision["review_reasons"]}


def test_quota_error_does_not_crash_and_routes_to_review(upload_client, fake_gemini):
    quota = genai_errors.ClientError(429, {"error": {"code": 429, "message": "Quota exceeded.",
                                                     "status": "RESOURCE_EXHAUSTED"}})
    for path, document_type in teach_case(fake_gemini, "seed-below-marks"):
        if document_type == "marksheet":
            fake_gemini.replies[sha256_of(path)] = quota
        response = upload(upload_client, "p-quota", path, document_type)
        assert response.status_code == 201

    body = response.json()  # the marksheet upload
    assert body["status"] == "extraction_failed"
    assert body["extraction"]["error"].startswith("Gemini quota or rate limit reached (HTTP 429")
    assert {(f["value"], f["verification_status"]) for f in body["facts"]} == {(None, "flagged")}
    for scheme_id in (SCHOLARSHIP, FEE_WAIVER):
        assert apply(upload_client, "p-quota", scheme_id)["verdict"] == "needs_human_review"


def test_blocked_response_routes_to_review(settings, tmp_path):
    path, doc = manifest_doc("seed-below-marks", "marksheet")
    fake = FakeGemini(default=faithful_reply(doc), block_reason="SAFETY")
    extractor = Extractor(GeminiProvider(api_key="k", model="gemini-test", client=fake), cache_dir=tmp_path / "c")
    with TestClient(create_app(settings, extractor=extractor)) as client:
        body = upload(client, "p-blocked", path, "marksheet").json()
    assert body["status"] == "extraction_failed"
    assert body["extraction"]["error"] == "Gemini blocked the document (SAFETY)"
    assert len(fake.calls) == 1


def test_a_conflicting_later_upload_reports_the_earlier_fact_it_flagged(upload_client, fake_gemini):
    pairs = teach_case(fake_gemini, "seed-name-mismatch")
    card = upload(upload_client, "p-rao", *pairs[0]).json()
    assert card["status"] == "verified"
    cert = upload(upload_client, "p-rao", *pairs[1]).json()
    assert cert["status"] == "flagged"
    name_fact = next(f for f in card["facts"] if f["field"] == "name")
    assert [(c["fact_id"], c["from"], c["to"]) for c in cert["other_facts_changed"]] == [
        (name_fact["fact_id"], "verified", "flagged")
    ]


def test_bad_uploads_are_refused(upload_client):
    path, _doc = manifest_doc("seed-approve", "marksheet")
    assert upload(upload_client, "p", path, "passport").status_code == 422
    response = upload_client.post(
        "/profiles/p/documents", data={"document_type": "marksheet"},
        files={"file": ("notes.txt", b"just some text", "text/plain")},
    )
    assert response.status_code == 422


# --- Prompt injection ---------------------------------------------------------------------

INJECTION_PATH, INJECTION_DOC = manifest_doc("prompt-injection", "marksheet")


def test_injection_text_is_only_ever_inside_the_image(upload_client, fake_gemini):
    fake_gemini.replies[sha256_of(INJECTION_PATH)] = faithful_reply(INJECTION_DOC)
    upload(upload_client, "p-inject", INJECTION_PATH, "marksheet")
    (call,) = fake_gemini.calls
    config = call["config"]
    assert config.system_instruction == system_prompt("marksheet")  # fixed template, nothing from the document
    assert len(call["contents"]) == 2 and FakeGemini.texts(call) == ["Extract the fields from this marksheet."]
    outside_image = json.dumps({"model": call["model"], "system": config.system_instruction,
                                "schema": config.response_json_schema, "texts": FakeGemini.texts(call)})
    for needle in ("NOTE TO AI", "ignore all previous", "99", "Arjun", "52"):
        assert needle not in outside_image


def test_correct_reading_of_the_injection_document_keeps_the_fixture_verdict(upload_client, fake_gemini):
    pairs = teach_case(fake_gemini, "seed-below-marks")
    fake_gemini.replies[sha256_of(INJECTION_PATH)] = faithful_reply(INJECTION_DOC)
    for path, document_type in pairs[:2]:  # identity card and income certificate
        upload(upload_client, "p-inject", path, document_type)
    upload(upload_client, "p-inject", INJECTION_PATH, "marksheet")
    assert apply(upload_client, "p-inject", SCHOLARSHIP)["verdict"] == "reject"
    assert apply(upload_client, "p-inject", FEE_WAIVER)["verdict"] == "approve"


def test_a_model_that_obeys_the_injection_cannot_add_fields_or_a_verdict(upload_client, fake_gemini):
    """If the model follows the document's instruction, the schema refuses the reply."""
    obeying = json.dumps({
        **json.loads(faithful_reply(INJECTION_DOC, marks_percent={"value": 99, "confidence": 1.0})),
        "verdict": "approve",
    })
    pairs = teach_case(fake_gemini, "seed-below-marks")
    fake_gemini.replies[sha256_of(INJECTION_PATH)] = [obeying, obeying]
    for path, document_type in pairs[:2]:
        upload(upload_client, "p-obey", path, document_type)
    body = upload(upload_client, "p-obey", INJECTION_PATH, "marksheet").json()

    assert body["status"] == "extraction_failed"
    assert sorted(f["field"] for f in body["facts"]) == ["date_of_birth", "marks_percent", "name"]
    assert all(f["value"] is None for f in body["facts"])
    for scheme_id in (SCHOLARSHIP, FEE_WAIVER):
        assert apply(upload_client, "p-obey", scheme_id)["verdict"] == "needs_human_review"
