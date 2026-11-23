"""Run the real extraction on the clean and blurry marksheets and print what came back.

    python scripts/live_check.py               # live extraction (the default)
    python scripts/live_check.py --injection   # also the prompt-injection marksheet
    python scripts/live_check.py --use-cache   # allow cached results

Each file is uploaded through the real API code path (FastAPI in-process, the real
Extractor, a throwaway database), so the output shows exactly what the app stores:
extraction_source, the raw model replies, each value with its confidence, and the
verification status. The expected column comes from backend/seed.py.

Needs ANTHROPIC_API_KEY (and optionally ANTHROPIC_MODEL) in .env. The key is never
printed.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from backend.app import create_app  # noqa: E402
from backend.config import REPO_ROOT, Settings  # noqa: E402
from backend.seed import SEED_PROFILES  # noqa: E402

DOCS = REPO_ROOT / "data" / "documents"
CHECKS = [
    ("clean marksheet", DOCS / "seed-below-marks" / "marksheet.png", "seed-below-marks"),
    ("blurry marksheet", DOCS / "seed-low-confidence" / "marksheet.jpg", "seed-low-confidence"),
]
INJECTION = ("prompt-injection marksheet", DOCS / "prompt-injection" / "marksheet.png", "seed-below-marks")


def seed_values(case_id: str) -> dict:
    seed = next(s for s in SEED_PROFILES if s.id == case_id)
    sheet = next(d for d in seed.documents if d.document_type == "marksheet")
    return {field: raw for field, (raw, _c) in sheet.facts.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--injection", action="store_true", help="also upload the prompt-injection marksheet")
    parser.add_argument("--use-cache", action="store_true", help="read cached results when present")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    base = Settings.from_env()
    print(f"ANTHROPIC_API_KEY: {'set' if base.anthropic_api_key else 'NOT SET'}")
    print(f"ANTHROPIC_MODEL:   {base.anthropic_model}")
    print(f"Cache:             {'allowed (--use-cache)' if args.use_cache else 'off: live extraction'}")
    print(f"Confidence cutoff: {base.confidence_cutoff:.2f}")

    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        settings = dataclasses.replace(
            base,
            database_url=f"sqlite:///{(Path(tmp) / 'live_check.db').as_posix()}",
            extraction_use_cache=args.use_cache,
        )
        app = create_app(settings)
        with TestClient(app) as client:
            for label, path, case_id in CHECKS + ([INJECTION] if args.injection else []):
                media = "image/jpeg" if path.suffix == ".jpg" else "image/png"
                response = client.post(
                    f"/profiles/live-check-{path.parent.name}/documents",
                    data={"document_type": "marksheet"},
                    files={"file": (path.name, path.read_bytes(), media)},
                )
                print("\n" + "=" * 78)
                print(f"{label}: {path.relative_to(REPO_ROOT).as_posix()}")
                if response.status_code != 201:
                    print(f"Upload failed: HTTP {response.status_code} {response.text}")
                    failures += 1
                    continue
                body = response.json()
                ext = body["extraction"]
                failures += ext["status"] != "ok"
                print(f"extraction_source: {ext['extraction_source']}   status: {ext['status']}   "
                      f"model: {ext['model']}   attempts: {ext['attempts']}")
                if ext["error"]:
                    print(f"error: {ext['error']}")
                for i, raw in enumerate(ext["raw_responses"], 1):
                    print(f"raw response {i}:")
                    try:
                        print(json.dumps(json.loads(raw), indent=2, ensure_ascii=False))
                    except ValueError:
                        print(raw)
                expected = seed_values(case_id)
                print(f"\n{'field':<16}{'value':<16}{'confidence':>11}  {'status':<10}{'expected (seed)':<18}checks failed")
                for fact in body["facts"]:
                    failed = "; ".join(c["message"] for c in fact["checks"] if not c["passed"]) or "-"
                    print(f"{fact['field']:<16}{str(fact['value']):<16}{fact['confidence']:>11.2f}  "
                          f"{fact['verification_status']:<10}{str(expected.get(fact['field'])):<18}{failed}")
                print(f"document status: {body['status']}")
    print("\nPaste everything above back into the chat.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
