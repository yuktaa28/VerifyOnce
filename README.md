# VerifyOnce

Verify a citizen once, then reuse that verification for every scheme. Citizens upload their documents one time to build a verified profile. An evaluator checks each application against the scheme's rules and cites the documents behind every decision. An officer reviews only the uncertain cases.

Built for Homingo Hackathon Edition 01, Automation track, problem statement B2: AI for the government office. All people, documents and schemes in this repo are fictional.

## Project layout

| Path | Contents |
|---|---|
| `backend/` | FastAPI service, data model, evaluator |
| `frontend/` | Streamlit app (coming in a later step) |
| `schemes/` | One YAML file per scheme: rules, thresholds, ruleset version |
| `scripts/` | `seed.py` loads the seed cases; `run_seed_cases.py` runs them through the API |
| `tests/` | pytest suite |
| `data/documents/` | Synthetic sample documents (never real personal data) |

## Setup

Python 3.11 or newer (developed on 3.13).

```powershell
py -3.13 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env     # optional; fill in your own values, never commit .env
```

| Variable | Default | Meaning |
|---|---|---|
| `VERIFY_ONCE_DB` | `data/verifyonce.db` | SQLite file path, or a full SQLAlchemy URL |
| `VERIFY_ONCE_CONFIDENCE_CUTOFF` | `0.80` | Evidence below this confidence goes to review |

## Run

```powershell
python scripts/seed.py --reset              # fresh database with the five seed profiles
uvicorn backend.main:app --reload           # API on http://127.0.0.1:8000, docs at /docs
python -m pytest                            # run the tests
python scripts/run_seed_cases.py            # every seed profile x every scheme, expected vs actual
```

Apply for a scheme:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/profiles/seed-below-marks/applications `
  -ContentType 'application/json' -Body '{"scheme_id": "state_student_scholarship"}'
```

## API

| Method | Path | Returns |
|---|---|---|
| `POST` | `/profiles/{id}/applications` | Body `{"scheme_id": ...}`. Creates a new decision: verdict, per-rule outcomes, source facts, mismatches, plain-language reason, `possible_schemes`. Resubmitting adds a new decision; it never overwrites. |
| `GET` | `/profiles/{id}` | Documents and the facts read from each |
| `GET` | `/applications/{id}` | Decision history and audit trail |
| `GET` | `/schemes` | Loaded schemes and rules |
| `GET` | `/health` | Status |

## How decisions are made

Plain code makes every decision (`backend/evaluator.py`). The LLM never decides. Precedence, first match wins:

1. **needs_human_review**: evidence for a field the scheme needs is missing, below the confidence cutoff, or conflicts across documents.
2. **reject**: the evidence is reliable and at least one eligibility rule fails.
3. **approve**: the evidence is reliable and every rule passes.

Review wins over reject on purpose. A rejection based on a misread or mismatched document would deny a citizen a benefit they qualify for. Every rule is still evaluated and recorded as pass, fail or unresolved, even when the verdict is review.

Evidence rules:

- A field's value comes from its highest-confidence observation. A value exactly at the cutoff is accepted.
- A fact an officer marked `verified` counts as full confidence. A fact marked `rejected` is ignored.
- Names match across case, spacing, punctuation, initials and word order ("Ravi K. Patil" = "Ravi Patil"). A different spelling is a real conflict ("Ishan Rao" vs "Ishan Rau").

### Schemes

| Scheme | Rules |
|---|---|
| State Student Scholarship | income ≤ ₹3,00,000 and marks ≥ 60% |
| Income-Based Fee Waiver | income ≤ ₹4,50,000 |

Both also require a consistent name and the last four digits of an ID. Rule operators: `gte`, `lte`, `eq`, `in`, `exists`. A new scheme that uses the existing fields and operators needs only a new YAML file in `schemes/`, named after its id.

### Records

- **Fact**: one value for one field from one document. Overlapping values from different documents are all kept, so conflicts stay visible. A fact's history lives in its audit events.
- **Decision**: immutable. Both the ORM and a SQLite trigger refuse updates and deletes. A resubmission or correction adds a new decision that points to the one it supersedes, plus an audit event.
- **ID numbers**: only the last four digits are ever stored.

## Seed cases

| Profile | Scholarship | Fee waiver | What it shows |
|---|---|---|---|
| `seed-approve` | approve | approve | Name variants with initials still match |
| `seed-reject-income` | reject | reject | Income above both limits |
| `seed-below-marks` | reject | approve | Fails on marks only; fee waiver offered in `possible_schemes` |
| `seed-name-mismatch` | review | review | "Ishan Rao" vs "Ishan Rau" |
| `seed-low-confidence` | review | review | Income read at 62% confidence |
