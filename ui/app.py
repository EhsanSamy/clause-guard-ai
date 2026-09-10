"""
Clause Guard AI — Streamlit UI.

Screens:

    1. Upload
        PDF drop zone, constraints, primary CTA.

    2. Processing
        Staged pipeline progress + case metadata + polling.

    3. Report
        Complete analysis OR incomplete-analysis state.

        Risk is shown only when a trustworthy assessment exists.

    4. Failed
        Plain-language backend/job failure + retry.

Sidebar:

    Case history + delete actions + new case.

Important status distinction:

    status
        Background job / persistence status.

    analysis_status
        Quality/completeness of the actual contract analysis.

    risk_assessment_available
        Whether a trustworthy overall risk score exists.

Run with:

    streamlit run ui/app.py
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any

import requests
import streamlit as st


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BACKEND_URL = os.environ.get(
    "BACKEND_URL",
    "http://localhost:8000",
)

POLL_SECONDS = 2.5

RISK_EMOJI = {
    "Low": "🟢",
    "Medium": "🟡",
    "High": "🟠",
    "Critical": "🔴",
    "Blocked": "⛔",
    "Invalid": "⚪",
}

RISK_ORDER = [
    "Critical",
    "High",
    "Medium",
    "Low",
    "Blocked",
    "Invalid",
    "Unknown",
]

PIPELINE_STEPS = [
    ("validate_input", "Validate input"),
    ("document_processing", "Process document"),
    ("classify_contract", "Classify contract"),
    ("extract_clauses", "Extract clauses"),
    ("guardrail_check", "Guardrail check"),
    ("retrieve_playbook_rules", "Retrieve playbook"),
    ("score_risk", "Score risk"),
    ("reflect", "Reflect"),
    ("decide_and_route", "Decide & route"),
]


st.set_page_config(
    page_title="Clause Guard AI",
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ---------------------------------------------------------------------------
# Session defaults
# ---------------------------------------------------------------------------

for key, default in {
    "case_id": None,
    "screen": "upload",
    "upload_error": None,
    "filter_score": "All",
    "filter_tag": "All",
    "filter_flagged_only": False,
    "delete_case_confirm": None,
    "delete_history_confirm": False,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _api_get(
    path: str,
    timeout: float = 5,
) -> tuple[Any | None, str | None]:
    """
    Send a GET request to the backend.
    """
    try:
        response = requests.get(
            f"{BACKEND_URL}{path}",
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json(), None
    except requests.RequestException as exc:
        return None, str(exc)


def _api_post(
    path: str,
    timeout: float = 30,
) -> tuple[Any | None, str | None]:
    """
    Send a POST request without a file.
    """
    try:
        response = requests.post(
            f"{BACKEND_URL}{path}",
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json(), None
    except requests.RequestException as exc:
        return None, str(exc)


def _api_post_file(
    path: str,
    filename: str,
    data: bytes,
) -> tuple[Any | None, str | None]:
    """
    Upload a PDF to the backend.
    """
    try:
        response = requests.post(
            f"{BACKEND_URL}{path}",
            files={
                "file": (
                    filename,
                    data,
                    "application/pdf",
                )
            },
            timeout=60,
        )
        response.raise_for_status()
        return response.json(), None
    except requests.RequestException as exc:
        return None, str(exc)


def _api_delete(
    path: str,
    timeout: float = 30,
) -> tuple[Any | None, str | None]:
    """
    Send a DELETE request to the backend.
    """
    try:
        response = requests.delete(
            f"{BACKEND_URL}{path}",
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json(), None
    except requests.RequestException as exc:
        return None, str(exc)


# ---------------------------------------------------------------------------
# General helpers
# ---------------------------------------------------------------------------

def _relative_time(
    iso: str | None,
) -> str:
    if not iso:
        return ""

    try:
        dt = datetime.fromisoformat(
            iso.replace("Z", "+00:00")
        )

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=timezone.utc
            )

        delta = (
            datetime.now(timezone.utc) - dt
        )

        secs = int(
            delta.total_seconds()
        )

        if secs < 60:
            return "just now"

        if secs < 3600:
            return f"{secs // 60} min ago"

        if secs < 86400:
            return f"{secs // 3600} h ago"

        return f"{secs // 86400} d ago"

    except (ValueError, TypeError):
        return ""


def _risk_badge(
    risk: str | None,
) -> str:
    risk = risk or "Unknown"

    return (
        f"{RISK_EMOJI.get(risk, '⚪')} "
        f"{risk}"
    )


def _analysis_badge(
    analysis_status: str | None,
) -> str:
    labels = {
        "completed": "✅ Complete",
        "partial": "⚠️ Incomplete",
        "failed": "❌ Failed",
        "blocked": "⛔ Blocked",
        "pending": "⏳ Pending",
        "running": "🔄 Running",
    }

    return labels.get(
        analysis_status or "",
        "—",
    )


def _friendly_error(
    raw: str | None,
) -> str:
    if not raw:
        return (
            "An unknown error occurred while "
            "processing this contract."
        )

    lower = raw.lower()

    if (
        "503" in raw
        or "unavailable" in lower
        or "high demand" in lower
    ):
        return (
            "The AI service is temporarily unavailable. "
            "Please wait a moment and try the analysis again."
        )

    if (
        "429" in raw
        or "resource_exhausted" in lower
        or "quota exceeded" in lower
    ):
        return (
            "The AI service has temporarily reached its "
            "usage limit. Please wait a moment and try again."
        )

    if (
        "api key" in lower
        or (
            "gemini" in lower
            and "not set" in lower
        )
    ):
        return (
            "The AI service is not configured correctly. "
            "Please check the server configuration."
        )

    if "timeout" in lower:
        return (
            "The analysis took too long to complete. "
            "Please try again with a shorter document if possible."
        )

    return (
        "The analysis could not be completed. "
        "Please try again."
    )


def _friendly_failed_step(
    record: dict[str, Any],
) -> str:
    failed_steps = (
        record.get("failed_steps")
        or []
    )

    if not failed_steps:
        return "One or more analysis steps did not complete."

    labels = {
        sid: label
        for sid, label in PIPELINE_STEPS
    }

    readable = [
        labels.get(
            step,
            step.replace("_", " ").title(),
        )
        for step in failed_steps
    ]

    return ", ".join(readable)


def _go(
    screen: str,
    case_id: str | None = None,
) -> None:
    st.session_state.screen = screen

    if case_id is not None:
        st.session_state.case_id = case_id

    st.rerun()


def _reset_filters() -> None:
    st.session_state.filter_score = "All"
    st.session_state.filter_tag = "All"
    st.session_state.filter_flagged_only = False


# ---------------------------------------------------------------------------
# Case actions
# ---------------------------------------------------------------------------

def _delete_case(
    case_id: str,
) -> None:
    """
    Delete one case through the backend.
    """
    result, err = _api_delete(
        f"/cases/{case_id}"
    )

    if err:
        st.error(
            f"Could not delete this case. {_friendly_error(err)}"
        )
        return

    # If the deleted case is currently open,
    # clear the active case and return to upload.
    if st.session_state.case_id == case_id:
        st.session_state.case_id = None
        st.session_state.screen = "upload"
        _reset_filters()

    st.session_state.delete_case_confirm = None
    st.toast(
        "Case deleted successfully.",
        icon="❌",
    )
    st.rerun()


def _delete_history() -> None:
    """
    Delete all cases through the backend.
    """
    result, err = _api_delete("/cases")

    if err:
        st.error(
            f"Could not clear case history. {_friendly_error(err)}"
        )
        return

    st.session_state.case_id = None
    st.session_state.screen = "upload"
    st.session_state.delete_history_confirm = False
    st.session_state.delete_case_confirm = None
    _reset_filters()

    deleted_count = (
        result.get("deleted_cases", 0)
        if isinstance(result, dict)
        else 0
    )

    st.toast(
        f"History cleared — {deleted_count} case(s) deleted.",
        icon="❌",
    )

    st.rerun()


def _retry_case(
    case_id: str,
) -> None:
    """
    Restart analysis for an existing case using its
    original uploaded PDF.
    """
    result, err = _api_post(
        f"/cases/{case_id}/retry",
        timeout=30,
    )

    if err:
        st.error(
            f"Could not restart the analysis. {_friendly_error(err)}"
        )
        return

    st.session_state.delete_case_confirm = None
    st.session_state.screen = "processing"
    st.session_state.case_id = case_id
    st.rerun()


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

def render_sidebar() -> None:
    with st.sidebar:
        st.markdown("### ⚖️ Clause Guard AI")
        st.caption("Contract risk review agent")

        st.divider()

        st.markdown("#### Case history")

        cases, err = _api_get("/cases")

        if err:
            st.warning(
                "Can't load history."
            )
            st.caption(
                _friendly_error(err)
            )

        elif not cases:
            st.caption(
                "No cases yet. Upload a contract to start."
            )

        else:
            for case in cases:
                cid = case.get("case_id") or ""
                fname = case.get("filename") or cid

                status = (
                    case.get("status")
                    or "unknown"
                )

                analysis_status = (
                    case.get("analysis_status")
                    or "pending"
                )

                risk = case.get(
                    "overall_risk"
                )

                risk_available = bool(
                    case.get(
                        "risk_assessment_available"
                    )
                )

                when = _relative_time(
                    case.get("created_at")
                )

                # Background-job status takes priority.
                if status == "processing":
                    prefix = "⏳"
                elif status == "failed":
                    prefix = "❌"
                elif not risk_available:
                    prefix = "⚠️"
                else:
                    prefix = RISK_EMOJI.get(
                        risk or "",
                        "📄",
                    )

                short = (
                    fname
                    if len(fname) <= 25
                    else fname[:22] + "…"
                )

                label = f"{prefix} {short}"

                if when:
                    label = (
                        f"{label}\n"
                        f"{when}"
                    )

                is_active = (
                    st.session_state.case_id
                    == cid
                )

                # Case row.
                case_col, delete_col = st.columns(
                    [5, 1],
                    gap="small",
                )

                with case_col:
                    if st.button(
                        label,
                        key=f"hist_{cid}",
                        use_container_width=True,
                        type=(
                            "primary"
                            if is_active
                            else "secondary"
                        ),
                    ):
                        if status == "processing":
                            _go(
                                "processing",
                                cid,
                            )
                        elif status == "failed":
                            _go(
                                "failed",
                                cid,
                            )
                        else:
                            _go(
                                "report",
                                cid,
                            )

                with delete_col:
                    if st.button(
                        "❌",
                        key=f"delete_{cid}",
                        help="Delete this case",
                    ):
                        st.session_state.delete_case_confirm = cid
                        st.session_state.delete_history_confirm = False
                        st.rerun()

                # Small status line below the case.
                if status != "processing":
                    st.caption(
                        _analysis_badge(
                            analysis_status
                        )
                    )

                # Confirmation for this specific case.
                if (
                    st.session_state.delete_case_confirm
                    == cid
                ):
                    st.warning(
                        "Delete this case permanently?"
                    )

                    confirm_col, cancel_col = st.columns(
                        2,
                        gap="small",
                    )

                    with confirm_col:
                        if st.button(
                            "Delete",
                            key=f"confirm_delete_{cid}",
                            type="primary",
                            use_container_width=True,
                        ):
                            _delete_case(cid)

                    with cancel_col:
                        if st.button(
                            "Cancel",
                            key=f"cancel_delete_{cid}",
                            use_container_width=True,
                        ):
                            st.session_state.delete_case_confirm = None
                            st.rerun()

        st.divider()

        # ---------------------------------------------------------------
        # Delete history
        # ---------------------------------------------------------------
        if cases:
            if st.session_state.delete_history_confirm:
                st.warning(
                    "Delete all case history permanently?"
                )

                confirm_col, cancel_col = st.columns(
                    2,
                    gap="small",
                )

                with confirm_col:
                    if st.button(
                        "Delete all",
                        key="confirm_delete_history",
                        type="primary",
                        use_container_width=True,
                    ):
                        _delete_history()

                with cancel_col:
                    if st.button(
                        "Cancel",
                        key="cancel_delete_history",
                        use_container_width=True,
                    ):
                        st.session_state.delete_history_confirm = False
                        st.rerun()

            else:
                if st.button(
                    "❌ Delete history",
                    key="delete_history",
                    use_container_width=True,
                ):
                    st.session_state.delete_history_confirm = True
                    st.session_state.delete_case_confirm = None
                    st.rerun()

        st.divider()

        if st.button(
            "＋ New case",
            use_container_width=True,
            type="primary",
        ):
            st.session_state.upload_error = None
            st.session_state.delete_case_confirm = None
            st.session_state.delete_history_confirm = False
            _reset_filters()

            _go(
                "upload",
                None,
            )


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

def render_upload_screen() -> None:
    st.markdown(
        "## 📄 Analyze a contract"
    )

    st.markdown(
        "Upload a commercial contract PDF. "
        "The agent extracts key clauses, scores them "
        "against the internal playbook, and flags "
        "items that need human review."
    )

    if st.session_state.upload_error:
        st.error(
            st.session_state.upload_error
        )
        st.session_state.upload_error = None

    col_main, col_side = st.columns(
        [2, 1]
    )

    with col_main:
        uploaded = st.file_uploader(
            "Contract PDF",
            type=["pdf"],
            help=(
                "PDF only. Typical commercial contracts "
                "work best (NDA, MSA, SOW)."
            ),
            label_visibility="collapsed",
        )

        st.caption(
            "Supported: PDF · One file per analysis · "
            "Processing usually takes 30–90 seconds"
        )

        analyze = st.button(
            "Analyze contract",
            type="primary",
            disabled=uploaded is None,
            use_container_width=False,
        )

        if analyze and uploaded is not None:
            with st.spinner(
                "Uploading…"
            ):
                result, err = _api_post_file(
                    "/cases",
                    filename=uploaded.name,
                    data=uploaded.getvalue(),
                )

            if err:
                st.session_state.upload_error = (
                    f"Upload failed: {err}"
                )
                st.rerun()

            else:
                _go(
                    "processing",
                    result["case_id"],
                )

    with col_side:
        st.info(
            "**What happens next**\n\n"
            "1. Validate & extract text\n"
            "2. Classify contract type\n"
            "3. Extract & tag clauses\n"
            "4. Retrieve playbook rules\n"
            "5. Score risk\n"
            "6. Review guardrails\n"
            "7. Produce the report"
        )

        st.caption(
            "Not legal advice. Outputs are decision-support only."
        )


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------

def render_processing_screen() -> None:
    case_id = st.session_state.case_id

    if not case_id:
        _go(
            "upload",
            None,
        )

    st.markdown(
        "## ⏳ Analyzing your contract"
    )

    record, err = _api_get(
        f"/cases/{case_id}"
    )

    if err:
        st.error(
            "Lost connection to the backend."
        )

        st.caption(
            _friendly_error(err)
        )

        if st.button(
            "Back to upload"
        ):
            _go(
                "upload",
                None,
            )

        return

    filename = (
        record.get("filename")
        or case_id
    )

    status = record.get(
        "status"
    )

    current_step = record.get(
        "current_step"
    )

    completed_steps = set(
        record.get(
            "completed_steps"
        )
        or []
    )

    failed_steps = set(
        record.get(
            "failed_steps"
        )
        or []
    )

    created = record.get(
        "created_at"
    )

    meta1, meta2, meta3 = st.columns(
        3
    )

    meta1.metric(
        "File",
        (
            filename
            if len(filename) < 40
            else filename[:37] + "…"
        ),
    )

    meta2.metric(
        "Case ID",
        case_id,
    )

    meta3.metric(
        "Started",
        _relative_time(created)
        or "—",
    )

    st.divider()

    st.markdown(
        "#### Pipeline progress"
    )

    # Use actual completed/failed/current step
    # information from the backend.
    for step_id, label in PIPELINE_STEPS:
        if step_id in failed_steps:
            st.error(
                f"❌ **{label}** — needs attention"
            )

        elif step_id in completed_steps:
            st.success(
                f"✓ **{label}**"
            )

        elif step_id == current_step:
            st.info(
                f"🔄 **{label}** — in progress"
            )

        else:
            st.caption(
                f"○ {label}"
            )

    st.divider()

    if status == "processing":
        current_label = next(
            (
                label
                for sid, label
                in PIPELINE_STEPS
                if sid == current_step
            ),
            "Processing your contract",
        )

        st.info(
            f"**Current step:** {current_label}"
        )

        time.sleep(
            POLL_SECONDS
        )

        st.rerun()

    elif status == "completed":
        # Background job finished.
        #
        # This may still be a partial analysis,
        # so the report screen decides how to render it.
        _go(
            "report",
            case_id,
        )

    elif status == "failed":
        _go(
            "failed",
            case_id,
        )

    else:
        st.warning(
            f"Unexpected status: {status!r}"
        )

        time.sleep(
            POLL_SECONDS
        )

        st.rerun()


# ---------------------------------------------------------------------------
# Failed
# ---------------------------------------------------------------------------

def render_failed_screen() -> None:
    case_id = st.session_state.case_id

    record, err = _api_get(
        f"/cases/{case_id}"
    )

    if err:
        st.error(
            "Can't load this case."
        )

        st.caption(
            _friendly_error(err)
        )

        if st.button(
            "Back to upload"
        ):
            _go(
                "upload",
                None,
            )

        return

    filename = (
        record.get("filename")
        or case_id
    )

    st.markdown(
        f"## ❌ Analysis failed — {filename}"
    )

    st.error(
        _friendly_error(
            record.get("error")
        )
    )

    failed_step = _friendly_failed_step(
        record
    )

    st.info(
        f"**Step requiring attention:** "
        f"{failed_step}"
    )

    # Technical details are deliberately hidden.
    # This keeps the user-facing experience clean while
    # still allowing developers/reviewers to debug.
    with st.expander(
        "Technical details"
    ):
        st.code(
            record.get("error")
            or "No error message stored.",
            language="text",
        )

        st.json(
            {
                "case_id": case_id,
                "status": record.get("status"),
                "analysis_status": record.get(
                    "analysis_status"
                ),
                "failed_steps": record.get(
                    "failed_steps",
                    [],
                ),
                "created_at": record.get(
                    "created_at"
                ),
                "updated_at": record.get(
                    "updated_at"
                ),
            }
        )

    c1, c2 = st.columns(2)

    with c1:
        if st.button(
            "↻ Try again",
            type="primary",
            use_container_width=True,
        ):
            _retry_case(case_id)

    with c2:
        st.download_button(
            "Download case JSON",
            data=json.dumps(
                record,
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            file_name=f"{case_id}_failed.json",
            mime="application/json",
            use_container_width=True,
        )


# ---------------------------------------------------------------------------
# Incomplete analysis
# ---------------------------------------------------------------------------

def render_incomplete_analysis(
    record: dict[str, Any],
) -> None:
    """
    User-facing state for partial/incomplete analysis.

    This is intentionally NOT rendered as a risk card.
    """

    clauses = record.get(
        "clauses"
    ) or []

    failed_steps = record.get(
        "failed_steps"
    ) or []

    st.warning(
        "### ⚠️ Assessment unavailable\n\n"
        "We couldn't complete the contract analysis, "
        "so a reliable overall risk level is not available."
    )

    if clauses:
        st.info(
            f"{len(clauses)} clause(s) were identified, "
            "but the risk assessment could not be "
            "completed reliably."
        )

    else:
        st.info(
            "No clauses were available for risk assessment. "
            "This does **not** mean the contract has no "
            "risky clauses."
        )

    if failed_steps:
        labels = {
            sid: label
            for sid, label in PIPELINE_STEPS
        }

        readable_steps = [
            labels.get(
                step,
                step.replace(
                    "_",
                    " ",
                ).title(),
            )
            for step in failed_steps
        ]

        st.caption(
            "Step requiring attention: "
            + ", ".join(readable_steps)
        )

    st.markdown(
        """
        **What to do next**

        - Retry the analysis.
        - If the issue continues, have a reviewer inspect the case.
        - Do not interpret this result as Low, Medium, High, or Critical risk.
        """
    )


# ---------------------------------------------------------------------------
# Complete report
# ---------------------------------------------------------------------------

def _render_complete_report(
    record: dict[str, Any],
) -> None:
    case_id = record.get(
        "case_id"
    )

    filename = (
        record.get("filename")
        or case_id
    )

    risk = record.get(
        "overall_risk"
    )

    human = bool(
        record.get(
            "human_review_required"
        )
    )

    clauses = record.get(
        "clauses"
    ) or []

    markdown_report = record.get(
        "final_report_markdown"
    )

    contract_type = record.get(
        "contract_type"
    )

    route = record.get(
        "route"
    )

    # ---------------------------------------------------------
    # Header / hero
    # ---------------------------------------------------------
    st.markdown(
        f"## 📋 Report — {filename}"
    )

    h1, h2, h3, h4 = st.columns(
        [2, 1, 1, 1]
    )

    with h1:
        st.markdown(
            f"### {_risk_badge(risk)}"
        )

        if human:
            st.warning(
                "**Human review required** "
                "before relying on this assessment."
            )

    with h2:
        st.metric(
            "Clauses",
            len(clauses),
        )

    with h3:
        flagged_n = sum(
            1
            for clause in clauses
            if clause.get("flagged")
        )

        st.metric(
            "Flagged",
            flagged_n,
        )

    with h4:
        st.metric(
            "Type",
            contract_type or "—",
        )

    if route:
        st.caption(
            f"Routing decision: `{route}` · "
            f"Case `{case_id}` · "
            f"{_relative_time(record.get('created_at'))}"
        )

    # ---------------------------------------------------------
    # Clause breakdown
    # ---------------------------------------------------------
    st.markdown(
        "#### Clause breakdown"
    )

    if not clauses:
        st.info(
            "The analysis completed without any "
            "clauses being flagged for review."
        )

    else:
        tags = sorted(
            {
                clause.get("tag")
                or "other"
                for clause in clauses
            }
        )

        scores = sorted(
            {
                clause.get("score")
                or "Unknown"
                for clause in clauses
            },
            key=lambda score: (
                RISK_ORDER.index(score)
                if score in RISK_ORDER
                else 99
            ),
        )

        f1, f2, f3 = st.columns(
            [1, 1, 1]
        )

        with f1:
            score_filter = st.selectbox(
                "Score",
                ["All", *scores],
                key="filter_score",
            )

        with f2:
            tag_filter = st.selectbox(
                "Tag",
                ["All", *tags],
                key="filter_tag",
            )

        with f3:
            flagged_only = st.checkbox(
                "Flagged only",
                key="filter_flagged_only",
            )

        filtered = []

        for clause in clauses:
            score = (
                clause.get("score")
                or "Unknown"
            )

            tag = (
                clause.get("tag")
                or "other"
            )

            if (
                score_filter != "All"
                and score != score_filter
            ):
                continue

            if (
                tag_filter != "All"
                and tag != tag_filter
            ):
                continue

            if (
                flagged_only
                and not clause.get("flagged")
            ):
                continue

            filtered.append(
                clause
            )

        def _sort_key(
            clause: dict,
        ) -> tuple:
            score = (
                clause.get("score")
                or "Unknown"
            )

            order = (
                RISK_ORDER.index(score)
                if score in RISK_ORDER
                else 99
            )

            return (
                order,
                0 if clause.get("flagged") else 1,
                clause.get("tag") or "",
            )

        filtered.sort(
            key=_sort_key
        )

        st.caption(
            f"Showing {len(filtered)} "
            f"of {len(clauses)} clauses"
        )

        table_rows = [
            {
                "Tag": clause.get("tag")
                or "—",

                "Score": clause.get("score")
                or "—",

                "Flagged": (
                    "🚩"
                    if clause.get("flagged")
                    else ""
                ),

                "Rationale": (
                    clause.get("rationale")
                    or ""
                ),
            }
            for clause in filtered
        ]

        st.dataframe(
            table_rows,
            use_container_width=True,
            hide_index=True,
        )

        with st.expander(
            "Full rationale per clause"
        ):
            for clause in filtered:
                badge = RISK_EMOJI.get(
                    clause.get("score")
                    or "",
                    "⚪",
                )

                flag = (
                    " 🚩"
                    if clause.get("flagged")
                    else ""
                )

                st.markdown(
                    f"**{badge} "
                    f"`{clause.get('tag')}` — "
                    f"{clause.get('score')}"
                    f"{flag}**"
                )

                st.write(
                    clause.get("rationale")
                    or "_No rationale provided._"
                )

                st.divider()

    # ---------------------------------------------------------
    # Generated markdown
    # ---------------------------------------------------------
    if markdown_report:
        st.markdown(
            "#### Generated report"
        )

        with st.expander(
            "View generated report",
            expanded=False,
        ):
            st.markdown(
                markdown_report
            )

    # ---------------------------------------------------------
    # Actions
    # ---------------------------------------------------------
    st.divider()

    a1, a2, a3 = st.columns(3)

    with a1:
        st.download_button(
            "⬇ Download JSON",
            data=json.dumps(
                record,
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            file_name=f"{case_id}_report.json",
            mime="application/json",
            use_container_width=True,
        )

    with a2:
        md_body = (
            markdown_report
            or f"# Report — {filename}\n\n"
            f"**Overall risk:** {risk}\n\n"
            f"**Human review:** "
            f"{'Yes' if human else 'No'}"
        )

        st.download_button(
            "⬇ Download Markdown",
            data=md_body,
            file_name=f"{case_id}_report.md",
            mime="text/markdown",
            use_container_width=True,
        )

    with a3:
        if st.button(
            "＋ Analyze another",
            type="primary",
            use_container_width=True,
        ):
            _go(
                "upload",
                None,
            )


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def render_report_screen() -> None:
    case_id = st.session_state.case_id

    if not case_id:
        _go(
            "upload",
            None,
        )

    record, err = _api_get(
        f"/cases/{case_id}"
    )

    if err:
        st.error(
            "Can't load this case."
        )

        st.caption(
            _friendly_error(err)
        )

        if st.button(
            "Back to upload"
        ):
            _go(
                "upload",
                None,
            )

        return

    analysis_status = record.get(
        "analysis_status"
    )

    risk_available = bool(
        record.get(
            "risk_assessment_available"
        )
    )

    # ---------------------------------------------------------
    # Failed background job
    # ---------------------------------------------------------
    if record.get("status") == "failed":
        render_failed_screen()
        return

    # ---------------------------------------------------------
    # Incomplete / partial / blocked analysis
    # ---------------------------------------------------------
    if (
        analysis_status in {
            "partial",
            "failed",
            "blocked",
        }
        or not risk_available
    ):
        filename = (
            record.get("filename")
            or case_id
        )

        st.markdown(
            f"## 📋 Analysis — {filename}"
        )

        st.caption(
            f"Case `{case_id}` · "
            f"{_analysis_badge(analysis_status)}"
        )

        if analysis_status == "blocked":
            st.error(
                "This request was blocked by the system "
                "guardrails before a risk assessment could "
                "be produced."
            )

        else:
            render_incomplete_analysis(
                record
            )

        # Keep the raw generated report secondary,
        # but only if one exists.
        markdown_report = record.get(
            "final_report_markdown"
        )

        if markdown_report:
            with st.expander(
                "View generated case report"
            ):
                st.markdown(
                    markdown_report
                )

        st.divider()

        c1, c2 = st.columns(2)

        with c1:
            # A blocked case should not be blindly retried.
            if analysis_status != "blocked":
                if st.button(
                    "↻ Try again",
                    type="primary",
                    use_container_width=True,
                ):
                    _retry_case(case_id)
            else:
                if st.button(
                    "＋ Analyze another",
                    type="primary",
                    use_container_width=True,
                ):
                    _go(
                        "upload",
                        None,
                    )

        with c2:
            st.download_button(
                "⬇ Download case JSON",
                data=json.dumps(
                    record,
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                ),
                file_name=(
                    f"{case_id}_case.json"
                ),
                mime="application/json",
                use_container_width=True,
            )

        return

    # ---------------------------------------------------------
    # Complete analysis
    # ---------------------------------------------------------
    _render_complete_report(
        record
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

render_sidebar()

screen = st.session_state.screen

if screen == "upload":
    render_upload_screen()

elif screen == "processing":
    render_processing_screen()

elif screen == "report":
    render_report_screen()

elif screen == "failed":
    render_failed_screen()

else:
    _go(
        "upload",
        None,
    )
