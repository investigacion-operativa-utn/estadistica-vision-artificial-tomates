from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Union

from math import hypot

import cv2

from app import db
from app.detector import TomatoDetector

_detector: Optional[TomatoDetector] = None


def _iou(box_a: tuple[float, float, float, float], box_b: tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b

    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)

    inter_w = max(0.0, inter_x2 - inter_x1)
    inter_h = max(0.0, inter_y2 - inter_y1)
    inter_area = inter_w * inter_h

    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    denom = area_a + area_b - inter_area
    if denom <= 0:
        return 0.0
    return inter_area / denom


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _center_from_box(box: tuple[float, float, float, float]) -> tuple[float, float]:
    x1, y1, x2, y2 = box
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def _box_size(box: tuple[float, float, float, float]) -> tuple[float, float]:
    x1, y1, x2, y2 = box
    return (max(0.0, x2 - x1), max(0.0, y2 - y1))


def _predict_box(track: dict[str, Union[float, int]], frame_idx: int) -> tuple[float, float, float, float]:
    last_frame = int(track["last_frame"])
    gap = max(1, frame_idx - last_frame)
    vx = float(track.get("vx", 0.0))
    vy = float(track.get("vy", 0.0))
    dx = vx * gap
    dy = vy * gap
    return (
        float(track["x1"]) + dx,
        float(track["y1"]) + dy,
        float(track["x2"]) + dx,
        float(track["y2"]) + dy,
    )


def _match_score(
    track: dict[str, Union[float, int]],
    detection_box: tuple[float, float, float, float],
    frame_idx: int,
) -> float:
    predicted_box = _predict_box(track, frame_idx)
    iou_score = _iou(predicted_box, detection_box)

    predicted_center = _center_from_box(predicted_box)
    detection_center = _center_from_box(detection_box)
    distance = hypot(detection_center[0] - predicted_center[0], detection_center[1] - predicted_center[1])

    pred_w, pred_h = _box_size(predicted_box)
    det_w, det_h = _box_size(detection_box)
    width_ratio = min(pred_w, det_w) / max(pred_w, det_w, 1.0)
    height_ratio = min(pred_h, det_h) / max(pred_h, det_h, 1.0)
    size_score = width_ratio * height_ratio

    frame_gap = max(1, frame_idx - int(track["last_frame"]))
    max_distance = max(24.0, (0.55 * max(pred_w, pred_h)) + (frame_gap * 14.0))
    if distance > max_distance:
        return 0.0

    direction_vx = float(track.get("vx", 0.0))
    if direction_vx < -1.0 and (detection_center[0] - predicted_center[0]) > max(16.0, pred_w * 0.45):
        return 0.0

    if size_score < 0.18:
        return 0.0

    distance_score = max(0.0, 1.0 - (distance / max_distance))
    if iou_score < 0.02 and distance_score < 0.55:
        return 0.0

    score = (0.5 * iou_score) + (0.35 * distance_score) + (0.15 * size_score)
    return score


def _create_track(det: Any, frame_idx: int) -> dict[str, Union[float, int]]:
    center_x = (det.x1 + det.x2) / 2.0
    center_y = (det.y1 + det.y2) / 2.0
    return {
        "x1": det.x1,
        "y1": det.y1,
        "x2": det.x2,
        "y2": det.y2,
        "center_x": center_x,
        "center_y": center_y,
        "vx": 0.0,
        "vy": 0.0,
        "hits": 1,
        "first_frame": frame_idx,
        "last_frame": frame_idx,
    }


def _update_track(track: dict[str, Union[float, int]], det: Any, frame_idx: int) -> None:
    previous_center_x = float(track["center_x"])
    previous_center_y = float(track["center_y"])
    previous_frame = int(track["last_frame"])
    frame_gap = max(1, frame_idx - previous_frame)

    center_x = (det.x1 + det.x2) / 2.0
    center_y = (det.y1 + det.y2) / 2.0
    instant_vx = (center_x - previous_center_x) / frame_gap
    instant_vy = (center_y - previous_center_y) / frame_gap

    track["vx"] = (0.65 * float(track.get("vx", 0.0))) + (0.35 * instant_vx)
    track["vy"] = (0.65 * float(track.get("vy", 0.0))) + (0.35 * instant_vy)
    track["x1"] = det.x1
    track["y1"] = det.y1
    track["x2"] = det.x2
    track["y2"] = det.y2
    track["center_x"] = center_x
    track["center_y"] = center_y
    track["last_frame"] = frame_idx
    track["hits"] = int(track.get("hits", 0)) + 1


def _get_detector() -> TomatoDetector:
    global _detector
    if _detector is None:
        _detector = TomatoDetector()
    return _detector


def process_job(job_id: str) -> None:
    job = db.get_job(job_id)
    if job is None:
        return

    try:
        db.update_job(job_id, status="processing", updated_at=_utc_now())

        video_path = job["video_path"]
        sample_every = int(job["sample_every_n_frames"])
        mm_per_px = job["calibration_mm_per_px"]

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise RuntimeError("No se pudo abrir el video")

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)

        db.update_job(
            job_id,
            total_frames=total_frames,
            processed_frames=0,
            updated_at=_utc_now(),
        )

        detector = _get_detector()
        frame_idx = 0
        pending_rows: list[dict[str, Union[float, int, None]]] = []
        tracks: dict[int, dict[str, Union[float, int]]] = {}
        next_track_id = 1
        max_track_gap = max(sample_every * 5, 6)
        min_match_score = 0.24
        min_track_hits_to_persist = 3

        while True:
            ok, frame = cap.read()
            if not ok:
                break

            if frame_idx % sample_every != 0:
                frame_idx += 1
                continue

            timestamp_sec = frame_idx / fps
            detections = detector.detect(frame)
            candidate_matches: list[tuple[float, int, int]] = []
            for detection_index, det in enumerate(detections):
                current_box = (det.x1, det.y1, det.x2, det.y2)
                for track_id, track in tracks.items():
                    last_frame = int(track["last_frame"])
                    if frame_idx - last_frame > max_track_gap:
                        continue
                    score = _match_score(track, current_box, frame_idx)
                    if score >= min_match_score:
                        candidate_matches.append((score, track_id, detection_index))

            candidate_matches.sort(reverse=True)
            assigned_track_ids: set[int] = set()
            assigned_detection_indices: set[int] = set()
            detection_to_track: dict[int, int] = {}

            for score, track_id, detection_index in candidate_matches:
                if track_id in assigned_track_ids or detection_index in assigned_detection_indices:
                    continue
                assigned_track_ids.add(track_id)
                assigned_detection_indices.add(detection_index)
                detection_to_track[detection_index] = track_id

            for detection_index, det in enumerate(detections):
                track_id = detection_to_track.get(detection_index)
                if track_id is None:
                    track_id = next_track_id
                    tracks[track_id] = _create_track(det, frame_idx)
                    next_track_id += 1
                else:
                    _update_track(tracks[track_id], det, frame_idx)

                width = max(0.0, det.x2 - det.x1)
                height = max(0.0, det.y2 - det.y1)
                diameter_px = (width + height) / 2.0
                diameter_mm = diameter_px * mm_per_px if mm_per_px is not None else None

                track_hits = int(tracks[track_id].get("hits", 1))
                if track_hits < min_track_hits_to_persist:
                    continue

                pending_rows.append(
                    {
                        "track_id": track_id,
                        "frame_idx": frame_idx,
                        "timestamp_sec": timestamp_sec,
                        "confidence": det.confidence,
                        "diameter_px": diameter_px,
                        "diameter_mm": diameter_mm,
                        "x1": det.x1,
                        "y1": det.y1,
                        "x2": det.x2,
                        "y2": det.y2,
                    }
                )

            tracks = {
                track_id: track
                for track_id, track in tracks.items()
                if frame_idx - int(track["last_frame"]) <= max_track_gap
            }

            if len(pending_rows) >= 500:
                db.add_measurements(job_id, pending_rows)
                pending_rows.clear()

            if frame_idx % (sample_every * 10) == 0:
                db.update_job(
                    job_id,
                    processed_frames=frame_idx,
                    updated_at=_utc_now(),
                )

            frame_idx += 1

        if pending_rows:
            db.add_measurements(job_id, pending_rows)

        cap.release()

        db.update_job(
            job_id,
            status="completed",
            processed_frames=total_frames,
            updated_at=_utc_now(),
        )
    except Exception as exc:
        db.update_job(
            job_id,
            status="failed",
            error=str(exc),
            updated_at=_utc_now(),
        )
    finally:
        # Clean up uploaded video to avoid accumulating files on disk
        try:
            vp = Path(video_path)
            if vp.exists() and str(vp.parent).replace("\\", "/").endswith("uploads"):
                vp.unlink(missing_ok=True)
        except Exception:
            pass
