"""The model filter used by scripts/list_models.py."""

from __future__ import annotations

import sys

from google.genai import types

from backend.config import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import list_models  # noqa: E402

GENERATE = ["generateContent", "countTokens"]


def model(name: str, actions=GENERATE) -> types.Model:
    return types.Model(name=f"models/{name}", supported_actions=actions)


def test_keeps_general_gemini_models_and_drops_specialised_ones():
    models = [
        model("gemini-3.8-flash"),
        model("gemini-2.5-flash"),
        model("gemini-3.5-flash-lite"),
        model("gemini-3.1-pro-preview"),
        model("gemini-embedding-001", ["embedContent"]),
        model("gemini-3.8-flash-tts"),
        model("gemini-3.1-flash-lite-image"),
        model("gemini-2.5-flash-native-audio-preview"),
        model("gemini-live-2.5-flash"),
        model("gemini-robotics-er-1.5-preview"),
        model("gemini-2.5-computer-use-preview"),
        model("gemma-3-27b-it"),
        model("imagen-4.0-generate-001", ["predict"]),
        model("gemini-old-no-generate", ["countTokens"]),
    ]
    assert list_models.image_model_names(models) == [
        "gemini-2.5-flash", "gemini-3.1-pro-preview", "gemini-3.5-flash-lite", "gemini-3.8-flash",
    ]


def test_duplicate_names_are_printed_once():
    assert list_models.image_model_names([model("gemini-2.5-flash"), model("gemini-2.5-flash")]) == ["gemini-2.5-flash"]
