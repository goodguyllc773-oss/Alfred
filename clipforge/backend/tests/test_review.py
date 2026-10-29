"""Test 15: review-state transitions and candidate editing."""

from __future__ import annotations

import pytest

from clipforge import services
from clipforge.db.models import CandidateClip, Job
from clipforge.errors import NotFound, ValidationFailed


@pytest.fixture
def analysed(ctx, worker, talk_copy):
    p = services.create_project(ctx, "review", default_profile_key="podcast")
    m, _ = services.import_media(ctx, p.id, str(talk_copy))
    assert worker.wait_idle(90)
    with ctx.session() as s:
        ids = [c.id for c in s.query(CandidateClip).filter_by(media_id=m.id).order_by(CandidateClip.score.desc())]
    assert ids
    return m, ids


def status(ctx, cid):
    with ctx.session() as s:
        return s.get(CandidateClip, cid).review_status


def test_transitions(ctx, analysed):
    _, (cid, *_) = analysed
    assert status(ctx, cid) == "pending"
    services.review_candidate(ctx, cid, "reject")
    assert status(ctx, cid) == "rejected"
    services.review_candidate(ctx, cid, "approve", auto_render=False)
    assert status(ctx, cid) == "approved"
    services.review_candidate(ctx, cid, "approve", auto_render=False)  # idempotent
    assert status(ctx, cid) == "approved"
    services.review_candidate(ctx, cid, "reset")
    assert status(ctx, cid) == "pending"
    with ctx.session() as s:
        assert s.get(CandidateClip, cid).reviewed_at is None
    with pytest.raises(ValidationFailed):
        services.review_candidate(ctx, cid, "publish")
    with pytest.raises(NotFound):
        services.review_candidate(ctx, "no-such-id", "approve")


def test_approve_renders_once(ctx, worker, analysed):
    _, (cid, *_) = analysed
    _, job_id = services.review_candidate(ctx, cid, "approve")
    assert job_id
    assert services.request_render(ctx, cid).id == job_id  # still active → deduplicated
    assert worker.wait_idle(120)
    with ctx.session() as s:
        c = s.get(CandidateClip, cid)
        done = [r for r in c.renders if r.status == "completed"]
        assert len(done) == 1, [(r.status, r.error) for r in c.renders]
        assert s.get(Job, job_id).status == "completed"
    # Already rendered → re-approving doesn't queue another render.
    services.review_candidate(ctx, cid, "reset")
    _, again = services.review_candidate(ctx, cid, "approve")
    assert again is None


def test_edit_boundaries_validation_and_snap(ctx, analysed):
    m, (cid, *_) = analysed
    with ctx.session() as s:
        c = s.get(CandidateClip, cid)
        start, end, dur = c.start_s, c.end_s, m.duration_s
    with pytest.raises(ValidationFailed):
        services.update_candidate(ctx, cid, {"start_s": end + 1, "end_s": end})
    with pytest.raises(ValidationFailed):
        services.update_candidate(ctx, cid, {"end_s": dur + 5})
    with pytest.raises(ValidationFailed):
        services.update_candidate(ctx, cid, {"start_s": 10, "end_s": 10.5})  # < 1 s
    with pytest.raises(ValidationFailed):
        services.update_candidate(ctx, cid, {"bogus": 1})
    c = services.update_candidate(ctx, cid, {"start_s": start + 2.13, "end_s": end - 1.77, "snap": True})
    assert c.start_s != start + 2.13  # snapped to a word edge
    assert c.transcript_text and c.original_start_s == start  # proposal kept for reference
    c = services.update_candidate(ctx, cid, {"profile_key": "gaming", "caption_preset_key": "neon-caps",
                                             "layout_overrides": {"facecam_share": 0.4},
                                             "caption_overrides": {"highlight_color": "#00FF00"}})
    assert (c.profile_key, c.caption_preset_key) == ("gaming", "neon-caps")
    with pytest.raises(ValidationFailed):
        services.update_candidate(ctx, cid, {"layout_overrides": {"facecam_share": 5}})
    with pytest.raises(ValidationFailed):
        services.update_candidate(ctx, cid, {"caption_overrides": {"highlight_color": "green"}})
    with pytest.raises(NotFound):
        services.update_candidate(ctx, cid, {"profile_key": "nope"})


def test_reanalysis_keeps_reviewed_clips_and_replaces_untouched(ctx, worker, analysed):
    from clipforge.analysis.candidates import overlap_fraction
    m, ids = analysed
    approved, untouched = ids[0], ids[1:]
    services.review_candidate(ctx, approved, "approve", auto_render=False)
    job = services.request_analysis(ctx, m.id, "conversation")
    assert worker.wait_idle(60)
    with ctx.session() as s:
        result = s.get(Job, job.id).result
        assert result["kept"] == 1 and result["replaced"] == len(untouched)
        rows = {c.id: c for c in s.query(CandidateClip).filter_by(media_id=m.id)}
        assert rows[approved].review_status == "approved"
        assert not set(untouched) & set(rows)  # untouched pending ones were replaced
        kept = rows.pop(approved)
        for c in rows.values():
            assert c.profile_key == "conversation" and c.review_status == "pending"
            assert overlap_fraction((c.start_s, c.end_s), (kept.start_s, kept.end_s)) <= 0.3 + 1e-9
