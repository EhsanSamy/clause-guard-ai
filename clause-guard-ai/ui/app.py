"""
Clause Guard AI — Streamlit UI.

Screens:
    1. Upload        — upload a PDF contract
    2. Processing     — polls the backend every ~2.5s until the case finishes
    3. Report          — risk badge, human-review banner, clause table, download
    Sidebar: History  — past cases, click to reopen their report

Run with:
    streamlit run ui/app.py
"""
from __future__ import annotations

import json
import os
import time

import requests
import streamlit as st

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")

RISK_COLORS = {
    "Low": "🟢",
    "Medium": "🟡",
    "High": "🟠",
    "Critical": "🔴",
}

st.set_page_config(page_title="Clause Guard AI", layout="wide")

if "case_id" not in st.session_state:
    st.session_state.case_id = None
if "screen" not in st.session_state:
    st.session_state.screen = "upload"


# ---------------------------------------------------------------- sidebar --
def render_history_sidebar() -> None:
    st.sidebar.header("Case history")
    try:
        cases = requests.get(f"{BACKEND_URL}/cases", timeout=5).json()
    except requests.RequestException:
        st.sidebar.warning("Can't reach the backend.")
        return

    if not cases:
        st.sidebar.caption("No cases yet.")
        return

    for case in cases:
        badge = RISK_COLORS.get(case.get("overall_risk"), "⚪")
        label = f"{badge} {case.get('filename', case['case_id'])}"
        if st.sidebar.button(label, key=f"hist_{case['case_id']}"):
            st.session_state.case_id = case["case_id"]
            st.session_state.screen = "report"
            st.rerun()

    if st.sidebar.button("➕ New case"):
        st.session_state.case_id = None
        st.session_state.screen = "upload"
        st.rerun()


# ----------------------------------------------------------- upload screen --
def render_upload_screen() -> None:
    st.title("📄 Clause Guard AI")
    st.write("Upload a contract PDF to get a clause-by-clause risk review.")

    uploaded = st.file_uploader("Contract PDF", type=["pdf"])
    if uploaded and st.button("Analyze contract", type="primary"):
        try:
            response = requests.post(
                f"{BACKEND_URL}/cases",
                files={"file": (uploaded.name, uploaded.getvalue(), "application/pdf")},
                timeout=30,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            st.error(f"Upload failed: {exc}")
            return

        st.session_state.case_id = response.json()["case_id"]
        st.session_state.screen = "processing"
        st.rerun()


# -------------------------------------------------------- processing screen --
def render_processing_screen() -> None:
    st.title("⏳ Analyzing your contract…")
    case_id = st.session_state.case_id

    try:
        record = requests.get(f"{BACKEND_URL}/cases/{case_id}", timeout=5).json()
    except requests.RequestException as exc:
        st.error(f"Lost connection to the backend: {exc}")
        return

    status = record.get("status")

    if status == "processing":
        with st.spinner("Extracting clauses, scoring risk, checking the playbook…"):
            time.sleep(2.5)
        st.rerun()
    elif status == "completed":
        st.session_state.screen = "report"
        st.rerun()
    elif status == "failed":
        st.error("Something went wrong while processing this contract.")
        st.code(record.get("error", "Unknown error"))
        if st.button("⬅ Back to upload"):
            st.session_state.case_id = None
            st.session_state.screen = "upload"
            st.rerun()
    else:
        st.warning(f"Unexpected status: {status!r}")


# ------------------------------------------------------------- report screen --
def render_report_screen() -> None:
    case_id = st.session_state.case_id
    try:
        record = requests.get(f"{BACKEND_URL}/cases/{case_id}", timeout=5).json()
    except requests.RequestException as exc:
        st.error(f"Can't load this case: {exc}")
        return

    st.title(f"📋 Report — {record.get('filename', case_id)}")

    risk = record.get("overall_risk", "Unknown")
    badge = RISK_COLORS.get(risk, "⚪")
    st.subheader(f"{badge} Overall risk: {risk}")

    if record.get("human_review_required"):
        st.warning("🚩 Human review required for this case.")

    notices = record.get("guardrail_notices") or []
    for notice in notices:
        st.info(f"🛡️ {notice}")

    clauses = record.get("clauses") or []
    if clauses:
        st.subheader("Clause breakdown")
        st.dataframe(
            [
                {
                    "Tag": c.get("tag"),
                    "Score": c.get("score"),
                    "Flagged": "🚩" if c.get("flagged") else "",
                    "Rationale": c.get("rationale"),
                }
                for c in clauses
            ],
            use_container_width=True,
        )
    else:
        st.caption("No clause-level detail in this record yet.")

    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            "⬇ Download report (JSON)",
            data=json.dumps(record, ensure_ascii=False, indent=2),
            file_name=f"{case_id}_report.json",
            mime="application/json",
        )
    with col2:
        if st.button("➕ Analyze another contract"):
            st.session_state.case_id = None
            st.session_state.screen = "upload"
            st.rerun()


# --------------------------------------------------------------------- main --
render_history_sidebar()

screen = st.session_state.screen
if screen == "upload":
    render_upload_screen()
elif screen == "processing":
    render_processing_screen()
elif screen == "report":
    render_report_screen()
