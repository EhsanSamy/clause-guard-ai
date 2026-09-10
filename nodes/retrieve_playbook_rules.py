from __future__ import annotations

import logging
from typing import List

from agent.state import GraphState, validate_update
from schemas.clause_schema import Clause
from tools.retriever import RetrievedRuleResult, get_index, retrieve_rule

logger = logging.getLogger(__name__)


def _retrieve_for_clause(clause: Clause, index) -> tuple[Clause, RetrievedRuleResult]:
    """Retrieve for one clause and return the updated Clause alongside the
    raw result (the caller needs the result to check .error for logging)."""
    result = retrieve_rule(clause.clause_text, clause.clause_tag, index=index)
    updated = clause.model_copy(update=result.to_clause_fields())
    return updated, result


def retrieve_playbook_rules(state: GraphState) -> GraphState:
    if state.get("blocked", False):
        logger.info(
            "retrieve_playbook_rules: case_id=%s is blocked upstream, skipping retrieval",
            state.get("case_id"),
        )
        return {}

    clauses: List[Clause] = state.get("clauses", []) or []
    if not clauses:
        logger.info(
            "retrieve_playbook_rules: case_id=%s has no clauses to retrieve for",
            state.get("case_id"),
        )
        return {"clauses": []}

    index = get_index()

    updated_clauses: List[Clause] = []
    new_tool_errors: List[str] = []
    found_count = 0

    for clause in clauses:
        try:
            updated, result = _retrieve_for_clause(clause, index)
        except Exception as exc:  
            logger.exception(
                "retrieve_playbook_rules: unexpected failure on clause_id=%s",
                getattr(clause, "clause_id", "<unknown>"),
            )
            new_tool_errors.append(
                f"retrieve_playbook_rules: unexpected failure on "
                f"clause_id={getattr(clause, 'clause_id', '<unknown>')}: {exc}"
            )
            updated_clauses.append(clause)
            continue

        updated_clauses.append(updated)
        if result.retrieved_rule_found:
            found_count += 1
        if result.error:
            new_tool_errors.append(
                f"retrieve_playbook_rules: clause_id={clause.clause_id} "
                f"(tag={clause.clause_tag.value}): {result.error}"
            )

    logger.info(
        "retrieve_playbook_rules: case_id=%s — %d/%d clauses matched a "
        "playbook rule (%d tool errors)",
        state.get("case_id"),
        found_count,
        len(clauses),
        len(new_tool_errors),
    )

    update = {"clauses": updated_clauses}
    if new_tool_errors:
        update["tool_errors"] = new_tool_errors

    return validate_update(update)