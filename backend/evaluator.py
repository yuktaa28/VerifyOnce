"""Deterministic eligibility evaluator. Plain code decides every verdict; no LLM is involved.

Verdict precedence (the first matching step wins):

1. ``needs_human_review``: evidence for a field the scheme needs is missing,
   unreadable or invalid on some document, below the confidence cutoff (default
   0.80), or conflicts across documents.
2. ``reject``: the evidence is reliable and at least one eligibility rule fails.
3. ``approve``: the evidence is reliable and every rule passes.

Review wins over reject on purpose. A rejection built on a misread or mismatched
document would deny a citizen a benefit they qualify for, so unreliable evidence
always goes to a person. Every rule is still evaluated and recorded (pass, fail or
unresolved), even when the verdict is review.

Evidence rules, per field:

* Observations an officer marked ``rejected`` are ignored.
* An observation an officer marked ``verified`` counts as confidence 1.0. (Automatic
  verification does not change confidence.)
* An observation with no value (unreadable, or it failed a validator) makes the field
  ``invalid``: the document cannot be checked against the others.
* The field is low-confidence when its best readable observation is below the cutoff.
  A value exactly at the cutoff is accepted, and a low-confidence reading that agrees
  with a confident one is covered by it.
* The field conflicts when any two readable observations disagree (names use
  ``backend.names.names_match``; other text ignores case and spacing).
* The value used is the readable observation with the highest confidence.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from dataclasses import field as dc_field
from typing import Any

from backend.config import DEFAULT_CONFIDENCE_CUTOFF
from backend.fields import FIELD_TYPES, document_label, field_label, format_value, to_json_value
from backend.names import names_match
from backend.schemes import Rule, Scheme

APPROVE = "approve"
REJECT = "reject"
NEEDS_HUMAN_REVIEW = "needs_human_review"

PASS = "pass"
FAIL = "fail"
UNRESOLVED = "unresolved"

MISSING = "missing"
INVALID = "invalid"
LOW_CONFIDENCE = "low_confidence"
CONFLICT = "conflict"


@dataclass(frozen=True)
class Observation:
    """One value for one field, read from one document."""

    field: str
    value: Any
    confidence: float
    fact_id: int | None = None
    document_id: int | None = None
    document_type: str | None = None
    verification_status: str = "unverified"
    officer_reviewed: bool = False
    problem: str | None = None  # why value is None (unreadable / failed validator)

    @property
    def effective_confidence(self) -> float:
        if self.verification_status == "verified" and self.officer_reviewed:
            return 1.0
        return self.confidence

    def as_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "document_id": self.document_id,
            "document_type": self.document_type,
            "field": self.field,
            "value": to_json_value(self.field, self.value),
            "confidence": self.confidence,
            "verification_status": self.verification_status,
            "problem": self.problem,
        }


@dataclass
class FieldEvidence:
    field: str
    observations: list[Observation]
    value: Any = None
    issues: list[str] = dc_field(default_factory=list)
    groups: list[list[Observation]] = dc_field(default_factory=list)

    @property
    def readable(self) -> list[Observation]:
        return [o for o in self.observations if o.value is not None]

    @property
    def unreadable(self) -> list[Observation]:
        return [o for o in self.observations if o.value is None]

    @property
    def best_confidence(self) -> float | None:
        readable = self.readable
        return readable[0].effective_confidence if readable else None

    @property
    def fact_ids(self) -> list[int]:
        return [o.fact_id for o in self.observations if o.fact_id is not None]


@dataclass(frozen=True)
class RuleOutcome:
    rule_id: str
    field: str
    operator: str
    expected: Any
    actual: Any
    outcome: str
    evidence_issues: list[str]
    source_fact_ids: list[int]
    message: str


@dataclass(frozen=True)
class ReviewReason:
    field: str
    issue: str
    message: str
    fact_ids: list[int]


@dataclass
class EvaluationResult:
    scheme_id: str
    scheme_name: str
    ruleset_version: str
    confidence_cutoff: float
    verdict: str
    reason: str
    rule_outcomes: list[RuleOutcome]
    review_reasons: list[ReviewReason]
    mismatches: list[dict[str, Any]]
    evaluated_values: dict[str, dict[str, Any]]
    source_facts: list[dict[str, Any]]

    def rule_outcomes_json(self) -> list[dict[str, Any]]:
        return [asdict(o) for o in self.rule_outcomes]

    def review_reasons_json(self) -> list[dict[str, Any]]:
        return [asdict(r) for r in self.review_reasons]


def values_agree(field_name: str, a: Any, b: Any) -> bool:
    if field_name == "name":
        return names_match(str(a), str(b))
    kind = FIELD_TYPES.get(field_name)
    if kind == "str":
        return _norm_text(a) == _norm_text(b)
    if kind == "float":
        return math.isclose(float(a), float(b), abs_tol=1e-6)
    return a == b


def resolve_field(field_name: str, observations: list[Observation], cutoff: float) -> FieldEvidence:
    """Collect one field's observations and flag missing, invalid, low-confidence or conflicting evidence."""
    usable = [o for o in observations if o.field == field_name and o.verification_status != "rejected"]
    usable.sort(key=lambda o: (-o.effective_confidence, o.fact_id or 0))
    evidence = FieldEvidence(field=field_name, observations=usable)
    if not usable:
        evidence.issues.append(MISSING)
        return evidence
    if evidence.unreadable:
        evidence.issues.append(INVALID)

    readable = evidence.readable
    if not readable:
        return evidence
    evidence.value = readable[0].value
    # Group observations that all agree with each other; more than one group is a conflict.
    for obs in readable:
        for group in evidence.groups:
            if all(values_agree(field_name, obs.value, other.value) for other in group):
                group.append(obs)
                break
        else:
            evidence.groups.append([obs])
    if len(evidence.groups) > 1:
        evidence.issues.append(CONFLICT)
    if readable[0].effective_confidence < cutoff:
        evidence.issues.append(LOW_CONFIDENCE)
    return evidence


def evaluate(
    scheme: Scheme,
    observations: list[Observation],
    confidence_cutoff: float = DEFAULT_CONFIDENCE_CUTOFF,
) -> EvaluationResult:
    """Decide one scheme for one profile's observations. Pure: no I/O, no randomness."""
    evidence = {f: resolve_field(f, observations, confidence_cutoff) for f in scheme.referenced_fields}

    review_reasons: list[ReviewReason] = []
    mismatches: list[dict[str, Any]] = []
    for name in scheme.evidence_fields:
        ev = evidence[name]
        for issue in ev.issues:
            review_reasons.append(
                ReviewReason(name, issue, _issue_message(ev, issue, confidence_cutoff), ev.fact_ids)
            )
        if CONFLICT in ev.issues:
            mismatches.append(_mismatch(ev))

    rule_outcomes = [_evaluate_rule(rule, evidence[rule.field], confidence_cutoff) for rule in scheme.rules]

    if review_reasons or any(o.outcome == UNRESOLVED for o in rule_outcomes):
        verdict = NEEDS_HUMAN_REVIEW
    elif any(o.outcome == FAIL for o in rule_outcomes):
        verdict = REJECT
    else:
        verdict = APPROVE

    evaluated_values = {
        name: {
            "value": to_json_value(name, ev.value),
            "confidence": ev.best_confidence,
            "fact_ids": ev.fact_ids,
            "issues": list(ev.issues),
        }
        for name, ev in evidence.items()
    }
    source_facts = [o.as_dict() for name in scheme.referenced_fields for o in evidence[name].observations]

    return EvaluationResult(
        scheme_id=scheme.id,
        scheme_name=scheme.name,
        ruleset_version=scheme.ruleset_version,
        confidence_cutoff=confidence_cutoff,
        verdict=verdict,
        reason=_reason(scheme, verdict, rule_outcomes, review_reasons),
        rule_outcomes=rule_outcomes,
        review_reasons=review_reasons,
        mismatches=mismatches,
        evaluated_values=evaluated_values,
        source_facts=source_facts,
    )


def _evaluate_rule(rule: Rule, ev: FieldEvidence, cutoff: float) -> RuleOutcome:
    expected = rule.expected()
    issues = list(ev.issues)

    if rule.op == "exists":
        present = bool(ev.readable)
        outcome = PASS if present else FAIL
        return RuleOutcome(
            rule.id, rule.field, rule.op, None, present, outcome, [], ev.fact_ids,
            _rule_message(rule, outcome, None),
        )

    if not ev.readable:
        outcome = UNRESOLVED
    elif CONFLICT in ev.issues:
        # Conflicting values only leave the rule unresolved if they would decide it differently.
        results = {_compare(rule, group[0].value, expected) for group in ev.groups}
        outcome = (PASS if results == {True} else FAIL) if len(results) == 1 else UNRESOLVED
    else:
        outcome = PASS if _compare(rule, ev.value, expected) else FAIL

    return RuleOutcome(
        rule_id=rule.id,
        field=rule.field,
        operator=rule.op,
        expected=to_json_value(rule.field, expected),
        actual=to_json_value(rule.field, ev.value),
        outcome=outcome,
        evidence_issues=issues,
        source_fact_ids=ev.fact_ids,
        message=_rule_message(rule, outcome, ev.value) if outcome != UNRESOLVED
        else f"{field_label(rule.field)} could not be checked: "
        f"{_lower_first(_issue_message(ev, issues[0], cutoff))}",
    )


def _compare(rule: Rule, actual: Any, expected: Any) -> bool:
    if rule.op == "gte":
        return actual >= expected
    if rule.op == "lte":
        return actual <= expected
    if rule.op == "eq":
        return _equal(actual, expected)
    if rule.op == "in":
        return any(_equal(actual, e) for e in expected)
    raise ValueError(f"unknown operator {rule.op!r}")  # pragma: no cover


def _equal(a: Any, b: Any) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        return _norm_text(a) == _norm_text(b)
    return a == b


def _norm_text(value: Any) -> str:
    return " ".join(str(value).split()).casefold()


def _rule_message(rule: Rule, outcome: str, actual: Any) -> str:
    label = field_label(rule.field)
    passed = outcome == PASS
    if rule.op == "exists":
        return f"{label} is on file" if passed else f"No document shows {_lower_first(label)}"
    shown = format_value(rule.field, actual)
    expected = format_value(rule.field, rule.expected())
    if rule.op == "gte":
        return f"{label} {shown} meets the minimum of {expected}" if passed \
            else f"{label} {shown} is below the minimum of {expected}"
    if rule.op == "lte":
        return f"{label} {shown} is within the limit of {expected}" if passed \
            else f"{label} {shown} is above the limit of {expected}"
    if rule.op == "eq":
        return f"{label} is {shown}, as required" if passed else f"{label} is {shown}, but must be {expected}"
    return f"{label} {shown} is one of the accepted values" if passed \
        else f"{label} {shown} is not one of: {expected}"


def _issue_message(ev: FieldEvidence, issue: str, cutoff: float) -> str:
    label = field_label(ev.field)
    if issue == MISSING:
        return f"{label} is missing from the documents"
    if issue == INVALID:
        parts = [
            f"on the {document_label(o.document_type)}: {_lower_first(o.problem or 'No value could be read')}"
            for o in ev.unreadable
        ]
        return f"{label} could not be used " + "; ".join(parts)
    if issue == LOW_CONFIDENCE:
        best = ev.readable[0]
        return (
            f"{label} was read with low confidence ({best.effective_confidence:.0%}, below the "
            f"{cutoff:.0%} cutoff) from the {document_label(best.document_type)}"
        )
    parts = []
    for group in ev.groups:
        docs = ", ".join(document_label(o.document_type) for o in group)
        parts.append(f"'{format_value(ev.field, group[0].value)}' ({docs})")
    return f"{label} differs across documents: " + " vs ".join(parts)


def _mismatch(ev: FieldEvidence) -> dict[str, Any]:
    return {
        "field": ev.field,
        "message": _issue_message(ev, CONFLICT, 0.0),
        "groups": [
            {
                "value": to_json_value(ev.field, group[0].value),
                "observations": [
                    {
                        "fact_id": o.fact_id,
                        "document_id": o.document_id,
                        "document_type": o.document_type,
                        "value": to_json_value(ev.field, o.value),
                        "confidence": o.confidence,
                    }
                    for o in group
                ],
            }
            for group in ev.groups
        ],
    }


def _reason(
    scheme: Scheme, verdict: str, outcomes: list[RuleOutcome], review_reasons: list[ReviewReason]
) -> str:
    passed = [_lower_first(o.message) for o in outcomes if o.outcome == PASS]
    failed = [_lower_first(o.message) for o in outcomes if o.outcome == FAIL]
    if verdict == NEEDS_HUMAN_REVIEW:
        problems = [_lower_first(r.message) for r in review_reasons] or [
            _lower_first(o.message) for o in outcomes if o.outcome == UNRESOLVED
        ]
        return (
            f"Sent to an officer for review. {scheme.name} was not decided automatically because "
            + "; ".join(problems) + "."
        )
    if verdict == REJECT:
        text = f"Not eligible for {scheme.name}: " + "; ".join(failed) + "."
        if passed:
            text += " Passed: " + "; ".join(passed) + "."
        return text
    return f"Approved for {scheme.name}: " + "; ".join(passed) + "."


def _lower_first(text: str) -> str:
    """Lower-case the first letter unless it starts an acronym ("ID number" stays as is)."""
    if len(text) > 1 and text[0].isupper() and text[1].islower():
        return text[0].lower() + text[1:]
    return text
