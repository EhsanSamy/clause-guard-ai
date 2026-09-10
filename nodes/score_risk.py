from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Callable, List, Optional

from agent.config import MODEL, THRESHOLDS
from agent.state import GraphState, validate_update
from schemas.clause_schema import Clause, RiskScore

logger = logging.getLogger(__name__)

PROMPT_FILE = (
    Path(__file__).resolve().parent.parent
    / "prompts"
    / "score_prompt.md"
)

CallModelFn = Callable[[str, str], str]


# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------

def _extract_fenced_block(markdown: str, heading: str) -> str:
    """Extract the first fenced code block under a markdown heading."""

    heading_pattern = re.compile(
        rf"^##\s+{re.escape(heading)}\s*$",
        re.MULTILINE,
    )

    heading_match = heading_pattern.search(markdown)

    if not heading_match:
        raise ValueError(
            f"prompts/score_prompt.md: heading "
            f"'## {heading}' not found"
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


def load_prompt_templates(
    path: Path = PROMPT_FILE,
) -> tuple[str, str]:
    """Load the scoring system prompt and user prompt template."""

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


# ---------------------------------------------------------------------------
# Building the per-clause prompt
# ---------------------------------------------------------------------------

def build_retrieved_rule_section(clause: Clause) -> str:
    """Build the playbook section included in the scoring prompt."""

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
    """Build the scoring prompt for one clause."""

    return USER_PROMPT_TEMPLATE.format(
        clause_tag=clause.clause_tag.value,
        clause_text=clause.clause_text,
        retrieved_rule_section=build_retrieved_rule_section(clause),
    )


# ---------------------------------------------------------------------------
# Model call — Gemini
# ---------------------------------------------------------------------------

class ScoringToolError(RuntimeError):
    """Raised when the scoring model cannot be called successfully."""


def _is_retryable_gemini_error(exc: Exception) -> bool:
    """
    Return True for temporary Gemini/API failures that may succeed
    if retried after a short delay.

    Retryable:
        429 - rate limit / quota pressure
        500 - internal server error
        502 - bad gateway
        503 - service unavailable
        504 - gateway timeout

    Non-retryable errors such as authentication or invalid requests
    are not retried.
    """

    message = str(exc).lower()

    retryable_codes = (
        "429",
        "500",
        "502",
        "503",
        "504",
    )

    return any(code in message for code in retryable_codes)


def _call_gemini(
    system_prompt: str,
    user_prompt: str,
) -> str:
    """
    Call Gemini for one clause.

    Temporary API errors are retried with exponential backoff.
    Permanent failures are raised immediately.
    """

    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise ScoringToolError(
            "GEMINI_API_KEY is not set. "
            "Set it in .env or enable SCORING_FORCE_MOCK=true "
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

    # Use the existing project retry configuration.
    max_retries = max(0, THRESHOLDS.max_scoring_retries)

    for attempt in range(1, max_retries + 2):
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

            text = getattr(response, "text", None)

            if not text or not text.strip():
                raise ScoringToolError(
                    "Gemini API returned no text content."
                )

            return text.strip()

        except ScoringToolError:
            raise

        except Exception as exc:
            retryable = _is_retryable_gemini_error(exc)

            if not retryable:
                raise ScoringToolError(
                    f"Gemini API call failed: {exc}"
                ) from exc

            if attempt > max_retries:
                raise ScoringToolError(
                    f"Gemini API call failed after "
                    f"{attempt} attempts: {exc}"
                ) from exc

            delay = min(2 ** (attempt - 1), 8)

            logger.warning(
                "Gemini temporary failure on attempt %d/%d. "
                "Retrying in %ds: %s",
                attempt,
                max_retries + 1,
                delay,
                exc,
            )

            time.sleep(delay)

    raise ScoringToolError(
        "Gemini API call failed unexpectedly."
    )


# ---------------------------------------------------------------------------
# Mock scorer
# ---------------------------------------------------------------------------

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
    """Deterministic local scorer used only for tests/mock mode."""

    found = (
        "retrieved_rule_found = false"
        not in user_prompt.lower()
    )

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
                "No severe-exposure pattern detected, so it defaults "
                "to Medium pending human review."
            )
        )

    return json.dumps(
        {
            "risk_score": score,
            "risk_rationale": rationale,
        }
    )


# ---------------------------------------------------------------------------
# Resolve model function
# ---------------------------------------------------------------------------

def resolve_default_call_model_fn() -> CallModelFn:
    """
    Resolve the scoring model.

    Mock mode is enabled explicitly through SCORING_FORCE_MOCK=true.

    Important:
    A missing GEMINI_API_KEY does NOT silently switch to mock mode.
    This prevents production runs from looking successful when Gemini
    was never actually called.
    """

    force_mock = (
        os.getenv("SCORING_FORCE_MOCK", "")
        .strip()
        .lower()
        in ("1", "true", "yes")
    )

    if force_mock:
        logger.info(
            "score_risk: SCORING_FORCE_MOCK is enabled — "
            "using mock scorer."
        )
        return _mock_call_model

    return _call_gemini


# ---------------------------------------------------------------------------
# Response parsing + validation
# ---------------------------------------------------------------------------

class ScoringValidationError(ValueError):
    """
    Raised when the model response fails schema or guardrail validation.
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
    """Find quoted phrases in the rationale absent from the rule."""

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
    """Parse Gemini JSON and apply scoring guardrails."""

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

    expected_keys = {
        "risk_score",
        "risk_rationale",
    }

    extra_keys = set(data.keys()) - expected_keys

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
            f"{[score.value for score in RiskScore]}"
        ) from exc

    rationale = data["risk_rationale"]

    if not isinstance(rationale, str) or not rationale.strip():
        raise ScoringValidationError(
            "risk_rationale must be a non-empty string"
        )

    rationale = rationale.strip()

    # -----------------------------------------------------------------------
    # Guardrail: no playbook coverage
    # -----------------------------------------------------------------------

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
                "retrieved_rule_found is False — risk_score may not "
                "be Low (docs/risk_rubric.md §2)"
            )

    else:

        # -------------------------------------------------------------------
        # Guardrail: quoted claims must be traceable to retrieved rule
        # -------------------------------------------------------------------

        unsupported = _find_unsupported_quotes(
            rationale,
            clause.retrieved_rule or "",
        )

        if unsupported:
            raise ScoringValidationError(
                "rationale contains quoted text not found in "
                f"retrieved_rule: {unsupported}"
            )

    return risk_score, rationale


# ---------------------------------------------------------------------------
# Per-clause scoring
# ---------------------------------------------------------------------------

def score_clause(
    clause: Clause,
    call_model_fn: Optional[CallModelFn] = None,
    max_retries: int = THRESHOLDS.max_scoring_retries,
) -> tuple[Clause, Optional[str]]:
    """
    Score one clause.

    Returns:
        (updated_clause, error)

    Important:
        Model/tool failure NEVER creates a fake Medium score.

        If scoring fails:
            risk_score = None
            risk_rationale = None
            flagged_for_review = True

        This allows the rest of the pipeline and UI to distinguish
        "not scored" from a real Medium assessment.
    """

    fn = call_model_fn or resolve_default_call_model_fn()

    system_prompt = SYSTEM_PROMPT
    user_prompt = build_user_prompt(clause)

    last_error: Optional[str] = None

    # Validation retries are separate from Gemini API retries.
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

            # Tool/API failures are already handled by _call_gemini
            # when they are temporary. Do not repeat the whole prompt
            # here because that can unnecessarily increase quota usage.
            break

        try:
            risk_score, rationale = parse_and_validate_response(
                raw,
                clause,
            )

            # High/Critical always require review.
            # No-playbook clauses always require human review.
            flagged = (
                risk_score
                in (
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

            # Ask Gemini to correct the invalid response.
            user_prompt = (
                build_user_prompt(clause)
                + "\n\n"
                "Your previous response was rejected because:\n"
                f"{exc}\n\n"
                "Correct the response and return ONLY the required "
                "JSON object. Do not add Markdown, explanations, "
                "or additional keys."
            )

    # -----------------------------------------------------------------------
    # Safe failure — NO FAKE RISK SCORE
    # -----------------------------------------------------------------------

    failure_rationale = (
        "Automated scoring could not produce a validated risk assessment. "
        "This clause requires manual review."
    )

    updated = clause.model_copy(
        update={
            # The critical change:
            # Do NOT default to Medium.
            "risk_score": None,
            "risk_rationale": failure_rationale,
            "flagged_for_review": True,
        }
    )

    return updated, last_error


# ---------------------------------------------------------------------------
# LangGraph node
# ---------------------------------------------------------------------------

def score_risk(state: GraphState) -> GraphState:
    """LangGraph node — score every extracted clause."""

    if state.get("blocked", False):
        logger.info(
            "score_risk: case_id=%s is blocked upstream, "
            "skipping scoring",
            state.get("case_id"),
        )

        return {
            "risk_assessment_available": False,
        }

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
            "clauses": [],
            "risk_assessment_available": False,
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

    risk_assessment_available = (
        len(updated_clauses) > 0
        and scored_count == len(updated_clauses)
    )

    logger.info(
        "score_risk: case_id=%s — %d/%d clauses scored "
        "(%d failed)",
        state.get("case_id"),
        scored_count,
        len(updated_clauses),
        len(new_tool_errors),
    )

    update = {
        "clauses": updated_clauses,
        "risk_assessment_available": risk_assessment_available,
    }

    if new_tool_errors:
        update["tool_errors"] = new_tool_errors
        update["failed_steps"] = ["score_risk"]

    return validate_update(update)
