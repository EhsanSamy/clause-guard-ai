"""
Adapter between the FastAPI backend and the LangGraph pipeline in agent/graph.py.

Wired against the real GraphState (agent/state.py) and Clause schema
(schemas/clause_schema.py) — no more guessing.

Contract with api/main.py:
    run_case(case_id: str, contract_text: str) -> dict

The returned dict is merged straight into the case's JSON record and
matches what api/case_store.py + ui/app.py expect:
    overall_risk: "Low" | "Medium" | "High" | "Critical" | "Blocked" | "Invalid"
    human_review_required: bool
    clauses: list[{"tag", "score", "rationale", "flagged"}]
    guardrail_notices: list[str]
    final_report_markdown: str | None   (raw markdown report, extra but handy)
"""
from __future__ import annotations

from typing import Any

from agent.graph import build_graph
from agent.state import initial_state

# Risk levels that require a human to sign off before anything downstream
# happens — mirrors agent/config.py RoutingConfig.mandatory_review_scores.
_MANDATORY_REVIEW_SCORES = {"High", "Critical"}

_graph = None  # built lazily once per process, then reused


def _get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def run_case(case_id: str, contract_text: str) -> dict[str, Any]:
    graph = _get_graph()

    state = initial_state(contract_text)
    # initial_state() generates its own case_id inside CaseState; overwrite
    # it with the one FastAPI already created the JSON record under, so the
    # two stay in sync.
    state["case_id"] = case_id

    final_state = graph.invoke(state)

    return _to_case_record(final_state)


def _enum_value(value: Any) -> Any:
    """RiskScore (and similar) are Enums under the hood — .value gives the
    plain string; plain strings/None pass through untouched."""
    return getattr(value, "value", value)


def _clause_to_dict(clause: Any) -> dict[str, Any]:
    """Clause is a Pydantic model (schemas/clause_schema.py) coming out of
    the graph, but be defensive in case it's already a plain dict."""
    if isinstance(clause, dict):
        get = clause.get
    else:
        get = lambda k, default=None: getattr(clause, k, default)  # noqa: E731

    return {
        "tag": _enum_value(get("clause_tag")),
        "score": _enum_value(get("risk_score")),
        "rationale": get("risk_rationale"),
        "flagged": bool(get("flagged_for_review", False)),
    }


def _to_case_record(final_state: dict[str, Any]) -> dict[str, Any]:
    """final_state is the GraphState TypedDict (plain dict) returned by
    graph.invoke(). Map its real field names to the shape api/case_store.py
    and the Streamlit UI expect.
    """
    # --- Hard stop before any real analysis: bad/empty input -------------
    if not final_state.get("input_valid", True):
        return {
            "overall_risk": "Invalid",
            "human_review_required": False,
            "clauses": [],
            "guardrail_notices": [
                final_state.get("input_error") or "Invalid input."
            ],
            "final_report_markdown": final_state.get("final_report"),
        }

    # --- Hard stop from guardrails (e.g. prompt injection) ----------------
    if final_state.get("blocked"):
        notices = list(final_state.get("guardrail_flags") or [])
        if final_state.get("block_reason"):
            notices.append(final_state["block_reason"])
        return {
            "overall_risk": "Blocked",
            "human_review_required": True,
            "clauses": [],
            "guardrail_notices": notices,
            "final_report_markdown": final_state.get("final_report"),
        }

    # --- Normal, fully-scored case -----------------------------------
    overall_risk = _enum_value(final_state.get("overall_risk_score"))

    guardrail_notices: list[str] = list(final_state.get("guardrail_flags") or [])
    guardrail_notices.extend(final_state.get("tool_errors") or [])

    clauses = [_clause_to_dict(c) for c in (final_state.get("clauses") or [])]

    return {
        "overall_risk": overall_risk,
        "human_review_required": overall_risk in _MANDATORY_REVIEW_SCORES,
        "clauses": clauses,
        "guardrail_notices": guardrail_notices,
        "final_report_markdown": final_state.get("final_report"),
        "contract_type": final_state.get("contract_type"),
        "revised": final_state.get("revised", False),
        "route": final_state.get("route"),
    }
