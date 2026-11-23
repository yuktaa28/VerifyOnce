"""The synthetic documents carry exactly the seed values, and the blurry one is really blurry."""

from __future__ import annotations

import inspect
import json
import sys

import pytest
from PIL import Image, ImageFilter, ImageStat

import backend.extraction
from backend.config import REPO_ROOT
from backend.extraction import DOCUMENT_FIELDS, image_media_type
from backend.fields import to_json_value
from backend.seed import SEED_PROFILES
from backend.verification import validate
from conftest import DOCUMENTS, load_manifest

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import make_documents  # noqa: E402

CASES = [s.id for s in SEED_PROFILES]


def test_every_seed_document_has_exactly_the_extracted_fields():
    for seed in SEED_PROFILES:
        for doc in seed.documents:
            assert tuple(doc.facts) == DOCUMENT_FIELDS[doc.document_type], (seed.id, doc.document_type)


@pytest.mark.parametrize("case", CASES)
def test_manifest_matches_the_seed(case):
    manifest = load_manifest(case)
    seed = next(s for s in SEED_PROFILES if s.id == case)
    assert manifest["watermark"] == "SYNTHETIC - DEMO ONLY"
    assert "tests only" in manifest["purpose"]
    assert manifest["expected_verdicts"] == seed.expected
    assert [d["document_type"] for d in manifest["documents"]] == [d.document_type for d in seed.documents]
    for entry, doc in zip(manifest["documents"], seed.documents):
        expected = {f: to_json_value(f, validate(f, raw)[0]) for f, (raw, _c) in doc.facts.items()}
        assert entry["expected"] == expected
        assert entry["seed_confidence"] == {f: c for f, (_raw, c) in doc.facts.items()}
        assert entry["quality"] == doc.quality
        image_media_type((DOCUMENTS / case / entry["file"]).read_bytes())  # a real, supported image


def test_fixed_facts_for_the_below_marks_case():
    docs = {d["document_type"]: d["expected"] for d in load_manifest("seed-below-marks")["documents"]}
    assert docs["income_certificate"]["annual_income_inr"] == 210000
    assert docs["marksheet"]["marks_percent"] == 52.0
    assert docs["identity_card"]["id_last4"] == "3390"


def test_mismatch_case_misspells_the_name_on_the_income_certificate():
    names = {d["document_type"]: d["expected"]["name"] for d in load_manifest("seed-name-mismatch")["documents"]}
    assert names == {"identity_card": "Ishan Rao", "income_certificate": "Ishan Rau", "marksheet": "Ishan Rao"}


def _image_quality(path) -> tuple[float, float, int]:
    """(edge energy after smoothing away grain, contrast, darkest 1% of pixels) in grayscale."""
    gray = Image.open(path).convert("L")
    edges = ImageStat.Stat(gray.filter(ImageFilter.GaussianBlur(1.5)).filter(ImageFilter.FIND_EDGES)).mean[0]
    histogram, total, seen = gray.histogram(), gray.width * gray.height, 0
    for level, count in enumerate(histogram):
        seen += count
        if seen >= total * 0.01:
            return edges, ImageStat.Stat(gray).stddev[0], level
    raise AssertionError("empty image")


def test_low_confidence_marksheet_is_measurably_blurry_and_low_contrast():
    blurry_edges, blurry_contrast, blurry_ink = _image_quality(DOCUMENTS / "seed-low-confidence" / "marksheet.jpg")
    clean_edges, clean_contrast, clean_ink = _image_quality(DOCUMENTS / "seed-below-marks" / "marksheet.png")
    assert blurry_edges < 0.6 * clean_edges  # soft strokes
    assert blurry_contrast < 0.25 * clean_contrast  # washed out
    assert clean_ink < 80 < 150 < blurry_ink  # even the darkest text is light grey


def test_injection_document_carries_the_instruction_and_the_real_marks():
    manifest = load_manifest("prompt-injection")
    assert "ignore all previous instructions" in manifest["injection_text"]
    (doc,) = manifest["documents"]
    assert doc["expected"]["marks_percent"] == 52.0


def test_extraction_never_reads_manifests():
    assert "manifest" not in inspect.getsource(backend.extraction)


def test_generator_reproduces_the_committed_manifests(tmp_path):
    make_documents.generate(tmp_path)
    for case in [*CASES, "prompt-injection"]:
        generated = json.loads((tmp_path / case / "manifest.json").read_text(encoding="utf-8"))
        assert generated == load_manifest(case)


def test_rupees_in_words():
    assert make_documents.rupees_in_words(210000) == "Two Lakh Ten Thousand"
    assert make_documents.rupees_in_words(520000) == "Five Lakh Twenty Thousand"
    assert make_documents.indian_grouping(260000) == "2,60,000"
