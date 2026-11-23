"""Request and response bodies for the HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class ApplicationRequest(BaseModel):
    scheme_id: str


class RuleOutcomeOut(BaseModel):
    rule_id: str
    field: str
    operator: str
    expected: Any
    actual: Any
    outcome: str
    evidence_issues: list[str]
    source_fact_ids: list[int]
    message: str


class SourceFactOut(BaseModel):
    fact_id: int | None
    document_id: int | None
    document_type: str | None
    field: str
    value: Any
    confidence: float
    verification_status: str
    problem: str | None = None


class MismatchOut(BaseModel):
    field: str
    message: str
    groups: list[dict[str, Any]]


class ReviewReasonOut(BaseModel):
    field: str
    issue: str
    message: str
    fact_ids: list[int]


class PossibleSchemeOut(BaseModel):
    scheme_id: str
    scheme_name: str
    predicted_verdict: str
    reason: str


class DecisionOut(BaseModel):
    decision_id: int
    application_id: int
    profile_id: str
    scheme_id: str
    scheme_name: str
    ruleset_version: str
    sequence: int
    supersedes_decision_id: int | None
    verdict: str
    reason: str
    explanation: str | None
    confidence_cutoff: float
    rule_outcomes: list[RuleOutcomeOut]
    source_facts: list[SourceFactOut]
    evaluated_values: dict[str, Any]
    mismatches: list[MismatchOut]
    review_reasons: list[ReviewReasonOut]
    possible_schemes: list[PossibleSchemeOut]
    decided_by: str
    audit_event_id: int | None
    created_at: datetime
