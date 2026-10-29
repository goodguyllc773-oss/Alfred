"""Word-timing repair.

Engines sometimes return no word timestamps (subtitle imports, some Whisper
failure modes) or slightly broken ones (overlaps, zero-length words, words outside
their segment). Everything downstream — sentence splitting, clip boundaries,
caption highlighting — assumes clean, monotonic word timings, so all segments pass
through ``repair_segment`` before they are stored.
"""

from __future__ import annotations

import re

from .base import Segment, Word

MIN_WORD_S = 0.06


def split_words(text: str) -> list[str]:
    return [w for w in re.split(r"\s+", text.strip()) if w]


def estimate_words(text: str, start: float, end: float) -> list[Word]:
    """Spread words across ``[start, end]`` proportionally to their length
    (+1 so short words like "a" still get time)."""
    tokens = split_words(text)
    if not tokens:
        return []
    span = max(end - start, MIN_WORD_S * len(tokens))
    weights = [len(t) + 1 for t in tokens]
    total = sum(weights)
    words, t = [], start
    for tok, w in zip(tokens, weights):
        d = span * w / total
        words.append(Word(tok, t, t + d, None, True))
        t += d
    return words


def repair_segment(seg: Segment) -> Segment:
    if seg.end < seg.start:
        seg.start, seg.end = seg.end, seg.start
    words = [w for w in seg.words if w.text.strip()]
    if not words:
        seg.words = estimate_words(seg.text, seg.start, seg.end)
        return seg
    fixed: list[Word] = []
    prev_end = seg.start
    for w in words:
        s = w.start if w.start == w.start else prev_end  # NaN guard
        e = w.end if w.end == w.end else s
        s = max(s, prev_end, seg.start - 0.5)
        e = max(e, s + MIN_WORD_S)
        fixed.append(Word(w.text.strip(), s, e, w.prob, w.estimated))
        prev_end = e
    seg.words = fixed
    seg.start = min(seg.start, fixed[0].start)
    seg.end = max(seg.end, fixed[-1].end)
    if not seg.text.strip():
        seg.text = " ".join(w.text for w in fixed)
    return seg
