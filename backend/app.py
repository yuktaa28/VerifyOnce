"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend import officer, service
from backend.api_models import ApplicationRequest, DecisionOut, EvidenceResolutionRequest, OfficerDecisionRequest
from backend.config import Settings
from backend.db import init_db, make_engine, make_session_factory
from backend.extraction import DOCUMENT_SCHEMAS, MAX_IMAGE_BYTES, Extractor, image_media_type
from backend.image_quality import MIN_CONTRAST, MIN_SHARPNESS
from backend.models import Application, AuditEvent, Document, Profile
from backend.schemes import load_schemes


class UTF8JSONResponse(JSONResponse):
    """Declare the charset so clients such as Windows PowerShell decode "₹" correctly."""

    media_type = "application/json; charset=utf-8"


def _document_status(facts: list[dict[str, Any]], extraction_ok: bool) -> str:
    if not extraction_ok:
        return "extraction_failed"
    return "flagged" if any(f["verification_status"] == "flagged" for f in facts) else "verified"


def create_app(settings: Settings | None = None, *, extractor: Extractor | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    engine = make_engine(settings.database_url)
    schemes = load_schemes(settings.schemes_dir)
    extractor = extractor or Extractor.from_settings(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        init_db(engine)
        yield
        engine.dispose()

    app = FastAPI(
        title="VerifyOnce",
        version="0.1.0",
        description="Verify a citizen once, then check any scheme against deterministic rules.",
        lifespan=lifespan,
        default_response_class=UTF8JSONResponse,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.schemes = schemes
    app.state.extractor = extractor

    def get_session(request: Request) -> Iterator[Session]:
        with request.app.state.session_factory() as session:
            yield session

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "schemes": list(schemes)}

    @app.get("/status")
    def status() -> dict[str, Any]:
        """What the UI's status strip shows. Says whether a key is set, never the key."""
        ex = app.state.extractor
        return {
            "status": "ok",
            "provider": ex.provider.name,
            "model": ex.provider.model,
            "provider_key_set": ex.provider.missing_configuration() is None,
            "extraction_mode": "cache first" if ex.use_cache else "live",
            "confidence_cutoff": settings.confidence_cutoff,
            "image_quality_thresholds": {"min_sharpness": MIN_SHARPNESS, "min_contrast": MIN_CONTRAST},
            "schemes": list(schemes),
        }

    @app.get("/profiles")
    def list_profiles(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
        return officer.list_profiles(session)

    @app.get("/profiles/{profile_id}/applications")
    def profile_applications(profile_id: str, session: Session = Depends(get_session)) -> list[dict[str, Any]]:
        if session.get(Profile, profile_id) is None:
            raise HTTPException(404, f"Profile {profile_id!r} not found")
        return officer.profile_applications(session, schemes, profile_id)

    @app.get("/schemes")
    def list_schemes() -> list[dict[str, Any]]:
        return [scheme.model_dump() for scheme in schemes.values()]

    @app.get("/profiles/{profile_id}")
    def get_profile(profile_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
        profile = session.scalar(
            select(Profile)
            .where(Profile.id == profile_id)
            .options(selectinload(Profile.documents).selectinload(Document.facts))
        )
        if profile is None:
            raise HTTPException(404, f"Profile {profile_id!r} not found")
        return {
            "profile_id": profile.id,
            "label": profile.label,
            "documents": [
                {
                    "document_id": doc.id,
                    "document_type": doc.document_type,
                    "source": doc.source,
                    "filename": doc.filename,
                    "image_quality": doc.image_quality,
                    "extraction": {
                        "status": doc.extraction_status,
                        "source": doc.extraction_source,
                        "model": doc.extraction_model,
                        "error": doc.extraction_error,
                    },
                    "facts": [service.fact_payload(f) for f in doc.facts],
                }
                for doc in profile.documents
            ],
        }

    @app.post("/profiles/{profile_id}/documents", status_code=201)
    def upload_document(
        profile_id: str,
        document_type: str = Form(...),
        file: UploadFile = File(...),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        """Upload one document image. Its fields are extracted, validated and cross-checked.

        Creates the profile if it does not exist yet.
        """
        if document_type not in DOCUMENT_SCHEMAS:
            raise HTTPException(422, f"document_type must be one of {sorted(DOCUMENT_SCHEMAS)}")
        content = file.file.read(MAX_IMAGE_BYTES + 1)
        try:
            image_media_type(content)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

        document, result, changes = service.ingest_document(
            session, app.state.extractor, profile_id, document_type, content,
            filename=file.filename, actor=f"citizen:{profile_id}", confidence_cutoff=settings.confidence_cutoff,
        )
        session.commit()
        facts = [service.fact_payload(f) for f in document.facts]
        own_fact_ids = {f["fact_id"] for f in facts}
        return {
            "profile_id": profile_id,
            "document_id": document.id,
            "document_type": document_type,
            "filename": document.filename,
            "sha256": document.sha256,
            "status": _document_status(facts, result.ok),
            "image_quality": document.image_quality,
            "extraction": {
                "extraction_source": result.extraction_source,
                "status": result.status,
                "provider": result.provider,
                "model": result.model,
                "attempts": result.attempts,
                "error": result.error,
                "raw_responses": result.raw_responses,
            },
            "facts": facts,
            "other_facts_changed": [c for c in changes if c["fact_id"] not in own_fact_ids],
        }

    @app.post("/profiles/{profile_id}/applications", status_code=201, response_model=DecisionOut)
    def submit_application(
        profile_id: str, body: ApplicationRequest, session: Session = Depends(get_session)
    ) -> dict[str, Any]:
        cutoff = settings.confidence_cutoff
        try:
            decision, audit = service.submit_application(
                session, schemes, profile_id, body.scheme_id, actor=f"citizen:{profile_id}",
                confidence_cutoff=cutoff,
            )
        except service.ProfileNotFound:
            raise HTTPException(404, f"Profile {profile_id!r} not found") from None
        except service.UnknownScheme:
            raise HTTPException(422, f"Unknown scheme_id {body.scheme_id!r}; see GET /schemes") from None
        except service.AlreadyDecided as exc:
            raise HTTPException(409, str(exc)) from None
        session.commit()
        possible = service.possible_schemes(
            session, schemes, profile_id, exclude=body.scheme_id, confidence_cutoff=cutoff
        )
        return service.decision_payload(
            decision, profile_id=profile_id, scheme=schemes[body.scheme_id],
            audit_event_id=audit.id, possible=possible,
        )

    @app.get("/applications/{application_id}")
    def get_application(application_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
        application = session.get(Application, application_id)
        if application is None:
            raise HTTPException(404, f"Application {application_id} not found")
        events = session.scalars(
            select(AuditEvent).where(AuditEvent.application_id == application_id).order_by(AuditEvent.id)
        )
        return {
            "application_id": application.id,
            "profile_id": application.profile_id,
            "scheme_id": application.scheme_id,
            "decisions": [
                {
                    "decision_id": d.id,
                    "sequence": d.sequence,
                    "supersedes_decision_id": d.supersedes_decision_id,
                    "verdict": d.verdict,
                    "reason": d.reason,
                    "ruleset_version": d.ruleset_version,
                    "decided_by": d.decided_by,
                    "created_at": d.created_at,
                }
                for d in application.decisions
            ],
            "audit_events": [
                {
                    "audit_event_id": e.id,
                    "action": e.action,
                    "actor": e.actor,
                    "decision_id": e.decision_id,
                    "details": e.details,
                    "created_at": e.created_at,
                }
                for e in events
            ],
        }

    @app.get("/applications/{application_id}/audit")
    def application_audit(application_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
        try:
            return officer.application_audit(session, application_id)
        except officer.CaseNotFound as exc:
            raise HTTPException(404, str(exc)) from None

    # --- Officer -----------------------------------------------------------------------

    def _officer_errors(call):
        try:
            return call()
        except officer.CaseNotFound as exc:
            raise HTTPException(404, str(exc)) from None
        except officer.ReasonRequired as exc:
            raise HTTPException(422, str(exc)) from None
        except officer.ActionRefused as exc:
            raise HTTPException(409, str(exc)) from None

    def _decision_response(session: Session, decision, audit) -> dict[str, Any]:
        application = session.get(Application, decision.application_id)
        return service.decision_payload(decision, profile_id=application.profile_id,
                                        scheme=schemes[application.scheme_id], audit_event_id=audit.id, possible=[])

    @app.get("/officer/queue")
    def officer_queue(verdict: list[str] | None = Query(default=None),
                      session: Session = Depends(get_session)) -> list[dict[str, Any]]:
        """Applications with their latest decision, needs_human_review first. Filter with ?verdict=..."""
        return officer.officer_queue(session, schemes, verdict)

    @app.get("/officer/cases/{application_id}")
    def case_detail(application_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
        return _officer_errors(lambda: officer.case_detail(
            session, schemes, application_id, cutoff=settings.confidence_cutoff))

    @app.post("/officer/cases/{application_id}/evidence", status_code=201, response_model=DecisionOut)
    def resolve_evidence(application_id: int, body: EvidenceResolutionRequest,
                         session: Session = Depends(get_session)) -> dict[str, Any]:
        """Confirm or reject one fact (typed reason required). Creates a re-evaluated Decision."""
        decision, audit = _officer_errors(lambda: officer.resolve_evidence(
            session, schemes, application_id, fact_id=body.fact_id, resolution=body.resolution,
            reason=body.reason, officer=body.officer, cutoff=settings.confidence_cutoff))
        session.commit()
        return _decision_response(session, decision, audit)

    @app.post("/officer/cases/{application_id}/decision", status_code=201, response_model=DecisionOut)
    def officer_decision(application_id: int, body: OfficerDecisionRequest,
                         session: Session = Depends(get_session)) -> dict[str, Any]:
        """approve / reject / override. Approval is refused (409) while flagged evidence is unresolved."""
        decision, audit = _officer_errors(lambda: officer.officer_decide(
            session, schemes, application_id, action=body.action, verdict=body.verdict,
            reason=body.reason, officer=body.officer, cutoff=settings.confidence_cutoff))
        session.commit()
        return _decision_response(session, decision, audit)

    return app
