"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend import service
from backend.api_models import ApplicationRequest, DecisionOut
from backend.config import Settings
from backend.db import init_db, make_engine, make_session_factory
from backend.models import Application, AuditEvent, Document, Profile
from backend.schemes import load_schemes


class UTF8JSONResponse(JSONResponse):
    """Declare the charset so clients such as Windows PowerShell decode "₹" correctly."""

    media_type = "application/json; charset=utf-8"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    engine = make_engine(settings.database_url)
    schemes = load_schemes(settings.schemes_dir)

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

    def get_session(request: Request) -> Iterator[Session]:
        with request.app.state.session_factory() as session:
            yield session

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "schemes": list(schemes)}

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
                    "facts": [
                        {
                            "fact_id": f.id,
                            "field": f.field,
                            "value": f.value_json,
                            "confidence": f.confidence,
                            "verification_status": f.verification_status,
                        }
                        for f in doc.facts
                    ],
                }
                for doc in profile.documents
            ],
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

    return app
