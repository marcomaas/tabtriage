"""TabTriage FastAPI Backend."""

import json
import logging
import platform
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from notion_client import (
    append_to_project,
    create_backlog_card,
    create_link,
    create_task,
    get_projects,
)
from summarizer import summarize_tab, cluster_tabs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# Config
CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.json"
config = json.loads(CONFIG_PATH.read_text())
DB_PATH = config["db_path"]
TRIAGE_HTML_PATH = config["triage_html_path"]
MAX_CONTENT = config["max_content_length"]

app = FastAPI(title="TabTriage", version="2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ── Models ───────────────────────────────────────────────────

class TabData(BaseModel):
    url: str
    title: str
    content: Optional[str] = None
    favicon: Optional[str] = None
    og_image: Optional[str] = None
    og_description: Optional[str] = None

class CaptureRequest(BaseModel):
    window_title: Optional[str] = None
    tabs: list[TabData]

class TriageItem(BaseModel):
    tab_id: int
    category: Optional[str] = None
    project_id: Optional[str] = None
    user_note: Optional[str] = None
    tags: Optional[list[str]] = None
    starred: Optional[bool] = None
    notion_target: Optional[str] = None  # links | parken | project | todo-today | todo-someday

class BulkTriageRequest(BaseModel):
    items: list[TriageItem]

class StarRequest(BaseModel):
    tab_id: int
    starred: bool


# ── Capture ──────────────────────────────────────────────────

HOSTNAME = platform.node().split(".")[0]  # e.g. "iMac" from "iMac.fritz.box"


@app.post("/api/capture")
def capture_tabs(req: CaptureRequest):
    """Receive tabs from extension, save to DB, trigger summarization."""
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO sessions (window_title, hostname) VALUES (?, ?)",
            (req.window_title, HOSTNAME),
        )
        session_id = cur.lastrowid

        seen_urls = set()
        tab_ids = []
        skipped = 0
        for tab in req.tabs:
            # Skip TabTriage's own page
            if "TabTriage/index.html" in tab.url:
                skipped += 1
                continue

            # Deduplicate within this session (same URL = same tab)
            if tab.url in seen_urls:
                skipped += 1
                continue
            seen_urls.add(tab.url)

            # Skip if this URL was already captured recently (within last 24h)
            existing = db.execute(
                """SELECT id FROM tabs WHERE url = ? AND captured_at > datetime('now', '-1 day')
                   ORDER BY id DESC LIMIT 1""",
                (tab.url,),
            ).fetchone()
            if existing:
                skipped += 1
                continue

            # Parse content from new JSON format or plain text
            content = None
            og_image = tab.og_image
            og_description = tab.og_description
            media_json = None

            if tab.content:
                try:
                    parsed = json.loads(tab.content)
                    content = parsed.get("text", "")[:MAX_CONTENT]
                    og_image = og_image or parsed.get("og_image")
                    og_description = og_description or parsed.get("og_description")
                    if parsed.get("media"):
                        media_json = json.dumps(parsed["media"])
                except (json.JSONDecodeError, AttributeError):
                    content = tab.content[:MAX_CONTENT]

            cur = db.execute(
                """INSERT INTO tabs (session_id, url, title, content, favicon, og_image, og_description, media)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_id, tab.url, tab.title, content, tab.favicon, og_image, og_description, media_json),
            )
            tab_ids.append(cur.lastrowid)

        if not tab_ids:
            # All tabs were duplicates - remove empty session
            db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            return {"session_id": None, "tab_count": 0, "skipped": skipped, "status": "all_duplicates"}

    # Summarize + cluster in background
    thread = threading.Thread(target=_summarize_and_cluster, args=(session_id, tab_ids))
    thread.start()

    # Generate triage page immediately (with pending summaries)
    _generate_triage_html()

    return {"session_id": session_id, "tab_count": len(tab_ids), "skipped": skipped, "status": "captured"}


def _summarize_and_cluster(session_id: int, tab_ids: list[int]):
    """Background: summarize each tab, then cluster."""
    log.info("Summarizing session %d (%d tabs)...", session_id, len(tab_ids))

    with get_db() as db:
        for tab_id in tab_ids:
            row = db.execute("SELECT title, url, content FROM tabs WHERE id = ?", (tab_id,)).fetchone()
            if not row:
                continue

            log.info("  Summarizing: %s", row["title"][:80])
            result = summarize_tab(row["title"], row["url"], row["content"])
            tags_json = json.dumps(result.get("tags", [])) if result.get("tags") else None
            db.execute(
                "UPDATE tabs SET summary = ?, suggested_category = ?, tags = COALESCE(tags, ?) WHERE id = ?",
                (result["summary"], result["suggested_category"], tags_json, tab_id),
            )
            db.commit()

    _generate_triage_html()
    log.info("Session %d summarization complete. Starting clustering...", session_id)

    # Clustering pass
    with get_db() as db:
        tabs = db.execute(
            "SELECT id, title, url, summary FROM tabs WHERE session_id = ?", (session_id,)
        ).fetchall()
        tab_list = [dict(t) for t in tabs]

    projects = get_projects()
    clusters = cluster_tabs(tab_list, projects)

    if clusters:
        with get_db() as db:
            for c in clusters:
                db.execute(
                    "UPDATE tabs SET cluster_id = ?, cluster_label = ?, project_id = COALESCE(project_id, ?) WHERE id = ?",
                    (c["cluster_id"], c["cluster_label"], c.get("suggested_project_id"), c["tab_id"]),
                )

    log.info("Session %d clustering complete.", session_id)
    _generate_triage_html()


# ── Sessions & Tabs ─────────────────────────────────────────

@app.get("/api/sessions")
def list_sessions():
    with get_db() as db:
        rows = db.execute("""
            SELECT s.*, COUNT(t.id) as tab_count,
                   SUM(CASE WHEN t.triaged_at IS NOT NULL THEN 1 ELSE 0 END) as triaged_count
            FROM sessions s LEFT JOIN tabs t ON t.session_id = s.id
            GROUP BY s.id ORDER BY s.captured_at DESC
        """).fetchall()
        return [dict(r) for r in rows]


@app.get("/api/sessions/{session_id}/tabs")
def session_tabs(session_id: int):
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM tabs WHERE session_id = ? ORDER BY id", (session_id,)
        ).fetchall()
        return [_tab_dict(r) for r in rows]


# ── Close Tabs (Extension polls this) ───────────────────────
# IMPORTANT: These static routes MUST come before /api/tabs/{tab_id}

_pending_close_urls: list[str] = []

@app.get("/api/tabs/pending-close")
def pending_close():
    """Extension polls this to get URLs to close."""
    return {"urls": list(_pending_close_urls)}

@app.post("/api/tabs/confirm-close")
def confirm_close(req: dict):
    """Extension confirms a tab was closed."""
    url = req.get("url")
    if url and url in _pending_close_urls:
        _pending_close_urls.remove(url)
    return {"status": "ok"}

@app.post("/api/tabs/request-close-bulk")
def request_close_bulk(req: dict):
    """Mark multiple tab URLs for closing."""
    tab_ids = req.get("tab_ids", [])
    with get_db() as db:
        for tid in tab_ids:
            row = db.execute("SELECT url FROM tabs WHERE id = ?", (tid,)).fetchone()
            if row and row["url"] not in _pending_close_urls:
                _pending_close_urls.append(row["url"])
    return {"status": "queued", "count": len(tab_ids)}


# ── Tab detail routes (parametric, must come after static) ──

@app.get("/api/tabs/{tab_id}")
def get_tab(tab_id: int):
    with get_db() as db:
        row = db.execute("SELECT * FROM tabs WHERE id = ?", (tab_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Tab not found")
        return _tab_dict(row, include_content=True)


@app.get("/api/tabs/{tab_id}/content")
def get_tab_content(tab_id: int):
    """Return full text content for a tab."""
    with get_db() as db:
        row = db.execute("SELECT content FROM tabs WHERE id = ?", (tab_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Tab not found")
        return {"content": row["content"]}

@app.post("/api/tabs/{tab_id}/request-close")
def request_close(tab_id: int):
    """Mark a tab's URL for closing by the extension."""
    with get_db() as db:
        row = db.execute("SELECT url FROM tabs WHERE id = ?", (tab_id,)).fetchone()
        if row and row["url"] not in _pending_close_urls:
            _pending_close_urls.append(row["url"])
    return {"status": "queued"}

@app.post("/api/tabs/{tab_id}/star")
def toggle_star(tab_id: int, req: StarRequest):
    with get_db() as db:
        db.execute("UPDATE tabs SET starred = ? WHERE id = ?", (1 if req.starred else 0, tab_id))
    return {"tab_id": tab_id, "starred": req.starred}


def _tab_dict(row, include_content=False) -> dict:
    d = dict(row)
    if d.get("tags"):
        try:
            d["tags"] = json.loads(d["tags"])
        except:
            d["tags"] = []
    if d.get("media"):
        try:
            d["media"] = json.loads(d["media"])
        except:
            d["media"] = []
    if not include_content:
        d.pop("content", None)
        d["has_content"] = bool(row["content"])
    return d


# ── Triage (single + bulk) ──────────────────────────────────

@app.post("/api/triage")
def triage_single(item: TriageItem):
    """Save single triage decision."""
    return _triage_one(item)


@app.post("/api/triage/bulk")
def triage_bulk(req: BulkTriageRequest):
    """Save multiple triage decisions at once."""
    results = []
    for item in req.items:
        results.append(_triage_one(item))
    _generate_triage_html()
    return {"results": results, "count": len(results)}


def _triage_one(item: TriageItem) -> dict:
    with get_db() as db:
        row = db.execute("SELECT * FROM tabs WHERE id = ?", (item.tab_id,)).fetchone()
        if not row:
            return {"tab_id": item.tab_id, "error": "not found"}

        updates = []
        params = []

        if item.category is not None:
            updates.append("category = ?")
            params.append(item.category)
        if item.project_id is not None:
            updates.append("project_id = ?")
            params.append(item.project_id)
        if item.user_note is not None:
            updates.append("user_note = ?")
            params.append(item.user_note)
        if item.tags is not None:
            updates.append("tags = ?")
            params.append(json.dumps(item.tags))
        if item.starred is not None:
            updates.append("starred = ?")
            params.append(1 if item.starred else 0)

        if item.category is not None:
            updates.append("triaged_at = ?")
            params.append(datetime.now().isoformat())

        if updates:
            params.append(item.tab_id)
            db.execute(f"UPDATE tabs SET {', '.join(updates)} WHERE id = ?", params)

    # Route to Notion
    notion_url = None
    if item.notion_target and item.category != "dismiss":
        title = row["title"]
        url = row["url"]
        summary = row["summary"] or row["title"]
        content = row["content"]

        if item.notion_target == "links":
            notion_url = create_link(title, url, summary, content)
        elif item.notion_target == "parken":
            notion_url = create_backlog_card(title, summary, url)
        elif item.notion_target == "project" and item.project_id:
            ok = append_to_project(item.project_id, title, url, summary)
            notion_url = "appended" if ok else None
        elif item.notion_target == "todo-today":
            notion_url = create_task(title, url, summary, when="today")
        elif item.notion_target == "todo-someday":
            notion_url = create_task(title, url, summary, when="someday")

    return {"tab_id": item.tab_id, "status": "triaged", "notion_url": notion_url}


# ── Search ───────────────────────────────────────────────────

@app.get("/api/search")
def search_tabs(
    q: str = "",
    category: str = "",
    starred: bool = False,
    project_id: str = "",
    session_id: int = 0,
    tag: str = "",
):
    with get_db() as db:
        if q and len(q) >= 2:
            rows = db.execute(
                """SELECT t.*, snippet(tabs_fts, 0, '<b>', '</b>', '...', 40) as snippet
                   FROM tabs_fts
                   JOIN tabs t ON t.id = tabs_fts.rowid
                   WHERE tabs_fts MATCH ?
                   ORDER BY rank LIMIT 100""",
                (q,),
            ).fetchall()
        else:
            conditions = ["1=1"]
            params = []
            if category:
                conditions.append("(category = ? OR suggested_category = ?)")
                params.extend([category, category])
            if starred:
                conditions.append("starred = 1")
            if project_id:
                conditions.append("project_id = ?")
                params.append(project_id)
            if session_id:
                conditions.append("session_id = ?")
                params.append(session_id)

            rows = db.execute(
                f"SELECT *, '' as snippet FROM tabs WHERE {' AND '.join(conditions)} ORDER BY id DESC LIMIT 200",
                params,
            ).fetchall()

        return [_tab_dict(r) for r in rows]


# ── Notion Projects ──────────────────────────────────────────

@app.get("/api/notion/projects")
def notion_projects():
    return get_projects()


# ── Triage HTML Generation ───────────────────────────────────

def _generate_triage_html():
    """Generate/update the static triage HTML page."""
    with get_db() as db:
        sessions = db.execute(
            "SELECT * FROM sessions ORDER BY captured_at DESC"
        ).fetchall()

        all_data = []
        for s in sessions:
            tabs = db.execute(
                "SELECT * FROM tabs WHERE session_id = ? ORDER BY id", (s["id"],)
            ).fetchall()
            tab_list = []
            for t in tabs:
                td = dict(t)
                td.pop("content", None)
                td["has_content"] = bool(t["content"])
                if td.get("tags"):
                    try:
                        td["tags"] = json.loads(td["tags"])
                    except:
                        td["tags"] = []
                if td.get("media"):
                    try:
                        td["media"] = json.loads(td["media"])
                    except:
                        td["media"] = []
                tab_list.append(td)
            all_data.append({"session": dict(s), "tabs": tab_list})

    data_json = json.dumps(all_data, ensure_ascii=False, indent=None)
    template_path = Path(__file__).parent / "triage_template.html"
    template = template_path.read_text(encoding="utf-8")
    html = template.replace("/*DATA_PLACEHOLDER*/[]", data_json)

    Path(TRIAGE_HTML_PATH).parent.mkdir(parents=True, exist_ok=True)
    Path(TRIAGE_HTML_PATH).write_text(html, encoding="utf-8")
    log.info("Triage HTML updated: %s", TRIAGE_HTML_PATH)


if __name__ == "__main__":
    log.info("Starting TabTriage backend on port %d", config["backend_port"])
    _generate_triage_html()
    uvicorn.run(app, host=config.get("backend_host", "0.0.0.0"), port=config["backend_port"])
