"""SQLAlchemy 2 data model.

Schema changes go through Alembic (``db/migrations``) — never ``create_all`` in
production code. IDs are UUID4 strings so rows stay stable across machines.
Timestamps are naive UTC.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


# --------------------------------------------------------------------------- projects / media
class Project(TimestampMixin, Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    default_profile_key: Mapped[str] = mapped_column(String(64), default="conversation", nullable=False)
    # Optional per-project export folder; falls back to the app-wide setting.
    export_dir: Mapped[str | None] = mapped_column(Text, nullable=True)

    media: Mapped[list[SourceMedia]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="SourceMedia.created_at"
    )


class SourceMedia(TimestampMixin, Base):
    __tablename__ = "source_media"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    # Last known absolute location. Never treated as the identity of the file —
    # ``fingerprint`` is. See media/paths.py for the relink strategy.
    original_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_name: Mapped[str] = mapped_column(String(400), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), default="ready", nullable=False)  # ready|missing|invalid

    container: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    duration_s: Mapped[float] = mapped_column(Float, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    fps: Mapped[float] = mapped_column(Float, nullable=False)
    video_codec: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    rotation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    has_audio: Mapped[bool] = mapped_column(Boolean, nullable=False)
    audio_codec: Mapped[str | None] = mapped_column(String(32), nullable=True)
    audio_channels: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sample_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    probe: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    profile_key: Mapped[str] = mapped_column(String(64), nullable=False)
    # Optional user-supplied transcript (.srt/.vtt/.json) used instead of Whisper.
    transcript_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    project: Mapped[Project] = relationship(back_populates="media")
    transcripts: Mapped[list[Transcript]] = relationship(
        back_populates="media", cascade="all, delete-orphan", order_by="Transcript.created_at"
    )
    candidates: Mapped[list[CandidateClip]] = relationship(
        back_populates="media", cascade="all, delete-orphan"
    )


# --------------------------------------------------------------------------- transcripts
class Transcript(TimestampMixin, Base):
    __tablename__ = "transcripts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    media_id: Mapped[str] = mapped_column(ForeignKey("source_media.id", ondelete="CASCADE"), index=True)
    engine: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    device: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="partial", nullable=False)  # partial|complete
    chunks_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    chunks_done: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    word_timestamps: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    media: Mapped[SourceMedia] = relationship(back_populates="transcripts")
    chunks: Mapped[list[TranscriptChunk]] = relationship(
        back_populates="transcript", cascade="all, delete-orphan", order_by="TranscriptChunk.index"
    )
    segments: Mapped[list[TranscriptSegment]] = relationship(
        back_populates="transcript", cascade="all, delete-orphan",
        order_by="TranscriptSegment.start_s",
    )


class TranscriptChunk(Base):
    __tablename__ = "transcript_chunks"
    __table_args__ = (UniqueConstraint("transcript_id", "index", name="uq_chunk_index"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    transcript_id: Mapped[str] = mapped_column(ForeignKey("transcripts.id", ondelete="CASCADE"), index=True)
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    start_s: Mapped[float] = mapped_column(Float, nullable=False)
    end_s: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)  # pending|done

    transcript: Mapped[Transcript] = relationship(back_populates="chunks")


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    transcript_id: Mapped[str] = mapped_column(ForeignKey("transcripts.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    idx: Mapped[int] = mapped_column(Integer, nullable=False)
    start_s: Mapped[float] = mapped_column(Float, nullable=False)
    end_s: Mapped[float] = mapped_column(Float, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # [{"w": "Hello,", "s": 1.23, "e": 1.61, "p": 0.98, "est": false}, ...]
    words: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    avg_logprob: Mapped[float | None] = mapped_column(Float, nullable=True)
    no_speech_prob: Mapped[float | None] = mapped_column(Float, nullable=True)

    transcript: Mapped[Transcript] = relationship(back_populates="segments")


# --------------------------------------------------------------------------- analysis / candidates
class AnalysisRun(TimestampMixin, Base):
    __tablename__ = "analysis_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    media_id: Mapped[str] = mapped_column(ForeignKey("source_media.id", ondelete="CASCADE"), index=True)
    transcript_id: Mapped[str | None] = mapped_column(ForeignKey("transcripts.id", ondelete="SET NULL"), nullable=True)
    profile_key: Mapped[str] = mapped_column(String(64), nullable=False)
    scorer: Mapped[str] = mapped_column(String(32), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class CandidateClip(TimestampMixin, Base):
    __tablename__ = "candidate_clips"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    media_id: Mapped[str] = mapped_column(ForeignKey("source_media.id", ondelete="CASCADE"), index=True)
    analysis_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    rank: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    start_s: Mapped[float] = mapped_column(Float, nullable=False)
    end_s: Mapped[float] = mapped_column(Float, nullable=False)
    original_start_s: Mapped[float] = mapped_column(Float, nullable=False)
    original_end_s: Mapped[float] = mapped_column(Float, nullable=False)
    transcript_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    score_breakdown: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    reasons: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    warnings: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    profile_key: Mapped[str] = mapped_column(String(64), nullable=False)
    caption_preset_key: Mapped[str] = mapped_column(String(64), nullable=False)
    layout_overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    caption_overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    review_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False, index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    post_title: Mapped[str] = mapped_column(Text, default="", nullable=False)
    post_hashtags: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)

    media: Mapped[SourceMedia] = relationship(back_populates="candidates")
    renders: Mapped[list[RenderedClip]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan", order_by="RenderedClip.version"
    )


class RenderedClip(TimestampMixin, Base):
    __tablename__ = "rendered_clips"
    __table_args__ = (UniqueConstraint("candidate_id", "version", name="uq_render_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidate_clips.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="rendering", nullable=False)  # rendering|completed|failed
    rel_path: Mapped[str | None] = mapped_column(Text, nullable=True)  # relative to data dir
    start_s: Mapped[float] = mapped_column(Float, nullable=False)
    end_s: Mapped[float] = mapped_column(Float, nullable=False)
    profile_key: Mapped[str] = mapped_column(String(64), nullable=False)
    caption_preset_key: Mapped[str] = mapped_column(String(64), nullable=False)
    encoder: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    width: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    height: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    validation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    candidate: Mapped[CandidateClip] = relationship(back_populates="renders")
    exports: Mapped[list[ExportRecord]] = relationship(back_populates="render", cascade="all, delete-orphan")


class ExportRecord(Base):
    __tablename__ = "exports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    render_id: Mapped[str] = mapped_column(ForeignKey("rendered_clips.id", ondelete="CASCADE"), index=True)
    dest_path: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    post_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    render: Mapped[RenderedClip] = relationship(back_populates="exports")


# --------------------------------------------------------------------------- presets / profiles
class EditingProfileRow(TimestampMixin, Base):
    __tablename__ = "editing_profiles"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class CaptionPresetRow(TimestampMixin, Base):
    __tablename__ = "caption_presets"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    style: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


# --------------------------------------------------------------------------- jobs
ACTIVE_JOB_STATES = ("queued", "running")
JOB_STATES = ("queued", "running", "completed", "failed", "cancelled")


class Job(TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        # No two *active* jobs may share a dedupe key — enforced by SQLite itself.
        Index(
            "uq_jobs_active_dedupe",
            "dedupe_key",
            unique=True,
            sqlite_where=text("status IN ('queued','running')"),
        ),
        Index("ix_jobs_claim", "lane", "status", "run_after"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    lane: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    dedupe_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    claim_token: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    worker_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    run_after: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Loose references for filtering in the UI (not FKs: jobs outlive deleted rows).
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    media_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    candidate_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    events: Mapped[list[JobEvent]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="JobEvent.id"
    )


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    level: Mapped[str] = mapped_column(String(10), default="info", nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    job: Mapped[Job] = relationship(back_populates="events")
