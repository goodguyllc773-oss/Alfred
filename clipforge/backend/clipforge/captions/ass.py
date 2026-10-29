"""Word-timed ASS subtitle generation.

Words (absolute times) → clip-relative, cleaned timings → caption *pages* (a few
words, ≤ ``max_lines`` lines) → one ASS ``Dialogue`` per word interval that shows
the whole page with the active word highlighted (and optionally "popped").

Platform-safe margins (fractions of the frame): top 12 %, bottom 22 %, sides 8 %.
TikTok/Reels put the caption, account name and buttons there.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..transcription.base import Word
from .style import CaptionStyle

SAFE_TOP = 0.12
SAFE_BOTTOM = 0.22
SAFE_SIDE = 0.08
LINE_HEIGHT = 1.18
PAGE_HOLD_S = 0.4
MIN_EVENT_S = 0.05

POSITION_Y = {"upper": 0.27, "middle": 0.50, "lower": 0.68}


@dataclass
class CWord:
    text: str
    start: float
    end: float


@dataclass
class Page:
    lines: list[list[CWord]]

    @property
    def words(self) -> list[CWord]:
        return [w for line in self.lines for w in line]

    @property
    def start(self) -> float:
        return self.words[0].start

    @property
    def end(self) -> float:
        return self.words[-1].end


def ass_color(hex_rgb: str, alpha: float = 0.0) -> str:
    """'#RRGGBB' + alpha (0 opaque .. 1 transparent) → '&HAABBGGRR'."""
    r, g, b = hex_rgb[1:3], hex_rgb[3:5], hex_rgb[5:7]
    a = int(round(max(0.0, min(1.0, alpha)) * 255))
    return f"&H{a:02X}{b}{g}{r}".upper()


def ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def escape_text(s: str) -> str:
    # ASS has no reliable escape for override braces; swap them for look-alikes.
    return s.replace("\\", "／").replace("{", "(").replace("}", ")").replace("\n", " ")


def clip_words(words: list[Word], clip_start: float, clip_end: float) -> list[CWord]:
    """Select words overlapping the clip, convert to clip time, make timings
    monotonic with a minimum duration, and clamp to the clip."""
    dur = clip_end - clip_start
    out: list[CWord] = []
    prev_end = 0.0
    for w in words:
        mid = (w.start + w.end) / 2
        if mid < clip_start or mid > clip_end:
            continue
        s = max(0.0, w.start - clip_start, prev_end)
        e = min(dur, max(w.end - clip_start, s + MIN_EVENT_S))
        if e - s < MIN_EVENT_S:
            if s >= dur:
                break
            e = min(dur, s + MIN_EVENT_S)
        text = w.text.strip()
        if text:
            out.append(CWord(text, s, e))
            prev_end = e
    return out


def _sentence_end(t: str) -> bool:
    return t.rstrip("\"'”’)]").endswith((".", "?", "!", "…"))


def build_pages(words: list[CWord], style: CaptionStyle) -> list[Page]:
    pages: list[Page] = []
    lines: list[list[CWord]] = [[]]

    def line_len(line: list[CWord]) -> int:
        return sum(len(w.text) for w in line) + max(0, len(line) - 1)

    def flush():
        nonlocal lines
        if any(lines):
            pages.append(Page([l for l in lines if l]))
        lines = [[]]

    for i, w in enumerate(words):
        prev = words[i - 1] if i else None
        if prev is not None and any(lines):
            n_words = sum(len(l) for l in lines)
            gap = w.start - prev.end
            if (_sentence_end(prev.text) or gap > 1.0 or n_words >= style.max_words_per_page
                    or (prev.text.endswith((",", ";", ":")) and n_words >= max(2, style.max_words_per_page // 2))):
                flush()
        cur = lines[-1]
        if cur and line_len(cur) + 1 + len(w.text) > style.max_chars_per_line:
            if len(lines) >= style.max_lines:
                flush()
            else:
                lines.append([])
        lines[-1].append(w)
    flush()
    return pages


def _word_markup(text: str, *, active: bool, spoken: bool, style: CaptionStyle,
                 hl: str, base: str) -> str:
    t = escape_text(text.upper() if style.uppercase else text)
    if style.highlight_mode == "progressive":
        return (f"{{\\1c{hl}}}{t}{{\\1c{base}}}" if (active or spoken) else t)
    if not active:
        return t
    if style.animation == "pop":
        p = style.pop_scale
        return (f"{{\\1c{hl}\\fscx{p}\\fscy{p}\\t(0,110,\\fscx{100 + (p - 100) // 3}"
                f"\\fscy{100 + (p - 100) // 3})}}{t}{{\\1c{base}\\fscx100\\fscy100}}")
    if style.animation == "color" or style.highlight_color != style.primary_color:
        return f"{{\\1c{hl}}}{t}{{\\1c{base}}}"
    return t


def caption_block_height(style: CaptionStyle, out_h: int, lines: int | None = None) -> float:
    scale = out_h / 1920
    return (lines or style.max_lines) * style.font_size * scale * LINE_HEIGHT


def resolve_position_y(style: CaptionStyle, position: str, out_h: int, *, seam_y: float | None = None,
                       face_box_out: tuple[float, float, float, float] | None = None,
                       avoid_faces: bool = True) -> float:
    """Centre y (0..1) of the caption block, kept inside the safe zone and, if
    asked, moved off a face (``face_box_out`` = normalised box in output coords)."""
    block = caption_block_height(style, out_h) / out_h
    lo, hi = SAFE_TOP + block / 2, 1 - SAFE_BOTTOM - block / 2
    if style.position_y is not None:
        y = style.position_y
    elif position == "seam" and seam_y is not None:
        y = seam_y
    else:
        y = POSITION_Y.get(position, POSITION_Y["lower"])
    y = min(hi, max(lo, y))
    if avoid_faces and face_box_out is not None and style.position_y is None:
        fx, fy, fw, fh = face_box_out
        top, bottom = y - block / 2, y + block / 2
        if top < fy + fh and bottom > fy:  # overlap
            below = fy + fh + block / 2 + 0.02
            above = fy - block / 2 - 0.02
            if below <= hi:
                y = below
            elif above >= lo:
                y = above
    return round(y, 4)


def build_ass(words: list[CWord], style: CaptionStyle, out_w: int, out_h: int, center_y: float) -> str:
    scale = out_h / 1920
    fs = max(10, int(round(style.font_size * scale)))
    base, hl = ass_color(style.primary_color), ass_color(style.highlight_color)
    outline_c = ass_color(style.outline_color)
    if style.box:
        border_style, outline, shadow = 3, max(4.0, fs * 0.18), 0
        back = ass_color(style.box_color, 1 - style.box_opacity)
        outline_c = back  # BorderStyle 3 paints the box with OutlineColour
    else:
        border_style, outline, shadow = 1, style.outline_px * scale, style.shadow_px * scale
        back = ass_color("#000000", 0.4)
    margin_side = int(out_w * SAFE_SIDE)
    header = "\n".join([
        "[Script Info]",
        "; Generated by ClipForge",
        "ScriptType: v4.00+",
        f"PlayResX: {out_w}",
        f"PlayResY: {out_h}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Caption,{style.font_family},{fs},{base},{hl},{outline_c},{back},"
        f"{-1 if style.bold else 0},0,0,0,100,100,0,0,{border_style},{outline:.1f},{shadow:.1f},"
        f"5,{margin_side},{margin_side},0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ])
    x, y = out_w // 2, int(round(center_y * out_h))
    events = []
    pages = build_pages(words, style)
    for pi, page in enumerate(pages):
        nxt_start = pages[pi + 1].start if pi + 1 < len(pages) else None
        page_end = page.end + PAGE_HOLD_S
        if nxt_start is not None:
            page_end = min(page_end, nxt_start)
        page_words = page.words
        for k, w in enumerate(page_words):
            ev_start = w.start
            ev_end = page_words[k + 1].start if k + 1 < len(page_words) else page_end
            if ev_end - ev_start < MIN_EVENT_S:
                continue
            parts = []
            idx = 0
            for line in page.lines:
                seg = []
                for lw in line:
                    seg.append(_word_markup(lw.text, active=idx == k, spoken=idx < k, style=style,
                                            hl=hl, base=base))
                    idx += 1
                parts.append(" ".join(seg))
            text = "\\N".join(parts)
            events.append(f"Dialogue: 0,{ass_time(ev_start)},{ass_time(ev_end)},Caption,,0,0,0,,"
                          f"{{\\an5\\pos({x},{y})}}{text}")
    return header + "\n" + "\n".join(events) + "\n"
