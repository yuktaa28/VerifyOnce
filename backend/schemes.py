"""Scheme definitions loaded from YAML files in ``schemes/``.

Each scheme is one YAML file named after its id. Rules use a small shared set of
operators, so adding a scheme that only uses known fields and operators needs no
code change:

    gte     field value >= value
    lte     field value <= value
    eq      field value == value (text compared case- and space-insensitively)
    in      field value is one of a list of values
    exists  at least one document shows the field (takes no value)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from backend.fields import FIELD_TYPES, FieldValueError, coerce_value

Operator = Literal["gte", "lte", "eq", "in", "exists"]
OPERATORS: tuple[str, ...] = ("gte", "lte", "eq", "in", "exists")


class SchemeError(ValueError):
    """A scheme file is missing, malformed, or refers to unknown fields or operators."""


class Rule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    field: str
    op: Operator
    value: Any = None
    description: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "Rule":
        if self.field not in FIELD_TYPES:
            raise ValueError(f"rule {self.id!r}: unknown field {self.field!r}")
        if self.op == "exists":
            if self.value is not None:
                raise ValueError(f"rule {self.id!r}: 'exists' takes no value")
            return self
        if self.value is None:
            raise ValueError(f"rule {self.id!r}: operator {self.op!r} needs a value")
        if self.op == "in" and (not isinstance(self.value, list) or not self.value):
            raise ValueError(f"rule {self.id!r}: 'in' needs a non-empty list")
        if self.op in ("gte", "lte") and FIELD_TYPES[self.field] == "str":
            raise ValueError(f"rule {self.id!r}: {self.op!r} cannot compare text field {self.field!r}")
        try:
            self.expected()
        except FieldValueError as exc:
            raise ValueError(f"rule {self.id!r}: {exc}") from exc
        return self

    def expected(self) -> Any:
        """The rule's value converted to the field's type (a list for 'in', None for 'exists')."""
        if self.op == "exists":
            return None
        if self.op == "in":
            return [coerce_value(self.field, v) for v in self.value]
        return coerce_value(self.field, self.value)


class Scheme(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str
    ruleset_version: str
    description: str = ""
    required_fields: tuple[str, ...] = ()
    rules: tuple[Rule, ...]

    @field_validator("id")
    @classmethod
    def _id_format(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9_]+", value):
            raise ValueError("scheme id must use lower-case letters, digits and underscores")
        return value

    @field_validator("required_fields")
    @classmethod
    def _known_fields(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unknown = [f for f in value if f not in FIELD_TYPES]
        if unknown:
            raise ValueError(f"unknown required fields: {unknown}")
        return value

    @model_validator(mode="after")
    def _check_rules(self) -> "Scheme":
        if not self.rules:
            raise ValueError("a scheme needs at least one rule")
        ids = [r.id for r in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate rule ids: {ids}")
        return self

    @property
    def evidence_fields(self) -> list[str]:
        """Fields whose evidence must be present, confident and consistent before deciding.

        These are the identity fields in ``required_fields`` plus every field a rule
        compares. Fields used only by ``exists`` rules are presence checks, not evidence.
        """
        fields = list(self.required_fields)
        for rule in self.rules:
            if rule.op != "exists" and rule.field not in fields:
                fields.append(rule.field)
        return fields

    @property
    def referenced_fields(self) -> list[str]:
        fields = self.evidence_fields
        for rule in self.rules:
            if rule.field not in fields:
                fields.append(rule.field)
        return fields


def load_scheme(path: Path) -> Scheme:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SchemeError(f"{path.name}: cannot read YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise SchemeError(f"{path.name}: expected a mapping at the top level")
    try:
        scheme = Scheme.model_validate(data)
    except ValidationError as exc:
        raise SchemeError(f"{path.name}: {exc}") from exc
    if scheme.id != path.stem:
        raise SchemeError(f"{path.name}: scheme id {scheme.id!r} must match the file name")
    return scheme


def load_schemes(directory: Path) -> dict[str, Scheme]:
    """Load every ``*.yaml`` scheme in a directory, keyed and sorted by scheme id."""
    directory = Path(directory)
    if not directory.is_dir():
        raise SchemeError(f"schemes directory not found: {directory}")
    schemes: dict[str, Scheme] = {}
    for path in sorted(directory.glob("*.yaml")):
        scheme = load_scheme(path)
        schemes[scheme.id] = scheme
    if not schemes:
        raise SchemeError(f"no scheme files in {directory}")
    return dict(sorted(schemes.items()))
