"""Run the real extraction on sample documents and print what came back.

    python scripts/live_check.py                                # clean and blurry marksheets
    python scripts/live_check.py --injection                    # ...plus the prompt-injection marksheet
    python scripts/live_check.py --profile seed-low-confidence  # all 3 documents of one case, then verdicts
    python scripts/live_check.py --use-cache                    # allow cached results

Each file is uploaded through the real API code path (FastAPI in-process, the real
Extractor, a throwaway database), so the output shows exactly what the app stores: the
image-quality measurements, extraction_source, the raw model replies, each value with
its confidence and verification status, and failed checks. With --profile, the three
documents go into one profile, so the cross-document checks run, and both schemes are
then evaluated. The expected column comes from backend/seed.py.

Uses the provider set by EXTRACTION_PROVIDER (default gemini). Needs that provider's
key in .env: GEMINI_API_KEY (optionally GEMINI_MODEL), or ANTHROPIC_API_KEY. Keys are
never printed. Quota limits and other API errors show up as failed extractions with a
clear error; the script does not crash.
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
MARKSHEETS = [
    ("clean marksheet", DOCS / "seed-below-marks" / "marksheet.png", "seed-below-marks"),
    ("blurry marksheet", DOCS / "seed-low-confidence" / "marksheet.jpg", "seed-low-confidence"),
]
INJECTION = ("prompt-injection marksheet", DOCS / "prompt-injection" / "marksheet.png", "seed-below-marks")


def seed_values(case_id: str, document_type: str) -> dict:
    seed = next(s for s in SEED_PROFILES if s.id == case_id)
    doc = next(d for d in seed.documents if d.document_type == document_type)
    return {field: raw for field, (raw, _c) in doc.facts.items()}


def case_documents(case_id: str) -> list[tuple[str, Path, str]]:
    seed = next(s for s in SEED_PROFILES if s.id == case_id)
    items = []
    for doc in seed.documents:
        (path,) = [p for p in (DOCS / case_id).glob(f"{doc.document_type}.*") if p.suffix in {".png", ".jpg"}]
        items.append((doc.document_type.replace("_", " "), path, doc.document_type))
    return items


def upload_and_print(client: TestClient, profile_id: str, label: str, path: Path, document_type: str,
                     case_id: str) -> bool:
    media = "image/jpeg" if path.suffix == ".jpg" else "image/png"
    response = client.post(
        f"/profiles/{profile_id}/documents",
        data={"document_type": document_type},
        files={"file": (path.name, path.read_bytes(), media)},
    )
    print("\n" + "=" * 78)
    print(f"{label}: {path.relative_to(REPO_ROOT).as_posix()}")
    if response.status_code != 201:
        print(f"Upload failed: HTTP {response.status_code} {response.text}")
        return False
    body = response.json()
    q = body["image_quality"]
    print(f"image quality: sharpness {q['sharpness']:.1f} (min {q['min_sharpness']:g})   "
          f"contrast {q['contrast']:.1f} (min {q['min_contrast']:g})   -> {'ok' if q['ok'] else 'LOW QUALITY'}")
    ext = body["extraction"]
    print(f"extraction_source: {ext['extraction_source']}   status: {ext['status']}   "
          f"provider: {ext['provider']}   model: {ext['model']}   attempts: {ext['attempts']}")
    if ext["error"]:
        print(f"error: {ext['error']}")
    for i, raw in enumerate(ext["raw_responses"], 1):
        print(f"raw response {i}:")
        try:
            print(json.dumps(json.loads(raw), indent=2, ensure_ascii=False))
        except ValueError:
            print(raw)
    expected = seed_values(case_id, document_type)
    print(f"\n{'field':<18}{'value':<16}{'confidence':>11}  {'status':<10}{'expected (seed)':<18}checks failed")
    for fact in body["facts"]:
        failed = "; ".join(c["message"] for c in fact["checks"] if not c["passed"]) or "-"
        print(f"{fact['field']:<18}{str(fact['value']):<16}{fact['confidence']:>11.2f}  "
              f"{fact['verification_status']:<10}{str(expected.get(fact['field'])):<18}{failed}")
    for change in body["other_facts_changed"]:
        print(f"  earlier fact {change['fact_id']} ({change['field']}) {change['from']} -> {change['to']}: "
              f"{'; '.join(change['reasons'])}")
    print(f"document status: {body['status']}")
    return ext["status"] == "ok"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--injection", action="store_true", help="also upload the prompt-injection marksheet")
    parser.add_argument("--profile", metavar="CASE", choices=[s.id for s in SEED_PROFILES],
                        help="upload all three documents of a seed case into one profile, then evaluate")
    parser.add_argument("--use-cache", action="store_true", help="read cached results when present")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    base = Settings.from_env()
    print(f"Provider:          {base.extraction_provider}")
    print(f"{base.provider_key_name + ':':<19}{'set' if base.provider_key_set else 'NOT SET'}")
    print(f"Model:             {base.provider_model}")
    print(f"Cache:             {'allowed (--use-cache)' if args.use_cache else 'off: live extraction'}")
    print(f"Confidence cutoff: {base.confidence_cutoff:.2f}")

    all_ok = True
    with tempfile.TemporaryDirectory() as tmp:
        settings = dataclasses.replace(
            base,
            database_url=f"sqlite:///{(Path(tmp) / 'live_check.db').as_posix()}",
            extraction_use_cache=args.use_cache,
        )
        with TestClient(create_app(settings)) as client:
            if args.profile:
                profile_id = f"live-check-{args.profile}"
                for label, path, document_type in case_documents(args.profile):
                    all_ok &= upload_and_print(client, profile_id, label, path, document_type, args.profile)
                print("\n" + "=" * 78)
                print(f"verdicts for {profile_id}:")
                for scheme_id in client.app.state.schemes:
                    decision = client.post(f"/profiles/{profile_id}/applications", json={"scheme_id": scheme_id}).json()
                    print(f"  {scheme_id:<27} {decision['verdict']}")
                    print(f"    {decision['reason']}")
            else:
                for label, path, case_id in MARKSHEETS + ([INJECTION] if args.injection else []):
                    profile_id = f"live-check-{path.parent.name}"
                    all_ok &= upload_and_print(client, profile_id, label, path, "marksheet", case_id)
    print("\nPaste everything above back into the chat.")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
