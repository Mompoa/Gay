import os
import re
import json
import html
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
    """Add a book or lesson to a file. kind must be 'book' or 'lesson'.
    The content is the full text of the book or lesson."""
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
# Isang self-contained page: nasa loob na ang lahat ng data at JavaScript,
# kaya gumagana rin ito offline kapag na-save sa phone.
PAGE = r"""<!doctype html>
<html lang="fil"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Library</title>
<style>
:root{color-scheme:dark;--bg:#0e1420;--panel:#20283a;--purple:#4527a0;--tile:#e6eaf5;--blue:#4285f4}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:#eef;font-family:"Space Grotesk",system-ui,sans-serif;min-height:100vh}
.wrap{max-width:720px;margin:0 auto;padding:28px 16px 60px}
h1{font-size:34px;letter-spacing:1px;margin:8px 0 6px}
.sub{color:#8b93a7;margin:0 0 22px;line-height:1.5}
.back{display:inline-block;color:#9db8ff;text-decoration:none;margin:0 0 14px;font-weight:600}
.panel{background:var(--panel);border-radius:28px;padding:16px;min-height:50vh}
.folder{display:flex;align-items:center;gap:16px;background:var(--purple);border-radius:28px;
padding:16px 18px;color:#fff;text-decoration:none;margin-bottom:14px}
.ficon{flex:none;width:68px;height:68px;border-radius:20px;background:rgba(255,255,255,.18);
display:flex;align-items:center;justify-content:center}
.fi{min-width:0}
.fname{font-size:22px;font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fmeta{font-size:15px;font-weight:700;margin-top:6px;opacity:.95}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:18px;padding:6px 2px}
.tile{text-decoration:none;color:#fff;text-align:center}
.thumb{background:var(--tile);border-radius:20px;aspect-ratio:4/5;display:flex;align-items:center;justify-content:center}
.name{margin-top:8px;font-weight:700;font-size:15px;word-break:break-word;display:-webkit-box;
-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.muted{color:#8b93a7}
.tag{font-size:12px;background:#2d3650;border-radius:8px;padding:3px 10px}
.read h2{font-size:26px;margin:12px 0}
.txt{white-space:pre-wrap;line-height:1.7;font-size:16px}
</style></head><body><div class="wrap" id="app"></div>
<script>
const FILES = __DATA__;
const app = document.getElementById('app');
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const enc = new TextEncoder();
const bytes = es => es.reduce((n, e) => n + enc.encode(e.title + e.content).length, 0);
const kb = n => n < 1024 ? n + ' B' : (n / 1024).toFixed(1) + ' KB';
const count = n => n + (n === 1 ? ' entry' : ' entries');
const DOC = '<svg viewBox="0 0 24 24" width="46" height="46"><path fill="#4285f4" d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zm2 16H8v-2h8v2zm0-4H8v-2h8v2zm-3-5V3.5L18.5 9H13z"/></svg>';
const BOOK = '<svg viewBox="0 0 24 24" width="46" height="46"><path fill="#f4a142" d="M18 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2zM6 4h5v8l-2.5-1.5L6 12V4z"/></svg>';
const FOLDER = '<svg viewBox="0 0 24 24" width="34" height="34"><path fill="#fff" d="M10 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-8l-2-2z"/></svg>';

const folderInner = f => `<div class="ficon">${FOLDER}</div><div class="fi">
<div class="fname">${esc(f.name)}</div>
<div class="fmeta">${count(f.entries.length)} · ${kb(bytes(f.entries))}</div></div>`;

function render() {
  const [a, b] = location.hash.slice(1).split('/');
  const f = FILES[a];
  if (!f) {
    app.innerHTML = '<h1>Library</h1><p class="sub">Mga aralin at aklat, nakaayos ayon sa file.</p>' +
      (FILES.length ? FILES.map((f, i) => `<a class="folder" href="#${i}">${folderInner(f)}</a>`).join('')
        : '<p class="muted">Wala pang file. Utusan si Claude na gumawa.</p>');
    return;
  }
  const e = b !== undefined ? f.entries.find(x => String(x.id) === b) : null;
  if (e) {
    app.innerHTML = `<a class="back" href="#${a}">‹ ${esc(f.name)}</a>
<div class="panel read"><span class="tag">${esc(e.kind)}</span><h2>${esc(e.title)}</h2>
<div class="txt">${esc(e.content)}</div></div>`;
    return;
  }
  app.innerHTML = `<a class="back" href="#">‹ Library</a>
<div class="panel"><div class="folder">${folderInner(f)}</div>
${f.description ? `<p class="muted">${esc(f.description)}</p>` : ''}
<div class="grid">${f.entries.map(e => `<a class="tile" href="#${a}/${e.id}">
<div class="thumb">${e.kind === 'book' ? BOOK : DOC}</div><div class="name">${esc(e.title)}</div></a>`).join('')}</div>
${f.entries.length ? '' : '<p class="muted">Walang laman pa.</p>'}</div>`;
}
addEventListener('hashchange', () => { render(); scrollTo(0, 0); });
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
