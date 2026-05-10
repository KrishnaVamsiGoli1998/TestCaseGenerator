"""
main.py — FastAPI Application
Exposes the AI test generation pipeline as a REST API.
Supports: single file and multi-file upload for both Python and JavaScript.
"""
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")
TEST_OUTPUT_DIR = os.getenv("TEST_OUTPUT_DIR", "generated_tests")
MAX_FILE_SIZE_KB = int(os.getenv("MAX_FILE_SIZE_KB", "500"))
MAX_FILES = int(os.getenv("MAX_FILES_PER_SESSION", "10"))

for d in (UPLOAD_DIR, TEST_OUTPUT_DIR, "temp"):
    os.makedirs(d, exist_ok=True)

app = FastAPI(
    title="AI Test Case Generator",
    description="Automatic pytest/Jest test generation using Claude AI. Supports Python & JavaScript (single file and multi-file).",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_frontend_dir = Path(__file__).parent.parent / "frontend"
if _frontend_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_frontend_dir)), name="static")


# ── Helper ────────────────────────────────────────────────────────────────────

def _allowed_ext(filename: str, language: str) -> bool:
    ext = Path(filename).suffix.lower()
    if language == "javascript":
        return ext in (".js", ".mjs")
    return ext == ".py"


def _save_upload(content: bytes, filename: str, session_dir: str) -> str:
    safe = Path(filename).name
    dest = os.path.join(session_dir, safe)
    with open(dest, "wb") as f:
        f.write(content)
    return dest


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def root():
    index = _frontend_dir / "index.html"
    if index.exists():
        return HTMLResponse(content=index.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>AI Test Generator v2</h1><a href='/docs'>API Docs</a>")


@app.get("/health")
def health():
    return {"status": "ok", "version": "2.0.0"}


@app.post("/generate", summary="Upload one or more source files and generate tests")
async def generate(
    files: list[UploadFile] = File(...),
    language: str = Form("python"),
):
    """
    Accept one or more .py / .js source files, run the full AI test pipeline, return results.
    language: 'python' (default) or 'javascript'
    """
    if language not in ("python", "javascript"):
        raise HTTPException(status_code=400, detail="language must be 'python' or 'javascript'")

    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Maximum {MAX_FILES} files per session")

    session_id = uuid.uuid4().hex[:8]
    session_dir = os.path.join(UPLOAD_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)

    saved_paths = []
    for upload in files:
        if not upload.filename:
            continue
        if not _allowed_ext(upload.filename, language):
            ext = ".py" if language == "python" else ".js"
            raise HTTPException(status_code=400, detail=f"Only {ext} files are accepted for {language}")

        content = await upload.read()
        if len(content) > MAX_FILE_SIZE_KB * 1024:
            raise HTTPException(status_code=400, detail=f"'{upload.filename}' exceeds {MAX_FILE_SIZE_KB} KB limit")

        saved_paths.append(_save_upload(content, upload.filename, session_dir))

    if not saved_paths:
        raise HTTPException(status_code=400, detail="No valid files were uploaded")

    from .feedback_loop import run_pipeline
    try:
        results = run_pipeline(saved_paths, language=language)
        return JSONResponse(content=results)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Pipeline error: {exc}") from exc



@app.get("/download/{filename}", summary="Download a generated test file")
def download(filename: str):
    safe = Path(filename).name
    path = os.path.join(TEST_OUTPUT_DIR, safe)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Test file not found")
    return FileResponse(path=path, filename=safe, media_type="application/octet-stream")
