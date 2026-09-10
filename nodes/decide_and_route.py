"""
nodes/decide_and_route.py

Person 4 — Decide & Route node. Final node before END.

Reads: case.clauses, case.blocked, case.input_valid
Writes: overall_risk_score, route, final_report

Per docs/guardrails.md §7 (Sensitive Actions): High/Critical overall
scores are hard-gated to mandatory human review at THIS routing level —
never left to the LLM's judgment call. Aggregation itself is
CaseState.compute_overall_risk() (docs/risk_rubric.md §3, worst-case-
dominates: any Critical -> Critical; else any High -> High; else >=2
Medium -> Medium; else Low) — not reimplemented here, so there's exactly
one place that logic lives.

Per docs/guardrails.md §1-3: invalid input / blocked cases short-circuit
here directly (no scoring ever ran) and get a plain-message report
instead of the full ReportSchema, since there's no risk score to report.
"""
from __future__ import annotations

import logging

from agent.state import GraphState, to_case_state, validate_update
from schemas.clause_schema import RiskScore
from tools.report_generator import (
    build_blocked_report,
    build_invalid_input_report,
    generate_report,
)

logger = logging.getLogger(__name__)

_ROUTE_FOR_RISK = {
    RiskScore.LOW: "Report",
    RiskScore.MEDIUM: "Notify",
    RiskScore.HIGH: "Escalate",
    RiskScore.CRITICAL: "Block",
}


def decide_and_route_node(state: GraphState) -> GraphState:
    case = to_case_state(state)

    # --- guardrail / validation short-circuits (docs/guardrails.md §1-3) ---
    if not case.input_valid:
        return validate_update({
            "route": "Invalid Input",
            "final_report": build_invalid_input_report(case),
        })

    if case.blocked:
        return validate_update({
            "route": "Blocked",
            "final_report": build_blocked_report(case),
        })

    # --- normal path ---
    overall = case.compute_overall_risk()
    if overall is None:
        # No clauses were scorable at all — don't silently report "Low"
        # on an assessment that never actually happened.
        logger.warning("decide_and_route: case_id=%s has no scored clauses", case.case_id)
        overall = RiskScore.MEDIUM

    route = _ROUTE_FOR_RISK[overall]
    case_with_score = case.model_copy(update={"overall_risk_score": overall})
    report_text = generate_report(case_with_score, route=route)

    return validate_update({
        "overall_risk_score": overall,
        "route": route,
        "final_report": report_text,
    })
