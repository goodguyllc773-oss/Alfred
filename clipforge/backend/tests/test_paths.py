"""Tests 16–17: missing source files, safe output naming and path handling."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from clipforge import services
from clipforge.db.models import CandidateClip, Job, SourceMedia
from clipforge.errors import Conflict, InvalidPath, SourceMissing
from clipforge.media.paths import (
    ensure_output_dir, fingerprint, from_relative, normalize_user_path, safe_filename, slugify, to_relative,
    unique_path,
)


# --------------------------------------------------------------------------- 16. missing sources
def test_missing_source_is_detected_and_relinked(ctx, worker, talk_copy, tmp_path):
    p = services.create_project(ctx, "moves")
    m, _ = services.import_media(ctx, p.id, str(talk_copy))
    assert worker.wait_idle(90)
    with ctx.session() as s:
        cid = s.query(CandidateClip).filter_by(media_id=m.id).first().id

    moved = tmp_path / "elsewhere" / "renamed.mp4"
    moved.parent.mkdir()
    shutil.move(str(talk_copy), moved)
    with pytest.raises(SourceMissing) as e:
        services.check_source(ctx, m.id)
    assert "Relink" in e.value.hint
    with ctx.session() as s:
        assert s.get(SourceMedia, m.id).status == "missing"

    job = services.request_render(ctx, cid)
    assert worker.wait_idle(30)
    with ctx.session() as s:
        j = s.get(Job, job.id)
        assert j.status == "failed" and j.error_code == "SOURCE_MISSING" and j.attempts == 1

    # A different file is refused unless forced.
    other = tmp_path / "other.mp4"
    other.write_bytes(b"\x00" * 1000)
    with pytest.raises(Conflict):
        services.relink_media(ctx, m.id, str(other))
    m2 = services.relink_media(ctx, m.id, str(moved))
    assert m2.status == "ready" and m2.original_path == str(moved) and m2.file_name == "renamed.mp4"

    services.request_render(ctx, cid)
    assert worker.wait_idle(120)
    with ctx.session() as s:
        assert s.get(CandidateClip, cid).renders[-1].status == "completed"
    assert moved.exists()  # never deleted or modified


def test_fingerprint_survives_copy_and_detects_changes(tmp_path, media):
    a = tmp_path / "a.mp4"
    shutil.copy(media["talk"]["video"], a)
    b = tmp_path / "sub" / "b.mp4"
    b.parent.mkdir()
    shutil.copy(a, b)
    assert fingerprint(a) == fingerprint(b)
    with open(b, "r+b") as f:
        f.seek(10)
        f.write(b"XX")
    assert fingerprint(a) != fingerprint(b)


# --------------------------------------------------------------------------- 17. naming / paths
@pytest.mark.parametrize("raw,expected", [
    ("clip.mp4", "clip.mp4"),
    ('bad:name*?"<>|.mp4', "bad_name______.mp4"),
    ("CON.mp4", "_CON.mp4"),
    ("lpt1", "_lpt1"),
    ("  trailing dots... ", "trailing dots"),
    ("../../etc/passwd", "_.._etc_passwd"),
    ("", "clip"),
])
def test_safe_filename(raw, expected):
    assert safe_filename(raw) == expected


def test_safe_filename_length_and_unicode():
    name = safe_filename("é" * 300 + ".mp4", max_len=100)
    assert len(name) <= 100 and name.endswith(".mp4")
    assert slugify("Café — Ep. #12!") == "cafe-ep-12"
    assert slugify("!!!") == "untitled"


def test_unique_path_never_overwrites(tmp_path):
    (tmp_path / "x.mp4").write_bytes(b"1")
    (tmp_path / "x (2).mp4").write_bytes(b"2")
    assert unique_path(tmp_path, "x.mp4").name == "x (3).mp4"
    assert unique_path(tmp_path, "y.mp4").name == "y.mp4"


def test_relative_storage_and_escape_guard(tmp_path):
    root = tmp_path / "data"
    f = root / "renders" / "p" / "c.mp4"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"")
    rel = to_relative(f, root)
    assert rel == "renders/p/c.mp4"
    assert from_relative(rel, root) == f.resolve()
    with pytest.raises(InvalidPath):
        from_relative("../../outside.mp4", root)


def test_user_path_validation(tmp_path):
    with pytest.raises(InvalidPath):
        normalize_user_path("")
    with pytest.raises(InvalidPath):
        normalize_user_path("videos/x.mp4")
    with pytest.raises(InvalidPath):
        normalize_user_path("/tmp/a\x00b")
    assert normalize_user_path(f'"{tmp_path}/x.mp4"') == tmp_path / "x.mp4"  # pasted with quotes
    assert normalize_user_path(str(tmp_path / "a" / ".." / "b.mp4")) == tmp_path / "b.mp4"


def test_output_dir_validation(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    with pytest.raises(InvalidPath):
        ensure_output_dir(str(f))
    new = ensure_output_dir(str(tmp_path / "new" / "nested"))
    assert new.is_dir()
    if sys.platform != "win32" and os.geteuid() != 0:
        ro = tmp_path / "ro"
        ro.mkdir()
        ro.chmod(0o500)
        with pytest.raises(InvalidPath):
            ensure_output_dir(str(ro))


def test_export_copies_with_metadata_and_never_overwrites(ctx, worker, talk_copy, tmp_path):
    p = services.create_project(ctx, "Export Test: Ep/1")
    m, _ = services.import_media(ctx, p.id, str(talk_copy))
    assert worker.wait_idle(90)
    with ctx.session() as s:
        cid = s.query(CandidateClip).filter_by(media_id=m.id).first().id
    services.request_render(ctx, cid)
    assert worker.wait_idle(120)
    with ctx.session() as s:
        rid = s.get(CandidateClip, cid).renders[-1].id
    out = tmp_path / "My Exports"
    a = services.export_render(ctx, rid, str(out))
    b = services.export_render(ctx, rid, str(out))
    pa, pb = Path(a.dest_path), Path(b.dest_path)
    assert pa.exists() and pb.exists() and pa != pb and pa.parent == out
    assert pa.name.startswith("export-test-ep-1_") and "/" not in pa.name and ":" not in pa.name
    meta = json.loads(Path(a.metadata_path).read_text())
    assert meta["hashtags"] and meta["source"]["file"] == "talk.mp4" and meta["selection_reasons"]
    assert pa.with_suffix(".txt").read_text().strip()
    assert not list(out.glob("*.part"))
    with pytest.raises(InvalidPath):
        services.export_render(ctx, rid, "relative/folder")
