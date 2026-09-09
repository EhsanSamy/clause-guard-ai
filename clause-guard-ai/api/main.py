"""
Clause Guard AI — FastAPI backend.

Endpoints:
    POST /cases        upload a PDF contract, kicks off a background job, returns {case_id}
    GET  /cases/{id}    fetch the case record (status: processing | completed | failed)
    GET  /cases         list all cases (for the Streamlit history sidebar)

Run with:
    uvicorn api.main:app --reload
"""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pypdf import PdfReader

from api import case_store, graph_runner

UPLOADS_DIR = Path(__file__).resolve().parent.parent / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Clause Guard AI API")

# Streamlit runs on a different port during local dev.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/cases")
async def create_case(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    if file.content_type != "application/pdf" and not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF uploads are supported right now.")

    case_id = uuid.uuid4().hex[:12]
    pdf_path = UPLOADS_DIR / f"{case_id}.pdf"
    pdf_path.write_bytes(await file.read())

    case_store.create_case(case_id, filename=file.filename)
    background_tasks.add_task(_process_case, case_id, pdf_path)

    return {"case_id": case_id}


@app.get("/cases/{case_id}")
async def get_case(case_id: str):
    record = case_store.get_case(case_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Case not found.")
    return record


@app.get("/cases")
async def list_cases():
    return case_store.list_cases()


def _process_case(case_id: str, pdf_path: Path) -> None:
    """Runs in the background: extract text -> run the graph -> persist result."""
    try:
        contract_text = _extract_pdf_text(pdf_path)
        result = graph_runner.run_case(case_id, contract_text)
        case_store.save_success(case_id, result)
    except Exception as exc:  # noqa: BLE001 — surface any failure to the UI
        case_store.save_failure(case_id, str(exc))


def _extract_pdf_text(pdf_path: Path) -> str:
    reader = PdfReader(str(pdf_path))
    return "\n\n".join(page.extract_text() or "" for page in reader.pages)
