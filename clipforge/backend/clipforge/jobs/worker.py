"""Job worker: one thread per lane, heartbeats, cancellation.

Lanes keep heavy work from stacking up: ``transcribe`` (GPU/large RAM) and
``media`` (analysis + rendering) each run one job at a time by default, so a
transcription and a render can overlap but two Whisper models never load at once.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
import traceback
import uuid
from typing import Callable

from ..errors import ClipForgeError, JobCancelled
from . import queue


class JobInterrupted(JobCancelled):
    """Worker shutting down — not a user cancel. Subclasses JobCancelled so engine
    code that kills subprocesses on cancel does the same here."""
from .lock import FileLock

log = logging.getLogger("clipforge.worker")

DEFAULT_LANES = {"transcribe": 1, "media": 1}


class JobContext:
    """Handed to a handler. ``progress`` is cheap to call often: DB writes are
    throttled, and each call is a cancellation checkpoint."""

    def __init__(self, worker: "Worker", job):
        self.worker = worker
        self.app = worker.app
        self.job_id: str = job.id
        self.token: str = job.claim_token
        self.payload: dict = dict(job.payload or {})
        self.kind: str = job.kind
        self.attempt: int = job.attempts
        self.cancel_event = threading.Event()
        self.shutdown_event = threading.Event()
        self._stage = "starting"
        self._progress = 0.0
        self._last_write = 0.0
        self._lock = threading.Lock()

    def check(self) -> None:
        if self.cancel_event.is_set():
            raise JobCancelled()
        if self.shutdown_event.is_set():
            raise JobInterrupted()

    def stage(self, name: str, progress: float | None = None) -> None:
        with self._lock:
            self._stage = name
            if progress is not None:
                self._progress = progress
        self._flush(force=True)
        self.check()

    def progress(self, fraction: float) -> None:
        with self._lock:
            self._progress = max(self._progress, min(1.0, fraction))
        self._flush()
        self.check()

    def event(self, message: str, level: str = "info", data: dict | None = None) -> None:
        with self.app.session() as s:
            queue.add_event(s, self.job_id, message, level, data)

    def _flush(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_write < 0.5:
            return
        self._last_write = now
        with self._lock:
            stage, prog = self._stage, self._progress
        if queue.heartbeat(self.app.sf, self.job_id, self.token, self.app.settings.lease_s,
                           stage=stage, progress=prog):
            self.cancel_event.set()


Handler = Callable[[JobContext], dict]


class Worker:
    def __init__(self, app, handlers: dict[str, Handler] | None = None,
                 lanes: dict[str, int] | None = None):
        from .handlers import HANDLERS
        self.app = app
        self.handlers = handlers or HANDLERS
        self.lanes = lanes or DEFAULT_LANES
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.lock = FileLock(app.settings.lock_path)
        self.running_jobs: dict[str, JobContext] = {}

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> bool:
        if not self.lock.acquire():
            log.warning("Another ClipForge worker holds %s; not starting a second one.",
                        self.app.settings.lock_path)
            return False
        recovered = queue.recover(self.app.sf, worker_id=self.worker_id, owns_data_dir=True)
        if recovered:
            log.info("Recovered %d interrupted job(s).", len(recovered))
        for lane, n in self.lanes.items():
            for i in range(n):
                t = threading.Thread(target=self._loop, args=(lane,), name=f"cf-{lane}-{i}", daemon=True)
                t.start()
                self._threads.append(t)
        log.info("Worker %s started (lanes: %s).", self.worker_id, self.lanes)
        return True

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        # Interrupt running jobs at their next checkpoint; they go back to the queue
        # without spending an attempt. Anything that doesn't stop in time is
        # recovered by the next start (the OS releases our lock when we exit).
        for ctx in list(self.running_jobs.values()):
            ctx.shutdown_event.set()
        for t in self._threads:
            t.join(timeout=timeout)
        self._threads.clear()
        self.lock.release()

    # ------------------------------------------------------------------ loop
    def _loop(self, lane: str) -> None:
        while not self._stop.is_set():
            try:
                job = queue.claim(self.app.sf, lane=lane, worker_id=self.worker_id,
                                  lease_s=self.app.settings.lease_s)
            except Exception:  # DB locked for too long etc. — keep the thread alive
                log.exception("claim failed")
                job = None
            if job is None:
                self._stop.wait(self.app.settings.poll_s)
                continue
            self.run_job(job)

    def _heartbeat(self, ctx: JobContext, done: threading.Event) -> None:
        while not done.wait(self.app.settings.heartbeat_s):
            try:
                if queue.heartbeat(self.app.sf, ctx.job_id, ctx.token, self.app.settings.lease_s):
                    ctx.cancel_event.set()
            except queue.LostLease:
                ctx.cancel_event.set()
                return
            except Exception:
                log.exception("heartbeat failed for %s", ctx.job_id)

    def run_job(self, job) -> None:
        ctx = JobContext(self, job)
        handler = self.handlers.get(job.kind)
        done = threading.Event()
        hb = threading.Thread(target=self._heartbeat, args=(ctx, done), daemon=True)
        hb.start()
        self.running_jobs[ctx.job_id] = ctx
        try:
            if handler is None:
                raise ClipForgeError(f"No handler for job kind '{job.kind}'.", code="UNKNOWN_JOB_KIND")
            result = handler(ctx)
            ctx.check()  # a cancel that lands after the last checkpoint still wins
            queue.complete(self.app.sf, ctx.job_id, ctx.token, result)
        except JobInterrupted:  # before JobCancelled: it's a subclass
            self._safe(queue.requeue_interrupted, self.app.sf, ctx.job_id, ctx.token)
        except JobCancelled:
            self._safe(queue.mark_cancelled, self.app.sf, ctx.job_id, ctx.token)
        except queue.LostLease:
            log.warning("Lost lease on %s; another worker owns it now.", ctx.job_id)
        except ClipForgeError as e:
            log.warning("Job %s failed: %s %s", ctx.job_id, e.code, e.message)
            self._safe(queue.fail, self.app.sf, ctx.job_id, ctx.token, code=e.code, message=e.message,
                       retryable=e.retryable, hint=e.hint)
        except Exception as e:  # noqa: BLE001 - unexpected bug: record it, retry safely
            log.exception("Job %s crashed", ctx.job_id)
            tb = traceback.format_exc(limit=8)
            self._safe(ctx.event, f"Unexpected error: {e}", "error", {"traceback": tb})
            self._safe(queue.fail, self.app.sf, ctx.job_id, ctx.token, code="INTERNAL_ERROR",
                       message=f"{type(e).__name__}: {e}", retryable=True)
        finally:
            done.set()
            hb.join(timeout=5)
            self.running_jobs.pop(ctx.job_id, None)

    @staticmethod
    def _safe(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except queue.LostLease:
            return None
        except Exception:
            log.exception("bookkeeping failed")
            return None

    def wait_idle(self, timeout: float = 60.0, poll: float = 0.1) -> bool:
        """Test helper: wait until no queued/running jobs remain."""
        from sqlalchemy import func, select

        from ..db.models import Job
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.app.session() as s:
                n = s.scalar(select(func.count()).select_from(Job).where(Job.status.in_(("queued", "running"))))
            if n == 0:
                return True
            time.sleep(poll)
        return False
