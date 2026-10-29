"""Tests 12–14: persistence after restart, failed-job retry, cancellation and
duplicate prevention."""

from __future__ import annotations

import threading
import time

import pytest
from sqlalchemy.exc import IntegrityError

from clipforge.context import AppContext
from clipforge.db.models import Job
from clipforge.errors import ClipForgeError, Conflict
from clipforge.jobs import queue
from clipforge.jobs.worker import Worker


@pytest.fixture(autouse=True)
def fast_retry(monkeypatch):
    monkeypatch.setattr(queue, "RETRY_BASE_S", 0.05)


def job(ctx, jid) -> Job:
    with ctx.session() as s:
        return s.get(Job, jid)


def run_worker(ctx, handlers, *, lanes=None) -> Worker:
    w = Worker(ctx, handlers=handlers, lanes=lanes or {"media": 1})
    assert w.start()
    return w


# --------------------------------------------------------------------------- 12. persistence
def test_jobs_survive_restart_and_orphans_are_recovered(ctx):
    done = []
    j1, _ = queue.enqueue(ctx.sf, kind="noop", lane="media", payload={"n": 1})
    j2, _ = queue.enqueue(ctx.sf, kind="noop", lane="media", payload={"n": 2})
    # Simulate a crash: a previous process claimed j1 and died mid-run.
    claimed = queue.claim(ctx.sf, lane="media", worker_id="dead-process", lease_s=600)
    assert claimed.id == j1.id and job(ctx, j1.id).status == "running"
    ctx.close()

    # "Restart": a brand-new context over the same data dir.
    ctx2 = AppContext(ctx.settings)
    try:
        assert job(ctx2, j1.id).status == "running"  # persisted
        w = run_worker(ctx2, {"noop": lambda c: done.append(c.payload["n"]) or {"ok": True}})
        try:
            assert w.wait_idle(10)
        finally:
            w.stop()
        a, b = job(ctx2, j1.id), job(ctx2, j2.id)
        assert a.status == b.status == "completed"
        assert sorted(done) == [1, 2]
        assert a.attempts == 2  # the interrupted attempt counts
        with ctx2.session() as s:
            msgs = [e.message for e in s.get(Job, j1.id).events]
        assert any("Recovered after the app restarted" in m for m in msgs)
    finally:
        ctx2.close()


def test_interrupted_too_often_fails_instead_of_looping(ctx):
    j, _ = queue.enqueue(ctx.sf, kind="noop", lane="media", payload={}, max_attempts=1)
    queue.claim(ctx.sf, lane="media", worker_id="dead", lease_s=600)
    queue.recover(ctx.sf, worker_id="me", owns_data_dir=True)
    r = job(ctx, j.id)
    assert r.status == "failed" and r.error_code == "INTERRUPTED"


def test_graceful_shutdown_requeues_without_spending_attempt(ctx):
    started = threading.Event()

    def slow(c):
        started.set()
        for _ in range(200):
            c.progress(0.1)
            time.sleep(0.02)
        return {}
    j, _ = queue.enqueue(ctx.sf, kind="slow", lane="media", payload={})
    w = run_worker(ctx, {"slow": slow})
    assert started.wait(5)
    w.stop()
    r = job(ctx, j.id)
    assert r.status == "queued" and r.attempts == 0


def test_only_one_worker_per_data_dir(ctx):
    a = Worker(ctx, handlers={}, lanes={"media": 1})
    b = Worker(ctx, handlers={}, lanes={"media": 1})
    assert a.start()
    try:
        assert not b.start()
    finally:
        a.stop()
    assert b.start()
    b.stop()


# --------------------------------------------------------------------------- 13. retry
def test_retryable_failure_retries_then_succeeds(ctx):
    calls = {"n": 0}

    def flaky(c):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ClipForgeError("temporary", code="TEMP", retryable=True)
        return {"ok": True}
    j, _ = queue.enqueue(ctx.sf, kind="flaky", lane="media", payload={}, max_attempts=3)
    w = run_worker(ctx, {"flaky": flaky})
    try:
        assert w.wait_idle(10)
    finally:
        w.stop()
    r = job(ctx, j.id)
    assert r.status == "completed" and r.attempts == 3 and calls["n"] == 3 and r.error is None


def test_non_retryable_failure_fails_immediately_and_never_reports_success(ctx):
    def bad(c):
        raise ClipForgeError("source is gone", code="SOURCE_MISSING", retryable=False, hint="relink it")
    j, _ = queue.enqueue(ctx.sf, kind="bad", lane="media", payload={}, max_attempts=5)
    w = run_worker(ctx, {"bad": bad})
    try:
        assert w.wait_idle(10)
    finally:
        w.stop()
    r = job(ctx, j.id)
    assert r.status == "failed" and r.attempts == 1 and r.error_code == "SOURCE_MISSING"
    assert "relink it" in r.error and r.result is None


def test_unexpected_exception_is_recorded_and_retried(ctx):
    def crash(c):
        raise ZeroDivisionError("oops")
    j, _ = queue.enqueue(ctx.sf, kind="crash", lane="media", payload={}, max_attempts=2)
    w = run_worker(ctx, {"crash": crash})
    try:
        assert w.wait_idle(10)
    finally:
        w.stop()
    r = job(ctx, j.id)
    assert r.status == "failed" and r.attempts == 2 and r.error_code == "INTERNAL_ERROR"
    with ctx.session() as s:
        assert any(e.data and "traceback" in e.data for e in s.get(Job, j.id).events)


def test_manual_retry_of_failed_job(ctx):
    state = {"fail": True}

    def h(c):
        if state["fail"]:
            raise ClipForgeError("nope", retryable=False)
        return {"ok": 1}
    j, _ = queue.enqueue(ctx.sf, kind="h", lane="media", payload={}, dedupe_key="k1")
    w = run_worker(ctx, {"h": h})
    try:
        assert w.wait_idle(10) and job(ctx, j.id).status == "failed"
        state["fail"] = False
        again, requeued = queue.retry(ctx.sf, j.id)
        assert requeued and again.id == j.id
        assert w.wait_idle(10)
    finally:
        w.stop()
    assert job(ctx, j.id).status == "completed"
    with pytest.raises(Conflict):
        queue.retry(ctx.sf, j.id)  # completed jobs can't be retried


def test_retry_does_not_duplicate_an_active_job(ctx):
    j, _ = queue.enqueue(ctx.sf, kind="x", lane="media", payload={}, dedupe_key="same")
    queue.request_cancel(ctx.sf, j.id)
    j2, created = queue.enqueue(ctx.sf, kind="x", lane="media", payload={}, dedupe_key="same")
    assert created
    active, requeued = queue.retry(ctx.sf, j.id)
    assert not requeued and active.id == j2.id
    assert job(ctx, j.id).status == "cancelled"


# --------------------------------------------------------------------------- 14. cancel / duplicates
def test_cancel_queued_job_never_runs(ctx):
    ran = []
    j, _ = queue.enqueue(ctx.sf, kind="r", lane="media", payload={})
    queue.request_cancel(ctx.sf, j.id)
    w = run_worker(ctx, {"r": lambda c: ran.append(1) or {}})
    try:
        time.sleep(0.4)
    finally:
        w.stop()
    assert job(ctx, j.id).status == "cancelled" and not ran


def test_cancel_running_job_stops_at_checkpoint(ctx):
    started, steps = threading.Event(), {"n": 0}

    def long(c):
        started.set()
        for i in range(500):
            steps["n"] = i
            c.progress(i / 500)
            time.sleep(0.01)
        return {"finished": True}
    j, _ = queue.enqueue(ctx.sf, kind="long", lane="media", payload={})
    w = run_worker(ctx, {"long": long})
    try:
        assert started.wait(5)
        queue.request_cancel(ctx.sf, j.id)
        assert w.wait_idle(10)
    finally:
        w.stop()
    r = job(ctx, j.id)
    assert r.status == "cancelled" and r.result is None and steps["n"] < 499


def test_cannot_cancel_finished_job(ctx):
    j, _ = queue.enqueue(ctx.sf, kind="z", lane="media", payload={})
    c = queue.claim(ctx.sf, lane="media", worker_id="w", lease_s=10)
    queue.complete(ctx.sf, c.id, c.claim_token, {})
    with pytest.raises(Conflict):
        queue.request_cancel(ctx.sf, j.id)


def test_enqueue_dedupes_active_jobs(ctx):
    a, created_a = queue.enqueue(ctx.sf, kind="t", lane="media", payload={}, dedupe_key="render:1")
    b, created_b = queue.enqueue(ctx.sf, kind="t", lane="media", payload={}, dedupe_key="render:1")
    assert created_a and not created_b and a.id == b.id
    # The database itself refuses a second active row with the same key.
    with ctx.session() as s:
        s.add(Job(kind="t", lane="media", payload={}, dedupe_key="render:1"))
        with pytest.raises(IntegrityError):
            s.flush()
        s.rollback()
    # Once finished, the key is free again.
    c = queue.claim(ctx.sf, lane="media", worker_id="w", lease_s=10)
    queue.complete(ctx.sf, c.id, c.claim_token, {})
    d, created_d = queue.enqueue(ctx.sf, kind="t", lane="media", payload={}, dedupe_key="render:1")
    assert created_d and d.id != a.id


def test_concurrent_claims_never_double_execute(ctx):
    ids = {queue.enqueue(ctx.sf, kind="k", lane="media", payload={"i": i})[0].id for i in range(30)}
    claimed: list[str] = []
    lock = threading.Lock()

    def grab(n):
        while True:
            j = queue.claim(ctx.sf, lane="media", worker_id=f"w{n}", lease_s=30)
            if j is None:
                return
            with lock:
                claimed.append(j.id)
    threads = [threading.Thread(target=grab, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(claimed) == sorted(ids) and len(claimed) == len(set(claimed))


def test_stale_owner_cannot_overwrite_new_owner(ctx):
    j, _ = queue.enqueue(ctx.sf, kind="k", lane="media", payload={})
    first = queue.claim(ctx.sf, lane="media", worker_id="old", lease_s=0.01)
    time.sleep(0.05)
    queue.recover(ctx.sf, worker_id="new", owns_data_dir=False)  # lease expired → re-queued
    second = queue.claim(ctx.sf, lane="media", worker_id="new", lease_s=30)
    assert second.id == j.id and second.claim_token != first.claim_token
    with pytest.raises(queue.LostLease):
        queue.complete(ctx.sf, j.id, first.claim_token, {"stale": True})
    queue.complete(ctx.sf, j.id, second.claim_token, {"fresh": True})
    assert job(ctx, j.id).result == {"fresh": True}
