"""Deterministic validators and cross-document checks."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from backend.models import AuditEvent
from backend.service import add_document, add_fact, create_profile, set_fact_status
from backend.verification import validate, verify_profile

TODAY = date(2026, 11, 23)


@pytest.mark.parametrize(
    ("field", "raw", "value"),
    [
        ("date_of_birth", "2006-07-21", date(2006, 7, 21)),
        ("date_of_birth", "21/07/2006", date(2006, 7, 21)),  # day first, as printed
        ("date_of_birth", "21-07-2006", date(2006, 7, 21)),
        ("annual_income_inr", 210000, 210000),
        ("annual_income_inr", "2,10,000", 210000),
        ("annual_income_inr", "Rs. 2,10,000/-", 210000),
        ("annual_income_inr", 0, 0),
        ("annual_income_inr", 210000.0, 210000),
        ("marks_percent", 52, 52.0),
        ("marks_percent", "52.00%", 52.0),
        ("marks_percent", 0, 0.0),
        ("marks_percent", 100, 100.0),
        ("id_last4", "2051", "2051"),
        ("id_last4", "XXXX XXXX 2051", "2051"),
        ("id_last4", "8765 4321 2051", "2051"),
        ("name", "  Ravi   K. Patil ", "Ravi K. Patil"),
        ("state", "Kerala", "Kerala"),
    ],
)
def test_valid_values_pass(field, raw, value):
    result, checks = validate(field, raw, today=TODAY)
    assert result == value
    assert all(c.passed for c in checks)


@pytest.mark.parametrize(
    ("field", "raw", "check"),
    [
        ("date_of_birth", "31/02/2006", "date_parses"),
        ("date_of_birth", "next tuesday", "date_parses"),
        ("date_of_birth", "2027-01-01", "date_plausible"),  # future
        ("date_of_birth", "1899-12-31", "date_plausible"),
        ("annual_income_inr", -5, "non_negative_integer"),
        ("annual_income_inr", 240000.5, "non_negative_integer"),
        ("annual_income_inr", "about two lakh", "non_negative_integer"),
        ("annual_income_inr", True, "non_negative_integer"),
        ("marks_percent", 100.5, "range_0_100"),
        ("marks_percent", -1, "range_0_100"),
        ("marks_percent", "A+", "range_0_100"),
        ("id_last4", "205", "four_digits"),
        ("id_last4", "XXXX", "four_digits"),
        ("name", "12345", "has_letters"),
    ],
)
def test_invalid_values_fail_and_leave_no_value(field, raw, check):
    result, checks = validate(field, raw, today=TODAY)
    assert result is None
    failed = [c for c in checks if not c.passed]
    assert [c.name for c in failed] == [check]


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_unreadable_values_fail(raw):
    value, checks = validate("marks_percent", raw)
    assert value is None
    assert [(c.name, c.passed) for c in checks] == [("readable", False)]


def test_id_failure_message_never_echoes_digits():
    _value, checks = validate("id_last4", "915")
    assert "915" not in checks[-1].message


# --- Statuses on stored facts -------------------------------------------------------------


def make_profile(session, docs):
    """docs: list of (document_type, {field: (raw, confidence)})."""
    profile = create_profile(session, "p1", actor="test")
    facts = {}
    for doc_type, fields in docs:
        document = add_document(session, profile, doc_type, actor="test")
        for field, (raw, conf) in fields.items():
            facts[(doc_type, field)] = add_fact(session, document, field, raw, conf, actor="test")
    return facts


def statuses(facts):
    return {key: f.verification_status for key, f in facts.items()}


def test_clean_facts_move_from_unverified_to_verified_with_an_audit_event(bare_session):
    facts = make_profile(bare_session, [("marksheet", {"name": ("Arjun Mehta", 0.96), "marks_percent": (52, 0.97)})])
    assert set(statuses(facts).values()) == {"unverified"}
    changes = verify_profile(bare_session, "p1", confidence_cutoff=0.8)
    assert set(statuses(facts).values()) == {"verified"}
    assert {(c["from"], c["to"]) for c in changes} == {("unverified", "verified")}
    events = bare_session.scalars(select(AuditEvent).where(AuditEvent.action == "fact.status_changed")).all()
    assert len(events) == 2


def test_null_is_always_flagged_even_with_high_confidence(bare_session):
    facts = make_profile(bare_session, [("marksheet", {"marks_percent": (None, 0.99)})])
    verify_profile(bare_session, "p1", confidence_cutoff=0.8)
    fact = facts[("marksheet", "marks_percent")]
    assert (fact.verification_status, fact.value_json) == ("flagged", None)
    assert fact.problem == "No value could be read"


def test_failed_validator_flags_the_fact(bare_session):
    facts = make_profile(bare_session, [("marksheet", {"marks_percent": (105, 0.99)})])
    verify_profile(bare_session, "p1", confidence_cutoff=0.8)
    fact = facts[("marksheet", "marks_percent")]
    assert (fact.verification_status, fact.value_json) == ("flagged", None)
    assert "outside 0-100" in fact.problem


def test_confidence_below_cutoff_flags_the_fact(bare_session):
    facts = make_profile(bare_session, [("marksheet", {"marks_percent": (81, 0.62)})])
    verify_profile(bare_session, "p1", confidence_cutoff=0.8)
    fact = facts[("marksheet", "marks_percent")]
    assert fact.verification_status == "flagged"
    assert fact.value_json == 81.0  # the value is kept; only the status says it needs a look
    assert "Confidence 0.62 is below the 0.80 cutoff" in [c["message"] for c in fact.checks]


def test_name_conflict_flags_both_documents(bare_session):
    facts = make_profile(bare_session, [
        ("identity_card", {"name": ("Ishan Rao", 0.97)}),
        ("income_certificate", {"name": ("Ishan Rau", 0.93)}),
    ])
    verify_profile(bare_session, "p1", confidence_cutoff=0.8)
    assert set(statuses(facts).values()) == {"flagged"}
    message = next(c["message"] for c in facts[("identity_card", "name")].checks if c["name"] == "cross_document")
    assert message == "Name differs from 'Ishan Rau' on the income certificate"


def test_initials_and_case_do_not_flag_names(bare_session):
    facts = make_profile(bare_session, [
        ("identity_card", {"name": ("Ravi K. Patil", 0.98)}),
        ("income_certificate", {"name": ("Ravi Patil", 0.95)}),
        ("marksheet", {"name": ("RAVI K PATIL", 0.96)}),
    ])
    verify_profile(bare_session, "p1", confidence_cutoff=0.8)
    assert set(statuses(facts).values()) == {"verified"}


def test_date_of_birth_conflict_flags(bare_session):
    facts = make_profile(bare_session, [
        ("identity_card", {"date_of_birth": ("2006-07-21", 0.97)}),
        ("marksheet", {"date_of_birth": ("21/07/2008", 0.96)}),
    ])
    verify_profile(bare_session, "p1", confidence_cutoff=0.8)
    assert set(statuses(facts).values()) == {"flagged"}


def test_a_later_document_can_flag_an_earlier_verified_fact(bare_session):
    profile = create_profile(bare_session, "p1", actor="test")
    card = add_document(bare_session, profile, "identity_card", actor="test")
    first = add_fact(bare_session, card, "name", "Ishan Rao", 0.97, actor="test")
    verify_profile(bare_session, "p1", confidence_cutoff=0.8)
    assert first.verification_status == "verified"

    cert = add_document(bare_session, profile, "income_certificate", actor="test")
    add_fact(bare_session, cert, "name", "Ishan Rau", 0.93, actor="test")
    changes = verify_profile(bare_session, "p1", confidence_cutoff=0.8)
    assert first.verification_status == "flagged"
    assert {"fact_id": first.id, "from": "verified", "to": "flagged"}.items() <= next(
        c for c in changes if c["fact_id"] == first.id
    ).items()


def test_officer_decisions_are_not_overwritten(bare_session):
    facts = make_profile(bare_session, [
        ("identity_card", {"name": ("Ishan Rao", 0.97)}),
        ("income_certificate", {"name": ("Ishan Rau", 0.93)}),
    ])
    verify_profile(bare_session, "p1", confidence_cutoff=0.8)
    rau = facts[("income_certificate", "name")]
    set_fact_status(bare_session, rau, "rejected", actor="officer:demo", note="Typo on the certificate")
    verify_profile(bare_session, "p1", confidence_cutoff=0.8)
    assert rau.verification_status == "rejected"
    # With the misspelt copy rejected, the identity card name has nothing to disagree with.
    assert facts[("identity_card", "name")].verification_status == "verified"
