"""Safety limits for the public website: upload checks, per-IP rate limits and the demo PIN.

All in memory and free: no external service. Limits reset when the server restarts.
"""

from __future__ import annotations

import hmac
import math
import threading
import time
from collections import deque
from collections.abc import Callable

MAX_UPLOAD_BYTES = 3 * 1024 * 1024
MAX_UPLOAD_MB = MAX_UPLOAD_BYTES // (1024 * 1024)
ALLOWED_UPLOAD_TYPES = ("png", "jpg")
# Requests bigger than this are refused before the body is read (3 MB file plus form overhead).
MAX_REQUEST_BYTES = MAX_UPLOAD_BYTES + 256 * 1024
PIN_HEADER = "X-Demo-Pin"

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SIGNATURE = b"\xff\xd8\xff"


class UploadRejected(ValueError):
    """An upload the website refuses, with a message a citizen can act on."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


def check_upload(content: bytes) -> str:
    """'png' or 'jpg', or UploadRejected with a clear reason.

    ``content`` is read with one byte more than the limit, so an oversized file is detected
    without reading all of it.
    """
    if not content:
        raise UploadRejected(422, "The file is empty. Choose a PNG or JPG image of the document.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise UploadRejected(413, f"The file is larger than {MAX_UPLOAD_MB} MB. "
                                  f"Upload a PNG or JPG image under {MAX_UPLOAD_MB} MB.")
    if content.startswith(PNG_SIGNATURE):
        return "png"
    if content.startswith(JPEG_SIGNATURE):
        return "jpg"
    raise UploadRejected(422, "Only PNG or JPG images are accepted. "
                              "PDF, GIF, WEBP and other files are not supported.")


class RateLimiter:
    """Sliding-window limit of ``limit`` hits per ``window`` seconds for each key (an IP address)."""

    def __init__(self, limit: int, window: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self.limit = limit
        self.window = window
        self.clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> int | None:
        """Record a hit. Returns None if allowed, else the seconds to wait before retrying."""
        now = self.clock()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return max(1, math.ceil(hits[0] + self.window - now))
            hits.append(now)
            if len(self._hits) > 10_000:  # forget idle keys so memory stays bounded
                self._prune(now)
            return None

    def blocked(self, key: str) -> int | None:
        """Seconds to wait if ``key`` is at its limit now, without recording a hit."""
        now = self.clock()
        with self._lock:
            hits = [t for t in self._hits.get(key, ()) if t > now - self.window]
            if len(hits) >= self.limit:
                return max(1, math.ceil(hits[0] + self.window - now))
            return None

    def _prune(self, now: float) -> None:
        for key in [k for k, v in self._hits.items() if not v or v[-1] <= now - self.window]:
            del self._hits[key]

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


def pin_matches(expected: str | None, given: str | None) -> bool:
    """Constant-time PIN comparison. With no PIN configured, nothing matches."""
    if not expected or not given:
        return False
    return hmac.compare_digest(expected.encode("utf-8"), given.strip().encode("utf-8"))
