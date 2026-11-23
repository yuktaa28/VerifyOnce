"""Operations that read and write profiles, facts, applications and decisions.

Every write that matters leaves an AuditEvent. Decisions are never edited: each
submission adds a new Decision, linked to the one it supersedes.
"""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.evaluator import APPROVE, NEEDS_HUMAN_REVIEW, Observation, evaluate
from backend.extraction import DOCUMENT_FIELDS, ExtractionResult, Extractor
from backend.fields import FIELD_TYPES, to_json_value
from backend.models import (
    VERIFICATION_STATUSES,
    Application,
    AuditEvent,
    Decision,
    Document,
    Fact,
    Profile,
)
from backend.schemes import Scheme
from backend.verification import validate, verify_profile


class ProfileNotFound(LookupError):
    pass


class UnknownScheme(LookupError):
    pass


# --- Profiles, documents, facts -------------------------------------------------------


def create_profile(session: Session, profile_id: str, *, label: str | None = None, actor: str) -> Profile:
    profile = Profile(id=profile_id, label=label)
    session.add(profile)
    session.flush()
    session.add(AuditEvent(actor=actor, action="profile.created", profile_id=profile.id, details={}))
    return profile


def add_document(
    session: Session,
    profile: Profile,
    document_type: str,
    *,
    actor: str,
    filename: str | None = None,
    source: str = "upload",
    sha256: str | None = None,
    extraction: ExtractionResult | None = None,
) -> Document:
    document = Document(
        profile=profile,
        document_type=document_type,
        filename=filename,
        source=source,
        sha256=sha256,
        extraction_status=extraction.status if extraction else None,
        extraction_source=extraction.extraction_source if extraction else None,
        extraction_model=extraction.model if extraction else None,
        extraction_error=extraction.error if extraction else None,
    )
    session.add(document)
    session.flush()
    session.add(
        AuditEvent(
            actor=actor,
            action="document.added",
            profile_id=profile.id,
            details={"document_id": document.id, "document_type": document_type, "source": source},
        )
    )
    return document


def add_fact(
    session: Session,
    document: Document,
    field: str,
    raw_value: Any,
    confidence: float,
    *,
    actor: str,
) -> Fact:
    """Record one observation, unverified, after running the field's validators.

    The stored value is typed and ID numbers are cut to their last 4 digits. If the raw
    value is unreadable or fails a validator, the stored value is empty and the failed
    check says why. ``verify_profile`` then sets the status.
    """
    value, checks = validate(field, raw_value)
    fact = Fact(
        document=document,
        field=field,
        value_type=FIELD_TYPES[field],
        value_json=to_json_value(field, value) if value is not None else None,
        confidence=confidence,
        verification_status="unverified",
        checks=[c.as_dict() for c in checks],
    )
    session.add(fact)
    session.flush()
    session.add(
        AuditEvent(
            actor=actor,
            action="fact.observed",
            profile_id=document.profile_id,
            fact_id=fact.id,
            details={"field": field, "document_id": document.id, "confidence": confidence,
                     "readable": value is not None},
        )
    )
    return fact


def ingest_document(
    session: Session,
    extractor: Extractor,
    profile_id: str,
    document_type: str,
    content: bytes,
    *,
    filename: str | None,
    actor: str,
    confidence_cutoff: float,
) -> tuple[Document, ExtractionResult, list[dict[str, Any]]]:
    """Extract one uploaded document, store its facts, and re-verify the whole profile.

    A failed extraction still stores one empty fact per field, so every field is
    flagged and the profile goes to an officer instead of silently missing data.
    """
    profile = session.get(Profile, profile_id)
    if profile is None:
        profile = create_profile(session, profile_id, actor=actor)
    result = extractor.extract(content, document_type)
    document = add_document(
        session, profile, document_type, actor=actor, filename=filename, source="upload",
        sha256=hashlib.sha256(content).hexdigest(), extraction=result,
    )
    for field in DOCUMENT_FIELDS[document_type]:
        reading = result.readings.get(field) if result.ok else None
        add_fact(
            session, document, field,
            reading["value"] if reading else None,
            float(reading["confidence"]) if reading else 0.0,
            actor=f"extractor:{result.extraction_source}",
        )
    changes = verify_profile(session, profile_id, confidence_cutoff=confidence_cutoff)
    return document, result, changes


def set_fact_status(
    session: Session, fact: Fact, status: str, *, actor: str, note: str | None = None
) -> AuditEvent:
    """An officer marks a fact verified or rejected. Automatic checks will not override it."""
    if status not in VERIFICATION_STATUSES:
        raise ValueError(f"status must be one of {VERIFICATION_STATUSES}")
    previous = fact.verification_status
    fact.verification_status = status
    fact.reviewed_by = actor
    event = AuditEvent(
        actor=actor,
        action="fact.status_changed",
        profile_id=fact.document.profile_id,
        fact_id=fact.id,
        details={"from": previous, "to": status, "note": note},
    )
    session.add(event)
    session.flush()
    return event


def profile_observations(session: Session, profile_id: str) -> list[Observation]:
    facts = session.scalars(
        select(Fact)
        .join(Document)
        .where(Document.profile_id == profile_id)
        .options(selectinload(Fact.document))
        .order_by(Fact.id)
    )
    return [
        Observation(
            field=f.field,
            value=f.value,
            confidence=f.confidence,
            fact_id=f.id,
            document_id=f.document_id,
            document_type=f.document.document_type,
            verification_status=f.verification_status,
            officer_reviewed=f.reviewed_by is not None,
            problem=f.problem,
        )
        for f in facts
    ]


def fact_payload(fact: Fact) -> dict[str, Any]:
    return {
        "fact_id": fact.id,
        "document_id": fact.document_id,
        "field": fact.field,
        "value": fact.value_json,
        "confidence": fact.confidence,
        "verification_status": fact.verification_status,
        "reviewed_by": fact.reviewed_by,
        "checks": fact.checks,
    }


# --- Applications and decisions -------------------------------------------------------


def submit_application(
    session: Session,
    schemes: dict[str, Scheme],
    profile_id: str,
    scheme_id: str,
    *,
    actor: str,
    confidence_cutoff: float,
) -> tuple[Decision, AuditEvent]:
    """Evaluate a scheme for a profile and store the result as a new, immutable Decision."""
    if session.get(Profile, profile_id) is None:
        raise ProfileNotFound(profile_id)
    scheme = schemes.get(scheme_id)
    if scheme is None:
        raise UnknownScheme(scheme_id)

    application = session.scalar(
        select(Application).where(Application.profile_id == profile_id, Application.scheme_id == scheme_id)
    )
    if application is None:
        application = Application(profile_id=profile_id, scheme_id=scheme_id)
        session.add(application)
        session.flush()
        session.add(
            AuditEvent(
                actor=actor,
                action="application.created",
                profile_id=profile_id,
                application_id=application.id,
                details={"scheme_id": scheme_id},
            )
        )

    previous = latest_decision(session, application.id)
    result = evaluate(scheme, profile_observations(session, profile_id), confidence_cutoff)

    decision = Decision(
        application_id=application.id,
        sequence=(previous.sequence + 1) if previous else 1,
        supersedes_decision_id=previous.id if previous else None,
        scheme_id=scheme.id,
        ruleset_version=scheme.ruleset_version,
        confidence_cutoff=confidence_cutoff,
        verdict=result.verdict,
        reason=result.reason,
        explanation=None,
        rule_outcomes=result.rule_outcomes_json(),
        evaluated_values=result.evaluated_values,
        source_facts=result.source_facts,
        mismatches=result.mismatches,
        review_reasons=result.review_reasons_json(),
        decided_by="evaluator",
    )
    session.add(decision)
    session.flush()

    audit = AuditEvent(
        actor=actor,
        action="decision.resubmitted" if previous else "decision.created",
        profile_id=profile_id,
        application_id=application.id,
        decision_id=decision.id,
        details={
            "verdict": decision.verdict,
            "ruleset_version": decision.ruleset_version,
            "sequence": decision.sequence,
            "supersedes_decision_id": decision.supersedes_decision_id,
            "previous_verdict": previous.verdict if previous else None,
        },
    )
    session.add(audit)
    session.flush()
    return decision, audit


def latest_decision(session: Session, application_id: int) -> Decision | None:
    return session.scalar(
        select(Decision)
        .where(Decision.application_id == application_id)
        .order_by(Decision.sequence.desc())
        .limit(1)
    )


def possible_schemes(
    session: Session,
    schemes: dict[str, Scheme],
    profile_id: str,
    *,
    exclude: str | None,
    confidence_cutoff: float,
) -> list[dict[str, Any]]:
    """Other schemes this profile may qualify for: a dry run that stores nothing.

    Lists schemes that would be approved, then those that would go to review.
    """
    observations = profile_observations(session, profile_id)
    found = []
    for scheme in schemes.values():
        if scheme.id == exclude:
            continue
        result = evaluate(scheme, observations, confidence_cutoff)
        if result.verdict in (APPROVE, NEEDS_HUMAN_REVIEW):
            found.append(
                {
                    "scheme_id": scheme.id,
                    "scheme_name": scheme.name,
                    "predicted_verdict": result.verdict,
                    "reason": result.reason,
                }
            )
    found.sort(key=lambda s: (s["predicted_verdict"] != APPROVE, s["scheme_id"]))
    return found


def decision_payload(
    decision: Decision,
    *,
    profile_id: str,
    scheme: Scheme,
    audit_event_id: int | None,
    possible: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "decision_id": decision.id,
        "application_id": decision.application_id,
        "profile_id": profile_id,
        "scheme_id": scheme.id,
        "scheme_name": scheme.name,
        "ruleset_version": decision.ruleset_version,
        "sequence": decision.sequence,
        "supersedes_decision_id": decision.supersedes_decision_id,
        "verdict": decision.verdict,
        "reason": decision.reason,
        "explanation": decision.explanation,
        "confidence_cutoff": decision.confidence_cutoff,
        "rule_outcomes": decision.rule_outcomes,
        "source_facts": decision.source_facts,
        "evaluated_values": decision.evaluated_values,
        "mismatches": decision.mismatches,
        "review_reasons": decision.review_reasons,
        "possible_schemes": possible,
        "decided_by": decision.decided_by,
        "audit_event_id": audit_event_id,
        "created_at": decision.created_at,
    }
