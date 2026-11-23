"""Runtime settings from environment variables and the repo's .env file (via python-dotenv).

The Anthropic API key is held here but never printed: it is excluded from repr().
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "data" / "verifyonce.db"
DEFAULT_CACHE_DIR = REPO_ROOT / "data" / "cache"
DEFAULT_CONFIDENCE_CUTOFF = 0.80
DEFAULT_ANTHROPIC_MODEL = "claude-opus-5-5"


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
    anthropic_api_key: str | None = field(default=None, repr=False)
    anthropic_model: str = DEFAULT_ANTHROPIC_MODEL
    extraction_use_cache: bool = False
    cache_dir: Path = DEFAULT_CACHE_DIR

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence_cutoff <= 1.0:
            raise ValueError("confidence_cutoff must be between 0 and 1")

    @classmethod
    def from_env(cls) -> "Settings":
        load_env_file()
        cutoff = os.environ.get("VERIFY_ONCE_CONFIDENCE_CUTOFF") or DEFAULT_CONFIDENCE_CUTOFF
        return cls(
            database_url=database_url(os.environ.get("VERIFY_ONCE_DB")),
            confidence_cutoff=float(cutoff),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
            anthropic_model=os.environ.get("ANTHROPIC_MODEL") or DEFAULT_ANTHROPIC_MODEL,
            extraction_use_cache=_flag(os.environ.get("EXTRACTION_USE_CACHE")),
        )
