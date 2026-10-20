import os
import re
import json
import threading
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import HTMLResponse

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOCK = threading.Lock()

# Itakda ang SECRET sa Render para hindi mahulaan ng iba ang MCP URL mo.
SECRET = os.environ.get("SECRET", "").strip("/")
MCP_PATH = f"/{SECRET}/mcp" if SECRET else "/mcp"

mcp = FastMCP(
    "Library",
    host="0.0.0.0",
    port=int(os.environ.get("PORT", 8000)),
    streamable_http_path=MCP_PATH,
)

KINDS = ("book", "lesson", "flashcards", "quiz")


# ---------- Storage ----------
def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "file"


def path_of(file: str) -> Path:
    return DATA_DIR / f"{slugify(file)}.json"


def load(file: str):
    p = path_of(file)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def save(file: str, data: dict):
    path_of(file).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------- MCP tools (para kay Claude) ----------
@mcp.tool()
def create_file(name: str, description: str = "") -> str:
    """Create a new file (collection) that will hold books and lessons."""
    with LOCK:
        if load(name):
            return f"Existing na ang file na '{name}'."
        save(name, {"name": name, "description": description, "entries": []})
    return f"Nagawa ang file na '{name}'."


@mcp.tool()
def list_files() -> str:
    """List all files and how many entries each one has."""
    rows = []
    for p in sorted(DATA_DIR.glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        rows.append(f"- {d['name']} ({len(d['entries'])} entries)")
    return "\n".join(rows) or "Wala pang file."


@mcp.tool()
def add_entry(file: str, title: str, content: str, kind: str = "lesson") -> str:
    """Add an entry to a file. kind: 'book', 'lesson', 'flashcards' or 'quiz'.
    Lessons and books use Markdown (#, **bold**, - bullets, 1. lists).
    For kind='flashcards', write each card as a line 'Q: question' followed by
    a line 'A: answer'.
    For kind='quiz', write each item as 'Q: question', then options on separate
    lines 'a) ...', 'b) ...', 'c) ...', 'd) ...', then 'ANS: b'."""
    if kind not in KINDS:
        return f"Mali ang kind. Pumili sa: {', '.join(KINDS)}."
    with LOCK:
        d = load(file)
        if not d:
            return f"Walang file na '{file}'. Gamitin muna ang create_file."
        next_id = max([e["id"] for e in d["entries"]], default=0) + 1
        d["entries"].append(
            {"id": next_id, "kind": kind, "title": title, "content": content}
        )
        save(file, d)
    return f"Naidagdag ang {kind} na '{title}' (id {next_id}) sa '{file}'."


@mcp.tool()
def read_file(file: str) -> str:
    """Read all entries inside a file."""
    d = load(file)
    if not d:
        return f"Walang file na '{file}'."
    out = [f"# {d['name']}\n{d.get('description', '')}\n"]
    for e in d["entries"]:
        out.append(f"## [{e['id']}] ({e['kind']}) {e['title']}\n{e['content']}\n")
    return "\n".join(out)


@mcp.tool()
def update_entry(file: str, entry_id: int, title: str = "", content: str = "") -> str:
    """Edit the title and/or content of an existing entry."""
    with LOCK:
        d = load(file)
        if not d:
            return f"Walang file na '{file}'."
        for e in d["entries"]:
            if e["id"] == entry_id:
                if title:
                    e["title"] = title
                if content:
                    e["content"] = content
                save(file, d)
                return f"Na-update ang entry {entry_id}."
    return f"Walang entry na id {entry_id}."


@mcp.tool()
def delete_entry(file: str, entry_id: int) -> str:
    """Delete an entry from a file."""
    with LOCK:
        d = load(file)
        if not d:
            return f"Walang file na '{file}'."
        before = len(d["entries"])
        d["entries"] = [e for e in d["entries"] if e["id"] != entry_id]
        save(file, d)
    return "Nabura." if len(d["entries"]) < before else f"Walang entry na id {entry_id}."


# ---------- Website (para sa iyo) ----------
PAGE = r"""<!doctype html>
<html lang="fil"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Library</title>
<style>
:root{color-scheme:dark;--bg:#0e1420;--panel:#20283a;--purple:#4527a0;--tile:#e6eaf5;--blue:#4285f4;--fg:#eef;--muted:#8b93a7;--chip:#2d3650}
[data-theme=light]{color-scheme:light;--bg:#f4f6fb;--panel:#e3e8f4;--fg:#1a2033;--muted:#5b6478;--chip:#d3daea}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font-family:"Space Grotesk",system-ui,sans-serif;min-height:100vh}
.wrap{max-width:720px;margin:0 auto;padding:28px 16px 60px}
.th{position:fixed;top:10px;right:12px;background:var(--panel);color:var(--fg);border:0;border-radius:50%;width:38px;height:38px;font-size:18px;cursor:pointer;z-index:5}
h1{font-size:34px;letter-spacing:1px;margin:8px 0 6px}
.sub{color:var(--muted);margin:0 0 14px;line-height:1.5}
.stats{font-weight:700;margin:0 0 12px}
.search{width:100%;padding:13px 16px;border-radius:16px;border:0;background:var(--panel);color:var(--fg);font:inherit;margin-bottom:14px}
.back{display:inline-block;color:#6f93ee;text-decoration:none;margin:0 0 14px;font-weight:600}
.panel{background:var(--panel);border-radius:28px;padding:16px;min-height:50vh}
.folder{display:flex;align-items:center;gap:16px;background:var(--purple);border-radius:28px;
padding:16px 18px;color:#fff;text-decoration:none;margin-bottom:14px}
.folder.sm{padding:12px 16px;border-radius:18px}.folder.sm span{opacity:.7}
.ficon{flex:none;width:68px;height:68px;border-radius:20px;background:rgba(255,255,255,.18);
display:flex;align-items:center;justify-content:center}
.fi{min-width:0}
.fname{font-size:22px;font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fmeta{font-size:15px;font-weight:700;margin-top:6px;opacity:.95}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:18px;padding:6px 2px}
.tile{text-decoration:none;color:var(--fg);text-align:center}
.thumb{background:var(--tile);border-radius:20px;aspect-ratio:4/5;display:flex;align-items:center;justify-content:center;position:relative;font-size:40px}
.chk{position:absolute;top:8px;right:10px;font-size:18px;color:#2e9e5b;font-weight:900}
.name{margin-top:8px;font-weight:700;font-size:15px;word-break:break-word;display:-webkit-box;
-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.muted{color:var(--muted)}
.tag{font-size:12px;background:var(--chip);border-radius:8px;padding:3px 10px}
.read h2{font-size:26px;margin:12px 0}
.md h2,.md h3,.md h4{margin:18px 0 6px}
.md p,.md li{line-height:1.7;font-size:16px;margin:6px 0}
code{background:var(--chip);padding:1px 6px;border-radius:6px}
.card{background:var(--panel);border-radius:24px;padding:28px 20px;min-height:180px;display:flex;flex-direction:column;justify-content:center;text-align:center;font-size:20px;line-height:1.5;cursor:pointer;margin:12px 0}
.card small{display:block;font-size:12px;letter-spacing:2px;color:var(--muted);margin-bottom:10px}
.btn{background:var(--blue);color:#fff;border:0;border-radius:16px;padding:14px 18px;font:inherit;font-weight:700;cursor:pointer;margin:6px 0}
.row{display:flex;gap:12px}.row .btn{flex:1}
.good{background:#2e9e5b}.bad{background:#d9534f}
.opt{display:block;width:100%;text-align:left;margin:8px 0;padding:14px 16px;border-radius:16px;border:2px solid transparent;background:var(--panel);color:var(--fg);font:inherit;cursor:pointer}
.opt.ok{border-color:#2e9e5b;background:rgba(46,158,91,.25)}
.opt.no{border-color:#d9534f;background:rgba(217,83,79,.25)}
.center{text-align:center}
</style></head><body>
<button class="th" onclick="theme()">◐</button>
<div class="wrap" id="app"></div>
<script>
const FILES = __DATA__;
const app = document.getElementById('app');
const $ = s => document.querySelector(s);
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const enc = new TextEncoder();
const bytes = es => es.reduce((n, e) => n + enc.encode(e.title + e.content).length, 0);
const kb = n => n < 1024 ? n + ' B' : (n / 1024).toFixed(1) + ' KB';
const count = n => n + (n === 1 ? ' entry' : ' entries');
const DOC = '<svg viewBox="0 0 24 24" width="46" height="46"><path fill="#4285f4" d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zm2 16H8v-2h8v2zm0-4H8v-2h8v2zm-3-5V3.5L18.5 9H13z"/></svg>';
const BOOK = '<svg viewBox="0 0 24 24" width="46" height="46"><path fill="#f4a142" d="M18 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2zM6 4h5v8l-2.5-1.5L6 12V4z"/></svg>';
const FOLDER = '<svg viewBox="0 0 24 24" width="34" height="34"><path fill="#fff" d="M10 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-8l-2-2z"/></svg>';
const ICON = {book: BOOK, lesson: DOC, flashcards: '🃏', quiz: '📝'};

// ---- Progress (localStorage, gumagana offline) ----
const store = {
  get(k, d) { try { const v = localStorage.getItem('lib:' + k); return v ? JSON.parse(v) : d; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem('lib:' + k, JSON.stringify(v)); } catch (e) {} }
};
const ymd = d => d.toLocaleDateString('en-CA');
const K = (f, e) => f.name + '/' + e.id;
const shuf = a => a.map(x => [Math.random(), x]).sort((p, q) => p[0] - q[0]).map(p => p[1]);
const ci = q => 'abcd'.indexOf(q.a[0].toLowerCase());
function touch() {
  const s = store.get('streak', {last: '', n: 0}), t = ymd(new Date());
  if (s.last === t) return;
  s.n = s.last === ymd(new Date(Date.now() - 864e5)) ? s.n + 1 : 1;
  s.last = t; store.set('streak', s);
}
const streak = () => {
  const s = store.get('streak', {last: '', n: 0});
  return s.last === ymd(new Date()) || s.last === ymd(new Date(Date.now() - 864e5)) ? s.n : 0;
};
const dn = f => f.entries.filter(e => store.get('done', {})[K(f, e)]).length;
function theme() {
  const t = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
  document.documentElement.dataset.theme = t; store.set('theme', t);
}
document.documentElement.dataset.theme = store.get('theme', 'dark');

// ---- Mini markdown ----
function md(src) {
  const inl = s => esc(s).replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>').replace(/\*([^*]+)\*/g, '<i>$1</i>');
  const out = []; let list = null, m;
  const close = () => { if (list) { out.push('</' + list + '>'); list = null; } };
  for (const l of src.split('\n')) {
    if ((m = l.match(/^(#{1,3})\s+(.*)/))) { close(); const h = m[1].length + 1; out.push(`<h${h}>${inl(m[2])}</h${h}>`); }
    else if ((m = l.match(/^\s*[-*]\s+(.*)/))) { if (list !== 'ul') { close(); out.push('<ul>'); list = 'ul'; } out.push(`<li>${inl(m[1])}</li>`); }
    else if ((m = l.match(/^\s*\d+[.)]\s+(.*)/))) { if (list !== 'ol') { close(); out.push('<ol>'); list = 'ol'; } out.push(`<li>${inl(m[1])}</li>`); }
    else if (!l.trim()) close();
    else { close(); out.push(`<p>${inl(l)}</p>`); }
  }
  close(); return out.join('');
}

// ---- Parsers para sa flashcards at quiz ----
function cards(c) {
  const out = []; let cur = null, m;
  for (const l of c.split('\n')) {
    if ((m = l.match(/^Q:\s*(.*)/i))) { cur = {q: m[1], a: ''}; out.push(cur); }
    else if ((m = l.match(/^A:\s*(.*)/i)) && cur) cur.a = m[1];
    else if (cur && l.trim()) { if (cur.a) cur.a += '\n' + l; else cur.q += '\n' + l; }
  }
  return out.filter(x => x.a);
}
function quiz(c) {
  const out = []; let cur = null, m;
  for (const l of c.split('\n')) {
    if ((m = l.match(/^Q:\s*(.*)/i))) { cur = {q: m[1], o: [], a: ''}; out.push(cur); }
    else if (!cur) continue;
    else if ((m = l.match(/^([a-d])[).]\s*(.*)/i))) cur.o.push(m[2]);
    else if ((m = l.match(/^ANS:\s*(.*)/i))) cur.a = m[1].trim();
  }
  return out.filter(x => x.a && x.o.length);
}

// ---- Study sessions ----
let S = null;
function start(a, id) {
  const f = FILES[a], e = f.entries.find(x => x.id === id), key = K(f, e);
  if (e.kind === 'quiz') {
    S = {type: 'quiz', a, id, key, items: shuf(quiz(e.content)), i: 0, score: 0, wrong: [], pick: null};
  } else {
    const bx = store.get('boxes', {});
    const items = cards(e.content).map((c, n) => ({...c, id: key + '/' + n}));
    items.sort((p, q) => (bx[p.id] || 1) - (bx[q.id] || 1) || Math.random() - .5);
    S = {type: 'cards', a, id, key, items, i: 0, flip: false, ok: 0, miss: []};
  }
  if (!S.items.length) { S = null; alert('Walang nabasang tamang format sa entry na ito.'); return; }
  draw();
}
const flip = () => { S.flip = true; draw(); };
function rate(ok) {
  const c = S.items[S.i], bx = store.get('boxes', {});
  bx[c.id] = ok ? Math.min((bx[c.id] || 1) + 1, 3) : 1; store.set('boxes', bx);
  if (ok) S.ok++; else S.miss.push(c);
  S.i++; S.flip = false; draw();
}
function again() { S.items = shuf(S.miss); S.miss = []; S.i = 0; S.ok = 0; S.flip = false; draw(); }
function pick(n) { const q = S.items[S.i]; S.pick = n; if (n === ci(q)) S.score++; else S.wrong.push(q); draw(); }
const next = () => { S.i++; S.pick = null; draw(); };

function draw() {
  const back = `<a class="back" href="#${S.a}">‹ Lumabas</a>`;
  if (S.type === 'cards') {
    const c = S.items[S.i];
    if (!c) {
      touch();
      app.innerHTML = `${back}<div class="panel center"><h2>Tapos na! 🎉</h2><p>Alam: ${S.ok} · Uulitin: ${S.miss.length}</p>${S.miss.length ? '<button class="btn" onclick="again()">Ulitin ang mga mintis</button>' : ''}</div>`;
      return;
    }
    app.innerHTML = `${back}<p class="muted">Card ${S.i + 1}/${S.items.length}</p>
<div class="card" onclick="flip()"><small>${S.flip ? 'SAGOT' : 'TANONG'}</small>${esc(S.flip ? c.a : c.q).replace(/\n/g, '<br>')}</div>
${S.flip ? '<div class="row"><button class="btn bad" onclick="rate(0)">Ulitin</button><button class="btn good" onclick="rate(1)">Alam ko</button></div>' : '<p class="muted center">I-tap ang card para makita ang sagot</p>'}`;
    return;
  }
  const q = S.items[S.i];
  if (!q) {
    const pct = Math.round(S.score * 100 / S.items.length), b = store.get('best', {});
    b[S.key] = Math.max(b[S.key] || 0, pct); store.set('best', b); touch();
    app.innerHTML = `${back}<div class="panel"><h2 class="center">Score: ${S.score}/${S.items.length} (${pct}%)</h2>
${S.wrong.length ? '<h3>Balikan ito:</h3>' + S.wrong.map(w => `<p><b>${esc(w.q)}</b><br>Sagot: ${esc(w.o[ci(w)])}</p>`).join('') : '<p class="center">Perfect! 👏</p>'}
<button class="btn" onclick="start(${S.a},${S.id})">Ulitin ang quiz</button></div>`;
    return;
  }
  const c = ci(q), done = S.pick !== null;
  app.innerHTML = `${back}<p class="muted">Tanong ${S.i + 1}/${S.items.length} · Score ${S.score}</p>
<div class="card" style="cursor:default">${esc(q.q)}</div>
${q.o.map((t, n) => `<button class="opt ${done ? (n === c ? 'ok' : n === S.pick ? 'no' : '') : ''}" ${done ? 'disabled' : ''} onclick="pick(${n})">${'abcd'[n]}) ${esc(t)}</button>`).join('')}
${done ? '<button class="btn" onclick="next()">Susunod ›</button>' : ''}`;
}

// ---- Iba pang aksyon ----
function toggleDone(a, id) {
  const d = store.get('done', {}), k = K(FILES[a], {id});
  d[k] = !d[k]; if (d[k]) touch(); store.set('done', d); render();
}
function speak(a, id) {
  if (!window.speechSynthesis) { alert('Hindi suportado ng browser na ito ang pagbasa.'); return; }
  speechSynthesis.cancel();
  const e = FILES[a].entries.find(x => x.id === id);
  const u = new SpeechSynthesisUtterance(e.title + '. ' + e.content.replace(/[#*`]/g, ''));
  u.lang = 'fil-PH'; speechSynthesis.speak(u);
}
function search(t) {
  t = t.trim().toLowerCase(); const r = $('#res');
  if (!t) { r.innerHTML = ''; return; }
  const hits = [];
  FILES.forEach((f, a) => f.entries.forEach(e => {
    if ((e.title + ' ' + e.content).toLowerCase().includes(t))
      hits.push(`<a class="folder sm" href="#${a}/${e.id}">${esc(e.title)} <span>· ${esc(f.name)}</span></a>`);
  }));
  r.innerHTML = hits.join('') || '<p class="muted">Walang nahanap.</p>';
}

// ---- Pages ----
const folderInner = f => `<div class="ficon">${FOLDER}</div><div class="fi">
<div class="fname">${esc(f.name)}</div>
<div class="fmeta">${count(f.entries.length)} · ${kb(bytes(f.entries))} · ✓ ${dn(f)}/${f.entries.length}</div></div>`;

function render() {
  const [a, b] = location.hash.slice(1).split('/');
  const f = /^\d+$/.test(a) && FILES[a];
  if (!f) {
    const nd = FILES.reduce((n, x) => n + dn(x), 0);
    app.innerHTML = '<h1>Library</h1><p class="sub">Mga aralin at aklat, nakaayos ayon sa file.</p>' +
      `<div class="stats">🔥 ${streak()} araw na sunod-sunod · ✓ ${nd} natapos</div>
<input class="search" placeholder="Maghanap sa lahat ng entry…" oninput="search(this.value)"><div id="res"></div>` +
      (FILES.length ? FILES.map((f, i) => `<a class="folder" href="#${i}">${folderInner(f)}</a>`).join('')
        : '<p class="muted">Wala pang file. Utusan si Claude na gumawa.</p>');
    return;
  }
  const e = b !== undefined ? f.entries.find(x => String(x.id) === b) : null;
  if (e) {
    const k = K(f, e), isDone = store.get('done', {})[k], best = store.get('best', {})[k];
    const special = e.kind === 'flashcards' || e.kind === 'quiz';
    const info = e.kind === 'quiz' ? quiz(e.content).length + ' tanong' : cards(e.content).length + ' card';
    app.innerHTML = `<a class="back" href="#${a}">‹ ${esc(f.name)}</a>
<div class="panel read"><span class="tag">${esc(e.kind)}</span><h2>${esc(e.title)}</h2>
${special ? `<p class="muted">${info}${best != null ? ' · Pinakamataas: ' + best + '%' : ''}</p>
<button class="btn" onclick="start(${a},${e.id})">${e.kind === 'quiz' ? '📝 Simulan ang Quiz' : '🃏 Simulan ang Flashcards'}</button>`
      : `<div class="row"><button class="btn" onclick="speak(${a},${e.id})">🔊 Basahin</button>
<button class="btn ${isDone ? 'good' : ''}" onclick="toggleDone(${a},${e.id})">${isDone ? '✓ Tapos na' : 'Markahang tapos'}</button></div>
<div class="md">${md(e.content)}</div>`}</div>`;
    return;
  }
  app.innerHTML = `<a class="back" href="#">‹ Library</a>
<div class="panel"><div class="folder">${folderInner(f)}</div>
${f.description ? `<p class="muted">${esc(f.description)}</p>` : ''}
<div class="grid">${f.entries.map(e => `<a class="tile" href="#${a}/${e.id}">
<div class="thumb">${ICON[e.kind] || DOC}${store.get('done', {})[K(f, e)] ? '<span class="chk">✓</span>' : ''}</div><div class="name">${esc(e.title)}</div></a>`).join('')}</div>
${f.entries.length ? '' : '<p class="muted">Walang laman pa.</p>'}</div>`;
}
addEventListener('hashchange', () => {
  S = null; if (window.speechSynthesis) speechSynthesis.cancel();
  render(); scrollTo(0, 0);
});
render();
</script></body></html>"""


def build_page() -> HTMLResponse:
    files = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(DATA_DIR.glob("*.json"))
    ]
    payload = json.dumps(files, ensure_ascii=False).replace("</", "<\\/")
    return HTMLResponse(PAGE.replace("__DATA__", payload))


@mcp.custom_route("/", methods=["GET"])
async def home(request: Request):
    return build_page()


@mcp.custom_route("/offline", methods=["GET"])
async def offline(request: Request):
    """Parehong page. Ito ang sine-save ng app sa phone."""
    return build_page()


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
