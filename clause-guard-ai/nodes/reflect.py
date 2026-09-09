"""
nodes/reflect.py

Person 4 — Reflection node.

Per docs/risk_rubric.md §5:
  - Reflection may re-examine risk_score for already-scored clauses.
  - A revision is only valid if it (a) actually changes risk_score, and
    (b) the revised risk_rationale still satisfies the citation rule —
    cites retrieved_rule, or explicitly states "no playbook coverage".
    Reflection cannot remove the citation requirement.
  - tests/test_reflection_revision.py is required proof this loop can
    change an outcome, not just restate the same score in different words.

This node focuses its LLM audit on flagged_for_review clauses (High /
Critical / no-playbook-coverage, per Clause.requires_human_review() and
the no-coverage rule) rather than re-auditing every Low-risk clause on
every run — those are the highest-stakes scores to get right.

Owns: reflection_notes, revised. May rewrite clauses[].risk_score /
risk_rationale / flagged_for_review for individual clauses whose
citation doesn't hold up under audit — reuses
Clause.has_valid_rationale() (schemas/clause_schema.py) as the same
citation guardrail nodes/score_risk.py enforces, so there's exactly one
place that rule lives.

Per docs/guardrails.md §6 (Tool Failures): a failed audit call is
appended to tool_errors and simply leaves that clause's score untouched
rather than crashing the graph.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Tuple

from agent.config import MODEL
from agent.state import GraphState, to_case_state, validate_update
from schemas.clause_schema import Clause, RiskScore
from tools.llm_client import call_json

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "reflect_prompt.md"
SYSTEM_PROMPT = PROMPT_PATH.read_text(encoding="utf-8")


def reflect_node(state: GraphState) -> GraphState:
    case = to_case_state(state)

    if not case.input_valid or case.blocked:
        return {}  # case never reached scoring — nothing to reflect on

    audit_targets = case.flagged_clauses()
    if not audit_targets:
        return validate_update({
            "reflection_notes": "No flagged clauses required reflection.",
            "revised": False,
        })

    notes: List[str] = []
    revised = False
    updated_clauses = list(case.clauses)
    new_tool_errors: List[str] = []

    for clause in audit_targets:
        try:
            result = call_json(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=_build_user_prompt(clause),
                model=MODEL.reflection_model,
                max_tokens=MODEL.max_tokens,
                temperature=MODEL.temperature,
            )
        except Exception as exc:
            logger.warning("reflect: clause_id=%s audit call failed: %s", clause.clause_id, exc)
            new_tool_errors.append(f"reflect: clause_id={clause.clause_id}: {exc}")
            notes.append(f"{clause.clause_id}: reflection audit call failed, score left unchanged.")
            continue

        note, revision = _apply_finding(clause, result)
        notes.append(note)
        if revision is not None:
            idx = next(i for i, c in enumerate(updated_clauses) if c.clause_id == clause.clause_id)
            updated_clauses[idx] = revision
            revised = True

    update = {
        "clauses": updated_clauses,
        "reflection_notes": "\n".join(notes) if notes else "No issues found during reflection.",
        "revised": revised,
    }
    if new_tool_errors:
        update["tool_errors"] = new_tool_errors

    return validate_update(update)


def _build_user_prompt(clause: Clause) -> str:
    rule_section = (
        f'Retrieved rule:\n"""\n{clause.retrieved_rule}\n"""'
        if clause.retrieved_rule_found and clause.retrieved_rule
        else "No playbook rule was retrieved for this clause (retrieved_rule_found = false)."
    )
    current_score = clause.risk_score.value if clause.risk_score else "None"
    return (
        f"Clause id: {clause.clause_id}\n"
        f"Clause tag: {clause.clause_tag.value}\n"
        f'Clause text:\n"""\n{clause.clause_text}\n"""\n\n'
        f"{rule_section}\n\n"
        f"Current risk_score: {current_score}\n"
        f"Current risk_rationale: {clause.risk_rationale}\n"
    )


def _apply_finding(clause: Clause, result: dict) -> Tuple[str, Optional[Clause]]:
    """
    Returns (note, revised_clause_or_None). Only returns a non-None clause
    if the correction is a real change AND passes the same citation
    guardrail score_risk.py enforces (Clause.has_valid_rationale()).
    """
    current_score = clause.risk_score.value if clause.risk_score else "None"
    raw_note = (result.get("note") or "").strip() or "reviewed, no note returned."
    note = f"{clause.clause_id}: {raw_note}"

    if not result.get("needs_correction"):
        return note, None

    try:
        new_score = RiskScore(result.get("corrected_risk_score"))
    except (ValueError, TypeError):
        return note + " [correction discarded: corrected_risk_score invalid]", None

    new_rationale = (result.get("corrected_rationale") or "").strip()
    if not new_rationale:
        return note + " [correction discarded: empty rationale]", None

    if new_score == clause.risk_score:
        return note + " [no actual score change — not applying]", None

    candidate = clause.model_copy(update={
        "risk_score": new_score,
        "risk_rationale": new_rationale,
        "flagged_for_review": (
            new_score in (RiskScore.HIGH, RiskScore.CRITICAL)
            or not clause.retrieved_rule_found
        ),
    })

    if not candidate.has_valid_rationale():
        return note + " [correction discarded: revised rationale failed citation check]", None

    return note + f" [revised {current_score} -> {new_score.value}]", candidate
