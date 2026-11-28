"""The synthetic demo documents in data/documents (16 images: 5 cases x 3, plus one injection test)."""

from __future__ import annotations

from pathlib import Path

from backend.config import REPO_ROOT
from backend.extraction import DOCUMENT_SCHEMAS

DOCUMENTS_DIR = REPO_ROOT / "data" / "documents"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}


def demo_documents(root: Path = DOCUMENTS_DIR) -> list[tuple[Path, str]]:
    """(image path, document type) for every demo image, sorted by path.

    The document type is the file name, e.g. seed-approve/identity_card.png.
    """
    found = []
    for path in sorted(Path(root).glob("*/*")):
        if path.suffix.lower() in IMAGE_SUFFIXES and path.stem in DOCUMENT_SCHEMAS:
            found.append((path, path.stem))
    return found
