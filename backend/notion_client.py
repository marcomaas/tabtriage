"""Notion API integration for TabTriage."""

import json
import logging
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

NOTION_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

# Load config
_config_path = Path(__file__).parent.parent / "config" / "config.json"
_config = json.loads(_config_path.read_text())
_notion_config = json.loads(Path(_config["notion_config"]).read_text())

API_KEY = _notion_config["api_key"]
LINKS_DB = _notion_config["databases"]["links"]
PARKEN_DB = _notion_config["databases"]["parken"]
TASKS_DB = _notion_config["databases"]["tasks"]


def _headers():
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _chunk_text(text: str, max_len: int = 2000) -> list[str]:
    """Split text into chunks, breaking at newlines."""
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        cut = text.rfind("\n", 0, max_len)
        if cut <= 0:
            cut = max_len
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


def get_projects() -> list[dict]:
    """Load projects from Parken DB for dropdown."""
    body = {
        "filter": {
            "property": "Dashboard",
            "select": {"does_not_equal": "Archiv"},
        },
        "sorts": [{"property": "Name", "direction": "ascending"}],
        "page_size": 100,
    }
    resp = httpx.post(f"{NOTION_BASE}/databases/{PARKEN_DB}/query", headers=_headers(), json=body, timeout=30)
    if not resp.is_success:
        log.error("Failed to load projects: %s", resp.text[:300])
        return []

    projects = []
    for page in resp.json().get("results", []):
        title_prop = page["properties"].get("Name", {}).get("title", [])
        name = title_prop[0]["plain_text"] if title_prop else "Untitled"
        projects.append({"id": page["id"], "name": name})
    return projects


def create_link(title: str, url: str, summary: str, content: str | None = None) -> str | None:
    """Create entry in Links DB (Web Clipper Mastertabelle)."""
    properties = {
        "Name": {"title": [{"text": {"content": title[:2000]}}]},
        "URL": {"url": url},
        "Summary": {"rich_text": [{"text": {"content": summary[:2000]}}]},
    }
    body = {"parent": {"database_id": LINKS_DB}, "properties": properties}
    resp = httpx.post(f"{NOTION_BASE}/pages", headers=_headers(), json=body, timeout=30)

    if not resp.is_success:
        log.error("Failed to create link: %s", resp.text[:300])
        return None

    page_id = resp.json()["id"]
    page_url = resp.json().get("url", "")

    # Append content blocks if available
    if content:
        _append_content_blocks(page_id, content)

    log.info("Created link in Notion: %s", title)
    return page_url


def create_backlog_card(title: str, summary: str, url: str) -> str | None:
    """Create card in Parken DB with Dashboard=Backlog."""
    desc = f"{summary}\n\nQuelle: {url}"
    properties = {
        "Name": {"title": [{"text": {"content": title[:2000]}}]},
        "Description": {"rich_text": [{"text": {"content": desc[:2000]}}]},
        "Dashboard": {"select": {"name": "Backlog"}},
    }
    body = {"parent": {"database_id": PARKEN_DB}, "properties": properties}
    resp = httpx.post(f"{NOTION_BASE}/pages", headers=_headers(), json=body, timeout=30)

    if not resp.is_success:
        log.error("Failed to create backlog card: %s", resp.text[:300])
        return None

    log.info("Created backlog card: %s", title)
    return resp.json().get("url", "")


def append_to_project(project_id: str, title: str, url: str, summary: str) -> bool:
    """Append bookmark block to existing project page."""
    blocks = [
        {"object": "block", "type": "divider", "divider": {}},
        {
            "object": "block",
            "type": "bookmark",
            "bookmark": {"url": url, "caption": [{"type": "text", "text": {"content": summary[:2000]}}]},
        },
    ]
    resp = httpx.patch(
        f"{NOTION_BASE}/blocks/{project_id}/children",
        headers=_headers(),
        json={"children": blocks},
        timeout=30,
    )
    if not resp.is_success:
        log.error("Failed to append to project: %s", resp.text[:300])
        return False

    log.info("Appended bookmark to project %s: %s", project_id, title)
    return True


def create_task(title: str, url: str, summary: str, when: str = "today") -> str | None:
    """Create task in Tasks DB. when='today' → Next Action, when='someday' → Someday/Maybe."""
    status = "Next Action" if when == "today" else "Someday/Maybe"
    properties = {
        "Name": {"title": [{"text": {"content": title[:2000]}}]},
        "Description": {"rich_text": [{"text": {"content": summary[:2000]}}]},
        "URL": {"url": url},
        "Status": {"status": {"name": status}},
        "Dashboard": {"select": {"name": "Marco"}},
    }
    body = {"parent": {"database_id": TASKS_DB}, "properties": properties}
    resp = httpx.post(f"{NOTION_BASE}/pages", headers=_headers(), json=body, timeout=30)

    if not resp.is_success:
        log.error("Failed to create task: %s", resp.text[:300])
        return None

    log.info("Created task (%s): %s", when, title)
    return resp.json().get("url", "")


def _append_content_blocks(page_id: str, content: str):
    """Append content text as paragraph blocks (2000-char chunks)."""
    blocks = []
    for chunk in _chunk_text(content, 2000):
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": chunk}}]},
        })

    # Notion allows max 100 blocks per request
    for i in range(0, len(blocks), 100):
        batch = blocks[i:i + 100]
        resp = httpx.patch(
            f"{NOTION_BASE}/blocks/{page_id}/children",
            headers=_headers(),
            json={"children": batch},
            timeout=30,
        )
        if not resp.is_success:
            log.error("Failed to append content blocks: %s", resp.text[:300])
            break
