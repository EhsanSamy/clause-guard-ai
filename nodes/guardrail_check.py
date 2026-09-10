"""
nodes/guardrail_check.py

STEP 7 — Guardrail Check.

Checks supplied contract/user text for:

1. Prompt injection
2. Unsafe requests

If either is detected:
- the appropriate guardrail flag is added
- the case is blocked
- a clear block reason is stored

This node does NOT call an LLM.
"""

from __future__ import annotations

import os

from agent.config import GUARDRAILS
from agent.state import GraphState, to_case_state, validate_update
from guardrails.injection_guardrails import inspect_text


def _get_text_to_check(
    raw_input: str,
    document_text: str | None,
) -> str:
    """
    Combine available user/contract text.

    If raw_input is a filesystem path, it is not treated as contract text
    here because document_processing is responsible for reading the file.
    """
    parts: list[str] = []

    if document_text:
        parts.append(document_text)

    if raw_input and not os.path.exists(raw_input):
        parts.append(raw_input)

    return "\n\n".join(parts)


def guardrail_check_node(state: GraphState) -> GraphState:
    """
    Run deterministic guardrail checks.

    If the case is already blocked, no additional processing is performed.
    """
    case = to_case_state(state)

    if not case.input_valid:
        return {}

    if case.blocked:
        return {}

    text = _get_text_to_check(
        raw_input=case.raw_input,
        document_text=case.document_text,
    )

    if not text.strip():
        return {}

    findings = inspect_text(text)

    prompt_injection = findings["prompt_injection"]
    unsafe_request = findings["unsafe_request"]

    if not prompt_injection and not unsafe_request:
        return {"guardrail_flags": []}

    flags: list[str] = []
    reasons: list[str] = []

    if prompt_injection:
        flags.append(
            GUARDRAILS.injection_flag
        )

        reasons.append(
            "Prompt injection detected in the supplied "
            "contract or user text."
        )

    if unsafe_request:
        flags.append(
            GUARDRAILS.unsafe_request_flag
        )

        reasons.append(
            "Unsafe request detected: the requested action "
            "is outside contract-risk review scope."
        )

    return validate_update(
        {
            "guardrail_flags": flags,
            "blocked": True,
            "block_reason": " ".join(reasons),
        }
    )