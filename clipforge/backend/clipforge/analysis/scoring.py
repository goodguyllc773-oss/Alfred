"""``heuristic-v1``: a transparent baseline clip scorer.

Every feature is a number in [0, 1] computed from the transcript, the loudness
curve and scene cuts, with the evidence kept so the UI can say *why*. The final
score is the profile-weighted mean × 100.

Limitations (deliberately stated): this does not understand meaning. It can't tell
a funny line from a flat one, follow who is speaking, or judge whether a story pays
off. It rewards structural signals that correlate with good clips — a hook-like
opener, a finished thought, a self-contained start, lively delivery. A semantic
re-ranker can be plugged in through ``analysis/semantic.py``.
"""

from __future__ import annotations

import math
import re
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from ..profiles.schema import FEATURES, EditingProfile
from .sentences import Sentence
from .signals import SILENCE_DB, Loudness

SCORER_NAME = "heuristic-v1"

HOOK_PHRASES = re.compile(
    r"\b(here'?s the thing|the (craziest|weirdest|funniest|worst|best|biggest|scariest)|"
    r"you (won'?t|will not) believe|i('ve)? never (told|said)|nobody (talks|knows)|"
    r"the truth is|let me tell you|the secret|the problem (is|with)|the reason|"
    r"what (happened|nobody)|this is (why|how|what)|i (can'?t|cannot) believe|"
    r"story time|true story|hot take|unpopular opinion|number one|the first time|"
    r"guess what|listen|honestly|imagine)\b", re.I)
DANGLING_START = {"and", "but", "so", "because", "or", "which", "then", "also", "plus", "like",
                  "though", "anyway", "cause", "'cause"}
FILLER = {"yeah", "yep", "okay", "ok", "um", "uh", "mhm", "right", "sure", "hmm", "yes", "no"}
BACKREF = {"that", "this", "it", "he", "she", "they", "those", "these", "him", "her", "them"}
DANGLING_END = {"and", "but", "so", "because", "the", "a", "an", "to", "of", "or", "with", "like",
                "if", "that", "is", "my", "your"}
LAUGH = re.compile(r"\b(ha(ha)+|lol|lmao)\b|\[(laughter|laughs?)\]|\((laughter|laughs?)\)", re.I)
INTENSIFIERS = re.compile(
    r"\b(insane|crazy|literally|never|always|worst|best|unbelievable|incredible|wild|"
    r"ridiculous|absolutely|seriously|actually|huge|massive|terrible|amazing|obsessed|"
    r"hate|love|wow|oh my god|no way|what the)\b", re.I)
STOPWORDS = set("""
a about above after again against all am an and any are aren't as at be because been before being
below between both but by can can't cannot could couldn't did didn't do does doesn't doing don't
down during each few for from further had hadn't has hasn't have haven't having he he'd he'll he's
her here here's hers herself him himself his how how's i i'd i'll i'm i've if in into is isn't it
it's its itself just know let's like me more most mustn't my myself no nor not now of off on once
only or other ought our ours ourselves out over own really right same say said she she'd she'll
she's should shouldn't so some such than that that's the their theirs them themselves then there
there's these they they'd they'll they're they've thing things think this those through to too
under until up very was wasn't we we'd we'll we're we've were weren't what what's when when's
where where's which while who who's whom why why's will with won't would wouldn't yeah you you'd
you'll you're you've your yours yourself yourselves gonna wanna gotta kind sort lot going get got
one two also well even much many okay because something anything everything people mean
""".split())


def _tok(word: str) -> str:
    return re.sub(r"^[^\w']+|[^\w']+$", "", word.lower())


def _quote(text: str, n: int = 70) -> str:
    text = text.strip()
    return text if len(text) <= n else text[: n - 1].rsplit(" ", 1)[0] + "…"


@dataclass
class ScoreResult:
    score: float
    features: dict[str, dict] = field(default_factory=dict)  # name → {value, weight, evidence}
    reasons: list[dict] = field(default_factory=list)        # [{feature, text}]
    warnings: list[str] = field(default_factory=list)


@dataclass
class ScoringContext:
    loudness: Loudness | None
    median_db: float
    global_std_db: float
    scene_cuts: list[float]
    profile: EditingProfile


def build_context(loudness: Loudness | None, scene_cuts: list[float], profile: EditingProfile) -> ScoringContext:
    if loudness and loudness.db:
        arr = np.asarray(loudness.db, dtype=np.float32)
        voiced = arr[arr > SILENCE_DB]
        median = float(np.median(voiced)) if voiced.size else float(np.median(arr))
        std = float(np.std(voiced)) if voiced.size > 1 else 1.0
    else:
        median, std = 0.0, 1.0
    return ScoringContext(loudness, median, max(std, 0.5), sorted(scene_cuts), profile)


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def _near(sorted_vals: list[float], t: float, tol: float) -> bool:
    i = bisect_left(sorted_vals, t - tol)
    return i < len(sorted_vals) and sorted_vals[i] <= t + tol


def score_window(window: list[Sentence], ctx: ScoringContext, start: float, end: float) -> ScoreResult:
    p = ctx.profile
    first, last = window[0], window[-1]
    words = [w for s in window for w in s.words]
    text = " ".join(s.text for s in window)
    duration = max(0.1, end - start)
    f: dict[str, float] = {}
    ev: dict[str, str] = {}
    warnings: list[str] = []

    # ---- hook
    first_tok = _tok(first.words[0].text)
    h = 0.3
    if first.is_question:
        h += 0.35
        ev["hook"] = f'Opens with a question: "{_quote(first.text)}"'
    m = HOOK_PHRASES.search(first.text)
    if m:
        h += 0.35
        ev.setdefault("hook", f'Opens with a hook ("{m.group(0)}"): "{_quote(first.text)}"')
    if first_tok in DANGLING_START:
        h -= 0.35
        warnings.append(f"Starts with “{first.words[0].text}” — may depend on what came before.")
    if len(first.words) <= 2 and all(_tok(w.text) in FILLER for w in first.words):
        h -= 0.2
        warnings.append("Opens with filler (“" + first.text + "”).")
    f["hook"] = min(1.0, max(0.0, h))

    # ---- completeness
    last_tok = _tok(last.words[-1].text)
    c = 0.55 if last.terminal else 0.15
    c += min(0.45, max(0.0, last.pause_after - 0.2) * 0.35)
    if last_tok in DANGLING_END and not last.terminal:
        c = 0.05
        warnings.append(f"Ends on “{last.words[-1].text}” — the thought may be cut off.")
    if last.is_question and last.pause_after < 1.0:
        c -= 0.25
        warnings.append("Ends on a question — the answer is outside the clip.")
    f["completeness"] = min(1.0, max(0.0, c))
    if f["completeness"] >= 0.6:
        pause = "end of the video" if last.pause_after > 900 else f"a {last.pause_after:.1f}s pause"
        ev["completeness"] = f"Ends on a finished sentence followed by {pause}."

    # ---- self-contained
    sc = 0.35
    if first.paragraph_start:
        sc += 0.5
        ev["self_contained"] = ("Starts right after a scene change." if first.scene_cut_before
                                else f"Starts a new thought after a {first.pause_before:.1f}s pause."
                                if first.index else "Starts at the beginning of the video.")
    else:
        sc += min(0.35, first.pause_before * 0.22)
    if first_tok in BACKREF:
        sc -= 0.25
        warnings.append(f"Opens with “{first.words[0].text}” — check it doesn't refer to something earlier.")
    if first_tok in DANGLING_START:
        sc -= 0.2
    f["self_contained"] = min(1.0, max(0.0, sc))

    # ---- energy / dynamics
    if ctx.loudness and ctx.loudness.db:
        arr = ctx.loudness.slice(start, end)
        voiced = arr[arr > SILENCE_DB]
        if voiced.size:
            diff = float(np.mean(voiced)) - ctx.median_db
            f["energy"] = _sigmoid(diff / 2.5)
            if diff >= 1.5:
                ev["energy"] = f"Delivery is {diff:.1f} dB louder than this video's typical level."
            std = float(np.std(voiced))
            peaks = int(np.sum(voiced > ctx.median_db + 9))
            f["dynamics"] = min(1.0, 0.5 * std / ctx.global_std_db + min(0.5, peaks / max(1, duration / 60) / 20))
            if f["dynamics"] >= 0.6:
                ev["dynamics"] = (f"Big volume swings ({peaks} loud peaks) — often laughter or a reaction."
                                  if peaks else "Lively, varied delivery.")
        else:
            f["energy"] = f["dynamics"] = 0.0
            warnings.append("Mostly silent.")
    else:
        f["energy"] = f["dynamics"] = 0.5  # unknown → neutral

    # ---- pace
    wps = len(words) / duration
    if 2.2 <= wps <= 3.6:
        pace = 1.0
    elif wps < 2.2:
        pace = max(0.0, wps / 2.2)
    else:
        pace = max(0.0, 1 - (wps - 3.6) / 2.5)
    dead = sum(max(0.0, b.start - a.end - 1.0) for a, b in zip(words, words[1:]))
    dead_frac = dead / duration
    pace *= max(0.0, 1 - 2 * dead_frac)
    f["pace"] = pace
    if pace >= 0.7:
        ev["pace"] = f"Steady pace: {wps:.1f} words/s with little dead air."
    if dead_frac > 0.2:
        warnings.append(f"{dead:.0f}s of dead air inside the clip.")

    # ---- emphasis
    hits = text.count("!") + len(LAUGH.findall(text))
    intens = INTENSIFIERS.findall(text)
    hits += len(intens)
    rate = hits / (duration / 60)
    f["emphasis"] = min(1.0, rate / 8)
    if f["emphasis"] >= 0.5:
        sample = ", ".join(sorted({(i if isinstance(i, str) else i[0]).lower() for i in intens})[:3])
        ev["emphasis"] = f"{hits} exclamations / laughs / intensifiers" + (f" ({sample})." if sample else ".")

    # ---- Q&A
    qa = 0.0
    for i, s in enumerate(window):
        if s.is_question and s.start - start <= 0.4 * duration:
            answer = sum(x.duration for x in window[i + 1:] if not x.is_question)
            if answer >= 12:
                qa = 1.0
                ev["qa"] = f'Question followed by a {answer:.0f}s answer: "{_quote(s.text, 60)}"'
                break
            qa = max(qa, 0.3)
    f["qa"] = qa

    # ---- topic focus
    content = [t for t in (_tok(w.text) for w in words) if len(t) >= 4 and t not in STOPWORDS]
    if content:
        counts = Counter(content)
        rep = sum(c - 1 for c in counts.values() if c > 1) / len(content)
        f["focus"] = min(1.0, rep * 2.5)
        top = [w for w, c in counts.most_common(3) if c > 1]
        if f["focus"] >= 0.5 and top:
            ev["focus"] = "Stays on one topic: " + ", ".join(f"“{w}”" for w in top) + " keep coming up."
    else:
        f["focus"] = 0.0

    # ---- duration fit
    d = p.duration
    f["duration_fit"] = max(0.0, 1 - abs(duration - d.target_s) / max(1.0, d.max_s - d.min_s))
    if f["duration_fit"] >= 0.8:
        ev["duration_fit"] = f"{duration:.0f}s — close to the {d.target_s:.0f}s target."

    # ---- scene features
    cuts = ctx.scene_cuts
    if cuts:
        f["scene_alignment"] = 0.5 * _near(cuts, start, 1.0) + 0.5 * _near(cuts, end, 1.0)
        if f["scene_alignment"] >= 0.5:
            ev["scene_alignment"] = "Cut points line up with scene changes."
        inside = bisect_left(cuts, end) - bisect_right(cuts, start)
        cpm = inside / (duration / 60)
        f["visual_activity"] = min(1.0, cpm / 12)
        if f["visual_activity"] >= 0.5:
            ev["visual_activity"] = f"{cpm:.0f} scene changes per minute — lots happening on screen."
    else:
        f["scene_alignment"] = f["visual_activity"] = 0.0

    weights = p.scoring.weights
    total_w = sum(weights.get(k, 0.0) for k in FEATURES)
    score = 100 * sum(weights.get(k, 0.0) * f.get(k, 0.0) for k in FEATURES) / total_w
    contributions = sorted(
        ((weights.get(k, 0.0) * f[k], k) for k in FEATURES if k in ev and weights.get(k, 0.0) > 0),
        reverse=True)
    reasons = [{"feature": k, "text": ev[k]} for _, k in contributions[:4]]
    features = {k: {"value": round(f.get(k, 0.0), 3), "weight": weights.get(k, 0.0),
                    "evidence": ev.get(k)} for k in FEATURES}
    return ScoreResult(round(score, 1), features, reasons, warnings)
