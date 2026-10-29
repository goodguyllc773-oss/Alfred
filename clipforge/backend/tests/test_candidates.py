"""Test 5: candidate duration and boundary validation (+ scoring transparency)."""

from __future__ import annotations

import pytest

from clipforge.analysis.candidates import generate_candidates, overlap_fraction, refine_bounds
from clipforge.analysis.scoring import SCORER_NAME
from clipforge.analysis.sentences import build_sentences, ends_sentence
from clipforge.analysis.signals import compute_loudness, detect_scenes
from clipforge.profiles.presets import BUILTIN_BY_KEY, BUILTIN_PROFILES
from clipforge.profiles.schema import DurationRules, EditingProfile
from clipforge.transcription.base import Word
from clipforge.transcription.sidecar import SidecarTranscriber


@pytest.fixture(scope="module")
def talk_words(media):
    res = SidecarTranscriber(media["talk"]["srt"]).transcribe_range(str(media["talk"]["video"]), 0, 1e9)
    return [w for s in res.segments for w in s.words]


@pytest.fixture(scope="module")
def talk_loudness(media):
    return compute_loudness(str(media["talk"]["video"]), media["talk"]["duration"])


@pytest.mark.parametrize("profile", BUILTIN_PROFILES, ids=lambda p: p.key)
def test_candidates_respect_duration_and_sentence_boundaries(profile, talk_words, talk_loudness, media):
    dur = media["talk"]["duration"]
    cands, stats = generate_candidates(talk_words, media_duration=dur, profile=profile, loudness=talk_loudness)
    assert cands, stats
    assert stats["scorer"] == SCORER_NAME
    seg = profile.segmentation
    starts = {round(s, 3) for s, _, _ in media["talk"]["segments"]}
    ends = {round(e, 3) for _, e, _ in media["talk"]["segments"]}
    for c in cands:
        overlong = any(r["feature"] == "duration" for r in c.reasons)
        limit = profile.duration.hard_max_s if overlong else profile.duration.max_s
        assert profile.duration.min_s - 1e-3 <= c.duration <= limit + 0.01, (c.start, c.end)
        assert 0 <= c.start < c.end <= dur + 1e-6
        # Clip starts just before a sentence start and ends just after a sentence end.
        inside = [w for w in talk_words if c.start <= w.start and w.end <= c.end]
        assert any(abs(inside[0].start - s) < 0.01 for s in starts), "starts mid-sentence"
        assert inside[0].start - c.start <= seg.lead_in_s + 1e-6  # lead-in never exceeds the setting
        assert any(abs(inside[-1].end - e) < 0.01 for e in ends), "ends mid-sentence"
        # No word is cut in half at either edge.
        for w in talk_words:
            assert not (w.start < c.start < w.end) and not (w.start < c.end < w.end)
        assert c.reasons and all(r["text"] for r in c.reasons)
        assert 0 <= c.score <= 100
    for i, a in enumerate(cands):
        for b in cands[i + 1:]:
            assert overlap_fraction((a.start, a.end), (b.start, b.end)) <= profile.max_overlap + 1e-9
    assert cands == sorted(cands, key=lambda c: c.score, reverse=True)


def test_hook_question_and_completeness_are_explained(talk_words, talk_loudness, media):
    cands, _ = generate_candidates(talk_words, media_duration=media["talk"]["duration"],
                                   profile=BUILTIN_BY_KEY["podcast"], loudness=talk_loudness)
    best = cands[0]
    texts = " ".join(r["text"] for r in best.reasons)
    assert "question" in texts.lower()
    assert best.features["hook"]["value"] >= 0.6
    assert best.post_title.startswith("Have you ever wondered")
    assert any(t.startswith("#") for t in best.hashtags)


def _w(text, start, step=0.3):
    out, t = [], start
    for tok in text.split():
        out.append(Word(tok, t, t + step * 0.9))
        t += step
    return out


def test_dangling_start_and_end_produce_warnings():
    words = (_w("Okay.", 0) + _w("And then he said it was fine and", 2.0)
             + _w("everyone left the room at once.", 12.0))
    prof = BUILTIN_BY_KEY["conversation"].model_copy(update={"duration": DurationRules(
        min_s=3, target_s=5, max_s=8, hard_max_s=10)})
    cands, _ = generate_candidates(words, media_duration=20, profile=prof)
    warnings = " ".join(w for c in cands for w in c.warnings)
    assert "depend on what came before" in warnings or "cut off" in warnings


def test_long_run_on_sentence_is_split_or_flagged():
    # 50 s with no punctuation and no pauses: nothing fits [30, 60] cleanly except
    # whole pieces; the splitter must break it rather than return nothing.
    words = _w(" ".join(["word"] * 170), 0.0)
    prof = BUILTIN_BY_KEY["conversation"]
    sents = build_sentences(words, max_sentence_s=prof.segmentation.max_sentence_s)
    assert all(s.duration <= prof.segmentation.max_sentence_s + 0.5 for s in sents)
    cands, _ = generate_candidates(words, media_duration=60, profile=prof)
    assert cands


def test_overlong_window_says_why():
    # One 70 s sentence: no boundary inside [30, 60] → allowed to hard_max with a reason.
    words = _w(" ".join(["talk"] * 233) + ".", 0.0)
    prof = BUILTIN_BY_KEY["conversation"].model_copy(update={
        "segmentation": BUILTIN_BY_KEY["conversation"].segmentation.model_copy(update={"max_sentence_s": 120})})
    cands, _ = generate_candidates(words, media_duration=80, profile=prof)
    assert cands and any(r["feature"] == "duration" and "mid-thought" in r["text"] for r in cands[0].reasons)


def test_refine_bounds_never_swallows_neighbours_and_snaps_to_cuts():
    prev, first, last, nxt = Word("a", 9.0, 9.9), Word("b", 10.0, 10.4), Word("c", 20.0, 20.5), Word("d", 20.6, 21)
    s, e = refine_bounds(first, last, prev, nxt, lead_in=1.0, tail=1.0, media_duration=100, scene_cuts=[])
    assert s >= 9.9 and e <= 20.6 and s <= 10.0 and e >= 20.5
    s, e = refine_bounds(first, last, None, None, lead_in=1.0, tail=1.0, media_duration=100, scene_cuts=[9.5, 20.8])
    assert s == 9.5 and e == 20.8


def test_sentence_end_detection():
    assert ends_sentence("done.") and ends_sentence("really?!") and ends_sentence('it."')
    assert not ends_sentence("Mr.") and not ends_sentence("e.g.") and not ends_sentence("so,")


def test_scene_detection_and_vlog_alignment(media):
    cuts = [c["t"] for c in detect_scenes(str(media["scenes"]), 12.0)]
    assert len(cuts) == 3
    for expected, got in zip([3.0, 6.0, 9.0], cuts):
        assert got == pytest.approx(expected, abs=0.25)


def test_profile_schema_rejects_bad_config():
    base = BUILTIN_BY_KEY["podcast"].model_dump()
    with pytest.raises(ValueError):
        EditingProfile.model_validate({**base, "duration": {"min_s": 60, "target_s": 30, "max_s": 50, "hard_max_s": 70}})
    with pytest.raises(ValueError):
        EditingProfile.model_validate({**base, "scoring": {"weights": {"virality": 1.0}}})
    with pytest.raises(ValueError):
        EditingProfile.model_validate({**base, "layout": {**base["layout"], "facecam_rect": {"x": 0.9, "y": 0, "w": 0.5, "h": 0.2}}})


def test_profiles_change_selection(talk_words, talk_loudness, media):
    """Profiles must affect which clips win, not just labels."""
    dur = media["talk"]["duration"]
    a, _ = generate_candidates(talk_words, media_duration=dur, profile=BUILTIN_BY_KEY["podcast"], loudness=talk_loudness)
    b, _ = generate_candidates(talk_words, media_duration=dur, profile=BUILTIN_BY_KEY["gaming"], loudness=talk_loudness)
    assert [(c.start, c.end) for c in a] != [(c.start, c.end) for c in b] or \
        [c.score for c in a] != [c.score for c in b]
