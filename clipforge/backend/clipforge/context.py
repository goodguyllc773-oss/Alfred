"""Process-wide wiring: settings, database, preferences, registries.

Both the API and the worker get an ``AppContext``; tests build one per temp dir.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from .captions.style import BUILTIN_CAPTION_PRESETS, CaptionStyle
from .config import Settings
from .db.models import AppSetting, CaptionPresetRow, EditingProfileRow, SourceMedia
from .db.session import make_engine, make_session_factory, run_migrations
from .errors import NotFound, ValidationFailed
from .media import hwaccel
from .profiles.presets import BUILTIN_PROFILES
from .profiles.schema import EditingProfile
from .transcription.base import Transcriber

log = logging.getLogger("clipforge")

DEFAULT_PREFS: dict[str, Any] = {
    "transcription_engine": None,      # None → Settings.transcriber
    "whisper_model": "small",
    "whisper_device": "auto",          # auto | cuda | cpu
    "whisper_compute_type": "auto",
    "whisper_language": None,          # None → auto-detect
    "encoder_preference": "auto",      # auto | cpu | h264_nvenc | h264_qsv | h264_amf
    "output_width": 1080,
    "output_height": 1920,
    "default_export_dir": str(Path.home() / "Videos" / "ClipForge"),
    "deep_validate": True,
}
PREF_CHOICES = {
    "whisper_device": {"auto", "cuda", "cpu"},
    "encoder_preference": {"auto", "cpu", "h264_nvenc", "h264_qsv", "h264_amf"},
    "transcription_engine": {None, "faster-whisper", "sidecar"},
}
OUTPUT_SIZES = {(1080, 1920), (720, 1280), (540, 960)}


class AppContext:
    def __init__(self, settings: Settings, *, migrate: bool = True):
        self.settings = settings
        settings.ensure_dirs()
        if migrate:
            run_migrations(settings.db_url)
        self.engine = make_engine(settings.db_url)
        self.sf = make_session_factory(self.engine)
        self._hw: hwaccel.HardwareReport | None = None
        self._hw_lock = threading.Lock()
        # Tests (or a future plugin system) can swap how transcribers are built.
        self.transcriber_factory: Callable[[AppContext, SourceMedia], Transcriber] = default_transcriber
        self.seed_registries()

    @contextmanager
    def session(self) -> Iterator[Session]:
        s = self.sf()
        try:
            yield s
            s.commit()
        except BaseException:
            s.rollback()
            raise
        finally:
            s.close()

    def close(self) -> None:
        self.engine.dispose()

    # ------------------------------------------------------------------ hardware
    def hardware(self, refresh: bool = False) -> hwaccel.HardwareReport:
        with self._hw_lock:
            if self._hw is None or refresh:
                self._hw = hwaccel.detect(self.settings.ffmpeg, refresh=refresh)
            return self._hw

    # ------------------------------------------------------------------ prefs
    def prefs(self) -> dict[str, Any]:
        with self.session() as s:
            rows = {r.key: r.value for r in s.scalars(select(AppSetting)).all()}
        out = dict(DEFAULT_PREFS)
        out.update({k: v for k, v in rows.items() if k in DEFAULT_PREFS})
        return out

    def update_prefs(self, patch: dict[str, Any]) -> dict[str, Any]:
        from .transcription.whisper import MODEL_SIZES
        for k, v in patch.items():
            if k not in DEFAULT_PREFS:
                raise ValidationFailed(f"Unknown setting '{k}'.")
            if k in PREF_CHOICES and v not in PREF_CHOICES[k]:
                raise ValidationFailed(f"Invalid value for {k}: {v!r}.")
            if k == "whisper_model" and v not in MODEL_SIZES and not Path(str(v)).is_dir():
                raise ValidationFailed(f"Unknown Whisper model '{v}'.",
                                       hint="Use one of " + ", ".join(MODEL_SIZES) +
                                            " or the full path to a CTranslate2 model folder.")
            if k == "default_export_dir":
                from .media.paths import normalize_user_path
                v = str(normalize_user_path(v))
        merged = {**self.prefs(), **patch}
        if (merged["output_width"], merged["output_height"]) not in OUTPUT_SIZES:
            raise ValidationFailed("Output size must be 1080x1920, 720x1280 or 540x960.")
        with self.session() as s:
            for k, v in patch.items():
                row = s.get(AppSetting, k)
                if row is None:
                    s.add(AppSetting(key=k, value=v))
                else:
                    row.value = v
        return self.prefs()

    # ------------------------------------------------------------------ registries
    def seed_registries(self) -> None:
        """Insert/refresh built-in profiles and caption presets from code."""
        with self.session() as s:
            for p in BUILTIN_PROFILES:
                row = s.get(EditingProfileRow, p.key)
                cfg = p.model_dump(mode="json")
                if row is None:
                    s.add(EditingProfileRow(key=p.key, name=p.name, builtin=True, config=cfg))
                elif row.builtin:
                    row.name, row.config = p.name, cfg
            for key, (name, style) in BUILTIN_CAPTION_PRESETS.items():
                row = s.get(CaptionPresetRow, key)
                st = style.model_dump(mode="json")
                if row is None:
                    s.add(CaptionPresetRow(key=key, name=name, builtin=True, style=st))
                elif row.builtin:
                    row.name, row.style = name, st

    def profile(self, key: str, s: Session | None = None) -> EditingProfile:
        def _get(sess: Session) -> EditingProfile:
            row = sess.get(EditingProfileRow, key)
            if row is None:
                raise NotFound(f"Unknown editing profile '{key}'.")
            return EditingProfile.model_validate(row.config)
        if s is not None:
            return _get(s)
        with self.session() as sess:
            return _get(sess)

    def caption_style(self, key: str, s: Session | None = None) -> CaptionStyle:
        def _get(sess: Session) -> CaptionStyle:
            row = sess.get(CaptionPresetRow, key)
            if row is None:
                raise NotFound(f"Unknown caption preset '{key}'.")
            return CaptionStyle.model_validate(row.style)
        if s is not None:
            return _get(s)
        with self.session() as sess:
            return _get(sess)


def default_transcriber(ctx: AppContext, media: SourceMedia) -> Transcriber:
    from .transcription.sidecar import SidecarTranscriber, find_sidecar
    from .transcription.whisper import FasterWhisperTranscriber

    prefs = ctx.prefs()
    engine = prefs["transcription_engine"] or ctx.settings.transcriber
    if media.transcript_path:
        return SidecarTranscriber(media.transcript_path)
    if engine == "sidecar":
        return SidecarTranscriber(find_sidecar(media.original_path))
    return FasterWhisperTranscriber(
        model_size=prefs["whisper_model"], device=prefs["whisper_device"],
        compute_type=prefs["whisper_compute_type"], language=prefs["whisper_language"],
        download_root=str(ctx.settings.models_dir), ffmpeg=ctx.settings.ffmpeg,
    )
