"""Tests 1–3: valid import, invalid/unsupported files, videos without audio."""

from __future__ import annotations

import pytest

from clipforge import services
from clipforge.errors import Conflict, InvalidMedia, InvalidPath
from clipforge.media.probe import probe


def test_probe_valid_video(media):
    info = probe(media["talk"]["video"])
    assert info.width == 640 and info.height == 360
    assert info.fps == pytest.approx(30, abs=0.01)
    assert info.has_audio and info.audio_codec == "aac"
    assert info.duration_s == pytest.approx(media["talk"]["duration"], abs=0.1)
    assert info.video_codec == "h264"


def test_import_creates_media_and_jobs(ctx, talk_copy):
    p = services.create_project(ctx, "My Pod")
    m, jobs = services.import_media(ctx, p.id, str(talk_copy))
    assert m.status == "ready" and m.has_audio and m.duration_s > 60
    assert m.fingerprint.startswith("v1:")
    assert len(jobs) == 1  # transcribe (analysis is chained after it)
    # Same file again → refused, not duplicated.
    with pytest.raises(Conflict):
        services.import_media(ctx, p.id, str(talk_copy))


@pytest.mark.parametrize("name,content,code", [
    ("notes.txt", b"hello", "UNSUPPORTED_FORMAT"),
    ("broken.mp4", b"\x00\x01this is not a video" * 100, "UNREADABLE_MEDIA"),
    ("empty.mp4", b"", "EMPTY_FILE"),
])
def test_invalid_files_rejected(tmp_path, name, content, code):
    f = tmp_path / name
    f.write_bytes(content)
    with pytest.raises(InvalidMedia) as e:
        probe(f)
    assert e.value.code == code
    assert e.value.message


def test_truncated_video_rejected(tmp_path, media):
    data = media["talk"]["video"].read_bytes()
    f = tmp_path / "cut.mp4"
    f.write_bytes(data[: len(data) // 50])  # moov atom at the end → unreadable
    with pytest.raises(InvalidMedia):
        probe(f)


def test_audio_only_rejected(tmp_path):
    import subprocess
    f = tmp_path / "audio.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=d=3", "-c:a", "aac", str(f)], check=True)
    with pytest.raises(InvalidMedia) as e:
        probe(f)
    assert e.value.code == "NO_VIDEO_STREAM"


def test_missing_and_relative_paths(ctx, tmp_path):
    p = services.create_project(ctx, "x")
    with pytest.raises(InvalidMedia) as e:
        services.import_media(ctx, p.id, str(tmp_path / "nope.mp4"))
    assert e.value.code == "FILE_NOT_FOUND"
    with pytest.raises(InvalidPath):
        services.import_media(ctx, p.id, "relative/video.mp4")


def test_video_without_audio(ctx, worker, media):
    info = probe(media["silent"])
    assert not info.has_audio and info.audio_codec is None
    p = services.create_project(ctx, "silent")
    m, jobs = services.import_media(ctx, p.id, str(media["silent"]), profile_key="vlog")
    assert worker.wait_idle(60)
    from clipforge.db.models import CandidateClip, Job
    with ctx.session() as s:
        job = s.get(Job, jobs[0])
        assert job.kind == "analyze" and job.status == "completed"  # no transcription for silent video
        assert job.result["fallback"] == "no-speech"
        cands = s.query(CandidateClip).filter_by(media_id=m.id).all()
        assert cands, "silent videos still get (clearly-labelled) visual-only candidates"
        assert "No speech" in cands[0].reasons[0]["text"]
        cid = cands[0].id
    services.request_render(ctx, cid)
    assert worker.wait_idle(120)
    with ctx.session() as s:
        r = s.get(CandidateClip, cid).renders[-1]
        assert r.status == "completed", r.error
        assert r.validation["audio_codec"] == "aac"  # silent AAC track added for platform compatibility
        assert "silent track" in r.settings["output"]["audio"]
