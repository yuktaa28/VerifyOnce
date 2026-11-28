"""Demo-day checks: prints a PASS/FAIL list and exits 0 only if everything passes.

    python scripts/preflight.py

Checks that .env exists (True/False only), the extraction key is set (never printed),
the API answers /health, the demo database exists, the demo cache is complete for the
current model, and whether extraction is live or served from the cache.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from sqlalchemy import func, inspect, select  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from backend.config import REPO_ROOT, Settings  # noqa: E402
from backend.db import make_engine, schema_problems  # noqa: E402
from backend.demo_assets import demo_documents  # noqa: E402
from backend.extraction import Extractor  # noqa: E402
from backend.models import Application, Profile  # noqa: E402
from frontend.api import base_url  # noqa: E402


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def check_api(url: str, get: Callable[[str], httpx.Response]) -> Check:
    try:
        response = get(f"{url}/health")
    except httpx.HTTPError as exc:
        return Check("API responds", False, f"{url}/health unreachable ({type(exc).__name__}): start uvicorn")
    if response.status_code != 200:
        return Check("API responds", False, f"{url}/health answered HTTP {response.status_code}")
    body = response.json()
    if "database" not in body:
        return Check("API responds", False, f"{url}/health answered, but the API runs older code: restart uvicorn")
    if body.get("status") != "ok":
        return Check("API responds", False, f"{url}/health: database {body['database']}")
    return Check("API responds", True, f"{url}/health OK ({body.get('profiles')} profiles, database ok)")


def check_database(settings: Settings) -> Check:
    url = make_url(settings.database_url)
    if url.get_backend_name() == "sqlite" and url.database and not Path(url.database).is_file():
        return Check("Database exists", False, f"{url.database} not found: run scripts\\demo_reset.py")
    engine = make_engine(settings.database_url)
    try:
        if not inspect(engine).has_table("profiles"):
            return Check("Database exists", False, f"{url.database} has no tables: run scripts\\demo_reset.py")
        problems = schema_problems(engine)
        if problems:
            return Check("Database exists", False, f"{url.database} is from an older version "
                                                   f"({problems[0]}): run scripts\\demo_reset.py")
        with engine.connect() as conn:
            profiles = conn.scalar(select(func.count()).select_from(Profile))
            applications = conn.scalar(select(func.count()).select_from(Application))
    finally:
        engine.dispose()
    return Check("Database exists", profiles > 0,
                 f"{url.database} ({profiles} profiles, {applications} applications)")


def run_checks(settings: Settings, *, env_file: Path, api_url: str,
               get: Callable[[str], httpx.Response]) -> list[Check]:
    checks = [
        Check(".env file exists", env_file.is_file(), str(env_file.is_file())),
        Check(f"{settings.provider_key_name} is set", settings.provider_key_set,
              "set" if settings.provider_key_set else "NOT SET (uploads would go to officer review)"),
        check_api(api_url, get),
        check_database(settings),
    ]
    extractor = Extractor.from_settings(settings)
    documents = demo_documents()
    cached = sum(extractor.is_cached(path.read_bytes(), doc_type) for path, doc_type in documents)
    complete = bool(documents) and cached == len(documents)
    checks.append(Check("Demo cache", complete,
                        f"{cached} of {len(documents)} demo documents cached for "
                        f"{settings.extraction_provider}/{settings.provider_model}"
                        + ("" if complete else ": run scripts\\prime_cache.py")))
    if settings.extraction_use_cache:
        checks.append(Check("Extraction mode", complete,
                            "cache first (EXTRACTION_USE_CACHE=1): demo documents are served from the cache"
                            + ("" if complete else ", but the cache is incomplete")))
    else:
        checks.append(Check("Extraction mode", settings.provider_key_set,
                            "live (EXTRACTION_USE_CACHE off): uploads call the model"))
    return checks


def report(checks: list[Check]) -> str:
    lines = [f"{'PASS' if c.ok else 'FAIL'}  {c.name}: {c.detail}" for c in checks]
    failed = sum(not c.ok for c in checks)
    lines.append("")
    lines.append("READY for the demo." if not failed else f"NOT READY: {failed} check(s) failed.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    settings = Settings.from_env()
    with httpx.Client(timeout=5.0) as client:
        checks = run_checks(settings, env_file=REPO_ROOT / ".env", api_url=base_url(), get=client.get)
    print(report(checks))
    return 0 if all(c.ok for c in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
