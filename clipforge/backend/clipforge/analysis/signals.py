"""Audio and visual signals, computed by streaming — never loading the whole file.

* **Loudness**: ffmpeg decodes to 8 kHz mono s16 on a pipe; we read it in blocks and
  emit RMS dBFS per ``window_s`` (0.5 s). A 3-hour video → 21 600 floats.
* **Scene cuts**: ffmpeg's ``select='gt(scene,T)'`` on a downscaled, frame-rate-
  reduced stream, printed with ``metadata=print``. Decoding still touches every
  frame, so this is the slowest analysis step; profiles can switch it off.
* **Silences**: derived from the loudness curve (runs below ``-45 dBFS``).

Results are cached as JSON under ``cache/<media_id>/``.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from ..errors import JobCancelled
from ..media.ffmpeg import _CREATE_FLAGS, resolve_binary

ProgressFn = Callable[[float], None]
LOUDNESS_RATE = 8000
SILENCE_DB = -45.0
FLOOR_DB = -90.0


@dataclass
class Loudness:
    window_s: float
    db: list[float]

    def slice(self, start: float, end: float) -> np.ndarray:
        a = max(0, int(start / self.window_s))
        b = max(a + 1, int(math.ceil(end / self.window_s)))
        return np.asarray(self.db[a:b], dtype=np.float32)

    @property
    def median(self) -> float:
        arr = np.asarray(self.db, dtype=np.float32)
        voiced = arr[arr > SILENCE_DB]
        return float(np.median(voiced)) if voiced.size else float(np.median(arr)) if arr.size else FLOOR_DB

    def silences(self, min_len_s: float = 0.4) -> list[tuple[float, float]]:
        out, run_start = [], None
        for i, v in enumerate(self.db + [0.0]):
            quiet = v <= SILENCE_DB
            if quiet and run_start is None:
                run_start = i
            elif not quiet and run_start is not None:
                if (i - run_start) * self.window_s >= min_len_s:
                    out.append((run_start * self.window_s, i * self.window_s))
                run_start = None
        return out

    def to_json(self) -> dict:
        return {"window_s": self.window_s, "db": [round(v, 2) for v in self.db]}

    @classmethod
    def from_json(cls, d: dict) -> "Loudness":
        return cls(float(d["window_s"]), [float(v) for v in d["db"]])


def compute_loudness(source: str, duration_s: float, *, ffmpeg: str = "ffmpeg", window_s: float = 0.5,
                     on_progress: ProgressFn | None = None) -> Loudness:
    exe = resolve_binary(ffmpeg)
    cmd = [exe, "-hide_banner", "-nostdin", "-v", "error", "-i", source, "-vn", "-map", "0:a:0",
           "-ac", "1", "-ar", str(LOUDNESS_RATE), "-f", "s16le", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            creationflags=_CREATE_FLAGS)
    per_window = int(LOUDNESS_RATE * window_s)
    block_bytes = per_window * 2 * 120  # 60 s of audio per read
    db: list[float] = []
    leftover = b""
    try:
        assert proc.stdout is not None
        while True:
            buf = proc.stdout.read(block_bytes)
            if not buf:
                break
            buf = leftover + buf
            usable = len(buf) - (len(buf) % (per_window * 2))
            leftover = buf[usable:]
            if usable:
                a = np.frombuffer(buf[:usable], dtype=np.int16).astype(np.float32) / 32768.0
                rms = np.sqrt(np.mean(a.reshape(-1, per_window) ** 2, axis=1))
                db.extend(np.maximum(20 * np.log10(np.maximum(rms, 1e-9)), FLOOR_DB).tolist())
            if on_progress and duration_s:
                on_progress(min(1.0, len(db) * window_s / duration_s))
        if leftover and len(leftover) >= 2:
            a = np.frombuffer(leftover[: len(leftover) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768.0
            rms = float(np.sqrt(np.mean(a ** 2))) if a.size else 0.0
            db.append(max(20 * math.log10(max(rms, 1e-9)), FLOOR_DB))
        proc.wait()
    except (JobCancelled, BaseException):
        proc.kill()
        proc.wait()
        raise
    return Loudness(window_s, db)


_PTS = re.compile(r"pts_time:([0-9.]+)")
_SCORE = re.compile(r"lavfi\.scene_score=([0-9.]+)")


def detect_scenes(source: str, duration_s: float, *, ffmpeg: str = "ffmpeg", threshold: float = 0.35,
                  sample_fps: float = 6.0, on_progress: ProgressFn | None = None) -> list[dict]:
    """Return ``[{"t": seconds, "score": 0..1}, ...]`` for detected cuts."""
    exe = resolve_binary(ffmpeg)
    vf = (f"fps={sample_fps},scale=192:-2,select='gt(scene\\,{threshold})',"
          f"metadata=print:file=-")
    cmd = [exe, "-hide_banner", "-nostdin", "-v", "error", "-i", source, "-an", "-sn",
           "-vf", vf, "-f", "null", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                            creationflags=_CREATE_FLAGS)
    cuts: list[dict] = []
    current_t: float | None = None
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            m = _PTS.search(line)
            if m:
                current_t = float(m.group(1))
                if on_progress and duration_s:
                    on_progress(min(1.0, current_t / duration_s))
                continue
            m = _SCORE.search(line)
            if m and current_t is not None:
                cuts.append({"t": round(current_t, 3), "score": round(float(m.group(1)), 3)})
                current_t = None
        proc.wait()
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    if on_progress:
        on_progress(1.0)
    return cuts


def cache_json(path: Path, compute: Callable[[], dict | list]) -> dict | list:
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    data = compute()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)
    return data


def plan_chunks(duration_s: float, chunk_s: float, loudness: Loudness | None,
                search_s: float = 30.0) -> list[tuple[float, float]]:
    """Split ``[0, duration]`` into ~``chunk_s`` pieces, moving each cut to the
    quietest loudness window within ``±search_s`` so words are rarely split."""
    if duration_s <= chunk_s * 1.25:
        return [(0.0, duration_s)]
    cuts = [0.0]
    t = chunk_s
    while t < duration_s - chunk_s * 0.25:
        cut = t
        if loudness and loudness.db:
            arr = loudness.slice(max(0, t - search_s), min(duration_s, t + search_s))
            if arr.size:
                cut = max(0.0, t - search_s) + int(np.argmin(arr)) * loudness.window_s + loudness.window_s / 2
        cut = max(cut, cuts[-1] + chunk_s * 0.5)
        cuts.append(round(cut, 3))
        t = cut + chunk_s
    cuts.append(duration_s)
    return list(zip(cuts[:-1], cuts[1:]))
