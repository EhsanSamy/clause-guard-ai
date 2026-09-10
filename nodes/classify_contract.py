"""
Person 1 — node: classify_contract
Owns: contract_type

Uses agent.config.MODEL.classification_model so the actual model string
is configured in one place (agent/config.py) rather than hardcoded here.
That field is still blank ("TODO: Model configuration") as of this
writing — tools/llm_client.py falls back to a default model if so, but
the team should fill in MODEL.classification_model once decided.
"""
from __future__ import annotations
from agent.config import MODEL
from agent.state import GraphState, to_case_state, validate_update
from tools.llm_client import call_json

SUGGESTED_TYPES = [
    "NDA", "MSA", "SOW", "Lease Agreement", "Employment Contract",
    "Supplier Contract", "Service Agreement", "Other",
]

SYSTEM_PROMPT = f"""You are a contract classification assistant for a small-business
contract review tool. Classify the contract text with a short contract_type label.
Prefer one of these common labels when it fits: {", ".join(SUGGESTED_TYPES)}.
If none fit well, return a short descriptive label of your own.

Respond with ONLY a JSON object, no prose, no markdown fences, in this exact shape:
{{"contract_type": "<label>", "confidence": <float 0-1>, "reason": "<one short sentence>"}}
"""


def classify_contract_node(state: GraphState) -> GraphState:
    case = to_case_state(state)
    if not case.input_valid or not case.document_text:
        return {}  # nothing valid to classify yet

    excerpt = case.document_text[:4000]

    try:
        result = call_json(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=f"Contract text:\n\n{excerpt}",
            model=MODEL.classification_model,
            max_tokens=MODEL.max_tokens,
            temperature=MODEL.temperature,
        )
        contract_type = result.get("contract_type")
        if not contract_type:
            raise ValueError(f"Model returned no contract_type: {result}")

        return validate_update({"contract_type": contract_type})

    # classify_contract_node
    except Exception as e:
        return {
        "tool_errors": [f"classify_contract: {e}"],
        "failed_steps": ["classify_contract"],
        }
