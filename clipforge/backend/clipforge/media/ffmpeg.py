"""Locate and run ffmpeg / ffprobe.

``run_ffmpeg`` streams ``-progress`` output so long renders report progress and can
be cancelled: the callback raises ``JobCancelled`` and we kill the child process.
Output is never buffered whole in memory except stderr's tail (for error messages).
"""

from __future__ import annotations

import collections
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Callable, Sequence

from ..errors import DependencyMissing, JobCancelled

ProgressFn = Callable[[float], None]

# Keep a console window from flashing up for every ffmpeg call on Windows.
_CREATE_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0

INSTALL_HINT = (
    "Install FFmpeg and make sure it is on PATH. On Windows: `winget install Gyan.FFmpeg` "
    "(then open a new terminal), or download a build from https://www.gyan.dev/ffmpeg/builds/ "
    "and set CLIPFORGE_FFMPEG / CLIPFORGE_FFPROBE to the full paths of ffmpeg.exe / ffprobe.exe."
)


def resolve_binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise DependencyMissing(f"'{name}' was not found.", hint=INSTALL_HINT)
    return path


@dataclass
class FFmpegResult:
    returncode: int
    stderr_tail: str


def run_ffmpeg(
    args: Sequence[str],
    *,
    ffmpeg: str = "ffmpeg",
    duration_s: float | None = None,
    on_progress: ProgressFn | None = None,
    cwd: str | None = None,
    timeout_s: float | None = None,
) -> FFmpegResult:
    """Run ffmpeg with ``-progress pipe:1``. ``on_progress`` receives 0..1 and may
    raise ``JobCancelled`` to abort (the process is killed before re-raising)."""
    exe = resolve_binary(ffmpeg)
    cmd = [exe, "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-nostats", *args]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd,
        creationflags=_CREATE_FLAGS, text=True, encoding="utf-8", errors="replace",
    )
    tail: collections.deque[str] = collections.deque(maxlen=40)

    def _drain_stderr():
        assert proc.stderr is not None
        for line in proc.stderr:
            tail.append(line.rstrip())

    t = threading.Thread(target=_drain_stderr, daemon=True)
    t.start()
    timer = None
    if timeout_s:
        timer = threading.Timer(timeout_s, proc.kill)
        timer.start()
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            key, _, value = line.strip().partition("=")
            if on_progress and duration_s and key in ("out_time_us", "out_time_ms"):
                try:
                    us = int(value)
                except ValueError:
                    continue
                on_progress(max(0.0, min(1.0, us / 1_000_000 / duration_s)))
            elif on_progress and key == "progress" and value == "end":
                on_progress(1.0)
        proc.wait()
    except JobCancelled:
        proc.kill()
        proc.wait()
        raise
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    finally:
        if timer:
            timer.cancel()
        t.join(timeout=5)
    return FFmpegResult(proc.returncode, "\n".join(tail))


def ffmpeg_version(ffmpeg: str = "ffmpeg") -> str | None:
    try:
        exe = resolve_binary(ffmpeg)
        out = subprocess.run([exe, "-hide_banner", "-version"], capture_output=True, text=True,
                             timeout=15, creationflags=_CREATE_FLAGS)
        return out.stdout.splitlines()[0] if out.stdout else None
    except Exception:
        return None


def run_capture(cmd: list[str], *, timeout_s: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout_s, creationflags=_CREATE_FLAGS)
