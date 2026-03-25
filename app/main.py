from __future__ import annotations

import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional
from uuid import uuid4

import aiofiles
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from app import db
from app.pipeline import process_job

MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB

app = FastAPI(title="Tomato Stats CV", docs_url=None, redoc_url=None)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response


app.add_middleware(SecurityHeadersMiddleware)

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = Path(tempfile.gettempdir()) / "tomato-cv" / "uploads"
STATIC_DIR = BASE_DIR / "app" / "static"
TEMPLATE_DIR = BASE_DIR / "app" / "templates"
DEFAULT_VIDEO_FILENAME = "kling_20260325_作品_Genera_un__668_0.mp4"
DEFAULT_VIDEO_PATH = BASE_DIR / DEFAULT_VIDEO_FILENAME


def _sanitize_filename(name: str) -> str:
    """Strip path separators and non-safe characters from a filename."""
    name = Path(name).name  # strip directory components
    name = re.sub(r"[^\w.\-]", "_", name)
    return name or "video.mp4"

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATE_DIR)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@app.on_event("startup")
def on_startup() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    db.init_db()


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="index.html")


@app.post("/api/jobs")
async def create_job(
    background_tasks: BackgroundTasks,
    video: UploadFile = File(...),
    sample_every_n_frames: int = Form(default=1),
    calibration_mm_per_px: Optional[float] = Form(default=None),
) -> dict:
    if sample_every_n_frames < 1 or sample_every_n_frames > 60:
        raise HTTPException(status_code=400, detail="sample_every_n_frames debe estar entre 1 y 60")

    if not video.filename:
        raise HTTPException(status_code=400, detail="Archivo inválido")

    if not video.filename.lower().endswith((".mp4", ".mov", ".avi", ".mkv")):
        raise HTTPException(status_code=400, detail="Formato no soportado")

    job_id = str(uuid4())
    safe_name = _sanitize_filename(video.filename)
    filename = f"{job_id}-{safe_name}"
    destination = UPLOAD_DIR / filename

    total_written = 0
    async with aiofiles.open(destination, "wb") as out_file:
        while chunk := await video.read(1024 * 1024):
            total_written += len(chunk)
            if total_written > MAX_UPLOAD_BYTES:
                await out_file.close()
                destination.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="El video excede el límite de 100 MB")
            await out_file.write(chunk)

    payload = {
        "id": job_id,
        "status": "uploaded",
        "error": None,
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
        "video_filename": safe_name,
        "video_path": str(destination),
        "calibration_mm_per_px": calibration_mm_per_px,
        "sample_every_n_frames": sample_every_n_frames,
        "total_frames": None,
        "processed_frames": 0,
    }
    db.create_job(payload)
    background_tasks.add_task(process_job, job_id)

    return {
        "job_id": job_id,
        "status": payload["status"],
        "video_url": f"/api/jobs/{job_id}/video",
    }


@app.post("/api/jobs/default")
def create_or_get_default_job(background_tasks: BackgroundTasks, force: bool = False) -> dict:
    if not DEFAULT_VIDEO_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No se encontró el video por defecto: {DEFAULT_VIDEO_FILENAME}",
        )

    existing = db.get_latest_job_for_video(str(DEFAULT_VIDEO_PATH))
    if (not force) and existing and existing.get("status") in {"uploaded", "processing", "completed"}:
        return {
            "job_id": existing["id"],
            "status": existing["status"],
            "video_url": f"/api/jobs/{existing['id']}/video",
            "default_video": True,
        }

    job_id = str(uuid4())
    payload = {
        "id": job_id,
        "status": "uploaded",
        "error": None,
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
        "video_filename": DEFAULT_VIDEO_FILENAME,
        "video_path": str(DEFAULT_VIDEO_PATH),
        "calibration_mm_per_px": None,
        "sample_every_n_frames": 1,
        "total_frames": None,
        "processed_frames": 0,
    }
    db.create_job(payload)
    background_tasks.add_task(process_job, job_id)

    return {
        "job_id": job_id,
        "status": payload["status"],
        "video_url": f"/api/jobs/{job_id}/video",
        "default_video": True,
    }


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job no encontrado")

    total_frames = job.get("total_frames")
    processed_frames = job.get("processed_frames") or 0
    progress = None

    if total_frames and total_frames > 0:
        progress = min(100.0, round((processed_frames / total_frames) * 100.0, 2))

    return {
        **job,
        "progress": progress,
    }


@app.get("/api/jobs/{job_id}/measurements")
def get_measurements(job_id: str, limit: int = 1000) -> dict:
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job no encontrado")

    limit = max(1, min(limit, 10000))
    measurements = db.get_measurements(job_id, limit=limit)
    return {
        "job_id": job_id,
        "limit": limit,
        "items": measurements,
    }


@app.get("/api/jobs/{job_id}/summary")
def get_summary(job_id: str) -> dict:
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job no encontrado")

    summary = db.get_summary(job_id)
    return {
        "job_id": job_id,
        **summary,
    }


@app.get("/api/jobs/{job_id}/video")
def get_video(job_id: str) -> FileResponse:
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job no encontrado")

    video_path = Path(job["video_path"]).resolve()
    allowed_dirs = (UPLOAD_DIR.resolve(), BASE_DIR.resolve())
    if not any(str(video_path).startswith(str(d)) for d in allowed_dirs):
        raise HTTPException(status_code=403, detail="Acceso denegado")
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video no disponible")

    return FileResponse(path=video_path, filename=job["video_filename"], media_type="video/mp4")


@app.get("/api/jobs/{job_id}/csv")
def download_csv(job_id: str) -> StreamingResponse:
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job no encontrado")

    rows = db.get_measurements(job_id, limit=100000)

    def _iter_csv() -> Iterator[bytes]:
        header = (
            "track_id,frame_idx,timestamp_sec,confidence,diameter_px,diameter_mm,"
            "x1,y1,x2,y2\n"
        )
        yield header.encode("utf-8")
        for row in rows:
            line = (
                f"{row.get('track_id')},{row['frame_idx']},{row['timestamp_sec']},{row['confidence']},"
                f"{row['diameter_px']},{row['diameter_mm']},"
                f"{row['x1']},{row['y1']},{row['x2']},{row['y2']}\n"
            )
            yield line.encode("utf-8")

    return StreamingResponse(
        _iter_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={job_id}.csv"},
    )
