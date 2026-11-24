"""Officer workflow: queue, evidence resolution, approval blocking, reasons, audit trail."""

from __future__ import annotations

import pytest

from backend.models import Decision
from backend.seed import FEE_WAIVER, SCHOLARSHIP

OFFICER = "Priya Sharma"


def apply(client, profile_id, scheme_id=SCHOLARSHIP):
    response = client.post(f"/profiles/{profile_id}/applications", json={"scheme_id": scheme_id})
    assert response.status_code == 201, response.text
    return response.json()


def case(client, application_id):
    response = client.get(f"/officer/cases/{application_id}")
    assert response.status_code == 200, response.text
    return response.json()


def resolve(client, application_id, fact_id, resolution, reason="Checked the original document", officer=OFFICER):
    return client.post(f"/officer/cases/{application_id}/evidence",
                       json={"fact_id": fact_id, "resolution": resolution, "reason": reason, "officer": officer})


def decide(client, application_id, action, reason="", verdict=None, officer=OFFICER):
    return client.post(f"/officer/cases/{application_id}/decision",
                       json={"action": action, "reason": reason, "verdict": verdict, "officer": officer})


def flagged_name_facts(detail):
    return {f["document_type"]: f for f in detail["unresolved_flags"] if f["field"] == "name"}


# --- Queue and status -----------------------------------------------------------------------


def test_status_reports_provider_mode_and_key_without_the_key(client):
    body = client.get("/status").json()
    assert body["status"] == "ok"
    assert (body["provider"], body["extraction_mode"], body["provider_key_set"]) == ("gemini", "live", False)
    assert body["confidence_cutoff"] == 0.85
    assert not any("api_key" in k for k in body)  # only whether a key is set, never the key


def test_queue_lists_needs_review_first_and_filters_by_verdict(seeded_client):
    apply(seeded_client, "seed-approve")
    apply(seeded_client, "seed-below-marks")
    apply(seeded_client, "seed-name-mismatch")
    queue = seeded_client.get("/officer/queue").json()
    assert [r["verdict"] for r in queue] == ["needs_human_review", "reject", "approve"]
    assert queue[0]["profile_id"] == "seed-name-mismatch" and queue[0]["unresolved_flags"] == 3

    only_review = seeded_client.get("/officer/queue", params={"verdict": "needs_human_review"}).json()
    assert [r["profile_id"] for r in only_review] == ["seed-name-mismatch"]
    two = seeded_client.get("/officer/queue", params=[("verdict", "approve"), ("verdict", "reject")]).json()
    assert {r["verdict"] for r in two} == {"approve", "reject"}


def test_profiles_are_listed_for_the_picker(seeded_client):
    ids = [p["profile_id"] for p in seeded_client.get("/profiles").json()]
    assert {"seed-approve", "seed-below-marks", "seed-low-confidence", "seed-name-mismatch",
            "seed-reject-income"} <= set(ids)


# --- Case detail ------------------------------------------------------------------------------


def test_case_shows_conflicts_side_by_side_and_image_quality(seeded_client):
    first = apply(seeded_client, "seed-name-mismatch")
    detail = case(seeded_client, first["application_id"])
    (mismatch,) = detail["current_evaluation"]["mismatches"]
    assert mismatch["field"] == "name"
    assert {g["value"]: [o["document_type"] for o in g["observations"]] for g in mismatch["groups"]} == {
        "Ishan Rao": ["identity_card", "marksheet"], "Ishan Rau": ["income_certificate"]}
    assert detail["can_approve"] is False
    assert len(detail["approve_blockers"]) == 3
    outcome = detail["decision"]["rule_outcomes"][0]
    assert outcome["requirement"] and outcome["source_documents"] == ["income_certificate"]

    blurry = apply(seeded_client, "seed-low-confidence")
    docs = {d["document_type"]: d for d in case(seeded_client, blurry["application_id"])["documents"]}
    quality = docs["marksheet"]["image_quality"]
    assert quality["ok"] is False and quality["sharpness"] == pytest.approx(8.3, abs=0.1)
    assert quality["contrast"] == pytest.approx(4.7, abs=0.1)
    assert docs["identity_card"]["image_quality"]["ok"] is True


def test_every_fact_carries_its_source_badge_data(seeded_client):
    profile = seeded_client.get("/profiles/seed-approve").json()
    fact = profile["documents"][0]["facts"][0]
    assert (fact["source"], fact["extraction_source"], fact["extraction_model"]) == ("seed", None, None)
    assert "flag_reasons" in fact and "document_type" in fact


# --- Approval blocking and reasons ------------------------------------------------------------


def test_approval_is_blocked_while_flagged_evidence_is_unresolved(seeded_client):
    first = apply(seeded_client, "seed-name-mismatch")
    response = decide(seeded_client, first["application_id"], "approve")
    assert response.status_code == 409
    assert response.json()["detail"].startswith("Approval is blocked: Name on the identity card is flagged")
    override = decide(seeded_client, first["application_id"], "override", "Officer is sure", verdict="approve")
    assert override.status_code == 409  # an override cannot approve past unresolved flags either


@pytest.mark.parametrize("reason", ["", "   ", "ok"])
def test_missing_reason_is_rejected(seeded_client, reason):
    first = apply(seeded_client, "seed-name-mismatch")
    application_id = first["application_id"]
    fact_id = next(iter(flagged_name_facts(case(seeded_client, application_id)).values()))["fact_id"]
    for response in (
        resolve(seeded_client, application_id, fact_id, "reject", reason=reason),
        decide(seeded_client, application_id, "override", reason, verdict="reject"),
        decide(seeded_client, application_id, "reject", reason),
    ):
        assert response.status_code == 422
        assert "needs a typed reason" in response.json()["detail"]
    assert len(case(seeded_client, application_id)["decisions"]) == 1  # nothing recorded


def test_officer_name_is_required(seeded_client):
    first = apply(seeded_client, "seed-below-marks")
    response = decide(seeded_client, first["application_id"], "reject", "Marks below 60%", officer="  ")
    assert response.status_code == 422


def test_plain_approval_needs_the_rules_to_pass_but_an_override_can(seeded_client):
    first = apply(seeded_client, "seed-below-marks")  # rejected: marks 52 < 60
    application_id = first["application_id"]
    refused = decide(seeded_client, application_id, "approve")
    assert refused.status_code == 409 and "Use override" in refused.json()["detail"]
    override = decide(seeded_client, application_id, "override", "Grace marks granted by the board",
                      verdict="approve")
    assert override.status_code == 201, override.text
    body = override.json()
    assert (body["verdict"], body["decided_by"], body["supersedes_decision_id"]) == (
        "approve", f"officer:{OFFICER}", first["decision_id"])
    assert "overrode the evaluator (reject to approve): Grace marks granted by the board" in body["reason"]


def test_citizen_cannot_resubmit_over_an_officer_decision(seeded_client):
    first = apply(seeded_client, "seed-below-marks")
    decide(seeded_client, first["application_id"], "reject", "Marks are below the minimum")
    response = seeded_client.post("/profiles/seed-below-marks/applications", json={"scheme_id": SCHOLARSHIP})
    assert response.status_code == 409


def test_evidence_from_another_profile_is_refused(seeded_client):
    first = apply(seeded_client, "seed-name-mismatch")
    other = seeded_client.get("/profiles/seed-approve").json()["documents"][0]["facts"][0]["fact_id"]
    assert resolve(seeded_client, first["application_id"], other, "confirm").status_code == 404


# --- Flow (a): resolve the name-mismatch case, with an audit trail -----------------------------


def test_resolving_the_name_mismatch_then_approving_keeps_the_original_decision(seeded_client, db_session):
    first = apply(seeded_client, "seed-name-mismatch")
    application_id = first["application_id"]
    assert first["verdict"] == "needs_human_review"
    names = flagged_name_facts(case(seeded_client, application_id))

    # Reject the misspelt name on the income certificate. The other two then agree and verify.
    resolved = resolve(seeded_client, application_id, names["income_certificate"]["fact_id"], "reject",
                       reason="Typo on the certificate; confirmed with the issuing office")
    assert resolved.status_code == 201, resolved.text
    assert resolved.json()["verdict"] == "approve"  # re-evaluated on the corrected evidence
    after = case(seeded_client, application_id)
    assert after["unresolved_flags"] == [] and after["can_approve"] is True

    approved = decide(seeded_client, application_id, "approve", "Identity confirmed")
    assert approved.status_code == 201, approved.text
    assert approved.json()["decided_by"] == f"officer:{OFFICER}"

    # The original decision is untouched; three decisions in a superseding chain.
    db_session.expire_all()
    original = db_session.get(Decision, first["decision_id"])
    assert (original.verdict, original.decided_by, original.reason) == (
        "needs_human_review", "evaluator", first["reason"])
    audit = seeded_client.get(f"/applications/{application_id}/audit").json()
    assert audit["original_decision_id"] == first["decision_id"]
    assert [(d["sequence"], d["verdict"], d["decided_by"]) for d in audit["decisions"]] == [
        (1, "needs_human_review", "evaluator"),
        (2, "approve", "evaluator"),
        (3, "approve", f"officer:{OFFICER}"),
    ]
    events = [(e["action"], e["actor"], e["is_original_decision"]) for e in audit["events"]]
    assert events == [
        ("application.created", "citizen:seed-name-mismatch", False),
        ("decision.created", "citizen:seed-name-mismatch", True),
        ("evidence.rejected", f"officer:{OFFICER}", False),
        ("decision.officer_approved", f"officer:{OFFICER}", False),
    ]
    resolution = audit["events"][2]
    assert resolution["reason"] == "Typo on the certificate; confirmed with the issuing office"
    assert resolution["fact_id"] == names["income_certificate"]["fact_id"]
    assert resolution["details"]["original_decision_id"] == first["decision_id"]
    assert all(e["at"] for e in audit["events"])  # every entry is timestamped


def test_resolving_evidence_reevaluates_the_profiles_other_open_applications(seeded_client):
    scholarship = apply(seeded_client, "seed-name-mismatch")
    waiver = apply(seeded_client, "seed-name-mismatch", FEE_WAIVER)
    names = flagged_name_facts(case(seeded_client, scholarship["application_id"]))
    resolve(seeded_client, scholarship["application_id"], names["income_certificate"]["fact_id"], "reject",
            reason="Typo on the certificate")
    waiver_case = case(seeded_client, waiver["application_id"])
    assert waiver_case["decision"]["verdict"] == "approve"
    assert [d["sequence"] for d in waiver_case["decisions"]] == [1, 2]
    events = seeded_client.get(f"/applications/{waiver['application_id']}/audit").json()["events"]
    assert events[-1]["action"] == "decision.reevaluated"


# --- Flow (b): the blurry marksheet reaches review and is resolved -----------------------------


def test_blurry_marksheet_case_reaches_review_and_can_be_resolved(seeded_client):
    first = apply(seeded_client, "seed-low-confidence")
    application_id = first["application_id"]
    assert first["verdict"] == "needs_human_review"
    detail = case(seeded_client, application_id)
    flagged = {f["field"]: f for f in detail["unresolved_flags"]}
    assert set(flagged) == {"name", "date_of_birth", "marks_percent"}
    assert "Image is blurry or low-contrast, so an officer must check it" in flagged["marks_percent"]["flag_reasons"]

    for fact in flagged.values():
        assert resolve(seeded_client, application_id, fact["fact_id"], "confirm",
                       reason="Matches the original marksheet").status_code == 201
    final = case(seeded_client, application_id)
    assert final["can_approve"] is True and final["decision"]["verdict"] == "approve"


# --- Flow (c): below marks rejected for the scholarship, approved for the fee waiver -----------


def test_below_marks_profile_is_rejected_then_reused_for_the_fee_waiver(seeded_client):
    rejected = apply(seeded_client, "seed-below-marks")
    assert rejected["verdict"] == "reject"
    (offer,) = rejected["possible_schemes"]
    assert (offer["scheme_id"], offer["predicted_verdict"]) == (FEE_WAIVER, "approve")
    documents_before = len(seeded_client.get("/profiles/seed-below-marks").json()["documents"])
    approved = apply(seeded_client, "seed-below-marks", offer["scheme_id"])
    assert approved["verdict"] == "approve"
    assert len(seeded_client.get("/profiles/seed-below-marks").json()["documents"]) == documents_before
    mine = seeded_client.get("/profiles/seed-below-marks/applications").json()
    assert {a["scheme_id"]: a["decision"]["verdict"] for a in mine} == {SCHOLARSHIP: "reject", FEE_WAIVER: "approve"}
