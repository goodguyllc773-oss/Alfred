"""Use-cases shared by the API, CLI and job handlers."""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .captions.style import CaptionStyle
from .context import AppContext
from .db.models import (
    CandidateClip, CaptionPresetRow, EditingProfileRow, ExportRecord, Project, RenderedClip,
    SourceMedia, Transcript, utcnow,
)
from .errors import Conflict, InvalidPath, NotFound, SourceMissing, ValidationFailed
from .jobs import queue
from .media.paths import (
    ensure_output_dir, fingerprint, format_ts, from_relative, normalize_user_path, safe_filename,
    slugify, unique_path,
)
from .media.probe import probe
from .profiles.schema import EditingProfile, Layout
from .transcription.base import Word

REVIEW_TRANSITIONS = {
    "approve": {"pending": "approved", "rejected": "approved", "approved": "approved"},
    "reject": {"pending": "rejected", "approved": "rejected", "rejected": "rejected"},
    "reset": {"approved": "pending", "rejected": "pending", "pending": "pending"},
}
MIN_CLIP_S, MAX_CLIP_S = 1.0, 600.0


def get_or_404(s: Session, model, id_: str, what: str):
    row = s.get(model, id_)
    if row is None:
        raise NotFound(f"{what} not found.")
    return row


# --------------------------------------------------------------------------- projects
def create_project(ctx: AppContext, name: str, description: str = "", default_profile_key: str = "conversation",
                   export_dir: str | None = None) -> Project:
    name = (name or "").strip()
    if not name:
        raise ValidationFailed("Project name is required.")
    ctx.profile(default_profile_key)
    if export_dir:
        export_dir = str(normalize_user_path(export_dir))
    with ctx.session() as s:
        p = Project(name=name[:200], slug=slugify(name), description=description or "",
                    default_profile_key=default_profile_key, export_dir=export_dir)
        s.add(p)
        s.flush()
        return p


# --------------------------------------------------------------------------- media
def import_media(ctx: AppContext, project_id: str, raw_path: str, *, profile_key: str | None = None,
                 auto_process: bool = True, transcript_path: str | None = None) -> tuple[SourceMedia, list[str]]:
    path = normalize_user_path(raw_path)
    info = probe(path, ffprobe=ctx.settings.ffprobe)  # raises InvalidMedia with a hint
    tpath = None
    if transcript_path:
        tp = normalize_user_path(transcript_path)
        if not tp.is_file() or tp.suffix.lower() not in (".srt", ".vtt", ".json"):
            raise InvalidPath("Transcript must be an existing .srt, .vtt or .json file.")
        tpath = str(tp)
    fp = fingerprint(path)
    with ctx.session() as s:
        project = get_or_404(s, Project, project_id, "Project")
        key = profile_key or project.default_profile_key
        ctx.profile(key, s)
        dup = s.scalar(select(SourceMedia).where(SourceMedia.project_id == project_id,
                                                 SourceMedia.fingerprint == fp))
        if dup:
            raise Conflict(f"This video is already in the project (as “{dup.file_name}”).",
                           hint="Open the existing item instead, or relink it if it moved.")
        m = SourceMedia(
            project_id=project_id, original_path=str(path), file_name=path.name,
            file_size=path.stat().st_size, fingerprint=fp, status="ready", container=info.container,
            duration_s=info.duration_s, width=info.display_width, height=info.display_height,
            fps=info.fps, video_codec=info.video_codec, rotation=info.rotation, has_audio=info.has_audio,
            audio_codec=info.audio_codec, audio_channels=info.audio_channels, sample_rate=info.sample_rate,
            probe=info.summary(), profile_key=key, transcript_path=tpath,
        )
        s.add(m)
        s.flush()
        media_id = m.id
    jobs = []
    if auto_process:
        jobs.append(start_processing(ctx, media_id).id)
    with ctx.session() as s:
        return s.get(SourceMedia, media_id), jobs


def start_processing(ctx: AppContext, media_id: str, *, force_transcribe: bool = False):
    with ctx.session() as s:
        m = get_or_404(s, SourceMedia, media_id, "Media")
        project_id, has_audio = m.project_id, m.has_audio
    if has_audio:
        job, _ = queue.enqueue(ctx.sf, kind="transcribe", lane="transcribe",
                               payload={"media_id": media_id, "auto_analyze": True, "force": force_transcribe},
                               dedupe_key=f"transcribe:{media_id}", project_id=project_id, media_id=media_id)
    else:
        job = request_analysis(ctx, media_id)
    return job


def request_analysis(ctx: AppContext, media_id: str, profile_key: str | None = None):
    with ctx.session() as s:
        m = get_or_404(s, SourceMedia, media_id, "Media")
        if profile_key:
            ctx.profile(profile_key, s)
            m.profile_key = profile_key
        project_id = m.project_id
    job, _ = queue.enqueue(ctx.sf, kind="analyze", lane="media",
                           payload={"media_id": media_id, "profile_key": profile_key},
                           dedupe_key=f"analyze:{media_id}", project_id=project_id, media_id=media_id)
    return job


def check_source(ctx: AppContext, media_id: str) -> Path:
    """Return the source path if the file is where we last saw it (same size);
    otherwise mark the media ``missing`` and raise ``SourceMissing``."""
    with ctx.session() as s:
        m = get_or_404(s, SourceMedia, media_id, "Media")
        p = Path(m.original_path)
        ok = p.is_file() and p.stat().st_size == m.file_size
        new_status = "ready" if ok else "missing"
        if m.status != new_status and m.status != "invalid":
            m.status = new_status
        name = m.file_name
    if not ok:
        raise SourceMissing(
            f"The source video “{name}” is no longer at {p}.",
            hint="Reconnect the drive, or use “Relink” on the media to point ClipForge at its new location. "
                 "ClipForge never moves or deletes your footage.")
    return p


def relink_media(ctx: AppContext, media_id: str, raw_path: str, *, force: bool = False) -> SourceMedia:
    path = normalize_user_path(raw_path)
    if not path.is_file():
        raise InvalidPath(f"File not found: {path}")
    fp = fingerprint(path)
    with ctx.session() as s:
        m = get_or_404(s, SourceMedia, media_id, "Media")
        if fp != m.fingerprint and not force:
            raise Conflict(
                "That file doesn't look like the same video (size/content fingerprint differs).",
                hint="If it's a re-export of the same footage, relink with “force”, then re-run analysis — "
                     "timestamps may no longer line up.")
        if fp != m.fingerprint:
            info = probe(path, ffprobe=ctx.settings.ffprobe)
            m.fingerprint, m.file_size, m.duration_s = fp, path.stat().st_size, info.duration_s
            m.probe = info.summary()
        m.original_path, m.file_name, m.status = str(path), path.name, "ready"
        s.flush()
        return m


def latest_transcript(s: Session, media_id: str, complete_only: bool = True) -> Transcript | None:
    q = select(Transcript).where(Transcript.media_id == media_id)
    if complete_only:
        q = q.where(Transcript.status == "complete")
    return s.scalar(q.order_by(Transcript.created_at.desc()).limit(1))


def transcript_words(t: Transcript | None, start: float | None = None, end: float | None = None) -> list[Word]:
    if t is None:
        return []
    out = []
    for seg in t.segments:
        if end is not None and seg.start_s > end:
            continue
        if start is not None and seg.end_s < start:
            continue
        out.extend(Word.from_json(w) for w in seg.words)
    out.sort(key=lambda w: w.start)
    return out


# --------------------------------------------------------------------------- candidates
def _text_in_range(words: list[Word], start: float, end: float) -> str:
    return " ".join(w.text for w in words if start <= (w.start + w.end) / 2 <= end)


def update_candidate(ctx: AppContext, candidate_id: str, patch: dict[str, Any]) -> CandidateClip:
    allowed = {"start_s", "end_s", "snap", "profile_key", "caption_preset_key", "layout_overrides",
               "caption_overrides", "post_title", "post_hashtags"}
    unknown = set(patch) - allowed
    if unknown:
        raise ValidationFailed(f"Unknown fields: {sorted(unknown)}")
    with ctx.session() as s:
        c = get_or_404(s, CandidateClip, candidate_id, "Clip")
        media = c.media
        start = float(patch.get("start_s", c.start_s))
        end = float(patch.get("end_s", c.end_s))
        words = transcript_words(latest_transcript(s, media.id))
        if patch.get("snap") and words:
            # Snap to the nearest word edges so the clip never starts/ends mid-word.
            start = max((w.start for w in words if w.start <= start + 0.3), default=start)
            start = max(0.0, start - 0.15)
            end = min((w.end for w in words if w.end >= end - 0.3), default=end)
            end = min(media.duration_s, end + 0.3)
        if not (0 <= start < end <= media.duration_s + 0.01):
            raise ValidationFailed(f"Clip must satisfy 0 ≤ start < end ≤ {media.duration_s:.2f}s.")
        if not (MIN_CLIP_S <= end - start <= MAX_CLIP_S):
            raise ValidationFailed(f"Clip length must be between {MIN_CLIP_S:.0f}s and {MAX_CLIP_S:.0f}s.")
        c.start_s, c.end_s = round(start, 3), round(min(end, media.duration_s), 3)
        if "start_s" in patch or "end_s" in patch:
            c.transcript_text = _text_in_range(words, c.start_s, c.end_s)
        if "profile_key" in patch:
            prof = ctx.profile(patch["profile_key"], s)
            c.profile_key = prof.key
        if "caption_preset_key" in patch:
            ctx.caption_style(patch["caption_preset_key"], s)
            c.caption_preset_key = patch["caption_preset_key"]
        if "layout_overrides" in patch:
            lo = patch["layout_overrides"] or {}
            base = ctx.profile(c.profile_key, s).layout.model_dump()
            try:
                Layout.model_validate({**base, **lo})
            except Exception as e:  # pydantic ValidationError → 422 with detail
                raise ValidationFailed(f"Invalid layout: {e}")
            c.layout_overrides = lo
        if "caption_overrides" in patch:
            co = patch["caption_overrides"] or {}
            base = ctx.caption_style(c.caption_preset_key, s).model_dump()
            try:
                CaptionStyle.model_validate({**base, **co})
            except Exception as e:
                raise ValidationFailed(f"Invalid caption style: {e}")
            c.caption_overrides = co
        if "post_title" in patch:
            c.post_title = str(patch["post_title"])[:300]
        if "post_hashtags" in patch:
            tags = [str(t).strip() for t in patch["post_hashtags"] or [] if str(t).strip()]
            c.post_hashtags = [t if t.startswith("#") else f"#{t}" for t in tags][:30]
        s.flush()
        return c


def review_candidate(ctx: AppContext, candidate_id: str, action: str, *, auto_render: bool = True) -> tuple[CandidateClip, str | None]:
    if action not in REVIEW_TRANSITIONS:
        raise ValidationFailed(f"Unknown review action '{action}'. Use approve, reject or reset.")
    job_id = None
    with ctx.session() as s:
        c = get_or_404(s, CandidateClip, candidate_id, "Clip")
        c.review_status = REVIEW_TRANSITIONS[action][c.review_status]
        c.reviewed_at = utcnow() if c.review_status != "pending" else None
        has_render = any(r.status == "completed" for r in c.renders)
    if action == "approve" and auto_render and not has_render:
        job_id = request_render(ctx, candidate_id).id
    with ctx.session() as s:
        return s.get(CandidateClip, candidate_id), job_id


def request_render(ctx: AppContext, candidate_id: str):
    with ctx.session() as s:
        c = get_or_404(s, CandidateClip, candidate_id, "Clip")
        media_id, project_id = c.media_id, c.media.project_id
    job, _ = queue.enqueue(ctx.sf, kind="render", lane="media", payload={"candidate_id": candidate_id},
                           dedupe_key=f"render:{candidate_id}", project_id=project_id, media_id=media_id,
                           candidate_id=candidate_id)
    return job


def effective_profile(ctx: AppContext, s: Session, c: CandidateClip) -> EditingProfile:
    return ctx.profile(c.profile_key, s).with_layout_overrides(c.layout_overrides or {})


def effective_caption_style(ctx: AppContext, s: Session, c: CandidateClip) -> CaptionStyle:
    base = ctx.caption_style(c.caption_preset_key, s).model_dump()
    return CaptionStyle.model_validate({**base, **(c.caption_overrides or {})})


def render_output_dir(ctx: AppContext, project: Project) -> Path:
    return ctx.settings.renders_dir / f"{project.slug}-{project.id[:8]}"


def render_file(ctx: AppContext, r: RenderedClip) -> Path:
    if not r.rel_path:
        raise NotFound("This render has no output file.")
    return from_relative(r.rel_path, ctx.settings.data_dir)


# --------------------------------------------------------------------------- export
def post_metadata(c: CandidateClip, media: SourceMedia, project: Project) -> dict:
    caption = c.post_title.strip()
    tags = " ".join(c.post_hashtags or [])
    return {
        "title": c.post_title,
        "caption": f"{caption}\n\n{tags}".strip(),
        "hashtags": c.post_hashtags,
        "source": {"project": project.name, "file": media.file_name,
                   "start": format_ts(c.start_s, ":"), "end": format_ts(c.end_s, ":"),
                   "start_s": c.start_s, "end_s": c.end_s},
        "transcript": c.transcript_text,
        "selection_reasons": [r.get("text") for r in c.reasons or []],
        "note": "Make sure you have the rights to post this footage before publishing.",
    }


def export_render(ctx: AppContext, render_id: str, folder: str | None = None) -> ExportRecord:
    with ctx.session() as s:
        r = get_or_404(s, RenderedClip, render_id, "Render")
        if r.status != "completed":
            raise Conflict("Only completed renders can be exported.")
        c = r.candidate
        media = c.media
        project = media.project
        src = render_file(ctx, r)
        if not src.is_file():
            raise NotFound("The rendered file is missing from the ClipForge data folder — render it again.")
        target_dir = ensure_output_dir(folder or project.export_dir or ctx.prefs()["default_export_dir"])
        title_slug = slugify(c.post_title or c.transcript_text[:60], max_len=40, fallback="clip")
        name = safe_filename(f"{project.slug}_{title_slug}_{format_ts(c.start_s)}.mp4")
        dest = unique_path(target_dir, name)
        part = dest.with_name(dest.name + ".part")
        shutil.copyfile(src, part)
        os.replace(part, dest)
        meta = post_metadata(c, media, project)
        meta["exported_at"] = datetime.now().isoformat(timespec="seconds")
        meta["file"] = dest.name
        meta_path = dest.with_suffix(".json")
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        dest.with_suffix(".txt").write_text(meta["caption"] + "\n", encoding="utf-8")
        rec = ExportRecord(render_id=r.id, dest_path=str(dest), metadata_path=str(meta_path), post_metadata=meta)
        s.add(rec)
        s.flush()
        return rec


# --------------------------------------------------------------------------- presets
def add_caption_preset(ctx: AppContext, key: str, name: str, style: dict) -> CaptionPresetRow:
    key = slugify(key, max_len=60)
    st = CaptionStyle.model_validate(style)
    with ctx.session() as s:
        row = s.get(CaptionPresetRow, key)
        if row is not None and row.builtin:
            raise Conflict("Built-in presets can't be overwritten; choose another key.")
        if row is None:
            row = CaptionPresetRow(key=key, name=name or key, builtin=False, style=st.model_dump(mode="json"))
            s.add(row)
        else:
            row.name, row.style = name or row.name, st.model_dump(mode="json")
        s.flush()
        return row


def add_profile(ctx: AppContext, base_key: str, key: str, name: str, changes: dict) -> EditingProfileRow:
    base = ctx.profile(base_key).model_dump()
    merged = {**base, **changes, "key": slugify(key, max_len=60), "name": name or key}
    prof = EditingProfile.model_validate(merged)
    with ctx.session() as s:
        row = s.get(EditingProfileRow, prof.key)
        if row is not None and row.builtin:
            raise Conflict("Built-in profiles can't be overwritten; choose another key.")
        if row is None:
            row = EditingProfileRow(key=prof.key, name=prof.name, builtin=False, config=prof.model_dump(mode="json"))
            s.add(row)
        else:
            row.name, row.config = prof.name, prof.model_dump(mode="json")
        s.flush()
        return row


def media_counts(s: Session, media_id: str) -> dict:
    rows = s.execute(select(CandidateClip.review_status, func.count()).where(CandidateClip.media_id == media_id)
                     .group_by(CandidateClip.review_status)).all()
    return {k: v for k, v in rows}
