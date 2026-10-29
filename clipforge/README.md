# ClipForge

Local-first clipping: import a long video (podcast, stream VOD, reaction, commentary,
gaming, vlog), transcribe it **on your machine**, get a ranked review queue of
30–60 s moments with the reasons they were picked, then render vertical 1080×1920
MP4s with animated word-by-word captions. Nothing is uploaded and nothing is
published automatically.

**Working on ClipForge? Read `HANDOFF.md` first** (current state, what's verified,
what's next).

Status: milestone 1 (working vertical slice). See `docs/implementation-plan.md` for
exactly what is done, what's only partly verified, and what's next.

## Install (Windows 10/11)

Prerequisites: Python 3.10+ (3.12 recommended), FFmpeg, and Node.js LTS for the UI
build. The setup script checks each one and tells you the `winget` command if it's
missing.

```bat
git clone https://github.com/goodguyllc773-oss/clipforge.git
cd clipforge
setup.cmd          :: add -Gpu to install CUDA libs for GPU transcription without asking
start.cmd          :: opens http://127.0.0.1:8765/
start-dev.cmd      :: dev servers instead: API :8765 + hot-reload UI :5173
```

`setup.cmd` creates `backend\.venv`, installs the Python packages there, offers the
NVIDIA cuBLAS/cuDNN wheels if it sees an NVIDIA GPU, builds the UI and runs
`clipforge doctor`.

**First transcription downloads the Whisper model** (`small` ≈ 460 MB) from
huggingface.co into `%LOCALAPPDATA%\ClipForge\models`. After that it works offline.

### GPU notes (RTX 2060 Super, 8 GB)
* Transcription: `small` (default) uses ~1 GB VRAM; `medium` or `distil-large-v3`
  (English) are more accurate and still fit. Change in Settings.
* If CUDA libraries are missing, transcription falls back to CPU and the job log
  says so. Fix: `backend\.venv\Scripts\python -m pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"`.
* Rendering uses NVENC only if a real test encode succeeds (Settings → Diagnostics),
  otherwise libx264.

## Install (macOS / Linux, or manual)

```bash
git clone https://github.com/goodguyllc773-oss/clipforge.git && cd clipforge
scripts/setup-mac.sh                # venv, packages, UI build, Claude sync hook, doctor
open start-dev.command              # dev servers (or: npm run dev)
```

## Use

1. **Library** → create a project (pick a default editing profile).
2. **Import video** → browse to the file (or paste its full path). Optionally attach an
   existing `.srt` / `.vtt` to skip Whisper. ClipForge never copies or modifies the
   source.
3. Wait for *Transcribing* → *Finding clips* (progress bars; cancellable).
4. **Review clips**: watch the source range or the render, read *Why this clip*,
   nudge boundaries, change profile / layout / caption style, **Approve** (renders
   automatically) or **Reject**. Keys: `J`/`K` next/prev, `A` approve, `X` reject.
5. **Export** an approved render to a folder: you get the MP4 plus `.json` (source
   timestamps, transcript, reasons, hashtags) and `.txt` (ready-to-paste caption).

Only clip footage you have the rights to edit and post.

## Develop

```bash
# API + worker on :8765 and hot-reload UI on :5173, in one window (Ctrl+C stops both)
npm run dev
# keep GitHub and this machine in step (also runs at every Claude session start)
npm run sync
# tests (needs ffmpeg/ffprobe on PATH; ~2 min)
cd backend && .venv/bin/python -m pytest      # Windows: .venv\Scripts\python -m pytest
# real-Whisper test (needs network the first time):
CLIPFORGE_TEST_WHISPER_MODEL=tiny.en python -m pytest -m whisper
```

Environment variables: `CLIPFORGE_DATA_DIR`, `CLIPFORGE_FFMPEG`, `CLIPFORGE_FFPROBE`,
`CLIPFORGE_TRANSCRIBER` (`faster-whisper` | `sidecar`), `CLIPFORGE_PORT`,
`CLIPFORGE_EMBEDDED_WORKER` (`0` → run `python -m clipforge worker` separately).

Data lives in `%LOCALAPPDATA%\ClipForge` (Windows) / `~/.local/share/clipforge`
(Linux) / `~/Library/Application Support/ClipForge` (macOS): `clipforge.db`, `logs/`,
`models/`, `cache/`, `renders/`.

Docs: `docs/architecture.md` (modules, data model, job state machine),
`docs/decisions.md` (why things are the way they are),
`docs/implementation-plan.md` (status checklist, tests, next milestone).

## Known limitations

* Clip ranking is a transparent **heuristic baseline** (hook / finished thought /
  self-contained start / delivery energy …). It does not understand humour or story
  payoff. A local-LLM re-ranker is the next milestone.
* No speaker diarization; split-screen regions and facecam rectangles are set by hand.
* Face framing is a fixed per-clip crop from OpenCV's frontal-face detector — no
  tracking during the clip, misses profile views.
* SRT/VTT imports have estimated (interpolated) word timings, so the highlight can
  drift slightly within a line.
* Whisper and NVENC paths were not exercised on real hardware in the build
  environment; see "Manual tests" in the plan.
