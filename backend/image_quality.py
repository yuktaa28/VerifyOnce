"""Deterministic image-quality check for uploaded documents. No model is involved.

Model confidence is not calibrated: a vision model can misread a blurry scan and still
report 0.8. So every upload is also measured directly:

* sharpness: variance of a Laplacian (second-derivative) filter. Crisp text edges give
  a high value; blur spreads edges out and the value collapses. The image is lightly
  smoothed first so scanner grain does not count as detail.
* contrast: standard deviation of the grey levels. Washed-out scans score low.

Images are scaled to a 1000-pixel width first, so scores do not depend on resolution.
A document below either threshold is "low quality": every fact read from it is
flagged with LOW_QUALITY_REASON, and a field whose only evidence comes from such
documents sends the application to an officer.

The thresholds come from the 16 synthetic documents in data/documents
(``python -m backend.image_quality`` prints the measurements):

    clean documents (15)   sharpness 137-217   contrast 42-67
    blurry marksheet       sharpness 8.3       contrast 4.7

Each threshold sits near the geometric midpoint, so the margin is about the same
ratio on both sides (sharpness about 4x, contrast about 3x). Real phone photos and
scans may need them re-tuned.
"""

from __future__ import annotations

import io
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageFilter

REFERENCE_WIDTH = 1000
MIN_SHARPNESS = 35.0
MIN_CONTRAST = 15.0
LOW_QUALITY_REASON = "Image is blurry or low-contrast, so an officer must check it"
CHECK_NAME = "image_quality"


@dataclass(frozen=True)
class ImageQuality:
    sharpness: float
    contrast: float
    min_sharpness: float = MIN_SHARPNESS
    min_contrast: float = MIN_CONTRAST

    @property
    def ok(self) -> bool:
        return self.sharpness >= self.min_sharpness and self.contrast >= self.min_contrast

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "ok": self.ok}

    def describe(self) -> str:
        return (f"sharpness {self.sharpness:.1f} (min {self.min_sharpness:g}), "
                f"contrast {self.contrast:.1f} (min {self.min_contrast:g})")


def measure(content: bytes) -> tuple[float, float]:
    """(sharpness, contrast) of an image file's bytes."""
    with Image.open(io.BytesIO(content)) as image:
        gray = image.convert("L")
    if gray.width != REFERENCE_WIDTH:
        height = max(3, round(gray.height * REFERENCE_WIDTH / gray.width))
        gray = gray.resize((REFERENCE_WIDTH, height), Image.Resampling.LANCZOS)
    contrast = float(np.asarray(gray, dtype=np.float64).std())
    s = np.asarray(gray.filter(ImageFilter.GaussianBlur(1)), dtype=np.float64)
    laplacian = s[1:-1, :-2] + s[1:-1, 2:] + s[:-2, 1:-1] + s[2:, 1:-1] - 4.0 * s[1:-1, 1:-1]
    return float(laplacian.var()), contrast


def assess(content: bytes, *, min_sharpness: float = MIN_SHARPNESS,
           min_contrast: float = MIN_CONTRAST) -> ImageQuality:
    sharpness, contrast = measure(content)
    return ImageQuality(round(sharpness, 2), round(contrast, 2), min_sharpness, min_contrast)


def quality_check(ok: bool, detail: str | None = None) -> dict[str, Any]:
    """The check stored on every fact read from a document."""
    if ok:
        message = "Image is sharp and clear enough" + (f" ({detail})" if detail else "")
    else:
        message = LOW_QUALITY_REASON
    return {"name": CHECK_NAME, "kind": CHECK_NAME, "passed": ok, "message": message}


def _report(folder: Path) -> int:
    paths = sorted(p for p in folder.rglob("*") if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"})
    print(f"{'document':<46}{'sharpness':>11}{'contrast':>10}  result")
    for path in paths:
        quality = assess(path.read_bytes())
        print(f"{path.relative_to(folder).as_posix():<46}{quality.sharpness:>11.1f}{quality.contrast:>10.1f}  "
              f"{'ok' if quality.ok else 'LOW QUALITY'}")
    print(f"thresholds: sharpness >= {MIN_SHARPNESS:g}, contrast >= {MIN_CONTRAST:g}")
    return 0


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    raise SystemExit(_report(Path(sys.argv[1]) if len(sys.argv) > 1 else root / "data" / "documents"))
