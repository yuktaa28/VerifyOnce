# VerifyOnce

Verify a citizen once, then reuse that verification for every scheme. VerifyOnce reads a citizen's documents, checks applications against government scheme rules in minutes, and cites the rule clause behind every decision.

Built for Homingo Hackathon Edition 01, problem statement B2: AI for the government office.

## Project layout

| Folder | Contents |
|---|---|
| `backend/` | FastAPI service: document extraction, rules engine, API |
| `frontend/` | Streamlit app for citizens and officers |
| `schemes/` | Scheme rules as structured YAML |
| `scripts/` | Helper and seed scripts |
| `tests/` | pytest suite |
| `data/documents/` | Synthetic sample documents (never real personal data) |

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux
pip install -r requirements.txt
cp .env.example .env          # then fill in your own values
```

Never commit `.env` or any real API key.

## Status

Step 0: project scaffold. The app is still being built.
