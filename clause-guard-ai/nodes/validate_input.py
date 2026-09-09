"""
Person 1 — node: validate_input
Guardrail: docs/guardrails.md §1 "Invalid or Missing Inputs"

Owns: input_valid, input_error
(case_id / raw_input are already set upstream by agent.state.initial_state()
before the graph starts running.)

raw_input can be EITHER:
  - a path to an uploaded file (.pdf / .docx / .txt), or
  - raw contract text pasted directly by the caller

Per docs/guardrails.md §1: on failure here, the graph routes straight to
an error-response node — no downstream node should run. This node only
sets input_valid/input_error; it does not touch guardrail_flags (that's
nodes/guardrail_check.py's job, per the same doc).

Returns a PARTIAL GraphState update (LangGraph merges it into the running
state) — never construct/return a full state here.
"""
from __future__ import annotations
import os
from agent.config import GUARDRAILS
from agent.state import GraphState, to_case_state, validate_update

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt"}
MAX_FILE_SIZE_MB = 15


def validate_input_node(state: GraphState) -> GraphState:
    case = to_case_state(state)
    raw = case.raw_input

    if not raw or not raw.strip():
        return validate_update(
            {"input_valid": False, "input_error": "raw_input is empty"}
        )

    if os.path.exists(raw):
        ext = os.path.splitext(raw)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            return validate_update({
                "input_valid": False,
                "input_error": f"Unsupported file type '{ext}'. Allowed: {sorted(ALLOWED_EXTENSIONS)}",
            })

        size_mb = os.path.getsize(raw) / (1024 * 1024)
        if size_mb == 0:
            return validate_update(
                {"input_valid": False, "input_error": "Uploaded file is empty."}
            )
        if size_mb > MAX_FILE_SIZE_MB:
            return validate_update({
                "input_valid": False,
                "input_error": f"File too large ({size_mb:.1f}MB). Max is {MAX_FILE_SIZE_MB}MB.",
            })

    else:
        # raw_input is pasted contract text, not a file path
        text_len = len(raw.strip())
        if text_len < GUARDRAILS.min_document_chars:
            return validate_update({
                "input_valid": False,
                "input_error": (
                    f"Pasted text too short ({text_len} chars) to plausibly be a "
                    f"contract (min {GUARDRAILS.min_document_chars})."
                ),
            })
        if text_len > GUARDRAILS.max_document_chars:
            return validate_update({
                "input_valid": False,
                "input_error": (
                    f"Pasted text too large ({text_len} chars, max "
                    f"{GUARDRAILS.max_document_chars})."
                ),
            })

    return validate_update({"input_valid": True, "input_error": None})
