"""Citizen: upload a document and see what was read, how sure, and why anything is flagged."""

import streamlit as st

from frontend import api, ui

DOCUMENT_TYPES = ["identity_card", "income_certificate", "marksheet"]
MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp", "gif": "image/gif"}

ui.page_chrome()
st.title("📄 Upload documents")
profile_id = ui.require_profile()
st.write(f"Uploading to profile **{profile_id}**. Each document is read once and reused for every scheme.")

with st.form("upload", clear_on_submit=False):
    document_type = st.selectbox("Document type", DOCUMENT_TYPES, format_func=ui.document_label)
    file = st.file_uploader("Document image", type=list(MIME))
    submitted = st.form_submit_button("Upload and read", type="primary")

if submitted:
    if file is None:
        st.warning("Choose an image file first.")
    else:
        extension = file.name.rsplit(".", 1)[-1].lower()
        with st.spinner("Reading the document. Live extraction can take a few seconds, longer if the model is busy."):
            st.session_state["last_upload"] = ui.call(
                api.upload, profile_id, document_type, file.name, file.getvalue(), MIME.get(extension, "image/png"))

result = st.session_state.get("last_upload")
if result and result["profile_id"] == profile_id:
    st.subheader(f"{ui.document_label(result['document_type'])}: {result['filename']}")
    status = result["status"]
    if status == "verified":
        st.success("Every field was read and verified.")
    elif status == "flagged":
        st.warning("Some fields are flagged. An officer will check them. The reasons are listed below.")
    else:
        st.error("The document could not be read, so every field goes to an officer.")

    quality = result.get("image_quality")
    (st.caption if quality and quality.get("ok") else st.warning)(ui.image_quality_line(quality))
    ext = result["extraction"]
    st.caption(f"Extraction: **{ext['extraction_source']}** · provider {ext['provider']} · model {ext['model']} · "
               f"{ext['attempts']} call(s)")
    if ext["error"]:
        st.error(f"Extraction error: {ext['error']}")
    ui.facts_table(result["facts"])
    for change in result["other_facts_changed"]:
        st.info(f"This upload changed an earlier fact: {ui.field_label(change['field'])} is now "
                f"{change['to']} ({'; '.join(change['reasons']) or 'checks passed'}).")

st.divider()
st.page_link("pages/citizen_schemes.py", label="Next: apply for schemes", icon="🏛️")
