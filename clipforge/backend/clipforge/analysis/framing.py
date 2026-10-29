"""Face-aware framing for the ``crop`` layout.

Samples a few frames across the clip (single-frame ffmpeg seeks — no full decode),
runs OpenCV's bundled Haar frontal-face cascade, and returns the median face centre
plus the median face box. This is a basic detector: it misses profile views and
small faces, and it does not follow a speaker *during* the clip — the crop is fixed
per clip. When nothing is found the profile's ``crop_center_x`` is used.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

import numpy as np

from ..media.ffmpeg import _CREATE_FLAGS, resolve_binary

SAMPLE_W = 480


@dataclass
class FaceSummary:
    center_x: float  # normalised 0..1 in source frame
    center_y: float
    box: tuple[float, float, float, float] | None  # x, y, w, h normalised (median face)
    faces_per_frame: float
    frames: int


def grab_frame(source: str, t: float, width: int, height: int, ffmpeg: str = "ffmpeg",
               out_w: int = SAMPLE_W) -> np.ndarray | None:
    exe = resolve_binary(ffmpeg)
    out_h = max(2, int(round(out_w * height / max(1, width) / 2)) * 2)
    cmd = [exe, "-hide_banner", "-nostdin", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", source,
           "-frames:v", "1", "-vf", f"scale={out_w}:{out_h}", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=30, creationflags=_CREATE_FLAGS)
    except subprocess.TimeoutExpired:
        return None
    if out.returncode != 0 or len(out.stdout) < out_w * out_h:
        return None
    return np.frombuffer(out.stdout[: out_w * out_h], dtype=np.uint8).reshape(out_h, out_w)


def _cascade():
    import cv2  # noqa: PLC0415
    c = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    return None if c.empty() else c


def detect_faces(gray: np.ndarray, cascade=None) -> list[tuple[int, int, int, int]]:
    cascade = cascade or _cascade()
    if cascade is None:
        return []
    h = gray.shape[0]
    faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6,
                                     minSize=(max(24, h // 12), max(24, h // 12)))
    return [tuple(int(v) for v in f) for f in faces] if len(faces) else []


def summarize_faces(source: str, start: float, end: float, width: int, height: int, *,
                    samples: int = 5, ffmpeg: str = "ffmpeg") -> FaceSummary | None:
    try:
        cascade = _cascade()
    except Exception:  # OpenCV missing/broken → caller falls back
        return None
    if cascade is None:
        return None
    centres, boxes, counts, frames = [], [], [], 0
    span = max(0.1, end - start)
    for k in range(samples):
        t = start + span * (k + 0.5) / samples
        gray = grab_frame(source, t, width, height, ffmpeg)
        if gray is None:
            continue
        frames += 1
        fh, fw = gray.shape
        faces = detect_faces(gray, cascade)
        counts.append(len(faces))
        if faces:
            x, y, w, h = max(faces, key=lambda f: f[2] * f[3])  # largest face = main subject
            centres.append(((x + w / 2) / fw, (y + h / 2) / fh))
            boxes.append((x / fw, y / fh, w / fw, h / fh))
    if not centres:
        return None
    cx, cy = np.median(np.array(centres), axis=0)
    box = tuple(float(v) for v in np.median(np.array(boxes), axis=0))
    return FaceSummary(float(cx), float(cy), box, float(np.mean(counts)) if counts else 0.0, frames)
