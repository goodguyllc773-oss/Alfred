"""FastAPI application (local only: binds 127.0.0.1, rejects foreign Host headers)."""

from __future__ import annotations

import logging
import os
import shutil
import string
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .. import __version__, services
from ..config import Settings, load_settings
from ..context import AppContext
from ..db.models import (
    CandidateClip, CaptionPresetRow, EditingProfileRow, Job, Project, RenderedClip, SourceMedia,
)
from ..errors import ClipForgeError, InvalidPath, NotFound, ValidationFailed
from ..jobs import queue
from ..jobs.worker import Worker
from ..media import probe as probe_mod
from ..media.ffmpeg import _CREATE_FLAGS, ffmpeg_version, resolve_binary
from ..media.paths import normalize_user_path
from .serialize import candidate_out, job_out, media_out, project_out, render_out

log = logging.getLogger("clipforge.api")

DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
TRANSCRIPT_EXTS = {".srt", ".vtt", ".json"}


# --------------------------------------------------------------------------- request bodies
class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    default_profile_key: str = "conversation"
    export_dir: str | None = None


class ProjectPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    default_profile_key: str | None = None
    export_dir: str | None = None


class ImportIn(BaseModel):
    path: str
    profile_key: str | None = None
    auto_process: bool = True
    transcript_path: str | None = None


class RelinkIn(BaseModel):
    path: str
    force: bool = False


class AnalyzeIn(BaseModel):
    profile_key: str | None = None


class ReviewIn(BaseModel):
    action: str
    auto_render: bool = True


class ExportIn(BaseModel):
    folder: str | None = None


class PresetIn(BaseModel):
    key: str
    name: str = ""
    style: dict[str, Any]


class ProfileIn(BaseModel):
    base_key: str
    key: str
    name: str = ""
    changes: dict[str, Any] = {}


def create_app(ctx: AppContext | None = None, *, settings: Settings | None = None,
               start_worker: bool | None = None) -> FastAPI:
    holder: dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        c = ctx or AppContext(settings or load_settings())
        holder["ctx"] = c
        app.state.ctx = c
        run_worker = c.settings.embedded_worker if start_worker is None else start_worker
        worker = Worker(c) if run_worker else None
        if worker:
            app.state.worker_started = worker.start()
        app.state.worker = worker
        try:
            yield
        finally:
            if worker:
                worker.stop()
            if ctx is None:
                c.close()

    app = FastAPI(title="ClipForge", version=__version__, lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    app.add_middleware(CORSMiddleware, allow_origins=DEV_ORIGINS, allow_methods=["*"], allow_headers=["*"])

    def C() -> AppContext:
        return app.state.ctx

    @app.exception_handler(ClipForgeError)
    async def _cf_error(_req: Request, exc: ClipForgeError):
        return JSONResponse(status_code=exc.http_status, content={"error": exc.to_dict()})

    # ------------------------------------------------------------------ system
    @app.get("/api/health")
    def health():
        w = app.state.worker
        return {"ok": True, "version": __version__,
                "worker": "running" if (w and getattr(app.state, "worker_started", False))
                else "external-or-none"}

    @app.get("/api/system/diagnostics")
    def diagnostics(refresh: bool = False):
        c = C()
        info: dict[str, Any] = {"version": __version__, "python": sys.version.split()[0],
                                "platform": sys.platform, "data_dir": str(c.settings.data_dir)}
        ffm = {}
        for name, binary in (("ffmpeg", c.settings.ffmpeg), ("ffprobe", c.settings.ffprobe)):
            try:
                ffm[name] = {"path": resolve_binary(binary), "ok": True}
            except ClipForgeError as e:
                ffm[name] = {"ok": False, **e.to_dict()}
        ffm["version"] = ffmpeg_version(c.settings.ffmpeg)
        info["ffmpeg"] = ffm
        try:
            info["hardware"] = c.hardware(refresh=refresh).to_dict()
        except ClipForgeError as e:
            info["hardware"] = {"error": e.to_dict()}
        deps = {}
        for mod in ("faster_whisper", "ctranslate2", "cv2", "numpy"):
            try:
                m = __import__(mod)
                deps[mod] = getattr(m, "__version__", "installed")
            except Exception as e:  # noqa: BLE001
                deps[mod] = f"missing: {e}"
        info["python_packages"] = deps
        prefs = c.prefs()
        models_dir = c.settings.models_dir
        info["transcription"] = {
            "engine": prefs["transcription_engine"] or c.settings.transcriber,
            "model": prefs["whisper_model"], "device": prefs["whisper_device"],
            "models_dir": str(models_dir),
            "downloaded_models": sorted(p.name for p in models_dir.iterdir() if p.is_dir()) if models_dir.is_dir() else [],
        }
        du = shutil.disk_usage(c.settings.data_dir)
        info["disk"] = {"free_gb": round(du.free / 1e9, 1), "total_gb": round(du.total / 1e9, 1)}
        return info

    @app.get("/api/settings")
    def get_settings():
        return C().prefs()

    @app.put("/api/settings")
    def put_settings(patch: dict[str, Any] = Body(...)):
        return C().update_prefs(patch)

    @app.get("/api/fs/list")
    def fs_list(path: str | None = None):
        """Folder browser for the import dialog (localhost only)."""
        if not path:
            roots = []
            if sys.platform == "win32":
                drives = getattr(os, "listdrives", None)
                letters = drives() if drives else [f"{d}:\\" for d in string.ascii_uppercase
                                                   if os.path.exists(f"{d}:\\")]
                roots += [{"name": d, "path": d, "kind": "dir"} for d in letters]
            home = Path.home()
            for p in (home, home / "Videos", home / "Downloads", home / "Desktop"):
                if p.is_dir():
                    roots.append({"name": str(p), "path": str(p), "kind": "dir"})
            if sys.platform != "win32":
                roots.append({"name": "/", "path": "/", "kind": "dir"})
            return {"path": None, "parent": None, "entries": roots}
        p = normalize_user_path(path)
        if not p.is_dir():
            raise InvalidPath(f"Not a folder: {p}")
        entries = []
        try:
            for e in sorted(os.scandir(p), key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower())):
                if e.name.startswith((".", "$")):
                    continue
                try:
                    if e.is_dir():
                        entries.append({"name": e.name, "path": e.path, "kind": "dir"})
                    elif e.is_file():
                        ext = Path(e.name).suffix.lower()
                        if ext in probe_mod.SUPPORTED_EXTENSIONS:
                            entries.append({"name": e.name, "path": e.path, "kind": "video",
                                            "size": e.stat().st_size})
                        elif ext in TRANSCRIPT_EXTS:
                            entries.append({"name": e.name, "path": e.path, "kind": "transcript",
                                            "size": e.stat().st_size})
                except OSError:
                    continue
        except PermissionError:
            raise InvalidPath(f"No permission to read {p}")
        parent = str(p.parent) if p.parent != p else None
        return {"path": str(p), "parent": parent, "entries": entries[:2000]}

    # ------------------------------------------------------------------ profiles / presets
    @app.get("/api/profiles")
    def profiles():
        with C().session() as s:
            return [{"key": r.key, "name": r.name, "builtin": r.builtin, "config": r.config}
                    for r in s.scalars(select(EditingProfileRow).order_by(EditingProfileRow.builtin.desc(),
                                                                           EditingProfileRow.name)).all()]

    @app.post("/api/profiles", status_code=201)
    def add_profile(body: ProfileIn):
        try:
            r = services.add_profile(C(), body.base_key, body.key, body.name, body.changes)
        except ValueError as e:
            raise ValidationFailed(str(e))
        return {"key": r.key, "name": r.name, "builtin": r.builtin, "config": r.config}

    @app.get("/api/caption-presets")
    def caption_presets():
        with C().session() as s:
            return [{"key": r.key, "name": r.name, "builtin": r.builtin, "style": r.style}
                    for r in s.scalars(select(CaptionPresetRow).order_by(CaptionPresetRow.builtin.desc(),
                                                                          CaptionPresetRow.name)).all()]

    @app.post("/api/caption-presets", status_code=201)
    def add_caption_preset(body: PresetIn):
        try:
            r = services.add_caption_preset(C(), body.key, body.name, body.style)
        except ValueError as e:
            raise ValidationFailed(str(e))
        return {"key": r.key, "name": r.name, "builtin": r.builtin, "style": r.style}

    # ------------------------------------------------------------------ projects
    @app.get("/api/projects")
    def list_projects():
        with C().session() as s:
            counts = dict(s.execute(select(SourceMedia.project_id, func.count()).group_by(SourceMedia.project_id)).all())
            return [project_out(p, counts.get(p.id, 0))
                    for p in s.scalars(select(Project).order_by(Project.updated_at.desc())).all()]

    @app.post("/api/projects", status_code=201)
    def create_project(body: ProjectIn):
        p = services.create_project(C(), body.name, body.description, body.default_profile_key, body.export_dir)
        return project_out(p, 0)

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str):
        with C().session() as s:
            p = services.get_or_404(s, Project, project_id, "Project")
            items = []
            for m in p.media:
                active = s.scalars(select(Job).where(Job.media_id == m.id, Job.status.in_(("queued", "running")))
                                   .order_by(Job.created_at)).all()
                items.append(media_out(m, counts=services.media_counts(s, m.id),
                                       transcript=services.latest_transcript(s, m.id, complete_only=False),
                                       active_jobs=active))
            return {**project_out(p, len(items)), "media": items}

    @app.patch("/api/projects/{project_id}")
    def patch_project(project_id: str, body: ProjectPatch):
        c = C()
        with c.session() as s:
            p = services.get_or_404(s, Project, project_id, "Project")
            if body.name is not None:
                p.name = body.name.strip()
            if body.description is not None:
                p.description = body.description
            if body.default_profile_key is not None:
                c.profile(body.default_profile_key, s)
                p.default_profile_key = body.default_profile_key
            if body.export_dir is not None:
                p.export_dir = str(normalize_user_path(body.export_dir)) if body.export_dir else None
            s.flush()
            return project_out(p)

    # ------------------------------------------------------------------ media
    @app.post("/api/projects/{project_id}/media", status_code=201)
    def import_media(project_id: str, body: ImportIn):
        m, jobs = services.import_media(C(), project_id, body.path, profile_key=body.profile_key,
                                        auto_process=body.auto_process, transcript_path=body.transcript_path)
        return {**media_out(m), "job_ids": jobs}

    @app.get("/api/media/{media_id}")
    def get_media(media_id: str):
        c = C()
        try:
            services.check_source(c, media_id)
        except ClipForgeError:
            pass  # status is updated to "missing"; still return the row
        with c.session() as s:
            m = services.get_or_404(s, SourceMedia, media_id, "Media")
            active = s.scalars(select(Job).where(Job.media_id == m.id, Job.status.in_(("queued", "running")))).all()
            return media_out(m, counts=services.media_counts(s, m.id),
                             transcript=services.latest_transcript(s, m.id, complete_only=False), active_jobs=active)

    @app.get("/api/media/{media_id}/transcript")
    def get_transcript(media_id: str):
        with C().session() as s:
            services.get_or_404(s, SourceMedia, media_id, "Media")
            t = services.latest_transcript(s, media_id, complete_only=False)
            if t is None:
                raise NotFound("No transcript yet.")
            return {"id": t.id, "status": t.status, "engine": t.engine, "model": t.model, "language": t.language,
                    "segments": [{"start_s": x.start_s, "end_s": x.end_s, "text": x.text, "words": x.words}
                                 for x in t.segments]}

    @app.get("/api/media/{media_id}/candidates")
    def list_candidates(media_id: str, status: str | None = None):
        with C().session() as s:
            services.get_or_404(s, SourceMedia, media_id, "Media")
            q = select(CandidateClip).where(CandidateClip.media_id == media_id)
            if status:
                q = q.where(CandidateClip.review_status == status)
            rows = s.scalars(q.order_by(CandidateClip.score.desc())).all()
            return [candidate_out(c) for c in rows]

    @app.post("/api/media/{media_id}/transcribe", status_code=202)
    def transcribe(media_id: str, force: bool = False):
        return job_out(services.start_processing(C(), media_id, force_transcribe=force))

    @app.post("/api/media/{media_id}/analyze", status_code=202)
    def analyze(media_id: str, body: AnalyzeIn = Body(default_factory=AnalyzeIn)):
        return job_out(services.request_analysis(C(), media_id, body.profile_key))

    @app.post("/api/media/{media_id}/relink")
    def relink(media_id: str, body: RelinkIn):
        return media_out(services.relink_media(C(), media_id, body.path, force=body.force))

    @app.get("/api/media/{media_id}/source")
    def media_source(media_id: str):
        c = C()
        p = services.check_source(c, media_id)
        if p.suffix.lower() not in probe_mod.BROWSER_PLAYABLE:
            raise ClipForgeError("This format can't be previewed in the browser.", code="NOT_PLAYABLE",
                                 http_status=415, hint="Render the clip to preview it.")
        return FileResponse(p, media_type="video/webm" if p.suffix.lower() == ".webm" else "video/mp4")

    @app.get("/api/media/{media_id}/frame")
    def media_frame(media_id: str, t: float = Query(0, ge=0), w: int = Query(480, ge=64, le=1920)):
        c = C()
        p = services.check_source(c, media_id)
        exe = resolve_binary(c.settings.ffmpeg)
        out = subprocess.run([exe, "-hide_banner", "-nostdin", "-v", "error", "-ss", f"{t:.3f}", "-i", str(p),
                              "-frames:v", "1", "-vf", f"scale={w}:-2", "-f", "image2", "-c:v", "mjpeg",
                              "-q:v", "4", "-"], capture_output=True, timeout=30, creationflags=_CREATE_FLAGS)
        if out.returncode != 0 or not out.stdout:
            raise ClipForgeError("Couldn't extract a frame at that time.", code="FRAME_FAILED")
        return Response(out.stdout, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})

    # ------------------------------------------------------------------ candidates / renders
    @app.get("/api/candidates/{candidate_id}")
    def get_candidate(candidate_id: str):
        with C().session() as s:
            return candidate_out(services.get_or_404(s, CandidateClip, candidate_id, "Clip"))

    @app.patch("/api/candidates/{candidate_id}")
    def patch_candidate(candidate_id: str, patch: dict[str, Any] = Body(...)):
        c = C()
        services.update_candidate(c, candidate_id, patch)
        with c.session() as s:
            return candidate_out(s.get(CandidateClip, candidate_id))

    @app.post("/api/candidates/{candidate_id}/review")
    def review(candidate_id: str, body: ReviewIn):
        c = C()
        _, job_id = services.review_candidate(c, candidate_id, body.action, auto_render=body.auto_render)
        with c.session() as s:
            return {**candidate_out(s.get(CandidateClip, candidate_id)), "render_job_id": job_id}

    @app.post("/api/candidates/{candidate_id}/render", status_code=202)
    def render(candidate_id: str):
        return job_out(services.request_render(C(), candidate_id))

    @app.get("/api/candidates/{candidate_id}/renders")
    def candidate_renders(candidate_id: str):
        with C().session() as s:
            c = services.get_or_404(s, CandidateClip, candidate_id, "Clip")
            return [render_out(r) for r in c.renders]

    @app.get("/api/renders/{render_id}")
    def get_render(render_id: str):
        with C().session() as s:
            return render_out(services.get_or_404(s, RenderedClip, render_id, "Render"))

    @app.get("/api/renders/{render_id}/video")
    def render_video(render_id: str):
        c = C()
        with c.session() as s:
            r = services.get_or_404(s, RenderedClip, render_id, "Render")
            if r.status != "completed":
                raise NotFound("Render isn't finished.")
            p = services.render_file(c, r)
        if not p.is_file():
            raise NotFound("Rendered file is missing — render again.")
        return FileResponse(p, media_type="video/mp4")

    @app.post("/api/renders/{render_id}/export")
    def export(render_id: str, body: ExportIn = Body(default_factory=ExportIn)):
        rec = services.export_render(C(), render_id, body.folder)
        return {"id": rec.id, "dest_path": rec.dest_path, "metadata_path": rec.metadata_path,
                "post_metadata": rec.post_metadata}

    # ------------------------------------------------------------------ jobs
    @app.get("/api/jobs")
    def list_jobs(status: str | None = None, media_id: str | None = None, project_id: str | None = None,
                  limit: int = Query(100, ge=1, le=500)):
        with C().session() as s:
            q = select(Job)
            if status:
                q = q.where(Job.status.in_(status.split(",")))
            if media_id:
                q = q.where(Job.media_id == media_id)
            if project_id:
                q = q.where(Job.project_id == project_id)
            return [job_out(j) for j in s.scalars(q.order_by(Job.created_at.desc()).limit(limit)).all()]

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        with C().session() as s:
            return job_out(services.get_or_404(s, Job, job_id, "Job"), events=True)

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        c = C()
        queue.request_cancel(c.sf, job_id)
        with c.session() as s:
            return job_out(s.get(Job, job_id))

    @app.post("/api/jobs/{job_id}/retry")
    def retry_job(job_id: str):
        c = C()
        job, requeued = queue.retry(c.sf, job_id)
        with c.session() as s:
            return {**job_out(s.get(Job, job.id)), "requeued": requeued}

    # ------------------------------------------------------------------ frontend
    dist = (settings or (ctx.settings if ctx else None) or load_settings()).frontend_dist
    if dist and Path(dist).is_dir():
        assets = Path(dist) / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):
            if full_path.startswith("api/"):
                raise NotFound("Unknown API route.")
            target = (Path(dist) / full_path).resolve()
            if full_path and target.is_file() and Path(dist).resolve() in target.parents:
                return FileResponse(target)
            return FileResponse(Path(dist) / "index.html")

    return app
