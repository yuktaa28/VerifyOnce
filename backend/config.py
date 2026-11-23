"""Runtime settings from environment variables, plus an optional .env file at the repo root."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "data" / "verifyonce.db"
DEFAULT_CONFIDENCE_CUTOFF = 0.80


def load_env_file(path: Path = REPO_ROOT / ".env") -> None:
    """Read KEY=VALUE lines from .env. Variables already set in the environment win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip("\"'")


def database_url(raw: str | None) -> str:
    """VERIFY_ONCE_DB may be a SQLAlchemy URL or a file path (relative to the repo root)."""
    if raw and "://" in raw:
        return raw
    path = Path(raw) if raw else DEFAULT_DB_PATH
    if not path.is_absolute():
        path = REPO_ROOT / path
    return f"sqlite:///{path.as_posix()}"


@dataclass(frozen=True)
class Settings:
    database_url: str
    schemes_dir: Path = REPO_ROOT / "schemes"
    confidence_cutoff: float = DEFAULT_CONFIDENCE_CUTOFF

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
        )
