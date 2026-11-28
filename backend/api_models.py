"""Request and response bodies for the HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class ApplicationRequest(BaseModel):
    scheme_id: str


class EvidenceResolutionRequest(BaseModel):
    fact_id: int
    resolution: Literal["confirm", "reject"]
    reason: str = ""
    officer: str = ""


class OfficerDecisionRequest(BaseModel):
    action: Literal["approve", "reject", "override"]
    verdict: Literal["approve", "reject"] | None = None
    reason: str = ""
    officer: str = ""


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
    requirement: str | None = None
    source_documents: list[str] = []


class SourceFactOut(BaseModel):
    fact_id: int | None
    document_id: int | None
    document_type: str | None
    field: str
    value: Any
    confidence: float
    verification_status: str
    problem: str | None = None
    low_quality_image: bool = False


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
    officer_brief: str | None = None
    explanation_source: str | None = None  # "generated" or "template"; never part of the verdict
    explanation_model: str | None = None
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
