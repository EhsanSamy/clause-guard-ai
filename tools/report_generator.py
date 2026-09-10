"""
tools/report_generator.py

Person 4 — builds the final report text.

The report generator distinguishes between:

1. Invalid input
   -> build_invalid_input_report()

2. Blocked by guardrails
   -> build_blocked_report()

3. Incomplete / failed analysis
   -> build_incomplete_report()

4. Fully analyzed case
   -> generate_report()

Only a fully analyzed case goes through ReportSchema because
ReportSchema requires an overall risk score.

Important:
A missing risk score must NEVER be converted into "Medium".
"Medium" is a real assessment and must only come from the
risk-scoring pipeline.

Per docs/guardrails.md §6 (Tool Failures):
the only LLM call here (the short plain-language summary)
has a deterministic fallback if it fails or the schema rejects
the result.

All other report fields are computed directly from validated
state and are never invented by an LLM.
"""

from __future__ import annotations

import logging

from agent.config import MODEL, ROUTING
from schemas.case_schema import CaseState
from schemas.clause_schema import Clause
from schemas.report_schema import ClauseReportEntry, ReportSchema
from tools.llm_client import call_json

logger = logging.getLogger(__name__)


def build_invalid_input_report(case: CaseState) -> str:
    """
    Build a report for input that failed validation.

    No risk assessment should be shown because analysis did not run.
    """
    reason = (
        case.input_error
        or "The submitted document failed input validation."
    )

    return (
        "# Contract Risk Review — Unable to Process\n\n"
        f"**Case ID:** {case.case_id}\n\n"
        "We could not process this submission, so no contract "
        "risk assessment was performed.\n\n"
        f"**Reason:** {reason}\n\n"
        "Please provide a valid contract document and try again."
    )


def build_blocked_report(case: CaseState) -> str:
    """
    Build a report for a request blocked by guardrails.

    No risk assessment should be shown because analysis did not run.
    """
    flags = ", ".join(case.guardrail_flags) or "No specific flag recorded."

    reason = (
        case.block_reason
        or "The request was blocked by the system guardrails."
    )

    return (
        "# Contract Risk Review — Request Blocked\n\n"
        f"**Case ID:** {case.case_id}\n\n"
        "This request was blocked before contract risk analysis "
        "could be completed.\n\n"
        f"**Reason:** {reason}\n\n"
        f"**Guardrail status:** {flags}\n\n"
        "No risk assessment was produced."
    )


def build_incomplete_report(case: CaseState) -> str:
    """
    Build a user-facing report when the pipeline did not produce
    a trustworthy risk assessment.

    This is intentionally separate from a normal risk report.

    We do NOT expose raw tool/LLM errors here. Technical details
    remain available in the stored case record for debugging.
    """

    completed = list(case.completed_steps or [])
    failed = list(case.failed_steps or [])

    if failed:
        failed_display = ", ".join(failed)
    else:
        failed_display = "One or more analysis steps did not complete."

    clauses_found = len(case.clauses)

    if clauses_found == 0:
        detail = (
            "No clauses were available for risk assessment. "
            "This does not mean the contract has no risky clauses."
        )
    else:
        detail = (
            f"{clauses_found} clause(s) were identified, but the "
            "risk assessment could not be completed reliably."
        )

    return (
        "# Contract Risk Review — Analysis Incomplete\n\n"
        f"**Case ID:** {case.case_id}\n\n"
        "### Assessment unavailable\n\n"
        "We could not complete the contract analysis, so a reliable "
        "overall risk level is not available.\n\n"
        f"{detail}\n\n"
        f"**Analysis step requiring attention:** {failed_display}\n\n"
        "Please retry the analysis. If the issue continues, "
        "have a reviewer inspect the case before relying on any "
        "risk assessment.\n\n"
        "**Important:** An unavailable assessment should not be "
        "interpreted as Low, Medium, or High risk."
    )


def generate_report(case: CaseState, route: str) -> str:
    """
    Generate the normal validated report.

    This function should only be called when a trustworthy risk
    assessment is available.
    """

    if not case.risk_assessment_available:
        logger.warning(
            "report_generator: generate_report called without "
            "a valid risk assessment for case_id=%s",
            case.case_id,
        )
        return build_incomplete_report(case)

    if case.overall_risk_score is None:
        logger.warning(
            "report_generator: risk_assessment_available=True but "
            "overall_risk_score is missing for case_id=%s",
            case.case_id,
        )
        return build_incomplete_report(case)

    scored = [
        clause
        for clause in case.clauses
        if clause.is_scored()
    ]

    clause_entries = [
        ClauseReportEntry(
            clause_id=clause.clause_id,
            clause_tag=clause.clause_tag,
            risk_score=clause.risk_score,
            risk_rationale=(
                clause.risk_rationale
                or "No rationale recorded."
            ),
            retrieved_rule_found=clause.retrieved_rule_found,
            flagged_for_review=clause.flagged_for_review,
            source_page=clause.source_page,
        )
        for clause in scored
    ]

    flagged_ids = [
        clause.clause_id
        for clause in scored
        if clause.flagged_for_review
    ]

    guardrail_notices = list(case.guardrail_flags)

    summary = _build_summary(
        case,
        scored,
        route,
    )

    recommendation = ROUTING.route_messages.get(
        case.overall_risk_score.value,
        "Manual review recommended.",
    )

    try:
        report = ReportSchema(
            case_id=case.case_id,
            contract_type=case.contract_type,
            overall_risk_score=case.overall_risk_score,
            requires_mandatory_review=(
                case.requires_mandatory_review()
            ),
            clause_entries=clause_entries,
            flagged_clause_ids=flagged_ids,
            guardrail_notices=guardrail_notices,
            revised_by_reflection=case.revised,
            summary=summary,
            recommendation=recommendation,
        )

    except Exception as exc:
        # Fail safe rather than crashing the graph.
        # The fallback still uses the actual risk score and
        # never invents a replacement score.
        logger.exception(
            "report_generator: ReportSchema validation failed "
            "for case_id=%s",
            case.case_id,
        )

        return _fallback_report(
            case,
            scored,
            exc,
        )

    return report.to_markdown()


def _build_summary(
    case: CaseState,
    scored: list[Clause],
    route: str,
) -> str:
    """
    Generate a short business-friendly summary.

    The summary LLM receives only already-computed facts.
    It cannot determine the risk level itself.
    """

    counts: dict[str, int] = {}

    for clause in scored:
        if clause.risk_score is None:
            continue

        score = clause.risk_score.value
        counts[score] = counts.get(score, 0) + 1

    counts_str = (
        ", ".join(
            f"{count} {score}"
            for score, count in counts.items()
        )
        or "no clauses scored"
    )

    overall = (
        case.overall_risk_score.value
        if case.overall_risk_score
        else "unknown"
    )

    fallback = (
        f"This {case.contract_type or 'contract'} had "
        f"{len(scored)} clause(s) reviewed against the playbook "
        f"({counts_str}). Overall risk is {overall}, "
        f"routed as '{route}'."
    )

    try:
        result = call_json(
            system_prompt=(
                "Write a 2-4 sentence plain-language summary of a "
                "contract risk review for a business owner who is "
                "not a lawyer. Be factual and calm. Do not invent "
                "details beyond the supplied facts. Do not change "
                "or reinterpret the provided risk level. "
                'Respond with ONLY JSON: '
                '{"summary": "<2-4 sentences>"}'
            ),
            user_prompt=(
                f"Contract type: "
                f"{case.contract_type or 'Unknown'}\n"
                f"Overall risk: {overall}\n"
                f"Clause risk counts: {counts_str}\n"
                f"Route: {route}"
            ),
            model=MODEL.report_model,
            max_tokens=300,
            temperature=MODEL.temperature,
        )

        summary = (
            result.get("summary") or ""
        ).strip()

        return summary if summary else fallback

    except Exception as exc:
        logger.warning(
            "report_generator: summary LLM call failed, "
            "using deterministic fallback: %s",
            exc,
        )

        return fallback


def _fallback_report(
    case: CaseState,
    scored: list[Clause],
    exc: Exception,
) -> str:
    """
    Minimal deterministic fallback.

    IMPORTANT:
    This function is only used after a valid risk assessment exists.
    It never converts a missing score into Medium.
    """

    overall = (
        case.overall_risk_score.value
        if case.overall_risk_score
        else "Unavailable"
    )

    lines = [
        f"# Contract Risk Review — {case.case_id}",
        "",
        f"Overall risk: {overall}",
        "",
        "The full report could not be formatted successfully.",
        "The available risk assessment is shown below.",
        "Manual review is recommended.",
        "",
    ]

    for clause in scored:
        score = (
            clause.risk_score.value
            if clause.risk_score
            else "unscored"
        )

        lines.append(
            f"- {clause.clause_id} "
            f"[{clause.clause_tag.value}]: {score}"
        )

    logger.error(
        "report_generator: using fallback report for case_id=%s: %s",
        case.case_id,
        exc,
    )

    return "\n".join(lines)