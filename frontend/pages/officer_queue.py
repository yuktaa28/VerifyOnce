"""Officer: every application with its latest decision, cases needing review first."""

import streamlit as st

from frontend import api, ui

VERDICTS = ["needs_human_review", "reject", "approve"]

ui.page_chrome()
st.title("📥 Review queue")
selected = st.multiselect("Show verdicts", VERDICTS, default=VERDICTS, format_func=ui.verdict_label)
rows = ui.call(api.queue, selected or VERDICTS)

waiting = sum(r["verdict"] == "needs_human_review" for r in rows)
st.metric("Waiting for an officer", waiting)
if not rows:
    st.info("No applications yet. Citizens apply on the 'Apply for schemes' page.")
    st.stop()

st.dataframe(
    [
        {
            "Case": r["application_id"],
            "Verdict": ui.verdict_label(r["verdict"]),
            "Profile": r["profile_id"],
            "Scheme": r["scheme_name"],
            "Unresolved flags": r["unresolved_flags"],
            "Decided by": r["decided_by"],
            "Decision #": r["sequence"],
        }
        for r in rows
    ],
    hide_index=True,
    width="stretch",
)

options = {r["application_id"]: f"Case {r['application_id']}: {r['profile_id']} · {r['scheme_name']} · "
                                f"{ui.verdict_label(r['verdict'])}" for r in rows}
case_id = st.selectbox("Open a case", list(options), format_func=options.get)
if st.button("Open case", type="primary"):
    st.session_state["case_id"] = case_id
    st.switch_page("pages/officer_case.py")
