"""Test 6: caption generation and punctuation (+ placement rules)."""

from __future__ import annotations

import re

import pytest

from clipforge.captions.ass import (
    SAFE_BOTTOM, SAFE_TOP, ass_color, ass_time, build_ass, build_pages, caption_block_height,
    clip_words, escape_text, resolve_position_y,
)
from clipforge.captions.style import BUILTIN_CAPTION_PRESETS, CaptionStyle
from clipforge.transcription.base import Word

TEXT = ("Here's the thing, nobody tells you this. The first three seconds decide almost everything! "
        "Do you agree? I think so.")


def words_for(text: str, start=100.0, step=0.35):
    out, t = [], start
    for tok in text.split():
        out.append(Word(tok, t, t + step * 0.85))
        t += step
    return out


def test_pages_preserve_punctuation_and_limits():
    style = CaptionStyle(max_chars_per_line=16, max_lines=2, max_words_per_page=6)
    cw = clip_words(words_for(TEXT), 100.0, 130.0)
    pages = build_pages(cw, style)
    rebuilt = " ".join(w.text for p in pages for w in p.words)
    assert rebuilt == TEXT  # nothing dropped, punctuation intact, order kept
    for p in pages:
        assert len(p.lines) <= 2
        assert len(p.words) <= 6
        for line in p.lines:
            assert sum(len(w.text) for w in line) + len(line) - 1 <= 16 or len(line) == 1
    # Sentence ends close a page: no page continues past "this." / "everything!" / "agree?"
    for p in pages:
        for w in p.words[:-1]:
            assert not w.text.endswith((".", "!", "?"))


def test_clip_words_relative_monotonic_and_clamped():
    ws = words_for("one two three four five six", start=10.0)
    ws[2] = Word("three", ws[1].start, ws[1].start)  # broken: zero-length and overlapping
    cw = clip_words(ws, 10.3, 11.6)
    assert cw[0].start >= 0 and cw[-1].end <= 1.3 + 1e-9
    for a, b in zip(cw, cw[1:]):
        assert a.end <= b.start + 1e-9 and a.end - a.start >= 0.049
    assert [w.text for w in cw][0] == "two"  # "one" mostly before the clip start


def test_ass_document_structure_and_highlight():
    style = BUILTIN_CAPTION_PRESETS["bold-pop"][1]
    cw = clip_words(words_for(TEXT), 100.0, 130.0)
    doc = build_ass(cw, style, 1080, 1920, 0.68)
    assert "PlayResX: 1080" in doc and "PlayResY: 1920" in doc and "WrapStyle: 2" in doc
    dialogues = [l for l in doc.splitlines() if l.startswith("Dialogue:")]
    assert len(dialogues) == len(cw)  # one event per spoken word
    hl = ass_color(style.highlight_color)
    for line in dialogues:
        assert line.count(f"\\1c{hl}") == 1   # exactly one active word
        assert "\\pos(540,1306)" in line
    # Event times are increasing and never overlap.
    spans = [tuple(re.match(r"Dialogue: 0,([^,]+),([^,]+),", l).groups()) for l in dialogues]
    secs = [(_s(a), _s(b)) for a, b in spans]
    for (s1, e1), (s2, e2) in zip(secs, secs[1:]):
        assert s1 < e1 <= s2 + 1e-6


def _s(t: str) -> float:
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def test_progressive_mode_colors_spoken_words():
    style = BUILTIN_CAPTION_PRESETS["karaoke"][1]
    cw = clip_words(words_for("alpha beta gamma"), 100, 102)
    doc = build_ass(cw, style, 1080, 1920, 0.5)
    last = [l for l in doc.splitlines() if l.startswith("Dialogue:")][-1]
    assert last.count("\\1c" + ass_color(style.highlight_color)) == 3


def test_escaping_and_uppercase():
    assert escape_text("a {b} \\c") == "a (b) ／c"
    style = CaptionStyle(uppercase=True)
    cw = clip_words(words_for("{weird} text"), 100, 101)
    doc = build_ass(cw, style, 540, 960, 0.5)
    assert "(WEIRD)" in doc and "{weird}" not in doc


def test_colors_and_times():
    assert ass_color("#FFE14D") == "&H004DE1FF"
    assert ass_color("#000000", 0.4) == "&H66000000"
    assert ass_time(3725.456) == "1:02:05.46"


def test_position_stays_in_safe_zone_and_avoids_faces():
    style = CaptionStyle()
    block = caption_block_height(style, 1920) / 1920
    for pos in ("upper", "middle", "lower"):
        y = resolve_position_y(style, pos, 1920)
        assert SAFE_TOP + block / 2 - 1e-6 <= y <= 1 - SAFE_BOTTOM - block / 2 + 1e-6
    # A face sitting exactly where the lower captions would go pushes them away.
    lower = resolve_position_y(style, "lower", 1920)
    face = (0.35, lower - 0.08, 0.3, 0.16)
    moved = resolve_position_y(style, "lower", 1920, face_box_out=face)
    assert moved != lower
    top, bottom = moved - block / 2, moved + block / 2
    assert bottom <= face[1] or top >= face[1] + face[3]


def test_style_validation():
    with pytest.raises(ValueError):
        CaptionStyle(primary_color="white")
    with pytest.raises(ValueError):
        CaptionStyle(font_family="Arial,{\\b1}")


def test_no_words_gives_empty_events():
    doc = build_ass([], CaptionStyle(), 540, 960, 0.5)
    assert "Dialogue:" not in doc
