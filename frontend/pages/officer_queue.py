"""Officer: every application with its latest decision, cases awaiting an officer first."""

import streamlit as st

from frontend import api, ui

VERDICTS = ["needs_human_review", "reject", "approve"]

ui.page_chrome()
st.title("📥 Review queue")
selected = st.multiselect("Show verdicts", VERDICTS, default=VERDICTS, format_func=ui.verdict_label)
rows = ui.call(api.queue, selected or VERDICTS)

waiting = sum(r["status"] == "awaiting officer decision" for r in rows)
st.metric("Awaiting officer decision", waiting)
if not rows:
    st.info("No applications yet. Citizens apply on the 'Apply for schemes' page.")
    st.stop()

ui.wrapped_table([
    {
        "Case": r["application_id"],
        "Status": ("⏳ " if r["status"] == "awaiting officer decision" else "") + r["status"],
        "Verdict on record": ui.verdict_label(r["verdict"]),
        "Suggested now": ui.verdict_label(r["suggested_verdict"]) if r["suggested_verdict"] else "—",
        "Profile": r["profile_id"],
        "Scheme": r["scheme_name"],
        "Unresolved flags": r["unresolved_flags"],
        "Decided by": r["decided_by"],
    }
    for r in rows
])

options = {r["application_id"]: f"Case {r['application_id']}: {r['profile_id']} · {r['scheme_name']} · "
                                f"{r['status']}" for r in rows}
case_id = st.selectbox("Open a case", list(options), format_func=options.get)
if st.button("Open case", type="primary"):
    st.session_state["case_id"] = case_id
    st.switch_page("pages/officer_case.py")
