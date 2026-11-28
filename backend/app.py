"""FastAPI application factory.

One process serves both the API and the website:

* the API under ``/api/...`` (also at the original unprefixed paths, hidden from the
  schema, so the Streamlit fallback and existing clients keep working);
* the built website (``web/dist``) at ``/``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend import officer, service
from backend.api_models import ApplicationRequest, DecisionOut, EvidenceResolutionRequest, OfficerDecisionRequest
from backend.config import Settings
from backend.db import init_db, make_engine, make_session_factory, schema_problems
from backend.demo_assets import demo_documents
from backend.explanations import Explainer
from backend.extraction import DOCUMENT_SCHEMAS, Extractor, image_media_type
from backend.image_quality import MIN_CONTRAST, MIN_SHARPNESS
from backend.models import Application, AuditEvent, Document, Profile
from backend.schemes import load_schemes
from backend.web_safety import (
    ALLOWED_UPLOAD_TYPES,
    MAX_REQUEST_BYTES,
    MAX_UPLOAD_BYTES,
    MAX_UPLOAD_MB,
    PIN_HEADER,
    RateLimiter,
    UploadRejected,
    check_upload,
    pin_matches,
)


class UTF8JSONResponse(JSONResponse):
    """Declare the charset so clients such as Windows PowerShell decode "₹" correctly."""

    media_type = "application/json; charset=utf-8"


def _document_status(facts: list[dict[str, Any]], extraction_ok: bool) -> str:
    if not extraction_ok:
        return "extraction_failed"
    return "flagged" if any(f["verification_status"] == "flagged" for f in facts) else "verified"


log = logging.getLogger("verifyonce.api")

DOCUMENT_LABELS = {"identity_card": "Identity card", "income_certificate": "Income certificate",
                   "marksheet": "Marksheet"}
RATE_LIMIT_MESSAGES = {
    "upload": "Too many uploads from your network. Wait {wait} seconds and try again.",
    "apply": "Too many applications from your network. Wait {wait} seconds and try again.",
    "pin": "Too many wrong PIN attempts. Wait {wait} seconds and try again.",
}
# The site loads only its own scripts and styles; nothing from other hosts.
WEBSITE_CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
               "script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; "
               "form-action 'self'; frame-ancestors 'none'")


def client_ip(request: Request) -> str:
    """The caller's address as the server sees it. Forwarded headers are not trusted."""
    return request.client.host if request.client else "unknown"


def create_app(settings: Settings | None = None, *, extractor: Extractor | None = None,
               explainer: Explainer | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    engine = make_engine(settings.database_url)
    schemes = load_schemes(settings.schemes_dir)
    extractor = extractor or Extractor.from_settings(settings)
    explainer = explainer or Explainer.from_settings(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        init_db(engine)
        yield
        engine.dispose()

    app = FastAPI(
        title="VerifyOnce",
        version="0.2.0",
        description="Verify a citizen once, then check any scheme against deterministic rules.",
        lifespan=lifespan,
        default_response_class=UTF8JSONResponse,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.schemes = schemes
    app.state.extractor = extractor
    app.state.explainer = explainer
    app.state.limits = {
        "upload": RateLimiter(settings.uploads_per_minute),
        "apply": RateLimiter(settings.applications_per_minute),
        "pin": RateLimiter(settings.pin_attempts_per_minute),
    }

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> UTF8JSONResponse:
        """Never send a stack trace to a client: log it here, return a plain message."""
        log.exception("Unexpected error on %s %s", request.method, request.url.path)
        return UTF8JSONResponse(status_code=500, content={
            "detail": "Something went wrong on the server. Details are in the API window's log."})

    @app.middleware("http")
    async def guard(request: Request, call_next):
        """Refuse oversized bodies before reading them, and add basic security headers."""
        length = request.headers.get("content-length", "")
        if request.method == "POST" and length.isdigit() and int(length) > MAX_REQUEST_BYTES:
            return UTF8JSONResponse(status_code=413, content={
                "detail": f"The file is larger than {MAX_UPLOAD_MB} MB. "
                          f"Upload a PNG or JPG image under {MAX_UPLOAD_MB} MB."})
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    if settings.cors_origins:
        # Off by default: the website is served from the API's own origin and needs no CORS.
        app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins),
                           allow_methods=["GET", "POST"], allow_headers=["Content-Type", PIN_HEADER])

    def get_session(request: Request) -> Iterator[Session]:
        with request.app.state.session_factory() as session:
            yield session

    # --- Safety dependencies -------------------------------------------------------------

    def _too_many(bucket: str, wait: int) -> HTTPException:
        return HTTPException(429, RATE_LIMIT_MESSAGES[bucket].format(wait=wait), headers={"Retry-After": str(wait)})

    def rate_limited(bucket: str):
        def dependency(request: Request) -> None:
            wait = request.app.state.limits[bucket].hit(client_ip(request))
            if wait is not None:
                raise _too_many(bucket, wait)
        return Depends(dependency)

    def pin_given_and_correct(request: Request) -> bool | None:
        """None if no PIN was sent, else whether it matches. Wrong guesses are rate-limited."""
        given = request.headers.get(PIN_HEADER)
        if not given:
            return None
        limiter: RateLimiter = request.app.state.limits["pin"]
        ip = client_ip(request)
        wait = limiter.blocked(ip)
        if wait is not None:
            raise _too_many("pin", wait)
        if pin_matches(settings.demo_pin, given):
            return True
        limiter.hit(ip)
        return False

    def officer_actions_allowed(request: Request) -> None:
        """On the public demo, officer actions need the demo PIN (and are off if none is set)."""
        if not settings.public_demo:
            return
        if not settings.demo_pin:
            raise HTTPException(403, "Officer actions are turned off on this public demo.")
        ok = pin_given_and_correct(request)
        if ok is None:
            raise HTTPException(403, "Officer actions on the public demo need the demo PIN.")
        if not ok:
            raise HTTPException(403, "That demo PIN is not correct.")

    api = APIRouter()

    # --- Status --------------------------------------------------------------------------

    @api.get("/health")
    def health(session: Session = Depends(get_session)) -> dict[str, Any]:
        """Liveness plus a database check, for preflight checks and the UI."""
        problems = schema_problems(engine)
        if problems:
            return {"status": "degraded", "database": "outdated schema: run scripts\\demo_reset.py",
                    "problems": problems, "schemes": list(schemes)}
        profiles = len(session.scalars(select(Profile.id)).all())
        return {"status": "ok", "database": "ok", "profiles": profiles, "schemes": list(schemes)}

    @api.get("/status")
    def status() -> dict[str, Any]:
        """What the status strip shows. Says whether a key or PIN is set, never the value."""
        ex = app.state.extractor
        return {
            "status": "ok",
            "provider": ex.provider.name,
            "model": ex.provider.model,
            "provider_key_set": ex.provider.missing_configuration() is None,
            "extraction_mode": "cache first" if ex.use_cache else "live",
            "confidence_cutoff": settings.confidence_cutoff,
            "image_quality_thresholds": {"min_sharpness": MIN_SHARPNESS, "min_contrast": MIN_CONTRAST},
            "explanations_enabled": app.state.explainer.enabled,
            "schemes": list(schemes),
            "public_demo": settings.public_demo,
            "officer_pin_required": settings.public_demo,
            "officer_actions_enabled": not settings.public_demo or bool(settings.demo_pin),
            "own_uploads_need_pin": settings.public_demo,
            "upload_limits": {"max_bytes": MAX_UPLOAD_BYTES, "max_mb": MAX_UPLOAD_MB,
                              "types": list(ALLOWED_UPLOAD_TYPES)},
            "rate_limits": {"uploads_per_minute": settings.uploads_per_minute,
                            "applications_per_minute": settings.applications_per_minute},
        }

    @api.post("/demo-pin")
    def check_demo_pin(request: Request) -> dict[str, Any]:
        """Check the demo PIN sent in the X-Demo-Pin header. Wrong guesses are rate-limited."""
        if not settings.demo_pin:
            raise HTTPException(403, "No demo PIN is set on this server.")
        ok = pin_given_and_correct(request)
        if not ok:
            raise HTTPException(403, "That demo PIN is not correct." if ok is False else "Enter the demo PIN.")
        return {"ok": True}

    # --- Sample documents ----------------------------------------------------------------

    def _demo_files() -> dict[str, tuple[Path, str]]:
        return {f"{path.parent.name}/{path.name}": (path, doc_type) for path, doc_type in demo_documents()}

    @api.get("/demo-documents")
    def list_demo_documents() -> list[dict[str, Any]]:
        """The synthetic sample documents, and whether a cached reading of each exists."""
        rows = []
        for key, (path, doc_type) in _demo_files().items():
            rows.append({
                "id": key,
                "case": path.parent.name,
                "filename": path.name,
                "document_type": doc_type,
                "label": f"{DOCUMENT_LABELS.get(doc_type, doc_type)} · {path.parent.name}",
                "cached": app.state.extractor.is_cached(path.read_bytes(), doc_type),
                "url": f"/api/demo-documents/{key}",
            })
        return rows

    @api.get("/demo-documents/{case}/{filename}")
    def demo_document_file(case: str, filename: str) -> FileResponse:
        found = _demo_files().get(f"{case}/{filename}")
        if found is None:
            raise HTTPException(404, "No such sample document")
        path = found[0]
        return FileResponse(path, media_type="image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png")

    # --- Citizen -------------------------------------------------------------------------

    @api.get("/profiles")
    def list_profiles(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
        return officer.list_profiles(session)

    @api.get("/profiles/{profile_id}/applications")
    def profile_applications(profile_id: str, session: Session = Depends(get_session)) -> list[dict[str, Any]]:
        if session.get(Profile, profile_id) is None:
            raise HTTPException(404, f"Profile {profile_id!r} not found")
        return officer.profile_applications(session, schemes, profile_id)

    @api.get("/schemes")
    def list_schemes() -> list[dict[str, Any]]:
        return [scheme.model_dump() for scheme in schemes.values()]

    @api.get("/profiles/{profile_id}")
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

    @api.post("/profiles/{profile_id}/documents", status_code=201, dependencies=[rate_limited("upload")])
    def upload_document(
        request: Request,
        profile_id: str,
        document_type: str = Form(...),
        file: UploadFile = File(...),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        """Upload one PNG or JPG document image (max 3 MB). Its fields are extracted, validated and cross-checked.

        Creates the profile if it does not exist yet. On the public demo, only the sample
        documents (whose readings are cached) are accepted unless the demo PIN is sent.
        """
        if document_type not in DOCUMENT_SCHEMAS:
            raise HTTPException(422, f"document_type must be one of {sorted(DOCUMENT_SCHEMAS)}")
        content = file.file.read(MAX_UPLOAD_BYTES + 1)
        try:
            check_upload(content)
        except UploadRejected as exc:
            raise HTTPException(exc.status_code, str(exc)) from None
        try:
            image_media_type(content)
        except ValueError as exc:
            raise HTTPException(422, f"{exc}. Upload a clear PNG or JPG image of the document.") from None
        if settings.public_demo and not app.state.extractor.is_cached(content, document_type):
            ok = pin_given_and_correct(request)
            if ok is False:
                raise HTTPException(403, "That demo PIN is not correct.")
            if ok is None:
                raise HTTPException(403, (
                    "This public demo accepts the sample documents only, so no model runs for unknown files. "
                    "Pick a sample document, or enter the demo PIN to upload your own synthetic image."))

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

    @api.post("/profiles/{profile_id}/applications", status_code=201, response_model=DecisionOut,
              dependencies=[rate_limited("apply")])
    def submit_application(
        profile_id: str, body: ApplicationRequest, session: Session = Depends(get_session)
    ) -> dict[str, Any]:
        cutoff = settings.confidence_cutoff
        try:
            decision, audit = service.submit_application(
                session, schemes, profile_id, body.scheme_id, actor=f"citizen:{profile_id}",
                confidence_cutoff=cutoff, explainer=app.state.explainer,
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

    @api.get("/applications/{application_id}")
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

    @api.get("/applications/{application_id}/audit")
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

    @api.get("/officer/queue")
    def officer_queue(verdict: list[str] | None = Query(default=None),
                      session: Session = Depends(get_session)) -> list[dict[str, Any]]:
        """Applications with their latest decision, awaiting-officer cases first. Filter with ?verdict=..."""
        return officer.officer_queue(session, schemes, verdict, cutoff=settings.confidence_cutoff)

    @api.get("/officer/cases/{application_id}")
    def case_detail(application_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
        return _officer_errors(lambda: officer.case_detail(
            session, schemes, application_id, cutoff=settings.confidence_cutoff))

    @api.post("/officer/cases/{application_id}/evidence", status_code=201,
              dependencies=[Depends(officer_actions_allowed)])
    def resolve_evidence(application_id: int, body: EvidenceResolutionRequest,
                         session: Session = Depends(get_session)) -> dict[str, Any]:
        """Confirm or reject one fact (typed reason required).

        Returns the re-evaluated *suggested* verdict. No decision is recorded: the case
        still needs an explicit officer decision.
        """
        result = _officer_errors(lambda: officer.resolve_evidence(
            session, schemes, application_id, fact_id=body.fact_id, resolution=body.resolution,
            reason=body.reason, officer=body.officer, cutoff=settings.confidence_cutoff,
            explainer=app.state.explainer))
        session.commit()
        return result

    @api.post("/officer/cases/{application_id}/decision", status_code=201, response_model=DecisionOut,
              dependencies=[Depends(officer_actions_allowed)])
    def officer_decision(application_id: int, body: OfficerDecisionRequest,
                         session: Session = Depends(get_session)) -> dict[str, Any]:
        """approve / reject / override. Approval is refused (409) while flagged evidence is unresolved."""
        decision, audit = _officer_errors(lambda: officer.officer_decide(
            session, schemes, application_id, action=body.action, verdict=body.verdict,
            reason=body.reason, officer=body.officer, cutoff=settings.confidence_cutoff,
            explainer=app.state.explainer))
        session.commit()
        return _decision_response(session, decision, audit)

    app.include_router(api, prefix="/api")
    # The original paths, used by the Streamlit fallback and earlier scripts. Same handlers,
    # same limits and PIN checks.
    app.include_router(api, include_in_schema=False)

    _mount_website(app, settings.web_dist_dir)
    return app


def _mount_website(app: FastAPI, dist: Path) -> None:
    """Serve the built single-page site from ``dist``; unknown paths get index.html."""
    dist = Path(dist)
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
    root = dist.resolve()

    @app.get("/", include_in_schema=False)
    @app.get("/{path:path}", include_in_schema=False)
    def website(path: str = ""):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "Not found")
        if path:
            candidate = (dist / path).resolve()
            if candidate.is_file() and root in candidate.parents:
                return FileResponse(candidate)
        index = dist / "index.html"
        if not index.is_file():
            return UTF8JSONResponse(status_code=404, content={
                "detail": "The website is not built yet. Run: cd web; npm install; npm run build"})
        return FileResponse(index, media_type="text/html; charset=utf-8",
                            headers={"Cache-Control": "no-cache", "Content-Security-Policy": WEBSITE_CSP})
