"""
tools/report_generator.py

Person 4 — builds the final report text.

Three cases:
  1. Invalid input (case.input_valid is False) -> build_invalid_input_report()
  2. Blocked by guardrails (case.blocked is True) -> build_blocked_report()
  3. Normal case -> generate_report(), which builds a validated
     schemas.report_schema.ReportSchema and returns its .to_markdown().

ReportSchema requires overall_risk_score (not Optional), so cases 1 and 2
deliberately do NOT go through it — there's no risk score to report when
analysis never ran.

Per docs/guardrails.md §6 (Tool Failures): the only LLM call here (the
short plain-language `summary`) has a deterministic fallback if it fails
or the schema rejects the result — every other field is computed
directly from already-validated state, never invented by an LLM, since a
human reviewer may rely on this report.
"""
from __future__ import annotations

import logging

from agent.config import MODEL, ROUTING
from schemas.case_schema import CaseState
from schemas.clause_schema import Clause, RiskScore
from schemas.report_schema import ClauseReportEntry, ReportSchema
from tools.llm_client import call_json

logger = logging.getLogger(__name__)


def build_invalid_input_report(case: CaseState) -> str:
    return (
        "# Contract Risk Review — Unable to Process\n\n"
        f"**Case ID:** {case.case_id}\n\n"
        "We could not process this submission.\n\n"
        f"**Reason:** {case.input_error or 'Input failed validation.'}\n\n"
        "Please provide a valid contract document and try again."
    )


def build_blocked_report(case: CaseState) -> str:
    return (
        "# Contract Risk Review — Request Blocked\n\n"
        f"**Case ID:** {case.case_id}\n\n"
        "This request was blocked before analysis could begin, per this "
        "system's guardrails (docs/guardrails.md §2-3).\n\n"
        f"**Reason:** {case.block_reason or 'Blocked by guardrails.'}\n\n"
        f"**Flags:** {', '.join(case.guardrail_flags) or 'none recorded'}\n\n"
        "If you believe this is a mistake, please contact a reviewer "
        "directly rather than resubmitting the same request."
    )


def generate_report(case: CaseState, route: str) -> str:
    """
    `case.overall_risk_score` must already be set by the caller
    (nodes/decide_and_route.py) before this is invoked.
    """
    scored = [c for c in case.clauses if c.is_scored()]

    clause_entries = [
        ClauseReportEntry(
            clause_id=c.clause_id,
            clause_tag=c.clause_tag,
            risk_score=c.risk_score,
            risk_rationale=c.risk_rationale or "No rationale recorded.",
            retrieved_rule_found=c.retrieved_rule_found,
            flagged_for_review=c.flagged_for_review,
            source_page=c.source_page,
        )
        for c in scored
    ]
    flagged_ids = [c.clause_id for c in scored if c.flagged_for_review]

    guardrail_notices = list(case.guardrail_flags) + [
        f"tool issue: {e}" for e in case.tool_errors
    ]

    summary = _build_summary(case, scored, route)
    recommendation = ROUTING.route_messages.get(
        case.overall_risk_score.value if case.overall_risk_score else "Medium",
        "Manual review recommended.",
    )

    try:
        report = ReportSchema(
            case_id=case.case_id,
            contract_type=case.contract_type,
            overall_risk_score=case.overall_risk_score,
            requires_mandatory_review=case.requires_mandatory_review(),
            clause_entries=clause_entries,
            flagged_clause_ids=flagged_ids,
            guardrail_notices=guardrail_notices,
            revised_by_reflection=case.revised,
            summary=summary,
            recommendation=recommendation,
        )
    except Exception as exc:
        # Fail safe rather than crash the graph (docs/guardrails.md §6) —
        # a plain-text fallback still tells a reviewer what happened.
        logger.exception(
            "report_generator: ReportSchema validation failed for case_id=%s", case.case_id
        )
        return _fallback_report(case, scored, exc)

    return report.to_markdown()


def _build_summary(case: CaseState, scored: list[Clause], route: str) -> str:
    counts: dict[str, int] = {}
    for c in scored:
        counts[c.risk_score.value] = counts.get(c.risk_score.value, 0) + 1
    counts_str = ", ".join(f"{v} {k}" for k, v in counts.items()) or "no clauses scored"
    overall = case.overall_risk_score.value if case.overall_risk_score else "unknown"

    fallback = (
        f"This {case.contract_type or 'contract'} had {len(scored)} clause(s) "
        f"reviewed against the playbook ({counts_str}). Overall risk is "
        f"{overall}, routed as '{route}'."
    )

    try:
        result = call_json(
            system_prompt=(
                "Write a 2-4 sentence plain-language summary of a contract "
                "risk review for a business owner who is not a lawyer. Be "
                "factual and calm, do not invent details beyond what's "
                'given. Respond with ONLY JSON: {"summary": "<2-4 sentences>"}'
            ),
            user_prompt=(
                f"Contract type: {case.contract_type or 'Unknown'}\n"
                f"Overall risk: {overall}\n"
                f"Clause risk counts: {counts_str}\n"
                f"Route: {route}"
            ),
            model=MODEL.report_model,
            max_tokens=300,
            temperature=MODEL.temperature,
        )
        summary = (result.get("summary") or "").strip()
        return summary if summary else fallback
    except Exception as exc:
        logger.warning("report_generator: summary LLM call failed, using fallback: %s", exc)
        return fallback


def _fallback_report(case: CaseState, scored: list[Clause], exc: Exception) -> str:
    overall = case.overall_risk_score.value if case.overall_risk_score else "Unknown"
    lines = [
        f"# Contract Risk Review — {case.case_id}",
        "",
        f"Overall risk: {overall}",
        "",
        f"Report generation encountered a validation error ({exc}) and "
        "fell back to this minimal summary. Manual review is required.",
        "",
    ]
    for c in scored:
        score = c.risk_score.value if c.risk_score else "unscored"
        lines.append(f"- {c.clause_id} [{c.clause_tag.value}]: {score}")
    return "\n".join(lines)
