"""Citizen: pick a seeded profile (or start a new one) and see what is on file."""

import streamlit as st

from frontend import api, ui

ui.page_chrome()
st.title("👤 Choose a profile")
st.write("Pick a citizen profile. Seeded profiles already have their documents on file.")

profiles = ui.call(api.profiles)
labels = {p["profile_id"]: f"{p['profile_id']} — {p['label'] or 'uploaded profile'}" for p in profiles}
ids = list(labels)
current = st.session_state.get("profile_id")

choice = st.selectbox(
    "Profile",
    options=ids,
    index=ids.index(current) if current in ids else None,
    format_func=lambda pid: labels[pid],
    placeholder="Choose a profile",
)
with st.expander("Or start a new profile (for uploading your own documents)"):
    new_id = st.text_input("New profile ID", placeholder="e.g. demo-citizen-1")
    if st.button("Start new profile") and new_id.strip():
        st.session_state["profile_id"] = new_id.strip()
        st.session_state.pop("last_decision", None)
        st.rerun()

if choice and choice != current:
    st.session_state["profile_id"] = choice
    st.session_state.pop("last_decision", None)
    st.session_state.pop("last_upload", None)
    st.rerun()  # so the sidebar shows the new profile too

if not current:
    st.stop()

st.subheader(f"Profile {current}")
if current not in ids:
    st.info("This profile is new. Upload documents to build it.")
    st.page_link("pages/citizen_upload.py", label="Upload documents", icon="📄")
    st.stop()

detail = ui.call(api.profile, current)
for doc in detail["documents"]:
    flagged = sum(f["verification_status"] == "flagged" for f in doc["facts"])
    title = f"{ui.document_label(doc['document_type'])} — {len(doc['facts'])} facts" + (
        f", ⚠️ {flagged} flagged" if flagged else ", all verified")
    with st.expander(title, expanded=bool(flagged)):
        st.caption(ui.image_quality_line(doc["image_quality"]))
        ui.facts_table(doc["facts"])

col1, col2 = st.columns(2)
col1.page_link("pages/citizen_upload.py", label="Upload another document", icon="📄")
col2.page_link("pages/citizen_schemes.py", label="Apply for schemes", icon="🏛️")
