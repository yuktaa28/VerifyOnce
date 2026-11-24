# VerifyOnce

Verify a citizen once, then reuse that verification for every scheme. Citizens upload their documents one time to build a verified profile. A vision model (Gemini by default, on Google's free tier) reads the fields from each document image, deterministic checks verify or flag every fact, and an evaluator checks each application against the scheme's rules, citing the documents behind every decision. An officer reviews only the uncertain cases.

Built for Homingo Hackathon Edition 01, Automation track, problem statement B2: AI for the government office. All people, documents and schemes in this repo are fictional.

## Project layout

| Path | Contents |
|---|---|
| `backend/` | FastAPI service: data model, extraction, verification, evaluator |
| `frontend/` | Streamlit app (citizen and officer pages). It calls the API over HTTP and holds no business logic. |
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
| `EXTRACTION_PROVIDER` | `gemini` | `gemini` (free tier) or `anthropic` (paid) |
| `GEMINI_API_KEY` | (none) | Needed for live extraction with Gemini. A free key comes from Google AI Studio. Without it, every upload is flagged for review. |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` | Gemini model used to read documents. `python scripts/list_models.py` lists the ones your key can use. (`gemini-3.8-flash` was often overloaded, and `gemini-2.5-flash` is closed to new accounts.) |
| `ANTHROPIC_API_KEY` | (none) | Only needed when `EXTRACTION_PROVIDER=anthropic` |
| `ANTHROPIC_MODEL` | `claude-opus-5-5` | Model used when the provider is `anthropic` |
| `EXTRACTION_USE_CACHE` | off | `1` reads cached results from `data/cache/` first; live is the default |
| `VERIFY_ONCE_DB` | `data/verifyonce.db` | SQLite file path, or a full SQLAlchemy URL |
| `VERIFY_ONCE_CONFIDENCE_CUTOFF` | `0.85` | Evidence below this confidence is flagged. Models report coarse values (0.8, 0.9, 1.0), so a bare 0.8 does not pass. |

A shell variable overrides `.env`, for example `$env:EXTRACTION_USE_CACHE="1"`.

## Run the app (two PowerShell windows)

Window 1, the API:

```powershell
cd C:\Users\YUKTA\OneDrive\PROJECTS\VerifyOnce
.venv\Scripts\python.exe scripts\seed.py --reset
.venv\Scripts\python.exe -m uvicorn backend.main:app --port 8000
```

Window 2, the UI (opens http://localhost:8501):

```powershell
cd C:\Users\YUKTA\OneDrive\PROJECTS\VerifyOnce
.venv\Scripts\python.exe -m streamlit run frontend\app.py --server.port 8501
```

The UI reads `API_BASE_URL` (default `http://127.0.0.1:8000`). If the UI says "API down", start window 1 and refresh the page.

- **Citizen pages:** choose a profile, upload documents, apply for schemes.
- **Officer pages:** review queue, case view.
- **Every page:** a "SYNTHETIC DATA - DEMO ONLY" banner and a status strip (API up/down, provider and model, live or cached extraction).
- **Display rules:** ID numbers are always masked, and every fact shows where it came from (`live` / `cache` and the model, or "seed fixture").

## Other commands

```powershell
python scripts/seed.py --reset              # fresh database with the five seed profiles
uvicorn backend.main:app --reload           # API on http://127.0.0.1:8000, docs at /docs
python -m pytest                            # all offline tests (the model is mocked)
python scripts/run_seed_cases.py            # every seed profile x every scheme, expected vs actual
python scripts/make_documents.py            # regenerate the synthetic documents
python scripts/list_models.py               # Gemini models your key can use that accept images (names only)
python scripts/live_check.py --injection    # real extraction of the clean, blurry and injection marksheets
python scripts/live_check.py --profile seed-low-confidence   # all 3 documents of a case, cross-checks, verdicts
python -m backend.image_quality             # sharpness and contrast of every synthetic document
python -m pytest -m live --run-live -s      # opt-in tests against the real provider (3 API calls)
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
| `POST` | `/profiles/{id}/applications` | Body `{"scheme_id": ...}`. Creates a new decision: verdict, per-rule outcomes (with requirement and source document), source facts, mismatches, plain-language reason, `possible_schemes`. Resubmitting adds a new decision; it never overwrites. Refused (409) once an officer has decided the application. |
| `GET` | `/profiles` | Profiles for the picker |
| `GET` | `/profiles/{id}` | Documents, image quality, extraction details, and each fact with its checks and flag reasons |
| `GET` | `/profiles/{id}/applications` | The profile's applications with their latest decision |
| `GET` | `/officer/queue` | Applications with their latest decision, `needs_human_review` first. Filter with `?verdict=...` (repeatable). |
| `GET` | `/officer/cases/{application_id}` | Case detail: latest decision, rule outcomes, current evaluation, conflicts side by side, unresolved flagged facts, documents with image quality, whether approval is allowed and why not |
| `POST` | `/officer/cases/{application_id}/evidence` | Body `{fact_id, resolution, reason, officer}`, where `resolution` is `confirm` or `reject`. Reason required. Re-evaluates the case (new decision). |
| `POST` | `/officer/cases/{application_id}/decision` | Body `{action, verdict, reason, officer}`, where `action` is `approve`, `reject` or `override`. New decision by the officer. |
| `GET` | `/applications/{id}/audit` | Audit history: original decision, evidence resolutions, officer actions, with actor and timestamp |
| `GET` | `/applications/{id}` | Decision history and raw audit events |
| `GET` | `/schemes` | Loaded schemes and rules |
| `GET` | `/status` | API status, provider and model, live or cached extraction, whether a key is set (never the key) |
| `GET` | `/health` | Status |

## Officer rules (`backend/officer.py`)

- Every officer action creates a new Decision and an AuditEvent that links to the application's original decision. Decisions are superseded, never edited.
- **Resolving evidence** means confirming or rejecting one flagged fact, and needs a typed reason. The profile's facts are re-checked (rejecting a misspelt name clears the conflict on the documents that agree), and the case is re-evaluated. Other evaluator-decided applications of the same profile are re-evaluated too.
- **Approve** is blocked while any flagged fact the scheme relies on is unresolved. It also needs the rules to pass on the current evidence.
- **Override** sets approve or reject against the evaluator and needs a typed reason. An override to approve is still blocked by unresolved flags.
- **Reject** needs a typed reason.
- A citizen cannot resubmit over an officer's decision.

There is no login yet. The officer's name comes from the sidebar and is recorded as `officer:<name>`.

## How a document becomes facts

1. **Extraction** (`backend/extraction.py`). A vision model reads the image into a fixed schema per document type: each field has a `value` (null if unreadable, never guessed) and a `confidence` from 0 to 1.
   - **Provider:** Gemini by default (google-genai SDK, `temperature` 0, JSON output constrained by `response_json_schema`), or Anthropic with `EXTRACTION_PROVIDER=anthropic`. The prompt, schema, retry and cache are the same for both.
   - The system prompt says document text is untrusted data, that instructions inside a document are ignored, and that the reply is only JSON.
   - The provider constrains the reply to the JSON schema, and Pydantic re-checks it with extra keys forbidden.
   - An invalid reply gets one retry. A second failure stores every field empty, so the document goes to an officer.
   - **Transient errors are retried:** HTTP 429 (quota / rate limit), 408, 5xx such as `503 UNAVAILABLE`, network timeouts and connection errors get up to 3 attempts, waiting about 2s and then 5s. Each retry is logged.
   - **Everything else goes straight to officer review:** a refusal, a blocked response (Gemini safety blocks included), an authentication or other client error (for example an invalid key or unknown model), or a transient error that outlasts the retries. There is no fallback model and no crash. The upload returns `extraction_failed`, every field is flagged, and the reason is logged and returned in `extraction.error`.
   - Results are cached by file hash in `data/cache/`, written only by real live runs. Each result says `live` or `cache`, and which provider and model produced it.
   - The model never sees scheme rules and never decides anything.
2. **Verification** (`backend/verification.py`). Each fact moves from `unverified` to `verified` or `flagged`. It is flagged when:
   - its value is null (model confidence is not calibrated, so a null is always flagged),
   - a validator fails (date of birth parses and is between 1900 and today; income is a non-negative whole number; marks are 0-100; `id_last4` is 4 digits),
   - its confidence is below the cutoff (0.85),
   - its document image failed the image-quality check (below),
   - or its name or date of birth disagrees with another document.

   Each new upload re-checks the whole profile, so a later document can flag an earlier fact. Facts an officer has reviewed keep the officer's status.
3. **Image quality** (`backend/image_quality.py`). Every upload is measured directly, with no model involved: sharpness is the variance of a Laplacian filter (after light smoothing, so scanner grain doesn't count), and contrast is the standard deviation of the grey levels. Both are measured at a fixed 1000-pixel width.
   - A document below either threshold (sharpness 35, contrast 15) is low quality. Every fact from it is flagged with "Image is blurry or low-contrast, so an officer must check it", even if the model reported full confidence.
   - The thresholds come from our 16 synthetic documents: the clean ones score 137-217 sharpness and 42-67 contrast; the blurry marksheet scores 8.3 and 4.7.
   - Real phone photos may need the thresholds re-tuned.

**Sampling.** Gemini is called with `temperature` 0. The Anthropic provider sends no `temperature`: the installed Anthropic SDK (2.x) uses API version 2026-10-21, which rejects `temperature`, `top_p` and `top_k` on every model.

**Known limit.** A model that obeyed an instruction hidden in a document *and* kept to the schema (for example reporting marks as 99) would not be caught by the schema. The prompt and the live injection test cover that; cross-checks within a document (subject totals against the percentage) would be a further defence.

## How decisions are made

Plain code makes every decision (`backend/evaluator.py`). The LLM never decides. Precedence, first match wins:

1. **needs_human_review**: evidence for a field the scheme needs is missing, unreadable or invalid on some document, below the confidence cutoff, or conflicts across documents.
2. **reject**: the evidence is reliable and at least one eligibility rule fails.
3. **approve**: the evidence is reliable and every rule passes.

Review wins over reject on purpose. A rejection based on a misread or mismatched document would deny a citizen a benefit they qualify for. Every rule is still evaluated and recorded as pass, fail or unresolved, even when the verdict is review.

Evidence rules:

- A field's value comes from its highest-confidence readable observation. A value exactly at the cutoff is accepted, and a low-confidence copy that agrees with a confident one is covered by it.
- A reading from a blurry or low-contrast image is never trusted on its own, whatever its confidence. A field whose only evidence comes from such images goes to review. A matching reading from a good document covers it, and a disagreeing one is still a conflict.
- A fact an officer marked `verified` counts as full confidence. A fact marked `rejected` is ignored. Automatic verification never raises confidence.
- Names match across case, spacing, punctuation, initials and word order ("Ravi K. Patil" = "Ravi Patil"). A different spelling is a real conflict ("Ishan Rao" vs "Ishan Rau").

### Schemes

| Scheme | Rules |
|---|---|
| State Student Scholarship | income ≤ ₹3,00,000 and marks ≥ 60% |
| Income-Based Fee Waiver | income ≤ ₹4,50,000 |

Both also require consistent identity evidence: name, date of birth and the last four digits of an ID (ruleset 2026.2 added date of birth). Rule operators: `gte`, `lte`, `eq`, `in`, `exists`. A new scheme that uses the existing fields and operators needs only a new YAML file in `schemes/`, named after its id.

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
