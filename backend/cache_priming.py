"""Fill data/cache/ from real extraction runs, for demo-day fallback (EXTRACTION_USE_CACHE=1).

Only results returned by a real model call are saved (Extractor.save_to_cache checks
that the result is live, made at least one call, and that its values are exactly what
the model replied). There is no way to pass values in. Files already cached for the
current provider and model are skipped, so it is safe to re-run after failures.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from backend.extraction import Extractor


@dataclass
class PrimeRow:
    file: str
    document_type: str
    model: str
    result: str  # "cached already", "ok", or "failed: <reason>"
    cache_written: bool


def prime_cache(
    extractor: Extractor,
    documents: list[tuple[Path, str]],
    *,
    root: Path,
    pause_seconds: float = 4.0,
    sleep: Callable[[float], None] = time.sleep,
    progress: Callable[[PrimeRow], None] | None = None,
) -> list[PrimeRow]:
    rows: list[PrimeRow] = []
    made_a_call = False
    for path, document_type in documents:
        content = path.read_bytes()
        name = path.relative_to(root).as_posix() if path.is_relative_to(root) else path.name
        if extractor.is_cached(content, document_type):
            row = PrimeRow(name, document_type, extractor.model, "cached already", False)
        else:
            if made_a_call and pause_seconds:
                sleep(pause_seconds)  # stay under free-tier requests-per-minute limits
            made_a_call = True
            result = extractor.extract_live_only(content, document_type)
            written = extractor.save_to_cache(result) if result.ok else False
            outcome = "ok" if result.ok else f"failed: {result.error}"
            if result.ok and not written:
                outcome = "ok, but refused by the cache (values did not match the model reply)"
            row = PrimeRow(name, document_type, result.model or extractor.model, outcome, written)
        rows.append(row)
        if progress:
            progress(row)
    return rows
