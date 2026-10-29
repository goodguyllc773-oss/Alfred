"""Durable job state machine on SQLite.

States: queued → running → completed | failed | cancelled (failed/cancelled can be
manually retried back to queued). Guarantees:

* **No duplicate active jobs**: a partial unique index on ``dedupe_key`` for
  queued/running rows; ``enqueue`` returns the existing job instead.
* **No double execution**: ``claim`` is one atomic ``UPDATE … WHERE status='queued'``
  stamping a fresh ``claim_token``; every later write is conditional on that token,
  so a worker that lost its lease can't overwrite the new owner's state.
* **Crash recovery**: ``recover`` re-queues orphaned running jobs (or fails them
  with ``INTERRUPTED`` once ``max_attempts`` is used up).
* **Never a false "completed"**: only ``complete`` sets it, and only for the
  current claim.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import DateTime, bindparam, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ..db.models import ACTIVE_JOB_STATES, Job, JobEvent, utcnow
from ..errors import Conflict, NotFound

RETRY_BASE_S = 5.0
RETRY_MAX_S = 300.0


class LostLease(Exception):
    """The job was re-claimed by someone else (our lease expired)."""


def add_event(s: Session, job_id: str, message: str, level: str = "info", data: dict | None = None) -> None:
    s.add(JobEvent(job_id=job_id, message=message[:4000], level=level, data=data))


def enqueue(sf: sessionmaker[Session], *, kind: str, lane: str, payload: dict[str, Any],
            dedupe_key: str | None = None, max_attempts: int = 3, priority: int = 0,
            project_id: str | None = None, media_id: str | None = None,
            candidate_id: str | None = None) -> tuple[Job, bool]:
    """Returns ``(job, created)``. With a ``dedupe_key`` an active job with the same
    key is returned instead of creating a second one."""
    with sf() as s:
        if dedupe_key:
            existing = s.scalar(select(Job).where(Job.dedupe_key == dedupe_key,
                                                  Job.status.in_(ACTIVE_JOB_STATES)))
            if existing:
                return existing, False
        job = Job(kind=kind, lane=lane, payload=payload, dedupe_key=dedupe_key, max_attempts=max_attempts,
                  priority=priority, project_id=project_id, media_id=media_id, candidate_id=candidate_id)
        s.add(job)
        try:
            s.flush()
            add_event(s, job.id, f"Queued {kind}.")
            s.commit()
            return job, True
        except IntegrityError:  # lost a race with another enqueue of the same key
            s.rollback()
            existing = s.scalar(select(Job).where(Job.dedupe_key == dedupe_key,
                                                  Job.status.in_(ACTIVE_JOB_STATES)))
            if existing is None:
                raise
            return existing, False


def claim(sf: sessionmaker[Session], *, lane: str, worker_id: str, lease_s: float) -> Job | None:
    token = str(uuid.uuid4())
    now = utcnow()
    with sf() as s:
        stmt = text(
            "UPDATE jobs SET status='running', claim_token=:tok, worker_id=:wid, "
            "lease_expires_at=:lease, attempts=attempts+1, started_at=:now, updated_at=:now, "
            "stage='starting' "
            "WHERE id = (SELECT id FROM jobs WHERE status='queued' AND lane=:lane AND run_after<=:now "
            "            AND cancel_requested=0 ORDER BY priority DESC, created_at LIMIT 1) "
            "AND status='queued'"
        ).bindparams(bindparam("now", type_=DateTime), bindparam("lease", type_=DateTime))
        res = s.execute(stmt, {"tok": token, "wid": worker_id, "lease": now + timedelta(seconds=lease_s),
                               "now": now, "lane": lane})
        if res.rowcount != 1:
            s.rollback()
            return None
        s.commit()
        job = s.scalar(select(Job).where(Job.claim_token == token))
        if job is not None:
            add_event(s, job.id, f"Started (attempt {job.attempts}/{job.max_attempts}) on {worker_id}.")
            s.commit()
        return job


def _owned(s: Session, job_id: str, token: str) -> Job:
    job = s.get(Job, job_id)
    if job is None or job.claim_token != token or job.status != "running":
        raise LostLease(job_id)
    return job


def heartbeat(sf: sessionmaker[Session], job_id: str, token: str, lease_s: float, *,
              stage: str | None = None, progress: float | None = None) -> bool:
    """Extend the lease (and optionally record progress). Returns ``cancel_requested``."""
    with sf() as s:
        values: dict[str, Any] = {"lease_expires_at": utcnow() + timedelta(seconds=lease_s),
                                  "updated_at": utcnow()}
        if stage is not None:
            values["stage"] = stage[:120]
        if progress is not None:
            values["progress"] = max(0.0, min(1.0, float(progress)))
        res = s.execute(update(Job).where(Job.id == job_id, Job.claim_token == token,
                                          Job.status == "running").values(**values))
        if res.rowcount != 1:
            s.rollback()
            raise LostLease(job_id)
        cancel = bool(s.scalar(select(Job.cancel_requested).where(Job.id == job_id)))
        s.commit()
        return cancel


def complete(sf: sessionmaker[Session], job_id: str, token: str, result: dict | None = None) -> None:
    with sf() as s:
        job = _owned(s, job_id, token)
        job.status, job.progress, job.result = "completed", 1.0, result or {}
        job.stage, job.finished_at, job.lease_expires_at = "done", utcnow(), None
        job.error_code = job.error = None
        add_event(s, job_id, "Completed.", data=result)
        s.commit()


def fail(sf: sessionmaker[Session], job_id: str, token: str, *, code: str, message: str,
         retryable: bool, hint: str | None = None) -> str:
    """Record a failure. Returns the new status: 'queued' (will retry) or 'failed'."""
    with sf() as s:
        job = _owned(s, job_id, token)
        job.error_code, job.error = code, message if not hint else f"{message}\n\nHow to fix: {hint}"
        job.lease_expires_at = None
        if retryable and job.attempts < job.max_attempts:
            delay = min(RETRY_MAX_S, RETRY_BASE_S * 2 ** (job.attempts - 1))
            job.status, job.stage, job.claim_token, job.worker_id = "queued", "retry scheduled", None, None
            job.run_after = utcnow() + timedelta(seconds=delay)
            add_event(s, job_id, f"{code}: {message} — retrying in {delay:.0f}s "
                                 f"(attempt {job.attempts}/{job.max_attempts}).", level="warning")
        else:
            job.status, job.finished_at, job.stage = "failed", utcnow(), "failed"
            add_event(s, job_id, f"{code}: {message}", level="error", data={"hint": hint})
        s.commit()
        return job.status


def mark_cancelled(sf: sessionmaker[Session], job_id: str, token: str) -> None:
    with sf() as s:
        job = _owned(s, job_id, token)
        job.status, job.finished_at, job.stage, job.lease_expires_at = "cancelled", utcnow(), "cancelled", None
        add_event(s, job_id, "Cancelled while running.", level="warning")
        s.commit()


def requeue_interrupted(sf: sessionmaker[Session], job_id: str, token: str) -> None:
    """Graceful shutdown: put the job back without spending an attempt."""
    with sf() as s:
        job = _owned(s, job_id, token)
        job.status, job.stage, job.claim_token, job.worker_id = "queued", "interrupted by shutdown", None, None
        job.attempts = max(0, job.attempts - 1)
        job.lease_expires_at, job.run_after = None, utcnow()
        add_event(s, job_id, "Interrupted by app shutdown; will resume on next start.", level="warning")
        s.commit()


def request_cancel(sf: sessionmaker[Session], job_id: str) -> Job:
    with sf() as s:
        job = s.get(Job, job_id)
        if job is None:
            raise NotFound("Job not found.")
        if job.status == "queued":
            job.status, job.finished_at, job.stage, job.cancel_requested = "cancelled", utcnow(), "cancelled", True
            add_event(s, job_id, "Cancelled before it started.", level="warning")
        elif job.status == "running":
            job.cancel_requested = True
            add_event(s, job_id, "Cancellation requested.", level="warning")
        else:
            raise Conflict(f"Job is already {job.status}.")
        s.commit()
        return job


def retry(sf: sessionmaker[Session], job_id: str) -> tuple[Job, bool]:
    """Manually re-queue a failed/cancelled job. Returns ``(job, requeued)``; if an
    equivalent job is already active, returns that one and ``requeued=False``."""
    with sf() as s:
        job = s.get(Job, job_id)
        if job is None:
            raise NotFound("Job not found.")
        if job.status not in ("failed", "cancelled"):
            raise Conflict(f"Only failed or cancelled jobs can be retried (this one is {job.status}).")
        job.status, job.stage, job.progress, job.attempts = "queued", "queued (manual retry)", 0.0, 0
        job.cancel_requested, job.claim_token, job.worker_id = False, None, None
        job.run_after, job.finished_at, job.started_at = utcnow(), None, None
        job.error_code = job.error = None
        try:
            add_event(s, job_id, "Manually re-queued.")
            s.commit()
            return job, True
        except IntegrityError:
            s.rollback()
            active = s.scalar(select(Job).where(Job.dedupe_key == job.dedupe_key,
                                                Job.status.in_(ACTIVE_JOB_STATES)))
            if active is None:
                raise
            return active, False


def recover(sf: sessionmaker[Session], *, worker_id: str, owns_data_dir: bool) -> list[str]:
    """Re-queue jobs whose worker died. With the data-dir lock held, every running
    job not owned by ``worker_id`` is orphaned; otherwise only expired leases are."""
    now = utcnow()
    touched = []
    with sf() as s:
        q = select(Job).where(Job.status == "running")
        for job in s.scalars(q).all():
            orphan = (job.worker_id != worker_id) if owns_data_dir else \
                (job.lease_expires_at is None or job.lease_expires_at < now)
            if not orphan:
                continue
            touched.append(job.id)
            job.claim_token, job.worker_id, job.lease_expires_at = None, None, None
            if job.cancel_requested:
                job.status, job.finished_at, job.stage = "cancelled", now, "cancelled"
                add_event(s, job.id, "Was cancelled when the app stopped.", level="warning")
            elif job.attempts >= job.max_attempts:
                job.status, job.finished_at, job.stage = "failed", now, "failed"
                job.error_code = "INTERRUPTED"
                job.error = f"Interrupted {job.attempts} time(s) (app closed or crashed while running)."
                add_event(s, job.id, job.error, level="error")
            else:
                job.status, job.stage, job.run_after = "queued", "recovered after restart", now
                add_event(s, job.id, "Recovered after the app restarted; re-queued.", level="warning")
        s.commit()
    return touched
