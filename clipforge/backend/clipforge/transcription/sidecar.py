"""Import an existing transcript instead of running Whisper.

Supports SRT, WebVTT and ClipForge JSON. SRT/VTT carry only cue-level timing, so
word timings are estimated (and flagged ``estimated``); JSON may carry real word
timings::

    {"language": "en", "segments": [
        {"start": 0.0, "end": 2.1, "text": "Hello there.",
         "words": [{"w": "Hello", "s": 0.0, "e": 0.4}, {"w": "there.", "s": 0.5, "e": 0.9}]}]}
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

from ..errors import TranscriptionFailed
from .base import ChunkResult, Segment, Word
from .words import repair_segment

SIDECAR_SUFFIXES = (".clipforge.json", ".json", ".srt", ".vtt")

_TS = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")
_TAGS = re.compile(r"<[^>]+>|\{\\[^}]*\}")


def _ts(s: str) -> float:
    m = _TS.search(s)
    if not m:
        raise ValueError(f"bad timestamp: {s!r}")
    h, mi, se, ms = m.groups()
    return int(h or 0) * 3600 + int(mi) * 60 + int(se) + int(ms.ljust(3, "0")) / 1000


def parse_srt_vtt(text: str) -> list[Segment]:
    segs: list[Segment] = []
    blocks = re.split(r"\r?\n\s*\r?\n", text.replace("﻿", ""))
    for block in blocks:
        lines = [l for l in block.strip().splitlines() if l.strip()]
        idx = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if idx is None:
            continue
        a, _, b = lines[idx].partition("-->")
        try:
            start, end = _ts(a), _ts(b.split()[0] if b.split() else b)
        except ValueError:
            continue
        body = " ".join(html.unescape(_TAGS.sub("", l)).strip() for l in lines[idx + 1:])
        body = re.sub(r"\s+", " ", body).strip()
        if body:
            segs.append(Segment(start, end, body))
    # Auto-generated captions often repeat the previous cue's text in a rolling
    # window; drop exact consecutive duplicates.
    out: list[Segment] = []
    for s in segs:
        if out and s.text == out[-1].text:
            out[-1].end = max(out[-1].end, s.end)
            continue
        out.append(s)
    return out


def parse_json(text: str) -> tuple[list[Segment], str | None]:
    data = json.loads(text)
    segs = []
    for raw in data.get("segments", []):
        words = [Word.from_json(w) for w in raw.get("words") or []]
        segs.append(Segment(float(raw["start"]), float(raw["end"]), str(raw.get("text", "")), words))
    return segs, data.get("language")


def find_sidecar(video_path: str | Path) -> Path | None:
    p = Path(video_path)
    for suffix in SIDECAR_SUFFIXES:
        c = p.with_name(p.stem + suffix)
        if c.is_file():
            return c
    return None


class SidecarTranscriber:
    name = "sidecar"

    def __init__(self, transcript_path: str | Path | None = None):
        self.transcript_path = Path(transcript_path) if transcript_path else None
        self._cache: tuple[list[Segment], str | None] | None = None

    def _load(self, source: str) -> tuple[list[Segment], str | None]:
        if self._cache is not None:
            return self._cache
        path = self.transcript_path or find_sidecar(source)
        if path is None or not path.is_file():
            raise TranscriptionFailed(
                "No transcript file found next to the video.", code="NO_SIDECAR", retryable=False,
                hint="Put a .srt, .vtt or .clipforge.json with the same name next to the video, "
                     "or switch the transcription engine to faster-whisper in Settings.")
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            if path.suffix.lower() == ".json":
                segs, lang = parse_json(text)
            else:
                segs, lang = parse_srt_vtt(text), None
        except (ValueError, KeyError, json.JSONDecodeError) as e:
            raise TranscriptionFailed(f"Couldn't parse transcript {path.name}: {e}",
                                      code="BAD_SIDECAR", retryable=False)
        segs = [repair_segment(s) for s in sorted(segs, key=lambda s: s.start)]
        self._cache = (segs, lang)
        return self._cache

    def describe(self) -> dict:
        return {"engine": self.name, "model": str(self.transcript_path or "auto"), "device": "n/a"}

    def transcribe_range(self, source, start_s, end_s, *, on_progress=None) -> ChunkResult:
        segs, lang = self._load(source)
        # A segment belongs to the chunk its start falls in, so none is duplicated.
        picked = [s for s in segs if start_s <= s.start < end_s]
        if on_progress:
            on_progress(end_s)
        return ChunkResult(picked, lang, "n/a")

    def release(self) -> None:
        self._cache = None
