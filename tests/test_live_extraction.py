"""Opt-in tests against the real extraction provider (EXTRACTION_PROVIDER, default gemini).

    python -m pytest -m live --run-live -s

Skipped by default, and when the selected provider's API key is not set. A quota or
rate limit (HTTP 429) skips the test with the provider's message instead of failing.
"""

from __future__ import annotations

import dataclasses

import pytest

from backend.config import Settings
from backend.extraction import Extractor
from backend.verification import validate
from conftest import manifest_doc

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def live() -> tuple[Extractor, float]:
    settings = Settings.from_env()
    if not settings.provider_key_set:
        pytest.skip(f"{settings.provider_key_name} is not set (provider: {settings.extraction_provider})")
    return Extractor.from_settings(dataclasses.replace(settings, extraction_use_cache=False)), settings.confidence_cutoff


def run(extractor: Extractor, case: str, label: str):
    path, doc = manifest_doc(case, "marksheet")
    result = extractor.extract(path.read_bytes(), "marksheet")
    readings = ", ".join(f"{f}={r['value']!r}@{r['confidence']:.2f}" for f, r in result.readings.items())
    print(f"\n{label}: {result.provider} {result.model} {result.extraction_source} {result.status} {readings}")
    if not result.ok and "quota or rate limit" in (result.error or ""):
        pytest.skip(result.error)
    return result, doc


def same(field: str, read, expected) -> bool:
    value, _checks = validate(field, read)
    return value is not None and str(value) == str(validate(field, expected)[0])


def test_clean_marksheet_is_read_exactly(live):
    extractor, cutoff = live
    result, doc = run(extractor, "seed-below-marks", "clean")
    assert result.ok and result.extraction_source == "live", result.error
    for field, expected in doc["expected"].items():
        assert same(field, result.readings[field]["value"], expected), field
        assert result.readings[field]["confidence"] >= cutoff, field


def test_blurry_marksheet_never_yields_a_verified_wrong_value(live):
    """A wrong reading is acceptable only if it would be flagged (null or below the cutoff)."""
    extractor, cutoff = live
    result, doc = run(extractor, "seed-low-confidence", "blurry")
    assert result.extraction_source == "live"
    if not result.ok:
        return  # a failed or blocked extraction is flagged and goes to an officer
    for field, expected in doc["expected"].items():
        reading = result.readings[field]
        correct = same(field, reading["value"], expected)
        flagged = validate(field, reading["value"])[0] is None or reading["confidence"] < cutoff
        assert correct or flagged, f"{field}: read {reading} but expected {expected}, and it would be verified"


def test_injection_marksheet_reports_what_is_printed(live):
    extractor, _cutoff = live
    result, doc = run(extractor, "prompt-injection", "injection")
    assert result.ok, result.error
    assert set(result.readings) == set(doc["expected"])
    assert result.readings["marks_percent"]["value"] == pytest.approx(52.0)
