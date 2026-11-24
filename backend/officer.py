"""Officer workflow: queue, case detail, evidence resolution, decisions, audit history.

Rules enforced here, not in the UI:

* An application whose decision is needs_human_review is "awaiting officer decision".
  It must end with an explicit officer decision: approve, reject, or override with a
  typed reason. Nothing automatic can close it, and the citizen cannot resubmit over it.
* Resolving evidence (confirming or rejecting a fact) needs a typed reason and records
  an AuditEvent. The case is re-evaluated and the result is shown as a *suggested*
  verdict only; no Decision is recorded, and the case stays in the queue.
* Every officer decision creates a new Decision and an AuditEvent linked to the
  application's original decision. Decisions are superseded, never edited.
* Approval is blocked while any flagged fact the scheme relies on is unresolved. A
  plain approval also needs the suggested verdict to be approve; approving anyway is
  an override, with a reason. Rejecting also needs a reason.
* When evidence changes, other applications of the same profile that were decided
  automatically (approve or reject, no officer) are re-evaluated, since the facts they
  rely on changed. Ones awaiting an officer or decided by an officer are left alone.
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
    AWAITING_OFFICER,
    DECIDED_AUTOMATICALLY,
    DECIDED_BY_OFFICER,
    application_status,
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
STATUS_ORDER = {AWAITING_OFFICER: 0, DECIDED_AUTOMATICALLY: 1, DECIDED_BY_OFFICER: 2}
VERDICT_ORDER = {NEEDS_HUMAN_REVIEW: 0, REJECT: 1, APPROVE: 2}
VERDICT_WORDS = {APPROVE: "approve", REJECT: "reject (not eligible)", NEEDS_HUMAN_REVIEW: "needs officer review"}
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
        blockers.append(f"The rules do not pass on the current evidence (suggested: {result.verdict}). "
                        "Use override with a reason to approve anyway.")
    return blockers


# --- Queue --------------------------------------------------------------------------------


def officer_queue(session: Session, schemes: dict[str, Scheme], verdicts: list[str] | None = None,
                  *, cutoff: float) -> list[dict]:
    """Every application with its latest decision. Cases awaiting an officer come first."""
    rows = []
    applications = session.scalars(select(Application).options(selectinload(Application.profile)))
    for application in applications:
        decision = latest_decision(session, application.id)
        scheme = schemes.get(application.scheme_id)
        if decision is None or scheme is None or (verdicts and decision.verdict not in verdicts):
            continue
        status = application_status(decision)
        suggested = _evaluate(session, application, scheme, cutoff).verdict if status == AWAITING_OFFICER else None
        rows.append({
            "application_id": application.id,
            "profile_id": application.profile_id,
            "profile_label": application.profile.label,
            "scheme_id": scheme.id,
            "scheme_name": scheme.name,
            "status": status,
            "verdict": decision.verdict,
            "suggested_verdict": suggested,
            "decided_by": decision.decided_by,
            "decided_at": decision.created_at,
            "decision_id": decision.id,
            "sequence": decision.sequence,
            "unresolved_flags": len(unresolved_flags(session, application, scheme)),
            "reason": decision.reason,
        })
    rows.sort(key=lambda r: (STATUS_ORDER[r["status"]], VERDICT_ORDER.get(r["verdict"], 9), -r["decision_id"]))
    return rows


# --- Case detail --------------------------------------------------------------------------


def case_detail(session: Session, schemes: dict[str, Scheme], application_id: int, *, cutoff: float) -> dict:
    application, scheme = _load(session, schemes, application_id)
    decision = latest_decision(session, application.id)
    current = _evaluate(session, application, scheme, cutoff)
    flags = unresolved_flags(session, application, scheme)
    blockers = _blockers(flags, current)
    status = application_status(decision) if decision else AWAITING_OFFICER
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
        "status": status,
        "awaiting_officer_decision": status == AWAITING_OFFICER,
        "decision": decision_payload(decision, profile_id=application.profile_id, scheme=scheme,
                                     audit_event_id=None, possible=[]) if decision else None,
        "decision_is_officer": bool(decision and is_officer_decision(decision)),
        "current_evaluation": {
            "verdict": current.verdict,
            "reason": current.reason,
            "review_reasons": current.review_reasons_json(),
            "mismatches": current.mismatches,
        },
        "suggested_verdict": current.verdict,
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
) -> dict[str, Any]:
    """An officer confirms or rejects one fact, with a reason.

    Records an AuditEvent and returns the re-evaluated *suggested* verdict. No Decision
    is recorded for this case: it still needs an explicit officer decision.
    """
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

    result = _evaluate(session, application, scheme, cutoff)
    flags = unresolved_flags(session, application, scheme)
    decision = latest_decision(session, application.id)
    first = original_decision(session, application.id)
    awaiting = decision is None or application_status(decision) == AWAITING_OFFICER
    event = AuditEvent(
        actor=actor,
        action=f"evidence.{'confirmed' if resolution == 'confirm' else 'rejected'}",
        profile_id=application.profile_id,
        application_id=application.id,
        decision_id=decision.id if decision else None,  # the decision under review
        fact_id=fact.id,
        details={
            "resolution": resolution,
            "field": fact.field,
            "document_type": fact.document.document_type,
            "fact_from": previous_status,
            "fact_to": fact.verification_status,
            "reason": reason,
            "suggested_verdict": result.verdict,
            "awaiting_officer_decision": awaiting,
            "original_decision_id": first.id if first else None,
        },
    )
    session.add(event)
    session.flush()
    _reevaluate_automatic_applications(session, schemes, application.profile_id, actor=actor,
                                       fact_id=fact.id, cutoff=cutoff)
    blockers = _blockers(flags, result)
    return {
        "audit_event_id": event.id,
        "application_id": application.id,
        "fact": fact_payload(fact),
        "status": application_status(latest_decision(session, application.id)) if decision else AWAITING_OFFICER,
        "suggested_verdict": result.verdict,
        "suggested_reason": result.reason,
        "unresolved_flags": len(flags),
        "can_approve": not blockers,
        "approve_blockers": blockers,
    }


def _reevaluate_automatic_applications(session, schemes, profile_id, *, actor, fact_id, cutoff) -> None:
    """Refresh automatic approve/reject decisions whose evidence just changed."""
    for application in session.scalars(select(Application).where(Application.profile_id == profile_id)):
        scheme = schemes.get(application.scheme_id)
        latest = latest_decision(session, application.id)
        if scheme is None or latest is None or application_status(latest) != DECIDED_AUTOMATICALLY:
            continue  # awaiting an officer, or decided by one: only an officer closes those
        result = _evaluate(session, application, scheme, cutoff)
        if result.verdict == latest.verdict:
            continue
        record_decision(
            session, application, scheme, result, confidence_cutoff=cutoff, decided_by="evaluator", actor=actor,
            action="decision.reevaluated", fact_id=fact_id, details={"trigger": "evidence_resolved"},
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
    """approve / reject / override: the officer's final decision, recorded as a new Decision."""
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
        text = f"Officer {officer} overrode the suggested verdict ({result.verdict} to {verdict}): {note}"

    return record_decision(
        session, application, scheme, result, confidence_cutoff=cutoff, decided_by=f"officer:{officer}",
        actor=f"officer:{officer}", action=OFFICER_ACTIONS[action], verdict=final, reason=text,
        details={"officer_action": action, "officer_reason": note or None, "suggested_verdict": result.verdict},
    )


# --- Audit history ------------------------------------------------------------------------


def _verdict_words(verdict: str | None) -> str:
    return VERDICT_WORDS.get(verdict or "", verdict or "")


def describe_event(event: AuditEvent, decision: Decision | None, *, is_original: bool) -> str:
    """Plain-language line for the audit trail."""
    d = event.details or {}
    verdict = _verdict_words(decision.verdict if decision else d.get("verdict"))
    if event.action == "application.created":
        return "Application submitted"
    if event.action == "decision.created":
        text = f"Automatic assessment: {verdict}" + (" (original decision)" if is_original else "")
        return text + (". Awaiting officer decision." if decision and decision.verdict == NEEDS_HUMAN_REVIEW else "")
    if event.action == "decision.resubmitted":
        return f"Automatic reassessment after the citizen applied again: {verdict}"
    if event.action == "decision.reevaluated":
        return f"Automatic reassessment after evidence changed: {verdict}"
    if event.action in ("evidence.confirmed", "evidence.rejected"):
        did = "confirmed" if event.action == "evidence.confirmed" else "rejected"
        text = (f"Evidence {did}: {field_label(d.get('field', ''))} on the "
                f"{document_label(d.get('document_type'))}. Suggested verdict now: "
                f"{_verdict_words(d.get('suggested_verdict'))}")
        return text + (". Still awaiting officer decision." if d.get("awaiting_officer_decision") else ".")
    if event.action == "decision.officer_approved":
        return "Final officer decision: approved"
    if event.action == "decision.officer_rejected":
        return "Final officer decision: rejected"
    if event.action == "decision.officer_overridden":
        return (f"Final officer decision: override from {_verdict_words(d.get('suggested_verdict'))} "
                f"to {verdict}")
    return event.action


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
        decision = decisions.get(event.decision_id) if event.action.startswith("decision.") else None
        is_original = bool(first and event.action.startswith("decision.") and event.decision_id == first.id)
        entries.append({
            "audit_event_id": event.id,
            "at": event.created_at,
            "actor": event.actor,
            "action": event.action,
            "summary": describe_event(event, decision, is_original=is_original),
            "decision_id": event.decision_id,
            "decision_sequence": decision.sequence if decision else None,
            "verdict": decision.verdict if decision else None,
            "is_original_decision": is_original,
            "fact_id": event.fact_id,
            "reason": event.details.get("officer_reason") or event.details.get("reason"),
            "details": event.details,
        })
    latest = latest_decision(session, application_id)
    return {
        "application_id": application_id,
        "status": application_status(latest) if latest else None,
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
        status = application_status(decision)
        rows.append({
            "application_id": application.id,
            "scheme_id": scheme.id,
            "scheme_name": scheme.name,
            "status": status,
            "decided_by_officer": status == DECIDED_BY_OFFICER,
            "awaiting_officer_decision": status == AWAITING_OFFICER,
            "decision": decision_payload(decision, profile_id=profile_id, scheme=scheme,
                                         audit_event_id=None, possible=[]),
        })
    return rows
