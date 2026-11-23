"""Submit every seed profile to every scheme through the API and print the verdicts.

Runs the FastAPI app in-process against the configured database, so no server is
needed. Each run adds new decisions (resubmissions); nothing is overwritten.

    python scripts/run_seed_cases.py
    python scripts/run_seed_cases.py --json seed-below-marks state_student_scholarship
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from backend.app import create_app  # noqa: E402
from backend.seed import SEED_PROFILES, seed_database  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", nargs=2, metavar=("PROFILE_ID", "SCHEME_ID"), help="print one full response")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    app = create_app()
    with TestClient(app) as client:
        with app.state.session_factory() as session:
            seed_database(session)
            session.commit()

        if args.json:
            profile_id, scheme_id = args.json
            response = client.post(f"/profiles/{profile_id}/applications", json={"scheme_id": scheme_id})
            print(json.dumps(response.json(), indent=2, ensure_ascii=False))
            return 0 if response.status_code == 201 else 1

        failures = 0
        print(f"{'profile':<22} {'scheme':<27} {'expected':<19} {'actual':<19}")
        for seed in SEED_PROFILES:
            for scheme_id, expected in seed.expected.items():
                body = client.post(f"/profiles/{seed.id}/applications", json={"scheme_id": scheme_id}).json()
                mark = "" if body["verdict"] == expected else "  <-- MISMATCH"
                failures += bool(mark)
                print(f"{seed.id:<22} {scheme_id:<27} {expected:<19} {body['verdict']:<19}{mark}")
                print(f"    {body['reason']}")
        return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
