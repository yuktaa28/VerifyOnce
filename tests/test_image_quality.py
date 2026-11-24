"""Deterministic image-quality check: thresholds hold on all 16 synthetic documents."""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageEnhance, ImageFilter

from backend.image_quality import LOW_QUALITY_REASON, MIN_CONTRAST, MIN_SHARPNESS, assess, quality_check
from conftest import DOCUMENTS, load_manifest

ALL_DOCUMENTS = [
    (case, doc["file"], doc["quality"])
    for case in ["seed-approve", "seed-reject-income", "seed-below-marks", "seed-name-mismatch",
                 "seed-low-confidence", "prompt-injection"]
    for doc in load_manifest(case)["documents"]
]


def test_there_are_sixteen_documents():
    assert len(ALL_DOCUMENTS) == 16
    assert [d for d in ALL_DOCUMENTS if d[2] == "blurry"] == [("seed-low-confidence", "marksheet.jpg", "blurry")]


@pytest.mark.parametrize(("case", "filename", "quality"), ALL_DOCUMENTS)
def test_clean_documents_pass_and_the_blurry_one_fails(case, filename, quality):
    result = assess((DOCUMENTS / case / filename).read_bytes())
    assert result.ok is (quality == "clean"), result.describe()


@pytest.mark.parametrize(("case", "filename", "quality"), ALL_DOCUMENTS)
def test_every_document_clears_the_thresholds_by_a_wide_margin(case, filename, quality):
    result = assess((DOCUMENTS / case / filename).read_bytes())
    if quality == "clean":
        assert result.sharpness > 3 * MIN_SHARPNESS and result.contrast > 2.5 * MIN_CONTRAST
    else:
        assert result.sharpness < MIN_SHARPNESS / 3 and result.contrast < MIN_CONTRAST / 2.5


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def test_blur_alone_and_low_contrast_alone_each_fail():
    clean = Image.open(DOCUMENTS / "seed-below-marks" / "marksheet.png").convert("RGB")
    assert assess(_png(clean)).ok
    blurred = assess(_png(clean.filter(ImageFilter.GaussianBlur(4))))
    assert blurred.sharpness < MIN_SHARPNESS and not blurred.ok
    faded = assess(_png(ImageEnhance.Contrast(clean).enhance(0.25)))
    assert faded.contrast < MIN_CONTRAST and not faded.ok


def test_scores_do_not_depend_on_resolution():
    clean = Image.open(DOCUMENTS / "seed-below-marks" / "marksheet.png").convert("RGB")
    big = assess(_png(clean.resize((2000, 2600), Image.Resampling.LANCZOS)))
    assert big.ok


def test_measurement_is_deterministic():
    content = (DOCUMENTS / "seed-low-confidence" / "marksheet.jpg").read_bytes()
    assert assess(content) == assess(content)


def test_quality_check_messages():
    assert quality_check(False)["message"] == LOW_QUALITY_REASON == "Image is blurry or low-contrast; needs human review"
    passed = quality_check(True, "sharpness 180.0")
    assert passed["passed"] and passed["kind"] == "image_quality"
