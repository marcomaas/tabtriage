"""Tests for FastAPI API endpoints."""

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))


@pytest.fixture
def app_client(tmp_path):
    """Create a FastAPI TestClient with isolated DB."""
    db_path = str(tmp_path / "test.db")
    html_path = str(tmp_path / "triage.html")

    # Init DB
    conn = sqlite3.connect(db_path)
    schema = (Path(__file__).parent.parent / "schema.sql").read_text()
    conn.executescript(schema)
    conn.commit()
    conn.close()

    config = {
        "backend_port": 5111,
        "backend_host": "0.0.0.0",
        "notion_config": "/dev/null",
        "triage_html_path": html_path,
        "db_path": db_path,
        "claude_timeout": 300,
        "max_content_length": 50000,
    }

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(config))

    import main
    main.DB_PATH = db_path
    main.TRIAGE_HTML_PATH = html_path
    main._generate_triage_html = MagicMock()

    # Clear pending close list between tests
    main._pending_close_urls.clear()

    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    yield client, main


def _seed_tab(main, url="http://example.com/test", title="Test Tab", content="Some content here"):
    """Insert a test tab directly into DB."""
    conn = sqlite3.connect(main.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("INSERT OR IGNORE INTO sessions (id, window_title, hostname) VALUES (1, 'Test Session', 'test')")
    cur = conn.execute(
        "INSERT INTO tabs (session_id, url, title, content, summary, suggested_category, tags) VALUES (1, ?, ?, ?, 'A summary', 'read-later', ?)",
        (url, title, content, json.dumps(["test-tag"])),
    )
    tab_id = cur.lastrowid
    conn.commit()
    conn.close()
    return tab_id


class TestSessions:
    def test_list_sessions_empty(self, app_client):
        client, main = app_client
        resp = client.get("/api/sessions")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_sessions_with_data(self, app_client):
        client, main = app_client
        _seed_tab(main)
        resp = client.get("/api/sessions")
        assert resp.status_code == 200
        sessions = resp.json()
        assert len(sessions) == 1
        assert sessions[0]["tab_count"] == 1


class TestTabDetail:
    def test_get_existing_tab(self, app_client):
        client, main = app_client
        tab_id = _seed_tab(main)
        resp = client.get(f"/api/tabs/{tab_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Test Tab"
        assert data["content"] is not None  # include_content=True for detail

    def test_get_nonexistent_tab(self, app_client):
        client, main = app_client
        resp = client.get("/api/tabs/99999")
        assert resp.status_code == 404

    def test_get_tab_content(self, app_client):
        client, main = app_client
        tab_id = _seed_tab(main, content="Full text content here")
        resp = client.get(f"/api/tabs/{tab_id}/content")
        assert resp.status_code == 200
        assert resp.json()["content"] == "Full text content here"


class TestStar:
    def test_star_toggle(self, app_client):
        client, main = app_client
        tab_id = _seed_tab(main)

        # Star it
        resp = client.post(f"/api/tabs/{tab_id}/star", json={"tab_id": tab_id, "starred": True})
        assert resp.status_code == 200
        assert resp.json()["starred"] is True

        # Verify in DB
        conn = sqlite3.connect(main.DB_PATH)
        row = conn.execute("SELECT starred FROM tabs WHERE id = ?", (tab_id,)).fetchone()
        assert row[0] == 1
        conn.close()

        # Unstar
        resp = client.post(f"/api/tabs/{tab_id}/star", json={"tab_id": tab_id, "starred": False})
        assert resp.json()["starred"] is False


class TestTriage:
    @patch("main.create_link")
    def test_triage_save_to_links(self, mock_create_link, app_client):
        client, main = app_client
        mock_create_link.return_value = "https://notion.so/page123"

        tab_id = _seed_tab(main)
        resp = client.post("/api/triage", json={
            "tab_id": tab_id,
            "category": "reference",
            "notion_target": "links",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "triaged"
        assert data["notion_url"] == "https://notion.so/page123"
        mock_create_link.assert_called_once()

    def test_triage_dismiss(self, app_client):
        client, main = app_client
        tab_id = _seed_tab(main)
        resp = client.post("/api/triage", json={
            "tab_id": tab_id,
            "category": "dismiss",
            "notion_target": "links",  # should be ignored for dismiss
        })
        data = resp.json()
        assert data["status"] == "triaged"
        assert data["notion_url"] is None  # dismiss skips Notion

    def test_triage_updates_db(self, app_client):
        client, main = app_client
        tab_id = _seed_tab(main)
        client.post("/api/triage", json={
            "tab_id": tab_id,
            "category": "actionable",
            "user_note": "Follow up on this",
            "tags": ["urgent", "review"],
        })
        conn = sqlite3.connect(main.DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM tabs WHERE id = ?", (tab_id,)).fetchone()
        assert row["category"] == "actionable"
        assert row["user_note"] == "Follow up on this"
        assert json.loads(row["tags"]) == ["urgent", "review"]
        assert row["triaged_at"] is not None
        conn.close()


class TestPendingClose:
    def test_pending_close_empty(self, app_client):
        client, main = app_client
        resp = client.get("/api/tabs/pending-close")
        assert resp.json() == {"urls": []}

    def test_request_close_and_poll(self, app_client):
        client, main = app_client
        tab_id = _seed_tab(main, url="http://example.com/closeme")

        # Request close
        client.post(f"/api/tabs/{tab_id}/request-close")

        # Poll
        resp = client.get("/api/tabs/pending-close")
        assert "http://example.com/closeme" in resp.json()["urls"]

    def test_confirm_close_removes_url(self, app_client):
        client, main = app_client
        tab_id = _seed_tab(main, url="http://example.com/closeme2")
        client.post(f"/api/tabs/{tab_id}/request-close")

        # Confirm
        client.post("/api/tabs/confirm-close", json={"url": "http://example.com/closeme2"})

        resp = client.get("/api/tabs/pending-close")
        assert resp.json()["urls"] == []

    def test_bulk_close(self, app_client):
        client, main = app_client
        t1 = _seed_tab(main, url="http://example.com/bulk1", title="B1")
        t2 = _seed_tab(main, url="http://example.com/bulk2", title="B2")

        resp = client.post("/api/tabs/request-close-bulk", json={"tab_ids": [t1, t2]})
        assert resp.json()["count"] == 2

        pending = client.get("/api/tabs/pending-close").json()["urls"]
        assert len(pending) == 2


class TestSearch:
    def test_search_by_query(self, app_client):
        client, main = app_client
        _seed_tab(main, title="Python Tutorial", content="Learn Python basics")

        resp = client.get("/api/search", params={"q": "Python"})
        assert resp.status_code == 200
        results = resp.json()
        assert len(results) >= 1

    def test_search_empty_query(self, app_client):
        client, main = app_client
        _seed_tab(main)
        resp = client.get("/api/search")
        assert resp.status_code == 200


class TestCapture:
    @patch("main.summarize_tab")
    @patch("main.cluster_tabs")
    def test_valid_capture(self, mock_cluster, mock_summarize, app_client):
        client, main = app_client
        mock_summarize.return_value = {"summary": "Test", "suggested_category": "read-later", "tags": []}
        mock_cluster.return_value = []

        resp = client.post("/api/capture", json={
            "window_title": "Test Window",
            "tabs": [
                {"url": "http://example.com/new1", "title": "New Tab 1"},
                {"url": "http://example.com/new2", "title": "New Tab 2"},
            ],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["tab_count"] == 2
        assert data["status"] == "captured"
