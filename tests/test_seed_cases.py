"""The five reproducible seed cases, end to end through the API."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from backend.models import Fact
from backend.seed import FEE_WAIVER, SCHOLARSHIP, SEED_PROFILES, seed_database

CASES = [(seed.id, scheme_id, verdict) for seed in SEED_PROFILES for scheme_id, verdict in seed.expected.items()]


def submit(client, profile_id, scheme_id):
    response = client.post(f"/profiles/{profile_id}/applications", json={"scheme_id": scheme_id})
    assert response.status_code == 201, response.text
    return response.json()


def test_there_are_exactly_five_seed_cases():
    assert [s.id for s in SEED_PROFILES] == [
        "seed-approve",
        "seed-reject-income",
        "seed-below-marks",
        "seed-name-mismatch",
        "seed-low-confidence",
    ]


@pytest.mark.parametrize(("profile_id", "scheme_id", "expected"), CASES)
def test_seed_case_verdicts(seeded_client, profile_id, scheme_id, expected):
    body = submit(seeded_client, profile_id, scheme_id)
    assert body["verdict"] == expected
    assert body["reason"]
    assert len(body["rule_outcomes"]) == len(seeded_client.app.state.schemes[scheme_id].rules)
    assert body["source_facts"]


def test_approval_matches_name_variants_without_flagging(seeded_client):
    body = submit(seeded_client, "seed-approve", SCHOLARSHIP)
    names = {f["value"] for f in body["source_facts"] if f["field"] == "name"}
    assert names == {"Ravi K. Patil", "Ravi Patil", "RAVI K PATIL"}
    assert body["mismatches"] == []
    assert {o["outcome"] for o in body["rule_outcomes"]} == {"pass"}


def test_income_rejection_fails_the_income_rule(seeded_client):
    body = submit(seeded_client, "seed-reject-income", SCHOLARSHIP)
    outcomes = {o["rule_id"]: o["outcome"] for o in body["rule_outcomes"]}
    assert outcomes == {"income_limit": "fail", "minimum_marks": "pass"}
    assert "above the limit of ₹3,00,000" in body["reason"]
    assert body["possible_schemes"] == []


def test_below_marks_fails_scholarship_but_suggests_the_fee_waiver(seeded_client):
    body = submit(seeded_client, "seed-below-marks", SCHOLARSHIP)
    assert body["verdict"] == "reject"
    income, marks = body["rule_outcomes"]
    assert (income["rule_id"], income["outcome"], income["actual"], income["expected"]) == (
        "income_limit", "pass", 210000, 300000,
    )
    assert (marks["rule_id"], marks["outcome"], marks["actual"], marks["expected"]) == (
        "minimum_marks", "fail", 52.0, 60.0,
    )
    for outcome in (income, marks):
        cited = [f for f in body["source_facts"] if f["fact_id"] in outcome["source_fact_ids"]]
        assert cited and all(f["field"] == outcome["field"] for f in cited)
    assert body["possible_schemes"] == [
        {
            "scheme_id": FEE_WAIVER,
            "scheme_name": "Income-Based Fee Waiver",
            "predicted_verdict": "approve",
            "reason": body["possible_schemes"][0]["reason"],
        }
    ]

    waiver = submit(seeded_client, "seed-below-marks", FEE_WAIVER)
    assert waiver["verdict"] == "approve"


def test_name_mismatch_reports_both_spellings(seeded_client):
    body = submit(seeded_client, "seed-name-mismatch", SCHOLARSHIP)
    assert body["verdict"] == "needs_human_review"
    (mismatch,) = body["mismatches"]
    assert mismatch["field"] == "name"
    groups = {g["value"]: [o["document_type"] for o in g["observations"]] for g in mismatch["groups"]}
    assert groups == {"Ishan Rao": ["identity_card", "marksheet"], "Ishan Rau": ["income_certificate"]}
    assert "'Ishan Rao' (identity card, marksheet) vs 'Ishan Rau' (income certificate)" in body["reason"]
    # The eligibility rules themselves still pass and are recorded.
    assert {o["outcome"] for o in body["rule_outcomes"]} == {"pass"}


def test_low_confidence_names_the_field_and_score(seeded_client):
    body = submit(seeded_client, "seed-low-confidence", SCHOLARSHIP)
    assert body["verdict"] == "needs_human_review"
    assert [(r["field"], r["issue"]) for r in body["review_reasons"]] == [
        ("marks_percent", "low_quality_image"),
        ("marks_percent", "low_confidence"),
    ]
    assert "blurry or low-contrast" in body["reason"]
    assert "62%, below the 85% cutoff" in body["reason"]
    # The blurry marksheet's name agrees with the confident copies, so it does not block,
    # and the fee waiver (no marks rule) is offered instead.
    assert [s["scheme_id"] for s in body["possible_schemes"]] == [FEE_WAIVER]


def test_seed_facts_move_to_verified_or_flagged(seeded_client):
    statuses = {}
    for seed in SEED_PROFILES:
        profile = seeded_client.get(f"/profiles/{seed.id}").json()
        for doc in profile["documents"]:
            for fact in doc["facts"]:
                statuses[(seed.id, doc["document_type"], fact["field"])] = fact["verification_status"]
    assert "unverified" not in statuses.values()
    flagged = sorted(k for k, v in statuses.items() if v == "flagged")
    assert flagged == [
        ("seed-low-confidence", "marksheet", "date_of_birth"),
        ("seed-low-confidence", "marksheet", "marks_percent"),
        ("seed-low-confidence", "marksheet", "name"),
        ("seed-name-mismatch", "identity_card", "name"),
        ("seed-name-mismatch", "income_certificate", "name"),
        ("seed-name-mismatch", "marksheet", "name"),
    ]


def test_seeding_is_reproducible_and_idempotent(seeded_client, db_session):
    def snapshot():
        rows = db_session.execute(select(Fact.field, Fact.value_json, Fact.confidence).order_by(Fact.id))
        return [tuple(r) for r in rows]

    before = snapshot()
    assert seed_database(db_session) == []
    assert snapshot() == before
    assert db_session.scalar(select(func.count()).select_from(Fact)) == sum(
        len(d.facts) for s in SEED_PROFILES for d in s.documents
    )
