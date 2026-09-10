from __future__ import annotations

from typing import List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from schemas.clause_schema import Clause, RiskScore


class CaseState(BaseModel):
    """The full state of one contract review run."""

    # --- validate_input ---
    case_id: str = Field(
        default_factory=lambda: f"case_{uuid4().hex[:8]}"
    )
    raw_input: str = Field(default="")
    input_valid: bool = Field(default=False)
    input_error: Optional[str] = Field(default=None)

    # --- document_processing / classify_contract ---
    document_text: Optional[str] = Field(default=None)
    contract_type: Optional[str] = Field(
        default=None,
        description='e.g. "NDA", "MSA", "SOW"',
    )

    # --- extract_clauses / guardrail_check ---
    clauses: List[Clause] = Field(default_factory=list)
    guardrail_flags: List[str] = Field(default_factory=list)
    blocked: bool = Field(default=False)
    block_reason: Optional[str] = Field(default=None)

    # --- risk assessment ---
    overall_risk_score: Optional[RiskScore] = Field(default=None)
    risk_assessment_available: bool = Field(default=False)

    # --- decide_and_route / reflect ---
    reflection_notes: Optional[str] = Field(default=None)
    revised: bool = Field(default=False)
    route: Optional[str] = Field(default=None)

    # --- report ---
    final_report: Optional[str] = Field(default=None)

    # --- analysis progress / execution status ---
    analysis_status: str = Field(
        default="pending",
        description=(
            "Overall execution status: "
            "pending, running, completed, partial, failed, blocked"
        ),
    )
    current_step: Optional[str] = Field(default=None)

    completed_steps: List[str] = Field(
        default_factory=list
    )

    failed_steps: List[str] = Field(
        default_factory=list
    )

    # --- shared ---
    tool_errors: List[str] = Field(default_factory=list)

    def compute_overall_risk(self) -> Optional[RiskScore]:
        """
        Worst-case-dominates aggregation —
        docs/risk_rubric.md §3.
        """

        scores = [
            c.risk_score
            for c in self.clauses
            if c.risk_score is not None
        ]

        if not scores:
            return None

        if RiskScore.CRITICAL in scores:
            return RiskScore.CRITICAL

        if RiskScore.HIGH in scores:
            return RiskScore.HIGH

        if scores.count(RiskScore.MEDIUM) >= 2:
            return RiskScore.MEDIUM

        return RiskScore.LOW

    def requires_mandatory_review(self) -> bool:
        """
        High/Critical overall scores hard-gate to human review —
        docs/risk_rubric.md §4.

        Never let the LLM decide this.
        """

        return self.overall_risk_score in (
            RiskScore.HIGH,
            RiskScore.CRITICAL,
        )

    def all_clauses_scored(self) -> bool:
        """Return True when every extracted clause has been scored."""

        return (
            len(self.clauses) > 0
            and all(c.is_scored() for c in self.clauses)
        )

    def flagged_clauses(self) -> List[Clause]:
        """Return clauses that require reviewer attention."""

        return [
            c
            for c in self.clauses
            if c.flagged_for_review
        ]

    def add_tool_error(self, message: str) -> None:
        """
        docs/guardrails.md §6 —
        tool failures never crash the graph.
        """

        self.tool_errors.append(message)

    def block(self, reason: str, flag: str) -> None:
        """
        docs/guardrails.md §2/§3 —
        injection & unsafe-request handling.
        """

        self.blocked = True
        self.block_reason = reason

        if flag not in self.guardrail_flags:
            self.guardrail_flags.append(flag)