from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from google.genai import types as genai_types
from sqlalchemy.orm import Session

from backend.app import create_app
from backend.config import REPO_ROOT, Settings
from backend.db import init_db, make_engine, make_session_factory
from backend.extraction import Extractor, GeminiProvider
from backend.schemes import Scheme, load_schemes
from backend.seed import seed_database

DOCUMENTS = REPO_ROOT / "data" / "documents"


# --- Live tests are opt-in -------------------------------------------------------------


def pytest_addoption(parser):
    parser.addoption("--run-live", action="store_true", help="run tests that call the real Anthropic API")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-live"):
        return
    skip = pytest.mark.skip(reason="calls the real Anthropic API; run with: python -m pytest -m live --run-live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _no_real_api_outside_live_tests(request, monkeypatch):
    """Non-live tests must never build a real API client, even if .env holds a key."""
    if request.node.get_closest_marker("live"):
        return

    def refuse(*_args, **_kwargs):
        raise AssertionError("a non-live test tried to create a real API client")

    monkeypatch.setattr("backend.extraction.anthropic.Anthropic", refuse)
    monkeypatch.setattr("backend.extraction.genai.Client", refuse)


def _pick_reply(replies: dict[str, Any], default: Any, sha: str, who: str) -> Any:
    reply = replies.get(sha, default)
    if isinstance(reply, list):
        reply = reply.pop(0)
    if isinstance(reply, Exception):
        raise reply
    if reply is None:
        raise AssertionError(f"{who} has no reply for image {sha[:12]}")
    return reply


# --- Fake Gemini client (the default provider) ---------------------------------------------


class FakeGemini:
    """Stands in for google.genai.Client and records every generate_content call.

    ``replies`` maps an image's sha256 to the reply text, or to a list of replies used
    one per attempt. A reply may be an exception to raise (e.g. a 429 ClientError).
    Responses are real google-genai response objects. ``finish_reason`` and
    ``block_reason`` simulate blocked answers.
    """

    def __init__(self, replies=None, default=None, finish_reason="STOP", block_reason=None):
        self.replies: dict[str, Any] = replies or {}
        self.default = default
        self.finish_reason = finish_reason
        self.block_reason = block_reason
        self.calls: list[dict[str, Any]] = []
        self.models = SimpleNamespace(generate_content=self._generate_content)

    def _generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        sha = hashlib.sha256(contents[0].inline_data.data).hexdigest()
        reply = _pick_reply(self.replies, self.default, sha, "FakeGemini")
        if self.block_reason:
            return genai_types.GenerateContentResponse(
                candidates=[],
                prompt_feedback=genai_types.GenerateContentResponsePromptFeedback(
                    block_reason=genai_types.BlockedReason[self.block_reason]
                ),
                model_version=model,
            )
        return genai_types.GenerateContentResponse(
            candidates=[genai_types.Candidate(
                content=genai_types.Content(role="model", parts=[genai_types.Part(text=reply)]),
                finish_reason=genai_types.FinishReason[self.finish_reason],
            )],
            model_version=model,
        )

    @staticmethod
    def system(call) -> str:
        return call["config"].system_instruction

    @staticmethod
    def texts(call) -> list[str]:
        return [c for c in call["contents"] if isinstance(c, str)]


# --- Fake Anthropic client ---------------------------------------------------------------


class FakeAnthropic:
    """Stands in for anthropic.Anthropic and records every messages.create call."""

    def __init__(self, replies=None, default=None, stop_reason="end_turn"):
        self.replies: dict[str, Any] = replies or {}
        self.default = default
        self.stop_reason = stop_reason
        self.calls: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs) -> SimpleNamespace:
        self.calls.append(kwargs)
        image = next(b for b in kwargs["messages"][0]["content"] if b["type"] == "image")
        sha = hashlib.sha256(base64.b64decode(image["source"]["data"])).hexdigest()
        reply = _pick_reply(self.replies, self.default, sha, "FakeAnthropic")
        return SimpleNamespace(
            content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=reply)],
            stop_reason=self.stop_reason,
            model=kwargs["model"],
        )


def load_manifest(case: str) -> dict[str, Any]:
    return json.loads((DOCUMENTS / case / "manifest.json").read_text(encoding="utf-8"))


def manifest_doc(case: str, document_type: str) -> tuple[Path, dict[str, Any]]:
    doc = next(d for d in load_manifest(case)["documents"] if d["document_type"] == document_type)
    return DOCUMENTS / case / doc["file"], doc


def faithful_reply(doc: dict[str, Any], **overrides: Any) -> str:
    """The reply a model that reads the document correctly would give (seed confidences)."""
    readings = {
        field: {"value": value, "confidence": doc["seed_confidence"][field]}
        for field, value in doc["expected"].items()
    }
    readings.update(overrides)
    return json.dumps(readings)


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def upload(client: TestClient, profile_id: str, path: Path, document_type: str):
    media = "image/jpeg" if path.suffix == ".jpg" else "image/png"
    return client.post(
        f"/profiles/{profile_id}/documents",
        data={"document_type": document_type},
        files={"file": (path.name, path.read_bytes(), media)},
    )


# --- App and database fixtures ------------------------------------------------------------


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}", cache_dir=tmp_path / "cache")


@pytest.fixture
def schemes() -> dict[str, Scheme]:
    return load_schemes(REPO_ROOT / "schemes")


@pytest.fixture
def client(settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def seeded_client(client) -> TestClient:
    with client.app.state.session_factory() as session:
        seed_database(session)
        session.commit()
    return client


@pytest.fixture
def db_session(client) -> Iterator[Session]:
    """A session on the same database the API client uses."""
    with client.app.state.session_factory() as session:
        yield session


@pytest.fixture
def bare_session(settings) -> Iterator[Session]:
    """A session on a fresh database, without the API."""
    engine = make_engine(settings.database_url)
    init_db(engine)
    with make_session_factory(engine)() as session:
        yield session
    engine.dispose()


@pytest.fixture
def fake_gemini() -> FakeGemini:
    return FakeGemini()


@pytest.fixture
def upload_client(settings, fake_gemini) -> Iterator[TestClient]:
    """API client whose extractor talks to the default provider, Gemini, mocked."""
    provider = GeminiProvider(api_key="test-key", model="gemini-test", client=fake_gemini)
    extractor = Extractor(provider, cache_dir=settings.cache_dir, sleep=lambda _seconds: None)
    with TestClient(create_app(settings, extractor=extractor)) as test_client:
        yield test_client
