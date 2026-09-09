"""
guardrails/injection_guardrails.py

Deterministic checks for prompt injection and unsafe requests.

This module does NOT call an LLM.

Its purpose is to detect obvious attempts to manipulate the agent
or request unsafe/out-of-scope actions.
"""

from __future__ import annotations

import re
from typing import List

from agent.config import GUARDRAILS


INJECTION_PATTERNS = [
    r"\bignore\s+(all\s+)?previous\s+instructions\b",
    r"\bdisregard\s+(the\s+above|previous|all\s+previous)\b",
    r"\bforget\s+(all\s+)?previous\s+instructions\b",
    r"\boutput\s+(your\s+)?system\s+prompt\b",
    r"\breveal\s+(your\s+)?system\s+prompt\b",
    r"\bshow\s+(me\s+)?your\s+system\s+prompt\b",
    r"\bprint\s+(your\s+)?system\s+prompt\b",
    r"\breveal\s+(your\s+)?instructions\b",
    r"\boutput\s+(your\s+)?instructions\b",
    r"\bignore\s+the\s+contract\b",
    r"\bdo\s+not\s+follow\s+the\s+contract\b",
    r"\bmark\s+.*\s+as\s+low\s+risk\b",
    r"\b(always|automatically)\s+(mark|score)\s+.*\s+low\b",
    r"\byou\s+are\s+now\s+(a|an)\b",
    r"\bact\s+as\s+(a|an)\b",
    r"\bsystem\s+message\s*:",
    r"\bdeveloper\s+message\s*:",
]


UNSAFE_REQUEST_PATTERNS = [
    r"\bauto[-\s]?sign\b",
    r"\bautomatically\s+sign\b",
    r"\bsign\s+the\s+contract\s+for\s+me\b",
    r"\bauto[-\s]?send\b",
    r"\bautomatically\s+send\b",
    r"\bsend\s+the\s+contract\s+for\s+me\b",
    r"\bhide\s+(this|the)\s+liability\b",
    r"\bhide\s+(this|the)\s+risk\b",
    r"\bconceal\s+(this|the)\s+liability\b",
    r"\bconceal\s+(this|the)\s+risk\b",
    r"\bhelp\s+me\s+hide\b",
    r"\bhelp\s+me\s+conceal\b",
    r"\bavoid\s+disclosing\b",
]


def _find_matches(
    text: str,
    patterns: List[str],
) -> List[str]:
    matches: List[str] = []

    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        )

        if match:
            matches.append(match.group(0))

    return matches


def check_prompt_injection(text: str) -> bool:
    """
    Return True if obvious prompt-injection language is detected.
    """
    if not text or not text.strip():
        return False

    lowered = text.lower()

    for hint in GUARDRAILS.injection_keyword_hints:
        if hint.lower() in lowered:
            return True

    return bool(
        _find_matches(
            text,
            INJECTION_PATTERNS,
        )
    )


def check_unsafe_request(text: str) -> bool:
    """
    Return True if the text contains an unsafe/out-of-scope request.
    """
    if not text or not text.strip():
        return False

    return bool(
        _find_matches(
            text,
            UNSAFE_REQUEST_PATTERNS,
        )
    )


def inspect_text(text: str) -> dict[str, bool]:
    """
    Run all deterministic guardrail checks.
    """
    return {
        "prompt_injection": check_prompt_injection(text),
        "unsafe_request": check_unsafe_request(text),
    }
