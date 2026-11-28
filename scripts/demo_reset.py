"""Reset the demo database to a known state.

    python scripts/demo_reset.py            # five seed profiles + two cases awaiting an officer
    python scripts/demo_reset.py --clean    # five seed profiles, no applications

Default (--with-applications): pre-creates the State Student Scholarship application
for seed-name-mismatch and seed-low-confidence, so the officer queue shows two cases
awaiting review at startup. seed-below-marks is left without applications for the live
"rejected for the scholarship, then approved for the fee waiver" moment.

Safe to run repeatedly: every run gives the same result. It drops and recreates the
tables instead of deleting the file, so it works even while the API is running. Uses
VERIFY_ONCE_DB (default data/verifyonce.db).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.engine import make_url  # noqa: E402

from backend.config import Settings  # noqa: E402
from backend.db import make_engine  # noqa: E402
from backend.demo import FIXED_PROFILE, reset_demo  # noqa: E402
from backend.explanations import Explainer  # noqa: E402
from backend.schemes import load_schemes  # noqa: E402

NEXT_STEPS = r"""
Next, in the VerifyOnce folder, start the website and API (http://127.0.0.1:8000):
  .venv\Scripts\python.exe scripts\serve.py
Then check everything before the demo (second window):
  .venv\Scripts\python.exe scripts\preflight.py
Streamlit fallback instead: run the API, then the UI (two windows):
  .venv\Scripts\python.exe -m uvicorn backend.main:app --port 8000
  .venv\Scripts\python.exe -m streamlit run frontend\app.py --server.port 8501
"""


def main(argv: list[str] | None = None, settings: Settings | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--with-applications", action="store_true", default=True,
                      help="pre-create the two review cases (default)")
    mode.add_argument("--clean", action="store_true", help="no applications at all")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    settings = settings or Settings.from_env()
    engine = make_engine(settings.database_url)
    try:
        summary = reset_demo(engine, load_schemes(settings.schemes_dir), with_applications=not args.clean,
                             confidence_cutoff=settings.confidence_cutoff,
                             explainer=Explainer.from_settings(settings))
    finally:
        engine.dispose()

    print(f"Demo database reset: {make_url(settings.database_url).database}")
    print(f"  mode: {'clean (no applications)' if args.clean else 'with applications'}")
    print(f"  profiles: {', '.join(summary['profiles'])}")
    print(f"  documents: {summary['documents']}, facts: {summary['facts']}, decisions: {summary['decisions']}")
    if summary["applications"]:
        print("  applications:")
        for a in summary["applications"]:
            print(f"    case {a['application_id']}: {a['profile_id']} / {a['scheme_id']} -> {a['verdict']} "
                  f"({a['status']})")
    else:
        print("  applications: none")
    print(f"  {FIXED_PROFILE}: no applications (for the live reject-then-fee-waiver moment)")
    print(NEXT_STEPS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
