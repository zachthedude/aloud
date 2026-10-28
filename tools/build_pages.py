"""Build the static GitHub Pages demo into _site/ (see web/js/demo.js).

    python tools/build_pages.py [--base /aloud] [--push]

Ships the web client, the server code zipped for Pyodide, and a database already
seeded with the demo subject so the first visit opens straight into it.
"""
import argparse
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "_site"

parser = argparse.ArgumentParser()
parser.add_argument("--base", default="/aloud", help="path Pages serves the site from")
parser.add_argument("--push", action="store_true", help="publish _site to the gh-pages branch")
args = parser.parse_args()
base = args.base.rstrip("/")

if OUT.exists():
    # _site/.git (from --push) holds read-only objects on Windows; clear the flag and retry.
    shutil.rmtree(OUT, onexc=lambda fn, path, _: (os.chmod(path, stat.S_IWRITE), fn(path)))
shutil.copytree(ROOT / "web", OUT)
(OUT / "sw.js").unlink()  # the in-page server makes an offline shell pointless

with zipfile.ZipFile(OUT / "server.zip", "w", zipfile.ZIP_DEFLATED) as z:
    for f in (ROOT / "app").rglob("*"):
        if f.is_file() and "__pycache__" not in f.parts:
            z.write(f, f.relative_to(ROOT))

with tempfile.TemporaryDirectory() as data:
    subprocess.run([sys.executable, "run.py", "--seed-only"], cwd=ROOT, check=True,
                   env={**os.environ, "ALOUD_DATA_DIR": data, "ALOUD_LLM_PROVIDER": "none"})
    with zipfile.ZipFile(OUT / "seed.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for f in Path(data).rglob("*"):
            if f.is_file() and not f.name.endswith(("-wal", "-shm")):
                z.write(f, f.relative_to(data))

index = OUT / "index.html"
html = index.read_text(encoding="utf-8")
assert '<script type="module" src="/js/app.js">' in html and "<p>Aloud</p>" in html
html = html.replace('src="/js/app.js"', 'src="/js/demo.js"')
html = html.replace("<p>Aloud</p>", '<p>Aloud</p>\n      <p id="demo-status" class="muted">Starting…</p>')
html = html.replace('="/', f'="{base}/')
index.write_text(html, encoding="utf-8")
manifest = OUT / "manifest.webmanifest"
manifest.write_text(manifest.read_text(encoding="utf-8").replace('"/', f'"{base}/'), encoding="utf-8")
shutil.copyfile(index, OUT / "404.html")  # deep links: Pages serves this for /aloud/space/...
(OUT / ".nojekyll").touch()
print("built", OUT)

if args.push:
    remote = subprocess.check_output(["git", "remote", "get-url", "origin"], cwd=ROOT, text=True).strip()
    run = lambda *a: subprocess.run(["git", *a], cwd=OUT, check=True)
    run("init", "-q", "-b", "gh-pages")
    run("add", "-A")
    run("-c", "user.name=pages", "-c", "user.email=pages@localhost", "commit", "-q", "-m", "Build")
    run("push", "-q", "-f", remote, "gh-pages")
    print("pushed gh-pages")
