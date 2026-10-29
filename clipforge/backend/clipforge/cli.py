"""Command line: ``python -m clipforge {serve,worker,doctor,migrate}``."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
import webbrowser


def _ctx(args):
    from .config import load_settings
    from .context import AppContext
    from .logging_setup import setup_logging

    overrides = {}
    if getattr(args, "data_dir", None):
        overrides["data_dir"] = args.data_dir
    settings = load_settings(**overrides)
    settings.ensure_dirs()
    setup_logging(settings.logs_dir)
    return settings, AppContext(settings)


def cmd_serve(args) -> int:
    import uvicorn

    from .api.app import create_app
    settings, ctx = _ctx(args)
    if args.host not in ("127.0.0.1", "localhost"):
        print("Refusing to bind to a non-local address: ClipForge's API can browse local folders.",
              file=sys.stderr)
        return 2
    app = create_app(ctx, start_worker=not args.no_worker)
    url = f"http://{args.host}:{args.port}/"
    print(f"ClipForge {settings.data_dir} → {url}")
    if args.open:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def cmd_worker(args) -> int:
    from .jobs.worker import Worker
    _settings, ctx = _ctx(args)
    w = Worker(ctx)
    if not w.start():
        print("Another worker is already running for this data directory.", file=sys.stderr)
        return 1
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
    print("Worker running. Ctrl+C to stop.")
    stop.wait()
    w.stop()
    return 0


def cmd_doctor(args) -> int:
    from .media.ffmpeg import ffmpeg_version
    settings, ctx = _ctx(args)
    ok = True
    print(f"Data dir : {settings.data_dir}")
    v = ffmpeg_version(settings.ffmpeg)
    print(f"FFmpeg   : {v or 'NOT FOUND'}")
    ok &= v is not None
    if v:
        hw = ctx.hardware(refresh=True)
        for e in hw.encoders:
            mark = "OK " if e.works else ("-- " if not e.compiled else "NO ")
            print(f"  {mark}{e.name:11s} {e.detail}")
        print(f"CUDA     : {hw.cuda_detail}")
    for mod in ("faster_whisper", "ctranslate2", "cv2", "numpy", "fastapi", "sqlalchemy"):
        try:
            m = __import__(mod)
            print(f"{mod:15s}: {getattr(m, '__version__', 'ok')}")
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"{mod:15s}: MISSING ({e})")
    prefs = ctx.prefs()
    print("Settings :", json.dumps(prefs))
    return 0 if ok else 1


def cmd_migrate(args) -> int:
    settings, _ctx_ = _ctx(args)
    print(f"Database at {settings.db_path} is up to date.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="clipforge")
    p.add_argument("--data-dir", help="override CLIPFORGE_DATA_DIR")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the local API + web UI (+ worker)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-worker", action="store_true", help="don't run jobs in this process")
    s.add_argument("--open", action="store_true", help="open the browser")
    s.set_defaults(fn=cmd_serve)
    sub.add_parser("worker", help="run only the job worker").set_defaults(fn=cmd_worker)
    sub.add_parser("doctor", help="check dependencies and hardware").set_defaults(fn=cmd_doctor)
    sub.add_parser("migrate", help="apply database migrations").set_defaults(fn=cmd_migrate)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
