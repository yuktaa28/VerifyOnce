"""Run the whole app with one command: build the website if needed, then serve site and API.

    .venv\\Scripts\\python.exe scripts\\serve.py              # http://127.0.0.1:8000
    .venv\\Scripts\\python.exe scripts\\serve.py --port 8080
    .venv\\Scripts\\python.exe scripts\\serve.py --build      # force a fresh website build
    .venv\\Scripts\\python.exe scripts\\serve.py --no-build   # serve whatever web\\dist holds

The website is rebuilt when web\\dist is missing or older than the files in web\\src.
Settings come from the environment and .env as usual (PUBLIC_DEMO, DEMO_PIN, ...);
nothing secret is printed.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

WEB = REPO_ROOT / "web"
DIST_INDEX = WEB / "dist" / "index.html"
SOURCES = ("src", "public", "index.html", "package.json", "package-lock.json", "vite.config.ts", "tsconfig.json")


def newest_source_mtime(web: Path = WEB) -> float:
    newest = 0.0
    for name in SOURCES:
        path = web / name
        files = [path] if path.is_file() else (p for p in path.rglob("*") if p.is_file()) if path.is_dir() else []
        for f in files:
            newest = max(newest, f.stat().st_mtime)
    return newest


def needs_build(web: Path = WEB) -> bool:
    index = web / "dist" / "index.html"
    return not index.is_file() or index.stat().st_mtime < newest_source_mtime(web)


def build_website(web: Path = WEB) -> None:
    npm = shutil.which("npm")
    if npm is None:
        raise SystemExit("npm was not found. Install Node.js 20.19 or newer (free), then run this again. "
                         "Or build elsewhere and copy web\\dist here, then use --no-build.")
    if not (web / "node_modules").is_dir():
        print("Installing website packages (npm ci)...", flush=True)
        subprocess.run([npm, "ci", "--no-audit", "--no-fund"], cwd=web, check=True)
    print("Building the website (npm run build)...", flush=True)
    subprocess.run([npm, "run", "build"], cwd=web, check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1", help="address to listen on (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="port (default 8000)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--build", action="store_true", help="always rebuild the website first")
    group.add_argument("--no-build", action="store_true", help="never build; serve web\\dist as it is")
    args = parser.parse_args(argv)

    if args.build or (not args.no_build and needs_build()):
        try:
            build_website()
        except subprocess.CalledProcessError as exc:
            print(f"The website build failed (exit code {exc.returncode}). See the messages above.", file=sys.stderr)
            return 1
    elif not DIST_INDEX.is_file():
        print("Note: web\\dist is missing, so only the API will answer. Run without --no-build to build the site.")

    import uvicorn  # noqa: PLC0415  (imported late so --help works without the venv)

    from backend.config import Settings  # noqa: PLC0415

    settings = Settings.from_env()
    mode = "cache first" if settings.extraction_use_cache else "live"
    print(f"VerifyOnce on http://{args.host}:{args.port}  (website at /, API at /api, docs at /docs)")
    print(f"Extraction: {settings.extraction_provider} / {settings.provider_model}, mode {mode}. "
          f"Public demo: {'on' if settings.public_demo else 'off'}. "
          f"Demo PIN: {'set' if settings.demo_pin else 'not set'}.", flush=True)
    uvicorn.run("backend.main:app", host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
