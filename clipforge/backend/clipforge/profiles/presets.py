"""Built-in editing profiles.

These are starting points, tuned by reasoning about each format — not learned from
performance data (there is none yet). Users can copy and adjust them.
"""

from __future__ import annotations

from .schema import (
    CaptionRules, DurationRules, EditingProfile, Framing, Layout, Rect, Scoring, Segmentation,
)

_BASE_WEIGHTS = {
    "hook": 1.6, "completeness": 1.4, "self_contained": 1.2, "energy": 0.7, "dynamics": 0.5,
    "pace": 0.6, "emphasis": 0.8, "qa": 0.4, "focus": 0.7, "duration_fit": 0.6,
    "scene_alignment": 0.0, "visual_activity": 0.0,
}


def _w(**changes: float) -> dict[str, float]:
    d = dict(_BASE_WEIGHTS)
    d.update(changes)
    return d


BUILTIN_PROFILES: list[EditingProfile] = [
    EditingProfile(
        key="podcast", name="Podcast / interview", content_type="podcast",
        description="Self-contained answers and stories. Q&A structure scores highly; "
                    "framing follows the detected face, or stacks two hosts with the split layout.",
        duration=DurationRules(min_s=30, target_s=50, max_s=60, hard_max_s=80),
        segmentation=Segmentation(sentence_pause_s=0.8, paragraph_pause_s=1.8, use_scene_cuts=False),
        scoring=Scoring(weights=_w(qa=1.4, self_contained=1.5, focus=1.0, energy=0.4, dynamics=0.3)),
        layout=Layout(kind="crop"),
        captions=CaptionRules(preset_key="bold-pop", position="lower"),
        hashtags=["podcast", "interview"],
    ),
    EditingProfile(
        key="conversation", name="Conversation / livestream talk", content_type="conversation",
        description="General talk. Balanced: strong opener, finished thought, lively delivery.",
        scoring=Scoring(weights=_w()),
        layout=Layout(kind="crop"),
        captions=CaptionRules(preset_key="bold-pop", position="lower"),
        hashtags=["stream", "clips"],
    ),
    EditingProfile(
        key="reaction", name="Reaction", content_type="reaction",
        description="Setup → payoff. Rewards loudness swings and exclamations; stacks the source "
                    "content above the reactor's camera.",
        duration=DurationRules(min_s=25, target_s=40, max_s=60, hard_max_s=75),
        segmentation=Segmentation(sentence_pause_s=0.7, paragraph_pause_s=1.4, use_scene_cuts=True,
                                  lead_in_s=1.0, tail_s=1.0),
        scoring=Scoring(weights=_w(energy=1.2, dynamics=1.4, emphasis=1.3, qa=0.1, focus=0.4,
                                   scene_alignment=0.5)),
        layout=Layout(kind="content_facecam", facecam_position="bottom", facecam_share=0.42,
                      facecam_rect=Rect(x=0.7, y=0.65, w=0.3, h=0.35), content_mode="fit"),
        captions=CaptionRules(preset_key="bold-pop", position="seam"),
        hashtags=["reaction"],
    ),
    EditingProfile(
        key="commentary", name="Commentary / debate", content_type="commentary",
        description="Arguments with a clear claim and conclusion. Rewards complete, focused points "
                    "and pointed questions.",
        duration=DurationRules(min_s=30, target_s=50, max_s=60, hard_max_s=80),
        scoring=Scoring(weights=_w(completeness=1.7, focus=1.2, qa=0.9, emphasis=1.0)),
        layout=Layout(kind="crop"),
        captions=CaptionRules(preset_key="clean-box", position="lower"),
        hashtags=["commentary", "debate"],
    ),
    EditingProfile(
        key="gaming", name="Gaming (gameplay + facecam)", content_type="gaming",
        description="Keeps the whole gameplay frame visible (fit, not crop) with the facecam "
                    "panel on top; rewards energy spikes and on-screen action.",
        duration=DurationRules(min_s=20, target_s=40, max_s=60, hard_max_s=75),
        segmentation=Segmentation(sentence_pause_s=0.7, paragraph_pause_s=2.0, use_scene_cuts=True,
                                  lead_in_s=1.0, tail_s=1.0),
        scoring=Scoring(weights=_w(hook=1.0, self_contained=0.6, energy=1.4, dynamics=1.3,
                                   emphasis=1.2, qa=0.0, focus=0.3, visual_activity=0.9,
                                   scene_alignment=0.3)),
        layout=Layout(kind="content_facecam", facecam_position="top", facecam_share=0.36,
                      facecam_rect=Rect(x=0.0, y=0.0, w=0.25, h=0.3), content_mode="fit"),
        framing=Framing(face_tracking=False),
        captions=CaptionRules(preset_key="bold-pop", position="seam", avoid_faces=False),
        hashtags=["gaming", "twitch"],
    ),
    EditingProfile(
        key="vlog", name="Vlog / IRL", content_type="vlog",
        description="Narrative continuity: snaps to scene cuts, prefers clips that start a new "
                    "scene or topic. Face-following crop; switch the layout to fit_blur to keep "
                    "the whole shot.",
        duration=DurationRules(min_s=30, target_s=45, max_s=60, hard_max_s=80),
        segmentation=Segmentation(sentence_pause_s=0.9, paragraph_pause_s=1.8, use_scene_cuts=True),
        scoring=Scoring(weights=_w(self_contained=1.4, scene_alignment=1.0, visual_activity=0.4,
                                   qa=0.2)),
        layout=Layout(kind="crop"),
        captions=CaptionRules(preset_key="clean-box", position="lower"),
        hashtags=["vlog", "irl"],
    ),
]

BUILTIN_BY_KEY = {p.key: p for p in BUILTIN_PROFILES}
DEFAULT_PROFILE_KEY = "conversation"
