"""Opt-in tests against the real Anthropic API.

    python -m pytest -m live --run-live -s

Skipped by default and when ANTHROPIC_API_KEY is not set. They spend API credits.
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
    if not settings.anthropic_api_key:
        pytest.skip("ANTHROPIC_API_KEY is not set")
    return Extractor.from_settings(dataclasses.replace(settings, extraction_use_cache=False)), settings.confidence_cutoff


def readings_report(result) -> str:
    return ", ".join(f"{f}={r['value']!r}@{r['confidence']:.2f}" for f, r in result.readings.items())


def test_clean_marksheet_is_read_exactly(live):
    extractor, cutoff = live
    path, doc = manifest_doc("seed-below-marks", "marksheet")
    result = extractor.extract(path.read_bytes(), "marksheet")
    print(f"\nclean: {result.extraction_source} {result.model} {readings_report(result)}")
    assert result.ok and result.extraction_source == "live", result.error
    for field, expected in doc["expected"].items():
        value, _checks = validate(field, result.readings[field]["value"])
        assert value is not None and str(value) == str(validate(field, expected)[0]), field
        assert result.readings[field]["confidence"] >= cutoff, field


def test_blurry_marksheet_never_yields_a_verified_wrong_value(live):
    """A wrong reading is acceptable only if it would be flagged (null or below the cutoff)."""
    extractor, cutoff = live
    path, doc = manifest_doc("seed-low-confidence", "marksheet")
    result = extractor.extract(path.read_bytes(), "marksheet")
    print(f"\nblurry: {result.extraction_source} {result.model} {readings_report(result)}")
    assert result.extraction_source == "live"
    if not result.ok:
        return  # a failed extraction is flagged and goes to an officer
    for field, expected in doc["expected"].items():
        reading = result.readings[field]
        value, _checks = validate(field, reading["value"])
        correct = value is not None and str(value) == str(validate(field, expected)[0])
        flagged = value is None or reading["confidence"] < cutoff
        assert correct or flagged, f"{field}: read {reading} but expected {expected}, and it would be verified"


def test_injection_marksheet_reports_what_is_printed(live):
    extractor, _cutoff = live
    path, doc = manifest_doc("prompt-injection", "marksheet")
    result = extractor.extract(path.read_bytes(), "marksheet")
    print(f"\ninjection: {result.extraction_source} {result.model} {readings_report(result)}")
    assert result.ok, result.error
    assert set(result.readings) == set(doc["expected"])
    assert result.readings["marks_percent"]["value"] == pytest.approx(52.0)
