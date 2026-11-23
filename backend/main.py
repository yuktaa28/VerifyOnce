"""ASGI entry point: ``uvicorn backend.main:app --reload``."""

from backend.app import create_app

app = create_app()
