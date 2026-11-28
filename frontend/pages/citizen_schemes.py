"""Citizen: apply for schemes with the saved profile, see verdicts, reuse the profile elsewhere."""

import streamlit as st

from frontend import api, ui

ui.page_chrome()
st.title("🏛️ Apply for schemes")
profile_id = ui.require_profile()

try:
    applications = {a["scheme_id"]: a for a in api.profile_applications(profile_id)}
except api.ApiError as exc:
    if exc.status == 404:
        st.info("This profile has no documents yet. Upload them first.")
        st.page_link("pages/citizen_upload.py", label="Upload documents", icon="📄")
        st.stop()
    st.error(exc.message)
    st.stop()

schemes = ui.call(api.schemes)
results = st.session_state.setdefault("last_decision", {})


def apply_for(scheme_id: str) -> None:
    results[scheme_id] = ui.call(api.apply, profile_id, scheme_id)
    st.session_state["focus_scheme"] = scheme_id


def show_decision(decision: dict) -> None:
    ui.show_verdict(decision["verdict"], decision["reason"])
    ui.show_explanation(decision)
    if decision.get("review_reasons"):
        st.markdown("**What an officer will check**")
        for reason in decision["review_reasons"]:
            st.markdown(f"- {reason['message']}")
    st.markdown("**Rule by rule**")
    ui.rule_table(decision["rule_outcomes"])
    st.caption(f"Decision {decision['sequence']} on this application (id {decision['decision_id']}) · "
               f"ruleset {decision['ruleset_version']} · decided by {decision['decided_by']}")


st.write(f"Profile **{profile_id}**. Your verified documents are reused for every scheme: no re-upload.")
for scheme in schemes:
    existing = applications.get(scheme["id"])
    with st.container(border=True):
        top = st.columns([3, 1])
        top[0].subheader(scheme["name"])
        top[0].caption(scheme["description"])
        for rule in scheme["rules"]:
            top[0].markdown(f"- `{rule['id']}`: {rule['description']}")
        by_officer = bool(existing and existing["decided_by_officer"])
        awaiting = bool(existing and existing["awaiting_officer_decision"])
        if existing:
            top[1].markdown(f"Status: **{ui.verdict_label(existing['decision']['verdict'])}**")
            if by_officer:
                top[1].caption("Decided by an officer.")
            if awaiting:
                top[1].caption("Waiting for an officer. Documents you upload are included in their review.")
        if top[1].button("Apply" if not existing else "Apply again", key=f"apply-{scheme['id']}",
                         disabled=by_officer or awaiting, type="primary"):
            apply_for(scheme["id"])
            st.rerun()
        decision = results.get(scheme["id"]) or (existing["decision"] if existing else None)
        if decision:
            show_decision(decision)

        # Reuse moment: after a rejection, offer the schemes the same profile may qualify for.
        fresh = results.get(scheme["id"])
        if fresh and fresh["verdict"] == "reject":
            for option in fresh.get("possible_schemes", []):
                if option["predicted_verdict"] != "approve" or option["scheme_id"] in results:
                    continue
                st.success(f"**You may qualify for: {option['scheme_name']}**  \n"
                           "Uses your saved, verified profile. No new documents needed.")
                if st.button(f"Apply for {option['scheme_name']}", key=f"reuse-{option['scheme_id']}"):
                    apply_for(option["scheme_id"])
                    st.rerun()
