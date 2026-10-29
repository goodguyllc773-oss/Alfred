"""Render one vertical, captioned clip.

Safety properties:
* The source is only ever an ffmpeg *input*.
* Output goes to ``<tmp>/<work-id>/out.part.mp4``; only after ffprobe + a full
  decode pass validate it is it ``os.replace``d into place under a name that never
  overwrites an earlier render. A failure leaves previous clips untouched.
* ffmpeg runs with ``cwd`` = the work dir and the ASS file referenced by a bare
  relative name, so Windows drive letters never need filter-string escaping.
* If a hardware encoder fails mid-render we retry once with libx264 and record it.
"""

from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..analysis.framing import FaceSummary
from ..captions.ass import build_ass, clip_words, resolve_position_y
from ..captions.style import CaptionStyle
from ..errors import JobCancelled, RenderFailed
from ..media.ffmpeg import ffmpeg_version, run_capture, run_ffmpeg, resolve_binary
from ..media.hwaccel import CPU_H264_ENCODER, ENCODER_ARGS, HardwareReport, choose_encoder
from ..media.paths import unique_path
from ..media.probe import probe
from ..profiles.schema import EditingProfile
from ..transcription.base import Word
from .layouts import plan_layout

ProgressFn = Callable[[float], None]


@dataclass
class RenderRequest:
    source: str
    start_s: float
    end_s: float
    src_w: int
    src_h: int
    src_fps: float
    has_audio: bool
    profile: EditingProfile
    caption_style: CaptionStyle
    words: list[Word]
    output_dir: Path
    tmp_root: Path
    final_name: str
    out_w: int = 1080
    out_h: int = 1920
    encoder_preference: str = "auto"
    face: FaceSummary | None = None
    captions: bool = True
    deep_validate: bool = True


@dataclass
class RenderOutput:
    path: Path
    encoder: str
    duration_s: float
    file_size: int
    settings: dict
    validation: dict
    warnings: list[str] = field(default_factory=list)


def output_fps(src_fps: float) -> float:
    if not src_fps or src_fps <= 0:
        return 30.0
    return round(min(src_fps, 60.0), 3)


def build_command(req: RenderRequest, encoder: str, ass_name: str | None, filtergraph: str,
                  fps: float, out_name: str) -> list[str]:
    dur = req.end_s - req.start_s
    args = ["-ss", f"{req.start_s:.3f}", "-t", f"{dur:.3f}", "-i", req.source]
    if not req.has_audio:
        args += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    tail = f"[vbase]fps={fps}"
    tail += f",ass={ass_name}[vout]" if ass_name else "[vout]"
    args += ["-filter_complex", f"{filtergraph};{tail}", "-map", "[vout]"]
    args += ["-map", "0:a:0" if req.has_audio else "1:a:0"]
    gop = max(1, int(round(fps * 2)))
    args += ["-c:v", encoder, *ENCODER_ARGS.get(encoder, []), "-g", str(gop)]
    # async resampling pads/trims audio to the video clock → no drift on VFR sources.
    args += ["-af", "aresample=async=1:first_pts=0", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
             "-ac", "2"]
    args += ["-t", f"{dur:.3f}", "-movflags", "+faststart", "-map_metadata", "-1",
             "-metadata", "comment=Rendered by ClipForge", out_name]
    return args


def validate_output(path: Path, *, expected_w: int, expected_h: int, expected_dur: float,
                    fps: float, ffprobe: str, ffmpeg: str, deep: bool) -> dict:
    checks: dict = {"path_exists": path.is_file()}
    if not checks["path_exists"] or path.stat().st_size == 0:
        raise RenderFailed("Render produced no output file.", code="OUTPUT_MISSING")
    info = probe(path, ffprobe=ffprobe, check_extension=False)
    streams = info.raw.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    a = next((s for s in streams if s.get("codec_type") == "audio"), {})
    tol = max(0.3, 3.0 / max(fps, 1))

    def _f(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    vdur, adur = _f(v.get("duration")), _f(a.get("duration"))
    checks.update({
        "video_codec": info.video_codec, "width": info.width, "height": info.height,
        "duration_s": info.duration_s, "expected_duration_s": round(expected_dur, 3),
        "audio_codec": info.audio_codec, "video_stream_s": vdur, "audio_stream_s": adur,
        "fps": info.fps,
    })
    problems = []
    if info.video_codec != "h264":
        problems.append(f"video codec is {info.video_codec}, expected h264")
    if (info.width, info.height) != (expected_w, expected_h):
        problems.append(f"size is {info.width}x{info.height}, expected {expected_w}x{expected_h}")
    if abs(info.duration_s - expected_dur) > tol:
        problems.append(f"duration {info.duration_s:.2f}s, expected {expected_dur:.2f}s")
    if info.audio_codec != "aac":
        problems.append(f"audio codec is {info.audio_codec}, expected aac")
    if vdur is not None and adur is not None:
        checks["av_duration_gap_s"] = round(abs(vdur - adur), 3)
        if abs(vdur - adur) > tol:
            problems.append(f"audio ({adur:.2f}s) and video ({vdur:.2f}s) lengths differ")
    if deep:
        exe = resolve_binary(ffmpeg)
        out = run_capture([exe, "-hide_banner", "-nostdin", "-v", "error", "-i", str(path),
                           "-f", "null", "-"], timeout_s=600)
        errs = [l for l in (out.stderr or "").splitlines() if l.strip()]
        checks["decode_errors"] = len(errs)
        if out.returncode != 0 or errs:
            problems.append("decode check failed: " + (errs[0] if errs else f"exit {out.returncode}"))
    checks["ok"] = not problems
    checks["problems"] = problems
    if problems:
        raise RenderFailed("Rendered file failed validation: " + "; ".join(problems),
                           code="OUTPUT_INVALID", retryable=True)
    return checks


def render_clip(req: RenderRequest, *, hw: HardwareReport, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe",
                on_progress: ProgressFn | None = None) -> RenderOutput:
    dur = req.end_s - req.start_s
    if dur <= 0.2:
        raise RenderFailed("Clip is too short to render.", code="BAD_RANGE", retryable=False)
    if not Path(req.source).is_file():
        from ..errors import SourceMissing
        raise SourceMissing(f"Source video not found: {req.source}",
                            hint="Reconnect the drive or relink the media in the project.")

    fps = output_fps(req.src_fps)
    plan = plan_layout(req.profile.layout, req.src_w, req.src_h, req.out_w, req.out_h, req.face)
    caption_y = resolve_position_y(
        req.caption_style, req.profile.captions.position, req.out_h, seam_y=plan.seam_y,
        face_box_out=plan.face_box_out, avoid_faces=req.profile.captions.avoid_faces)
    cwords = clip_words(req.words, req.start_s, req.end_s) if req.captions else []

    work = req.tmp_root / f"work-{uuid.uuid4().hex[:12]}"
    work.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    try:
        ass_name = None
        if cwords:
            ass_name = "captions.ass"
            (work / ass_name).write_text(
                build_ass(cwords, req.caption_style, req.out_w, req.out_h, caption_y), encoding="utf-8")
        elif req.captions:
            warnings.append("No transcript words in this range — rendered without captions.")

        encoder = choose_encoder(hw, req.encoder_preference)
        out_name = "out.part.mp4"
        attempts = [encoder] + ([CPU_H264_ENCODER] if encoder != CPU_H264_ENCODER else [])
        result, args = None, []
        for enc in attempts:
            args = build_command(req, enc, ass_name, plan.filtergraph, fps, out_name)

            def prog(x: float) -> None:
                if on_progress:
                    on_progress(0.9 * x)

            result = run_ffmpeg(args, ffmpeg=ffmpeg, duration_s=dur, on_progress=prog, cwd=str(work))
            if result.returncode == 0:
                encoder = enc
                break
            if enc != CPU_H264_ENCODER:
                warnings.append(f"{enc} failed ({result.stderr_tail.splitlines()[-1:] or ['?']}); "
                                "fell back to CPU encoding (libx264).")
                (work / out_name).unlink(missing_ok=True)
        assert result is not None
        if result.returncode != 0:
            last = [l for l in result.stderr_tail.splitlines() if l.strip()][-3:]
            raise RenderFailed("ffmpeg failed: " + " | ".join(last), code="FFMPEG_FAILED")

        tmp_out = work / out_name
        validation = validate_output(tmp_out, expected_w=req.out_w, expected_h=req.out_h,
                                     expected_dur=dur, fps=fps, ffprobe=ffprobe, ffmpeg=ffmpeg,
                                     deep=req.deep_validate)
        if on_progress:
            on_progress(0.97)
        req.output_dir.mkdir(parents=True, exist_ok=True)
        final = unique_path(req.output_dir, req.final_name)
        os.replace(tmp_out, final)
        if on_progress:
            on_progress(1.0)
        settings = {
            "ffmpeg": ffmpeg_version(ffmpeg),
            "argv": ["ffmpeg", "-hide_banner", "-nostdin", "-y", *args],
            "encoder": encoder,
            "encoder_args": ENCODER_ARGS.get(encoder, []),
            "output": {"width": req.out_w, "height": req.out_h, "fps": fps,
                       "audio": "aac 160k 48kHz stereo" + ("" if req.has_audio else " (silent track)")},
            "range": {"start_s": req.start_s, "end_s": req.end_s, "duration_s": round(dur, 3)},
            "layout": {"config": req.profile.layout.model_dump(), "plan": plan.details,
                       "filtergraph": plan.filtergraph},
            "captions": {"style": req.caption_style.model_dump(), "position": req.profile.captions.position,
                         "center_y": caption_y, "words": len(cwords),
                         "estimated_words": sum(1 for w in req.words
                                                if w.estimated and req.start_s <= w.start < req.end_s)},
            "face": None if req.face is None else {"center_x": req.face.center_x, "center_y": req.face.center_y,
                                                   "box": req.face.box, "frames": req.face.frames},
        }
        return RenderOutput(final, encoder, validation["duration_s"], final.stat().st_size, settings,
                            validation, warnings)
    except JobCancelled:
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
