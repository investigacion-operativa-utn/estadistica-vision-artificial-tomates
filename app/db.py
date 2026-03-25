from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Optional

import tempfile

DATA_DIR = Path(tempfile.gettempdir()) / "tomato-cv"
DB_PATH = DATA_DIR / "app.db"


def _get_conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                video_filename TEXT NOT NULL,
                video_path TEXT NOT NULL,
                calibration_mm_per_px REAL,
                sample_every_n_frames INTEGER NOT NULL,
                total_frames INTEGER,
                processed_frames INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS measurements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                track_id INTEGER,
                frame_idx INTEGER NOT NULL,
                timestamp_sec REAL NOT NULL,
                confidence REAL,
                diameter_px REAL NOT NULL,
                diameter_mm REAL,
                x1 REAL NOT NULL,
                y1 REAL NOT NULL,
                x2 REAL NOT NULL,
                y2 REAL NOT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(id)
            );
            """
        )

        cols = conn.execute("PRAGMA table_info(measurements)").fetchall()
        col_names = {row[1] for row in cols}
        if "track_id" not in col_names:
            conn.execute("ALTER TABLE measurements ADD COLUMN track_id INTEGER")


def create_job(payload: dict[str, Any]) -> None:
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                id, status, error, created_at, updated_at,
                video_filename, video_path, calibration_mm_per_px,
                sample_every_n_frames, total_frames, processed_frames
            )
            VALUES (
                :id, :status, :error, :created_at, :updated_at,
                :video_filename, :video_path, :calibration_mm_per_px,
                :sample_every_n_frames, :total_frames, :processed_frames
            )
            """,
            payload,
        )


def update_job(job_id: str, **fields: Any) -> None:
    if not fields:
        return

    allowed = {
        "status",
        "error",
        "updated_at",
        "total_frames",
        "processed_frames",
    }
    data = {k: v for k, v in fields.items() if k in allowed}
    if not data:
        return

    assignments = ", ".join([f"{k} = :{k}" for k in data])
    data["id"] = job_id

    with _get_conn() as conn:
        conn.execute(
            f"UPDATE jobs SET {assignments} WHERE id = :id",
            data,
        )


def get_job(job_id: str) -> Optional[dict[str, Any]]:
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def get_latest_job_for_video(video_path: str) -> Optional[dict[str, Any]]:
    with _get_conn() as conn:
        row = conn.execute(
            """
            SELECT * FROM jobs
            WHERE video_path = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (video_path,),
        ).fetchone()
    return dict(row) if row else None


def add_measurements(job_id: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with _get_conn() as conn:
        conn.executemany(
            """
            INSERT INTO measurements (
                job_id, track_id, frame_idx, timestamp_sec, confidence,
                diameter_px, diameter_mm, x1, y1, x2, y2
            )
            VALUES (
                :job_id, :track_id, :frame_idx, :timestamp_sec, :confidence,
                :diameter_px, :diameter_mm, :x1, :y1, :x2, :y2
            )
            """,
            [{"job_id": job_id, **row} for row in rows],
        )


def get_measurements(job_id: str, limit: int = 1000) -> list[dict[str, Any]]:
    with _get_conn() as conn:
        query = (
            "SELECT * FROM measurements WHERE job_id = ? "
            "ORDER BY frame_idx ASC, id ASC LIMIT ?"
        )
        rows = conn.execute(query, (job_id, limit)).fetchall()
    return [dict(r) for r in rows]


def get_summary(job_id: str) -> dict[str, Any]:
    with _get_conn() as conn:
        values = conn.execute(
            "SELECT diameter_px, diameter_mm FROM measurements WHERE job_id = ?",
            (job_id,),
        ).fetchall()

    diameter_px = [float(r["diameter_px"]) for r in values]
    diameter_mm = [float(r["diameter_mm"]) for r in values if r["diameter_mm"] is not None]

    def _stats(numbers: list[float]) -> dict[str, Optional[float]]:
        n = len(numbers)
        if n == 0:
            return {"mean": None, "std_dev": None}
        mean = sum(numbers) / n
        variance = sum((x - mean) ** 2 for x in numbers) / n
        return {"mean": mean, "std_dev": variance ** 0.5}

    return {
        "count": len(diameter_px),
        "diameter_px": _stats(diameter_px),
        "diameter_mm": _stats(diameter_mm),
    }
