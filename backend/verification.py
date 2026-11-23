"""Deterministic checks that move facts from ``unverified`` to ``verified`` or ``flagged``.

A fact is flagged when any of these hold, otherwise it is verified:

* its value is null (unreadable). Model confidence is not calibrated, so a null is
  always flagged, whatever confidence came with it;
* a validator fails (date of birth must parse and be plausible, income must be a
  non-negative whole number, marks must be 0-100, id_last4 must be 4 digits);
* its confidence is below the cutoff (default 0.80);
* for name and date of birth, it disagrees with the same field on another document.

A failed validator leaves the stored value empty, so a bad reading can never reach
the evaluator. Facts an officer has reviewed (``reviewed_by`` set) keep the officer's
status; automatic checks never overwrite it.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.evaluator import values_agree
from backend.fields import document_label, field_label, format_value, parse_date
from backend.models import AuditEvent, Document, Fact

VALIDATOR = "validator"
CONFIDENCE = "confidence"
CROSS_DOCUMENT = "cross_document"
CROSS_CHECKED_FIELDS = ("name", "date_of_birth")
EARLIEST_BIRTH_DATE = date(1900, 1, 1)

VERIFIED = "verified"
FLAGGED = "flagged"


@dataclass(frozen=True)
class Check:
    name: str
    kind: str
    passed: bool
    message: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ok(name: str, message: str) -> Check:
    return Check(name, VALIDATOR, True, message)


def _fail(name: str, message: str) -> Check:
    return Check(name, VALIDATOR, False, message)


# --- Validators ---------------------------------------------------------------------------


def validate(field: str, raw: Any, *, today: date | None = None) -> tuple[Any, list[Check]]:
    """Return (typed value, checks). The value is None when unreadable or any check fails."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, [_fail("readable", "No value could be read")]
    checks = [_ok("readable", f"{field_label(field)} was read")]
    value, field_checks = _VALIDATORS[field](raw, today or date.today())
    checks.extend(field_checks)
    return (value if all(c.passed for c in checks) else None), checks


def _validate_text(field: str):
    def check(raw: Any, _today: date) -> tuple[Any, list[Check]]:
        text = " ".join(str(raw).split())
        if not any(ch.isalpha() for ch in text):
            return None, [_fail("has_letters", f"{field_label(field)} contains no letters")]
        return text, [_ok("has_letters", f"{field_label(field)} contains letters")]

    return check


def _validate_birth_date(raw: Any, today: date) -> tuple[Any, list[Check]]:
    try:
        parsed = parse_date(raw)
    except (TypeError, ValueError):
        return None, [_fail("date_parses", f"Date of birth {str(raw)!r} is not a valid date")]
    checks = [_ok("date_parses", f"Date of birth {parsed.isoformat()} is a valid date")]
    if parsed > today:
        checks.append(_fail("date_plausible", f"Date of birth {parsed.isoformat()} is in the future"))
    elif parsed < EARLIEST_BIRTH_DATE:
        checks.append(_fail("date_plausible", f"Date of birth {parsed.isoformat()} is before 1900"))
    else:
        checks.append(_ok("date_plausible", "Date of birth is between 1900 and today"))
    return parsed, checks


def _validate_income(raw: Any, _today: date) -> tuple[Any, list[Check]]:
    name = "non_negative_integer"
    if isinstance(raw, bool):
        return None, [_fail(name, "Annual income is not a number")]
    if isinstance(raw, float):
        if not raw.is_integer():
            return None, [_fail(name, f"Annual income {raw} is not a whole number of rupees")]
        raw = int(raw)
    if isinstance(raw, str):
        text = re.sub(r"(?i)\b(inr|rs)\b\.?|[₹,\s]|/-$", "", raw)
        if not re.fullmatch(r"-?\d+", text):
            return None, [_fail(name, f"Annual income {raw!r} is not a whole number of rupees")]
        raw = int(text)
    if not isinstance(raw, int):
        return None, [_fail(name, "Annual income is not a number")]
    if raw < 0:
        return None, [_fail(name, f"Annual income {raw} is negative")]
    return raw, [_ok(name, "Annual income is a non-negative whole number")]


def _validate_marks(raw: Any, _today: date) -> tuple[Any, list[Check]]:
    name = "range_0_100"
    if isinstance(raw, bool):
        return None, [_fail(name, "Marks percentage is not a number")]
    try:
        value = float(str(raw).replace("%", "").strip()) if isinstance(raw, str) else float(raw)
    except (TypeError, ValueError):
        return None, [_fail(name, f"Marks percentage {raw!r} is not a number")]
    if not 0 <= value <= 100:
        return None, [_fail(name, f"Marks percentage {value:g} is outside 0-100")]
    return value, [_ok(name, "Marks percentage is between 0 and 100")]


def _validate_id_last4(raw: Any, _today: date) -> tuple[Any, list[Check]]:
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) < 4:
        # Never echo the digits: the message is stored and shown.
        return None, [_fail("four_digits", f"ID number has {len(digits)} readable digits; 4 are needed")]
    return digits[-4:], [_ok("four_digits", "Last 4 digits of the ID number were read")]


_VALIDATORS = {
    "name": _validate_text("name"),
    "state": _validate_text("state"),
    "date_of_birth": _validate_birth_date,
    "annual_income_inr": _validate_income,
    "marks_percent": _validate_marks,
    "id_last4": _validate_id_last4,
}


# --- Fact status ------------------------------------------------------------------------


def confidence_check(confidence: float, cutoff: float) -> Check:
    if confidence >= cutoff:
        return Check(CONFIDENCE, CONFIDENCE, True, f"Confidence {confidence:.2f} meets the {cutoff:.2f} cutoff")
    return Check(CONFIDENCE, CONFIDENCE, False, f"Confidence {confidence:.2f} is below the {cutoff:.2f} cutoff")


def cross_document_check(fact: Fact, others: list[Fact]) -> Check:
    label = field_label(fact.field)
    if not others:
        return Check(CROSS_DOCUMENT, CROSS_DOCUMENT, True, f"No other document shows {label.lower()} yet")
    disagree = [o for o in others if not values_agree(fact.field, fact.value, o.value)]
    if not disagree:
        docs = ", ".join(sorted({document_label(o.document.document_type) for o in others}))
        return Check(CROSS_DOCUMENT, CROSS_DOCUMENT, True, f"{label} matches the {docs}")
    parts = [
        f"'{format_value(o.field, o.value)}' on the {document_label(o.document.document_type)}" for o in disagree
    ]
    return Check(CROSS_DOCUMENT, CROSS_DOCUMENT, False, f"{label} differs from " + ", ".join(parts))


def verify_profile(
    session: Session, profile_id: str, *, confidence_cutoff: float, actor: str = "system"
) -> list[dict[str, Any]]:
    """Re-run the checks on every fact of a profile. Returns the status changes made.

    Run after every new document: a fact verified earlier can become flagged when a
    later document disagrees with it.
    """
    facts = list(
        session.scalars(
            select(Fact)
            .join(Document)
            .where(Document.profile_id == profile_id)
            .options(selectinload(Fact.document))
            .order_by(Fact.id)
        )
    )
    comparable = [f for f in facts if f.verification_status != "rejected" and f.value_json is not None]
    changes = []
    for fact in facts:
        if fact.reviewed_by is not None:
            continue  # an officer decided this fact
        checks = [c for c in fact.checks or [] if c.get("kind") == VALIDATOR]
        checks.append(confidence_check(fact.confidence, confidence_cutoff).as_dict())
        if fact.field in CROSS_CHECKED_FIELDS and fact.value_json is not None:
            others = [o for o in comparable if o.field == fact.field and o.document_id != fact.document_id]
            checks.append(cross_document_check(fact, others).as_dict())

        status = VERIFIED if fact.value_json is not None and all(c["passed"] for c in checks) else FLAGGED
        if checks != fact.checks:
            fact.checks = checks
        if status != fact.verification_status:
            reasons = [c["message"] for c in checks if not c["passed"]]
            changes.append(
                {"fact_id": fact.id, "field": fact.field, "document_id": fact.document_id,
                 "from": fact.verification_status, "to": status, "reasons": reasons}
            )
            session.add(
                AuditEvent(
                    actor=actor,
                    action="fact.status_changed",
                    profile_id=profile_id,
                    fact_id=fact.id,
                    details={"from": fact.verification_status, "to": status, "reasons": reasons},
                )
            )
            fact.verification_status = status
    session.flush()
    return changes
