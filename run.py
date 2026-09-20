#!/usr/bin/env python
"""Start Aloud.

    python run.py                start the server
    python run.py --seed         load the demo subject, then start
    python run.py --seed-only    load the demo subject and exit
    python run.py --reset        wipe the database first
    python run.py --reload       restart on code changes
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import load_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Aloud: a voice-first learning platform.")
    parser.add_argument("--host", default=None, help="Host to bind (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="Port to bind (default 8000)")
    parser.add_argument("--reload", action="store_true", help="Restart on code changes")
    parser.add_argument("--reset", action="store_true", help="Wipe the database first")
    parser.add_argument("--seed", action="store_true", help="Load the demo subject")
    parser.add_argument("--seed-only", action="store_true", help="Load the demo subject and exit")
    args = parser.parse_args()

    settings = load_settings()
    settings.ensure_directories()

    if args.reset:
        from app.storage.db import Database

        Database(settings.db_path).reset()
        print(f"Database reset: {settings.db_path}")

    if args.seed or args.seed_only:
        from app.seed.demo import seed

        result = seed(settings)
        print(
            f"Demo subject ready: {result['title']} "
            f"({result['lessons']} lessons, {result['concepts']} concepts)"
        )
        print(f"  Sign in with  {result['email']}  /  {result['password']}")
        if args.seed_only:
            return 0

    import uvicorn

    host = args.host or settings.host
    port = args.port or settings.port
    print(f"\n  Aloud is running at  http://{host}:{port}\n")

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=args.reload,
        log_level="debug" if settings.debug else "info",
        access_log=settings.debug,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
