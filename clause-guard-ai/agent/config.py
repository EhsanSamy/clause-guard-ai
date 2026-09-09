from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Dict, List


# --------------------------------------------------------------------------- 
# TODO: Model configuration
# --------------------------------------------------------------------------- 
@dataclass(frozen=True)
class ModelConfig:
    classification_model: str = ""
    extraction_model: str = ""
    scoring_model: str = os.getenv("SCORING_MODEL", "gemini-3.6-flash")
<<<<<<< HEAD
    reflection_model: str = os.getenv("REFLECTION_MODEL", "gemini-3.8-flash")
    report_model: str = os.getenv("REPORT_MODEL", "gemini-3.8-flash")
    max_tokens: int = 4000
=======
    reflection_model: str = ""
    report_model: str = ""

    max_tokens: int = 8000
>>>>>>> origin/main
    temperature: float = 0.0  

# --------------------------------------------------------------------------- 
# Risk rubric thresholds
# --------------------------------------------------------------------------- 
@dataclass(frozen=True)
class RiskThresholds:
    medium_clause_count_for_medium_case: int = 2

    max_scoring_retries: int = 2
    max_report_validation_retries: int = 2

# --------------------------------------------------------------------------- 
# Retrieval configuration 
# --------------------------------------------------------------------------- 
@dataclass(frozen=True)
class RetrievalConfig:
    knowledge_base_dir: str = "retrieval/knowledge_base"
    top_k: int = 1  
    similarity_threshold: float = 0.0  

# --------------------------------------------------------------------------- 
# Guardrail configuration 
# --------------------------------------------------------------------------- 
@dataclass(frozen=True)
class GuardrailConfig:
    injection_flag: str = "prompt_injection_detected"
    unsafe_request_flag: str = "unsafe_request_detected"
    invalid_input_flag: str = "invalid_input"
    no_playbook_match_flag: str = "no_playbook_match"
    tool_failure_flag: str = "tool_failure"

    min_document_chars: int = 200
    max_document_chars: int = 500_000

    injection_keyword_hints: List[str] = field(
        default_factory=lambda: [
            "ignore previous instructions",
            "ignore all previous",
            "disregard the above",
            "you are now",
            "system prompt",
            "act as",
            "output your instructions",
        ]
    )

# ---------------------------------------------------------------------------
# Routing configuration — mirrors docs/risk_rubric.md §4
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RoutingConfig:
    mandatory_review_scores: tuple = ("High", "Critical")
    hard_block_scores: tuple = ("Critical",)

    route_messages: Dict[str, str] = field(
        default_factory=lambda: {
            "Low": "Auto-generate report, no human gate required.",
            "Medium": "Report generated; reviewer check recommended.",
            "High": "Mandatory reviewer sign-off before any downstream action.",
            "Critical": "Hard block on automated approval; escalation flag set.",
        }
    )

# --------------------------------------------------------------------------- 
# Single importable config instance
# --------------------------------------------------------------------------- 
MODEL = ModelConfig()
THRESHOLDS = RiskThresholds()
RETRIEVAL = RetrievalConfig()
GUARDRAILS = GuardrailConfig()
ROUTING = RoutingConfig()