from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class ClauseTag(str, Enum):
    INDEMNIFICATION = "indemnification"
    LIMITATION_OF_LIABILITY = "limitation_of_liability"
    AUTO_RENEWAL = "auto_renewal"
    TERMINATION = "termination"
    PAYMENT_TERMS = "payment_terms"
    GOVERNING_LAW = "governing_law"
    CONFIDENTIALITY = "confidentiality"
    IP_ASSIGNMENT = "ip_assignment"
    OTHER = "other"


class RiskScore(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"


class Clause(BaseModel):
    """One extracted clause and everything downstream nodes attach to it."""

    # --- set by document_processing / extract_clauses ---
    clause_id: str = Field(..., description="Stable unique id, e.g. 'clause_003'")
    clause_text: str = Field(..., min_length=1, description="Verbatim extracted text")
    clause_tag: ClauseTag
    source_page: Optional[int] = Field(default=None, ge=1)

    # --- set by retrieve_playbook_rules ---
    retrieved_rule: Optional[str] = Field(
        default=None, description="Text of the matched playbook rule, if any"
    )
    retrieved_rule_found: bool = Field(
        default=False,
        description="False if no playbook match was found for this clause's tag",
    )

    # --- set by score_risk ---
    risk_score: Optional[RiskScore] = Field(default=None)
    risk_rationale: Optional[str] = Field(
        default=None,
        description=(
            "Must cite retrieved_rule when retrieved_rule_found is True, or "
            "explicitly state 'no playbook coverage' when False. Validated by "
            "the hallucination guardrail in nodes/score_risk.py."
        ),
    )
    flagged_for_review: bool = Field(default=False)

    @field_validator("clause_text")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("clause_text must not be blank")
        return v

    def is_scored(self) -> bool:
        return self.risk_score is not None

    def requires_human_review(self) -> bool:
        return self.risk_score in (RiskScore.HIGH, RiskScore.CRITICAL)

    def has_valid_rationale(self) -> bool:
        if self.risk_score is None:
            return True  # nothing to validate yet
        if not self.risk_rationale or not self.risk_rationale.strip():
            return False
        if not self.retrieved_rule_found:
            return "no playbook coverage" in self.risk_rationale.lower()
        return True


class ClauseFixture:
    @staticmethod
    def scored_low() -> Clause:
        return Clause(
            clause_id="clause_001",
            clause_text=(
                "Liability under this Agreement shall not exceed the total fees "
                "paid in the 12 months preceding the claim."
            ),
            clause_tag=ClauseTag.LIMITATION_OF_LIABILITY,
            source_page=3,
            retrieved_rule=(
                "A liability cap at 12 months' fees is within the fair range."
            ),
            retrieved_rule_found=True,
            risk_score=RiskScore.LOW,
            risk_rationale=(
                "Matches the playbook's stated acceptable liability cap of "
                "12 months' fees; no deviation."
            ),
            flagged_for_review=False,
        )

    @staticmethod
    def scored_critical_no_coverage() -> Clause:
        return Clause(
            clause_id="clause_007",
            clause_text=(
                "This Agreement shall be governed by the laws of a jurisdiction "
                "not recognized by either party's home country, with no forum "
                "selection clause."
            ),
            clause_tag=ClauseTag.OTHER,
            source_page=9,
            retrieved_rule=None,
            retrieved_rule_found=False,
            risk_score=RiskScore.CRITICAL,
            risk_rationale=(
                "No playbook coverage for this clause; unusual and ambiguous "
                "jurisdiction language flagged as severe exposure by default "
                "rule in risk_rubric.md."
            ),
            flagged_for_review=True,
        )

    @staticmethod
    def unscored() -> Clause:
        return Clause(
            clause_id="clause_002",
            clause_text="Either party may terminate this Agreement for convenience.",
            clause_tag=ClauseTag.TERMINATION,
            source_page=5,
        )