"""Test 4: transcription timestamps — sidecar parsing, word-timing repair, the
faster-whisper adapter (with a stand-in model), chunk planning and resume."""

from __future__ import annotations

import os
from types import SimpleNamespace

import numpy as np
import pytest

from clipforge import services
from clipforge.analysis.signals import Loudness, plan_chunks
from clipforge.db.models import Job, Transcript, TranscriptChunk
from clipforge.errors import TranscriptionFailed
from clipforge.transcription import whisper as whisper_mod
from clipforge.transcription.base import ChunkResult, Segment, Word
from clipforge.transcription.sidecar import SidecarTranscriber, parse_srt_vtt
from clipforge.transcription.words import estimate_words, repair_segment


def test_srt_sidecar_sentence_timings_exact(media):
    tr = SidecarTranscriber(media["talk"]["srt"])
    res = tr.transcribe_range(str(media["talk"]["video"]), 0, 1e9)
    expected = media["talk"]["segments"]
    assert len(res.segments) == len(expected)
    for seg, (s, e, text) in zip(res.segments, expected):
        assert seg.start == pytest.approx(s, abs=0.002) and seg.end == pytest.approx(e, abs=0.002)
        assert seg.text == text
        assert " ".join(w.text for w in seg.words) == text  # punctuation preserved on words
        assert all(w.estimated for w in seg.words)          # SRT has no word timing
        assert seg.words[0].start == pytest.approx(s, abs=0.01)
        assert seg.words[-1].end == pytest.approx(e, abs=0.01)


def test_json_sidecar_keeps_real_word_times(media):
    tr = SidecarTranscriber(media["dir"] / "talk_words.json")
    res = tr.transcribe_range("unused.mp4", 0, 1e9)
    w0 = res.segments[0].words[0]
    assert not w0.estimated and res.language == "en"
    starts = [w.start for s in res.segments for w in s.words]
    assert starts == sorted(starts)


def test_vtt_with_tags_and_rolling_duplicates():
    vtt = """WEBVTT

00:00:01.000 --> 00:00:02.500
<c.color>Hello</c> <b>there</b>, friend.

00:00:02.500 --> 00:00:03.000
Hello there, friend.

00:01:02.000 --> 00:01:04.250 align:start
Next {\\i1}line{\\i0} &amp; more
"""
    segs = parse_srt_vtt(vtt)
    assert [s.text for s in segs] == ["Hello there, friend.", "Next line & more"]
    assert segs[0].end == 3.0  # merged duplicate cue
    assert segs[1].start == pytest.approx(62.0)


def test_word_repair_monotonic_and_min_duration():
    seg = Segment(10.0, 12.0, "a b c d", [
        Word("a", 10.0, 10.5), Word("b", 10.4, 10.4), Word("c", float("nan"), 11.0), Word("d", 10.9, 11.5)])
    repair_segment(seg)
    times = [(w.start, w.end) for w in seg.words]
    for (s1, e1), (s2, e2) in zip(times, times[1:]):
        assert e1 <= s2 + 1e-9
    assert all(e - s >= 0.059 for s, e in times)


def test_estimate_words_spreads_by_length():
    ws = estimate_words("I absolutely love it.", 0.0, 4.0)
    assert [w.text for w in ws] == ["I", "absolutely", "love", "it."]
    assert ws[0].start == 0 and ws[-1].end == pytest.approx(4.0)
    assert (ws[1].end - ws[1].start) > (ws[0].end - ws[0].start)


def test_plan_chunks_cuts_at_quiet_points():
    db = [-20.0] * 2400  # 1200 s at 0.5 s windows
    db[1230] = -80.0     # silence at 615 s, near the 600 s target
    chunks = plan_chunks(1200, 600, Loudness(0.5, db))
    assert len(chunks) == 2
    assert chunks[0][1] == pytest.approx(615.25, abs=0.01)
    assert chunks[-1][1] == 1200 and chunks[0][0] == 0


# --------------------------------------------------------------------------- faster-whisper adapter
class FakeWhisperModel:
    calls: list = []
    fail_cuda = False

    def __init__(self, size, device, compute_type, download_root=None):
        if device == "cuda" and FakeWhisperModel.fail_cuda:
            raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
        self.device = device
        FakeWhisperModel.calls.append(("load", size, device, compute_type))

    def transcribe(self, audio, **kw):
        assert kw["word_timestamps"] is True
        assert audio.dtype == np.float32
        dur = len(audio) / 16000
        W = SimpleNamespace

        def gen():
            yield W(start=0.5, end=2.0, text=" Hello world.", avg_logprob=-0.2, no_speech_prob=0.01,
                    words=[W(word=" Hello", start=0.5, end=1.0, probability=0.9),
                           W(word=" world.", start=1.1, end=2.0, probability=0.8)])
            yield W(start=2.5, end=min(dur, 4.0), text=" No words here", avg_logprob=-0.5,
                    no_speech_prob=0.1, words=[])
        return gen(), SimpleNamespace(language="en")


@pytest.fixture
def fake_whisper(monkeypatch):
    import faster_whisper
    FakeWhisperModel.calls = []
    FakeWhisperModel.fail_cuda = False
    monkeypatch.setattr(faster_whisper, "WhisperModel", FakeWhisperModel)
    return FakeWhisperModel


def test_whisper_adapter_offsets_words(fake_whisper, media):
    tr = whisper_mod.FasterWhisperTranscriber("small", device="cpu", cuda_available=0)
    res = tr.transcribe_range(str(media["talk"]["video"]), 30.0, 40.0)
    s0, s1 = res.segments
    assert (s0.start, s0.end) == (30.5, 32.0)            # chunk offset applied
    assert [(w.text, w.start, w.end) for w in s0.words] == [("Hello", 30.5, 31.0), ("world.", 31.1, 32.0)]
    assert s1.words and all(w.estimated for w in s1.words)  # missing word times filled in
    assert res.language == "en" and res.device == "cpu"


def test_whisper_cuda_failure_falls_back_to_cpu(fake_whisper, media):
    fake_whisper.fail_cuda = True
    tr = whisper_mod.FasterWhisperTranscriber("small", device="auto", cuda_available=1)
    res = tr.transcribe_range(str(media["talk"]["video"]), 0, 5)
    assert res.device == "cpu"
    assert any("using CPU" in w for w in res.warnings)
    assert [c[2] for c in fake_whisper.calls] == ["cpu"]


def test_whisper_download_error_is_actionable(monkeypatch, media):
    import faster_whisper

    class Boom:
        def __init__(self, *a, **k):
            raise ConnectionError("Max retries exceeded with url: huggingface.co (Caused by ProxyError)")
    monkeypatch.setattr(faster_whisper, "WhisperModel", Boom)
    tr = whisper_mod.FasterWhisperTranscriber("small", device="cpu")
    with pytest.raises(TranscriptionFailed) as e:
        tr.transcribe_range(str(media["talk"]["video"]), 0, 5)
    assert e.value.code == "MODEL_DOWNLOAD_FAILED" and e.value.retryable and "huggingface" in e.value.hint


def test_read_pcm_is_bounded_to_range(media):
    pcm = whisper_mod.read_pcm(str(media["talk"]["video"]), 10.0, 13.0)
    assert pcm.dtype == np.float32 and abs(len(pcm) - 3 * 16000) <= 160
    assert np.abs(pcm).max() <= 1.0


# --------------------------------------------------------------------------- resume
class FlakyTranscriber:
    """Fails on chunk 1 the first time, to simulate a crash mid-transcription."""
    name = "flaky"

    def __init__(self, inner, state):
        self.inner, self.state = inner, state

    def describe(self):
        return {"engine": "flaky", "model": "test", "device": "cpu"}

    def transcribe_range(self, source, start_s, end_s, *, on_progress=None):
        self.state["calls"].append(start_s)
        if start_s > 0 and not self.state["failed"]:
            self.state["failed"] = True
            raise TranscriptionFailed("simulated crash", retryable=True)
        return self.inner.transcribe_range(source, start_s, end_s, on_progress=on_progress)

    def release(self):
        pass


def test_transcription_resumes_from_committed_chunks(ctx, worker, talk_copy, monkeypatch):
    import clipforge.jobs.queue as q
    monkeypatch.setattr(q, "RETRY_BASE_S", 0.05)
    object.__setattr__(ctx.settings, "transcribe_chunk_s", 30.0)  # force several chunks
    state = {"calls": [], "failed": False}
    ctx.transcriber_factory = lambda app, m: FlakyTranscriber(SidecarTranscriber(talk_copy.with_suffix(".srt")), state)
    p = services.create_project(ctx, "resume")
    m, jobs = services.import_media(ctx, p.id, str(talk_copy))
    assert worker.wait_idle(90)
    with ctx.session() as s:
        job = s.get(Job, jobs[0])
        assert job.status == "completed" and job.attempts == 2
        t = s.query(Transcript).filter_by(media_id=m.id).one()
        chunks = s.query(TranscriptChunk).filter_by(transcript_id=t.id).all()
        assert t.status == "complete" and len(chunks) >= 2 and all(c.status == "done" for c in chunks)
        assert len(t.segments) == len(talk_copy.with_suffix(".srt").read_text().strip().split("\n\n"))
    # chunk 0 transcribed once only: the retry resumed at chunk 1
    assert state["calls"].count(0.0) == 1
    assert state["calls"].count(chunks[1].start_s) == 2


@pytest.mark.whisper
@pytest.mark.skipif(not os.environ.get("CLIPFORGE_TEST_WHISPER_MODEL"),
                    reason="set CLIPFORGE_TEST_WHISPER_MODEL (e.g. tiny.en) with a downloaded model")
def test_real_whisper_transcribes_synthetic_speech(media):
    tr = whisper_mod.FasterWhisperTranscriber(os.environ["CLIPFORGE_TEST_WHISPER_MODEL"], device="auto")
    res = tr.transcribe_range(str(media["talk"]["video"]), 0, 20)
    text = " ".join(s.text for s in res.segments).lower()
    assert "viral" in text or "videos" in text
    words = [w for s in res.segments for w in s.words]
    assert words and all(b.start >= a.start for a, b in zip(words, words[1:]))
    # Whisper's sentence starts should land near the true (constructed) ones.
    true_start = media["talk"]["segments"][1][0]
    assert any(abs(s.start - true_start) < 1.0 for s in res.segments)
    assert isinstance(res, ChunkResult)
