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
STYLE = """
<style>
:root{color-scheme:dark}
body{font-family:system-ui,sans-serif;background:#111;color:#eee;margin:0;padding:16px;
max-width:720px;margin-inline:auto;line-height:1.6}
a{color:#7cc4ff;text-decoration:none}
.card{background:#1c1c1e;border-radius:12px;padding:14px 16px;margin:12px 0}
.tag{font-size:12px;background:#2d2d30;border-radius:6px;padding:2px 8px;margin-right:6px}
.txt{white-space:pre-wrap}
h1{font-size:24px}h2{font-size:18px;margin:0 0 6px}
.muted{color:#999}
</style>
"""


def page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title>{STYLE}</head><body>{body}</body></html>"
    )


@mcp.custom_route("/", methods=["GET"])
async def home(request: Request):
    cards = ""
    for p in sorted(DATA_DIR.glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        cards += (
            f"<div class='card'><h2><a href='/f/{p.stem}'>{html.escape(d['name'])}</a></h2>"
            f"<div class='muted'>{html.escape(d.get('description', ''))}</div>"
            f"<div class='muted'>{len(d['entries'])} entries</div></div>"
        )
    return page(
        "Library",
        "<h1>📚 Library</h1>" + (cards or "<p class='muted'>Wala pang file. Utusan si Claude na gumawa.</p>"),
    )


@mcp.custom_route("/f/{slug}", methods=["GET"])
async def view_file(request: Request):
    d = load(request.path_params["slug"])
    if not d:
        return page("Not found", "<h1>Walang ganyang file</h1><a href='/'>Balik</a>")
    body = f"<a href='/'>← Balik</a><h1>{html.escape(d['name'])}</h1>"
    body += f"<p class='muted'>{html.escape(d.get('description', ''))}</p>"
    for e in d["entries"]:
        body += (
            f"<div class='card'><span class='tag'>{html.escape(e['kind'])}</span>"
            f"<h2>{html.escape(e['title'])}</h2>"
            f"<div class='txt'>{html.escape(e['content'])}</div></div>"
        )
    if not d["entries"]:
        body += "<p class='muted'>Walang laman pa.</p>"
    return page(d["name"], body)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
