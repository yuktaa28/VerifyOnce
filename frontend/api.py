"""Thin HTTP client for the VerifyOnce API.

The frontend holds no business logic: verdicts, flags, blocking rules and audit all
come from these calls. API_BASE_URL comes from the environment or from .env (only that
one value is read), defaulting to http://127.0.0.1:8000.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
_client: httpx.Client | None = None


class ApiError(Exception):
    def __init__(self, status: int | None, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def base_url() -> str:
    if os.environ.get("API_BASE_URL"):
        return os.environ["API_BASE_URL"]
    configured = dotenv_values(_ENV_FILE).get("API_BASE_URL") if _ENV_FILE.is_file() else None
    return configured or DEFAULT_BASE_URL


def use_client(client: httpx.Client | None) -> None:
    """Swap the HTTP client (tests pass a FastAPI TestClient, which is an httpx.Client)."""
    global _client
    _client = client


def _http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(base_url=base_url(), timeout=180.0)
    return _client


def _request(method: str, path: str, **kwargs: Any) -> Any:
    try:
        try:
            response = _http().request(method, path, **kwargs)
        except httpx.ConnectError:
            time.sleep(0.5)  # one quick retry: the API may still be starting
            response = _http().request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        raise ApiError(None, f"Cannot reach the API at {base_url()} ({type(exc).__name__})") from None
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = response.text
        if not isinstance(detail, str):
            detail = "; ".join(f"{'.'.join(map(str, d.get('loc', [])))}: {d.get('msg')}" for d in detail) \
                if isinstance(detail, list) else str(detail)
        raise ApiError(response.status_code, detail)
    return response.json()


# --- Shared -------------------------------------------------------------------------------

def status() -> dict:
    return _request("GET", "/status")


def schemes() -> list[dict]:
    return _request("GET", "/schemes")


# --- Citizen ------------------------------------------------------------------------------

def profiles() -> list[dict]:
    return _request("GET", "/profiles")


def profile(profile_id: str) -> dict:
    return _request("GET", f"/profiles/{profile_id}")


def profile_applications(profile_id: str) -> list[dict]:
    return _request("GET", f"/profiles/{profile_id}/applications")


def upload(profile_id: str, document_type: str, filename: str, content: bytes, mime: str) -> dict:
    return _request("POST", f"/profiles/{profile_id}/documents", data={"document_type": document_type},
                    files={"file": (filename, content, mime)})


def apply(profile_id: str, scheme_id: str) -> dict:
    return _request("POST", f"/profiles/{profile_id}/applications", json={"scheme_id": scheme_id})


# --- Officer ------------------------------------------------------------------------------

def queue(verdicts: list[str] | None = None) -> list[dict]:
    return _request("GET", "/officer/queue", params=[("verdict", v) for v in verdicts or []])


def case(application_id: int) -> dict:
    return _request("GET", f"/officer/cases/{application_id}")


def resolve_evidence(application_id: int, fact_id: int, resolution: str, reason: str, officer: str) -> dict:
    return _request("POST", f"/officer/cases/{application_id}/evidence",
                    json={"fact_id": fact_id, "resolution": resolution, "reason": reason, "officer": officer})


def decide(application_id: int, action: str, reason: str, officer: str, verdict: str | None = None) -> dict:
    return _request("POST", f"/officer/cases/{application_id}/decision",
                    json={"action": action, "verdict": verdict, "reason": reason, "officer": officer})


def audit(application_id: int) -> dict:
    return _request("GET", f"/applications/{application_id}/audit")
