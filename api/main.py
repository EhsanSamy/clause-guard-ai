"""
Clause Guard AI — FastAPI backend.

Endpoints:

    GET /health
        Liveness check.

    POST /cases
        Upload a PDF contract, start a background job,
        and return {case_id}.

    GET /cases/{case_id}
        Fetch the case record.

    GET /cases
        List all cases for the Streamlit history sidebar.

    DELETE /cases/{case_id}
        Delete one case and its uploaded PDF.

    DELETE /cases
        Delete all cases and their uploaded PDFs.

    POST /cases/{case_id}/retry
        Retry the analysis of an existing case using
        its original uploaded PDF.

Run with:

    uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    HTTPException,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from pypdf import PdfReader

from api import case_store, graph_runner


UPLOADS_DIR = (
    Path(__file__).resolve().parent.parent / "uploads"
)
UPLOADS_DIR.mkdir(exist_ok=True)


app = FastAPI(
    title="Clause Guard AI API",
    description=(
        "Contract risk review agent — upload PDFs, "
        "poll case status, and fetch reports."
    ),
    version="1.0.0",
)


# Streamlit runs on a different port during local development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    """
    Lightweight liveness probe.
    """
    return {
        "status": "ok",
        "service": "clause-guard-ai",
        "version": "1.0.0",
    }


@app.post("/cases")
async def create_case(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
) -> dict:
    """
    Upload a PDF contract and start the review pipeline
    in the background.
    """

    # --------------------------------------------------------------
    # Validate file type
    # --------------------------------------------------------------
    if file.content_type != "application/pdf" and not (
        file.filename
        and file.filename.lower().endswith(".pdf")
    ):
        raise HTTPException(
            status_code=400,
            detail="Only PDF uploads are supported right now.",
        )

    # --------------------------------------------------------------
    # Create case
    # --------------------------------------------------------------
    case_id = uuid.uuid4().hex[:12]

    pdf_path = UPLOADS_DIR / f"{case_id}.pdf"

    # Save uploaded PDF.
    pdf_path.write_bytes(await file.read())

    # Create initial case record.
    case_store.create_case(
        case_id,
        filename=file.filename or f"{case_id}.pdf",
    )

    # Start background processing.
    background_tasks.add_task(
        _process_case,
        case_id,
        pdf_path,
    )

    return {
        "case_id": case_id,
    }


@app.get("/cases/{case_id}")
async def get_case(case_id: str) -> dict:
    """
    Return the complete case record.

    The record contains both job status and analysis status,
    for example:

        status = "completed"
        analysis_status = "partial"

    This distinction is important for the UI.
    """
    record = case_store.get_case(case_id)

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="Case not found.",
        )

    return record


@app.get("/cases")
async def list_cases() -> list[dict]:
    """
    Return lightweight case summaries for the history sidebar.
    """
    return case_store.list_cases()


@app.delete("/cases/{case_id}")
async def delete_case(case_id: str) -> dict:
    """
    Delete one case from the history and remove its uploaded PDF.
    """

    # Check that the case exists first.
    record = case_store.get_case(case_id)

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="Case not found.",
        )

    # Delete the persisted JSON case record.
    deleted = case_store.delete_case(case_id)

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Case not found.",
        )

    # Delete the uploaded PDF if it exists.
    pdf_path = UPLOADS_DIR / f"{case_id}.pdf"

    if pdf_path.exists():
        pdf_path.unlink()

    return {
        "case_id": case_id,
        "deleted": True,
        "message": "Case deleted successfully.",
    }


@app.delete("/cases")
async def delete_all_cases() -> dict:
    """
    Delete all cases from the history and remove
    their uploaded PDFs.
    """

    # Get existing cases before deleting their JSON records
    # so we know which uploaded PDFs belong to them.
    cases = case_store.list_cases()

    # Delete all JSON case records.
    deleted_count = case_store.clear_cases()

    # Delete the uploaded PDFs belonging to those cases.
    pdfs_deleted = 0

    for case in cases:
        case_id = case.get("case_id")

        if not case_id:
            continue

        pdf_path = UPLOADS_DIR / f"{case_id}.pdf"

        if pdf_path.exists():
            pdf_path.unlink()
            pdfs_deleted += 1

    return {
        "deleted_cases": deleted_count,
        "deleted_pdfs": pdfs_deleted,
        "message": "Case history cleared successfully.",
    }


@app.post("/cases/{case_id}/retry")
async def retry_case(
    case_id: str,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Retry the analysis of an existing case using
    its original uploaded PDF.
    """

    # --------------------------------------------------------------
    # Check that the case exists
    # --------------------------------------------------------------
    record = case_store.get_case(case_id)

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="Case not found.",
        )

    # --------------------------------------------------------------
    # Check that the original PDF still exists
    # --------------------------------------------------------------
    pdf_path = UPLOADS_DIR / f"{case_id}.pdf"

    if not pdf_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Original uploaded PDF was not found.",
        )

    # --------------------------------------------------------------
    # Prevent duplicate retry while the case is already processing.
    # --------------------------------------------------------------
    if record.get("status") == "processing":
        raise HTTPException(
            status_code=409,
            detail="Case is already being processed.",
        )

    # --------------------------------------------------------------
    # Reset previous analysis state
    # --------------------------------------------------------------
    reset_record = case_store.reset_case_for_retry(case_id)

    if reset_record is None:
        raise HTTPException(
            status_code=404,
            detail="Case not found.",
        )

    # --------------------------------------------------------------
    # Start the pipeline again in the background.
    # --------------------------------------------------------------
    background_tasks.add_task(
        _process_case,
        case_id,
        pdf_path,
    )

    return {
        "case_id": case_id,
        "status": "processing",
        "message": "Case analysis restarted.",
    }


def _process_case(
    case_id: str,
    pdf_path: Path,
) -> None:
    """
    Background processing pipeline:

        PDF
         ↓
        text extraction
         ↓
        LangGraph
         ↓
        persist result
    """

    try:
        # ----------------------------------------------------------
        # 1. Extract PDF text
        # ----------------------------------------------------------
        contract_text = _extract_pdf_text(
            pdf_path
        )

        # ----------------------------------------------------------
        # 2. Run LangGraph
        # ----------------------------------------------------------
        result = graph_runner.run_case(
            case_id,
            contract_text,
        )

        # ----------------------------------------------------------
        # 3. Persist graph result
        #
        # IMPORTANT:
        # save_success() does not necessarily mean that the
        # contract analysis was successful.
        #
        # graph_runner may return:
        #
        #   analysis_status = "completed"
        #
        # or:
        #
        #   analysis_status = "partial"
        #
        # or:
        #
        #   analysis_status = "blocked"
        # ----------------------------------------------------------
        case_store.save_success(
            case_id,
            result,
        )

    except Exception as exc:  # noqa: BLE001
        # Unexpected backend failure.
        #
        # This is different from a partial analysis returned by
        # graph_runner.
        case_store.save_failure(
            case_id,
            str(exc),
        )


def _extract_pdf_text(
    pdf_path: Path,
) -> str:
    """
    Extract text from every page of the uploaded PDF.
    """
    reader = PdfReader(
        str(pdf_path)
    )

    text = "\n\n".join(
        page.extract_text() or ""
        for page in reader.pages
    )

    return text
