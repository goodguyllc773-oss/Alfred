"""Optional semantic re-ranking interface.

Nothing implements this yet. The intended first implementation is a local LLM
through Ollama (no key, no cost, offline): give it each top-N window's transcript
and ask for a 0..1 "would this work as a standalone short?" score with a one-line
reason. A cloud provider could implement the same protocol behind an explicit
opt-in, since it would send transcript text off the machine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from ..profiles.schema import EditingProfile


@dataclass
class SemanticScore:
    value: float  # 0..1
    reason: str | None = None


class SemanticScorer(Protocol):
    name: str

    def score(self, windows: Sequence[tuple[float, float, str]],
              profile: EditingProfile) -> list[SemanticScore | None]:
        """Return one score (or None to abstain) per ``(start, end, text)`` window."""
