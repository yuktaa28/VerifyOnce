"""Shared presentation pieces: banner, status strip, badges, tables. No business rules."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import streamlit as st

from frontend import api

BANNER = "SYNTHETIC DATA - DEMO ONLY"

FIELD_LABELS = {
    "name": "Name",
    "date_of_birth": "Date of birth",
    "state": "State",
    "annual_income_inr": "Annual income",
    "marks_percent": "Marks",
    "id_last4": "ID number",
}
DOCUMENT_LABELS = {
    "identity_card": "Identity card",
    "income_certificate": "Income certificate",
    "marksheet": "Marksheet",
}
VERDICT_LABELS = {
    "approve": "✅ Approved",
    "reject": "❌ Not eligible",
    "needs_human_review": "🟡 Needs officer review",
}
STATUS_LABELS = {
    "verified": "✅ verified",
    "flagged": "⚠️ flagged",
    "rejected": "✖ rejected by officer",
    "unverified": "… unverified",
}


# --- Page chrome --------------------------------------------------------------------------


def banner() -> None:
    st.markdown(
        f"<div style='background:#b42318;color:#fff;padding:6px 12px;border-radius:6px;"
        f"font-weight:700;text-align:center;letter-spacing:.05em'>{BANNER}</div>",
        unsafe_allow_html=True,
    )


def status_strip() -> dict | None:
    """API up/down, provider and model, live or cached extraction. Returns /status or None."""
    try:
        status = api.status()
    except api.ApiError as exc:
        st.error(f"API down: {exc.message}. Start it with `uvicorn backend.main:app`.")
        return None
    key = "" if status["provider_key_set"] else " · ⚠️ no API key: uploads go to officer review"
    st.caption(
        f"🟢 API up at {api.base_url()} · extraction: **{status['provider']} / {status['model']}** · "
        f"mode: **{status['extraction_mode']}** · confidence cutoff {status['confidence_cutoff']:.2f}{key}"
    )
    return status


def page_chrome() -> dict:
    """Banner and status strip, drawn first on every page. Stops the page if the API is down."""
    banner()
    status = status_strip()
    if status is None:
        st.stop()
    return status


def call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run an API call; show the API's message and stop the page if it fails."""
    try:
        return fn(*args, **kwargs)
    except api.ApiError as exc:
        st.error(exc.message)
        st.stop()


# --- Formatting ---------------------------------------------------------------------------


def field_label(field: str) -> str:
    return FIELD_LABELS.get(field, field.replace("_", " ").capitalize())


def document_label(document_type: str | None) -> str:
    return DOCUMENT_LABELS.get(document_type or "", (document_type or "document").replace("_", " "))


def _inr(amount: int) -> str:
    digits = str(int(amount))
    if len(digits) <= 3:
        return f"₹{digits}"
    head, tail, groups = digits[:-3], digits[-3:], []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return "₹" + ",".join(([head] if head else []) + groups) + "," + tail


def show_value(field: str, value: Any) -> str:
    """Display a fact value. ID numbers are always masked."""
    if value is None:
        return "— unreadable —"
    if field == "id_last4":
        return f"XXXX XXXX {value}"
    if field == "annual_income_inr" and isinstance(value, (int, float)):
        return _inr(value)
    if field == "marks_percent" and isinstance(value, (int, float)):
        return f"{float(value):g}%"
    return str(value)


def source_badge(fact: dict) -> str:
    """Where a fact came from: live / cache extraction and the model, or the seed fixtures."""
    if fact.get("source") == "seed":
        return "seed fixture · no model"
    source = fact.get("extraction_source") or "unknown"
    return f"{source} · {fact.get('extraction_model') or 'no model'}"


def verdict_label(verdict: str | None) -> str:
    return VERDICT_LABELS.get(verdict or "", verdict or "—")


def show_verdict(verdict: str, reason: str) -> None:
    box = {"approve": st.success, "reject": st.error}.get(verdict, st.warning)
    box(f"**{verdict_label(verdict)}**  \n{reason}")


# --- Tables -------------------------------------------------------------------------------


def wrapped_table(rows: list[dict]) -> None:
    """A static table whose cells wrap, so long reasons are never cut off."""
    if rows:
        st.table(rows, hide_index=True)


def facts_table(facts: list[dict], *, show_document: bool = False) -> None:
    rows = []
    for fact in facts:
        row = {
            "Field": field_label(fact["field"]),
            "Value": show_value(fact["field"], fact["value"]),
            "Confidence": f"{fact['confidence']:.2f}",
            "Status": STATUS_LABELS.get(fact["verification_status"], fact["verification_status"]),
            "Source": source_badge(fact),
            "Why flagged": "; ".join(fact.get("flag_reasons") or []) or "—",
        }
        if show_document:
            row = {"Document": document_label(fact.get("document_type")), **row}
        rows.append(row)
    wrapped_table(rows)


def rule_table(outcomes: list[dict]) -> None:
    rows = [
        {
            "Rule": o["rule_id"],
            "Requirement": o.get("requirement") or f"{o['field']} {o['operator']} {o['expected']}",
            "Value found": show_value(o["field"], o.get("actual")),
            "Source document": ", ".join(document_label(d) for d in o.get("source_documents") or []) or "—",
            "Result": {"pass": "✅ pass", "fail": "❌ fail"}.get(o["outcome"], "🟡 unresolved"),
        }
        for o in outcomes
    ]
    wrapped_table(rows)


def image_quality_line(quality: dict | None) -> str:
    if not quality:
        return "Image quality: not measured"
    if "sharpness" not in quality:
        return f"Image quality: {'ok' if quality.get('ok') else 'LOW QUALITY'} (seed label)"
    verdict = "ok" if quality["ok"] else "LOW QUALITY: blurry or low-contrast"
    return (f"Image quality: sharpness {quality['sharpness']:.1f} (min {quality['min_sharpness']:g}), "
            f"contrast {quality['contrast']:.1f} (min {quality['min_contrast']:g}) → {verdict}")


def require_profile() -> str:
    profile_id = st.session_state.get("profile_id")
    if not profile_id:
        st.info("Choose a profile first.")
        st.page_link("pages/citizen_profile.py", label="Choose a profile", icon="👤")
        st.stop()
    return profile_id
