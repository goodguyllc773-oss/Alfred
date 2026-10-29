"""faster-whisper engine.

* Audio for one chunk is decoded by ffmpeg straight to 16 kHz mono PCM over a pipe
  (≈ 1.9 MB per minute), so RAM is bounded by the chunk length, not the video.
* Device ``auto`` → CUDA when CTranslate2 sees a GPU (``int8_float16``: ~1 GB VRAM
  for ``small``, ~2 GB for ``medium``), else CPU ``int8``. If CUDA fails to load
  (missing cuBLAS/cuDNN DLLs is the classic Windows failure) we fall back to CPU and
  say so in the job log instead of failing the job.
* The model is released after each job so VRAM is free for NVENC rendering.
"""

from __future__ import annotations

import gc
import os
import site
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np

from ..errors import DependencyMissing, TranscriptionFailed
from ..media.ffmpeg import _CREATE_FLAGS, resolve_binary
from .base import ChunkResult, Segment, Word
from .words import repair_segment

MODEL_SIZES = ("tiny", "tiny.en", "base", "base.en", "small", "small.en", "medium", "medium.en",
               "large-v3", "large-v3-turbo", "distil-large-v3")
SAMPLE_RATE = 16000

_CUDA_HINT = (
    "CUDA libraries for faster-whisper weren't found. Either install them into the venv with "
    "`pip install nvidia-cublas-cu12 nvidia-cudnn-cu12==9.*` (see README → GPU), or set "
    "Settings → Transcription device to CPU."
)
_DOWNLOAD_HINT = (
    "The Whisper model is downloaded once from huggingface.co into the ClipForge models folder. "
    "Check your internet connection / proxy, or copy a CTranslate2 Whisper model folder there "
    "and set the model name to its path."
)


_dll_dirs_added = False


def ensure_cuda_dll_dirs() -> list[str]:
    """Windows only: make pip-installed NVIDIA DLLs (``nvidia-cublas-cu12``,
    ``nvidia-cudnn-cu12``) loadable. They live in ``site-packages/nvidia/*/bin``,
    which isn't on the DLL search path by default."""
    global _dll_dirs_added
    if sys.platform != "win32" or _dll_dirs_added:
        return []
    _dll_dirs_added = True
    added = []
    for base in {Path(p) for p in site.getsitepackages() + [site.getusersitepackages()]}:
        for bin_dir in (base / "nvidia").glob("*/bin"):
            if bin_dir.is_dir():
                try:
                    os.add_dll_directory(str(bin_dir))
                except OSError:
                    continue
                os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
                added.append(str(bin_dir))
    return added


def read_pcm(source: str, start_s: float, end_s: float, ffmpeg: str = "ffmpeg") -> np.ndarray:
    exe = resolve_binary(ffmpeg)
    cmd = [exe, "-hide_banner", "-nostdin", "-v", "error", "-ss", f"{start_s:.3f}",
           "-t", f"{max(0.0, end_s - start_s):.3f}", "-i", source, "-vn", "-map", "0:a:0",
           "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"]
    out = subprocess.run(cmd, capture_output=True, creationflags=_CREATE_FLAGS)
    if out.returncode != 0:
        err = out.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["unknown error"]
        raise TranscriptionFailed(f"Couldn't decode audio: {err[0]}", code="AUDIO_DECODE_FAILED",
                                  retryable=False)
    return np.frombuffer(out.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def _is_cuda_error(e: BaseException) -> bool:
    msg = str(e).lower()
    return any(k in msg for k in ("cuda", "cudnn", "cublas", "libcudart", "gpu", "cudart"))


def _is_download_error(e: BaseException) -> bool:
    name = type(e).__name__.lower()
    msg = str(e).lower()
    return any(k in name for k in ("http", "connection", "entrynotfound", "hfhub", "timeout")) or \
        any(k in msg for k in ("huggingface", "connection", "max retries", "proxy", "timed out"))


class FasterWhisperTranscriber:
    name = "faster-whisper"
    _model_lock = threading.Lock()

    def __init__(self, model_size: str = "small", device: str = "auto", compute_type: str = "auto",
                 download_root: str | None = None, language: str | None = None,
                 beam_size: int = 5, vad_filter: bool = True, ffmpeg: str = "ffmpeg",
                 cuda_available: int | None = None):
        self.model_size = model_size
        self.device_pref = device
        self.compute_type_pref = compute_type
        self.download_root = download_root
        self.language = language or None
        self.beam_size = beam_size
        self.vad_filter = vad_filter
        self.ffmpeg = ffmpeg
        self._cuda_available = cuda_available
        self._model = None
        self.device = ""
        self.compute_type = ""
        self.warnings: list[str] = []

    # ------------------------------------------------------------------ model
    def _targets(self) -> list[tuple[str, str]]:
        if self.device_pref == "cpu":
            return [("cpu", self.compute_type_pref if self.compute_type_pref != "auto" else "int8")]
        cuda_n = self._cuda_available
        if cuda_n is None:
            from ..media.hwaccel import cuda_devices
            cuda_n = cuda_devices()[0]
        gpu = ("cuda", self.compute_type_pref if self.compute_type_pref != "auto" else "int8_float16")
        cpu = ("cpu", "int8")
        if self.device_pref == "cuda":
            return [gpu]
        return [gpu, cpu] if cuda_n > 0 else [cpu]

    def _load(self):
        if self._model is not None:
            return self._model
        ensure_cuda_dll_dirs()
        try:
            from faster_whisper import WhisperModel  # noqa: PLC0415
        except ImportError as e:
            raise DependencyMissing(f"faster-whisper is not installed: {e}",
                                    hint="Run the setup script, or `pip install faster-whisper`.")
        last: BaseException | None = None
        for device, compute in self._targets():
            try:
                with self._model_lock:
                    self._model = WhisperModel(self.model_size, device=device, compute_type=compute,
                                               download_root=self.download_root)
                self.device, self.compute_type = device, compute
                return self._model
            except Exception as e:  # noqa: BLE001 - mapped below
                last = e
                if _is_download_error(e):
                    raise TranscriptionFailed(
                        f"Couldn't download Whisper model '{self.model_size}': {e}",
                        code="MODEL_DOWNLOAD_FAILED", hint=_DOWNLOAD_HINT, retryable=True)
                if device == "cuda" and _is_cuda_error(e):
                    self.warnings.append(f"GPU transcription unavailable ({e}); using CPU.")
                    continue
                raise TranscriptionFailed(f"Couldn't load Whisper model '{self.model_size}': {e}",
                                          code="MODEL_LOAD_FAILED", retryable=False,
                                          hint=_CUDA_HINT if _is_cuda_error(e) else None)
        raise TranscriptionFailed(f"Couldn't load Whisper on any device: {last}",
                                  code="MODEL_LOAD_FAILED", hint=_CUDA_HINT, retryable=False)

    def describe(self) -> dict:
        return {"engine": self.name, "model": self.model_size,
                "device": self.device or self.device_pref, "compute_type": self.compute_type,
                "language": self.language, "beam_size": self.beam_size, "vad_filter": self.vad_filter}

    def release(self) -> None:
        self._model = None
        gc.collect()

    # ------------------------------------------------------------------ inference
    def _run(self, audio: np.ndarray, offset: float, on_progress) -> tuple[list[Segment], str | None]:
        model = self._load()
        seg_iter, info = model.transcribe(
            audio, language=self.language, beam_size=self.beam_size, word_timestamps=True,
            vad_filter=self.vad_filter, condition_on_previous_text=False,
        )
        out: list[Segment] = []
        for s in seg_iter:  # lazy generator: inference happens while iterating
            words = [Word(w.word.strip(), offset + w.start, offset + w.end, w.probability)
                     for w in (s.words or []) if w.word.strip()]
            out.append(repair_segment(Segment(offset + s.start, offset + s.end, s.text.strip(), words,
                                              s.avg_logprob, s.no_speech_prob)))
            if on_progress:
                on_progress(offset + s.end)
        return out, getattr(info, "language", None)

    def transcribe_range(self, source, start_s, end_s, *, on_progress=None) -> ChunkResult:
        audio = read_pcm(source, start_s, end_s, self.ffmpeg)
        if audio.size < SAMPLE_RATE // 10:
            return ChunkResult([], self.language, self.device or self.device_pref)
        self.warnings = []
        try:
            segs, lang = self._run(audio, start_s, on_progress)
        except TranscriptionFailed:
            raise
        except Exception as e:  # noqa: BLE001
            # cuDNN DLLs are loaded lazily on first inference, so a CUDA failure can
            # surface here rather than at load time. Retry the chunk on CPU once.
            if self.device == "cuda" and _is_cuda_error(e) and self.device_pref == "auto":
                self.warnings.append(f"GPU inference failed ({e}); retrying on CPU.")
                self._model = None
                self.device_pref = "cpu"
                segs, lang = self._run(audio, start_s, on_progress)
            else:
                raise TranscriptionFailed(f"Whisper inference failed: {e}", code="INFERENCE_FAILED",
                                          hint=_CUDA_HINT if _is_cuda_error(e) else None)
        return ChunkResult(segs, lang, self.device, list(self.warnings))
