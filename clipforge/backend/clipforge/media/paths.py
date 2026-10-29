"""Path handling: safe names, unique outputs, fingerprints, folder validation.

Strategy for source media (see docs/architecture.md):
* the absolute path is only the *last known location*;
* identity is ``fingerprint`` = size + sha256 of the first and last 1 MiB, cheap even
  for multi-GB files and stable when a file is moved or copied to another drive;
* everything ClipForge writes is stored relative to the data dir.
"""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from pathlib import Path, PurePath

from ..errors import InvalidPath

_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
FINGERPRINT_BLOCK = 1024 * 1024


def slugify(text: str, max_len: int = 48, fallback: str = "untitled") -> str:
    norm = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", norm).strip("-").lower()
    slug = slug[:max_len].rstrip("-")
    return slug or fallback


def safe_filename(name: str, max_len: int = 120, fallback: str = "clip") -> str:
    """Make ``name`` safe as a single path component on Windows, macOS and Linux."""
    name = unicodedata.normalize("NFC", name)
    name = _UNSAFE.sub("_", name)
    name = name.strip().strip(".")
    name = re.sub(r"\s+", " ", name)
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem or not re.fullmatch(r"[A-Za-z0-9]{1,10}", ext):
        stem, ext = name, ""
    if stem.split(".")[0].upper() in _WINDOWS_RESERVED:
        stem = "_" + stem
    budget = max_len - (len(ext) + 1 if ext else 0)
    stem = stem[:budget].rstrip(" .")
    if not stem:
        stem = fallback
    return f"{stem}.{ext}" if ext else stem


def unique_path(directory: Path, filename: str) -> Path:
    """``directory/filename`` or, if taken, ``name (2).ext``, ``name (3).ext`` …"""
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for i in range(2, 10_000):
        c = directory / f"{stem} ({i}){suffix}"
        if not c.exists():
            return c
    raise InvalidPath(f"Could not find a free file name for {filename} in {directory}")


def fingerprint(path: str | Path) -> str:
    p = Path(path)
    size = p.stat().st_size
    h = hashlib.sha256()
    h.update(str(size).encode())
    with p.open("rb") as f:
        h.update(f.read(FINGERPRINT_BLOCK))
        if size > 2 * FINGERPRINT_BLOCK:
            f.seek(-FINGERPRINT_BLOCK, os.SEEK_END)
            h.update(f.read(FINGERPRINT_BLOCK))
    return f"v1:{size}:{h.hexdigest()[:32]}"


def normalize_user_path(raw: str) -> Path:
    """Validate a path typed or pasted by the user."""
    if not isinstance(raw, str) or not raw.strip():
        raise InvalidPath("Path is empty.")
    raw = raw.strip().strip('"').strip("'")
    if "\x00" in raw:
        raise InvalidPath("Path contains a NUL byte.")
    p = Path(os.path.expandvars(os.path.expanduser(raw)))
    if not p.is_absolute():
        raise InvalidPath(f"Please give a full (absolute) path, got: {raw}",
                          hint="On Windows that looks like C:\\Videos\\stream.mp4")
    return Path(os.path.normpath(p))


def ensure_output_dir(raw: str) -> Path:
    """Validate (and create if needed) a user-chosen output folder."""
    p = normalize_user_path(raw)
    if p.exists() and not p.is_dir():
        raise InvalidPath(f"Not a folder: {p}")
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise InvalidPath(f"Can't create folder {p}: {e.strerror or e}")
    probe = p / f".clipforge-write-test-{os.getpid()}"
    try:
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as e:
        raise InvalidPath(f"Folder isn't writable: {p} ({e.strerror or e})",
                          hint="Pick a folder in your user profile, e.g. Videos\\ClipForge.")
    return p


def to_relative(path: Path, root: Path) -> str:
    """Store paths under ``root`` as forward-slash relative strings."""
    rel = Path(path).resolve().relative_to(Path(root).resolve())
    return PurePath(rel).as_posix()


def from_relative(rel: str, root: Path) -> Path:
    p = (Path(root) / Path(rel)).resolve()
    root_r = Path(root).resolve()
    if p != root_r and root_r not in p.parents:
        raise InvalidPath("Stored path escapes the data directory.")
    return p


def format_ts(seconds: float, sep: str = "-") -> str:
    """12.5 → '00-00-12' ; used in file names."""
    s = max(0, int(seconds))
    return f"{s // 3600:02d}{sep}{(s % 3600) // 60:02d}{sep}{s % 60:02d}"
