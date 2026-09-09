# Shared State Schema — Contract Risk Review Agent

This is the single source of truth for every field that flows through the LangGraph
State object. **Every field name here is locked after Phase 0.** If a node needs a
field that isn't listed, add it here first and get the team to re-confirm before
writing code against it.

Two Pydantic models back this: `schemas/case_schema.py` (the top-level `CaseState`)
and `schemas/clause_schema.py` (`Clause`, nested inside `CaseState.clauses`).

---

## 1. Clause Tag Vocabulary (locked)

Every extracted clause is tagged with exactly one of these 8 categories. This
vocabulary drives extraction (Person 2), the playbook (Person 3), and scoring.
No clause tag may be introduced outside this list without a full-team decision.

1. `indemnification`
2. `limitation_of_liability`
3. `auto_renewal`
4. `termination`
5. `payment_terms`
6. `governing_law`
7. `confidentiality`
8. `ip_assignment`

A clause that doesn't fit any category is tagged `other` and is not scored against
the playbook (see `guardrails.md` — No Playbook Match).

---

## 2. `Clause` (schemas/clause_schema.py)

| Field | Type | Set by | Description |
|---|---|---|---|
| `clause_id` | `str` | document_processing | Stable unique id, e.g. `clause_003` |
| `clause_text` | `str` | extract_clauses | Verbatim text of the clause as extracted from the document |
| `clause_tag` | `Literal[...]` | extract_clauses | One of the 8 categories above, or `other` |
| `source_page` | `int \| None` | extract_clauses | Page number in source doc, if available |
| `retrieved_rule` | `str \| None` | retrieve_playbook_rules | Text of the matched playbook rule |
| `retrieved_rule_found` | `bool` | retrieve_playbook_rules | `False` if no playbook match was found for this tag |
| `risk_score` | `Literal["Low","Medium","High","Critical"] \| None` | score_risk | Set by the scoring node; `None` until scored |
| `risk_rationale` | `str \| None` | score_risk | Must cite `retrieved_rule` when `retrieved_rule_found` is `True`, or explicitly state "no playbook coverage" when `False` |
| `flagged_for_review` | `bool` | score_risk / reflect | `True` for High/Critical clauses |

---

## 3. `CaseState` (schemas/case_schema.py)

The top-level object passed between every LangGraph node.

| Field | Type | Set by | Description |
|---|---|---|---|
| `case_id` | `str` | validate_input | Unique id for this contract review run |
| `raw_input` | `str` | validate_input | Raw uploaded text / file path before processing |
| `input_valid` | `bool` | validate_input | `False` short-circuits the graph to an error-response node |
| `input_error` | `str \| None` | validate_input | Human-readable reason input was rejected |
| `document_text` | `str \| None` | document_processing | Cleaned, parsed full contract text |
| `contract_type` | `str \| None` | classify_contract | e.g. "NDA", "MSA", "SOW" |
| `clauses` | `list[Clause]` | extract_clauses | See `Clause` model above |
| `guardrail_flags` | `list[str]` | guardrail_check | e.g. `["prompt_injection_detected"]` |
| `blocked` | `bool` | guardrail_check | `True` halts the graph and routes to a safe-refusal response |
| `block_reason` | `str \| None` | guardrail_check | Why the run was blocked |
| `overall_risk_score` | `Literal["Low","Medium","High","Critical"] \| None` | decide_and_route | Aggregated from per-clause scores per `risk_rubric.md` |
| `reflection_notes` | `str \| None` | reflect | Self-critique output; may trigger a re-score |
| `revised` | `bool` | reflect | `True` if Reflection changed at least one clause's score |
| `final_report` | `str \| None` | report_generator | Rendered output for the user |
| `route` | `str \| None` | decide_and_route | Which downstream path the Router/Supervisor chose |
| `tool_errors` | `list[str]` | any node | Appended to whenever a tool call fails (see `guardrails.md`) |

---

## 4. Field Ownership Quick Reference

| Field group | Owner |
|---|---|
| `raw_input`, `input_valid`, `input_error`, `document_text`, `contract_type` | Person 1 |
| `clauses[].clause_text`, `clauses[].clause_tag`, `guardrail_flags`, `blocked`, `block_reason` | Person 2 |
| `clauses[].retrieved_rule`, `clauses[].retrieved_rule_found`, `clauses[].risk_score`, `clauses[].risk_rationale` | Person 3 |
| `reflection_notes`, `revised`, `route`, `overall_risk_score`, `final_report` | Person 4 |
| `tool_errors` | Shared — any node that calls a tool |

---

## 5. Change Process

If a node needs a field not listed above:
1. Propose it in the team channel with type + owner.
2. Add it to this file and to the relevant Pydantic model.
3. Re-export updated fixtures in `fixtures/` so everyone's stub data stays in sync.

Do not silently add fields to `CaseState` or `Clause` — the whole point of Phase 0
is that this file is the contract everyone codes against.
