"""
Simple JSON-file-based persistence for Clause Guard AI cases.

Each case is one file:

    results/{case_id}.json

Safe for concurrent background-task writes via a per-process lock.

The store keeps both:

    - job/persistence status
    - detailed analysis status and pipeline progress
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
    """Return the JSON file path for a case."""
    return RESULTS_DIR / f"{case_id}.json"


def _now() -> str:
    """Return the current UTC timestamp as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def create_case(case_id: str, filename: str) -> dict[str, Any]:
    """
    Write the initial processing record as soon as the upload lands.
    """
    now = _now()

    record = {
        "case_id": case_id,
        "filename": filename,

        # Job/persistence status.
        "status": "processing",

        # Detailed analysis status.
        "analysis_status": "pending",

        # Pipeline progress.
        "current_step": "validate_input",
        "completed_steps": [],
        "failed_steps": [],

        # Risk state.
        "risk_assessment_available": False,
        "overall_risk": None,
        "human_review_required": False,

        # Results.
        "clauses": [],
        "guardrail_notices": [],
        "final_report_markdown": None,
        "contract_type": None,
        "route": None,
        "revised": False,

        # Error information.
        "error": None,

        # Timestamps.
        "created_at": now,
        "updated_at": now,
        "completed_at": None,
    }

    _write(case_id, record)
    return record


def update_step(
    case_id: str,
    step: str,
) -> dict[str, Any] | None:
    """
    Persist live pipeline progress so the UI can render
    a real stepper/timeline.
    """
    record = get_case(case_id)

    if record is None:
        return None

    record["current_step"] = step
    record["analysis_status"] = "running"

    completed_steps = list(
        record.get("completed_steps") or []
    )

    if step not in completed_steps:
        completed_steps.append(step)

    record["completed_steps"] = completed_steps
    record["updated_at"] = _now()

    _write(case_id, record)
    return record


def save_success(
    case_id: str,
    graph_result: dict[str, Any],
) -> dict[str, Any]:
    """
    Persist the final graph result.

    A graph result may represent:
        - completed analysis
        - partial analysis
        - blocked analysis

    The detailed `analysis_status` from graph_runner is therefore
    preserved instead of blindly treating every result as successful.
    """
    record = get_case(case_id) or {
        "case_id": case_id
    }

    record.update(graph_result)

    analysis_status = graph_result.get(
        "analysis_status",
        "completed",
    )

    # The background job itself finished running.
    #
    # This does NOT necessarily mean the contract analysis was
    # successful. The UI should use analysis_status for that.
    record["status"] = "completed"
    record["analysis_status"] = analysis_status

    if graph_result.get("current_step"):
        record["current_step"] = graph_result["current_step"]

    # Make sure progress fields always exist.
    record["completed_steps"] = list(
        graph_result.get(
            "completed_steps",
            record.get("completed_steps") or [],
        )
    )

    record["failed_steps"] = list(
        graph_result.get(
            "failed_steps",
            record.get("failed_steps") or [],
        )
    )

    record["risk_assessment_available"] = bool(
        graph_result.get(
            "risk_assessment_available",
            False,
        )
    )

    now = _now()
    record["updated_at"] = now
    record["completed_at"] = now

    _write(case_id, record)
    return record


def save_failure(
    case_id: str,
    error: str,
) -> dict[str, Any]:
    """
    Persist a failure that prevented the graph from completing.

    This is different from a partial analysis returned successfully
    by graph_runner.
    """
    record = get_case(case_id) or {
        "case_id": case_id
    }

    record["status"] = "failed"
    record["analysis_status"] = "failed"
    record["error"] = error

    record["risk_assessment_available"] = False
    record["overall_risk"] = None
    record["human_review_required"] = False

    now = _now()
    record["updated_at"] = now
    record["completed_at"] = now

    _write(case_id, record)
    return record


def delete_case(case_id: str) -> bool:
    """
    Delete a single case from the JSON-based case history.

    Returns:
        True  -> case existed and was deleted.
        False -> case did not exist.
    """
    path = _path(case_id)

    with _lock:
        if not path.exists():
            return False

        path.unlink()
        return True


def clear_cases() -> int:
    """
    Delete all persisted case records from the history.

    Returns:
        The number of case records deleted.
    """
    deleted_count = 0

    with _lock:
        for path in RESULTS_DIR.glob("*.json"):
            if path.is_file():
                path.unlink()
                deleted_count += 1

    return deleted_count


def reset_case_for_retry(
    case_id: str,
) -> dict[str, Any] | None:
    """
    Reset an existing case so its analysis can be run again.

    The case identity and original upload metadata are preserved,
    while previous analysis results and progress are cleared.
    """
    record = get_case(case_id)

    if record is None:
        return None

    now = _now()

    # Preserve case_id, filename, and created_at.
    record["status"] = "processing"
    record["analysis_status"] = "pending"

    # Reset pipeline progress.
    record["current_step"] = "validate_input"
    record["completed_steps"] = []
    record["failed_steps"] = []

    # Reset risk state.
    record["risk_assessment_available"] = False
    record["overall_risk"] = None
    record["human_review_required"] = False

    # Reset analysis results.
    record["clauses"] = []
    record["guardrail_notices"] = []
    record["final_report_markdown"] = None
    record["contract_type"] = None
    record["route"] = None
    record["revised"] = False

    # Reset errors.
    record["error"] = None

    # Reset timestamps related to the current run.
    record["updated_at"] = now
    record["completed_at"] = None

    _write(case_id, record)
    return record


def get_case(
    case_id: str,
) -> dict[str, Any] | None:
    """Load one case from disk."""
    path = _path(case_id)

    if not path.exists():
        return None

    with _lock:
        return json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )


def list_cases() -> list[dict[str, Any]]:
    """
    Return lightweight summaries for the history sidebar,
    newest first.
    """
    summaries: list[dict[str, Any]] = []

    for path in RESULTS_DIR.glob("*.json"):
        with _lock:
            data = json.loads(
                path.read_text(
                    encoding="utf-8"
                )
            )

        summaries.append(
            {
                "case_id": data.get("case_id"),
                "filename": data.get("filename"),
                "status": data.get("status"),

                # Analysis information.
                "analysis_status": data.get(
                    "analysis_status"
                ),
                "risk_assessment_available": data.get(
                    "risk_assessment_available",
                    False,
                ),
                "overall_risk": data.get(
                    "overall_risk"
                ),
                "human_review_required": data.get(
                    "human_review_required"
                ),

                # Pipeline progress.
                "current_step": data.get(
                    "current_step"
                ),
                "completed_steps": data.get(
                    "completed_steps",
                    [],
                ),
                "failed_steps": data.get(
                    "failed_steps",
                    [],
                ),

                "created_at": data.get(
                    "created_at"
                ),
                "updated_at": data.get(
                    "updated_at"
                ),
                "completed_at": data.get(
                    "completed_at"
                ),
            }
        )

    summaries.sort(
        key=lambda record: (
            record.get("created_at") or ""
        ),
        reverse=True,
    )

    return summaries


def _write(
    case_id: str,
    record: dict[str, Any],
) -> None:
    """Atomically write a case record to disk."""
    with _lock:
        _path(case_id).write_text(
            json.dumps(
                record,
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
