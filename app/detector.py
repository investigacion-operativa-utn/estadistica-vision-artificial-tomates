from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np


@dataclass
class Detection:
    x1: float
    y1: float
    x2: float
    y2: float
    confidence: Optional[float]


class TomatoDetector:
    """Tomato detector backed by Ultralytics.

    Modes:
    - auto: use Ultralytics as primary detector and fallback to `basic` if unavailable
    - yolo: use Ultralytics only
    - basic: use the legacy color segmentation fallback
    """

    def __init__(self) -> None:
        self._yolo = None
        self._world_prompt = os.getenv("TOMATO_WORLD_PROMPT", "tomato")
        self._mode = os.getenv("TOMATO_DETECTION_MODE", "auto").strip().lower()
        if self._mode == "classic":
            self._mode = "auto"
        self._confidence = float(os.getenv("TOMATO_YOLO_CONFIDENCE", "0.12"))
        self._image_size = int(os.getenv("TOMATO_YOLO_IMGSZ", "960"))
        self._roi_top_ratio = float(os.getenv("TOMATO_ROI_TOP_RATIO", "0.17"))
        self._roi_bottom_ratio = float(os.getenv("TOMATO_ROI_BOTTOM_RATIO", "0.78"))
        self._min_box_size = float(os.getenv("TOMATO_MIN_BOX_SIZE", "14"))
        self._max_box_ratio = float(os.getenv("TOMATO_MAX_BOX_RATIO", "0.28"))
        self._fallback_mode = os.getenv("TOMATO_FALLBACK_MODE", "basic").strip().lower()

        model_name = os.getenv("TOMATO_MODEL_WEIGHTS") or os.getenv("TOMATO_YOLO_MODEL", "yolov8s-world.pt")
        if self._mode in {"auto", "yolo"}:
            try:
                self._yolo = self._build_model(model_name)
            except Exception:
                self._yolo = None

    def detect(self, frame: np.ndarray) -> list[Detection]:
        if self._mode == "basic":
            return self._detect_basic_color(frame)

        if self._yolo is not None:
            detections = self._detect_yolo(frame)
            if detections or self._mode == "yolo":
                return detections

        if self._mode == "auto" and self._fallback_mode == "basic":
            return self._detect_basic_color(frame)

        return []

    def _build_model(self, model_name: str):
        if "world" in model_name.lower():
            from ultralytics import YOLOWorld

            model = YOLOWorld(model_name)
            model.set_classes([self._world_prompt])
            return model

        from ultralytics import YOLO

        return YOLO(model_name)

    def _detect_yolo(self, frame: np.ndarray) -> list[Detection]:
        results = self._yolo.predict(
            frame,
            verbose=False,
            conf=self._confidence,
            imgsz=self._image_size,
        )
        detections: list[Detection] = []
        frame_height, frame_width = frame.shape[:2]
        y_min = frame_height * self._roi_top_ratio
        y_max = frame_height * self._roi_bottom_ratio
        max_box_width = frame_width * self._max_box_ratio
        max_box_height = frame_height * self._max_box_ratio

        for result in results:
            boxes = result.boxes
            if boxes is None:
                continue
            for box in boxes:
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                conf = float(box.conf.item()) if box.conf is not None else None
                width = max(0.0, x2 - x1)
                height = max(0.0, y2 - y1)
                if width < self._min_box_size or height < self._min_box_size:
                    continue
                if width > max_box_width or height > max_box_height:
                    continue

                center_y = (y1 + y2) / 2.0
                if center_y < y_min or center_y > y_max:
                    continue

                aspect_ratio = width / max(height, 1.0)
                if not (0.45 <= aspect_ratio <= 1.85):
                    continue

                detections.append(Detection(x1=x1, y1=y1, x2=x2, y2=y2, confidence=conf))

        return detections

    def _detect_basic_color(self, frame: np.ndarray) -> list[Detection]:
        # Legacy fallback kept for debugging and comparison against the advanced pipeline.
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

        lower_red_1 = np.array([0, 45, 40])
        upper_red_1 = np.array([22, 255, 255])
        lower_red_2 = np.array([165, 45, 40])
        upper_red_2 = np.array([180, 255, 255])

        mask1 = cv2.inRange(hsv, lower_red_1, upper_red_1)
        mask2 = cv2.inRange(hsv, lower_red_2, upper_red_2)
        mask = cv2.bitwise_or(mask1, mask2)

        kernel = np.ones((5, 5), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.GaussianBlur(mask, (5, 5), 0)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        detections: list[Detection] = []
        h_frame, w_frame = frame.shape[:2]
        y_min = int(0.26 * h_frame)
        y_max = int(0.78 * h_frame)

        for contour in contours:
            area = cv2.contourArea(contour)
            if area < 180:
                continue

            perimeter = cv2.arcLength(contour, True)
            if perimeter <= 0:
                continue

            circularity = (4.0 * np.pi * area) / (perimeter * perimeter)

            x, y, w, h = cv2.boundingRect(contour)

            cy = y + (h / 2)
            if cy < y_min or cy > y_max:
                continue

            # Ignore huge regions from belt/metal structures.
            if w > 0.22 * w_frame or h > 0.22 * h_frame:
                continue

            aspect_ratio = w / max(h, 1)
            hull = cv2.convexHull(contour)
            hull_area = cv2.contourArea(hull)
            solidity = area / hull_area if hull_area > 0 else 0

            # Tomato-like blobs are mostly compact and close to circular.
            if not (0.6 <= aspect_ratio <= 1.45):
                continue
            if circularity < 0.55:
                continue
            if solidity < 0.86:
                continue

            detections.append(
                Detection(
                    x1=float(x),
                    y1=float(y),
                    x2=float(x + w),
                    y2=float(y + h),
                    confidence=0.45,
                )
            )
        return detections
