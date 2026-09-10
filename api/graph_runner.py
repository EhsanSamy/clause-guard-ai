"""
Adapter between the FastAPI backend and the LangGraph pipeline
in agent/graph.py.

Contract with api/main.py:

    run_case(case_id: str, contract_text: str) -> dict

Returned dict is merged into the case JSON and contains:

    analysis_status,
    current_step,
    completed_steps,
    failed_steps,
    risk_assessment_available,
    overall_risk,
    human_review_required,
    clauses,
    guardrail_notices,
    final_report_markdown,
    contract_type,
    route,
    revised

Live progress:

    While the graph streams, each completed node name is written
    to case_store via update_step() so the UI can highlight progress.
"""

from __future__ import annotations

from typing import Any

from agent.graph import build_graph
from agent.state import initial_state
from api import case_store


# Risk levels that require a human to sign off.
_MANDATORY_REVIEW_SCORES = {"High", "Critical"}


# Node names as registered in agent/graph.py.
# These must match the UI pipeline steps.
_KNOWN_STEPS = {
    "validate_input",
    "document_processing",
    "classify_contract",
    "extract_clauses",
    "guardrail_check",
    "retrieve_playbook_rules",
    "score_risk",
    "reflect",
    "decide_and_route",
}


_graph = None  # Built lazily once per process, then reused.


def _get_graph():
    """Build and cache the LangGraph instance."""

    global _graph

    if _graph is None:
        _graph = build_graph()

    return _graph


def run_case(
    case_id: str,
    contract_text: str,
) -> dict[str, Any]:
    """
    Run one contract through the LangGraph pipeline.

    The graph is streamed node-by-node so the API/UI can show
    live progress.
    """

    graph = _get_graph()

    state = initial_state(contract_text)

    # Keep FastAPI case_id and graph state in sync.
    state["case_id"] = case_id

    # Initial progress state.
    state["analysis_status"] = "running"
    state["current_step"] = "validate_input"

    case_store.update_step(
        case_id,
        "validate_input",
    )

    final_state: dict[str, Any] = dict(state)

    try:
        # Stream node-by-node so the UI can show live progress.
        #
        # stream_mode="updates" yields:
        #
        # {
        #     "node_name": state_delta
        # }
        #
        # for each completed node.
        for event in graph.stream(
            state,
            stream_mode="updates",
        ):
            if not isinstance(event, dict):
                continue

            for node_name, delta in event.items():

                if node_name in _KNOWN_STEPS:
                    case_store.update_step(
                        case_id,
                        node_name,
                    )

                if isinstance(delta, dict):
                    final_state.update(delta)

                    # Keep progress information synchronized.
                    completed_steps = list(
                        final_state.get("completed_steps") or []
                    )

                    if (
                        node_name in _KNOWN_STEPS
                        and node_name not in completed_steps
                    ):
                        completed_steps.append(node_name)

                    final_state["completed_steps"] = completed_steps
                    final_state["current_step"] = node_name

    except TypeError:
        # Compatibility fallback for older LangGraph versions
        # that don't support stream_mode="updates".
        final_state = graph.invoke(state)

        # In fallback mode we cannot reliably know every completed
        # node, so mark the final graph node as completed.
        final_state["current_step"] = "decide_and_route"

        completed_steps = list(
            final_state.get("completed_steps") or []
        )

        if "decide_and_route" not in completed_steps:
            completed_steps.append("decide_and_route")

        final_state["completed_steps"] = completed_steps

        case_store.update_step(
            case_id,
            "decide_and_route",
        )

    # Determine final execution status.
    final_state = _finalize_analysis_status(final_state)

    return _to_case_record(final_state)


# ---------------------------------------------------------------------------
# Final analysis status
# ---------------------------------------------------------------------------

def _finalize_analysis_status(
    final_state: dict[str, Any],
) -> dict[str, Any]:
    """
    Determine whether the analysis completed successfully,
    partially completed, failed, or was blocked.

    A valid risk assessment requires:
        - at least one clause
        - every clause successfully scored
        - an overall risk score
        - no scoring failure

    Most importantly, a missing or incomplete risk assessment
    must NEVER be represented as a valid risk score.
    """

    # -----------------------------------------------------------------------
    # Invalid input
    # -----------------------------------------------------------------------

    if not final_state.get("input_valid", True):
        final_state["analysis_status"] = "failed"
        final_state["risk_assessment_available"] = False
        final_state["overall_risk_score"] = None

        return final_state

    # -----------------------------------------------------------------------
    # Explicitly blocked by guardrails
    # -----------------------------------------------------------------------

    if final_state.get("blocked"):
        final_state["analysis_status"] = "blocked"
        final_state["risk_assessment_available"] = False
        final_state["overall_risk_score"] = None

        return final_state

    # -----------------------------------------------------------------------
    # Collect scoring state
    # -----------------------------------------------------------------------

    clauses = final_state.get("clauses") or []

    tool_errors = list(
        final_state.get("tool_errors") or []
    )

    failed_steps = list(
        final_state.get("failed_steps") or []
    )

    overall_risk = final_state.get("overall_risk_score")

    # A clause is considered scored only when it has a real
    # RiskScore value.
    scored_clauses = [
        clause
        for clause in clauses
        if _clause_has_risk_score(clause)
    ]

    all_clauses_scored = (
        len(clauses) > 0
        and len(scored_clauses) == len(clauses)
    )

    score_risk_failed = "score_risk" in failed_steps

    # -----------------------------------------------------------------------
    # Determine whether the risk assessment is actually valid
    # -----------------------------------------------------------------------

    risk_available = (
        len(clauses) > 0
        and all_clauses_scored
        and overall_risk is not None
        and not score_risk_failed
    )

    final_state["risk_assessment_available"] = risk_available

    # -----------------------------------------------------------------------
    # Valid assessment
    # -----------------------------------------------------------------------

    if risk_available:

        if failed_steps or tool_errors:
            final_state["analysis_status"] = "partial"
        else:
            final_state["analysis_status"] = "completed"

        return final_state

    # -----------------------------------------------------------------------
    # No valid assessment
    # -----------------------------------------------------------------------

    # IMPORTANT:
    # Never leave a stale/generated overall risk behind when scoring
    # did not actually complete.
    final_state["overall_risk_score"] = None

    if (
        tool_errors
        or failed_steps
        or clauses
    ):
        final_state["analysis_status"] = "partial"

    else:
        final_state["analysis_status"] = "failed"

    return final_state


def _clause_has_risk_score(clause: Any) -> bool:
    """
    Return True only when a clause contains an actual risk score.

    Supports both:
        - Clause Pydantic models
        - plain dictionaries
    """

    if isinstance(clause, dict):
        return clause.get("risk_score") is not None

    return getattr(clause, "risk_score", None) is not None


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _enum_value(value: Any) -> Any:
    """
    Convert Enum values to their underlying value.

    Plain strings and None pass through unchanged.
    """

    return getattr(value, "value", value)


def _clause_to_dict(
    clause: Any,
) -> dict[str, Any]:
    """
    Convert a Clause Pydantic model or plain dictionary
    into the simplified shape consumed by the API/UI.
    """

    if isinstance(clause, dict):
        get = clause.get

    else:

        def get(
            key: str,
            default: Any = None,
        ) -> Any:
            return getattr(
                clause,
                key,
                default,
            )

    return {
        "tag": _enum_value(
            get("clause_tag")
        ),
        "score": _enum_value(
            get("risk_score")
        ),
        "rationale": get(
            "risk_rationale"
        ),
        "flagged": bool(
            get(
                "flagged_for_review",
                False,
            )
        ),
    }


# ---------------------------------------------------------------------------
# Case record conversion
# ---------------------------------------------------------------------------

def _to_case_record(
    final_state: dict[str, Any],
) -> dict[str, Any]:
    """
    Map GraphState fields to the case-record shape used by
    case_store and the Streamlit UI.
    """

    analysis_status = final_state.get(
        "analysis_status",
        "failed",
    )

    current_step = final_state.get(
        "current_step"
    )

    completed_steps = list(
        final_state.get("completed_steps") or []
    )

    failed_steps = list(
        final_state.get("failed_steps") or []
    )

    risk_assessment_available = bool(
        final_state.get(
            "risk_assessment_available",
            False,
        )
    )

    # -----------------------------------------------------------------------
    # Hard stop: invalid input
    # -----------------------------------------------------------------------

    if not final_state.get(
        "input_valid",
        True,
    ):
        notice = (
            final_state.get("input_error")
            or "The provided contract could not be processed."
        )

        return {
            "analysis_status": "failed",
            "current_step": current_step,
            "completed_steps": completed_steps,
            "failed_steps": failed_steps,
            "risk_assessment_available": False,
            "overall_risk": None,
            "human_review_required": False,
            "clauses": [],
            "guardrail_notices": [notice],
            "final_report_markdown": final_state.get(
                "final_report"
            ),
            "contract_type": final_state.get(
                "contract_type"
            ),
            "revised": final_state.get(
                "revised",
                False,
            ),
            "route": final_state.get(
                "route"
            ),
        }

    # -----------------------------------------------------------------------
    # Hard stop: guardrails
    # -----------------------------------------------------------------------

    if final_state.get("blocked"):
        notices = list(
            final_state.get("guardrail_flags") or []
        )

        if final_state.get("block_reason"):
            notices.append(
                final_state["block_reason"]
            )

        return {
            "analysis_status": "blocked",
            "current_step": current_step,
            "completed_steps": completed_steps,
            "failed_steps": failed_steps,
            "risk_assessment_available": False,
            "overall_risk": None,
            "human_review_required": True,
            "clauses": [],
            "guardrail_notices": notices,
            "final_report_markdown": final_state.get(
                "final_report"
            ),
            "contract_type": final_state.get(
                "contract_type"
            ),
            "revised": final_state.get(
                "revised",
                False,
            ),
            "route": final_state.get(
                "route"
            ),
        }

    # -----------------------------------------------------------------------
    # Notices
    # -----------------------------------------------------------------------

    guardrail_notices: list[str] = list(
        final_state.get("guardrail_flags") or []
    )

    guardrail_notices.extend(
        final_state.get("tool_errors") or []
    )

    # -----------------------------------------------------------------------
    # Clauses
    # -----------------------------------------------------------------------

    clauses = [
        _clause_to_dict(clause)
        for clause in (
            final_state.get("clauses") or []
        )
    ]

    # -----------------------------------------------------------------------
    # Risk assessment
    # -----------------------------------------------------------------------

    overall_risk = _enum_value(
        final_state.get(
            "overall_risk_score"
        )
    )

    # IMPORTANT:
    #
    # Never expose an overall risk score if the complete
    # risk assessment was not actually produced.
    #
    # Example that must NEVER happen:
    #
    #     clauses = 0
    #     risk = Medium
    #
    # or:
    #
    #     clauses = 5
    #     only 2 scored
    #     risk = Medium
    #
    if not risk_assessment_available:
        overall_risk = None

    human_review_required = (
        overall_risk in _MANDATORY_REVIEW_SCORES
    )

    # If scoring is incomplete, manual review is required,
    # but we do not pretend that the contract has a risk level.
    if not risk_assessment_available and clauses:
        human_review_required = True

    # -----------------------------------------------------------------------
    # Final API record
    # -----------------------------------------------------------------------

    return {
        "analysis_status": analysis_status,
        "current_step": current_step,
        "completed_steps": completed_steps,
        "failed_steps": failed_steps,
        "risk_assessment_available": (
            risk_assessment_available
        ),
        "overall_risk": overall_risk,
        "human_review_required": human_review_required,
        "clauses": clauses,
        "guardrail_notices": guardrail_notices,
        "final_report_markdown": final_state.get(
            "final_report"
        ),
        "contract_type": final_state.get(
            "contract_type"
        ),
        "revised": final_state.get(
            "revised",
            False,
        ),
        "route": final_state.get(
            "route"
        ),
    }
