# ClipForge — Implementation plan & status

Legend: ✅ done and covered by automated tests · 🟡 implemented, verified only partly
(see note) · ⬜ not started · ⛔ blocked

Last updated with milestone 1 (vertical slice).

## Milestone 1 — local vertical slice

### A. Project and video import
- ✅ Create/list/update projects (`/api/projects`)
- ✅ Import by absolute path; folder browser for the import dialog (`/api/fs/list`)
- ✅ ffprobe inspection: duration, display size (rotation-aware), fps, codecs, audio
- ✅ Rejects unsupported extensions, empty/corrupt/truncated files, audio-only files,
  missing files, relative paths — each with a code + hint
- ✅ Duplicate import of the same file into a project is refused (fingerprint)
- ✅ Missing ffmpeg/ffprobe → `DEPENDENCY_MISSING` with install instructions

### B. Transcription
- 🟡 faster-whisper engine: chunked PCM over a pipe, word timestamps, VAD, CUDA→CPU
  fallback, model released after each job, actionable download/CUDA errors.
  **Tested with a stand-in model only** — huggingface.co is blocked in the build
  environment, so no real Whisper weights could be downloaded. The real-model test
  (`tests/test_transcription.py::test_real_whisper_transcribes_synthetic_speech`) is
  in place and skipped; run it on the Windows machine (see README).
- ✅ Sidecar engine: import `.srt` / `.vtt` / ClipForge `.json`
- ✅ Word-timing repair (monotonic, min duration, interpolation when missing)
- ✅ Progress reporting; resume from committed chunks after a crash (tested)
- 🟡 Windows CUDA DLL discovery for pip-installed cuBLAS/cuDNN (`ensure_cuda_dll_dirs`)
  — written, **untested** (no Windows/GPU here)

### C. Candidate generation
- ✅ Sentences/paragraphs from punctuation + pauses; run-on sentences split
- ✅ Sentence-aligned windows within [min, max]; over-long only when no boundary
  exists, with an explanation
- ✅ Lead-in/tail that never swallows a neighbouring word; snapping to scene cuts
- ✅ `heuristic-v1` scoring with per-feature evidence, warnings, configurable weights
- ✅ Overlap suppression, ranking, post title + hashtags
- ✅ Visual-only fallback for videos with no speech (clearly labelled)
- ✅ `SemanticScorer` interface (no implementation yet — by design)

### D. Editing profiles
- ✅ Pydantic schema (duration, segmentation, weights, layout, framing, captions)
- ✅ Six built-ins: podcast, conversation, reaction, commentary, gaming, vlog
- ✅ Profiles change selection (weights), cutting (pauses, scene snapping,
  lead-in) and framing (layout) — tested
- ✅ Per-clip layout overrides; custom profiles via API (`POST /api/profiles`)
- 🟡 Face-centred crop (OpenCV Haar, sampled frames). Crop math tested; detection
  itself not validated on real footage (synthetic fixtures contain no faces)
- ⬜ Speaker-change detection / diarization (no free local model without a HF token;
  pauses are the only turn signal today)
- ⬜ Automatic facecam region detection (user sets the rectangle; a frame preview
  with the rectangle overlay helps)

### E. Animated captions
- ✅ Word-timed ASS, active-word highlight, pop animation, karaoke mode
- ✅ Pages respect line length / line count / sentence ends / commas; punctuation kept
- ✅ Safe-zone placement; moves off a detected face; "seam" position for stacked layouts
- ✅ Five presets + per-clip overrides + custom presets via API
- ✅ Missing/broken word timings handled

### F. Rendering
- ✅ 9:16 H.264/AAC MP4 at 1080×1920 (or 720×1280 / 540×960)
- ✅ Layouts: crop, fit_blur, split_stack, content_facecam (fit/crop, top/bottom)
- ✅ Measured A/V sync (flash/beep fixture): onsets within one frame
- ✅ Hardware encoder detection via real test encode; CPU fallback on failure
- ✅ Temp output → ffprobe + full decode validation → atomic move; versioned names
- ✅ Exact settings (argv, encoder, layout plan, caption style) stored per render
- ✅ Silent sources get a silent AAC track
- 🟡 NVENC path: selection/fallback logic tested; an actual NVENC encode could not be
  run here (no GPU). Verify on the RTX 2060 Super.

### G. Review interface (React + Vite)
- ✅ Library, project page, import dialog, job progress/cancel, failure list + retry
- ✅ Review: candidate queue with filters, rendered preview / source-range preview,
  reasons + warnings + score breakdown, transcript, boundary editor (nudge, snap,
  reset), profile / layout / caption tweaks, post title + hashtags, approve / reject
  / reset (keys A / X / J / K), render-again, export with copyable caption
- ✅ Jobs page with event history; Settings + diagnostics page
- 🟡 Verified by a scripted headless-Chromium walkthrough (import → approve → render
  → export) in the dev container; no automated UI test is committed yet

### H. Persistent jobs
- ✅ SQLite state machine: queued/running/completed/failed/cancelled
- ✅ Recovery after restart; graceful-shutdown requeue; one worker per data dir
- ✅ Duplicate prevention (DB partial unique index) and atomic claims under contention
- ✅ Cancellation of queued and running jobs (ffmpeg killed, temp files removed)
- ✅ Retries with backoff for retryable errors; immediate failure for non-retryable;
  manual retry; stale workers can't overwrite a re-claimed job

### Packaging / setup
- 🟡 `setup.cmd` / `start.cmd` + PowerShell scripts — **not executed** (no Windows
  in the build environment). Review on first run.
- ⬜ Tauri 2 desktop shell

## Automated tests (pytest)

`101 passed, 1 skipped` in ~105 s on a 4-core container (Python 3.13, FFmpeg 6.1.1,
no GPU). Mapping to the required list:

| # | Requirement | Where |
|---|---|---|
| 1 | Valid video import | `test_import.py::test_probe_valid_video`, `::test_import_creates_media_and_jobs` |
| 2 | Invalid / unsupported files | `test_import.py::test_invalid_files_rejected`, `::test_truncated_video_rejected`, `::test_audio_only_rejected`, `::test_missing_and_relative_paths` |
| 3 | Videos without audio | `test_import.py::test_video_without_audio` |
| 4 | Transcription timestamps | `test_transcription.py` (sidecar exactness, word repair, adapter offsets, resume) |
| 5 | Candidate duration / boundaries | `test_candidates.py` (all six profiles) |
| 6 | Caption generation / punctuation | `test_captions.py` |
| 7 | Rendering with captions | `test_render.py::test_render_with_captions_burns_text`, `::test_every_layout_renders_valid_vertical_output` |
| 8 | A/V sync | `test_render.py::test_audio_video_stay_in_sync` |
| 9 | CPU encoding fallback | `test_render.py::test_hardware_encoder_failure_falls_back_to_cpu` |
| 10 | HW acceleration detection | `test_render.py::test_hardware_detection_*` |
| 11 | Output validation | `test_render.py::test_output_validation_catches_bad_files`, `::test_render_failure_keeps_previous_outputs` |
| 12 | Job persistence after restart | `test_jobs.py::test_jobs_survive_restart_and_orphans_are_recovered` |
| 13 | Failed-job retry | `test_jobs.py::test_retryable_*`, `::test_manual_retry_of_failed_job` |
| 14 | Cancellation / duplicate prevention | `test_jobs.py::test_cancel_*`, `::test_enqueue_dedupes_active_jobs`, `::test_concurrent_claims_never_double_execute` |
| 15 | Review-state transitions | `test_review.py` |
| 16 | Missing source files | `test_paths.py::test_missing_source_is_detected_and_relinked` |
| 17 | Safe output naming / paths | `test_paths.py` |

Fixtures are generated with ffmpeg at test time (test patterns + `flite` synthetic
speech), so no third-party footage is involved.

## Manual tests still to do (on the Windows 10/11 + RTX 2060 Super machine)

1. `setup.cmd` from a clean checkout (Python, FFmpeg via winget, venv, GPU libs, UI build).
2. `clipforge doctor`: NVENC shows ✓; CUDA shows 1 device.
3. Real Whisper: `set CLIPFORGE_TEST_WHISPER_MODEL=tiny.en` then
   `.venv\Scripts\python -m pytest -m whisper`; then a real 1-hour podcast with `small`
   on GPU — note time per hour of audio and peak VRAM.
4. Render a clip with NVENC and confirm the render row's `encoder` is `h264_nvenc`.
5. Face crop on real podcast/vlog footage; facecam layout on a real gaming VOD.
6. Kill the app mid-transcription and mid-render; restart; confirm resume / re-render.
7. Paths with spaces, non-ASCII names, a source on an external drive that is then
   unplugged (→ "missing" + relink).
8. Upload an exported clip to TikTok/Reels drafts: check captions stay clear of the UI.

## Next milestone (M2) — make the picks good and the loop fast

1. **Local semantic re-ranker** via Ollama (`SemanticScorer`): score the top ~40
   windows per video for "works standalone / has a payoff", one-line reason, cache by
   transcript hash. Opt-in, offline, $0.
2. **Real-footage evaluation set**: 5–10 creator-authorised videos with hand-marked
   "good clips"; report precision@10 for heuristic-v1 vs. re-ranked, to tune weights.
3. **Speaker turns**: lightweight energy/embedding-based speaker-change detection to
   improve podcast boundaries and split-screen choice.
4. **Moving face tracking** (smoothed per-second crop path instead of one fixed crop).
5. **Timeline editor**: waveform + word strip for dragging clip edges.
6. **Tauri 2 shell** with native file dialogs and the backend as a sidecar.
7. Playwright UI test committed to CI.

Later (not before the above): source connectors (creator-authorised only), official
platform publishing APIs with tokens in the OS keychain, accounts/entitlements,
optional remote workers.
