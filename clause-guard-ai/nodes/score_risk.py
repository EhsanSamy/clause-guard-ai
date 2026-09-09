from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Callable, List, Optional

from agent.config import MODEL, THRESHOLDS
from agent.state import GraphState, validate_update
from schemas.clause_schema import Clause, RiskScore

logger = logging.getLogger(__name__)

PROMPT_FILE = Path(__file__).resolve().parent.parent / "prompts" / "score_prompt.md"

CallModelFn = Callable[[str, str], str]


# --------------------------------------------------------------------------- #
# Prompt loading
# --------------------------------------------------------------------------- #
def _extract_fenced_block(markdown: str, heading: str) -> str:
    """Extract the content of the first fenced code block that appears
    after the given '## <heading>' line.
    """
    heading_pattern = re.compile(
        rf"^##\s+{re.escape(heading)}\s*$",
        re.MULTILINE,
    )

    heading_match = heading_pattern.search(markdown)

    if not heading_match:
        raise ValueError(
            f"prompts/score_prompt.md: heading '## {heading}' not found"
        )

    rest = markdown[heading_match.end():]

    fence_match = re.search(
        r"```(?:\w*)\n(.*?)\n```",
        rest,
        re.DOTALL,
    )

    if not fence_match:
        raise ValueError(
            f"prompts/score_prompt.md: no fenced code block found "
            f"under '## {heading}'"
        )

    return fence_match.group(1)


def load_prompt_templates(path: Path = PROMPT_FILE) -> tuple[str, str]:
    markdown = path.read_text(encoding="utf-8")

    system_prompt = _extract_fenced_block(
        markdown,
        "System Prompt",
    )

    user_template = _extract_fenced_block(
        markdown,
        "User Prompt Template",
    )

    return system_prompt, user_template


SYSTEM_PROMPT, USER_PROMPT_TEMPLATE = load_prompt_templates()


# --------------------------------------------------------------------------- #
# Building the per-clause prompt
# --------------------------------------------------------------------------- #
def build_retrieved_rule_section(clause: Clause) -> str:
    if clause.retrieved_rule_found and clause.retrieved_rule:
        return (
            "Retrieved playbook rule for this category:\n"
            '"""\n'
            f"{clause.retrieved_rule}\n"
            '"""'
        )

    return (
        "No playbook rule was found for this clause category "
        "(retrieved_rule_found = false).\n"
        "There is no retrieved_rule text available to cite."
    )


def build_user_prompt(clause: Clause) -> str:
    return USER_PROMPT_TEMPLATE.format(
        clause_tag=clause.clause_tag.value,
        clause_text=clause.clause_text,
        retrieved_rule_section=build_retrieved_rule_section(clause),
    )


# --------------------------------------------------------------------------- #
# Model call — Gemini
# --------------------------------------------------------------------------- #
class ScoringToolError(RuntimeError):
    """Raised on a genuine model-call failure.

    Examples:
    - missing API key
    - authentication failure
    - network failure
    - rate limit
    - Gemini API error
    """


def _call_gemini(system_prompt: str, user_prompt: str) -> str:

    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise ScoringToolError(
            "GEMINI_API_KEY not set — cannot call the real scoring model. "
            "Set it in .env, or use score_clause(..., call_model_fn=mock) "
            "for local testing."
        )

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise ScoringToolError(
            "google-genai is not installed. "
            "Install it with: pip install google-genai"
        ) from exc

    try:
        client = genai.Client(api_key=api_key)

        response = client.models.generate_content(
            model=MODEL.scoring_model,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=MODEL.temperature,
                max_output_tokens=MODEL.max_tokens,
                response_mime_type="application/json",
            ),
        )

    except Exception as exc: 
        raise ScoringToolError(
            f"Gemini API call failed: {exc}"
        ) from exc

    text = getattr(response, "text", None)

    if not text or not text.strip():
        raise ScoringToolError(
            "Gemini API returned no text content"
        )

    return text.strip()


# --------------------------------------------------------------------------- #
# Mock scorer
# --------------------------------------------------------------------------- #
_SEVERE_PATTERN_HINTS = [
    "uncapped",
    "unlimited liability",
    "any and all claims",
    "no cap",
    "in perpetuity",
    "sole discretion",
    "mutually agreed upon at the time of dispute",
]

def _mock_call_model(
    system_prompt: str,
    user_prompt: str,
) -> str:

    found = "retrieved_rule_found = false" not in user_prompt.lower()

    clause_text_match = re.search(
        r'Clause text:\n"""\n(.*?)\n"""',
        user_prompt,
        re.DOTALL,
    )

    clause_text = (
        clause_text_match.group(1)
        if clause_text_match
        else ""
    )

    lowered = clause_text.lower()

    has_severe_pattern = any(
        hint in lowered
        for hint in _SEVERE_PATTERN_HINTS
    )

    if found:
        score = (
            RiskScore.HIGH.value
            if has_severe_pattern
            else RiskScore.LOW.value
        )

        rationale = (
            "Mock scorer: retrieved playbook rule was available; "
            + (
                "clause text matches a known severe-exposure pattern."
                if has_severe_pattern
                else
                "clause text appears consistent with the retrieved rule."
            )
        )

    else:
        score = (
            RiskScore.CRITICAL.value
            if has_severe_pattern
            else RiskScore.MEDIUM.value
        )

        rationale = (
            "Mock scorer: there is no playbook coverage for this clause. "
            + (
                "Clause text matches a known severe-exposure pattern, "
                "so it defaults to Critical."
                if has_severe_pattern
                else
                "No severe-exposure pattern detected, so it defaults to "
                "Medium pending human review."
            )
        )

    return json.dumps(
        {
            "risk_score": score,
            "risk_rationale": rationale,
        }
    )


# --------------------------------------------------------------------------- #
# Resolve model function
# --------------------------------------------------------------------------- #
def resolve_default_call_model_fn() -> CallModelFn:
    force_mock = os.getenv(
        "SCORING_FORCE_MOCK",
        "",
    ).strip().lower() in ("1", "true", "yes")

    if force_mock:
        logger.info(
            "score_risk: SCORING_FORCE_MOCK is enabled — "
            "using mock scorer."
        )
        return _mock_call_model

    if not os.getenv("GEMINI_API_KEY"):
        logger.warning(
            "score_risk: GEMINI_API_KEY not set — using mock scorer. "
            "Results are NOT reliable risk assessments in this mode."
        )
        return _mock_call_model

    return _call_gemini


# --------------------------------------------------------------------------- #
# Response parsing + validation
# --------------------------------------------------------------------------- #
class ScoringValidationError(ValueError):
    """Raised when the model response fails schema or guardrail validation.

    Caught internally by score_clause() to drive the retry loop.
    """


def _strip_code_fences(text: str) -> str:
    """Remove Markdown JSON code fences if the model returns them."""

    text = text.strip()

    fence_match = re.match(
        r"^```(?:json)?\s*\n(.*?)\n```$",
        text,
        re.DOTALL,
    )

    return (
        fence_match.group(1).strip()
        if fence_match
        else text
    )


def _find_unsupported_quotes(
    rationale: str,
    retrieved_rule: str,
) -> List[str]:
    quotes = re.findall(
        r'"([^"]{4,})"',
        rationale,
    )

    return [
        quote
        for quote in quotes
        if quote.lower() not in retrieved_rule.lower()
    ]


def parse_and_validate_response(
    raw_text: str,
    clause: Clause,
) -> tuple[RiskScore, str]:
    """Parse Gemini JSON response and apply scoring guardrails."""

    cleaned = _strip_code_fences(raw_text)

    try:
        data = json.loads(cleaned)

    except json.JSONDecodeError as exc:
        raise ScoringValidationError(
            f"response was not valid JSON: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise ScoringValidationError(
            "response JSON was not an object"
        )

    extra_keys = set(data.keys()) - {
        "risk_score",
        "risk_rationale",
    }

    if extra_keys:
        raise ScoringValidationError(
            f"response contained unexpected keys: {extra_keys}"
        )

    if (
        "risk_score" not in data
        or "risk_rationale" not in data
    ):
        raise ScoringValidationError(
            "response missing risk_score or risk_rationale"
        )

    raw_score = data["risk_score"]

    try:
        risk_score = RiskScore(raw_score)

    except ValueError as exc:
        raise ScoringValidationError(
            f"risk_score {raw_score!r} is not one of "
            f"{[s.value for s in RiskScore]}"
        ) from exc

    rationale = data["risk_rationale"]

    if not isinstance(rationale, str) or not rationale.strip():
        raise ScoringValidationError(
            "risk_rationale must be a non-empty string"
        )

    rationale = rationale.strip()

    # --------------------------------------------------------------------- #
    # Guardrail: no playbook coverage
    # --------------------------------------------------------------------- #
    if not clause.retrieved_rule_found:

        if (
            "no playbook coverage" not in rationale.lower()
            and "no coverage" not in rationale.lower()
        ):
            raise ScoringValidationError(
                "retrieved_rule_found is False but rationale does not "
                "state 'no playbook coverage'"
            )

        if risk_score == RiskScore.LOW:
            raise ScoringValidationError(
                "retrieved_rule_found is False — risk_score may not be Low "
                "(docs/risk_rubric.md §2)"
            )

    else:

        # ----------------------------------------------------------------- #
        # Guardrail: quoted claims must be traceable to retrieved rule
        # ----------------------------------------------------------------- #
        unsupported = _find_unsupported_quotes(
            rationale,
            clause.retrieved_rule or "",
        )

        if unsupported:
            raise ScoringValidationError(
                "rationale contains quoted text not found in retrieved_rule: "
                f"{unsupported}"
            )

    return risk_score, rationale


# --------------------------------------------------------------------------- #
# Per-clause scoring with bounded retries + safe fallback
# --------------------------------------------------------------------------- #
def score_clause(
    clause: Clause,
    call_model_fn: Optional[CallModelFn] = None,
    max_retries: int = THRESHOLDS.max_scoring_retries,
) -> tuple[Clause, Optional[str]]:

    fn = call_model_fn or resolve_default_call_model_fn()

    system_prompt = SYSTEM_PROMPT
    user_prompt = build_user_prompt(clause)

    last_error: Optional[str] = None

    for attempt in range(
        1,
        max_retries + 2,
    ):

        try:
            raw = fn(
                system_prompt,
                user_prompt,
            )

        except ScoringToolError as exc:

            last_error = (
                f"tool failure on attempt {attempt}: {exc}"
            )

            logger.warning(
                "score_clause: clause_id=%s %s",
                clause.clause_id,
                last_error,
            )

            # API/network/auth failures aren't immediately retried.
            break

        try:
            risk_score, rationale = parse_and_validate_response(
                raw,
                clause,
            )

            # High/Critical always require review.
            # No-playbook clauses always require human review.
            flagged = (
                risk_score in (
                    RiskScore.HIGH,
                    RiskScore.CRITICAL,
                )
                or not clause.retrieved_rule_found
            )

            updated = clause.model_copy(
                update={
                    "risk_score": risk_score,
                    "risk_rationale": rationale,
                    "flagged_for_review": flagged,
                }
            )

            return updated, None

        except ScoringValidationError as exc:

            last_error = (
                f"validation failed on attempt {attempt}: {exc}"
            )

            logger.warning(
                "score_clause: clause_id=%s %s",
                clause.clause_id,
                last_error,
            )

            # Give Gemini the exact validation failure so the next
            # attempt can correct its response.
            user_prompt = (
                build_user_prompt(clause)
                + f"\n\nYour previous response was rejected: {exc}. "
                "Correct this and respond again with only the JSON object."
            )

    # --------------------------------------------------------------------- #
    # Safe fallback
    # --------------------------------------------------------------------- #
    fallback_rationale = (
        "Automated scoring failed validation after repeated attempts "
        f"({last_error}). Defaulting to Medium pending manual review "
        "rather than accepting an unvalidated score."
    )

    updated = clause.model_copy(
        update={
            "risk_score": RiskScore.MEDIUM,
            "risk_rationale": fallback_rationale,
            "flagged_for_review": True,
        }
    )

    return updated, last_error


# --------------------------------------------------------------------------- #
# LangGraph node
# --------------------------------------------------------------------------- #

def score_risk(state: GraphState) -> GraphState:
    """LangGraph node — score every extracted clause."""

    if state.get("blocked", False):

        logger.info(
            "score_risk: case_id=%s is blocked upstream, skipping scoring",
            state.get("case_id"),
        )

        return {}

    clauses: List[Clause] = (
        state.get("clauses", [])
        or []
    )

    if not clauses:

        logger.info(
            "score_risk: case_id=%s has no clauses to score",
            state.get("case_id"),
        )

        return {
            "clauses": []
        }

    call_model_fn = resolve_default_call_model_fn()

    updated_clauses: List[Clause] = []
    new_tool_errors: List[str] = []

    for clause in clauses:

        updated, error = score_clause(
            clause,
            call_model_fn=call_model_fn,
        )

        updated_clauses.append(updated)

        if error:
            new_tool_errors.append(
                f"score_risk: clause_id={clause.clause_id} "
                f"(tag={clause.clause_tag.value}): {error}"
            )

    scored_count = sum(
        1
        for clause in updated_clauses
        if clause.is_scored()
    )

    logger.info(
        "score_risk: case_id=%s — %d/%d clauses scored "
        "(%d fell back after errors)",
        state.get("case_id"),
        scored_count,
        len(clauses),
        len(new_tool_errors),
    )

    update = {
        "clauses": updated_clauses
    }

    if new_tool_errors:
        update["tool_errors"] = new_tool_errors

    return validate_update(update)