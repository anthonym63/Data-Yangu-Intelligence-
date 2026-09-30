"""File upload widget. Accepts .csv and .xlsx, validates before handing off."""

from typing import Any

import streamlit as st

ACCEPTED_EXTENSIONS = ["csv", "xlsx"]
MAX_MB = 200


def render() -> Any | None:
    """Render the uploader and return a validated file buffer, or None.

    Errors render inline rather than raising, so a bad file never takes the
    Streamlit runtime down mid-session.
    """
    uploaded = st.file_uploader(
        "Upload a spreadsheet",
        type=ACCEPTED_EXTENSIONS,
        help="CSV or Excel. Your data is processed locally and never sent to the AI.",
    )

    if uploaded is None:
        return None

    extension = uploaded.name.rsplit(".", 1)[-1].lower() if "." in uploaded.name else ""
    if extension not in ACCEPTED_EXTENSIONS:
        st.error(f"Unsupported file type '.{extension}'. Upload a .csv or .xlsx file.")
        return None

    size = getattr(uploaded, "size", None)
    if size == 0:
        st.error("That file is empty. Check the export and try again.")
        return None
    if size and size > MAX_MB * 1024 * 1024:
        st.error(f"File is larger than {MAX_MB} MB. Split it and upload in parts.")
        return None

    return uploaded
