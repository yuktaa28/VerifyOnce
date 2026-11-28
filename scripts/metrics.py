"""Measure the five seed cases through the State Student Scholarship and the fee waiver.

    python scripts/metrics.py

Prints only what it measures:
* time-to-verdict per case: wall-clock time of the application request, in-process,
  on a fresh scratch database. The seed documents are already verified, so this
  excludes document extraction (model calls);
* how many verdicts were decided automatically vs sent to human review;
* how many review cases ended with an explicit officer decision, counted in the demo
  database (VERIFY_ONCE_DB), which is only read, never changed.
No estimates and no comparison with manual processing.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import inspect, select  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from backend.app import create_app  # noqa: E402
from backend.config import Settings  # noqa: E402
from backend.db import make_engine  # noqa: E402
from backend.models import Application, Decision  # noqa: E402
from backend.seed import FEE_WAIVER, SCHOLARSHIP, SEED_PROFILES, seed_database  # noqa: E402

SCHEMES = (SCHOLARSHIP, FEE_WAIVER)
REVIEW = "needs_human_review"


def measure_seed_cases(database_url: str) -> list[dict]:
    """Apply every seed profile to both schemes on a fresh database and time each verdict."""
    rows = []
    with TestClient(create_app(Settings(database_url=database_url))) as client:
        with client.app.state.session_factory() as session:
            seed_database(session)
            session.commit()
        for seed in SEED_PROFILES:
            for scheme_id in SCHEMES:
                start = time.perf_counter()
                response = client.post(f"/profiles/{seed.id}/applications", json={"scheme_id": scheme_id})
                elapsed_ms = (time.perf_counter() - start) * 1000
                rows.append({"case": seed.id, "scheme": scheme_id, "verdict": response.json()["verdict"],
                             "ms": elapsed_ms})
    return rows


def officer_outcomes(database_url: str) -> dict | None:
    """Review cases in a database and how many an officer closed. None if there is no database.

    Reads only application ids and each decision's verdict and author, so it also
    works on a database from an older version.
    """
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite" and url.database and not Path(url.database).is_file():
        return None
    engine = make_engine(database_url)
    try:
        if not inspect(engine).has_table("applications"):
            return None
        with Session(engine) as session:
            review_cases = closed = 0
            for application_id in session.scalars(select(Application.id)):
                decisions = session.execute(
                    select(Decision.verdict, Decision.decided_by)
                    .where(Decision.application_id == application_id).order_by(Decision.sequence)).all()
                verdicts = [d.verdict for d in decisions]
                if REVIEW not in verdicts:
                    continue
                review_cases += 1
                first_review = verdicts.index(REVIEW)
                if any(d.decided_by.startswith("officer:") for d in decisions[first_review + 1:]):
                    closed += 1
        return {"database": url.database, "review_cases": review_cases, "closed_by_officer": closed}
    except SQLAlchemyError as exc:
        return {"database": url.database, "error": f"could not be read ({type(exc).__name__})"}
    finally:
        engine.dispose()


def format_report(rows: list[dict], demo: dict | None) -> str:
    lines = ["Time to verdict (fresh scratch database; documents already verified, so no model calls):",
             f"  {'case':<22} {'scheme':<27} {'verdict':<20} {'ms':>8}"]
    lines += [f"  {r['case']:<22} {r['scheme']:<27} {r['verdict']:<20} {r['ms']:>8.1f}" for r in rows]
    times = sorted(r["ms"] for r in rows)
    lines.append(f"  median {times[len(times) // 2]:.1f} ms, slowest {times[-1]:.1f} ms over {len(rows)} verdicts")
    automatic = sum(r["verdict"] != REVIEW for r in rows)
    review = len(rows) - automatic
    lines += ["", f"Decided automatically: {automatic} of {len(rows)} ({automatic / len(rows):.0%})",
              f"Sent to human review: {review} of {len(rows)} ({review / len(rows):.0%})",
              f"  In this scratch run no officer acted, so 0 of {review} review cases are closed yet.", ""]
    if demo is None:
        lines.append("Demo database not found, so officer outcomes were not counted.")
    elif "error" in demo:
        lines.append(f"Demo database {demo['database']} {demo['error']}; run scripts\\demo_reset.py.")
    else:
        lines.append(f"Officer outcomes in the demo database ({demo['database']}):")
        lines.append(f"  review cases that ended with an explicit officer decision: "
                     f"{demo['closed_by_officer']} of {demo['review_cases']}")
        lines.append(f"  still awaiting an officer: {demo['review_cases'] - demo['closed_by_officer']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        rows = measure_seed_cases(f"sqlite:///{(Path(tmp) / 'metrics.db').as_posix()}")
    print(format_report(rows, officer_outcomes(Settings.from_env().database_url)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
