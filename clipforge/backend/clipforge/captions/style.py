"""Caption style schema and built-in presets.

Sizes are defined for a 1920-px-tall frame and scaled to the actual output height.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


class CaptionStyle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    font_family: str = Field("Arial", min_length=1, max_length=80)
    bold: bool = True
    font_size: int = Field(84, ge=20, le=220)
    uppercase: bool = False
    primary_color: str = "#FFFFFF"
    highlight_color: str = "#FFE14D"
    outline_color: str = "#000000"
    outline_px: float = Field(6, ge=0, le=20)
    shadow_px: float = Field(2, ge=0, le=20)
    box: bool = False  # opaque box behind text instead of outline
    box_color: str = "#000000"
    box_opacity: float = Field(0.6, ge=0, le=1)
    highlight_mode: Literal["word", "progressive"] = "word"
    animation: Literal["pop", "color", "none"] = "pop"
    pop_scale: int = Field(118, ge=100, le=160)
    max_chars_per_line: int = Field(18, ge=6, le=60)
    max_lines: int = Field(2, ge=1, le=4)
    max_words_per_page: int = Field(6, ge=1, le=20)
    # Overrides the profile's position when set (centre of the caption block, 0..1 of height).
    position_y: float | None = Field(None, ge=0.05, le=0.95)

    @field_validator("primary_color", "highlight_color", "outline_color", "box_color")
    @classmethod
    def _hex(cls, v: str) -> str:
        if not _HEX.match(v):
            raise ValueError("colors must be #RRGGBB")
        return v.upper()

    @field_validator("font_family")
    @classmethod
    def _font(cls, v: str) -> str:
        if any(c in v for c in ",{}\\\n\r"):
            raise ValueError("font name contains invalid characters")
        return v.strip()


BUILTIN_CAPTION_PRESETS: dict[str, tuple[str, CaptionStyle]] = {
    "bold-pop": ("Bold pop (yellow word)", CaptionStyle()),
    "clean-box": ("Clean box", CaptionStyle(
        font_size=66, box=True, outline_px=0, shadow_px=0, highlight_color="#7CE7FF",
        animation="color", max_chars_per_line=24, max_words_per_page=8)),
    "neon-caps": ("Neon caps", CaptionStyle(
        uppercase=True, font_size=90, highlight_color="#39FF88", outline_px=7, pop_scale=125,
        max_chars_per_line=14, max_words_per_page=4)),
    "karaoke": ("Karaoke fill", CaptionStyle(
        highlight_mode="progressive", animation="none", highlight_color="#FF5EA8",
        max_chars_per_line=20)),
    "minimal": ("Minimal (no highlight)", CaptionStyle(
        font_size=64, bold=False, highlight_color="#FFFFFF", animation="none", outline_px=3,
        shadow_px=1, max_chars_per_line=26, max_words_per_page=9)),
}
DEFAULT_CAPTION_PRESET = "bold-pop"
