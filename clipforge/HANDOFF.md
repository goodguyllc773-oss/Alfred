# Handoff (ClipForge)

Read this first, every time you work on ClipForge, before touching anything else.
Update it before you stop, and immediately before any context compaction (see
CLAUDE.md, "ClipForge: HANDOFF.md", for the exact rule).

_Last updated 2026-10-29 (cloud session, Linux container, no GPU). **Where we left
off:** milestone 1, the first working vertical slice, is built, tested and pushed:
import → transcribe → candidate clips → review → render → export. It's all on the
branch `claude/clipforge-local-first-ai-ojxjpr`, **not merged to `main`**, and **no
PR is open**. It has **never run on the Windows PC**: that first run (section
"First run on the Windows PC") is the next job. Nothing is half-done in the code;
what's unverified is listed under "Not verified yet"._

## Step zero, every session: fetch before trusting this file

**Before reading anything else, run `git fetch origin` and compare local HEAD to
the remote branch.** This repo is worked on from several machines (Windows PC, Mac,
cloud sessions). A stale checkout means a stale HANDOFF.

- ClipForge currently lives **only** on `claude/clipforge-local-first-ai-ojxjpr`.
  `main` has no `clipforge/` folder yet. On a machine that's on `main`:
  `git fetch origin && git switch claude/clipforge-local-first-ai-ojxjpr`.
- If local is behind: `git pull --ff-only` *before* reading the rest of this file.
- If this file says something different from `git log`, trust `git log` and fix
  this file.

## Current state

- **Branch:** `claude/clipforge-local-first-ai-ojxjpr`, pushed and tracking origin.
  - Last code commit: `7595bef` "clipforge: local-first AI clipping app —
    milestone 1 vertical slice". This handoff is committed on top of it; see
    `git log -1`.
  - Not merged, no PR. Ask the user before opening one or merging into `main`.
- **Where it has run:** only the 2026-10-29 cloud session:
  - Linux, Python 3.13, Node 22, FFmpeg 6.1.1, 4 CPUs, 15 GB RAM, **no GPU**.
  - **Windows PC: never set up. Mac: never set up.**
- **Running servers:** none. The cloud session's server and data dir were
  temporary and are gone; nothing carries over from them.
- **Data:** none worth keeping exists anywhere yet. A fresh data dir is created on
  first start:
  - Windows: `%LOCALAPPDATA%\ClipForge`
  - Mac: `~/Library/Application Support/ClipForge`
  - Linux: `~/.local/share/clipforge`
  - Override with `CLIPFORGE_DATA_DIR`.
  - It holds `clipforge.db` (projects, transcripts, clips, jobs), `renders/`,
    `models/` (Whisper downloads), `cache/` and `logs/`. **Not in git.**
- **Database:** one Alembic migration, `0001_initial_schema`, applied
  automatically on start. `alembic check` reported no drift from the models.
- **Per-machine setup (git doesn't carry it):**
  - `clipforge/backend/.venv` (Python packages)
  - `clipforge/frontend/node_modules` and `clipforge/frontend/dist` (built UI)
  - the Whisper model in `models/`
  - `setup.cmd` (Windows) creates all of them. After any pull that changes
    `pyproject.toml` or `package-lock.json`, re-run it (it is safe to re-run).
- **Gotcha #1:** the UI is served by the backend from `frontend/dist`. After
  changing anything in `frontend/src`, run `npm run build` in `frontend/` (or use
  `npm run dev` on :5173 for hot reload). Otherwise :8765 serves the old UI.
- **Gotcha #2:** `CLIPFORGE_TRANSCRIBER=sidecar` makes the worker read
  `.srt/.vtt/.json` files next to the video instead of running Whisper. That's how
  the cloud session tested without a model. The default is `faster-whisper`.
- **Gotcha #3:** `pkill -f "clipforge serve …"` run from a Bash tool call matches
  its own shell and kills it (exit 144). Stop the server with Ctrl+C in its window,
  or kill by PID.
- **Gotcha #4:** in Playwright, `has-text("Approve")` matches case-insensitively,
  so it also hits the "approved (0)" filter tab. Use the exact `✓ Approve` text.
- **Gotcha #5:** huggingface.co was blocked by the cloud session's network policy,
  so cloud sessions can't download Whisper models unless the environment's Network
  access is changed. PyPI and npm work.
- **Gotcha #6:** schema changes: edit `db/models.py`, then from `backend/` run
  `alembic -c alembic.ini upgrade head` and then
  `alembic -c alembic.ini revision --autogenerate -m "..."`. Autogenerating
  against an empty DB re-emits the whole schema instead of a diff.

## First run on the Windows PC: the runbook

This has **never been done**. Do it in order and record what you actually see here.

1. **Get the code:** in the Alfred clone,
   `git fetch origin && git switch claude/clipforge-local-first-ai-ojxjpr && git pull --ff-only`.
2. **Prerequisites** (`setup.cmd` checks each one and prints the winget command):
   - Python 3.10+ (3.12 recommended): `winget install Python.Python.3.12`
   - FFmpeg: `winget install Gyan.FFmpeg`. Open a new terminal afterwards so PATH
     updates.
   - Node.js LTS: `winget install OpenJS.NodeJS.LTS`
3. **`cd clipforge` then `setup.cmd -Gpu`.**
   - `-Gpu` installs `nvidia-cublas-cu12` + `nvidia-cudnn-cu12==9.*` into the venv
     without asking.
   - It creates `backend\.venv`, runs `pip install -e backend[dev]`, builds the UI
     and runs `clipforge doctor`.
   - ⚠ **The PowerShell scripts have never been executed**: the cloud session had
     no PowerShell. Expect to fix small script errors on this first run.
4. **Check `doctor` output:**
   - `h264_nvenc` should say **OK** (in the cloud it said "Cannot load
     libcuda.so.1", which was expected with no GPU).
   - `CUDA` should say **1 CUDA device(s)**.
   - If CUDA shows 0 while `nvidia-smi` works, the DLL search-path helper
     (`transcription/whisper.py` → `ensure_cuda_dll_dirs`, never run on Windows) is
     the first suspect.
5. **Run the tests:** `backend\.venv\Scripts\python -m pytest` from
   `clipforge\backend`. Expect 101 passed, 1 skipped. Then the real-Whisper test:
   `set CLIPFORGE_TEST_WHISPER_MODEL=tiny.en` and
   `backend\.venv\Scripts\python -m pytest -m whisper`. The first run downloads
   `tiny.en` from huggingface.co.
6. **`start.cmd`**: it should open http://127.0.0.1:8765/. Settings →
   Diagnostics should show NVENC ✓ and the CUDA device.
7. **A real end-to-end:**
   - Make a project and import a real long video the user has rights to.
   - The first transcription downloads `small` (≈460 MB).
   - Record: transcription time per hour of audio, which device the transcript
     shows (`cuda` vs `cpu`), and the render encoder (`h264_nvenc`?).
   - Approve a clip, play it, export it, open the exported MP4.
8. **Robustness checks:**
   - Close the server mid-transcription and mid-render, start again, and confirm
     the transcription resumes and the render re-runs.
   - Unplug or rename a source file: it should show "missing" and relink should
     work.
   - Paths with spaces and non-ASCII names.
9. Update this file ("Current state" → where it has run, plus "Last verified
   working"), then commit and push.

## Last verified working (2026-10-29, cloud session)

- **Automated tests:** `python -m pytest` in `clipforge/backend` gave **101
  passed, 1 skipped** in 103 s.
  - The skip is `test_real_whisper_transcribes_synthetic_speech`, which needs a
    downloaded model.
  - Test media is generated by ffmpeg at test time (test patterns + `flite`
    synthetic speech, with an SRT timed to the exact sentence edges). No
    third-party footage.
  - `docs/implementation-plan.md` maps each of the 17 required test areas to its
    tests.
- **Full workflow in a real browser** (headless Chromium through Playwright
  against `python -m clipforge serve` with `CLIPFORGE_TRANSCRIBER=sidecar`):
  - Created "Demo Podcast" (podcast profile) and imported an 80 s synthetic-speech
    video with its `.srt`.
  - Transcribe and analyze both completed; 2 clips were found:
    - #1 scored 72, 0:00–0:44. Its reasons: question followed by a 32 s answer;
      ends on a finished sentence + 1.9 s pause; opens with a question.
    - #2 scored 53, 0:47–1:20.
  - Approve → a render job ran automatically → the "Rendered v1" player showed the
    1080×1920 clip with yellow word-highlight captions. Encoder: libx264.
  - Export wrote `.mp4` + `.json` + `.txt` into the chosen folder; the "Copy
    caption" box appeared.
  - Jobs page and Settings/Diagnostics rendered. **0 console errors.**
- **A/V sync was measured, not assumed:** a flash + beep every 2 s, cut from 0.5 s.
  The flash onsets were exactly [0.5, 2.5, 4.5, 6.5] s on the video frames, and the
  beep onsets matched within the 5 ms measuring window.
- **Hardware detection on a no-GPU machine:** NVENC and QSV are compiled into
  ffmpeg but correctly reported as not working ("Cannot load libcuda.so.1", "Error
  creating a MFX session: -9"), so renders fell back to libx264.
- **Crash-resume:** a test transcriber made chunk 1 fail; the retry skipped
  committed chunk 0 and finished (attempts = 2).
- Bugs found and fixed while testing (all covered by tests now):
  - a clip end could round 0.3 ms past the video's end;
  - `safe_filename` treated `_etc_passwd` as a file extension;
  - the review player cut off the bottom of 9:16 clips (the captions).

## Decisions (made by Claude, NOT asked or confirmed by the user)

All recorded with reasons in `docs/decisions.md` (D1–D12). Ask before relying on
the big ones as if the user had agreed:

- ClipForge lives in the Alfred repo as `clipforge/`, not its own repo.
- The UI is a localhost web page for now; Tauri was deferred.
- A SQLite job queue (no Redis/Celery).
- Default Whisper model `small`.
- Approving a clip auto-renders it; export needs approval.
- Silent videos get scene-based "weak" candidates and a silent audio track.

## Not verified yet / known limits

1. **Real Whisper never ran.** Only a stand-in model was used (models couldn't be
   downloaded). Covered by runbook steps 5 and 7.
2. **NVENC never encoded:** only its detection and fallback logic were tested.
   Runbook steps 4 and 7.
3. **Windows scripts never executed** (`setup.cmd`, `start.cmd`, both `.ps1`
   files). Neither was `ensure_cuda_dll_dirs`.
4. **Face framing is untested on real faces:** the synthetic videos have none.
   Only the crop math is tested.
5. **Clip ranking is a heuristic baseline (`heuristic-v1`).** It scores structure
   (hook, finished thought, energy…), not meaning. The UI says so.
6. No speaker detection. Facecam and split-screen regions are set by hand.
7. SRT/VTT imports have estimated word timings, so the highlight can drift within
   a line.
8. **No committed UI test:** the browser walkthrough was a one-off script.

## What's next

1. **First run on the Windows PC:** the runbook above, steps 1–9. Fix whatever
   breaks in the setup scripts, and record real Whisper speed and NVENC.
2. Ask the user whether to **open a PR / merge into `main`** once step 1 passes.
3. **Milestone 2** (details in `docs/implementation-plan.md`). First item: a local
   semantic re-ranker through Ollama (`analysis/semantic.py` is the plug-in point),
   plus a small hand-labelled set of real videos to measure pick quality.
