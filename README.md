# VerifyOnce

Verify a citizen once, then reuse that verification for every scheme. Citizens upload their documents one time to build a verified profile. Claude reads the fields from each document image, deterministic checks verify or flag every fact, and an evaluator checks each application against the scheme's rules, citing the documents behind every decision. An officer reviews only the uncertain cases.

Built for Homingo Hackathon Edition 01, Automation track, problem statement B2: AI for the government office. All people, documents and schemes in this repo are fictional.

## Project layout

| Path | Contents |
|---|---|
| `backend/` | FastAPI service: data model, extraction, verification, evaluator |
| `frontend/` | Streamlit app (coming in a later step) |
| `schemes/` | One YAML file per scheme: rules, thresholds, ruleset version |
| `scripts/` | Seed loader, document generator, live extraction check, seed-case runner |
| `tests/` | pytest suite (live API tests are opt-in) |
| `data/documents/` | Synthetic, watermarked document images for the seed cases, with test-only manifests |

## Setup (Windows PowerShell)

Python 3.11 or newer (developed on 3.13).

```powershell
py -3.13 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Then edit `.env` and fill in your own values. `.env` is git-ignored: never commit it.

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | (none) | Needed for live extraction. Without it every upload is flagged for review. |
| `ANTHROPIC_MODEL` | `claude-opus-5-5` | Model used to read documents |
| `EXTRACTION_USE_CACHE` | off | `1` reads cached results from `data/cache/` first; live is the default |
| `VERIFY_ONCE_DB` | `data/verifyonce.db` | SQLite file path, or a full SQLAlchemy URL |
| `VERIFY_ONCE_CONFIDENCE_CUTOFF` | `0.80` | Evidence below this confidence is flagged |

A shell variable overrides `.env`, for example `$env:EXTRACTION_USE_CACHE="1"`.

## Run

```powershell
python scripts/seed.py --reset              # fresh database with the five seed profiles
uvicorn backend.main:app --reload           # API on http://127.0.0.1:8000, docs at /docs
python -m pytest                            # all offline tests (the model is mocked)
python scripts/run_seed_cases.py            # every seed profile x every scheme, expected vs actual
python scripts/make_documents.py            # regenerate the synthetic documents
python scripts/live_check.py --injection    # real extraction of the clean, blurry and injection marksheets
python -m pytest -m live --run-live -s      # opt-in tests against the real API (spends credits)
```

After pulling a step that changes the data model, run `python scripts/seed.py --reset`. The database has no migrations yet.

Upload a document and apply for a scheme:

```powershell
curl.exe -F "document_type=marksheet" -F "file=@data/documents/seed-below-marks/marksheet.png" http://127.0.0.1:8000/profiles/demo-1/documents
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/profiles/demo-1/applications -ContentType 'application/json' -Body '{"scheme_id": "income_based_fee_waiver"}'
```

## API

| Method | Path | Returns |
|---|---|---|
| `POST` | `/profiles/{id}/documents` | Multipart `document_type` (`identity_card`, `income_certificate`, `marksheet`) and `file` (PNG, JPEG, GIF or WEBP). Extracts the fields, then returns each normalized fact with its confidence, validator results and status, plus the document status, `extraction_source` and the raw model replies. Creates the profile if needed. |
| `POST` | `/profiles/{id}/applications` | Body `{"scheme_id": ...}`. Creates a new decision: verdict, per-rule outcomes, source facts, mismatches, plain-language reason, `possible_schemes`. Resubmitting adds a new decision; it never overwrites. |
| `GET` | `/profiles/{id}` | Documents, extraction details, and each fact with its checks |
| `GET` | `/applications/{id}` | Decision history and audit trail |
| `GET` | `/schemes` | Loaded schemes and rules |
| `GET` | `/health` | Status |

## How a document becomes facts

1. **Extraction** (`backend/extraction.py`). Claude vision reads the image into a fixed schema per document type: each field has a `value` (null if unreadable, never guessed) and a `confidence` from 0 to 1.
   - The system prompt says document text is untrusted data, that instructions inside a document are ignored, and that the reply is only JSON.
   - Structured outputs constrain the reply, and Pydantic re-checks it with extra keys forbidden.
   - An invalid reply gets one retry. A second failure stores every field empty, so the document goes to an officer.
   - Results are cached by file hash in `data/cache/`, written only by real live runs. Each result says `live` or `cache`.
   - The model never sees scheme rules and never decides anything.
2. **Verification** (`backend/verification.py`). Each fact moves from `unverified` to `verified` or `flagged`. It is flagged when:
   - its value is null (model confidence is not calibrated, so a null is always flagged),
   - a validator fails (date of birth parses and is between 1900 and today; income is a non-negative whole number; marks are 0-100; `id_last4` is 4 digits),
   - its confidence is below the cutoff,
   - or its name or date of birth disagrees with another document.

   Each new upload re-checks the whole profile, so a later document can flag an earlier fact. Facts an officer has reviewed keep the officer's status.

**No `temperature` is sent.** The installed Anthropic SDK (2.x) uses API version 2026-10-21, which rejects `temperature`, `top_p` and `top_k` on every model. Consistency comes from schema-constrained output and the deterministic checks instead.

**Known limit.** A model that obeyed an instruction hidden in a document *and* kept to the schema (for example reporting marks as 99) would not be caught by the schema. The prompt and the live injection test cover that; cross-checks within a document (subject totals against the percentage) would be a further defence.

## How decisions are made

Plain code makes every decision (`backend/evaluator.py`). The LLM never decides. Precedence, first match wins:

1. **needs_human_review**: evidence for a field the scheme needs is missing, unreadable or invalid on some document, below the confidence cutoff, or conflicts across documents.
2. **reject**: the evidence is reliable and at least one eligibility rule fails.
3. **approve**: the evidence is reliable and every rule passes.

Review wins over reject on purpose. A rejection based on a misread or mismatched document would deny a citizen a benefit they qualify for. Every rule is still evaluated and recorded as pass, fail or unresolved, even when the verdict is review.

Evidence rules:

- A field's value comes from its highest-confidence readable observation. A value exactly at the cutoff is accepted, and a low-confidence copy that agrees with a confident one is covered by it.
- A fact an officer marked `verified` counts as full confidence. A fact marked `rejected` is ignored. Automatic verification never raises confidence.
- Names match across case, spacing, punctuation, initials and word order ("Ravi K. Patil" = "Ravi Patil"). A different spelling is a real conflict ("Ishan Rao" vs "Ishan Rau").

### Schemes

| Scheme | Rules |
|---|---|
| State Student Scholarship | income ≤ ₹3,00,000 and marks ≥ 60% |
| Income-Based Fee Waiver | income ≤ ₹4,50,000 |

Both also require a consistent name and the last four digits of an ID. Rule operators: `gte`, `lte`, `eq`, `in`, `exists`. A new scheme that uses the existing fields and operators needs only a new YAML file in `schemes/`, named after its id.

### Records

- **Fact**: one value for one field from one document, with its confidence, status, checks and history. Overlapping values from different documents are all kept, so conflicts stay visible.
- **Decision**: immutable. Both the ORM and a SQLite trigger refuse updates and deletes. A resubmission or correction adds a new decision that points to the one it supersedes, plus an audit event.
- **ID numbers**: only the last four digits are ever stored. Long digit runs in model replies are masked before anything is kept.

## Seed cases and documents

`backend/seed.py` is the single source of truth. `scripts/make_documents.py` renders each seed document as a watermarked image ("SYNTHETIC - DEMO ONLY") in `data/documents/<case>/`, with a `manifest.json` of expected values. Manifests are for tests only; extraction never reads them.

| Profile | Scholarship | Fee waiver | What it shows |
|---|---|---|---|
| `seed-approve` | approve | approve | Name variants with initials still match |
| `seed-reject-income` | reject | reject | Income above both limits |
| `seed-below-marks` | reject | approve | Fails on marks only (income 2,10,000, marks 52%); fee waiver offered in `possible_schemes` |
| `seed-name-mismatch` | review | review | Income certificate says "Ishan Rau", the other documents "Ishan Rao" |
| `seed-low-confidence` | review | approve | Blurry, low-contrast marksheet; the fee waiver needs no marks |

`data/documents/prompt-injection/` holds the below-marks marksheet with an instruction to AI systems printed in its remarks, for the injection tests.
