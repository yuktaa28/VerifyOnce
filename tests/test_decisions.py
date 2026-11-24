"""Decisions are immutable, always come with an audit event, and resubmission appends."""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from backend.models import AuditEvent, Decision, Fact, ImmutableRecordError

SCHOLARSHIP = "state_student_scholarship"


def submit(client, profile_id, scheme_id=SCHOLARSHIP):
    response = client.post(f"/profiles/{profile_id}/applications", json={"scheme_id": scheme_id})
    assert response.status_code == 201, response.text
    return response.json()


def test_each_decision_has_a_linked_audit_event(seeded_client, db_session):
    body = submit(seeded_client, "seed-approve")
    event = db_session.get(AuditEvent, body["audit_event_id"])
    assert event.decision_id == body["decision_id"]
    assert event.application_id == body["application_id"]
    assert event.action == "decision.created"
    assert event.details["verdict"] == body["verdict"] == "approve"
    decision = db_session.get(Decision, body["decision_id"])
    assert [e.id for e in decision.audit_events] == [event.id]


def test_decision_cannot_be_changed_through_the_orm(seeded_client, db_session):
    body = submit(seeded_client, "seed-below-marks")
    decision = db_session.get(Decision, body["decision_id"])
    decision.verdict = "approve"
    with pytest.raises(ImmutableRecordError):
        db_session.flush()
    db_session.rollback()
    assert db_session.get(Decision, body["decision_id"]).verdict == "reject"


def test_decision_cannot_be_deleted_through_the_orm(seeded_client, db_session):
    body = submit(seeded_client, "seed-below-marks")
    db_session.delete(db_session.get(Decision, body["decision_id"]))
    with pytest.raises(ImmutableRecordError):
        db_session.flush()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE decisions SET verdict = 'approve'",
        "DELETE FROM decisions",
        "UPDATE audit_events SET actor = 'someone else'",
        "DELETE FROM audit_events",
    ],
)
def test_database_refuses_raw_sql_changes(seeded_client, db_session, statement):
    submit(seeded_client, "seed-below-marks")
    with pytest.raises(DBAPIError, match="immutable"):
        db_session.execute(text(statement))


def test_resubmission_appends_a_new_decision(seeded_client, db_session):
    first = submit(seeded_client, "seed-below-marks")
    second = submit(seeded_client, "seed-below-marks")

    assert second["application_id"] == first["application_id"]
    assert second["decision_id"] != first["decision_id"]
    assert (first["sequence"], second["sequence"]) == (1, 2)
    assert second["supersedes_decision_id"] == first["decision_id"]
    assert second["audit_event_id"] != first["audit_event_id"]

    old = db_session.get(Decision, first["decision_id"])
    assert (old.verdict, old.reason, old.supersedes_decision_id) == (first["verdict"], first["reason"], None)

    history = seeded_client.get(f"/applications/{first['application_id']}").json()
    assert [d["decision_id"] for d in history["decisions"]] == [first["decision_id"], second["decision_id"]]
    assert [e["action"] for e in history["audit_events"]] == [
        "application.created",
        "decision.created",
        "decision.resubmitted",
    ]


def test_correction_then_officer_decision_keeps_the_old_decision(seeded_client, db_session):
    """An officer confirms the blurry marks figure, then decides; the review decision stays."""
    first = submit(seeded_client, "seed-low-confidence")
    assert first["verdict"] == "needs_human_review"
    application_id = first["application_id"]

    # While awaiting an officer, the citizen cannot resubmit to get an automatic result.
    again = seeded_client.post("/profiles/seed-low-confidence/applications", json={"scheme_id": SCHOLARSHIP})
    assert again.status_code == 409 and "awaiting an officer decision" in again.json()["detail"]

    marks_fact_id = first["review_reasons"][0]["fact_ids"][0]
    assert db_session.get(Fact, marks_fact_id).field == "marks_percent"
    flagged = seeded_client.get(f"/officer/cases/{application_id}").json()["unresolved_flags"]
    for fact in flagged:
        response = seeded_client.post(f"/officer/cases/{application_id}/evidence", json={
            "fact_id": fact["fact_id"], "resolution": "confirm", "reason": "Checked the original marksheet",
            "officer": "demo"})
        assert response.status_code == 201
    assert response.json()["suggested_verdict"] == "approve"
    assert response.json()["status"] == "awaiting officer decision"

    decided = seeded_client.post(f"/officer/cases/{application_id}/decision",
                                 json={"action": "approve", "reason": "", "officer": "demo"}).json()
    assert (decided["verdict"], decided["sequence"], decided["supersedes_decision_id"]) == (
        "approve", 2, first["decision_id"])
    db_session.expire_all()
    assert db_session.get(Decision, first["decision_id"]).verdict == "needs_human_review"

    events = db_session.scalars(select(AuditEvent).where(AuditEvent.fact_id == marks_fact_id)).all()
    # observed -> flagged by the automatic checks -> confirmed by the officer
    assert [(e.action, e.actor) for e in events] == [
        ("fact.observed", "seed"),
        ("fact.status_changed", "seed"),
        ("evidence.confirmed", "officer:demo"),
    ]
    assert (events[1].details["to"], events[2].details["fact_to"]) == ("flagged", "verified")
    assert [e.id for e in db_session.get(Fact, marks_fact_id).history] == [e.id for e in events]


def test_unknown_profile_and_scheme_are_refused(seeded_client):
    missing = seeded_client.post("/profiles/nobody/applications", json={"scheme_id": SCHOLARSHIP})
    assert missing.status_code == 404
    bad_scheme = seeded_client.post("/profiles/seed-approve/applications", json={"scheme_id": "lottery"})
    assert bad_scheme.status_code == 422
