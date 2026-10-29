"""End-to-end through the HTTP API with a real worker: import → transcribe →
analyse → review → render → export, plus API guard rails."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clipforge.api.app import create_app


@pytest.fixture
def client(ctx):
    app = create_app(ctx, start_worker=True)
    with TestClient(app) as c:
        yield c


def wait_jobs(client, media_id, timeout=150):
    deadline = time.time() + timeout
    while time.time() < deadline:
        active = client.get("/api/jobs", params={"media_id": media_id, "status": "queued,running"}).json()
        if not active:
            return client.get("/api/jobs", params={"media_id": media_id}).json()
        time.sleep(0.2)
    raise AssertionError("jobs did not finish")


def test_full_workflow(client, talk_copy, tmp_path):
    assert client.get("/api/health").json()["worker"] == "running"
    profiles = {p["key"] for p in client.get("/api/profiles").json()}
    assert {"podcast", "conversation", "reaction", "commentary", "gaming", "vlog"} <= profiles
    assert len(client.get("/api/caption-presets").json()) >= 5

    pr = client.post("/api/projects", json={"name": "Show", "default_profile_key": "podcast"})
    assert pr.status_code == 201
    pid = pr.json()["id"]

    listing = client.get("/api/fs/list", params={"path": str(talk_copy.parent)}).json()
    kinds = {e["name"]: e["kind"] for e in listing["entries"]}
    assert kinds == {"talk.mp4": "video", "talk.srt": "transcript"}

    r = client.post(f"/api/projects/{pid}/media", json={"path": str(talk_copy)})
    assert r.status_code == 201, r.text
    media = r.json()
    assert media["has_audio"] and media["width"] == 640 and media["browser_playable"]
    jobs = wait_jobs(client, media["id"])
    assert {j["kind"]: j["status"] for j in jobs} == {"transcribe": "completed", "analyze": "completed"}

    tr = client.get(f"/api/media/{media['id']}/transcript").json()
    assert tr["status"] == "complete" and tr["segments"][0]["words"]

    cands = client.get(f"/api/media/{media['id']}/candidates").json()
    assert cands and all(c["reasons"] and c["review_status"] == "pending" for c in cands)
    best = cands[0]
    assert 30 <= best["duration_s"] <= 80 and best["score_breakdown"]["hook"]["weight"] > 0

    # Adjust, approve → render job; then preview + export.
    p = client.patch(f"/api/candidates/{best['id']}", json={"end_s": best["end_s"] - 0.5,
                                                             "caption_preset_key": "clean-box"})
    assert p.status_code == 200 and p.json()["caption_preset_key"] == "clean-box"
    rv = client.post(f"/api/candidates/{best['id']}/review", json={"action": "approve"}).json()
    assert rv["review_status"] == "approved" and rv["render_job_id"]
    wait_jobs(client, media["id"])
    job = client.get(f"/api/jobs/{rv['render_job_id']}").json()
    assert job["status"] == "completed" and job["events"], job
    c = client.get(f"/api/candidates/{best['id']}").json()
    rid = c["latest_render_id"]
    render = client.get(f"/api/renders/{rid}").json()
    assert render["validation"]["ok"] and render["settings"]["argv"] and render["encoder"]

    v = client.get(f"/api/renders/{rid}/video", headers={"Range": "bytes=0-99"})
    assert v.status_code == 206 and len(v.content) == 100  # seekable preview
    src = client.get(f"/api/media/{media['id']}/source", headers={"Range": "bytes=0-9"})
    assert src.status_code == 206
    fr = client.get(f"/api/media/{media['id']}/frame", params={"t": 3})
    assert fr.status_code == 200 and fr.content[:2] == b"\xff\xd8"

    ex = client.post(f"/api/renders/{rid}/export", json={"folder": str(tmp_path / "exports")})
    assert ex.status_code == 200, ex.text
    assert Path(ex.json()["dest_path"]).is_file()
    assert client.get(f"/api/renders/{rid}").json()["exports"]

    # Re-render creates v2, keeps v1.
    client.post(f"/api/candidates/{best['id']}/render")
    wait_jobs(client, media["id"])
    renders = client.get(f"/api/candidates/{best['id']}/renders").json()
    assert [r["version"] for r in renders] == [1, 2] and all(r["status"] == "completed" for r in renders)

    proj = client.get(f"/api/projects/{pid}").json()
    assert proj["media"][0]["candidate_counts"].get("approved") == 1


def test_errors_are_structured(client, tmp_path):
    pid = client.post("/api/projects", json={"name": "E"}).json()["id"]
    bad = tmp_path / "x.txt"
    bad.write_text("no")
    r = client.post(f"/api/projects/{pid}/media", json={"path": str(bad)})
    assert r.status_code == 422 and r.json()["error"]["code"] == "UNSUPPORTED_FORMAT"
    assert r.json()["error"]["hint"]
    assert client.get("/api/candidates/nope").status_code == 404
    assert client.post("/api/jobs/nope/cancel").status_code == 404
    r = client.put("/api/settings", json={"whisper_device": "tpu"})
    assert r.status_code == 422
    r = client.put("/api/settings", json={"whisper_model": "medium", "encoder_preference": "cpu"})
    assert r.status_code == 200 and r.json()["whisper_model"] == "medium"


def test_rejects_foreign_host_header(client):
    r = client.get("/api/health", headers={"Host": "evil.example.com"})
    assert r.status_code == 400


def test_diagnostics(client):
    d = client.get("/api/system/diagnostics").json()
    assert d["ffmpeg"]["ffmpeg"]["ok"] and d["hardware"]["usable_h264"]
    assert "faster_whisper" in d["python_packages"] and d["transcription"]["models_dir"]
