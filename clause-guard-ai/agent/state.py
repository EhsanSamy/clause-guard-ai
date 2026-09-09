from __future__ import annotations

import operator
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from schemas.case_schema import CaseState
from schemas.clause_schema import Clause, RiskScore


class GraphState(TypedDict, total=False):
    case_id: str
    raw_input: str
    input_valid: bool
    input_error: Optional[str]

    document_text: Optional[str]
    contract_type: Optional[str]

    clauses: List[Clause]
    guardrail_flags: Annotated[List[str], operator.add]
    blocked: bool
    block_reason: Optional[str]

    overall_risk_score: Optional[RiskScore]
    reflection_notes: Optional[str]
    revised: bool
    route: Optional[str]
    final_report: Optional[str]

    # --- shared ---
    tool_errors: Annotated[List[str], operator.add]

def to_case_state(state: GraphState) -> CaseState:
    return CaseState(**{k: v for k, v in state.items() if v is not None or k in state})


def from_case_state(case: CaseState) -> GraphState:
    return {field: getattr(case, field) for field in CaseState.model_fields}  # type: ignore[return-value]


def validate_update(update: Dict[str, Any]) -> Dict[str, Any]:
    scratch = CaseState()
    merged = {**scratch.model_dump(), **update}
    validated = CaseState(**merged)  # raises pydantic.ValidationError if malformed
    return {k: getattr(validated, k) for k in update.keys()}


def initial_state(raw_input: str) -> GraphState:
    """Entry point used by the graph's START node / validate_input."""
    case = CaseState(raw_input=raw_input)
    return from_case_state(case)