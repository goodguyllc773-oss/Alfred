"""Words → sentences → paragraphs.

Whisper punctuation is decent but not reliable on fast conversational speech, so a
sentence also ends at a long enough pause, and run-on "sentences" longer than
``max_sentence_s`` are split at their largest internal pause (or a comma).
A *paragraph* (topic-ish unit) starts after a long pause or a scene cut.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from dataclasses import dataclass, field

from ..transcription.base import Word

_TERMINAL = re.compile(r"[.?!…]+[\"'”’)\]]*$")
_ABBREV = {"mr.", "mrs.", "ms.", "dr.", "prof.", "st.", "vs.", "etc.", "e.g.", "i.e.", "u.s.", "jr.", "sr."}


def ends_sentence(token: str) -> bool:
    t = token.lower()
    if t in _ABBREV:
        return False
    if re.fullmatch(r"(?:[a-z]\.){2,}", t):  # "a.m." / "u.k."
        return False
    return bool(_TERMINAL.search(token))


@dataclass
class Sentence:
    words: list[Word]
    pause_before: float = 0.0
    pause_after: float = 0.0
    paragraph_start: bool = False
    scene_cut_before: bool = False
    index: int = 0
    terminal: bool = field(default=False)

    @property
    def start(self) -> float:
        return self.words[0].start

    @property
    def end(self) -> float:
        return self.words[-1].end

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def is_question(self) -> bool:
        return self.text.rstrip("\"'”’)] ").endswith("?")

    @property
    def duration(self) -> float:
        return self.end - self.start


def _split_long(words: list[Word], max_s: float) -> list[list[Word]]:
    if len(words) < 4 or words[-1].end - words[0].start <= max_s:
        return [words]
    best_i, best_score = None, -1.0
    for i in range(1, len(words) - 1):
        gap = words[i].start - words[i - 1].end
        score = gap + (0.25 if words[i - 1].text.endswith((",", ";", ":", "—")) else 0.0)
        # Prefer cuts nearer the middle so pieces are balanced.
        mid = abs(i - len(words) / 2) / len(words)
        score -= mid * 0.2
        if score > best_score:
            best_i, best_score = i, score
    assert best_i is not None
    return _split_long(words[:best_i], max_s) + _split_long(words[best_i:], max_s)


def build_sentences(words: list[Word], *, sentence_pause_s: float = 0.8, paragraph_pause_s: float = 1.6,
                    max_sentence_s: float = 20.0, scene_cuts: list[float] | None = None) -> list[Sentence]:
    words = [w for w in words if w.text.strip()]
    if not words:
        return []
    groups: list[list[Word]] = []
    cur: list[Word] = []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = (nxt.start - w.end) if nxt else 999.0
        if ends_sentence(w.text) or gap >= sentence_pause_s or nxt is None:
            groups.append(cur)
            cur = []
    pieces: list[list[Word]] = []
    for g in groups:
        pieces.extend(_split_long(g, max_sentence_s))

    cuts = sorted(scene_cuts or [])
    sentences: list[Sentence] = []
    for i, p in enumerate(pieces):
        s = Sentence(p, index=i, terminal=ends_sentence(p[-1].text))
        prev_end = pieces[i - 1][-1].end if i else 0.0
        s.pause_before = p[0].start - prev_end if i else p[0].start
        s.pause_after = (pieces[i + 1][0].start - p[-1].end) if i + 1 < len(pieces) else 999.0
        if i:
            k = bisect_left(cuts, prev_end - 0.25)
            s.scene_cut_before = k < len(cuts) and cuts[k] <= p[0].start + 0.25
        s.paragraph_start = i == 0 or s.pause_before >= paragraph_pause_s or s.scene_cut_before
        sentences.append(s)
    return sentences
