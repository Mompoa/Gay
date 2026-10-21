import os
import re
import json
import threading
import uuid
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


@mcp.tool()
def delete_file(file: str) -> str:
    """Delete a whole file and everything inside it. Cannot be undone.
    Only use when the user explicitly asks to delete the file."""
    with LOCK:
        p = path_of(file)
        if not p.exists():
            return f"Walang file na '{file}'."
        p.unlink()
    return f"Nabura ang file na '{file}' at lahat ng laman nito."


# ---------- Schedule ----------
SCHED_PATH = DATA_DIR / "schedule.sched"
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def load_sched() -> dict:
    if SCHED_PATH.exists():
        return json.loads(SCHED_PATH.read_text(encoding="utf-8"))
    return {"items": [], "removed": []}


def save_sched(d: dict):
    SCHED_PATH.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def norm_time(t: str):
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", t or "")
    if not m or int(m[1]) > 23 or int(m[2]) > 59:
        return None
    return f"{int(m[1]):02d}:{m[2]}"


def norm_day(d: str):
    d = (d or "").strip()[:3].capitalize()
    return DAYS.index(d) if d in DAYS else None


@mcp.tool()
def add_class(title: str, days: list[str], start: str, end: str, place: str = "") -> str:
    """Add a class to the weekly schedule (one block per day).
    days: any of 'Mon','Tue','Wed','Thu','Fri','Sat','Sun'.
    start and end: 24-hour 'HH:MM', for example '13:30'.
    place: room name or 'ONLINE'."""
    s, e = norm_time(start), norm_time(end)
    idx = [norm_day(x) for x in days]
    if s is None or e is None or e <= s:
        return "Mali ang oras. Gamitin ang HH:MM (24-hour) at dapat mas huli ang end."
    if not idx or None in idx:
        return "Mali ang araw. Gamitin ang Mon, Tue, Wed, Thu, Fri, Sat, Sun."
    chosen = sorted(set(idx))
    with LOCK:
        d = load_sched()
        ids = []
        for i in chosen:
            cid = uuid.uuid4().hex[:8]
            d["items"].append(
                {"id": cid, "title": title, "day": i, "start": s, "end": e, "place": place}
            )
            ids.append(cid)
        save_sched(d)
    return f"Naidagdag ang '{title}' ({', '.join(DAYS[i] for i in chosen)} {s}-{e}). IDs: {', '.join(ids)}"


@mcp.tool()
def list_schedule() -> str:
    """List all classes in the weekly schedule with their ids."""
    items = sorted(load_sched()["items"], key=lambda x: (x["day"], x["start"]))
    rows = [
        f"[{x['id']}] {DAYS[x['day']]} {x['start']}-{x['end']} {x['title']}"
        + (f" ({x['place']})" if x.get("place") else "")
        for x in items
    ]
    return "\n".join(rows) or "Wala pang schedule."


@mcp.tool()
def update_class(
    class_id: str,
    title: str = "",
    day: str = "",
    start: str = "",
    end: str = "",
    place: str = "",
) -> str:
    """Edit one class block by id (see list_schedule). Leave a field empty to keep it.
    day is one of Mon..Sun. start and end are 24-hour 'HH:MM'."""
    with LOCK:
        d = load_sched()
        for x in d["items"]:
            if x["id"] != class_id:
                continue
            nd = norm_day(day) if day else x["day"]
            ns = norm_time(start) if start else x["start"]
            ne = norm_time(end) if end else x["end"]
            if nd is None or ns is None or ne is None or ne <= ns:
                return "Mali ang araw o oras."
            x.update(
                {
                    "title": title or x["title"],
                    "day": nd,
                    "start": ns,
                    "end": ne,
                    "place": place or x.get("place", ""),
                }
            )
            save_sched(d)
            return f"Na-update ang klase {class_id}."
    return f"Walang klase na id {class_id}."


@mcp.tool()
def delete_class(class_id: str) -> str:
    """Delete one class block by id (see list_schedule)."""
    with LOCK:
        d = load_sched()
        before = len(d["items"])
        d["items"] = [x for x in d["items"] if x["id"] != class_id]
        if len(d["items"]) == before:
            return f"Walang klase na id {class_id}."
        d["removed"].append(class_id)
        save_sched(d)
    return "Nabura ang klase."


@mcp.tool()
def clear_schedule() -> str:
    """Delete every class in the schedule. Only use when the user explicitly asks."""
    with LOCK:
        d = load_sched()
        d["removed"] += [x["id"] for x in d["items"]]
        d["items"] = []
        save_sched(d)
    return "Nabura ang buong schedule."


# ---------- Website (para sa iyo) ----------
PAGE = r"""<!doctype html>
<html lang="fil"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Library</title>
<style>
:root{color-scheme:dark;--bg:#0e1420;--panel:#20283a;--purple:#4527a0;--tile:#e6eaf5;--blue:#4285f4;--fg:#eef;--muted:#8b93a7;--chip:#2d3650;--line:rgba(127,127,127,.25)}
[data-theme=light]{color-scheme:light;--bg:#f4f6fb;--panel:#e3e8f4;--fg:#1a2033;--muted:#5b6478;--chip:#d3daea}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font-family:"Space Grotesk",system-ui,sans-serif;min-height:100vh}
.wrap{max-width:720px;margin:0 auto;padding:28px 16px 110px}
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
a.btn{display:block;text-align:center;text-decoration:none}
.tw{overflow-x:auto;margin:12px 0}
.tw table{border-collapse:collapse;width:100%;min-width:260px}
.tw th,.tw td{border:1px solid var(--line);padding:9px 11px;text-align:left;vertical-align:top;line-height:1.5}
.tw th{background:var(--purple);color:#fff}
.btn.sm{padding:8px 12px;font-size:13px}
.fab{position:fixed;right:18px;bottom:22px;width:60px;height:60px;border-radius:20px;background:var(--blue);color:#fff;font-size:32px;display:flex;align-items:center;justify-content:center;text-decoration:none;box-shadow:0 6px 18px rgba(0,0,0,.4);z-index:5}
.lbl{display:block;font-weight:700;margin-top:6px}
.inp{width:100%;padding:13px 14px;border-radius:14px;border:0;background:var(--bg);color:var(--fg);font:inherit;margin:4px 0 12px}
.ta{min-height:260px;resize:vertical}
.days{display:flex;flex-wrap:wrap;gap:8px;margin:6px 0 12px}
.chip{background:var(--bg);padding:8px 12px;border-radius:12px;display:flex;gap:6px;align-items:center}
.row>div{flex:1}
.sw{overflow-x:auto;background:var(--panel);border-radius:20px;padding:8px 4px;margin-bottom:14px}
.sg{display:grid;grid-template-columns:44px repeat(7,minmax(86px,1fr));min-width:690px}
.dh{text-align:center;padding:10px 0;font-weight:700}
.dh.td{background:var(--blue);color:#fff;border-radius:10px 10px 0 0}
.dh.we{opacity:.6}
.axis{position:relative}
.hr{position:absolute;right:6px;font-size:12px;color:var(--muted)}
.dcol{position:relative;border-left:1px solid var(--line);background-image:linear-gradient(var(--line) 1px,transparent 1px);background-size:100% 64px}
.dcol.we{background-color:rgba(127,127,127,.12)}
.dcol.td{background-color:rgba(66,133,244,.14)}
.blk{position:absolute;left:3px;right:3px;border-radius:12px;border:2px solid #fff;color:#fff;padding:4px 6px;font-size:11px;line-height:1.35;overflow:hidden;text-decoration:none;display:block}
.blk b{display:block;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.blk span{display:block;opacity:.9;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
</style></head><body>
<button class="th" onclick="theme()">◐</button>
<div class="wrap" id="app"></div>
<script>
const SERVER = __DATA__;
const SSERVER = __SCHED__;
let FILES = [];
let SCHED = [];
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

// ---- Data na naka-save sa phone (hindi nabubura kapag nag-redeploy ang server) ----
const hash32 = s => { let x = 0; for (const c of s) x = (x * 31 + c.charCodeAt(0)) | 0; return x; };
const uid = () => Math.random().toString(36).slice(2, 9);
const persist = () => store.set('db', FILES);
const persistS = () => store.set('sched', SCHED);
function loadLocal() {
  const gone = store.get('gone', {});
  const db = store.get('db', []);
  db.forEach(f => { f.next = f.next || Math.max(0, ...f.entries.map(e => e.id)) + 1; });
  SERVER.forEach(sf => {
    const live = sf.entries.filter(se => gone[sf.name + '|' + se.title] !== hash32(se.content));
    let lf = db.find(x => x.name === sf.name);
    if (!lf) {
      if (!live.length && (gone['F|' + sf.name] || sf.entries.length)) return;
      lf = {name: sf.name, description: sf.description || '', entries: [], next: 1};
      db.push(lf); delete gone['F|' + sf.name];
    }
    live.forEach(se => {
      const sig = hash32(se.content);
      const le = lf.entries.find(x => x.src === se.title || (x.src === undefined && x.title === se.title));
      if (!le) lf.entries.push({id: lf.next++, kind: se.kind, title: se.title, content: se.content, src: se.title, sig});
      else { if (!le.edited) { le.kind = se.kind; le.content = se.content; } le.src = se.title; le.sig = sig; }
    });
  });
  FILES = db; store.set('gone', gone); persist();
  loadSched();
}
function loadSched() {
  let L = store.get('sched', []);
  const g = store.get('sgone', {}), rm = new Set(SSERVER.removed || []);
  L = L.filter(x => !(x.src && rm.has(x.src) && !x.edited));
  (SSERVER.items || []).forEach(s => {
    if (rm.has(s.id)) return;
    const sig = hash32(JSON.stringify([s.title, s.day, s.start, s.end, s.place || '']));
    if (g[s.id] === sig) return;
    const l = L.find(x => x.src === s.id);
    if (!l) L.push({id: uid(), title: s.title, day: s.day, start: s.start, end: s.end, place: s.place || '', src: s.id, sig});
    else { if (!l.edited) Object.assign(l, {title: s.title, day: s.day, start: s.start, end: s.end, place: s.place || ''}); l.sig = sig; }
  });
  SCHED = L; persistS();
}
function tomb(g, f, e) {
  const src = e.src === undefined ? e.title : e.src;
  if (src !== null) g[f.name + '|' + src] = e.sig !== undefined ? e.sig : hash32(e.content);
}
function delEntry(a, id) {
  if (!confirm('Burahin ang entry na ito sa phone?')) return;
  const f = FILES[a], e = f.entries.find(x => x.id === id), g = store.get('gone', {});
  tomb(g, f, e); store.set('gone', g);
  f.entries = f.entries.filter(x => x.id !== id); persist(); location.hash = '#' + a;
}
function delFile(a) {
  if (!confirm('Burahin ang buong file at lahat ng laman nito sa phone?')) return;
  const f = FILES[a], g = store.get('gone', {});
  f.entries.forEach(e => tomb(g, f, e));
  g['F|' + f.name] = 1; store.set('gone', g);
  FILES.splice(a, 1); persist(); location.hash = '';
}

// ---- Mini markdown ----
function md(src) {
  const inl = s => esc(s).replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>').replace(/\*([^*]+)\*/g, '<i>$1</i>');
  const out = []; let list = null, tbl = null, m;
  const close = () => {
    if (list) { out.push('</' + list + '>'); list = null; }
    if (tbl) {
      const rows = tbl.filter(r => !r.every(c => /^:?-+:?$/.test(c)));
      out.push('<div class="tw"><table>' + rows.map((r, i) => '<tr>' + r.map(c => `<${i ? 'td' : 'th'}>${inl(c)}</${i ? 'td' : 'th'}>`).join('') + '</tr>').join('') + '</table></div>');
      tbl = null;
    }
  };
  for (const l of src.split('\n')) {
    if (/^\s*\|/.test(l)) { if (!tbl) { close(); tbl = []; } tbl.push(l.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim())); }
    else if ((m = l.match(/^(#{1,3})\s+(.*)/))) { close(); const h = m[1].length + 1; out.push(`<h${h}>${inl(m[2])}</h${h}>`); }
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
  const [a, b, c] = location.hash.slice(1).split('/');
  if (a === 'schedule') return schedView();
  if (a === 'sched') return schedForm(b === 'edit' ? c : '');
  if (a === 'newfile') return fileForm();
  if (a === 'new' && FILES[b]) return entryForm(+b, 0);
  if (a === 'edit' && FILES[b]) return entryForm(+b, +c);
  const f = /^\d+$/.test(a) && FILES[a];
  if (!f) {
    const nd = FILES.reduce((n, x) => n + dn(x), 0);
    app.innerHTML = '<h1>Library</h1><p class="sub">Mga aralin at aklat, nakaayos ayon sa file.</p>' +
      `<div class="stats">🔥 ${streak()} araw na sunod-sunod · ✓ ${nd} natapos</div>
<input class="search" placeholder="Maghanap sa lahat ng entry…" oninput="search(this.value)"><div id="res"></div>
<a class="folder" href="#schedule"><div class="ficon">📅</div><div class="fi"><div class="fname">Schedule</div><div class="fmeta">${SCHED.length} klase</div></div></a>` +
      (FILES.length ? FILES.map((f, i) => `<a class="folder" href="#${i}">${folderInner(f)}</a>`).join('')
        : '<p class="muted">Wala pang file. Utusan si Claude na gumawa, o pindutin ang ＋.</p>') + '<a class="fab" href="#newfile">＋</a>';
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
<div class="md">${md(e.content)}</div>`}<a class="btn" href="#edit/${a}/${e.id}">✏️ I-edit</a><button class="btn bad" onclick="delEntry(${a},${e.id})">🗑 Burahin ang entry</button></div>`;
    return;
  }
  app.innerHTML = `<a class="back" href="#">‹ Library</a>
<div class="panel"><div class="folder">${folderInner(f)}</div>
${f.description ? `<p class="muted">${esc(f.description)}</p>` : ''}
<div class="grid">${f.entries.map(e => `<a class="tile" href="#${a}/${e.id}">
<div class="thumb">${ICON[e.kind] || DOC}${store.get('done', {})[K(f, e)] ? '<span class="chk">✓</span>' : ''}</div><div class="name">${esc(e.title)}</div></a>`).join('')}</div>
${f.entries.length ? '' : '<p class="muted">Walang laman pa.</p>'}<button class="btn bad" onclick="delFile(${a})">🗑 Burahin ang file</button></div><a class="fab" href="#new/${a}">＋</a>`;
}
// ---- Mga form (reviewer) at Schedule ----
const KINDS = ['lesson', 'book', 'flashcards', 'quiz'];
const TPL = {
  reviewer: '# Reviewer: (Paksa)\n\n## Mahahalagang Termino\n| Termino | Kahulugan |\n|---|---|\n| Termino 1 | kahulugan |\n| Termino 2 | kahulugan |\n\n## Mga Dapat Tandaan\n- \n- \n\n## Mabilis na Review\n',
  flashcards: 'Q: Tanong 1\nA: Sagot 1\n\nQ: Tanong 2\nA: Sagot 2\n',
  quiz: 'Q: Tanong 1\na) Pagpipilian A\nb) Pagpipilian B\nc) Pagpipilian C\nd) Pagpipilian D\nANS: a\n'
};
function entryForm(fi, id) {
  const f = FILES[fi], e = id ? f.entries.find(x => x.id === id) : null;
  if (id && !e) { location.hash = '#' + fi; return; }
  app.innerHTML = `<a class="back" href="#${fi}${e ? '/' + id : ''}">‹ Bumalik</a>
<div class="panel"><h2>${e ? 'I-edit ang entry' : 'Bagong entry sa ' + esc(f.name)}</h2>
<label class="lbl">Pamagat</label><input id="f_title" class="inp" value="${e ? esc(e.title) : ''}" placeholder="Hal. Reviewer: Module 1">
<label class="lbl">Uri</label><select id="f_kind" class="inp">${KINDS.map(k => `<option ${e && e.kind === k ? 'selected' : ''}>${k}</option>`).join('')}</select>
<label class="lbl">Nilalaman</label>
<div class="row"><button class="btn sm" onclick="tpl('reviewer')">📝 Reviewer</button><button class="btn sm" onclick="tpl('flashcards')">🃏 Flashcards</button><button class="btn sm" onclick="tpl('quiz')">❓ Quiz</button></div>
<textarea id="f_body" class="inp ta" placeholder="I-type o i-paste ang reviewer dito (Markdown ang format)">${e ? esc(e.content) : ''}</textarea>
<button class="btn" style="width:100%" onclick="saveEntry(${fi},${id || 0})">💾 I-save</button></div>`;
}
function tpl(k) {
  const b = $('#f_body');
  if (b.value.trim() && !confirm('Papalitan ang nakasulat na. Ituloy?')) return;
  b.value = TPL[k]; $('#f_kind').value = k === 'reviewer' ? 'lesson' : k;
}
function saveEntry(fi, id) {
  const t = $('#f_title').value.trim(), c = $('#f_body').value, k = $('#f_kind').value, f = FILES[fi];
  if (!t || !c.trim()) { alert('Lagyan ng pamagat at nilalaman.'); return; }
  if (id) {
    const e = f.entries.find(x => x.id === id);
    Object.assign(e, {title: t, content: c, kind: k, edited: true});
  } else f.entries.push({id: f.next++, kind: k, title: t, content: c, src: null});
  persist(); location.hash = '#' + fi + (id ? '/' + id : '');
}
function fileForm() {
  app.innerHTML = `<a class="back" href="#">‹ Library</a><div class="panel"><h2>Bagong file</h2>
<label class="lbl">Pangalan</label><input id="n_name" class="inp" placeholder="Hal. Contemporary World">
<label class="lbl">Paglalarawan (hindi kailangan)</label><input id="n_desc" class="inp">
<button class="btn" style="width:100%" onclick="saveFile()">💾 Gumawa</button></div>`;
}
function saveFile() {
  const n = $('#n_name').value.trim();
  if (!n) { alert('Lagyan ng pangalan.'); return; }
  if (FILES.some(f => f.name.toLowerCase() === n.toLowerCase())) { alert('May ganyang pangalan na.'); return; }
  FILES.push({name: n, description: $('#n_desc').value.trim(), entries: [], next: 1}); persist();
  location.hash = '#' + (FILES.length - 1);
}

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const PAL = ['#4285f4', '#e8710a', '#188038', '#a142f4', '#d93025', '#12a4af'];
const mins = t => { const [h, m] = t.split(':').map(Number); return h * 60 + m; };
const t12 = t => { const [h, m] = t.split(':').map(Number); return (h % 12 || 12) + ':' + String(m).padStart(2, '0') + ' ' + (h >= 12 ? 'PM' : 'AM'); };
function schedView() {
  const L = SCHED, PX = 64, today = (new Date().getDay() + 6) % 7;
  let s0 = 7, e0 = 18;
  if (L.length) { s0 = Math.min(...L.map(x => Math.floor(mins(x.start) / 60))); e0 = Math.max(...L.map(x => Math.ceil(mins(x.end) / 60))); }
  const H = (e0 - s0) * PX;
  let axis = '';
  for (let h = s0; h < e0; h++) axis += `<div class="hr" style="top:${(h - s0) * PX + 2}px">${h % 12 || 12}${h < 12 ? 'a' : 'p'}</div>`;
  const heads = DAYS.map((d, i) => `<div class="dh ${i > 4 ? 'we' : ''} ${i === today ? 'td' : ''}">${d}</div>`).join('');
  const cols = DAYS.map((d, i) => `<div class="dcol ${i > 4 ? 'we' : ''} ${i === today ? 'td' : ''}" style="height:${H}px">` +
    L.filter(x => x.day === i).map(x => {
      const top = (mins(x.start) - s0 * 60) * PX / 60, ht = Math.max((mins(x.end) - mins(x.start)) * PX / 60, 30);
      return `<a class="blk" style="top:${top}px;height:${ht}px;background:${PAL[Math.abs(hash32(x.title)) % PAL.length]}" href="#sched/edit/${x.id}">
<b>${esc(x.title)}</b><span>${t12(x.start)}</span><span>${esc(x.place || '')}</span></a>`;
    }).join('') + '</div>').join('');
  const list = [...L].sort((p, q) => p.day - q.day || mins(p.start) - mins(q.start)).map(x =>
    `<a class="folder sm" href="#sched/edit/${x.id}">${DAYS[x.day]} ${t12(x.start)}–${t12(x.end)} · ${esc(x.title)}${x.place ? ' <span>· ' + esc(x.place) + '</span>' : ''}</a>`).join('');
  app.innerHTML = `<a class="back" href="#">‹ Library</a><h1>Weekly View</h1>
${L.length ? '' : '<p class="muted">Wala pang schedule. Utusan si Claude na gumawa, o pindutin ang ＋.</p>'}
<div class="sw"><div class="sg">
<div></div>${heads}
<div class="axis" style="height:${H}px">${axis}</div>${cols}
</div></div>${list}
${L.length ? '<button class="btn bad" style="width:100%;margin-top:14px" onclick="clearSched()">🗑 Burahin ang buong schedule</button>' : ''}
<a class="fab" href="#sched/new">＋</a>`;
}
function schedForm(id) {
  const x = id ? SCHED.find(s => s.id === id) : null;
  if (id && !x) { location.hash = '#schedule'; return; }
  app.innerHTML = `<a class="back" href="#schedule">‹ Schedule</a><div class="panel"><h2>${x ? 'I-edit ang klase' : 'Bagong klase'}</h2>
<label class="lbl">Asignatura</label><input id="s_title" class="inp" value="${x ? esc(x.title) : ''}" placeholder="Hal. Mathematics">
<label class="lbl">Araw</label><div class="days">${DAYS.map((d, i) => `<label class="chip"><input type="checkbox" value="${i}" ${x && x.day === i ? 'checked' : ''}> ${d}</label>`).join('')}</div>
<div class="row"><div><label class="lbl">Simula</label><input id="s_start" type="time" class="inp" value="${x ? x.start : '08:00'}"></div>
<div><label class="lbl">Tapos</label><input id="s_end" type="time" class="inp" value="${x ? x.end : '09:00'}"></div></div>
<label class="lbl">Lugar / Room</label><input id="s_place" class="inp" value="${x ? esc(x.place || '') : ''}" placeholder="Hal. ONLINE o COM LAB">
<button class="btn" style="width:100%" onclick="saveClass('${id || ''}')">💾 I-save</button>
${x ? `<button class="btn bad" style="width:100%" onclick="delClass('${id}')">🗑 Burahin</button>` : ''}</div>`;
}
function saveClass(id) {
  const t = $('#s_title').value.trim(), st = $('#s_start').value, en = $('#s_end').value, pl = $('#s_place').value.trim();
  const ds = [...document.querySelectorAll('.days input:checked')].map(i => +i.value);
  if (!t || !st || !en || !ds.length) { alert('Kumpletuhin ang asignatura, araw, at oras.'); return; }
  if (mins(en) <= mins(st)) { alert('Dapat mas huli ang oras ng tapos kaysa sa simula.'); return; }
  ds.forEach((d, n) => {
    if (id && n === 0) Object.assign(SCHED.find(s => s.id === id), {title: t, day: d, start: st, end: en, place: pl, edited: true});
    else SCHED.push({id: uid(), title: t, day: d, start: st, end: en, place: pl, src: null});
  });
  persistS(); location.hash = '#schedule';
}
function tombS(x) { if (x.src) { const g = store.get('sgone', {}); g[x.src] = x.sig; store.set('sgone', g); } }
function delClass(id) {
  if (!confirm('Burahin ang klase na ito?')) return;
  tombS(SCHED.find(s => s.id === id));
  SCHED = SCHED.filter(s => s.id !== id); persistS(); location.hash = '#schedule';
}
function clearSched() {
  if (!confirm('Burahin ang BUONG schedule sa phone?')) return;
  SCHED.forEach(tombS); SCHED = []; persistS(); render();
}

addEventListener('hashchange', () => {
  S = null; if (window.speechSynthesis) speechSynthesis.cancel();
  render(); scrollTo(0, 0);
});
loadLocal();
render();
</script></body></html>"""


def build_page() -> HTMLResponse:
    files = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(DATA_DIR.glob("*.json"))
    ]
    payloads = {
        "__DATA__": json.dumps(files, ensure_ascii=False).replace("</", "<\\/"),
        "__SCHED__": json.dumps(load_sched(), ensure_ascii=False).replace("</", "<\\/"),
    }
    return HTMLResponse(re.sub(r"__DATA__|__SCHED__", lambda m: payloads[m.group(0)], PAGE))


@mcp.custom_route("/", methods=["GET"])
async def home(request: Request):
    return build_page()


@mcp.custom_route("/offline", methods=["GET"])
async def offline(request: Request):
    """Parehong page. Ito ang sine-save ng app sa phone."""
    return build_page()


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
