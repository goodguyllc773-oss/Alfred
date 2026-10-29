"""Candidate generation.

1. Words → sentences/paragraphs (``sentences.py``).
2. Every run of whole sentences whose span is within ``[min_s, max_s]`` is a window.
   If no sentence boundary falls inside that range for a given start (one long
   sentence), the window may run to ``hard_max_s`` rather than cut mid-thought — and
   says so in its reasons.
3. Windows are scored (``scoring.py``), optionally re-ranked by a ``SemanticScorer``.
4. Greedy overlap suppression keeps the best non-overlapping set.
5. Boundaries get a little lead-in/tail, never swallowing a neighbouring word, and
   snap to a scene cut when one sits in the gap.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field

from ..profiles.schema import EditingProfile
from ..transcription.base import Word
from .scoring import SCORER_NAME, STOPWORDS, ScoreResult, _tok, build_context, score_window
from .semantic import SemanticScorer
from .sentences import Sentence, build_sentences
from .signals import Loudness


@dataclass
class Candidate:
    start: float
    end: float
    text: str
    score: float
    features: dict = field(default_factory=dict)
    reasons: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    first_sentence: str = ""
    post_title: str = ""
    hashtags: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start


def overlap_fraction(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    shorter = max(1e-6, min(a[1] - a[0], b[1] - b[0]))
    return inter / shorter


def refine_bounds(first: Word, last: Word, prev: Word | None, nxt: Word | None, *,
                  lead_in: float, tail: float, media_duration: float,
                  scene_cuts: list[float]) -> tuple[float, float]:
    start = first.start - lead_in
    if prev is not None:
        start = max(start, prev.end + 0.05)
    start = max(0.0, min(start, first.start))
    end = last.end + tail
    if nxt is not None:
        end = min(end, nxt.start - 0.05)
    end = min(media_duration, max(end, last.end))
    # Snap to a scene cut sitting in the lead-in / tail gap so the clip doesn't
    # flash a frame of the neighbouring shot.
    i = bisect_right(scene_cuts, first.start + 0.05) - 1
    if i >= 0 and start <= scene_cuts[i] <= first.start + 0.05:
        start = scene_cuts[i]
    j = bisect_left(scene_cuts, last.end - 0.05)
    if j < len(scene_cuts) and last.end - 0.05 <= scene_cuts[j] <= end:
        end = scene_cuts[j]
    # Clamp after rounding so the end can never land past the last frame.
    return round(start, 3), min(round(end, 3), media_duration)


def _windows(sentences: list[Sentence], profile: EditingProfile) -> list[tuple[int, int, bool]]:
    d = profile.duration
    out = []
    for i in range(len(sentences)):
        found = False
        for j in range(i, len(sentences)):
            dur = sentences[j].end - sentences[i].start
            if dur < d.min_s:
                continue
            if dur <= d.max_s:
                out.append((i, j, False))
                found = True
                continue
            if not found and dur <= d.hard_max_s:
                out.append((i, j, True))
            break
    return out


def post_title(first_sentence: str, limit: int = 90) -> str:
    t = first_sentence.strip().strip("\"'")
    if len(t) > limit:
        t = t[: limit - 1].rsplit(" ", 1)[0] + "…"
    return t[:1].upper() + t[1:] if t else ""


def hashtags_for(text: str, profile: EditingProfile, n_keywords: int = 2) -> list[str]:
    from collections import Counter
    words = [t for t in (_tok(w) for w in text.split()) if len(t) >= 5 and t not in STOPWORDS
             and t.isalpha()]
    top = [w for w, c in Counter(words).most_common(n_keywords) if c > 1]
    tags = list(dict.fromkeys([*profile.hashtags, *top, "fyp"]))
    return [f"#{t}" for t in tags]


def generate_candidates(words: list[Word], *, media_duration: float, profile: EditingProfile,
                        loudness: Loudness | None = None, scene_cuts: list[float] | None = None,
                        semantic: SemanticScorer | None = None) -> tuple[list[Candidate], dict]:
    cuts = sorted(scene_cuts or [])
    seg = profile.segmentation
    sentences = build_sentences(words, sentence_pause_s=seg.sentence_pause_s,
                                paragraph_pause_s=seg.paragraph_pause_s,
                                max_sentence_s=seg.max_sentence_s, scene_cuts=cuts)
    stats = {"scorer": SCORER_NAME, "words": len(words), "sentences": len(sentences),
             "scene_cuts": len(cuts), "windows": 0, "selected": 0}
    if not sentences:
        cands = visual_only_candidates(media_duration, profile, cuts)
        stats["fallback"] = "no-speech"
        stats["selected"] = len(cands)
        return cands, stats

    ctx = build_context(loudness, cuts, profile)
    flat = [w for s in sentences for w in s.words]
    # index of each sentence's first word in ``flat``
    offsets, k = [], 0
    for s in sentences:
        offsets.append(k)
        k += len(s.words)

    scored: list[Candidate] = []
    windows = _windows(sentences, profile)
    stats["windows"] = len(windows)
    for i, j, over in windows:
        first_w = flat[offsets[i]]
        last_idx = offsets[j] + len(sentences[j].words) - 1
        last_w = flat[last_idx]
        prev_w = flat[offsets[i] - 1] if offsets[i] > 0 else None
        next_w = flat[last_idx + 1] if last_idx + 1 < len(flat) else None
        start, end = refine_bounds(first_w, last_w, prev_w, next_w, lead_in=seg.lead_in_s,
                                   tail=seg.tail_s, media_duration=media_duration, scene_cuts=cuts)
        if not over and end - start > profile.duration.max_s:
            # Padding pushed it over the limit: give back tail, then lead-in — never speech.
            excess = end - start - profile.duration.max_s
            cut_tail = min(excess, end - last_w.end)
            end -= cut_tail
            start += min(excess - cut_tail, first_w.start - start)
            start, end = round(start, 3), round(end, 3)
        window = sentences[i: j + 1]
        res: ScoreResult = score_window(window, ctx, start, end)
        reasons = list(res.reasons)
        if over:
            reasons.append({"feature": "duration", "text":
                            f"Runs {end - start:.0f}s (over the {profile.duration.max_s:.0f}s max) "
                            "so the last sentence isn't cut off mid-thought."})
        text = " ".join(s.text for s in window)
        scored.append(Candidate(start, end, text, res.score, res.features, reasons, res.warnings,
                                first_sentence=window[0].text))

    if semantic is not None and scored:
        top = sorted(scored, key=lambda c: c.score, reverse=True)[: profile.max_candidates * 3]
        for c, s in zip(top, semantic.score([(c.start, c.end, c.text) for c in top], profile)):
            if s is None:
                continue
            c.features["semantic"] = {"value": s.value, "weight": None, "evidence": s.reason}
            c.score = round(0.5 * c.score + 50 * s.value, 1)
            if s.reason:
                c.reasons.insert(0, {"feature": "semantic", "text": s.reason})

    selected: list[Candidate] = []
    for c in sorted(scored, key=lambda c: c.score, reverse=True):
        if all(overlap_fraction((c.start, c.end), (s.start, s.end)) <= profile.max_overlap for s in selected):
            selected.append(c)
            if len(selected) >= profile.max_candidates:
                break
    for c in selected:
        c.post_title = post_title(c.first_sentence)
        c.hashtags = hashtags_for(c.text, profile)
    stats["selected"] = len(selected)
    return selected, stats


def visual_only_candidates(media_duration: float, profile: EditingProfile,
                           cuts: list[float]) -> list[Candidate]:
    """No speech to work with: group scene segments into target-length clips, or
    fall back to evenly spaced windows. Clearly labelled as a weak baseline."""
    d = profile.duration
    if media_duration < d.min_s:
        spans = [(0.0, media_duration)]
        basis = "No speech found — using the whole video (it's shorter than the minimum clip length)."
    elif cuts:
        bounds = [0.0, *[c for c in cuts if 0 < c < media_duration], media_duration]
        spans, s = [], bounds[0]
        for b in bounds[1:]:
            if b - s >= d.target_s or (b - s >= d.min_s and b == bounds[-1]):
                spans.append((s, min(b, s + d.hard_max_s)))
                s = b
        basis = "No speech found — grouped consecutive scenes into a clip-length span."
    else:
        spans, t = [], 0.0
        while t + d.min_s <= media_duration:
            spans.append((t, min(media_duration, t + d.target_s)))
            t += d.target_s
        basis = "No speech or scene changes found — evenly spaced window. Review carefully."
    out = []
    for s, e in spans[: profile.max_candidates]:
        inside = bisect_left(cuts, e) - bisect_right(cuts, s)
        activity = min(1.0, inside / max(1e-6, (e - s) / 60) / 12)
        out.append(Candidate(
            round(s, 3), round(e, 3), "", round(activity * 100, 1),
            {"visual_activity": {"value": round(activity, 3), "weight": 1.0, "evidence": None}},
            [{"feature": "fallback", "text": basis}],
            ["No transcript — captions will be empty and scoring is visual-only."],
        ))
    return out
