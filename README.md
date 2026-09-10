# Clause Guard AI

**Contract Risk Review Agent**

An agentic AI system that reviews commercial contracts, extracts key clauses, scores them against an internal playbook, applies guardrails, and produces structured risk reports with optional human-review escalation.

Built with **LangGraph**, **Gemini**, **LlamaIndex**, **FastAPI**, and **Streamlit**.

---

## Overview

Clause Guard AI ingests a contract (PDF), runs it through a multi-node LangGraph pipeline, and returns a clause-by-clause risk assessment.

### Pipeline flow

```
validate_input
    → document_processing → classify_contract → extract_clauses
    → guardrail_check
         ├─ blocked  → decide_and_route → END
         └─ continue → retrieve_playbook_rules → score_risk
                       → reflect → decide_and_route → END
```

### What it does

| Step              | Description                                                |
| ----------------- | ---------------------------------------------------------- |
| Validate input    | Rejects empty, unreadable, or unsupported uploads          |
| Process document  | Extracts clean text from the PDF                           |
| Classify contract | Identifies contract type (NDA, MSA, etc.)                  |
| Extract clauses   | Pulls and tags clauses into 8 controlled categories        |
| Guardrail check   | Detects prompt injection and unsafe requests               |
| Retrieve playbook | Matches each clause to internal playbook rules             |
| Score risk        | Assigns Low / Medium / High / Critical per clause          |
| Reflect           | Re-evaluates scores for consistency                        |
| Decide & route    | Computes overall risk and flags cases needing human review |

### Clause categories (locked vocabulary)

- indemnification
- limitation_of_liability
- auto_renewal
- termination
- payment_terms
- governing_law
- confidentiality
- ip_assignment

---

## Project structure

```
clause-guard-ai/
├── agent/                  # LangGraph definition, state, config
│   ├── graph.py
│   ├── state.py
│   └── config.py
├── nodes/                  # One file per graph node
├── tools/                  # LLM client, retriever, report generator
├── guardrails/             # Injection and safety checks
├── retrieval/
│   └── knowledge_base/     # Curated playbook (not scraped legal text)
├── schemas/                # Pydantic models (Case, Clause, Report)
├── prompts/                # Prompt templates (kept separate from logic)
├── api/                    # FastAPI backend
├── ui/                     # Streamlit frontend
├── docs/                   # Schema, risk rubric, guardrails
├── tests/
├── run_demo.py             # Offline smoke test (no API/UI)
├── requirements.txt
└── requirements_additions.txt
```

---

## Prerequisites

- Python 3.10+
- A Gemini API key (`GEMINI_API_KEY`)

---

## Setup

```bash
# Clone and enter the project
cd clause-guard-ai

# Create and activate a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```env
GEMINI_API_KEY=your_key_here

# Optional model overrides
SCORING_MODEL=gemini-3.6-flash
REFLECTION_MODEL=gemini-3.8-flash
REPORT_MODEL=gemini-3.8-flash
```

> Never commit a real `.env`. Use `.env.example` for placeholders only.

---

## Running the system

### 1. Offline demo (no server)

Runs the full graph on sample contracts (happy path + edge cases):

```bash
python run_demo.py
```

### 2. API + UI (recommended for interactive testing)

**Terminal 1 — FastAPI backend**

```bash
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

**Terminal 2 — Streamlit UI**

```bash
streamlit run ui/app.py
```

Open the UI at [http://localhost:8501](http://localhost:8501).
The UI expects the API at `http://localhost:8000` (override with `BACKEND_URL` if needed).

### API endpoints

| Method | Path             | Description                       |
| ------ | ---------------- | --------------------------------- |
| POST   | /cases           | Upload a PDF → returns {case_id} |
| GET    | /cases/{case_id} | Poll status and result            |
| GET    | /cases           | List all cases (history sidebar)  |

---

## Risk levels

| Level    | Meaning                                        |
| -------- | ---------------------------------------------- |
| Low      | Matches or is more favorable than the playbook |
| Medium   | Common negotiable deviation — flag for review |
| High     | Meaningful exposure beyond playbook tolerance  |
| Critical | Severe risk; mandatory human sign-off          |

Overall case risk and routing (report / notify / escalate) are decided in `nodes/decide_and_route.py` according to the thresholds in `agent/config.py` and `docs/risk_rubric.md`.

---

## Guardrails

| Guardrail               | Behavior                                                      |
| ----------------------- | ------------------------------------------------------------- |
| Invalid / missing input | Short-circuits to error response                              |
| Prompt injection        | Blocks the run; never treats injected text as instructions    |
| Unsafe request          | Blocks out-of-scope or binding-action requests                |
| No playbook match       | Clause tagged other / flagged; scoring notes lack of coverage |

See `docs/guardrails.md` for full trigger and field details.

---

## Documentation

| File                | Purpose                                      |
| ------------------- | -------------------------------------------- |
| docs/schema.md      | Shared state schema (single source of truth) |
| docs/risk_rubric.md | Low → Critical definitions and examples     |
| docs/guardrails.md  | Guardrail triggers and handling              |
|                     |                                              |

---

## Development notes

- Prompts live in `prompts/` — do not hardcode them inside node files.
- Playbook content in `retrieval/knowledge_base/` is team-written summaries only (no scraped case law).
- API keys stay in environment variables; only placeholders belong in the repo.
- Each node owner owns tests for their node; edge-case tests are shared.
