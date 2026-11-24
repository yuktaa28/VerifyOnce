"""Officer: one case. Verdict, rules, evidence, conflicts side by side, actions, audit history."""

import streamlit as st

from frontend import api, ui

ui.page_chrome()
st.title("🔎 Case view")

flash = st.session_state.pop("case_flash", None)
if flash:
    st.success(flash)

case_id = st.session_state.get("case_id")
if case_id is None:
    rows = ui.call(api.queue)
    if not rows:
        st.info("No applications yet.")
        st.stop()
    options = {r["application_id"]: f"Case {r['application_id']}: {r['profile_id']} · {r['scheme_name']} · "
                                    f"{ui.verdict_label(r['verdict'])}" for r in rows}
    case_id = st.selectbox("Choose a case", list(options), format_func=options.get)
    st.session_state["case_id"] = case_id

detail = ui.call(api.case, case_id)
officer = (st.session_state.get("officer_name") or "").strip()
decision = detail["decision"]
documents = {d["document_id"]: d for d in detail["documents"]}


def done(message: str) -> None:
    st.session_state["case_flash"] = message
    st.rerun()


# --- Verdict --------------------------------------------------------------------------------

st.subheader(f"Case {detail['application_id']}: {detail['profile_id']} · {detail['scheme']['name']}")
st.caption(detail["profile_label"] or "")
current = detail["current_evaluation"]
if detail["awaiting_officer_decision"]:
    st.info("**Status: awaiting officer decision.** This case closes only when an officer approves, "
            "rejects, or overrides with a typed reason.")
    with st.container(border=True):
        st.markdown(f"**Suggested: {ui.verdict_label(detail['suggested_verdict'])}**  \n{current['reason']}")
        st.caption("Re-evaluated on the current evidence. A suggestion only, not a decision.")
    st.markdown("**Decision on record**")
else:
    st.markdown(f"**Status: {detail['status']}**")
ui.show_verdict(decision["verdict"], decision["reason"])
st.caption(f"Decision {decision['sequence']} on this application (id {decision['decision_id']}) · decided by "
           f"{decision['decided_by']} · ruleset {decision['ruleset_version']}")

st.markdown("#### Rule outcomes")
ui.rule_table(decision["rule_outcomes"])

# --- Conflicts side by side ---------------------------------------------------------------

st.markdown("#### Conflicts between documents")
if not current["mismatches"]:
    st.caption("No conflicts between documents.")
for mismatch in current["mismatches"]:
    st.markdown(f"**{ui.field_label(mismatch['field'])}** differs across documents")
    for column, group in zip(st.columns(len(mismatch["groups"])), mismatch["groups"]):
        with column.container(border=True):
            docs = ", ".join(ui.document_label(o["document_type"]) for o in group["observations"])
            st.metric(docs, ui.show_value(mismatch["field"], group["value"]))
            for obs in group["observations"]:
                quality = (documents.get(obs["document_id"]) or {}).get("image_quality") or {}
                note = "" if quality.get("ok", True) else " · ⚠️ low-quality image"
                st.caption(f"{ui.document_label(obs['document_type'])} · confidence {obs['confidence']:.2f} · "
                           f"fact #{obs['fact_id']}{note}")

# --- Low-quality documents ----------------------------------------------------------------

low_quality = [d for d in detail["documents"] if d["image_quality"] and not d["image_quality"].get("ok")]
if low_quality:
    st.markdown("#### Low-quality documents")
    for doc in low_quality:
        q = doc["image_quality"]
        with st.container(border=True):
            st.markdown(f"**{ui.document_label(doc['document_type'])}** ({doc['filename'] or 'no file name'}): "
                        "blurry or low-contrast, so every fact read from it is flagged.")
            if "sharpness" in q:
                a, b = st.columns(2)
                a.metric("Sharpness", f"{q['sharpness']:.1f}")
                a.caption(f"Minimum {q['min_sharpness']:g}")
                b.metric("Contrast", f"{q['contrast']:.1f}")
                b.caption(f"Minimum {q['min_contrast']:g}")

# --- Evidence to resolve ------------------------------------------------------------------

st.markdown("#### Flagged evidence to resolve")
if not detail["unresolved_flags"]:
    st.caption("No unresolved flagged evidence for this scheme.")
for fact in detail["unresolved_flags"]:
    with st.container(border=True):
        st.markdown(f"**{ui.field_label(fact['field'])}** on the **{ui.document_label(fact['document_type'])}**: "
                    f"`{ui.show_value(fact['field'], fact['value'])}` · confidence {fact['confidence']:.2f} · "
                    f"{ui.source_badge(fact)}")
        for reason in fact["flag_reasons"]:
            st.markdown(f"- {reason}")
        with st.form(f"resolve-{fact['fact_id']}"):
            resolution = st.radio(
                "Resolution", ["confirm", "reject"], horizontal=True,
                format_func=lambda r: {"confirm": "Confirm: the value is right",
                                       "reject": "Reject: the value is wrong"}[r],
            )
            reason = st.text_input("Reason (required)", key=f"reason-{fact['fact_id']}")
            if st.form_submit_button("Record resolution"):
                try:
                    result = api.resolve_evidence(case_id, fact["fact_id"], resolution, reason, officer)
                    done(f"{ui.field_label(fact['field'])} on the {ui.document_label(fact['document_type'])} "
                         f"{'confirmed' if resolution == 'confirm' else 'rejected'}. Suggested verdict now: "
                         f"{ui.verdict_label(result['suggested_verdict'])}. The case still needs your decision.")
                except api.ApiError as exc:
                    st.error(exc.message)

with st.expander("All evidence on this profile"):
    for doc in detail["documents"]:
        st.markdown(f"**{ui.document_label(doc['document_type'])}** · {ui.image_quality_line(doc['image_quality'])}")
        ui.facts_table(doc["facts"])

# --- Decide -------------------------------------------------------------------------------

st.markdown("#### Decide")
if not officer:
    st.warning("Enter your officer name in the sidebar before acting.")
approve_col, reject_col, override_col = st.columns(3)

with approve_col.container(border=True):
    st.markdown("**Approve**")
    if not detail["can_approve"]:
        st.warning("Approval is blocked:\n" + "\n".join(f"- {b}" for b in detail["approve_blockers"]))
    note = st.text_input("Note (optional)", key="approve-note")
    if st.button("Approve", type="primary", disabled=not detail["can_approve"]):
        try:
            api.decide(case_id, "approve", note, officer)
            done("Approved. A new decision was recorded.")
        except api.ApiError as exc:
            st.error(exc.message)

with reject_col, st.form("reject"):
    st.markdown("**Reject**")
    reason = st.text_area("Reason (required)", key="reject-reason")
    if st.form_submit_button("Reject"):
        try:
            api.decide(case_id, "reject", reason, officer)
            done("Rejected. A new decision was recorded.")
        except api.ApiError as exc:
            st.error(exc.message)

with override_col, st.form("override"):
    st.markdown("**Override the evaluator**")
    verdict = st.radio("New verdict", ["approve", "reject"], horizontal=True, format_func=ui.verdict_label)
    reason = st.text_area("Reason (required)", key="override-reason")
    if st.form_submit_button("Override"):
        try:
            api.decide(case_id, "override", reason, officer, verdict=verdict)
            done("Override recorded as a new decision.")
        except api.ApiError as exc:
            st.error(exc.message)

# --- Audit history ------------------------------------------------------------------------

st.markdown("#### Audit history")
history = ui.call(api.audit, case_id)
ui.wrapped_table([
    {
        "When (UTC)": str(e["at"])[:19].replace("T", " "),
        "Actor": e["actor"],
        "What happened": e["summary"],
        "Decision": e["decision_sequence"] or "—",
        "Reason": e["reason"] or "—",
    }
    for e in history["events"]
])
st.caption("Decisions are superseded, never edited. Every row above is permanent.")
