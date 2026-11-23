"""The profile fields VerifyOnce knows about, their types, and how to read and show them.

Full ID numbers are never stored: anything written to ``id_last4`` is cut down to its
last four digits here, before it can reach the database.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

FIELD_TYPES: dict[str, str] = {
    "name": "str",
    "date_of_birth": "date",
    "state": "str",
    "annual_income_inr": "int",
    "marks_percent": "float",
    "id_last4": "str",
}

FIELD_LABELS: dict[str, str] = {
    "name": "Name",
    "date_of_birth": "Date of birth",
    "state": "State",
    "annual_income_inr": "Annual income",
    "marks_percent": "Marks percentage",
    "id_last4": "ID number (last 4 digits)",
}

DOCUMENT_LABELS: dict[str, str] = {
    "identity_card": "identity card",
    "income_certificate": "income certificate",
    "marksheet": "marksheet",
}


class FieldValueError(ValueError):
    """A raw value cannot be read as the field's type."""


def mask_id(raw: Any) -> str:
    """Keep only the last four digits of an ID number ("XXXX XXXX 2051" -> "2051")."""
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) < 4:
        raise FieldValueError("An ID number needs at least 4 digits")
    return digits[-4:]


def coerce_value(field: str, raw: Any) -> Any:
    """Turn a raw extracted value into the field's typed value, or raise FieldValueError."""
    if field not in FIELD_TYPES:
        raise FieldValueError(f"Unknown field {field!r}")
    if raw is None:
        raise FieldValueError(f"{field}: value is missing")
    if field == "id_last4":
        return mask_id(raw)

    kind = FIELD_TYPES[field]
    try:
        if kind == "str":
            text = " ".join(str(raw).split())
            if not text:
                raise FieldValueError(f"{field}: value is empty")
            return text
        if kind == "int":
            return _to_int(field, raw)
        if kind == "float":
            return _to_percent(field, raw)
        if kind == "date":
            return parse_date(raw)
    except (TypeError, ValueError) as exc:
        if isinstance(exc, FieldValueError):
            raise
        raise FieldValueError(f"{field}: cannot read {raw!r} as {kind}") from exc
    raise FieldValueError(f"{field}: unsupported type {kind}")  # pragma: no cover


def _to_int(field: str, raw: Any) -> int:
    if isinstance(raw, bool):
        raise FieldValueError(f"{field}: expected a number")
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, float):
        if not raw.is_integer():
            raise FieldValueError(f"{field}: expected a whole number")
        value = int(raw)
    else:
        text = re.sub(r"(?i)\b(inr|rs)\b\.?|[₹,\s]", "", str(raw))
        value = int(text)
    if value < 0:
        raise FieldValueError(f"{field}: cannot be negative")
    return value


def _to_percent(field: str, raw: Any) -> float:
    if isinstance(raw, bool):
        raise FieldValueError(f"{field}: expected a number")
    value = float(str(raw).replace("%", "").strip()) if isinstance(raw, str) else float(raw)
    if not 0 <= value <= 100:
        raise FieldValueError(f"{field}: must be between 0 and 100")
    return value


def parse_date(raw: Any) -> date:
    """ISO dates (2006-07-21) or Indian day-first dates (21/07/2006, 21-07-2006, 21.07.2006)."""
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    text = str(raw).strip()
    day_first = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", text)
    if day_first:
        day, month, year = (int(part) for part in day_first.groups())
        return date(year, month, day)
    return date.fromisoformat(text)


def to_json_value(field: str, value: Any) -> Any:
    """Typed value -> JSON-safe value (dates become ISO strings)."""
    if isinstance(value, (list, tuple)):
        return [to_json_value(field, v) for v in value]
    if isinstance(value, date):
        return value.isoformat()
    return value


def from_json_value(field: str, stored: Any) -> Any:
    """JSON value from the database -> typed value."""
    if stored is None:
        return None
    if FIELD_TYPES.get(field) == "date":
        return date.fromisoformat(stored)
    if FIELD_TYPES.get(field) == "float":
        return float(stored)
    return stored


def format_inr(amount: int) -> str:
    """Indian digit grouping: 300000 -> "₹3,00,000"."""
    sign = "-" if amount < 0 else ""
    digits = str(abs(int(amount)))
    if len(digits) <= 3:
        return f"{sign}₹{digits}"
    head, tail = digits[:-3], digits[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return f"{sign}₹{','.join(groups)},{tail}"


def format_value(field: str, value: Any) -> str:
    """Human-readable value for plain-language reasons."""
    if isinstance(value, (list, tuple)):
        return ", ".join(format_value(field, v) for v in value)
    if field == "annual_income_inr" and isinstance(value, (int, float)):
        return format_inr(int(value))
    if field == "marks_percent" and isinstance(value, (int, float)):
        return f"{float(value):g}%"
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def field_label(field: str) -> str:
    return FIELD_LABELS.get(field, field.replace("_", " ").capitalize())


def document_label(document_type: str | None) -> str:
    if not document_type:
        return "unknown document"
    return DOCUMENT_LABELS.get(document_type, document_type.replace("_", " "))
