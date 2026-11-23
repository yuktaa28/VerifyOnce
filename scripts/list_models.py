"""List the Gemini models your GEMINI_API_KEY can use that accept image input.

    python scripts/list_models.py

Prints model names only, sorted, one per line. The key is read from .env (or the
shell) and never printed.

The Gemini API does not report input types per model, so "accepts images" means:
a ``gemini-*`` model that supports ``generateContent`` and is not a specialised
variant (embedding, text-to-speech, audio/live, image generation, robotics,
computer use). All general Gemini models take image input.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx2  # noqa: E402
from google import genai  # noqa: E402
from google.genai import errors as genai_errors  # noqa: E402

from backend.config import Settings  # noqa: E402

SPECIALISED = ("embedding", "tts", "audio", "live", "image", "robotics", "computer-use")


def accepts_images(model: Any) -> bool:
    name = (getattr(model, "name", "") or "").removeprefix("models/")
    actions = getattr(model, "supported_actions", None) or []
    return (
        name.startswith("gemini-")
        and "generateContent" in actions
        and not any(word in name for word in SPECIALISED)
    )


def image_model_names(models: list[Any]) -> list[str]:
    return sorted({m.name.removeprefix("models/") for m in models if accepts_images(m)})


def main() -> int:
    settings = Settings.from_env()
    if not settings.gemini_api_key:
        print("GEMINI_API_KEY: NOT SET")
        return 1
    client = genai.Client(api_key=settings.gemini_api_key)
    try:
        models = list(client.models.list(config={"page_size": 100}))
    except genai_errors.APIError as exc:
        print(f"Gemini API error {exc.code} {exc.status}: {exc.message}")
        return 1
    except (httpx2.TransportError, OSError):
        print("Could not reach the Gemini API")
        return 1
    for name in image_model_names(models):
        print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
