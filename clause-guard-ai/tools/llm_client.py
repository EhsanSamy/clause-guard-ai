cat > tools/llm_client.py <<'PY'
"""
tools/llm_client.py

<<<<<<< HEAD
Shared Gemini client wrapper. Any node that needs a structured (JSON)
model call should go through call_json() so retry/parsing/error-handling
behavior stays consistent across the whole graph (classify_contract,
extract_clauses, score_risk, reflect, report_generator, ...).

Requires GEMINI_API_KEY in the environment — see .env.example.
=======
Shared Gemini client wrapper.

Any node that needs a structured JSON model call should go through
call_json() so retry/parsing/error-handling behavior stays consistent
across the graph.

Requires GEMINI_API_KEY in the environment.
>>>>>>> origin/main
"""

from __future__ import annotations

import json
import os
from typing import Optional
<<<<<<< HEAD
from dotenv import load_dotenv
load_dotenv()
import google.generativeai as genai

_configured: bool = False

# Used only as a fallback if agent.config.MODEL.* is still blank.
FALLBACK_MODEL = "gemini-3.8-flash"


def _ensure_configured() -> None:
    global _configured
    if not _configured:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set in the environment.")
        genai.configure(api_key=api_key)
        _configured = True
=======

from google import genai
from google.genai import types


_client: Optional[genai.Client] = None

# Used when MODEL.* is empty.
FALLBACK_MODEL = "gemini-3.5-flash"


def get_client() -> genai.Client:
    global _client

    if _client is None:
        api_key = os.environ.get("GEMINI_API_KEY")

        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set in the environment."
            )

        _client = genai.Client(api_key=api_key)

    return _client
>>>>>>> origin/main


def call_json(
    system_prompt: str,
    user_prompt: str,
    model: str = "",
    max_tokens: int = 1000,
    temperature: float = 0.0,
) -> dict:
    """
    Calls Gemini and expects a JSON object back.

    The caller receives a Python dict.
    Invalid JSON raises ValueError so the calling node can
    append the error to tool_errors.
    """
<<<<<<< HEAD
    _ensure_configured()

    gemini_model = genai.GenerativeModel(
        model_name=model or FALLBACK_MODEL,
        system_instruction=system_prompt,
    )

    resp = gemini_model.generate_content(
        user_prompt,
        generation_config=genai.types.GenerationConfig(
            max_output_tokens=max_tokens,
            temperature=temperature,
        ),
    )

    raw = (resp.text or "").strip()
=======

    client = get_client()

    response = client.models.generate_content(
        model=model or FALLBACK_MODEL,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=temperature,
            max_output_tokens=max_tokens,
            response_mime_type="application/json",
        ),
    )

    raw = (response.text or "").strip()

    if not raw:
        raise ValueError("Gemini returned an empty response.")

>>>>>>> origin/main
    cleaned = raw.replace("```json", "").replace("```", "").strip()

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Gemini did not return valid JSON: {raw[:500]}"
        ) from e

    if not isinstance(result, dict):
        raise ValueError(
            f"Gemini returned JSON but not an object: {type(result).__name__}"
        )

    return result
PY
