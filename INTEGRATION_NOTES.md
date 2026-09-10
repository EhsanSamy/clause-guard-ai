# Integration notes

## Where these files go
Drop these into your repo, replacing the empty `api/` and `ui/` placeholders:
```
clause-guard-ai/
├── api/
│   ├── __init__.py
│   ├── main.py          # FastAPI app
│   ├── case_store.py    # JSON persistence
│   └── graph_runner.py  # adapter to agent/graph.py  <-- needs your input, see below
└── ui/
    └── app.py            # Streamlit app
```
Then merge `requirements_additions.txt` into your `requirements.txt`.

## What's fully wired
- Upload flow: `POST /cases` (PDF only) → saves file, extracts text with `pypdf`,
  returns `{case_id}` immediately.
- Background job: FastAPI `BackgroundTasks` runs the graph without blocking the request.
- Storage: one `results/{case_id}.json` file per case, written by `api/case_store.py`.
- `GET /cases/{case_id}` for polling, `GET /cases` for the history sidebar.
- Streamlit: 4 screens exactly as we discussed — Upload → Processing (polls every ~2.5s)
  → Report (risk badge, human-review banner, clause table, guardrail notices,
  JSON download) → History sidebar (click a past case to reopen its report).

## What I could not verify (GitHub blocked me from browsing into subfolders)
`api/graph_runner.py` is the one file that's guessing:
- how to import/build your compiled graph from `agent/graph.py`
- the real field names on `GraphState` (`agent/state.py`)
- the exact shape of the final node output (`nodes/decide_and_route.py`,
  `nodes/score_risk.py`, `tools/report_generator.py`)

Every guess is marked `ADAPT_ME` in that file. Send me `agent/state.py` and
`agent/graph.py` (and whichever node produces the final report dict) and I'll
replace the guessed parts with the exact field names so `overall_risk`,
`human_review_required`, `clauses`, and `guardrail_notices` line up with what
your graph actually returns.

## Also worth confirming
- `nodes/tag_clauses.py` — is this new node already wired into `agent/graph.py`,
  or does `graph_runner.py` need to call it separately before/after `graph.invoke()`?
- Whether prompt-injection guardrail rejections (like the one in your VS Code
  screenshot) come back as a normal case result (`status: "failed"` /
  a `guardrail_notices` entry) or raise before the graph even starts — the API
  currently treats any exception from `graph_runner.run_case` as a `"failed"`
  case, which should catch that, but worth double-checking against
  `guardrails/injection_guardrails.py`.
