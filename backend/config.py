"""Runtime settings from environment variables and the repo's .env file (via python-dotenv).

API keys are held here but never printed: they are excluded from repr().
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "data" / "verifyonce.db"
DEFAULT_CACHE_DIR = REPO_ROOT / "data" / "cache"
# Models report coarse confidences (0.8, 0.9, 1.0), so 0.8 must not pass: the cutoff is 0.85.
DEFAULT_CONFIDENCE_CUTOFF = 0.85
DEFAULT_EXTRACTION_PROVIDER = "gemini"
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
DEFAULT_ANTHROPIC_MODEL = "claude-opus-5-5"
EXTRACTION_PROVIDERS = ("gemini", "anthropic")


def load_env_file(path: Path = REPO_ROOT / ".env") -> None:
    """Load .env into the environment. Variables already set in the shell win."""
    load_dotenv(path, override=False)


def database_url(raw: str | None) -> str:
    """VERIFY_ONCE_DB may be a SQLAlchemy URL or a file path (relative to the repo root)."""
    if raw and "://" in raw:
        return raw
    path = Path(raw) if raw else DEFAULT_DB_PATH
    if not path.is_absolute():
        path = REPO_ROOT / path
    return f"sqlite:///{path.as_posix()}"


def _flag(raw: str | None) -> bool:
    return (raw or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str
    schemes_dir: Path = REPO_ROOT / "schemes"
    confidence_cutoff: float = DEFAULT_CONFIDENCE_CUTOFF
    extraction_provider: str = DEFAULT_EXTRACTION_PROVIDER
    gemini_api_key: str | None = field(default=None, repr=False)
    gemini_model: str = DEFAULT_GEMINI_MODEL
    anthropic_api_key: str | None = field(default=None, repr=False)
    anthropic_model: str = DEFAULT_ANTHROPIC_MODEL
    extraction_use_cache: bool = False
    cache_dir: Path = DEFAULT_CACHE_DIR
    explanations_enabled: bool = False

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence_cutoff <= 1.0:
            raise ValueError("confidence_cutoff must be between 0 and 1")
        if self.extraction_provider not in EXTRACTION_PROVIDERS:
            raise ValueError(f"EXTRACTION_PROVIDER must be one of {EXTRACTION_PROVIDERS}")

    @property
    def provider_key_set(self) -> bool:
        """Whether the selected provider has an API key (never the key itself)."""
        key = self.gemini_api_key if self.extraction_provider == "gemini" else self.anthropic_api_key
        return bool(key)

    @property
    def provider_key_name(self) -> str:
        return "GEMINI_API_KEY" if self.extraction_provider == "gemini" else "ANTHROPIC_API_KEY"

    @property
    def provider_model(self) -> str:
        return self.gemini_model if self.extraction_provider == "gemini" else self.anthropic_model

    @classmethod
    def from_env(cls) -> "Settings":
        load_env_file()
        cutoff = os.environ.get("VERIFY_ONCE_CONFIDENCE_CUTOFF") or DEFAULT_CONFIDENCE_CUTOFF
        return cls(
            database_url=database_url(os.environ.get("VERIFY_ONCE_DB")),
            confidence_cutoff=float(cutoff),
            extraction_provider=(os.environ.get("EXTRACTION_PROVIDER") or DEFAULT_EXTRACTION_PROVIDER).strip().lower(),
            gemini_api_key=os.environ.get("GEMINI_API_KEY") or None,
            gemini_model=os.environ.get("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL,
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
            anthropic_model=os.environ.get("ANTHROPIC_MODEL") or DEFAULT_ANTHROPIC_MODEL,
            extraction_use_cache=_flag(os.environ.get("EXTRACTION_USE_CACHE")),
            explanations_enabled=_flag(os.environ.get("EXPLANATIONS_ENABLED")),
        )
