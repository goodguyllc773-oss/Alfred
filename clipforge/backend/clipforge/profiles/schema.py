"""Editing profile schema.

One analysis + render engine, many profiles. A profile changes:
  * which windows score well      → ``scoring.weights``
  * how windows are cut           → ``duration``, ``segmentation``
  * how the clip is framed        → ``layout``, ``framing``
  * where/how captions sit        → ``captions``
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

FEATURES = (
    "hook", "completeness", "self_contained", "energy", "dynamics", "pace", "emphasis",
    "qa", "focus", "duration_fit", "scene_alignment", "visual_activity",
)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Rect(Strict):
    """Normalised rectangle in source-frame coordinates (0..1)."""
    x: float = Field(0.0, ge=0, le=1)
    y: float = Field(0.0, ge=0, le=1)
    w: float = Field(1.0, gt=0, le=1)
    h: float = Field(1.0, gt=0, le=1)

    @model_validator(mode="after")
    def _inside(self):
        if self.x + self.w > 1.0001 or self.y + self.h > 1.0001:
            raise ValueError("rectangle extends outside the frame")
        return self


class DurationRules(Strict):
    min_s: float = Field(30, ge=3, le=600)
    target_s: float = Field(45, ge=3, le=600)
    max_s: float = Field(60, ge=3, le=600)
    # Allowed when there's no natural sentence boundary inside [min, max].
    hard_max_s: float = Field(75, ge=3, le=900)

    @model_validator(mode="after")
    def _ordered(self):
        if not (self.min_s <= self.target_s <= self.max_s <= self.hard_max_s):
            raise ValueError("durations must satisfy min ≤ target ≤ max ≤ hard_max")
        return self


class Segmentation(Strict):
    sentence_pause_s: float = Field(0.8, ge=0.2, le=5)
    paragraph_pause_s: float = Field(1.6, ge=0.3, le=10)
    max_sentence_s: float = Field(20, ge=3, le=120)
    use_scene_cuts: bool = False
    scene_threshold: float = Field(0.35, ge=0.05, le=0.95)
    lead_in_s: float = Field(0.25, ge=0, le=3)
    tail_s: float = Field(0.5, ge=0, le=3)


class Scoring(Strict):
    weights: dict[str, float]

    @model_validator(mode="after")
    def _known(self):
        unknown = set(self.weights) - set(FEATURES)
        if unknown:
            raise ValueError(f"unknown scoring features: {sorted(unknown)}")
        if any(v < 0 for v in self.weights.values()):
            raise ValueError("weights must be ≥ 0")
        if sum(self.weights.values()) <= 0:
            raise ValueError("at least one weight must be > 0")
        return self


LayoutKind = Literal["crop", "fit_blur", "split_stack", "content_facecam"]


class Layout(Strict):
    kind: LayoutKind = "crop"
    # crop: manual horizontal centre (0..1) when face tracking is off/fails.
    crop_center_x: float = Field(0.5, ge=0, le=1)
    zoom: float = Field(1.0, ge=1.0, le=3.0)
    # split_stack: two regions stacked top/bottom (e.g. two podcast hosts).
    top_rect: Rect = Rect(x=0.0, y=0.0, w=0.5, h=1.0)
    bottom_rect: Rect = Rect(x=0.5, y=0.0, w=0.5, h=1.0)
    # content_facecam: gameplay/source content + a webcam/reactor region.
    facecam_rect: Rect = Rect(x=0.0, y=0.0, w=0.25, h=0.3)
    content_rect: Rect = Rect()
    facecam_position: Literal["top", "bottom"] = "top"
    facecam_share: float = Field(0.36, ge=0.15, le=0.6)
    content_mode: Literal["fit", "crop"] = "fit"


class Framing(Strict):
    face_tracking: bool = True
    samples: int = Field(5, ge=1, le=30)


CaptionPosition = Literal["lower", "middle", "upper", "seam"]


class CaptionRules(Strict):
    preset_key: str = "bold-pop"
    position: CaptionPosition = "lower"
    avoid_faces: bool = True


class EditingProfile(Strict):
    key: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")
    name: str
    description: str = ""
    content_type: str
    duration: DurationRules = DurationRules()
    segmentation: Segmentation = Segmentation()
    scoring: Scoring
    layout: Layout = Layout()
    framing: Framing = Framing()
    captions: CaptionRules = CaptionRules()
    max_candidates: int = Field(20, ge=1, le=200)
    # Max fraction of the shorter clip two candidates may share.
    max_overlap: float = Field(0.3, ge=0, le=0.9)
    hashtags: list[str] = []

    def with_layout_overrides(self, overrides: dict) -> "EditingProfile":
        if not overrides:
            return self
        merged = self.layout.model_dump()
        merged.update(overrides)
        return self.model_copy(update={"layout": Layout.model_validate(merged)})
