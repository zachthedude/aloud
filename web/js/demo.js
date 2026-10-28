// Static demo (GitHub Pages): runs the real FastAPI server inside the browser with
// Pyodide and answers /api/* calls from it over ASGI. The database lives in
// IndexedDB, so subjects and progress survive reloads. Only the Pages build
// loads this file; `python run.py` never does.

const PYODIDE = 'https://cdn.jsdelivr.net/pyodide/v0.27.2/full/';
const status = document.getElementById('demo-status');
const say = (text) => { if (status) status.textContent = text; };
// Storage can be blocked (private modes, site data off); the demo still runs, it just won't remember.
const store = {
  get(key) { try { return localStorage.getItem(key); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* not remembered */ } },
};
// Each demo on this origin needs its own IndexedDB database: IDBFS names it after the mountpoint.
const SAVE = '/save-aloud';

const PY_BOOT = `
import asyncio, os, sys, concurrent.futures, anyio.to_thread

# Pyodide has no threads. Run thread-pool work inline instead: FastAPI's sync
# endpoints (via anyio) and Aloud's background job runner.
async def _run_sync(func, *args, **_):
    return func(*args)
anyio.to_thread.run_sync = _run_sync

class _InlinePool:
    def __init__(self, *a, **k): pass
    def submit(self, fn, *a, **k):
        f = concurrent.futures.Future()
        try: f.set_result(fn(*a, **k))
        except BaseException as e: f.set_exception(e)
        return f
    def shutdown(self, *a, **k): pass
concurrent.futures.ThreadPoolExecutor = _InlinePool

sys.path.insert(0, '/aloud')
from app.main import create_app
app = create_app()
await app.router.lifespan_context(app).__aenter__()

async def handle(method, path, query, headers, body):
    body = body.to_bytes() if hasattr(body, 'to_bytes') else bytes(body)
    done = asyncio.Event()
    first = True
    async def receive():
        nonlocal first
        if first:
            first = False
            return {'type': 'http.request', 'body': body, 'more_body': False}
        await done.wait()
        return {'type': 'http.disconnect'}
    out = {'status': 500, 'headers': [], 'body': []}
    async def send(message):
        if message['type'] == 'http.response.start':
            out['status'] = message['status']
            out['headers'] = [(k.decode('latin-1'), v.decode('latin-1')) for k, v in message['headers']]
        elif message['type'] == 'http.response.body':
            out['body'].append(message.get('body', b''))
            if not message.get('more_body'): done.set()
    scope = {
        'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
        'method': method, 'scheme': 'https', 'path': path, 'raw_path': path.encode(),
        'query_string': query.encode(), 'root_path': '',
        'headers': [(k.encode('latin-1'), v.encode('latin-1')) for k, v in headers],
        'client': ('127.0.0.1', 0), 'server': ('demo', 443),
    }
    await app(scope, receive, send)
    done.set()
    return out['status'], out['headers'], b''.join(out['body'])
`;

async function boot() {
  say('Loading the teacher…');
  const { loadPyodide } = await import(PYODIDE + 'pyodide.mjs');
  const py = await loadPyodide({ indexURL: PYODIDE });
  await py.loadPackage(['micropip', 'sqlite3', 'hashlib', 'pydantic', 'lxml', 'Pillow', 'ssl']);
  const micropip = py.pyimport('micropip');
  await micropip.install(['anyio==4.7.0', 'starlette==0.41.3', 'fastapi==0.115.6', 'python-multipart==0.0.20', 'email-validator==2.2.0', 'pypdf==5.1.0', 'python-docx==1.1.2', 'python-pptx==1.0.2']);

  say('Opening your library…');
  py.FS.mkdirTree(SAVE);
  py.FS.mount(py.FS.filesystems.IDBFS, {}, SAVE);
  await new Promise((resolve) => py.FS.syncfs(true, resolve));
  if (!py.FS.analyzePath(`${SAVE}/data/aloud.db`).exists) {
    py.unpackArchive(await (await fetch(`${BASE}/seed.zip`)).arrayBuffer(), 'zip', { extractDir: `${SAVE}/data` });
  }
  py.unpackArchive(await (await fetch(`${BASE}/server.zip`)).arrayBuffer(), 'zip', { extractDir: '/aloud' });

  // Sessions are signed, so the key must outlive a page load.
  let secret = store.get('aloud_demo_secret');
  if (!secret) {
    secret = [...crypto.getRandomValues(new Uint8Array(32))].map((b) => b.toString(16).padStart(2, '0')).join('');
    store.set('aloud_demo_secret', secret);
  }
  const env = py.pyimport('os').environ;
  env.update(py.toPy({ ALOUD_DATA_DIR: `${SAVE}/data`, ALOUD_SECRET_KEY: secret, ALOUD_LLM_PROVIDER: 'none' }));
  await py.runPythonAsync(PY_BOOT);
  const handle = py.globals.get('handle');

  // The cookies the server sets are kept here and replayed on every call.
  let saved = [];
  try { saved = JSON.parse(store.get('aloud_demo_cookies') || '[]'); } catch { /* corrupt: start signed out */ }
  const jar = new Map(saved);
  let saving = Promise.resolve();
  const realFetch = window.fetch.bind(window);

  window.fetch = async (input, init = {}) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.origin);
    if (!url.pathname.startsWith('/api/')) return realFetch(input, init);

    const method = (init.method || 'GET').toUpperCase();
    const headers = [];
    let body = new Uint8Array();
    const sent = new Headers(init.headers);
    if (init.body !== undefined && init.body !== null) {
      // Let the browser serialise FormData into real multipart, boundary and all.
      const encoded = new Request(url, { method, body: init.body, headers: sent });
      body = new Uint8Array(await encoded.arrayBuffer());
      const type = encoded.headers.get('content-type');
      if (type) sent.set('content-type', type);
    }
    sent.forEach((v, k) => headers.push([k, v]));
    if (jar.size) headers.push(['cookie', [...jar].map(([k, v]) => `${k}=${v}`).join('; ')]);

    const result = await handle(method, url.pathname, url.search.slice(1), py.toPy(headers), py.toPy(body));
    const [code, resHeaders, resBody] = result.toJs();
    result.destroy();

    const out = new Headers();
    for (const [k, v] of resHeaders) {
      if (k !== 'set-cookie') { out.append(k, v); continue; }
      const [pair, ...attrs] = v.split(';');
      const [name, ...rest] = pair.split('=');
      const value = rest.join('=');
      if (!value || attrs.some((a) => /max-age=0\b|expires=thu, 01 jan 1970/i.test(a))) jar.delete(name.trim());
      else jar.set(name.trim(), value);
    }
    store.set('aloud_demo_cookies', JSON.stringify([...jar]));
    // Answer only once the write is in IndexedDB, so a reload straight after can't lose it.
    if (method !== 'GET') {
      saving = saving.then(() => new Promise((r) => py.FS.syncfs(false, (err) => { if (err) console.error('Demo save failed', err); r(); })));
      await saving;
    }

    const empty = code === 204 || code === 304 || method === 'HEAD';
    return new Response(empty ? null : resBody, { status: code, headers: out });
  };

  // Uploads use XMLHttpRequest for progress events; route them through the fetch
  // above. Only the parts core/api.js uses are implemented.
  window.XMLHttpRequest = class {
    constructor() {
      this.upload = new EventTarget();
      this.events = new EventTarget();
      this.status = 0;
      this.responseText = '';
    }
    open(method, url) { this.method = method; this.url = url; }
    addEventListener(type, fn) { this.events.addEventListener(type, fn); }
    send(body) {
      window.fetch(this.url, { method: this.method, body })
        .then(async (res) => {
          const done = new ProgressEvent('progress', { lengthComputable: true, loaded: 1, total: 1 });
          this.upload.dispatchEvent(done);
          this.status = res.status;
          this.responseText = await res.text();
          this.events.dispatchEvent(new Event('load'));
        })
        .catch(() => this.events.dispatchEvent(new Event('error')));
    }
  };
}

// Same rule as core/router.js: the app's root, whatever path Pages serves it from.
const BASE = new URL('../', import.meta.url).pathname.replace(/\/$/, '');

// Visitors who sign up get an empty library; the sample subject lives on the
// seeded account, so say how to reach it.
function demoNote() {
  try { if (localStorage.getItem('aloud_demo_note') === 'closed') return; } catch { /* private mode */ }
  const note = document.createElement('aside');
  note.setAttribute('aria-label', 'About this demo');
  note.style.cssText = `position:fixed;left:16px;bottom:16px;z-index:40;max-width:min(360px,calc(100vw - 32px));
    padding:14px 44px 14px 16px;border-radius:16px;background:var(--chalk-raised);color:var(--ink-soft);
    border:1px solid var(--chalk-line);box-shadow:0 12px 32px -12px rgba(27,36,48,.25);
    font:var(--text-sm)/1.5 var(--font-sans)`;
  note.innerHTML = `<strong style="color:var(--ink)">Live demo.</strong> Everything runs in your browser and saves here.
    To open the sample physics subject, sign in as <code>student@aloud.app</code> / <code>learnaloud</code>.
    <button type="button" aria-label="Dismiss" style="position:absolute;top:8px;right:8px;width:28px;height:28px;
      border:0;border-radius:50%;background:transparent;color:inherit;font-size:18px;cursor:pointer">×</button>`;
  note.querySelector('button').addEventListener('click', () => {
    note.remove();
    try { localStorage.setItem('aloud_demo_note', 'closed'); } catch { /* private mode */ }
  });
  document.body.append(note);
}

boot()
  .then(() => { demoNote(); return import('./app.js'); })
  .catch((err) => {
    console.error(err.message || err);
    say('The demo could not start in this browser. Try a recent Chrome, Edge, Firefox or Safari.');
  });
