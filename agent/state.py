from __future__ import annotations

import operator
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from schemas.case_schema import CaseState
from schemas.clause_schema import Clause, RiskScore


class GraphState(TypedDict, total=False):
    # --- case / input ---
    case_id: str
    raw_input: str
    input_valid: bool
    input_error: Optional[str]

    # --- document / classification ---
    document_text: Optional[str]
    contract_type: Optional[str]

    # --- clause analysis ---
    clauses: List[Clause]

    # --- guardrails ---
    guardrail_flags: Annotated[List[str], operator.add]
    blocked: bool
    block_reason: Optional[str]

    # --- risk assessment ---
    overall_risk_score: Optional[RiskScore]
    risk_assessment_available: bool

    # --- reflection / routing ---
    reflection_notes: Optional[str]
    revised: bool
    route: Optional[str]

    # --- report ---
    final_report: Optional[str]

    # --- analysis progress / execution status ---
    analysis_status: str
    current_step: Optional[str]
    completed_steps: Annotated[List[str], operator.add]
    failed_steps: Annotated[List[str], operator.add]

    # --- shared errors ---
    tool_errors: Annotated[List[str], operator.add]


def to_case_state(state: GraphState) -> CaseState:
    """
    Convert the LangGraph state into the Pydantic CaseState model.
    """
    return CaseState(
        **{
            key: value
            for key, value in state.items()
            if value is not None or key in state
        }
    )


def from_case_state(case: CaseState) -> GraphState:
    """
    Convert the Pydantic CaseState model back into LangGraph state.
    """
    return {
        field: getattr(case, field)
        for field in CaseState.model_fields
    }  # type: ignore[return-value]


def validate_update(update: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate a partial state update using the CaseState Pydantic model.

    Raises:
        pydantic.ValidationError:
            If the update would produce an invalid CaseState.
    """
    scratch = CaseState()

    merged = {
        **scratch.model_dump(),
        **update,
    }

    validated = CaseState(**merged)

    return {
        key: getattr(validated, key)
        for key in update.keys()
    }


def initial_state(raw_input: str) -> GraphState:
    """
    Create the initial state used when the graph starts.
    """
    case = CaseState(
        raw_input=raw_input,
        analysis_status="pending",
        current_step=None,
        completed_steps=[],
        failed_steps=[],
        risk_assessment_available=False,
    )

    return from_case_state(case)