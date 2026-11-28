"""Run live extraction once for each demo document and save the results to data/cache/.

    python scripts/prime_cache.py            # 16 images in data/documents, ~4 s apart
    python scripts/prime_cache.py --pause 8  # slower, for tight free-tier limits

Then, if the API is slow or down on demo day, set EXTRACTION_USE_CACHE=1: uploads of
these exact files are served from the cache and labelled "cache" with the model that
produced them.

Only results returned by a real model call are saved; there is no way to pass values
in. Files already cached for the current provider and model are skipped, so re-run it
after any failures. 503/429 errors get the usual retries (2 s, then 5 s) first.
Keys are read from .env by the code and never printed.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.cache_priming import PrimeRow, prime_cache  # noqa: E402
from backend.config import Settings  # noqa: E402
from backend.demo_assets import DOCUMENTS_DIR, demo_documents  # noqa: E402
from backend.extraction import Extractor  # noqa: E402


def print_row(row: PrimeRow) -> None:
    print(f"{row.file:<44} {row.model:<24} {'yes' if row.cache_written else 'no':<6} {row.result}", flush=True)


def main(argv: list[str] | None = None, settings: Settings | None = None, extractor: Extractor | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pause", type=float, default=4.0, help="seconds between model calls (default 4)")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    settings = settings or Settings.from_env()
    extractor = extractor or Extractor.from_settings(dataclasses.replace(settings, extraction_use_cache=True))
    print(f"Provider: {settings.extraction_provider}   model: {extractor.model}   "
          f"{settings.provider_key_name}: {'set' if settings.provider_key_set else 'NOT SET'}")
    print(f"Cache folder: {extractor.cache_dir}\n")
    print(f"{'document':<44} {'model':<24} {'cache':<6} result")
    documents = demo_documents()
    rows = prime_cache(extractor, documents, root=DOCUMENTS_DIR, pause_seconds=args.pause, progress=print_row)

    cached = sum(r.cache_written or r.result == "cached already" for r in rows)
    failed = [r for r in rows if r.result.startswith("failed") or r.result.startswith("ok, but")]
    print(f"\n{cached} of {len(rows)} documents are cached for {extractor.model}.")
    if failed:
        print("Failed to cache (re-run this script later; cached files are skipped):")
        for row in failed:
            print(f"  {row.file}: {row.result}")
        return 1
    print("No failures.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
