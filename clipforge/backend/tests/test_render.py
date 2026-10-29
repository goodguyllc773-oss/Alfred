"""Tests 7–11: rendering with captions, A/V sync, CPU fallback, hardware detection,
output validation. These run real ffmpeg encodes (a few seconds each)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from clipforge.analysis.framing import FaceSummary
from clipforge.captions.style import BUILTIN_CAPTION_PRESETS, CaptionStyle
from clipforge.errors import JobCancelled, RenderFailed, SourceMissing
from clipforge.media import hwaccel
from clipforge.media.probe import probe
from clipforge.profiles.presets import BUILTIN_BY_KEY
from clipforge.render import renderer as renderer_mod
from clipforge.render.layouts import plan_layout
from clipforge.render.renderer import RenderRequest, render_clip, validate_output
from clipforge.transcription.sidecar import SidecarTranscriber

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def hw():
    return hwaccel.detect()


@pytest.fixture(scope="module")
def talk_words(media):
    res = SidecarTranscriber(media["talk"]["srt"]).transcribe_range(str(media["talk"]["video"]), 0, 1e9)
    return [w for s in res.segments for w in s.words]


def req_for(src: Path, tmp: Path, *, start=2.0, end=6.0, profile="podcast", layout=None, words=(),
            style=None, out=(360, 640), name="clip.mp4", face=None, captions=True) -> RenderRequest:
    info = probe(src)
    prof = BUILTIN_BY_KEY[profile]
    if layout:
        prof = prof.with_layout_overrides(layout)
    return RenderRequest(source=str(src), start_s=start, end_s=end, src_w=info.display_width,
                         src_h=info.display_height, src_fps=info.fps, has_audio=info.has_audio, profile=prof,
                         caption_style=style or BUILTIN_CAPTION_PRESETS["bold-pop"][1], words=list(words),
                         output_dir=tmp / "out", tmp_root=tmp / "out" / ".tmp", final_name=name,
                         out_w=out[0], out_h=out[1], face=face, captions=captions)


def frame_gray(path: Path, t: float, w: int, h: int) -> np.ndarray:
    out = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", str(path), "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True)
    return np.frombuffer(out.stdout, dtype=np.uint8).reshape(h, w).astype(np.int16)


# --------------------------------------------------------------------------- 7. captions
def test_render_with_captions_burns_text(media, tmp_path, hw, talk_words):
    src = media["talk"]["video"]
    s0 = media["talk"]["segments"][1]  # "Here's the thing nobody tells you about short videos."
    a = render_clip(req_for(src, tmp_path, start=s0[0] - 0.2, end=s0[1] + 0.3, words=talk_words,
                            name="cap.mp4"), hw=hw)
    b = render_clip(req_for(src, tmp_path, start=s0[0] - 0.2, end=s0[1] + 0.3, words=talk_words,
                            name="nocap.mp4", captions=False), hw=hw)
    assert a.validation["ok"] and b.validation["ok"]
    assert a.settings["captions"]["words"] == len(s0[2].split())
    t = 1.0
    fa, fb = frame_gray(a.path, t, 360, 640), frame_gray(b.path, t, 360, 640)
    y = int(a.settings["captions"]["center_y"] * 640)
    band = slice(y - 40, y + 40)
    assert np.abs(fa[band] - fb[band]).mean() > 8, "captions not visible in the caption band"
    outside = slice(0, 120)
    assert np.abs(fa[outside] - fb[outside]).mean() < 2, "something other than captions changed"
    # Source untouched; outputs never overwrite each other.
    assert probe(src).duration_s == pytest.approx(media["talk"]["duration"], abs=0.01)
    c = render_clip(req_for(src, tmp_path, words=talk_words, name="cap.mp4"), hw=hw)
    assert c.path.name == "cap (2).mp4" and a.path.exists()


@pytest.mark.parametrize("layout", [
    {"kind": "crop"}, {"kind": "crop", "zoom": 1.5}, {"kind": "fit_blur"}, {"kind": "split_stack"},
    {"kind": "content_facecam", "content_mode": "fit"},
    {"kind": "content_facecam", "content_mode": "crop", "facecam_position": "bottom"},
], ids=lambda d: "-".join(str(v) for v in d.values()))
def test_every_layout_renders_valid_vertical_output(media, tmp_path, hw, layout):
    out = render_clip(req_for(media["talk"]["video"], tmp_path, layout=layout, captions=False), hw=hw)
    v = out.validation
    assert (v["width"], v["height"]) == (360, 640) and v["video_codec"] == "h264" and v["audio_codec"] == "aac"
    assert v["duration_s"] == pytest.approx(4.0, abs=0.1)


def test_full_hd_vertical_output(media, tmp_path, hw, talk_words):
    out = render_clip(req_for(media["talk"]["video"], tmp_path, out=(1080, 1920), words=talk_words), hw=hw)
    assert (out.validation["width"], out.validation["height"]) == (1080, 1920)
    assert out.settings["argv"][0] == "ffmpeg" and "-c:v" in out.settings["argv"]


def test_face_crop_math():
    from clipforge.profiles.schema import Layout
    face = FaceSummary(center_x=0.8, center_y=0.4, box=(0.75, 0.3, 0.1, 0.2), faces_per_frame=1, frames=5)
    plan = plan_layout(Layout(kind="crop"), 1920, 1080, 1080, 1920, face)
    cw, ch, x, y = plan.details["crop"]
    assert (cw, ch) == (608, 1080) and x + cw <= 1920
    assert x <= 0.8 * 1920 <= x + cw          # face inside the crop
    fx, fy, fw, fh = plan.face_box_out
    assert 0 <= fx <= 1 and 0 <= fy <= 1      # face box mapped into output coords
    centred = plan_layout(Layout(kind="crop"), 1920, 1080, 1080, 1920, None)
    assert centred.details["crop"][2] == (1920 - 608) // 2


# --------------------------------------------------------------------------- 8. A/V sync
def _onsets(signal: np.ndarray, rate: float, thresh: float) -> list[float]:
    above = signal > thresh
    return [i / rate for i in range(1, len(above)) if above[i] and not above[i - 1]]


def test_audio_video_stay_in_sync(media, tmp_path, hw):
    out = render_clip(req_for(media["flash"], tmp_path, start=0.5, end=8.5, profile="vlog",
                              layout={"kind": "fit_blur"}, captions=False), hw=hw)
    fps = out.settings["output"]["fps"]
    v = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out.path), "-vf", "scale=16:16,format=gray",
                        "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    luma = np.frombuffer(v, dtype=np.uint8).reshape(-1, 256).mean(axis=1)
    a = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out.path), "-ac", "1", "-ar", "48000", "-f", "s16le", "-"],
                       capture_output=True, check=True).stdout
    pcm = np.frombuffer(a, dtype=np.int16).astype(np.float32) / 32768
    win = 240  # 5 ms
    rms = np.sqrt((pcm[: len(pcm) // win * win].reshape(-1, win) ** 2).mean(axis=1))
    vid_on = _onsets(luma, fps, (luma.max() + luma.min()) / 2)
    aud_on = _onsets(rms, 48000 / win, 0.2)
    expected = [0.5, 2.5, 4.5, 6.5]
    assert len(vid_on) == len(aud_on) == 4, (vid_on, aud_on)
    frame = 1 / fps
    for e, tv, ta in zip(expected, vid_on, aud_on):
        assert abs(tv - e) <= frame + 0.005
        assert abs(ta - e) <= 0.02
        assert abs(tv - ta) <= frame + 0.02, f"A/V offset {tv - ta:+.3f}s"


# --------------------------------------------------------------------------- 9. CPU fallback
def test_hardware_encoder_failure_falls_back_to_cpu(media, tmp_path, hw, monkeypatch):
    monkeypatch.setattr(renderer_mod, "choose_encoder", lambda report, pref: "h264_doesnotexist")
    out = render_clip(req_for(media["talk"]["video"], tmp_path, captions=False), hw=hw)
    assert out.encoder == "libx264"
    assert any("fell back to CPU" in w for w in out.warnings)
    assert out.validation["ok"]


def test_cpu_preference_forces_libx264(media, tmp_path):
    fake = hwaccel.HardwareReport(ffmpeg="ffmpeg", encoders=[
        hwaccel.EncoderCheck("h264_nvenc", True, True), hwaccel.EncoderCheck("libx264", True, True)])
    assert hwaccel.choose_encoder(fake, "cpu") == "libx264"
    assert hwaccel.choose_encoder(fake, "auto") == "h264_nvenc"
    assert hwaccel.choose_encoder(fake, "h264_qsv") == "h264_nvenc"  # unusable preference → best usable


# --------------------------------------------------------------------------- 10. hw detection
def test_hardware_detection_verifies_with_real_encode(hw):
    names = {e.name: e for e in hw.encoders}
    assert names["libx264"].compiled and names["libx264"].works
    for e in hw.encoders:
        assert e.compiled or not e.works         # can't work if not compiled
        assert e.detail                          # every verdict is explained
    assert "libx264" in hw.usable_h264
    ok, detail = hwaccel.test_encoder("h264_doesnotexist")
    assert not ok and detail
    assert isinstance(hw.cuda_devices, int) and hw.cuda_detail


def test_hardware_detection_without_ffmpeg():
    r = hwaccel.detect("/nonexistent/ffmpeg")
    assert r.ffmpeg is None and r.usable_h264 == []
    assert hwaccel.choose_encoder(r) == "libx264"


# --------------------------------------------------------------------------- 11. validation
def test_output_validation_catches_bad_files(media, tmp_path, hw):
    good = render_clip(req_for(media["talk"]["video"], tmp_path, captions=False), hw=hw).path
    kwargs = dict(expected_w=360, expected_h=640, expected_dur=4.0, fps=30, ffprobe="ffprobe", ffmpeg="ffmpeg",
                  deep=True)
    assert validate_output(good, **kwargs)["ok"]
    with pytest.raises(RenderFailed, match="size"):
        validate_output(good, **{**kwargs, "expected_w": 1080, "expected_h": 1920})
    with pytest.raises(RenderFailed, match="duration"):
        validate_output(good, **{**kwargs, "expected_dur": 9.0})
    bad = tmp_path / "trunc.mp4"
    data = good.read_bytes()
    bad.write_bytes(data[: int(len(data) * 0.6)])  # faststart → moov is first; mdat truncated
    with pytest.raises(Exception):
        validate_output(bad, **kwargs)
    with pytest.raises(RenderFailed):
        validate_output(tmp_path / "nothing.mp4", **kwargs)


def test_render_failure_keeps_previous_outputs(media, tmp_path, hw, monkeypatch):
    first = render_clip(req_for(media["talk"]["video"], tmp_path, captions=False), hw=hw)
    size = first.path.stat().st_size

    def broken(*a, **k):
        raise RenderFailed("simulated validation failure", code="OUTPUT_INVALID")
    monkeypatch.setattr(renderer_mod, "validate_output", broken)
    with pytest.raises(RenderFailed):
        render_clip(req_for(media["talk"]["video"], tmp_path, captions=False), hw=hw)
    assert first.path.stat().st_size == size
    out_dir = tmp_path / "out"
    assert sorted(p.name for p in out_dir.glob("*.mp4")) == ["clip.mp4"]  # no half-written file
    assert not any((out_dir / ".tmp").iterdir())                         # work dir cleaned


def test_render_cancel_kills_ffmpeg_and_cleans_up(media, tmp_path, hw):
    calls = {"n": 0}

    def prog(f):
        calls["n"] += 1
        if f > 0.05:
            raise JobCancelled()
    with pytest.raises(JobCancelled):
        render_clip(req_for(media["talk"]["video"], tmp_path, start=0, end=40, out=(1080, 1920)), hw=hw,
                    on_progress=prog)
    assert calls["n"] >= 1
    assert not list((tmp_path / "out").glob("*.mp4"))
    assert not any((tmp_path / "out" / ".tmp").iterdir())


def test_missing_source_render(tmp_path, media, hw):
    src = tmp_path / "gone.mp4"
    shutil.copy(media["silent"], src)
    req = req_for(src, tmp_path)
    src.unlink()
    with pytest.raises(SourceMissing):
        render_clip(req, hw=hw)


def test_caption_style_override_is_applied(media, tmp_path, hw, talk_words):
    style = CaptionStyle(font_size=120, highlight_color="#FF0000", uppercase=True)
    out = render_clip(req_for(media["talk"]["video"], tmp_path, words=talk_words, style=style), hw=hw)
    assert out.settings["captions"]["style"]["highlight_color"] == "#FF0000"
    assert out.settings["captions"]["style"]["uppercase"] is True
