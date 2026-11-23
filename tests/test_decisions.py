"""Decisions are immutable, always come with an audit event, and resubmission appends."""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from backend.models import AuditEvent, Decision, Fact, ImmutableRecordError
from backend.service import set_fact_status

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


def test_correction_then_resubmission_keeps_the_old_decision(seeded_client, db_session):
    """An officer verifies the blurry marks figure; the new decision approves, the old one stays."""
    first = submit(seeded_client, "seed-low-confidence")
    assert first["verdict"] == "needs_human_review"

    marks_fact_id = first["review_reasons"][0]["fact_ids"][0]
    fact = db_session.get(Fact, marks_fact_id)
    assert (fact.field, fact.verification_status) == ("marks_percent", "flagged")
    set_fact_status(db_session, fact, "verified", actor="officer:demo", note="Checked the original marksheet")
    db_session.commit()

    second = submit(seeded_client, "seed-low-confidence")
    assert second["verdict"] == "approve"
    assert second["supersedes_decision_id"] == first["decision_id"]
    db_session.expire_all()
    assert db_session.get(Decision, first["decision_id"]).verdict == "needs_human_review"

    events = db_session.scalars(select(AuditEvent).where(AuditEvent.fact_id == marks_fact_id)).all()
    # observed -> flagged by the automatic checks -> verified by the officer
    assert [(e.action, e.actor) for e in events] == [
        ("fact.observed", "seed"),
        ("fact.status_changed", "seed"),
        ("fact.status_changed", "officer:demo"),
    ]
    assert [e.details.get("to") for e in events[1:]] == ["flagged", "verified"]
    assert [e.id for e in db_session.get(Fact, marks_fact_id).history] == [e.id for e in events]


def test_unknown_profile_and_scheme_are_refused(seeded_client):
    missing = seeded_client.post("/profiles/nobody/applications", json={"scheme_id": SCHOLARSHIP})
    assert missing.status_code == 404
    bad_scheme = seeded_client.post("/profiles/seed-approve/applications", json={"scheme_id": "lottery"})
    assert bad_scheme.status_code == 422
