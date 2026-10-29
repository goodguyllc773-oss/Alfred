"""Hardware capability detection.

An encoder appearing in ``ffmpeg -encoders`` only means ffmpeg was *built* with it.
NVENC still needs an NVIDIA driver, QSV an Intel iGPU (the i5-9400F has none), AMF
an AMD driver. So every hardware encoder is verified with a tiny real encode before
we trust it; results are cached per ffmpeg binary for the life of the process.
"""

from __future__ import annotations

import re
import subprocess
import threading
from dataclasses import asdict, dataclass, field

from .ffmpeg import resolve_binary, run_capture

# Preference order for H.264 output.
HW_H264_ENCODERS = ("h264_nvenc", "h264_qsv", "h264_amf")
CPU_H264_ENCODER = "libx264"

# Encoder-specific quality args (roughly comparable visual quality).
ENCODER_ARGS: dict[str, list[str]] = {
    "libx264": ["-preset", "veryfast", "-crf", "20", "-profile:v", "high", "-pix_fmt", "yuv420p"],
    "h264_nvenc": ["-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "21", "-b:v", "0",
                   "-profile:v", "high", "-pix_fmt", "yuv420p"],
    "h264_qsv": ["-preset", "medium", "-global_quality", "22", "-profile:v", "high", "-pix_fmt", "nv12"],
    "h264_amf": ["-quality", "balanced", "-rc", "cqp", "-qp_i", "21", "-qp_p", "23",
                 "-profile:v", "high", "-pix_fmt", "yuv420p"],
}


@dataclass
class EncoderCheck:
    name: str
    compiled: bool
    works: bool
    detail: str = ""


@dataclass
class HardwareReport:
    ffmpeg: str | None
    encoders: list[EncoderCheck] = field(default_factory=list)
    cuda_devices: int = 0
    cuda_detail: str = ""

    @property
    def usable_h264(self) -> list[str]:
        return [e.name for e in self.encoders if e.works]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["usable_h264"] = self.usable_h264
        return d


_cache: dict[str, HardwareReport] = {}
_lock = threading.Lock()


def compiled_encoders(ffmpeg: str = "ffmpeg") -> set[str]:
    exe = resolve_binary(ffmpeg)
    out = run_capture([exe, "-hide_banner", "-encoders"], timeout_s=30)
    names = set()
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in "VAS":
            names.add(parts[1])
    return names


def test_encoder(name: str, ffmpeg: str = "ffmpeg") -> tuple[bool, str]:
    """Encode 10 frames of black video to the null muxer with ``name``."""
    exe = resolve_binary(ffmpeg)
    cmd = [exe, "-hide_banner", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
           "color=c=black:s=320x240:r=30:d=0.34", "-frames:v", "10",
           "-c:v", name, *ENCODER_ARGS.get(name, []), "-f", "null", "-"]
    try:
        out = run_capture(cmd, timeout_s=30)
    except subprocess.TimeoutExpired:
        return False, "test encode timed out"
    if out.returncode == 0:
        return True, "test encode ok"
    lines = [l for l in (out.stderr or "").splitlines() if l.strip()]
    # The encoder's own first message is the root cause ("Cannot load libcuda.so.1",
    # "Error creating a MFX session"); ffmpeg's trailing lines are generic.
    own = [l for l in lines if f"[{name} @" in l]
    detail = (own or lines or [f"exit code {out.returncode}"])[0]
    return False, re.sub(r"^\[[^\]]+\]\s*", "", detail)[:300]


def cuda_devices() -> tuple[int, str]:
    """CUDA devices visible to CTranslate2 (what faster-whisper uses)."""
    try:
        import ctranslate2  # noqa: PLC0415 - optional heavy import
        n = int(ctranslate2.get_cuda_device_count())
        return n, f"ctranslate2 {ctranslate2.__version__}: {n} CUDA device(s)"
    except Exception as e:  # pragma: no cover - depends on install
        return 0, f"ctranslate2 unavailable: {e}"


def detect(ffmpeg: str = "ffmpeg", *, refresh: bool = False) -> HardwareReport:
    with _lock:
        if not refresh and ffmpeg in _cache:
            return _cache[ffmpeg]
        try:
            exe = resolve_binary(ffmpeg)
        except Exception:
            report = HardwareReport(ffmpeg=None)
            report.cuda_devices, report.cuda_detail = cuda_devices()
            return report
        compiled = compiled_encoders(ffmpeg)
        report = HardwareReport(ffmpeg=exe)
        for name in (*HW_H264_ENCODERS, CPU_H264_ENCODER):
            if name not in compiled:
                report.encoders.append(EncoderCheck(name, False, False, "not compiled into this ffmpeg"))
                continue
            ok, detail = test_encoder(name, ffmpeg)
            report.encoders.append(EncoderCheck(name, True, ok, detail))
        report.cuda_devices, report.cuda_detail = cuda_devices()
        _cache[ffmpeg] = report
        return report


def choose_encoder(report: HardwareReport, preference: str = "auto") -> str:
    """``preference``: 'auto' (best working hw, else libx264), 'cpu', or an encoder name."""
    usable = report.usable_h264
    if preference == "cpu":
        return CPU_H264_ENCODER
    if preference not in ("auto", "") and preference in usable:
        return preference
    for name in HW_H264_ENCODERS:
        if name in usable:
            return name
    return CPU_H264_ENCODER
