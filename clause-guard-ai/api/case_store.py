"""
Simple JSON-file-based persistence for Clause Guard AI cases.

Each case is one file: results/{case_id}.json
This keeps things dead simple for the demo (matches the team's existing
GraphState -> JSON pattern) while still being safe for concurrent
background-task writes via a per-process lock.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)

_lock = threading.Lock()


def _path(case_id: str) -> Path:
    return RESULTS_DIR / f"{case_id}.json"


def create_case(case_id: str, filename: str) -> dict[str, Any]:
    """Write the initial 'processing' record as soon as the upload lands."""
    record = {
        "case_id": case_id,
        "filename": filename,
        "status": "processing",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _write(case_id, record)
    return record


def save_success(case_id: str, graph_result: dict[str, Any]) -> dict[str, Any]:
    record = get_case(case_id) or {"case_id": case_id}
    record.update(graph_result)
    record["status"] = "completed"
    record["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write(case_id, record)
    return record


def save_failure(case_id: str, error: str) -> dict[str, Any]:
    record = get_case(case_id) or {"case_id": case_id}
    record["status"] = "failed"
    record["error"] = error
    record["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write(case_id, record)
    return record


def get_case(case_id: str) -> dict[str, Any] | None:
    path = _path(case_id)
    if not path.exists():
        return None
    with _lock:
        return json.loads(path.read_text(encoding="utf-8"))


def list_cases() -> list[dict[str, Any]]:
    """Lightweight summaries for the history sidebar, newest first."""
    summaries = []
    for path in RESULTS_DIR.glob("*.json"):
        with _lock:
            data = json.loads(path.read_text(encoding="utf-8"))
        summaries.append(
            {
                "case_id": data.get("case_id"),
                "filename": data.get("filename"),
                "status": data.get("status"),
                "overall_risk": data.get("overall_risk"),
                "human_review_required": data.get("human_review_required"),
                "created_at": data.get("created_at"),
            }
        )
    summaries.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return summaries


def _write(case_id: str, record: dict[str, Any]) -> None:
    with _lock:
        _path(case_id).write_text(
            json.dumps(record, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
