# VerifyOnce demo script

All data is synthetic. Seeded profiles load fixture values directly: no model ran for them, and their facts are badged **"seed fixture · no model"**. Only the upload in step (d) goes through the model, and its badge says whether it ran **live** or came from the **cache** of an earlier real run.

## Before you start (5 minutes before)

From the `VerifyOnce` folder:

```powershell
.venv\Scripts\python.exe scripts\demo_reset.py
```
Then start the whole app (website and API) in one window. It builds the website first if needed:
```powershell
.venv\Scripts\python.exe scripts\serve.py
```
Finally, in a second window, check everything:
```powershell
.venv\Scripts\python.exe scripts\preflight.py
```

You want **READY for the demo**. Then open http://127.0.0.1:8000.

After the reset, the officer queue holds two cases **awaiting officer decision**: `seed-name-mismatch` and `seed-low-confidence`, both for the State Student Scholarship. `seed-below-marks` has no applications yet.

## Click-through (check every page, about 2 minutes)

1. **Home** (`/`): the amber strip reads "Synthetic data - demo only. Never upload real documents." The footer's **System status** shows *Up · database ok*, the model, and the extraction mode.
2. **Try as a citizen** → *Profile* → `seed-below-marks`. Three documents appear, each fact badged **seed fixture · no model**.
3. **4. Apply for schemes** → **Apply** under *State Student Scholarship*. The result says **Not eligible**, the rule table shows `minimum_marks` **✕ fail**, and a green **You may qualify for** box appears.
4. Click **Apply for Income-Based Fee Waiver** in that box. The result says **Approved**.
5. **Officer console**: the counts show 2 awaiting an officer. Filter **Awaiting officer**, then **Open** `seed-name-mismatch`.
6. Type an **Officer name**. Under *Evidence to resolve* → *Name on the income certificate*: **Reject: the value is wrong**, type a reason, **Record resolution**. The banner says the suggested verdict is now Approved, and the case still waits.
7. **Decide** → **Approve** → type a reason → **Record decision**. The **Audit history** lists every step with actor and time.
8. **How it works**: the architecture diagram and *Limitations and responsible use*.
9. Optional: narrow the window to phone width. The menu collapses into a **Menu** button and nothing scrolls sideways.

To start again from a known state, stop `serve.py` (Ctrl+C), run `scripts\demo_reset.py`, and start `serve.py` again.

## 3-minute version

### 0:00 to 0:15, opening
**Say:** "Citizens prove the same facts again for every scheme. VerifyOnce verifies documents once and reuses them. Rules decide eligibility, and an officer handles only the uncertain cases. Everything you'll see is synthetic data."
**Point at:** the amber **Synthetic data - demo only** strip and the **System status** footer (model, *Live* or *Cache first*).

### 0:15 to 0:55, (a) rejected for one scheme, approved for another with no re-upload
1. **Try as a citizen** → *Profile* → `seed-below-marks`.
   **Say:** "Arjun's three documents are already on file and verified. These are seeded fixtures, so no model ran for them."
2. **Apply** under *State Student Scholarship*.
   **Say:** "Not eligible: marks 52% are below the 60% minimum. The table shows each rule, the value found, and the document it came from. Income passed."
3. Click **Apply for Income-Based Fee Waiver** in the green *You may qualify for* box.
   **Say:** "Same verified profile, no new upload. Approved."

### 0:55 to 1:55, (b) an officer resolves a name mismatch, with an audit trail
1. **Officer console**.
   **Say:** "Two cases are awaiting an officer. Nothing automatic can close them."
   Type "Demo Officer" as the **Officer name**, then **Open** the `seed-name-mismatch` case.
2. **Point at** *Conflicts between documents*: "Ishan Rao" on the identity card and marksheet, "Ishan Rau" on the income certificate, side by side.
   **Say:** "Approve is blocked while evidence is unresolved."
3. Under *Name on the income certificate*:
   1. Choose **Reject: the value is wrong**.
   2. In *Reason (required)*, type `Typo on the income certificate; confirmed with the issuing office`.
   3. Click **Record resolution**.

   **Say:** "A reason is mandatory. The case is re-evaluated as a suggestion, not a decision. It still waits for me."
   **Point at:** *awaiting officer decision* and *Suggested: Approved*.
4. **Decide** → **Approve** → type a reason → **Record decision**.
5. **Audit history**.
   **Say:** "The original automatic assessment, my evidence decision with its reason, and my final decision, each with actor and time. Decisions are never edited, only superseded."

### 1:55 to 2:30, (c) a blurry scan the system refuses to trust
1. **Back to the queue** → **Open** the `seed-low-confidence` case.
2. **Point at** *Image quality*: the marksheet's sharpness 8.3 against a minimum of 35, and contrast 4.7 against a minimum of 15.
   **Say:** "This is measured directly from the image, with no AI. Every fact from this scan is flagged, whatever confidence a model reports, so the marks can't decide the scholarship. An officer has to check the original."

### 2:30 to 2:55, (d) one upload through the model
1. **Citizen** → *Or start a new profile* → type `demo-live` → **Start**.
2. **3. Upload a document** → *Or use a sample document* → **Marksheet · seed-low-confidence** (the type switches to Marksheet) → **Upload and read**. You can also drag the file `data\documents\seed-low-confidence\marksheet.jpg` onto the drop area.
3. **Read the extraction line out loud, exactly as shown:**
   - If the badge says **live**: "That was a live call to Gemini just now."
   - If it says **cache**: "This reading was made by Gemini in an earlier real run and replayed from the cache. No model ran just now."

   **Say:** "Look at what the model read and the confidence it gave. Whatever it says, the image check flags every field: Image is blurry or low-contrast, so an officer must check it."
   Do not claim the reading is right or wrong without checking it against the image. In one earlier real run, the model read the year of birth as 2004 instead of 2006, with confidence 0.8.

### 2:55 to 3:00, close
**Say:** "Verified once, reused everywhere. Rules decide, officers sign off every uncertain case, and every step is on the record."

## 60-second version

1. **(0:00)** The strip and the System status footer. **Say:** "Synthetic data; rules decide eligibility; officers handle uncertain cases."
2. **(0:10)** **Try as a citizen** → `seed-below-marks` → **Apply** under the scholarship → **Apply for Income-Based Fee Waiver**. **Say:** "Rejected on marks, approved for the fee waiver with the same documents, no re-upload. These are seeded fixtures."
3. **(0:30)** **Officer console** → open `seed-name-mismatch`.
   - Reject *Name on the income certificate* with a typed reason → **Record resolution**, then **Approve**.
   - **Say:** "The officer must give a reason. The system only suggests a verdict; the officer decides."
4. **(0:50)** **Audit history**. **Say:** "Every step is recorded, with who did it and when."

## Live, cached and seeded: what each one means

| What you see | What actually happened |
|---|---|
| Fact badge **seed fixture · no model** | Values loaded from `backend/seed.py`. No model ran. |
| Fact badge **live · gemini-3.5-flash-lite** | Gemini read this image just now. |
| Fact badge **cache · gemini-3.5-flash-lite** | Gemini read this exact file in an earlier real run (`scripts\prime_cache.py`), and the stored reply was replayed. No model ran now. |
| System status **Live** / **Cache first** | Whether uploads call the model or use the cache first. |

Never describe a cached or seeded result as live.

## Public demo mode

For a public website, start it with `PUBLIC_DEMO=1` and a `DEMO_PIN` set in the environment (never in a file you commit):
```powershell
$env:PUBLIC_DEMO="1"; $env:DEMO_PIN="<choose one>"; .venv\Scripts\python.exe scripts\serve.py
```
Visitors can then upload only the cached sample documents, and officer actions ask for the PIN (enter it next to *Officer name*). Viewing is open to everyone.

## Streamlit fallback

The Streamlit UI still works against the same API: start `scripts\serve.py` (or `uvicorn backend.main:app --port 8000`), then:
```powershell
.venv\Scripts\python.exe -m streamlit run frontend\app.py --server.port 8501
```
Open http://localhost:8501 and set the sidebar *Officer name*. Use it with `PUBLIC_DEMO` off (the default).

## If something fails

| Problem | What to do |
|---|---|
| Footer says **API unreachable** | Start `scripts\serve.py`, then reload the page. Run `scripts\preflight.py`. |
| `http://127.0.0.1:8000` says **the website is not built yet** | Run `scripts\serve.py --build`, or `cd web; npm ci; npm run build`. Needs Node.js. |
| An upload shows **Gemini API error 503 UNAVAILABLE** or **quota or rate limit reached (HTTP 429)** | The app already retried (2s, 5s). Every field is flagged and the document goes to an officer: say so. It's the designed behaviour. For the rest of the demo, switch to the cache (next row). |
| Model slow or down, or no internet | In the `serve.py` window press Ctrl+C, run `$env:EXTRACTION_USE_CACHE="1"`, then start `scripts\serve.py` again in the same window. The 16 demo documents are now served from the cache and labelled **cache**. Say so. |
| **Too many uploads** or **Too many applications** | The per-IP rate limit. Wait the number of seconds shown, or restart `serve.py`. |
| Preflight says **Demo cache: n of 16** | Run `scripts\prime_cache.py` before the demo (needs the API key and internet). Re-run it to retry any failures; cached files are skipped. |
| Preflight says the database **is from an older version** | Run `scripts\demo_reset.py`, then restart `serve.py`. |
| The queue does not show two cases | Run `scripts\demo_reset.py` (without `--clean`) and refresh. |
| Anything else | `scripts\demo_reset.py`, restart `serve.py`, refresh. Details are in the server window, never on screen. |
