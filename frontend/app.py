"""VerifyOnce Streamlit app: `streamlit run frontend/app.py` (the API must be running).

Each page draws the banner and status strip itself (ui.page_chrome), so they appear on
every page however it is reached.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import logging  # noqa: E402

import streamlit as st  # noqa: E402

st.set_page_config(page_title="VerifyOnce", page_icon="🪪", layout="wide")

with st.sidebar:
    st.text_input("Officer name (for officer actions)", key="officer_name", value="Demo Officer")
    if st.session_state.get("profile_id"):
        st.caption(f"Citizen profile: **{st.session_state['profile_id']}**")

navigation = st.navigation({
    "Citizen": [
        st.Page("pages/citizen_profile.py", title="Choose profile", icon="👤", default=True),
        st.Page("pages/citizen_upload.py", title="Upload documents", icon="📄"),
        st.Page("pages/citizen_schemes.py", title="Apply for schemes", icon="🏛️"),
    ],
    "Officer": [
        st.Page("pages/officer_queue.py", title="Review queue", icon="📥"),
        st.Page("pages/officer_case.py", title="Case view", icon="🔎"),
    ],
})
try:
    navigation.run()
except Exception:  # st.stop / st.rerun derive from BaseException and pass through
    logging.getLogger("verifyonce.ui").exception("Page error")
    st.error("Something went wrong on this page. Try again; if it keeps happening, check the API window. "
             "(Details are in the Streamlit console, never shown here.)")
