"""ffprobe wrapper: turn a file into validated ``MediaInfo`` (or a clear error).

ffprobe reads container headers and stream metadata only — the file is never loaded
into memory.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from pathlib import Path

from ..errors import InvalidMedia
from .ffmpeg import resolve_binary, run_capture

# Containers we accept. ffprobe can read far more, but these are what creators
# actually produce and what our render path is tested against.
SUPPORTED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".flv", ".ts", ".mts", ".m2ts"}
# Browser <video> can play these directly for source preview.
BROWSER_PLAYABLE = {".mp4", ".m4v", ".webm", ".mov"}

MIN_DURATION_S = 1.0
MAX_DIMENSION = 8192


@dataclass
class MediaInfo:
    path: str
    container: str
    duration_s: float
    width: int
    height: int
    fps: float
    video_codec: str
    rotation: int
    has_audio: bool
    audio_codec: str | None = None
    audio_channels: int | None = None
    sample_rate: int | None = None
    bit_rate: int | None = None
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def display_width(self) -> int:
        return self.height if abs(self.rotation) in (90, 270) else self.width

    @property
    def display_height(self) -> int:
        return self.width if abs(self.rotation) in (90, 270) else self.height

    def summary(self) -> dict:
        d = asdict(self)
        d.pop("raw", None)
        return d


def _fps(stream: dict) -> float:
    for key in ("avg_frame_rate", "r_frame_rate"):
        v = stream.get(key)
        if v and v not in ("0/0", "0"):
            try:
                f = float(Fraction(v))
                if 0 < f < 1000:
                    return round(f, 3)
            except (ValueError, ZeroDivisionError):
                pass
    return 0.0


def _rotation(stream: dict) -> int:
    tags = stream.get("tags") or {}
    if "rotate" in tags:
        try:
            return int(tags["rotate"]) % 360
        except ValueError:
            pass
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                return int(sd["rotation"]) % 360
            except (TypeError, ValueError):
                pass
    return 0


def probe(path: str | Path, *, ffprobe: str = "ffprobe", check_extension: bool = True) -> MediaInfo:
    p = Path(path)
    if not p.exists():
        raise InvalidMedia(f"File not found: {p}", code="FILE_NOT_FOUND",
                           hint="Check the path, or that the drive is connected.")
    if not p.is_file():
        raise InvalidMedia(f"Not a file: {p}", code="NOT_A_FILE")
    if check_extension and p.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise InvalidMedia(
            f"Unsupported file type '{p.suffix or '(none)'}'.",
            code="UNSUPPORTED_FORMAT",
            hint="Supported: " + ", ".join(sorted(SUPPORTED_EXTENSIONS)) +
                 ". Convert other formats to MP4 first (e.g. with HandBrake or ffmpeg).",
        )
    if p.stat().st_size == 0:
        raise InvalidMedia("The file is empty (0 bytes).", code="EMPTY_FILE")

    exe = resolve_binary(ffprobe)
    try:
        out = run_capture([exe, "-v", "error", "-print_format", "json", "-show_format",
                           "-show_streams", str(p)], timeout_s=60)
    except subprocess.TimeoutExpired:
        raise InvalidMedia("ffprobe timed out reading the file.", code="PROBE_TIMEOUT",
                           hint="The file may be on a slow or disconnected network drive.")
    if out.returncode != 0:
        msg = (out.stderr or "").strip().splitlines()[-1:] or ["unknown error"]
        raise InvalidMedia(f"Not a readable video: {msg[0]}", code="UNREADABLE_MEDIA",
                           hint="The file may be corrupt, incomplete, or not a video.")
    try:
        data = json.loads(out.stdout or "{}")
    except json.JSONDecodeError:
        raise InvalidMedia("ffprobe returned unparseable output.", code="UNREADABLE_MEDIA")

    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise InvalidMedia("The file has no video stream.", code="NO_VIDEO_STREAM",
                           hint="Audio-only files aren't supported yet — add a still image "
                                "or waveform video first.")

    duration = 0.0
    for v in (fmt.get("duration"), video.get("duration")):
        try:
            if v is not None and float(v) > 0:
                duration = float(v)
                break
        except ValueError:
            continue
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    info = MediaInfo(
        path=str(p),
        container=str(fmt.get("format_name") or ""),
        duration_s=round(duration, 3),
        width=width,
        height=height,
        fps=_fps(video),
        video_codec=str(video.get("codec_name") or ""),
        rotation=_rotation(video),
        has_audio=audio is not None,
        audio_codec=audio.get("codec_name") if audio else None,
        audio_channels=int(audio["channels"]) if audio and audio.get("channels") else None,
        sample_rate=int(audio["sample_rate"]) if audio and audio.get("sample_rate") else None,
        bit_rate=int(fmt["bit_rate"]) if fmt.get("bit_rate", "").isdigit() else None,
        raw=data,
    )
    validate(info)
    return info


def validate(info: MediaInfo) -> None:
    if info.duration_s < MIN_DURATION_S:
        raise InvalidMedia(f"Video is too short ({info.duration_s:.2f}s).", code="TOO_SHORT",
                           hint=f"ClipForge needs at least {MIN_DURATION_S:.0f}s of footage.")
    if not (16 <= info.width <= MAX_DIMENSION and 16 <= info.height <= MAX_DIMENSION):
        raise InvalidMedia(f"Unusual video dimensions {info.width}x{info.height}.", code="BAD_DIMENSIONS")
    if info.fps <= 0:
        raise InvalidMedia("Could not determine the frame rate.", code="BAD_FRAME_RATE",
                           hint="The file may be a still image or a damaged stream.")
