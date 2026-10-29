# ClipForge — Decision log

Short records of choices that shape the codebase. Newest last.

## D1. A new top-level `clipforge/` directory in this repo
The repository is a collection of independent tools (one per top-level directory).
Nothing in `alfred/` or `timebox-timer/` is related to video, so ClipForge lives in
its own directory and touches nothing else. It could be split into its own repo
later without changes (no cross-directory imports).

## D2. Python engine + FastAPI, React/Vite UI, Tauri deferred
Video tooling (faster-whisper, OpenCV, numpy) is Python-native, so the engine is
Python. The UI talks to it over a localhost HTTP API, which works today in any
browser and is exactly what a Tauri 2 shell would wrap later (the shell would start
the backend as a sidecar and point a webview at it). Tauri was **not** added in
milestone 1: it needs the Rust toolchain + WebView2 setup on Windows and adds no
capability the pipeline needs yet. Native file dialogs are replaced by a server-side
folder browser (`/api/fs/list`) for now.

## D3. FFmpeg subprocesses, not Python bindings
`ffprobe`/`ffmpeg` run as subprocesses with `-progress pipe:1`. This keeps memory
bounded (no frames in Python), lets us kill a render on cancel, and makes every
render reproducible — the exact argv is stored on the render row. PyAV (pulled in
by faster-whisper) is not used directly.

## D4. ASS subtitles via libass, referenced by relative path
Word-level animated captions are ASS events rendered by ffmpeg's `ass` filter.
ffmpeg filter-string escaping of Windows paths (`C\:/...`) is notoriously fragile,
so ffmpeg runs with `cwd` set to a per-render work dir and the ASS file is
referenced as `captions.ass`.

## D5. Hardware encoders are trusted only after a test encode
`ffmpeg -encoders` listing `h264_nvenc` says nothing about drivers. Each hardware
encoder gets a 10-frame test encode; the result (with the encoder's own error line)
is cached and shown in Settings → Diagnostics. If a hardware encode still fails
mid-render we retry once with libx264 and record a warning. In this development
container NVENC/QSV are compiled in but fail (`Cannot load libcuda.so.1`), which is
exactly the case this guards against.

## D6. Chunked transcription with per-chunk commits
faster-whisper decodes its whole input into RAM (16 kHz float32 ≈ 230 MB/hour). We
feed it ~10-minute PCM chunks from an ffmpeg pipe instead, with cut points moved to
the quietest moment within ±30 s. Each chunk's segments and its `done` flag commit in
one transaction, which is what makes resume-after-crash safe. Default model `small`
with `int8_float16` on CUDA (~1 GB VRAM on the RTX 2060 Super), `int8` on CPU; the
model is released after each job so NVENC has the VRAM.

## D7. SQLite job queue instead of Celery/Redis/RQ
Single machine, single user, $0 budget: a job table with an atomic claim
(`UPDATE … WHERE status='queued'` + claim token), leases + heartbeats, a partial
unique index for dedupe, and an OS file lock for "one worker per data dir" covers
the durability needs without another service. The lane/lease/worker_id columns are
the seam for a remote worker later.

## D8. Transparent heuristic scorer first
Milestone 1 ships `heuristic-v1`: named structural features × per-profile weights,
with evidence strings. It is labelled as a baseline everywhere it's shown. A
`SemanticScorer` protocol is the plug-in point for a local LLM (Ollama is already
used by the Alfred tool in this repo) — deliberately not built until the baseline
pipeline is solid, and to avoid pretending semantic understanding exists.

## D9. Source files: absolute path + content fingerprint
Sources stay where the user keeps them (often external drives). We store the last
known absolute path plus `v1:<size>:<sha256(first MiB + last MiB)>`. Relinking
verifies the fingerprint; everything ClipForge writes is stored relative to the data
dir so the data dir itself can move.

## D10. Sidecar transcripts as a first-class engine
Importing an existing `.srt/.vtt/.json` is a real feature (creators often have
captions already) and also gives the test suite deterministic transcripts. SRT/VTT
words are timed by interpolation and flagged `estimated`; the UI warns about it.

## D11. Silent videos still produce candidates — labelled as weak
No audio → no transcript → candidates come from scene changes (or even windows),
with a reason that says so and a warning that captions will be empty. Renders get a
silent AAC track because some platforms reject video-only uploads.

## D12. Approve auto-renders; export requires approval
Approving a clip without a completed render queues one (deduplicated). Export is
only enabled for approved clips, and always writes a new file (`name (2).mp4`) next
to a `.json` with post metadata and a `.txt` caption. Nothing is published.
