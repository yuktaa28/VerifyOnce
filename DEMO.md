# VerifyOnce demo script

All data is synthetic. Seeded profiles load fixture values directly: no model ran for them, and their facts are badged **"seed fixture · no model"**. Only the upload in step (d) calls the model, and its badge says whether it ran **live** or came from the **cache** of an earlier real run.

## Before you start (5 minutes before)

From the `VerifyOnce` folder:

```powershell
.venv\Scripts\python.exe scripts\demo_reset.py
```
Then start the two servers in two windows:
```powershell
.venv\Scripts\python.exe -m uvicorn backend.main:app --port 8000
```
```powershell
.venv\Scripts\python.exe -m streamlit run frontend\app.py --server.port 8501
```
Finally, check everything:
```powershell
.venv\Scripts\python.exe scripts\preflight.py
```

You want **READY for the demo**. Then open http://localhost:8501, set the sidebar *Officer name* (for example "Demo Officer"), and keep the file `data\documents\seed-low-confidence\marksheet.jpg` handy for step (d).

After the reset, the officer queue holds two cases **awaiting officer decision**: `seed-name-mismatch` and `seed-low-confidence`, both for the State Student Scholarship. `seed-below-marks` has no applications yet.

## 3-minute version

### 0:00 to 0:15, opening
**Say:** "Citizens prove the same facts again for every scheme. VerifyOnce verifies documents once and reuses them. Rules decide eligibility, and an officer handles only the uncertain cases. Everything you'll see is synthetic data."
**Point at:** the red **SYNTHETIC DATA - DEMO ONLY** banner and the status strip (API up, Gemini model, *live* or *cache first*).

### 0:15 to 0:55, (a) rejected for one scheme, approved for another with no re-upload
1. Sidebar **Choose profile** → *Profile* → `seed-below-marks`.
   **Say:** "Arjun's three documents are already on file and verified. These are seeded fixtures, so no model ran for them."
2. Sidebar **Apply for schemes** → **Apply** under *State Student Scholarship*.
   **Say:** "Not eligible: marks 52% are below the 60% minimum. The table shows each rule, the value found, and the document it came from. Income passed."
3. Click **Apply for Income-Based Fee Waiver** in the green *You may qualify for* box.
   **Say:** "Same verified profile, no new upload. Approved."

### 0:55 to 1:55, (b) an officer resolves a name mismatch, with an audit trail
1. Sidebar **Review queue**.
   **Say:** "Two cases are awaiting an officer. Nothing automatic can close them."
   Select *Case … seed-name-mismatch*, then click **Open case**.
2. **Point at** *Conflicts between documents*: "Ishan Rao" on the identity card and marksheet, "Ishan Rau" on the income certificate.
   **Say:** "Approve is blocked while evidence is unresolved."
3. Under *Name on the Income certificate*:
   1. Choose **Reject: the value is wrong**.
   2. In *Reason (required)*, type `Typo on the income certificate; confirmed with the issuing office`.
   3. Click **Record resolution**.

   **Say:** "A reason is mandatory. The case is re-evaluated as a suggestion, not a decision. It still waits for me."
   **Point at:** *Status: awaiting officer decision* and *Suggested: ✅ Approved*.
4. Scroll to **Decide** → **Approve**.
5. Scroll to **Audit history**.
   **Say:** "The original automatic assessment, my evidence decision with its reason, and my final decision, each with actor and time. Decisions are never edited, only superseded."

### 1:55 to 2:30, (c) a blurry scan the system refuses to trust
1. Sidebar **Review queue** → select the *seed-low-confidence* case → **Open case**.
2. **Point at** *Low-quality documents*: sharpness 8.3 against a minimum of 35, contrast 4.7 against a minimum of 15.
   **Say:** "This is measured directly from the image, with no AI. Every fact from this scan is flagged, whatever confidence a model reports, so the marks can't decide the scholarship. An officer has to check the original."

### 2:30 to 2:55, (d) one upload through the model
1. Sidebar **Choose profile** → expand *Or start a new profile* → type `demo-live` → **Start new profile**.
2. Sidebar **Upload documents** → *Document type*: **Marksheet** → *Document image*: choose `data\documents\seed-low-confidence\marksheet.jpg` → **Upload and read**.
3. **Read the extraction line out loud, exactly as shown:**
   - If it says **Extraction: live**: "That was a live call to Gemini just now."
   - If it says **Extraction: cache**: "This reading was made by Gemini in an earlier real run and replayed from the cache. No model ran just now."

   **Say:** "Look at what the model read and the confidence it gave. Whatever it says, the image check flags every field: Image is blurry or low-contrast, so an officer must check it."
   Do not claim the reading is right or wrong without checking it against the image. In one earlier real run, the model read the year of birth as 2004 instead of 2006, with confidence 0.8.

### 2:55 to 3:00, close
**Say:** "Verified once, reused everywhere. Rules decide, officers sign off every uncertain case, and every step is on the record."

## 60-second version

1. **(0:00)** Banner and status strip. **Say:** "Synthetic data; rules decide eligibility; officers handle uncertain cases."
2. **(0:10)** **Choose profile** `seed-below-marks` → **Apply for schemes** → **Apply** under the scholarship → **Apply for Income-Based Fee Waiver**. **Say:** "Rejected on marks, approved for the fee waiver with the same documents, no re-upload. These are seeded fixtures."
3. **(0:30)** **Review queue** → open the *seed-name-mismatch* case.
   - Reject *Name on the Income certificate* with a typed reason → **Record resolution**, then **Approve**.
   - **Say:** "The officer must give a reason. The system only suggests a verdict; the officer decides."
4. **(0:50)** Scroll to **Audit history**. **Say:** "Every step is recorded, with who did it and when."

## Live, cached and seeded: what each one means

| What you see | What actually happened |
|---|---|
| Fact badge **seed fixture · no model** | Values loaded from `backend/seed.py`. No model ran. |
| Fact badge **live · gemini-3.5-flash-lite** | Gemini read this image just now. |
| Fact badge **cache · gemini-3.5-flash-lite** | Gemini read this exact file in an earlier real run (`scripts\prime_cache.py`), and the stored reply was replayed. No model ran now. |
| Status strip **mode: live** / **mode: cache first** | Whether uploads call the model or use the cache first. |

Never describe a cached or seeded result as live.

## If something fails

| Problem | What to do |
|---|---|
| UI says **API down** | Start window 1 (uvicorn), then refresh the page. Run `scripts\preflight.py`. |
| An upload shows **Gemini API error 503 UNAVAILABLE** or **quota or rate limit reached (HTTP 429)** | The app already retried (2s, 5s). Every field is flagged and the document goes to an officer: say so. It's the designed behaviour. For the rest of the demo, switch to the cache (next row). |
| Model slow or down, or no internet | In window 1 press Ctrl+C, run `$env:EXTRACTION_USE_CACHE="1"`, then restart uvicorn in the same window. The 16 demo documents are now served from the cache and labelled **cache**. Say so. |
| Preflight says **Demo cache: n of 16** | Run `scripts\prime_cache.py` before the demo (needs the API key and internet). Re-run it to retry any failures; cached files are skipped. |
| Preflight says the database **is from an older version** | Run `scripts\demo_reset.py`, then restart uvicorn. |
| The queue does not show two cases | Run `scripts\demo_reset.py` (without `--clean`) and refresh. |
| Anything else | `scripts\demo_reset.py`, restart both windows, refresh. Details are in the server windows, never on screen. |
