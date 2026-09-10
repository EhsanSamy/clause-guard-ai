"""
nodes/decide_and_route.py

Person 4 — Decide & Route node. Final node before END.

Reads:
    case.input_valid
    case.blocked
    case.clauses
    case.risk_assessment_available

Writes:
    overall_risk_score
    risk_assessment_available
    route
    final_report

Routing rules:
    Low      -> Report
    Medium   -> Notify
    High     -> Escalate
    Critical -> Block

High/Critical scores are hard-gated to mandatory human review
at this routing level.

Aggregation itself is CaseState.compute_overall_risk()
(docs/risk_rubric.md §3).

Important:
    A missing or incomplete risk assessment is NOT converted into Medium.

    If any clause is unscored, there is no trustworthy overall score.
    The case is marked as incomplete and receives an
    "Analysis Incomplete" report instead of a normal risk report.
"""

from __future__ import annotations

import logging

from agent.state import (
    GraphState,
    to_case_state,
    validate_update,
)
from schemas.clause_schema import RiskScore
from tools.report_generator import (
    build_blocked_report,
    build_incomplete_report,
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

    # ---------------------------------------------------------
    # Guardrail / validation short-circuits
    # ---------------------------------------------------------

    if not case.input_valid:
        return validate_update(
            {
                "route": "Invalid Input",
                "risk_assessment_available": False,
                "overall_risk_score": None,
                "final_report": build_invalid_input_report(case),
            }
        )

    if case.blocked:
        return validate_update(
            {
                "route": "Blocked",
                "risk_assessment_available": False,
                "overall_risk_score": None,
                "final_report": build_blocked_report(case),
            }
        )

    # ---------------------------------------------------------
    # Normal analysis path
    # ---------------------------------------------------------
    #
    # IMPORTANT:
    # Do not calculate an overall risk score unless EVERY
    # extracted clause has a valid risk score.
    #
    # Example:
    #
    #   Clause 1 -> High
    #   Clause 2 -> Low
    #   Clause 3 -> None  (Gemini failed)
    #
    # Computing the overall risk from only Clause 1 + Clause 2
    # would produce a misleading "High" assessment.
    #
    # Instead, the whole assessment becomes incomplete.
    # ---------------------------------------------------------

    if not case.clauses:
        logger.warning(
            "decide_and_route: case_id=%s has no clauses",
            case.case_id,
        )

        incomplete_case = case.model_copy(
            update={
                "overall_risk_score": None,
                "risk_assessment_available": False,
            }
        )

        return validate_update(
            {
                "overall_risk_score": None,
                "risk_assessment_available": False,
                "route": "Analysis Incomplete",
                "final_report": build_incomplete_report(
                    incomplete_case
                ),
            }
        )

    if not case.all_clauses_scored():
        unscored_clause_ids = [
            clause.clause_id
            for clause in case.clauses
            if not clause.is_scored()
        ]

        logger.warning(
            "decide_and_route: case_id=%s has unscored clauses: %s",
            case.case_id,
            unscored_clause_ids,
        )

        incomplete_case = case.model_copy(
            update={
                "overall_risk_score": None,
                "risk_assessment_available": False,
            }
        )

        return validate_update(
            {
                "overall_risk_score": None,
                "risk_assessment_available": False,
                "route": "Analysis Incomplete",
                "final_report": build_incomplete_report(
                    incomplete_case
                ),
            }
        )

    # ---------------------------------------------------------
    # Valid risk assessment
    # ---------------------------------------------------------

    overall = case.compute_overall_risk()

    # This should normally be unreachable because
    # all_clauses_scored() above guarantees that every clause
    # has a risk score. Keep the guard as a defensive check.
    if overall is None:
        logger.warning(
            "decide_and_route: case_id=%s could not compute "
            "a trustworthy overall risk",
            case.case_id,
        )

        incomplete_case = case.model_copy(
            update={
                "overall_risk_score": None,
                "risk_assessment_available": False,
            }
        )

        return validate_update(
            {
                "overall_risk_score": None,
                "risk_assessment_available": False,
                "route": "Analysis Incomplete",
                "final_report": build_incomplete_report(
                    incomplete_case
                ),
            }
        )

    case_with_score = case.model_copy(
        update={
            "overall_risk_score": overall,
            "risk_assessment_available": True,
        }
    )

    route = _ROUTE_FOR_RISK[overall]

    report_text = generate_report(
        case_with_score,
        route=route,
    )

    return validate_update(
        {
            "overall_risk_score": overall,
            "risk_assessment_available": True,
            "route": route,
            "final_report": report_text,
        }
    )
