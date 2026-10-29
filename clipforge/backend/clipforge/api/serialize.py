"""Row → JSON-ready dict. Kept explicit so the API contract is easy to read."""

from __future__ import annotations

from datetime import datetime

from ..db.models import CandidateClip, ExportRecord, Job, Project, RenderedClip, SourceMedia, Transcript
from ..media.probe import BROWSER_PLAYABLE


def ts(d: datetime | None) -> str | None:
    return d.isoformat(timespec="seconds") + "Z" if d else None


def project_out(p: Project, media_count: int | None = None) -> dict:
    return {"id": p.id, "name": p.name, "slug": p.slug, "description": p.description,
            "default_profile_key": p.default_profile_key, "export_dir": p.export_dir,
            "media_count": media_count, "created_at": ts(p.created_at), "updated_at": ts(p.updated_at)}


def media_out(m: SourceMedia, *, counts: dict | None = None, transcript: Transcript | None = None,
              active_jobs: list[Job] | None = None) -> dict:
    ext = "." + m.file_name.rsplit(".", 1)[-1].lower() if "." in m.file_name else ""
    return {
        "id": m.id, "project_id": m.project_id, "file_name": m.file_name, "path": m.original_path,
        "status": m.status, "file_size": m.file_size, "duration_s": m.duration_s, "width": m.width,
        "height": m.height, "fps": m.fps, "video_codec": m.video_codec, "container": m.container,
        "has_audio": m.has_audio, "audio_codec": m.audio_codec, "audio_channels": m.audio_channels,
        "sample_rate": m.sample_rate, "rotation": m.rotation, "profile_key": m.profile_key,
        "transcript_path": m.transcript_path, "browser_playable": ext in BROWSER_PLAYABLE,
        "candidate_counts": counts or {},
        "transcript": None if transcript is None else {
            "id": transcript.id, "engine": transcript.engine, "model": transcript.model,
            "device": transcript.device, "language": transcript.language, "status": transcript.status,
            "chunks_done": transcript.chunks_done, "chunks_total": transcript.chunks_total,
            "word_timestamps": transcript.word_timestamps},
        "active_jobs": [job_out(j) for j in active_jobs or []],
        "created_at": ts(m.created_at),
    }


def render_out(r: RenderedClip) -> dict:
    return {"id": r.id, "candidate_id": r.candidate_id, "version": r.version, "status": r.status,
            "file": r.rel_path.rsplit("/", 1)[-1] if r.rel_path else None, "start_s": r.start_s,
            "end_s": r.end_s, "profile_key": r.profile_key, "caption_preset_key": r.caption_preset_key,
            "encoder": r.encoder, "width": r.width, "height": r.height, "duration_s": r.duration_s,
            "file_size": r.file_size, "error": r.error, "validation": r.validation, "settings": r.settings,
            "job_id": r.job_id, "exports": [export_out(e) for e in r.exports],
            "created_at": ts(r.created_at)}


def export_out(e: ExportRecord) -> dict:
    return {"id": e.id, "dest_path": e.dest_path, "metadata_path": e.metadata_path,
            "post_metadata": e.post_metadata, "created_at": ts(e.created_at)}


def candidate_out(c: CandidateClip, *, with_renders: bool = True) -> dict:
    d = {
        "id": c.id, "media_id": c.media_id, "rank": c.rank, "start_s": c.start_s, "end_s": c.end_s,
        "duration_s": round(c.end_s - c.start_s, 3), "original_start_s": c.original_start_s,
        "original_end_s": c.original_end_s, "transcript_text": c.transcript_text, "score": c.score,
        "score_breakdown": c.score_breakdown, "reasons": c.reasons, "warnings": c.warnings,
        "profile_key": c.profile_key, "caption_preset_key": c.caption_preset_key,
        "layout_overrides": c.layout_overrides, "caption_overrides": c.caption_overrides,
        "review_status": c.review_status, "reviewed_at": ts(c.reviewed_at), "post_title": c.post_title,
        "post_hashtags": c.post_hashtags, "analysis_run_id": c.analysis_run_id,
        "created_at": ts(c.created_at), "updated_at": ts(c.updated_at),
    }
    if with_renders:
        d["renders"] = [render_out(r) for r in c.renders]
        done = [r for r in c.renders if r.status == "completed"]
        d["latest_render_id"] = done[-1].id if done else None
    return d


def job_out(j: Job, *, events: bool = False) -> dict:
    d = {"id": j.id, "kind": j.kind, "lane": j.lane, "status": j.status, "stage": j.stage,
         "progress": round(j.progress, 4), "attempts": j.attempts, "max_attempts": j.max_attempts,
         "error_code": j.error_code, "error": j.error, "result": j.result, "payload": j.payload,
         "cancel_requested": j.cancel_requested, "project_id": j.project_id, "media_id": j.media_id,
         "candidate_id": j.candidate_id, "created_at": ts(j.created_at), "started_at": ts(j.started_at),
         "finished_at": ts(j.finished_at), "updated_at": ts(j.updated_at), "run_after": ts(j.run_after)}
    if events:
        d["events"] = [{"ts": ts(e.ts), "level": e.level, "message": e.message, "data": e.data}
                       for e in j.events]
    return d
