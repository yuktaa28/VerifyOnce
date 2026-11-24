"""Evaluator precedence, confidence cutoff, operators, and YAML-only schemes."""

from __future__ import annotations

import itertools
from datetime import date

import pytest

from backend.evaluator import (
    APPROVE,
    CONFLICT,
    FAIL,
    INVALID,
    LOW_CONFIDENCE,
    LOW_QUALITY_IMAGE,
    MISSING,
    NEEDS_HUMAN_REVIEW,
    PASS,
    REJECT,
    UNRESOLVED,
    Observation,
    evaluate,
)
from backend.schemes import SchemeError, load_schemes

SCHOLARSHIP = "state_student_scholarship"
_ids = itertools.count(1)


def obs(field, value, confidence=0.95, doc="identity_card", status="unverified", officer=False, problem=None,
        low_quality=False):
    return Observation(
        field, value, confidence, fact_id=next(_ids), document_type=doc, verification_status=status,
        officer_reviewed=officer, problem=problem, low_quality_image=low_quality,
    )


def profile(*, income=240_000, marks=78.5, income_conf=0.95, second_name="Ravi Patil", extra=()):
    return [
        obs("name", "Ravi K. Patil"),
        obs("date_of_birth", date(2006, 3, 14)),
        obs("id_last4", "2051"),
        obs("name", second_name, doc="income_certificate"),
        obs("annual_income_inr", income, income_conf, doc="income_certificate"),
        obs("marks_percent", marks, doc="marksheet"),
        *extra,
    ]


def outcome(result, rule_id):
    return next(o for o in result.rule_outcomes if o.rule_id == rule_id)


# --- Precedence -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("evidence_reliable", "rule_fails", "expected"),
    [
        (True, False, APPROVE),  # step 3: all rules pass
        (True, True, REJECT),  # step 2: a rule fails on reliable evidence
        (False, False, NEEDS_HUMAN_REVIEW),  # step 1: unreliable evidence
        (False, True, NEEDS_HUMAN_REVIEW),  # step 1 beats step 2
    ],
)
def test_verdict_precedence(schemes, evidence_reliable, rule_fails, expected):
    observations = profile(
        income=900_000 if rule_fails else 240_000,
        second_name="Ravi K. Patil" if evidence_reliable else "Ravi Pateel",
    )
    assert evaluate(schemes[SCHOLARSHIP], observations).verdict == expected


def test_review_still_records_the_failing_rule(schemes):
    result = evaluate(schemes[SCHOLARSHIP], profile(income=900_000, second_name="Ravi Pateel"))
    assert result.verdict == NEEDS_HUMAN_REVIEW
    assert outcome(result, "income_limit").outcome == FAIL
    assert [r.issue for r in result.review_reasons] == [CONFLICT]


def test_every_rule_is_recorded_with_pass_and_fail(schemes):
    result = evaluate(schemes[SCHOLARSHIP], profile(marks=52.0))
    assert result.verdict == REJECT
    assert [(o.rule_id, o.outcome) for o in result.rule_outcomes] == [
        ("income_limit", PASS),
        ("minimum_marks", FAIL),
    ]
    income = outcome(result, "income_limit")
    assert (income.actual, income.expected, income.operator) == (240_000, 300_000, "lte")
    assert income.source_fact_ids
    assert "below the minimum of 60%" in result.reason
    assert "Passed: annual income ₹2,40,000 is within the limit of ₹3,00,000" in result.reason


def test_missing_evidence_needs_review(schemes):
    observations = [o for o in profile() if o.field != "marks_percent"]
    result = evaluate(schemes[SCHOLARSHIP], observations)
    assert result.verdict == NEEDS_HUMAN_REVIEW
    assert outcome(result, "minimum_marks").outcome == UNRESOLVED
    assert [(r.field, r.issue) for r in result.review_reasons] == [("marks_percent", MISSING)]


def test_missing_identity_field_needs_review(schemes):
    observations = [o for o in profile() if o.field != "id_last4"]
    result = evaluate(schemes[SCHOLARSHIP], observations)
    assert result.verdict == NEEDS_HUMAN_REVIEW
    assert result.review_reasons[0].field == "id_last4"


def test_conflicting_numbers_need_review(schemes):
    extra = (obs("annual_income_inr", 250_000, doc="bank_statement"),)
    result = evaluate(schemes[SCHOLARSHIP], profile(extra=extra))
    assert result.verdict == NEEDS_HUMAN_REVIEW
    assert result.mismatches[0]["field"] == "annual_income_inr"
    # Both values pass the limit, so the rule itself is still decided.
    assert outcome(result, "income_limit").outcome == PASS


def test_conflict_that_changes_the_rule_result_is_unresolved(schemes):
    extra = (obs("annual_income_inr", 350_000, doc="bank_statement"),)
    result = evaluate(schemes[SCHOLARSHIP], profile(extra=extra))
    assert result.verdict == NEEDS_HUMAN_REVIEW
    assert outcome(result, "income_limit").outcome == UNRESOLVED


# --- Confidence cutoff ------------------------------------------------------------------


def test_confidence_below_cutoff_needs_review(schemes):
    result = evaluate(schemes[SCHOLARSHIP], profile(income_conf=0.79))
    assert result.verdict == NEEDS_HUMAN_REVIEW
    assert [(r.field, r.issue) for r in result.review_reasons] == [("annual_income_inr", LOW_CONFIDENCE)]
    assert ("annual income was read from the income certificate with confidence 79%, below the 85% cutoff"
            in result.reason)


def test_default_cutoff_rejects_a_coarse_point_eight(schemes):
    """Models report coarse values like 0.8; that must not count as confident."""
    assert evaluate(schemes[SCHOLARSHIP], profile(income_conf=0.80)).verdict == NEEDS_HUMAN_REVIEW
    assert evaluate(schemes[SCHOLARSHIP], profile(income_conf=0.90)).verdict == APPROVE


def test_confidence_exactly_at_cutoff_is_accepted(schemes):
    assert evaluate(schemes[SCHOLARSHIP], profile(income_conf=0.85)).verdict == APPROVE


def test_cutoff_is_configurable(schemes):
    observations = profile(income_conf=0.85)
    assert evaluate(schemes[SCHOLARSHIP], observations).verdict == APPROVE
    assert evaluate(schemes[SCHOLARSHIP], observations, confidence_cutoff=0.90).verdict == NEEDS_HUMAN_REVIEW
    assert evaluate(schemes[SCHOLARSHIP], profile(income_conf=0.70), confidence_cutoff=0.60).verdict == APPROVE


def test_high_confidence_copy_covers_a_low_confidence_one(schemes):
    extra = (obs("annual_income_inr", 240_000, 0.97, doc="bank_statement"),)
    assert evaluate(schemes[SCHOLARSHIP], profile(income_conf=0.40, extra=extra)).verdict == APPROVE


# --- Low-quality images -------------------------------------------------------------------


def blurry_marksheet_profile(*, marks_conf=1.0, dob=date(2006, 3, 14), officer=False):
    """Every marksheet reading comes from a blurry image, however confident the model was."""
    return [
        o for o in profile() if o.document_type != "marksheet"
    ] + [
        obs("name", "Ravi Patil", 1.0, doc="marksheet", low_quality=True),
        obs("date_of_birth", dob, 1.0, doc="marksheet", low_quality=True),
        obs("marks_percent", 78.5, marks_conf, doc="marksheet", low_quality=True,
            status="verified" if officer else "flagged", officer=officer),
    ]


def test_field_read_only_from_a_blurry_image_needs_review_even_at_full_confidence(schemes):
    result = evaluate(schemes[SCHOLARSHIP], blurry_marksheet_profile(marks_conf=1.0))
    assert result.verdict == NEEDS_HUMAN_REVIEW
    assert [(r.field, r.issue) for r in result.review_reasons] == [("marks_percent", LOW_QUALITY_IMAGE)]
    assert result.reason.endswith(
        "because marks percentage comes only from the marksheet, whose image is blurry or low-contrast.")
    assert ";" not in result.review_reasons[0].message  # joins cleanly with other reasons


def test_blurry_reading_that_agrees_with_a_good_document_is_covered(schemes):
    # name and date of birth also come from the identity card, so only marks need review
    result = evaluate(schemes[SCHOLARSHIP], blurry_marksheet_profile())
    assert {r.field for r in result.review_reasons} == {"marks_percent"}
    assert evaluate(schemes["income_based_fee_waiver"], blurry_marksheet_profile()).verdict == APPROVE


def test_blurry_reading_that_disagrees_is_still_a_conflict(schemes):
    result = evaluate(schemes["income_based_fee_waiver"], blurry_marksheet_profile(dob=date(2004, 3, 14)))
    assert result.verdict == NEEDS_HUMAN_REVIEW
    assert [(r.field, r.issue) for r in result.review_reasons] == [("date_of_birth", CONFLICT)]


def test_officer_can_confirm_a_reading_from_a_blurry_image(schemes):
    assert evaluate(schemes[SCHOLARSHIP], blurry_marksheet_profile(officer=True)).verdict == APPROVE


def test_officer_verified_fact_counts_as_full_confidence(schemes):
    observations = [
        o if o.field != "annual_income_inr"
        else obs("annual_income_inr", 240_000, 0.40, status="verified", officer=True)
        for o in profile()
    ]
    assert evaluate(schemes[SCHOLARSHIP], observations).verdict == APPROVE


def test_automatic_verification_does_not_raise_confidence(schemes):
    observations = [
        o if o.field != "annual_income_inr" else obs("annual_income_inr", 240_000, 0.40, status="verified")
        for o in profile()
    ]
    assert evaluate(schemes[SCHOLARSHIP], observations).verdict == NEEDS_HUMAN_REVIEW


def test_unreadable_value_on_any_document_needs_review(schemes):
    extra = (obs("name", None, 0.0, doc="marksheet", status="flagged", problem="No value could be read"),)
    result = evaluate(schemes[SCHOLARSHIP], profile(extra=extra))
    assert result.verdict == NEEDS_HUMAN_REVIEW
    assert [(r.field, r.issue) for r in result.review_reasons] == [("name", INVALID)]
    assert "name could not be used on the marksheet: no value could be read" in result.reason


def test_field_with_only_invalid_values_is_unresolved(schemes):
    observations = [
        o if o.field != "marks_percent"
        else obs("marks_percent", None, 0.9, doc="marksheet", problem="Marks percentage 105 is outside 0-100")
        for o in profile()
    ]
    result = evaluate(schemes[SCHOLARSHIP], observations)
    assert result.verdict == NEEDS_HUMAN_REVIEW
    assert outcome(result, "minimum_marks").outcome == UNRESOLVED
    assert "marks percentage 105 is outside 0-100" in result.reason


def test_officer_rejected_fact_is_ignored(schemes):
    extra = (obs("name", "Ravi Pateel", 0.99, doc="marksheet", status="rejected"),)
    assert evaluate(schemes[SCHOLARSHIP], profile(extra=extra)).verdict == APPROVE


# --- Operators and YAML-only schemes ----------------------------------------------------

NEW_SCHEME_YAML = """
id: southern_merit_award
name: Southern Merit Award
ruleset_version: "test-1"
required_fields: [name]
rules:
  - {id: state_ok, field: state, op: in, value: [Karnataka, Kerala, Tamil Nadu]}
  - {id: has_id, field: id_last4, op: exists}
  - {id: born_2006, field: date_of_birth, op: gte, value: 2006-01-01}
  - {id: top_marks, field: marks_percent, op: eq, value: 100}
"""


def test_new_scheme_needs_only_a_yaml_file(tmp_path):
    (tmp_path / "southern_merit_award.yaml").write_text(NEW_SCHEME_YAML, encoding="utf-8")
    scheme = load_schemes(tmp_path)["southern_merit_award"]

    observations = [
        obs("name", "Kavya Nair"),
        obs("state", "  kerala "),
        obs("id_last4", "4408"),
        obs("date_of_birth", date(2006, 5, 30)),
        obs("marks_percent", 100.0, doc="marksheet"),
    ]
    result = evaluate(scheme, observations)
    assert result.verdict == APPROVE
    assert {o.rule_id: o.outcome for o in result.rule_outcomes} == {
        "state_ok": PASS, "has_id": PASS, "born_2006": PASS, "top_marks": PASS,
    }

    no_id = [o for o in observations if o.field != "id_last4"]
    result = evaluate(scheme, no_id)
    assert result.verdict == REJECT  # 'exists' is a rule, not required evidence
    assert outcome(result, "has_id").outcome == FAIL

    wrong_state = [o if o.field != "state" else obs("state", "Gujarat") for o in observations]
    assert outcome(evaluate(scheme, wrong_state), "state_ok").outcome == FAIL


@pytest.mark.parametrize(
    "rules",
    [
        "[{id: r, field: shoe_size, op: gte, value: 1}]",  # unknown field
        "[{id: r, field: marks_percent, op: between, value: 1}]",  # unknown operator
        "[{id: r, field: marks_percent, op: gte}]",  # missing value
        "[{id: r, field: state, op: in, value: Kerala}]",  # 'in' needs a list
        "[{id: r, field: state, op: gte, value: Kerala}]",  # cannot order text
        "[{id: r, field: id_last4, op: exists, value: 1}]",  # 'exists' takes no value
        "[]",  # no rules
    ],
)
def test_invalid_scheme_files_are_refused(tmp_path, rules):
    (tmp_path / "bad.yaml").write_text(
        f'id: bad\nname: Bad\nruleset_version: "1"\nrules: {rules}\n', encoding="utf-8"
    )
    with pytest.raises(SchemeError):
        load_schemes(tmp_path)


def test_scheme_thresholds_and_versions_come_from_yaml(schemes):
    scholarship, waiver = schemes[SCHOLARSHIP], schemes["income_based_fee_waiver"]
    assert scholarship.ruleset_version and waiver.ruleset_version
    assert {r.id: r.value for r in scholarship.rules} == {"income_limit": 300000, "minimum_marks": 60}
    assert {r.id: r.value for r in waiver.rules} == {"income_limit": 450000}


def test_evaluation_is_deterministic(schemes):
    observations = profile(marks=52.0)
    first = evaluate(schemes[SCHOLARSHIP], observations)
    second = evaluate(schemes[SCHOLARSHIP], list(reversed(observations)))
    assert first.verdict == second.verdict
    assert first.reason == second.reason
    assert first.rule_outcomes_json() == second.rule_outcomes_json()
