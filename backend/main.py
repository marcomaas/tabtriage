"""TabTriage FastAPI Backend."""

import asyncio
import json
import logging
import platform
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from notion_client import (
    append_to_project,
    create_backlog_card,
    create_link,
    create_task,
    get_projects,
)
from extractor import extract_content
from summarizer import summarize_tab, cluster_tabs, analyze_content

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


def _init_db():
    """Ensure all tables exist (handles migrations for new tables/columns)."""
    with get_db() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS ignored_domains (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT NOT NULL UNIQUE,
            added_at TEXT DEFAULT (datetime('now'))
        )""")
        # Migration: add behavior_data column if missing
        cols = [r[1] for r in db.execute("PRAGMA table_info(tabs)").fetchall()]
        if "behavior_data" not in cols:
            db.execute("ALTER TABLE tabs ADD COLUMN behavior_data TEXT")

_init_db()


def _extract_domain(url: str) -> str:
    """Extract domain from URL, stripping www. prefix."""
    try:
        return urlparse(url).hostname.replace("www.", "")
    except Exception:
        return ""


# ── Models ───────────────────────────────────────────────────

class TabData(BaseModel):
    url: str
    title: str
    content: Optional[str] = None
    favicon: Optional[str] = None
    og_image: Optional[str] = None
    og_description: Optional[str] = None
    behavior: Optional[dict] = None  # scroll_depth_pct, active_time_sec, etc.

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


# ── Session Progress Tracking ────────────────────────────────

_session_progress: dict[int, dict] = {}


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
            # Skip TabTriage's own page (both file:// and hosted versions)
            if "TabTriage/index.html" in tab.url or tab.url.rstrip("/").endswith(":5111"):
                skipped += 1
                continue

            # Skip ignored domains
            tab_domain = _extract_domain(tab.url)
            if tab_domain:
                ignored = db.execute(
                    "SELECT 1 FROM ignored_domains WHERE domain = ?", (tab_domain,)
                ).fetchone()
                if ignored:
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

            behavior_json = json.dumps(tab.behavior) if tab.behavior else None

            cur = db.execute(
                """INSERT INTO tabs (session_id, url, title, content, favicon, og_image, og_description, media, behavior_data)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_id, tab.url, tab.title, content, tab.favicon, og_image, og_description, media_json, behavior_json),
            )
            tab_ids.append(cur.lastrowid)

        if not tab_ids:
            # All tabs were duplicates - remove empty session
            db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            return {"session_id": None, "tab_count": 0, "skipped": skipped, "status": "all_duplicates",
                    "message": f"Alle {skipped} Tabs waren bereits in den letzten 24h erfasst."}

    # Init progress tracking
    _session_progress[session_id] = {
        "total": len(tab_ids),
        "completed": 0,
        "phase": "summarizing",
        "clusters": 0,
    }

    # Summarize + cluster in background
    thread = threading.Thread(target=_summarize_and_cluster, args=(session_id, tab_ids))
    thread.start()

    # Generate triage page immediately (with pending summaries)
    _generate_triage_html()

    return {"session_id": session_id, "tab_count": len(tab_ids), "skipped": skipped, "status": "captured"}


def _summarize_and_cluster(session_id: int, tab_ids: list[int]):
    """Background: summarize each tab, then cluster."""
    log.info("Summarizing session %d (%d tabs)...", session_id, len(tab_ids))

    progress = _session_progress.get(session_id, {})

    with get_db() as db:
        for i, tab_id in enumerate(tab_ids):
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

            # Update progress
            if progress:
                progress["completed"] = i + 1

    _generate_triage_html()
    log.info("Session %d summarization complete. Starting clustering...", session_id)

    # Update progress phase
    if progress:
        progress["phase"] = "clustering"

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

    # Count unique clusters
    cluster_count = len(set(c["cluster_id"] for c in clusters)) if clusters else 0

    log.info("Session %d clustering complete.", session_id)

    # Finalize progress
    if progress:
        progress["phase"] = "done"
        progress["clusters"] = cluster_count

    _generate_triage_html()


# ── Progress SSE ─────────────────────────────────────────────

@app.get("/api/capture/{session_id}/progress")
def get_capture_progress(session_id: int):
    """Return current progress for a capture session."""
    progress = _session_progress.get(session_id)
    if not progress:
        return {"total": 0, "completed": 0, "phase": "done", "clusters": 0}
    return progress


@app.get("/api/capture/{session_id}/progress/stream")
async def stream_capture_progress(session_id: int):
    """SSE stream for real-time capture progress."""
    async def event_generator():
        while True:
            progress = _session_progress.get(session_id)
            if not progress:
                yield f"data: {json.dumps({'total': 0, 'completed': 0, 'phase': 'done', 'clusters': 0})}\n\n"
                break
            yield f"data: {json.dumps(progress)}\n\n"
            if progress.get("phase") == "done":
                break
            await asyncio.sleep(2)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


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


# ── Ignored Domains ──────────────────────────────────────────

@app.get("/api/ignored-domains")
def list_ignored_domains():
    with get_db() as db:
        rows = db.execute("SELECT domain, added_at FROM ignored_domains ORDER BY added_at DESC").fetchall()
        return [dict(r) for r in rows]


@app.post("/api/ignored-domains")
def add_ignored_domain(req: dict):
    domain = req.get("domain", "").strip().lower().replace("www.", "")
    if not domain:
        raise HTTPException(400, "Domain is required")
    with get_db() as db:
        try:
            db.execute("INSERT INTO ignored_domains (domain) VALUES (?)", (domain,))
        except sqlite3.IntegrityError:
            pass  # already exists
    _generate_triage_html()
    return {"domain": domain, "status": "added"}


@app.delete("/api/ignored-domains/{domain:path}")
def remove_ignored_domain(domain: str):
    with get_db() as db:
        db.execute("DELETE FROM ignored_domains WHERE domain = ?", (domain,))
    _generate_triage_html()
    return {"domain": domain, "status": "removed"}


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


# ── Re-Extract (Server-side via trafilatura) ─────────────────

_re_extract_progress: dict[str, dict] = {}  # batch_id -> {total, completed, failed}
_re_summarize_progress: dict[str, dict] = {}  # batch_id -> {total, completed, failed}
_pending_re_extract: dict[int, dict] = {}  # tab_id -> {"url": url, "queued_at": time}


# ── Re-Summarize (for tabs WITH content but failed summary) ───

@app.post("/api/tabs/re-summarize-batch")
def re_summarize_batch():
    """Batch re-summarize all tabs that have content but failed summary."""
    import uuid

    with get_db() as db:
        rows = db.execute(
            """SELECT id, title, url, content FROM tabs
               WHERE (summary LIKE '[Zusammenfassung fehlgeschlagen:%' OR summary LIKE '[Kein ausreichender%' OR summary IS NULL)
               AND triaged_at IS NULL"""
        ).fetchall()

    if not rows:
        return {"status": "none", "count": 0}

    batch_id = str(uuid.uuid4())[:8]
    tab_list = [dict(r) for r in rows]
    _re_summarize_progress[batch_id] = {"total": len(tab_list), "completed": 0, "failed": 0}

    thread = threading.Thread(target=_batch_re_summarize, args=(batch_id, tab_list))
    thread.start()

    return {"status": "started", "count": len(tab_list), "batch_id": batch_id}


@app.get("/api/tabs/re-summarize-progress/{batch_id}")
def re_summarize_progress(batch_id: str):
    progress = _re_summarize_progress.get(batch_id)
    if not progress:
        return {"total": 0, "completed": 0, "failed": 0, "done": True}
    done = progress["completed"] + progress["failed"] >= progress["total"]
    return {**progress, "done": done}


# ── Extension-based Re-Extract polling ────────────────────────

@app.get("/api/tabs/pending-re-extract")
def pending_re_extract():
    """Extension polls this to find tabs needing content re-extraction."""
    import time
    # Clean stale entries (>60s)
    cutoff = time.time() - 60
    for tid in list(_pending_re_extract.keys()):
        if _pending_re_extract[tid]["queued_at"] < cutoff:
            del _pending_re_extract[tid]
    return {"tabs": [{"tab_id": tid, "url": info["url"]} for tid, info in _pending_re_extract.items()]}


@app.post("/api/tabs/request-re-extract-batch")
def request_re_extract_batch():
    """Server-side batch re-extract for all tabs with failed content."""
    import uuid

    with get_db() as db:
        rows = db.execute(
            "SELECT id, url FROM tabs WHERE summary LIKE '[Kein ausreichender%' AND triaged_at IS NULL"
        ).fetchall()

    if not rows:
        return {"status": "none", "count": 0}

    batch_id = str(uuid.uuid4())[:8]
    tab_list = [{"id": r["id"], "url": r["url"]} for r in rows]
    _re_extract_progress[batch_id] = {"total": len(tab_list), "completed": 0, "failed": 0}

    thread = threading.Thread(target=_batch_re_extract, args=(batch_id, tab_list))
    thread.start()

    return {"status": "started", "count": len(tab_list), "batch_id": batch_id}


@app.get("/api/tabs/re-extract-progress/{batch_id}")
def re_extract_progress(batch_id: str):
    """Poll progress of a batch re-extract."""
    progress = _re_extract_progress.get(batch_id)
    if not progress:
        return {"total": 0, "completed": 0, "failed": 0, "done": True}
    done = progress["completed"] + progress["failed"] >= progress["total"]
    return {**progress, "done": done}


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

@app.post("/api/tabs/{tab_id}/re-summarize")
def re_summarize_tab(tab_id: int):
    """Re-summarize a single tab (with content, or title-only fallback)."""
    with get_db() as db:
        row = db.execute("SELECT title, url, content FROM tabs WHERE id = ?", (tab_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Tab not found")

    def _do_resummarize(tid, title, url, content):
        try:
            s = summarize_tab(title, url, content)
            tags_json = json.dumps(s.get("tags", [])) if s.get("tags") else None
            with get_db() as db:
                db.execute(
                    "UPDATE tabs SET summary = ?, suggested_category = ?, tags = COALESCE(?, tags) WHERE id = ?",
                    (s["summary"], s["suggested_category"], tags_json, tid),
                )
                db.commit()
            _generate_triage_html()
            log.info("Re-summarized tab %d", tid)
        except Exception as e:
            log.error("Re-summarize failed for tab %d: %s", tid, e)

    thread = threading.Thread(target=_do_resummarize, args=(tab_id, row["title"], row["url"], row["content"]))
    thread.start()

    return {"status": "re-summarizing", "tab_id": tab_id}


@app.post("/api/tabs/{tab_id}/update-content")
def update_content(tab_id: int, req: dict):
    """Extension sends extracted content for a tab (or null if tab not found)."""
    import time
    content = req.get("content")

    if content:
        # Extension delivered content — remove from pending queue
        _pending_re_extract.pop(tab_id, None)
        # Extension successfully extracted content
        parsed_content = content
        og_image = req.get("og_image")
        og_description = req.get("og_description")

        # Try to parse JSON format from content.js
        try:
            parsed = json.loads(content)
            parsed_content = parsed.get("text", "")[:MAX_CONTENT]
            og_image = og_image or parsed.get("og_image")
            og_description = og_description or parsed.get("og_description")
        except (json.JSONDecodeError, AttributeError):
            parsed_content = content[:MAX_CONTENT]

        with get_db() as db:
            db.execute(
                """UPDATE tabs SET content = ?,
                   og_image = COALESCE(?, og_image),
                   og_description = COALESCE(?, og_description)
                   WHERE id = ?""",
                (parsed_content, og_image, og_description, tab_id),
            )
            db.commit()

        # Re-summarize in background
        def _resummarize(tid):
            with get_db() as db:
                r = db.execute("SELECT title, url, content FROM tabs WHERE id = ?", (tid,)).fetchone()
                if r:
                    s = summarize_tab(r["title"], r["url"], r["content"])
                    tags_json = json.dumps(s.get("tags", [])) if s.get("tags") else None
                    db.execute(
                        "UPDATE tabs SET summary = ?, suggested_category = ?, tags = COALESCE(?, tags) WHERE id = ?",
                        (s["summary"], s["suggested_category"], tags_json, tid),
                    )
                    db.commit()
            _generate_triage_html()
            log.info("Extension re-extract + re-summarize complete for tab %d", tid)

        threading.Thread(target=_resummarize, args=(tab_id,)).start()
        return {"status": "content_received", "tab_id": tab_id}
    else:
        # Extension couldn't find the tab — trafilatura fallback will be triggered by the timer
        log.info("Extension reported tab %d not found, trafilatura fallback will handle it", tab_id)
        return {"status": "not_found", "tab_id": tab_id}


@app.post("/api/tabs/{tab_id}/request-re-extract")
def request_re_extract(tab_id: int):
    """Dual re-extract: queue for extension, fallback to trafilatura after 15s."""
    import time as _time

    with get_db() as db:
        row = db.execute("SELECT url, title FROM tabs WHERE id = ?", (tab_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Tab not found")

    # Queue for extension polling
    _pending_re_extract[tab_id] = {"url": row["url"], "queued_at": _time.time()}

    def _fallback_extract(tid, url):
        """Wait 15s for extension, then fallback to trafilatura."""
        _time.sleep(15)
        # Check if extension already delivered content
        if tid not in _pending_re_extract:
            log.info("Tab %d: extension already delivered content, skipping trafilatura", tid)
            return

        # Extension didn't deliver — remove from queue and try trafilatura
        _pending_re_extract.pop(tid, None)
        log.info("Tab %d: extension timeout, trying trafilatura fallback for %s", tid, url)

        result = extract_content(url)
        if not result or not result.get("text"):
            log.warning("Trafilatura fallback also failed for tab %d: %s", tid, url)
            return

        content = result["text"][:MAX_CONTENT]
        with get_db() as db:
            db.execute(
                """UPDATE tabs SET content = ?,
                   og_image = COALESCE(?, og_image),
                   og_description = COALESCE(?, og_description)
                   WHERE id = ?""",
                (content, result.get("og_image"), result.get("og_description"), tid),
            )
            db.commit()

        # Re-summarize
        with get_db() as db:
            r = db.execute("SELECT title, url, content FROM tabs WHERE id = ?", (tid,)).fetchone()
            if r:
                s = summarize_tab(r["title"], r["url"], r["content"])
                tags_json = json.dumps(s.get("tags", [])) if s.get("tags") else None
                db.execute(
                    "UPDATE tabs SET summary = ?, suggested_category = ?, tags = COALESCE(?, tags) WHERE id = ?",
                    (s["summary"], s["suggested_category"], tags_json, tid),
                )
                db.commit()
        _generate_triage_html()
        log.info("Trafilatura fallback re-extracted and re-summarized tab %d", tid)

    thread = threading.Thread(target=_fallback_extract, args=(tab_id, row["url"]))
    thread.start()

    return {"status": "queued", "tab_id": tab_id, "message": "Extension gets 15s, then trafilatura fallback"}


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
    if d.get("behavior_data"):
        try:
            d["behavior_data"] = json.loads(d["behavior_data"])
        except:
            d["behavior_data"] = None
    if not include_content:
        d.pop("content", None)
        d["has_content"] = bool(row["content"])
    return d


# ── Triage (single + bulk + auto) ───────────────────────────

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


@app.get("/api/triage/auto/preview")
def triage_auto_preview():
    """Preview what auto-triage would do, without changing anything."""
    with get_db() as db:
        rows = db.execute(
            "SELECT id, title, url, suggested_category, starred FROM tabs WHERE triaged_at IS NULL AND suggested_category IS NOT NULL"
        ).fetchall()

    counts = {"read-later": 0, "reference": 0, "actionable": 0, "archive": 0}
    tabs_by_cat: dict[str, list] = {"read-later": [], "reference": [], "actionable": [], "archive": []}
    for row in rows:
        cat = row["suggested_category"]
        if cat in counts:
            counts[cat] += 1
            tabs_by_cat[cat].append({"id": row["id"], "title": row["title"], "url": row["url"]})

    return {
        "total": len(rows),
        "counts": counts,
        "will_close": counts.get("archive", 0),
        "will_star": counts.get("actionable", 0),
        "tabs_by_category": tabs_by_cat,
    }


# Undo buffer: stores pre-triage state for reversal
_undo_buffer: dict[str, dict] = {}  # batch_id -> {tab_ids, pre_state, timestamp}


@app.post("/api/triage/auto")
def triage_auto():
    """Auto-triage all untriaged tabs using their AI-suggested categories."""
    import time
    import uuid

    with get_db() as db:
        rows = db.execute(
            "SELECT id, suggested_category, starred, category, triaged_at FROM tabs WHERE triaged_at IS NULL AND suggested_category IS NOT NULL"
        ).fetchall()

    if not rows:
        return {"status": "auto_triaged", "total": 0, "saved": 0, "starred": 0, "archived": 0, "close_requested": 0, "batch_id": None}

    # Save pre-triage state for undo
    batch_id = str(uuid.uuid4())[:8]
    pre_state = []
    for row in rows:
        pre_state.append({
            "tab_id": row["id"],
            "category": row["category"],
            "starred": row["starred"],
            "triaged_at": row["triaged_at"],
        })
    _undo_buffer[batch_id] = {"pre_state": pre_state, "timestamp": time.time()}

    # Clean old undo buffers (>5 min)
    cutoff = time.time() - 300
    for bid in list(_undo_buffer.keys()):
        if _undo_buffer[bid]["timestamp"] < cutoff:
            del _undo_buffer[bid]

    results = {"saved": 0, "starred": 0, "archived": 0, "close_requested": 0}
    items = []
    close_ids = []

    for row in rows:
        cat = row["suggested_category"]
        item = TriageItem(tab_id=row["id"], category=cat)

        if cat == "actionable":
            item.starred = True
            results["starred"] += 1

        if cat == "archive":
            results["archived"] += 1
            close_ids.append(row["id"])
        else:
            results["saved"] += 1

        items.append(item)

    for item in items:
        _triage_one(item)

    # Request close for archived tabs
    if close_ids:
        with get_db() as db:
            for tid in close_ids:
                row_url = db.execute("SELECT url FROM tabs WHERE id = ?", (tid,)).fetchone()
                if row_url and row_url["url"] not in _pending_close_urls:
                    _pending_close_urls.append(row_url["url"])
        results["close_requested"] = len(close_ids)

    _generate_triage_html()
    return {"status": "auto_triaged", "total": len(items), "batch_id": batch_id, **results}


@app.post("/api/triage/auto/undo")
def triage_auto_undo(req: dict):
    """Undo an auto-triage batch by restoring pre-triage state."""
    batch_id = req.get("batch_id")
    if not batch_id or batch_id not in _undo_buffer:
        raise HTTPException(404, "Undo batch not found or expired")

    batch = _undo_buffer.pop(batch_id)
    restored = 0

    with get_db() as db:
        for entry in batch["pre_state"]:
            db.execute(
                "UPDATE tabs SET category = ?, starred = ?, triaged_at = ? WHERE id = ?",
                (entry["category"], entry["starred"], entry["triaged_at"], entry["tab_id"]),
            )
            # Remove from pending close if it was queued
            row = db.execute("SELECT url FROM tabs WHERE id = ?", (entry["tab_id"],)).fetchone()
            if row and row["url"] in _pending_close_urls:
                _pending_close_urls.remove(row["url"])
            restored += 1

    _generate_triage_html()
    return {"status": "undone", "restored": restored}


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
                if td.get("behavior_data"):
                    try:
                        td["behavior_data"] = json.loads(td["behavior_data"])
                    except:
                        td["behavior_data"] = None
                tab_list.append(td)
            all_data.append({"session": dict(s), "tabs": tab_list})

    # Fetch ignored domains
    with get_db() as db:
        ignored_rows = db.execute("SELECT domain FROM ignored_domains ORDER BY domain").fetchall()
    ignored_list = [r["domain"] for r in ignored_rows]

    data_json = json.dumps(all_data, ensure_ascii=False, indent=None)
    ignored_json = json.dumps(ignored_list, ensure_ascii=False, indent=None)
    template_path = Path(__file__).parent / "triage_template.html"
    template = template_path.read_text(encoding="utf-8")
    html = template.replace("/*DATA_PLACEHOLDER*/[]", data_json)
    html = html.replace("/*IGNORED_DOMAINS_PLACEHOLDER*/[]", ignored_json)

    Path(TRIAGE_HTML_PATH).parent.mkdir(parents=True, exist_ok=True)
    Path(TRIAGE_HTML_PATH).write_text(html, encoding="utf-8")
    log.info("Triage HTML updated: %s", TRIAGE_HTML_PATH)


# ── Hosted Version: Serve Triage Page ────────────────────────

@app.get("/api/triage-data")
def triage_data():
    """Return all sessions + tabs as JSON for the hosted triage page."""
    with get_db() as db:
        sessions = db.execute(
            "SELECT * FROM sessions ORDER BY captured_at DESC"
        ).fetchall()

        all_data = []
        for s in sessions:
            tabs = db.execute(
                "SELECT * FROM tabs WHERE session_id = ? ORDER BY id", (s["id"],)
            ).fetchall()
            tab_list = [_tab_dict(t) for t in tabs]
            all_data.append({"session": dict(s), "tabs": tab_list})

    with get_db() as db:
        ignored_rows = db.execute("SELECT domain FROM ignored_domains ORDER BY domain").fetchall()
    ignored_list = [r["domain"] for r in ignored_rows]

    return {"sessions": all_data, "ignored_domains": ignored_list}


def _batch_re_extract(batch_id: str, tab_list: list[dict]):
    """Background: re-extract content for multiple tabs server-side."""
    progress = _re_extract_progress.get(batch_id, {})
    for item in tab_list:
        tid, url = item["id"], item["url"]
        try:
            result = extract_content(url)
            if result and result.get("text"):
                content = result["text"][:MAX_CONTENT]
                with get_db() as db:
                    db.execute(
                        """UPDATE tabs SET content = ?,
                           og_image = COALESCE(?, og_image),
                           og_description = COALESCE(?, og_description)
                           WHERE id = ?""",
                        (content, result.get("og_image"), result.get("og_description"), tid),
                    )
                    db.commit()

                # Re-summarize
                with get_db() as db:
                    r = db.execute("SELECT title, url, content FROM tabs WHERE id = ?", (tid,)).fetchone()
                    if r:
                        s = summarize_tab(r["title"], r["url"], r["content"])
                        tags_json = json.dumps(s.get("tags", [])) if s.get("tags") else None
                        db.execute(
                            "UPDATE tabs SET summary = ?, suggested_category = ?, tags = COALESCE(?, tags) WHERE id = ?",
                            (s["summary"], s["suggested_category"], tags_json, tid),
                        )
                        db.commit()
                progress["completed"] = progress.get("completed", 0) + 1
                log.info("Batch re-extract: tab %d done (%d/%d)", tid, progress["completed"], progress["total"])
            else:
                # Trafilatura failed — try title-only summarize fallback
                with get_db() as db:
                    r = db.execute("SELECT title, url FROM tabs WHERE id = ?", (tid,)).fetchone()
                    if r:
                        s = summarize_tab(r["title"], r["url"], None)
                        if not s["summary"].startswith("[Kein"):
                            tags_json = json.dumps(s.get("tags", [])) if s.get("tags") else None
                            db.execute(
                                "UPDATE tabs SET summary = ?, suggested_category = ?, tags = COALESCE(?, tags) WHERE id = ?",
                                (s["summary"], s["suggested_category"], tags_json, tid),
                            )
                            db.commit()
                            progress["completed"] = progress.get("completed", 0) + 1
                            log.info("Batch re-extract: tab %d title-only fallback succeeded", tid)
                        else:
                            progress["failed"] = progress.get("failed", 0) + 1
                            log.warning("Batch re-extract: tab %d failed (no content, title-only also failed)", tid)
        except Exception as e:
            progress["failed"] = progress.get("failed", 0) + 1
            log.error("Batch re-extract error for tab %d: %s", tid, e)

    _generate_triage_html()
    log.info("Batch re-extract %s complete: %d/%d succeeded", batch_id, progress.get("completed", 0), progress["total"])


def _batch_re_summarize(batch_id: str, tab_list: list[dict]):
    """Background: re-summarize multiple tabs that already have content."""
    progress = _re_summarize_progress.get(batch_id, {})
    for item in tab_list:
        tid = item["id"]
        try:
            s = summarize_tab(item["title"], item["url"], item["content"])
            tags_json = json.dumps(s.get("tags", [])) if s.get("tags") else None
            with get_db() as db:
                db.execute(
                    "UPDATE tabs SET summary = ?, suggested_category = ?, tags = COALESCE(?, tags) WHERE id = ?",
                    (s["summary"], s["suggested_category"], tags_json, tid),
                )
                db.commit()
            progress["completed"] = progress.get("completed", 0) + 1
            log.info("Batch re-summarize: tab %d done (%d/%d)", tid, progress["completed"], progress["total"])
        except Exception as e:
            progress["failed"] = progress.get("failed", 0) + 1
            log.error("Batch re-summarize error for tab %d: %s", tid, e)

    _generate_triage_html()
    log.info("Batch re-summarize %s complete: %d/%d succeeded", batch_id, progress.get("completed", 0), progress["total"])


# ── Content Insights ──────────────────────────────────────────

@app.post("/api/insights/analyze")
def insights_analyze(req: dict):
    """Deep content analysis across filtered tabs."""
    cluster_id = req.get("cluster_id")
    tag = req.get("tag")
    query = req.get("query")
    max_tabs = req.get("max_tabs", 30)

    with get_db() as db:
        conditions = ["triaged_at IS NULL OR triaged_at IS NOT NULL"]  # all tabs
        params = []

        if cluster_id:
            conditions.append("cluster_id = ?")
            params.append(cluster_id)
        if tag:
            conditions.append("tags LIKE ?")
            params.append(f"%{tag}%")

        rows = db.execute(
            f"SELECT id, title, url, summary, content, tags FROM tabs WHERE {' AND '.join(conditions)} ORDER BY id DESC LIMIT ?",
            params + [max_tabs],
        ).fetchall()

    if query:
        rows = [r for r in rows if query.lower() in (r["title"] or "").lower() or query.lower() in (r["summary"] or "").lower()]

    if not rows:
        return {"error": "Keine passenden Tabs gefunden."}

    tab_list = [dict(r) for r in rows]
    result = analyze_content(tab_list)
    result["tab_count"] = len(tab_list)
    return result


@app.get("/api/insights/topics")
def insights_topics():
    """Aggregated topic overview from tags and clusters."""
    with get_db() as db:
        rows = db.execute("SELECT tags, cluster_id, cluster_label FROM tabs WHERE triaged_at IS NULL").fetchall()

    tag_counts: dict[str, int] = {}
    cluster_counts: dict[str, dict] = {}

    for r in rows:
        if r["tags"]:
            try:
                tags = json.loads(r["tags"])
                for t in tags:
                    tag_counts[t] = tag_counts.get(t, 0) + 1
            except Exception:
                pass
        if r["cluster_id"] and r["cluster_label"]:
            if r["cluster_id"] not in cluster_counts:
                cluster_counts[r["cluster_id"]] = {"label": r["cluster_label"], "count": 0}
            cluster_counts[r["cluster_id"]]["count"] += 1

    return {
        "tags": sorted(tag_counts.items(), key=lambda x: -x[1]),
        "clusters": sorted(cluster_counts.values(), key=lambda x: -x["count"]),
    }


@app.get("/", response_class=HTMLResponse)
def serve_triage_page():
    """Serve the triage page directly from FastAPI."""
    template_path = Path(__file__).parent / "triage_template.html"
    template = template_path.read_text(encoding="utf-8")

    # For hosted version, we use fetch-based loading, so embed empty data
    # The template detects hosted mode and fetches from /api/triage-data
    html = template.replace("/*DATA_PLACEHOLDER*/[]", "[]")
    html = html.replace("/*IGNORED_DOMAINS_PLACEHOLDER*/[]", "[]")
    return HTMLResponse(content=html)


if __name__ == "__main__":
    log.info("Starting TabTriage backend on port %d", config["backend_port"])
    _generate_triage_html()
    uvicorn.run(app, host=config.get("backend_host", "0.0.0.0"), port=config["backend_port"])
