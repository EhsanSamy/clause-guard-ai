from __future__ import annotations
from typing import List, Optional
from pydantic import BaseModel, Field, model_validator
from schemas.clause_schema import ClauseTag, RiskScore

class ClauseReportEntry(BaseModel):
    clause_id: str
    clause_tag: ClauseTag
    risk_score: RiskScore
    risk_rationale: str
    retrieved_rule_found: bool
    flagged_for_review: bool
    source_page: Optional[int] = None


class ReportSchema(BaseModel):
    case_id: str
    contract_type: Optional[str] = None
    overall_risk_score: RiskScore
    requires_mandatory_review: bool
    clause_entries: List[ClauseReportEntry] = Field(default_factory=list)
    flagged_clause_ids: List[str] = Field(default_factory=list)
    guardrail_notices: List[str] = Field(
        default_factory=list,
        description=(
            "Human-readable notices surfaced from CaseState.guardrail_flags "
            "and tool_errors, e.g. 'retrieval unavailable for 1 clause — "
            "scored conservatively'."
        ),
    )
    revised_by_reflection: bool = Field(
        default=False,
        description="True if the Reflection pass changed any clause's score.",
    )
    summary: str = Field(
        ..., min_length=1, description="Short plain-language summary, 2-4 sentences."
    )
    recommendation: str = Field(
        ...,
        min_length=1,
        description=(
            "Next action for the human reviewer, phrased per docs/risk_rubric.md "
            "§4 routing table (e.g. 'Mandatory legal sign-off required before "
            "proceeding.')."
        ),
    )

    @model_validator(mode="after")
    def _consistency_checks(self) -> "ReportSchema":
        if self.overall_risk_score in (RiskScore.HIGH, RiskScore.CRITICAL):
            if not self.requires_mandatory_review:
                raise ValueError(
                    "High/Critical overall_risk_score must set "
                    "requires_mandatory_review=True (risk_rubric.md §4)."
                )

        entry_ids = {e.clause_id for e in self.clause_entries}
        flagged_not_in_entries = set(self.flagged_clause_ids) - entry_ids
        if flagged_not_in_entries:
            raise ValueError(
                f"flagged_clause_ids references unknown clauses: "
                f"{flagged_not_in_entries}"
            )

        entries_flagged_ids = {
            e.clause_id for e in self.clause_entries if e.flagged_for_review
        }
        if entries_flagged_ids != set(self.flagged_clause_ids):
            raise ValueError(
                "flagged_clause_ids must exactly match clause_entries marked "
                "flagged_for_review=True."
            )

        return self

    def to_markdown(self) -> str:
        lines = [
            f"# Contract Risk Review — {self.case_id}",
            "",
            f"**Contract type:** {self.contract_type or 'Unknown'}",
            f"**Overall risk:** {self.overall_risk_score.value}",
            f"**Mandatory human review required:** "
            f"{'Yes' if self.requires_mandatory_review else 'No'}",
            "",
            "## Summary",
            self.summary,
            "",
            "## Recommendation",
            self.recommendation,
        ]

        if self.guardrail_notices:
            lines += ["", "## Guardrail Notices"]
            lines += [f"- {n}" for n in self.guardrail_notices]

        if self.revised_by_reflection:
            lines += ["", "_Note: at least one clause score was revised during "
                        "the Reflection pass._"]

        lines += ["", "## Clauses", ""]
        for e in self.clause_entries:
            flag = "flagged for review" if e.flagged_for_review else ""
            page = f" (p. {e.source_page})" if e.source_page else ""
            lines += [
                f"### {e.clause_tag.value} — {e.risk_score.value}{flag}",
                f"*Clause `{e.clause_id}`{page}*",
                "",
                e.risk_rationale,
                "",
            ]

        return "\n".join(lines)
