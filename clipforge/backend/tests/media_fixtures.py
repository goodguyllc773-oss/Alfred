"""Generate test media with ffmpeg alone — no downloaded or copyrighted footage.

* ``make_talk_video``: a test-pattern video whose audio is synthesized speech
  (ffmpeg's ``flite`` filter when available, otherwise tones), plus an SRT whose
  sentence timings are exact because we built the audio sentence by sentence.
* ``make_flash_beep_video``: white flashes and 1 kHz beeps at the same instants,
  for measuring audio/video sync after rendering.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

FF = shutil.which("ffmpeg") or "ffmpeg"
FP = shutil.which("ffprobe") or "ffprobe"

TALK = [
    "Have you ever wondered why some videos go viral and others never do?",
    "Here's the thing nobody tells you about short videos.",
    "The first three seconds decide almost everything.",
    "If the opening line does not grab you, you scroll away.",
    "So the hook matters more than the camera, more than the lights, and more than the edit.",
    "I tested this on forty clips last month.",
    "The clips that opened with a question kept people watching twice as long.",
    "That was honestly the biggest surprise of the whole experiment!",
    "And the clips that started in the middle of a thought did terribly.",
    "People need context, but they need it fast.",
    "Okay.",
    "Let me tell you about the worst clip I ever posted.",
    "It was a story about my dog stealing a whole pizza off the kitchen counter.",
    "The story was funny, but I cut it right before the punchline.",
    "Nobody knew how it ended, and the comments were furious.",
    "Lesson learned: always keep the payoff inside the clip.",
    "The camera matters less than you think, and the story matters more than you think.",
    "Next week I will show you the exact template I use for every clip.",
]


def run(cmd: list[str]) -> None:
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)}\n{out.stderr[-2000:]}")


@lru_cache(maxsize=1)
def has_flite() -> bool:
    out = subprocess.run([FF, "-hide_banner", "-filters"], capture_output=True, text=True)
    return " flite " in out.stdout


def duration(path: Path) -> float:
    out = subprocess.run([FP, "-v", "error", "-show_entries", "format=duration", "-of",
                          "default=nw=1:nk=1", str(path)], capture_output=True, text=True)
    return float(out.stdout.strip())


def _srt_ts(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def make_talk_video(out: Path, sentences: list[str] = TALK, *, size: str = "640x360", fps: int = 30,
                    pause: float = 0.7, long_pause_every: int = 5, long_pause: float = 1.9,
                    srt: bool = True) -> dict:
    """Returns {"video", "srt", "segments": [(start, end, text)], "duration"}."""
    work = out.parent / (out.stem + "_parts")
    work.mkdir(parents=True, exist_ok=True)
    parts, segments, t = [], [], 0.4
    silence_lead = work / "lead.wav"
    run([FF, "-y", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "0.4", str(silence_lead)])
    parts.append(silence_lead)
    for i, text in enumerate(sentences):
        wav = work / f"s{i:02d}.wav"
        if has_flite():
            txt = work / f"s{i:02d}.txt"
            txt.write_text(text, encoding="utf-8")
            # Relative textfile name (cwd=work) keeps the lavfi string free of path escaping.
            subprocess.run([FF, "-y", "-f", "lavfi", "-i", f"flite=textfile={txt.name}:voice=slt",
                            "-ar", "16000", "-ac", "1", wav.name], cwd=work, capture_output=True, check=True)
        else:
            d = 0.32 * len(text.split())
            run([FF, "-y", "-f", "lavfi", "-i", f"sine=f={300 + 40 * (i % 7)}:r=16000", "-t", f"{d:.2f}",
                 "-ac", "1", str(wav)])
        d = duration(wav)
        segments.append((round(t, 3), round(t + d, 3), text))
        parts.append(wav)
        gap = long_pause if (i + 1) % long_pause_every == 0 else pause
        gap_wav = work / f"g{i:02d}.wav"
        run([FF, "-y", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", f"{gap:.2f}", str(gap_wav)])
        parts.append(gap_wav)
        t += d + gap
    listing = work / "list.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    audio = work / "audio.wav"
    subprocess.run([FF, "-y", "-f", "concat", "-safe", "0", "-i", listing.name, "-c", "copy", audio.name],
                   cwd=work, capture_output=True, check=True)
    total = duration(audio)
    run([FF, "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}", "-i", str(audio),
         "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "96k", "-shortest", str(out)])
    srt_path = None
    if srt:
        srt_path = out.with_suffix(".srt")
        srt_path.write_text("".join(f"{i + 1}\n{_srt_ts(s)} --> {_srt_ts(e)}\n{txt}\n\n"
                                    for i, (s, e, txt) in enumerate(segments)), encoding="utf-8")
    shutil.rmtree(work, ignore_errors=True)
    return {"video": out, "srt": srt_path, "segments": segments, "duration": duration(out)}


def make_json_transcript(path: Path, segments: list[tuple[float, float, str]]) -> Path:
    """ClipForge JSON sidecar with evenly spread word timings (engine-style)."""
    segs = []
    for s, e, text in segments:
        toks = text.split()
        step = (e - s) / len(toks)
        words = [{"w": w, "s": round(s + k * step, 3), "e": round(s + (k + 0.9) * step, 3)}
                 for k, w in enumerate(toks)]
        segs.append({"start": s, "end": e, "text": text, "words": words})
    path.write_text(json.dumps({"language": "en", "segments": segs}), encoding="utf-8")
    return path


def make_silent_video(out: Path, seconds: float = 12, size: str = "320x240") -> Path:
    run([FF, "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=25", "-t", str(seconds),
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-an", str(out)])
    return out


def make_flash_beep_video(out: Path, seconds: float = 9, period: float = 2.0, size: str = "320x240",
                          fps: int = 30) -> Path:
    """Flash + beep at t = 1, 3, 5, 7 … (each 0.1 s)."""
    video = (f"color=c=black:s={size}:r={fps}:d={seconds},"
             f"drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='between(mod(t\\,{period}),1,1.1)'")
    audio = f"aevalsrc='0.8*sin(2*PI*1000*t)*between(mod(t\\,{period})\\,1\\,1.1)':s=48000:d={seconds}"
    run([FF, "-y", "-f", "lavfi", "-i", video, "-f", "lavfi", "-i", audio, "-c:v", "libx264",
         "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", str(out)])
    return out


def make_scene_video(out: Path, colors=("red", "blue", "green", "yellow"), each: float = 3.0,
                     size: str = "320x240") -> Path:
    inputs, filters = [], []
    for i, c in enumerate(colors):
        inputs += ["-f", "lavfi", "-i", f"color=c={c}:s={size}:r=25:d={each}"]
        filters.append(f"[{i}:v]")
    run([FF, "-y", *inputs, "-filter_complex", "".join(filters) + f"concat=n={len(colors)}:v=1:a=0[v]",
         "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(out)])
    return out
