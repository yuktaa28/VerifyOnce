"""Smoke tests: every Streamlit page loads against the real API (in-process) without errors."""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from backend.config import REPO_ROOT
from frontend import api

APP = str(REPO_ROOT / "frontend" / "app.py")
BANNER = "SYNTHETIC DATA - DEMO ONLY"
PAGES = [
    "pages/citizen_profile.py",
    "pages/citizen_upload.py",
    "pages/citizen_schemes.py",
    "pages/officer_queue.py",
    "pages/officer_case.py",
]


@pytest.fixture
def ui_client(seeded_client):
    """Point the frontend's HTTP client at the in-process API (a TestClient is an httpx.Client)."""
    api.use_client(seeded_client)
    yield seeded_client
    api.use_client(None)


def text_of(at: AppTest) -> str:
    parts = [m.value for m in at.markdown] + [c.value for c in at.caption] + [t.value for t in at.title]
    parts += [s.value for s in at.subheader] + [e.value for e in at.error] + [w.value for w in at.warning]
    parts += [s.value for s in at.success] + [i.value for i in at.info]
    return "\n".join(str(p) for p in parts)


def run_page(page: str, **state) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60)
    for key, value in state.items():
        at.session_state[key] = value
    at.run()
    at.switch_page(page).run()
    return at


@pytest.mark.parametrize("page", PAGES)
def test_each_page_loads(ui_client, page):
    application = ui_client.post("/profiles/seed-name-mismatch/applications",
                                 json={"scheme_id": "state_student_scholarship"}).json()
    at = run_page(page, profile_id="seed-below-marks", case_id=application["application_id"])
    assert not at.exception, at.exception
    text = text_of(at)
    assert BANNER in text
    assert "API up" in text and "gemini" in text  # status strip
    assert not at.error, [e.value for e in at.error]


def tables_of(at: AppTest) -> list:
    return [t.value for t in at.table]


def test_profile_page_masks_ids_and_shows_source_badges(ui_client):
    at = run_page("pages/citizen_profile.py", profile_id="seed-approve")
    tables = tables_of(at)
    values = {v for table in tables for v in table["Value"]}
    assert "XXXX XXXX 2051" in values and "2051" not in values
    assert {s for table in tables for s in table["Source"]} == {"seed fixture · no model"}


def test_long_flag_reasons_are_shown_in_full(ui_client):
    """Facts tables wrap text (st.table), so long reasons are not cut off."""
    at = run_page("pages/citizen_profile.py", profile_id="seed-low-confidence")
    reasons = [r for table in tables_of(at) for r in table["Why flagged"]]
    assert ("Image is blurry or low-contrast, so an officer must check it; "
            "Confidence 0.62 is below the 0.85 cutoff") in reasons
    assert not at.dataframe  # nothing rendered in a truncating grid

    at = run_page("pages/citizen_profile.py", profile_id="seed-name-mismatch")
    reasons = [r for table in tables_of(at) for r in table["Why flagged"]]
    assert "Name differs from 'Ishan Rao' on the identity card, 'Ishan Rao' on the marksheet" in reasons


def test_schemes_page_shows_reuse_offer_after_rejection(ui_client):
    at = run_page("pages/citizen_schemes.py", profile_id="seed-below-marks")
    at.button(key="apply-state_student_scholarship").click().run()
    assert not at.exception, at.exception
    text = text_of(at)
    assert "Not eligible" in text
    assert "You may qualify for: Income-Based Fee Waiver" in text
    at.button(key="reuse-income_based_fee_waiver").click().run()
    assert "Approved" in text_of(at)
    mine = ui_client.get("/profiles/seed-below-marks/applications").json()
    assert {a["scheme_id"]: a["decision"]["verdict"] for a in mine} == {
        "state_student_scholarship": "reject", "income_based_fee_waiver": "approve"}


def test_case_page_blocks_approval_with_unresolved_flags(ui_client):
    application = ui_client.post("/profiles/seed-name-mismatch/applications",
                                 json={"scheme_id": "state_student_scholarship"}).json()
    at = run_page("pages/officer_case.py", case_id=application["application_id"])
    approve = next(b for b in at.button if b.label == "Approve")
    assert approve.disabled
    assert "Approval is blocked" in text_of(at)
    shown = {m.label: m.value for m in at.metric}  # the two spellings, side by side
    assert shown == {"Identity card, Marksheet": "Ishan Rao", "Income certificate": "Ishan Rau"}
    assert "awaiting officer decision" in text_of(at)


def test_case_page_shows_a_suggestion_not_a_decision_after_resolution(ui_client):
    application = ui_client.post("/profiles/seed-name-mismatch/applications",
                                 json={"scheme_id": "state_student_scholarship"}).json()
    case_id = application["application_id"]
    rau = next(f for f in ui_client.get(f"/officer/cases/{case_id}").json()["unresolved_flags"]
               if f["value"] == "Ishan Rau")
    ui_client.post(f"/officer/cases/{case_id}/evidence", json={
        "fact_id": rau["fact_id"], "resolution": "reject", "reason": "Typo on the certificate", "officer": "Asha"})
    at = run_page("pages/officer_case.py", case_id=case_id)
    text = text_of(at)
    assert "Suggested: ✅ Approved" in text
    assert "awaiting officer decision" in text and "Decision on record" in text
    assert not next(b for b in at.button if b.label == "Approve").disabled
    audit = [row for table in tables_of(at) if "What happened" in table for row in table["What happened"]]
    assert ("Evidence rejected: Name on the income certificate. Suggested verdict now: approve. "
            "Still awaiting officer decision.") in audit

    queue = run_page("pages/officer_queue.py")
    row = next(r for t in tables_of(queue) if "Status" in t for r in t.to_dict("records") if r["Case"] == case_id)
    assert (row["Status"], row["Suggested now"]) == ("⏳ awaiting officer decision", "✅ Approved")


def test_pages_say_when_the_api_is_down(monkeypatch):
    import httpx

    api.use_client(httpx.Client(base_url="http://127.0.0.1:9", timeout=0.5))
    try:
        at = AppTest.from_file(APP, default_timeout=60).run()
        assert "API down" in text_of(at)
    finally:
        api.use_client(None)
