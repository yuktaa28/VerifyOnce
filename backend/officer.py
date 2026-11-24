"""Officer workflow: queue, case detail, evidence resolution, decisions, audit history.

Rules enforced here, not in the UI:

* Every officer action creates a new Decision and an AuditEvent linked to the
  application's original decision. Decisions are superseded, never edited.
* Resolving evidence (confirming or rejecting a fact) and overriding the evaluator
  both require a typed reason. So does rejecting an application.
* Approval is blocked while any flagged fact the scheme relies on is unresolved. A
  plain approval also needs the rules to pass on the current evidence; approving
  anyway is an override, with a reason.
* After evidence is resolved, the application is re-evaluated (a new Decision). Other
  applications of the same profile that the evaluator decided are re-evaluated too,
  because the facts they rely on have changed.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.evaluator import APPROVE, NEEDS_HUMAN_REVIEW, REJECT, EvaluationResult, evaluate
from backend.fields import document_label, field_label
from backend.models import Application, AuditEvent, Decision, Document, Fact, Profile
from backend.schemes import Scheme
from backend.service import (
    decision_payload,
    fact_payload,
    is_officer_decision,
    latest_decision,
    original_decision,
    profile_observations,
    record_decision,
)
from backend.verification import verify_profile

MIN_REASON_LENGTH = 5
VERDICT_ORDER = {NEEDS_HUMAN_REVIEW: 0, REJECT: 1, APPROVE: 2}
OFFICER_ACTIONS = {
    "approve": "decision.officer_approved",
    "reject": "decision.officer_rejected",
    "override": "decision.officer_overridden",
}


class CaseNotFound(LookupError):
    pass


class ReasonRequired(ValueError):
    pass


class ActionRefused(RuntimeError):
    """The action is not allowed in the case's current state (HTTP 409)."""


def _require_reason(reason: str | None, what: str) -> str:
    text = " ".join((reason or "").split())
    if len(text) < MIN_REASON_LENGTH:
        raise ReasonRequired(f"{what} needs a typed reason (at least {MIN_REASON_LENGTH} characters)")
    return text


def _require_officer(officer: str | None) -> str:
    name = " ".join((officer or "").split())
    if not name:
        raise ReasonRequired("Enter the officer's name")
    return name


def _load(session: Session, schemes: dict[str, Scheme], application_id: int) -> tuple[Application, Scheme]:
    application = session.get(Application, application_id)
    if application is None or application.scheme_id not in schemes:
        raise CaseNotFound(f"Application {application_id} not found")
    return application, schemes[application.scheme_id]


def _evaluate(session: Session, application: Application, scheme: Scheme, cutoff: float) -> EvaluationResult:
    return evaluate(scheme, profile_observations(session, application.profile_id), cutoff)


def unresolved_flags(session: Session, application: Application, scheme: Scheme) -> list[Fact]:
    """Flagged facts of the fields this scheme relies on that no officer has resolved yet."""
    facts = session.scalars(
        select(Fact)
        .join(Document)
        .where(Document.profile_id == application.profile_id, Fact.verification_status == "flagged")
        .options(selectinload(Fact.document))
        .order_by(Fact.id)
    )
    return [f for f in facts if f.field in scheme.referenced_fields]


def _blockers(flags: list[Fact], result: EvaluationResult) -> list[str]:
    blockers = [
        f"{field_label(f.field)} on the {document_label(f.document.document_type)} is flagged and unresolved"
        for f in flags
    ]
    if not flags and result.verdict != APPROVE:
        blockers.append(f"The rules do not pass on the current evidence (evaluator: {result.verdict}). "
                        "Use override with a reason to approve anyway.")
    return blockers


# --- Queue --------------------------------------------------------------------------------


def officer_queue(session: Session, schemes: dict[str, Scheme], verdicts: list[str] | None = None) -> list[dict]:
    """Every application with its latest decision, needs_human_review first, newest first within a verdict."""
    rows = []
    applications = session.scalars(select(Application).options(selectinload(Application.profile)))
    for application in applications:
        decision = latest_decision(session, application.id)
        scheme = schemes.get(application.scheme_id)
        if decision is None or scheme is None or (verdicts and decision.verdict not in verdicts):
            continue
        rows.append({
            "application_id": application.id,
            "profile_id": application.profile_id,
            "profile_label": application.profile.label,
            "scheme_id": scheme.id,
            "scheme_name": scheme.name,
            "verdict": decision.verdict,
            "decided_by": decision.decided_by,
            "decided_at": decision.created_at,
            "decision_id": decision.id,
            "sequence": decision.sequence,
            "unresolved_flags": len(unresolved_flags(session, application, scheme)),
            "reason": decision.reason,
        })
    rows.sort(key=lambda r: (VERDICT_ORDER.get(r["verdict"], 9), -r["decision_id"]))
    return rows


# --- Case detail --------------------------------------------------------------------------


def case_detail(session: Session, schemes: dict[str, Scheme], application_id: int, *, cutoff: float) -> dict:
    application, scheme = _load(session, schemes, application_id)
    decision = latest_decision(session, application.id)
    current = _evaluate(session, application, scheme, cutoff)
    flags = unresolved_flags(session, application, scheme)
    blockers = _blockers(flags, current)
    documents = session.scalars(
        select(Document).where(Document.profile_id == application.profile_id)
        .options(selectinload(Document.facts)).order_by(Document.id)
    ).all()
    return {
        "application_id": application.id,
        "profile_id": application.profile_id,
        "profile_label": application.profile.label,
        "scheme": {"id": scheme.id, "name": scheme.name, "ruleset_version": scheme.ruleset_version,
                   "referenced_fields": scheme.referenced_fields},
        "decision": decision_payload(decision, profile_id=application.profile_id, scheme=scheme,
                                     audit_event_id=None, possible=[]) if decision else None,
        "decision_is_officer": bool(decision and is_officer_decision(decision)),
        "current_evaluation": {
            "verdict": current.verdict,
            "reason": current.reason,
            "review_reasons": current.review_reasons_json(),
            "mismatches": current.mismatches,
        },
        "unresolved_flags": [fact_payload(f) for f in flags],
        "can_approve": not blockers,
        "approve_blockers": blockers,
        "documents": [
            {
                "document_id": doc.id,
                "document_type": doc.document_type,
                "filename": doc.filename,
                "source": doc.source,
                "image_quality": doc.image_quality,
                "extraction": {"status": doc.extraction_status, "source": doc.extraction_source,
                               "model": doc.extraction_model, "error": doc.extraction_error},
                "facts": [fact_payload(f) for f in doc.facts],
            }
            for doc in documents
        ],
        "decisions": [
            {"decision_id": d.id, "sequence": d.sequence, "verdict": d.verdict, "decided_by": d.decided_by,
             "supersedes_decision_id": d.supersedes_decision_id, "reason": d.reason, "created_at": d.created_at}
            for d in application.decisions
        ],
    }


# --- Evidence resolution ------------------------------------------------------------------


def resolve_evidence(
    session: Session,
    schemes: dict[str, Scheme],
    application_id: int,
    *,
    fact_id: int,
    resolution: str,
    reason: str | None,
    officer: str | None,
    cutoff: float,
) -> tuple[Decision, AuditEvent]:
    """An officer confirms or rejects one fact, with a reason. The case is then re-evaluated."""
    reason = _require_reason(reason, "Resolving evidence")
    officer = _require_officer(officer)
    if resolution not in ("confirm", "reject"):
        raise ReasonRequired("resolution must be 'confirm' or 'reject'")
    application, scheme = _load(session, schemes, application_id)
    fact = session.get(Fact, fact_id)
    if fact is None or fact.document.profile_id != application.profile_id:
        raise CaseNotFound(f"Fact {fact_id} is not part of application {application_id}")

    actor = f"officer:{officer}"
    previous_status = fact.verification_status
    fact.verification_status = "verified" if resolution == "confirm" else "rejected"
    fact.reviewed_by = actor
    session.flush()
    # Other facts' cross-document checks may change (e.g. rejecting a misspelt name
    # clears the conflict on the documents that agree).
    verify_profile(session, application.profile_id, confidence_cutoff=cutoff)

    details = {
        "trigger": "evidence_resolved",
        "resolution": resolution,
        "field": fact.field,
        "document_type": fact.document.document_type,
        "fact_from": previous_status,
        "fact_to": fact.verification_status,
        "reason": reason,
    }
    result = _evaluate(session, application, scheme, cutoff)
    decision, audit = record_decision(
        session, application, scheme, result, confidence_cutoff=cutoff, decided_by="evaluator",
        actor=actor, action=f"evidence.{'confirmed' if resolution == 'confirm' else 'rejected'}",
        fact_id=fact.id, details=details,
    )
    _reevaluate_other_applications(session, schemes, application, actor=actor, fact_id=fact.id, cutoff=cutoff)
    return decision, audit


def _reevaluate_other_applications(session, schemes, application, *, actor, fact_id, cutoff) -> None:
    others = session.scalars(
        select(Application).where(Application.profile_id == application.profile_id, Application.id != application.id)
    )
    for other in others:
        scheme = schemes.get(other.scheme_id)
        latest = latest_decision(session, other.id)
        if scheme is None or latest is None or is_officer_decision(latest):
            continue  # an officer's decision stands until an officer changes it
        result = _evaluate(session, other, scheme, cutoff)
        record_decision(
            session, other, scheme, result, confidence_cutoff=cutoff, decided_by="evaluator", actor=actor,
            action="decision.reevaluated", fact_id=fact_id,
            details={"trigger": "evidence_resolved_in_another_case", "case_application_id": application.id},
        )


# --- Officer decisions --------------------------------------------------------------------


def officer_decide(
    session: Session,
    schemes: dict[str, Scheme],
    application_id: int,
    *,
    action: str,
    reason: str | None,
    officer: str | None,
    verdict: str | None = None,
    cutoff: float,
) -> tuple[Decision, AuditEvent]:
    """approve / reject / override. Each creates a new Decision decided by the officer."""
    if action not in OFFICER_ACTIONS:
        raise ReasonRequired(f"action must be one of {sorted(OFFICER_ACTIONS)}")
    officer = _require_officer(officer)
    application, scheme = _load(session, schemes, application_id)
    result = _evaluate(session, application, scheme, cutoff)
    flags = unresolved_flags(session, application, scheme)
    note = " ".join((reason or "").split())

    if action == "approve":
        blockers = _blockers(flags, result)
        if blockers:
            raise ActionRefused("Approval is blocked: " + "; ".join(blockers))
        final = APPROVE
        text = f"Approved by officer {officer} after review." + (f" Note: {note}" if note else "")
    elif action == "reject":
        note = _require_reason(reason, "Rejecting an application")
        final = REJECT
        text = f"Rejected by officer {officer}: {note}"
    else:
        note = _require_reason(reason, "Overriding the evaluator")
        if verdict not in (APPROVE, REJECT):
            raise ReasonRequired("An override needs a verdict: approve or reject")
        if verdict == APPROVE and flags:
            raise ActionRefused("Approval is blocked: " + "; ".join(_blockers(flags, result)))
        final = verdict
        text = f"Officer {officer} overrode the evaluator ({result.verdict} to {verdict}): {note}"

    return record_decision(
        session, application, scheme, result, confidence_cutoff=cutoff, decided_by=f"officer:{officer}",
        actor=f"officer:{officer}", action=OFFICER_ACTIONS[action], verdict=final, reason=text,
        details={"officer_action": action, "officer_reason": note or None},
    )


# --- Audit history ------------------------------------------------------------------------


def application_audit(session: Session, application_id: int) -> dict[str, Any]:
    application = session.get(Application, application_id)
    if application is None:
        raise CaseNotFound(f"Application {application_id} not found")
    first = original_decision(session, application_id)
    decisions = {d.id: d for d in application.decisions}
    events = session.scalars(
        select(AuditEvent).where(AuditEvent.application_id == application_id).order_by(AuditEvent.id)
    )
    entries = []
    for event in events:
        decision = decisions.get(event.decision_id)
        entries.append({
            "audit_event_id": event.id,
            "at": event.created_at,
            "actor": event.actor,
            "action": event.action,
            "decision_id": event.decision_id,
            "decision_sequence": decision.sequence if decision else None,
            "verdict": decision.verdict if decision else None,
            "is_original_decision": bool(first and event.decision_id == first.id),
            "fact_id": event.fact_id,
            "reason": event.details.get("officer_reason") or event.details.get("reason"),
            "details": event.details,
        })
    return {
        "application_id": application_id,
        "original_decision_id": first.id if first else None,
        "decisions": [
            {"decision_id": d.id, "sequence": d.sequence, "verdict": d.verdict, "decided_by": d.decided_by,
             "supersedes_decision_id": d.supersedes_decision_id, "reason": d.reason, "created_at": d.created_at}
            for d in application.decisions
        ],
        "events": entries,
    }


def list_profiles(session: Session) -> list[dict[str, Any]]:
    profiles = session.scalars(
        select(Profile).options(selectinload(Profile.documents), selectinload(Profile.applications)).order_by(Profile.id)
    )
    return [
        {"profile_id": p.id, "label": p.label, "documents": len(p.documents), "applications": len(p.applications)}
        for p in profiles
    ]


def profile_applications(session: Session, schemes: dict[str, Scheme], profile_id: str) -> list[dict[str, Any]]:
    applications = session.scalars(
        select(Application).where(Application.profile_id == profile_id).order_by(Application.id)
    )
    rows = []
    for application in applications:
        scheme = schemes.get(application.scheme_id)
        decision = latest_decision(session, application.id)
        if scheme is None or decision is None:
            continue
        rows.append({
            "application_id": application.id,
            "scheme_id": scheme.id,
            "scheme_name": scheme.name,
            "decided_by_officer": is_officer_decision(decision),
            "decision": decision_payload(decision, profile_id=profile_id, scheme=scheme,
                                         audit_event_id=None, possible=[]),
        })
    return rows
