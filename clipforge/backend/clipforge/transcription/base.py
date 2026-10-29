"""Transcription types and the engine protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

ProgressFn = Callable[[float], None]  # absolute seconds of audio processed


@dataclass
class Word:
    text: str
    start: float
    end: float
    prob: float | None = None
    # True when the timing was interpolated rather than reported by the engine.
    estimated: bool = False

    def to_json(self) -> dict:
        d = {"w": self.text, "s": round(self.start, 3), "e": round(self.end, 3)}
        if self.prob is not None:
            d["p"] = round(self.prob, 3)
        if self.estimated:
            d["est"] = True
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Word":
        return cls(d["w"], float(d["s"]), float(d["e"]), d.get("p"), bool(d.get("est", False)))


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)
    avg_logprob: float | None = None
    no_speech_prob: float | None = None


@dataclass
class ChunkResult:
    segments: list[Segment]
    language: str | None = None
    device: str = ""
    warnings: list[str] = field(default_factory=list)


class Transcriber(Protocol):
    name: str

    def describe(self) -> dict:
        """Engine/model/device facts recorded on the transcript row."""

    def transcribe_range(self, source: str, start_s: float, end_s: float, *,
                         on_progress: ProgressFn | None = None) -> ChunkResult:
        """Transcribe ``[start_s, end_s)`` of ``source``; timestamps are absolute."""

    def release(self) -> None:
        """Free model memory (VRAM) after a job."""
