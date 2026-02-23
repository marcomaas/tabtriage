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
    # Clear session progress and undo buffer between tests
    main._session_progress.clear()
    main._undo_buffer.clear()

    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    yield client, main


def _seed_tab(main, url="http://example.com/test", title="Test Tab", content="Some content here",
              suggested_category="read-later", triaged_at=None):
    """Insert a test tab directly into DB."""
    conn = sqlite3.connect(main.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("INSERT OR IGNORE INTO sessions (id, window_title, hostname) VALUES (1, 'Test Session', 'test')")
    cur = conn.execute(
        "INSERT INTO tabs (session_id, url, title, content, summary, suggested_category, tags, triaged_at) VALUES (1, ?, ?, ?, 'A summary', ?, ?, ?)",
        (url, title, content, suggested_category, json.dumps(["test-tag"]), triaged_at),
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


class TestAutoTriage:
    def test_auto_triage_basic(self, app_client):
        client, main = app_client
        _seed_tab(main, url="http://example.com/a1", title="Tab A", suggested_category="read-later")
        _seed_tab(main, url="http://example.com/a2", title="Tab B", suggested_category="actionable")
        _seed_tab(main, url="http://example.com/a3", title="Tab C", suggested_category="archive")

        resp = client.post("/api/triage/auto")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "auto_triaged"
        assert data["total"] == 3
        assert data["saved"] == 2  # read-later + actionable
        assert data["archived"] == 1
        assert data["starred"] == 1  # actionable gets starred

    def test_auto_triage_skips_already_triaged(self, app_client):
        client, main = app_client
        _seed_tab(main, url="http://example.com/done", triaged_at="2026-01-01T00:00:00")
        _seed_tab(main, url="http://example.com/pending", title="Pending")

        resp = client.post("/api/triage/auto")
        data = resp.json()
        assert data["total"] == 1  # only the untriaged one

    def test_auto_triage_empty(self, app_client):
        client, main = app_client
        resp = client.post("/api/triage/auto")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_auto_triage_archives_request_close(self, app_client):
        client, main = app_client
        _seed_tab(main, url="http://example.com/archive-me", suggested_category="archive")

        client.post("/api/triage/auto")

        pending = client.get("/api/tabs/pending-close").json()["urls"]
        assert "http://example.com/archive-me" in pending

    def test_auto_triage_returns_batch_id(self, app_client):
        client, main = app_client
        _seed_tab(main, url="http://example.com/b1", suggested_category="read-later")

        resp = client.post("/api/triage/auto")
        data = resp.json()
        assert data["batch_id"] is not None
        assert len(data["batch_id"]) == 8


class TestAutoTriagePreview:
    def test_preview_counts(self, app_client):
        client, main = app_client
        _seed_tab(main, url="http://example.com/p1", suggested_category="read-later")
        _seed_tab(main, url="http://example.com/p2", suggested_category="actionable")
        _seed_tab(main, url="http://example.com/p3", suggested_category="archive")

        resp = client.get("/api/triage/auto/preview")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert data["counts"]["read-later"] == 1
        assert data["counts"]["actionable"] == 1
        assert data["counts"]["archive"] == 1
        assert data["will_close"] == 1
        assert data["will_star"] == 1

    def test_preview_empty(self, app_client):
        client, main = app_client
        resp = client.get("/api/triage/auto/preview")
        assert resp.json()["total"] == 0

    def test_preview_does_not_change_data(self, app_client):
        client, main = app_client
        tab_id = _seed_tab(main, url="http://example.com/pnc", suggested_category="archive")

        client.get("/api/triage/auto/preview")

        # Tab should still be untriaged
        conn = sqlite3.connect(main.DB_PATH)
        row = conn.execute("SELECT triaged_at FROM tabs WHERE id = ?", (tab_id,)).fetchone()
        assert row[0] is None
        conn.close()


class TestAutoTriageUndo:
    def test_undo_restores_state(self, app_client):
        client, main = app_client
        t1 = _seed_tab(main, url="http://example.com/u1", suggested_category="actionable")
        t2 = _seed_tab(main, url="http://example.com/u2", suggested_category="archive")

        # Auto-triage
        resp = client.post("/api/triage/auto")
        batch_id = resp.json()["batch_id"]

        # Verify they were triaged
        conn = sqlite3.connect(main.DB_PATH)
        conn.row_factory = sqlite3.Row
        row1 = conn.execute("SELECT triaged_at, starred FROM tabs WHERE id = ?", (t1,)).fetchone()
        assert row1["triaged_at"] is not None
        assert row1["starred"] == 1
        conn.close()

        # Undo
        resp = client.post("/api/triage/auto/undo", json={"batch_id": batch_id})
        assert resp.status_code == 200
        assert resp.json()["restored"] == 2

        # Verify restored
        conn = sqlite3.connect(main.DB_PATH)
        conn.row_factory = sqlite3.Row
        row1 = conn.execute("SELECT triaged_at, starred, category FROM tabs WHERE id = ?", (t1,)).fetchone()
        assert row1["triaged_at"] is None
        assert row1["starred"] == 0
        assert row1["category"] is None
        conn.close()

    def test_undo_removes_pending_close(self, app_client):
        client, main = app_client
        _seed_tab(main, url="http://example.com/uc", suggested_category="archive")

        resp = client.post("/api/triage/auto")
        batch_id = resp.json()["batch_id"]

        # URL should be in pending close
        pending = client.get("/api/tabs/pending-close").json()["urls"]
        assert "http://example.com/uc" in pending

        # Undo
        client.post("/api/triage/auto/undo", json={"batch_id": batch_id})

        # URL should be removed from pending close
        pending = client.get("/api/tabs/pending-close").json()["urls"]
        assert "http://example.com/uc" not in pending

    def test_undo_invalid_batch(self, app_client):
        client, main = app_client
        resp = client.post("/api/triage/auto/undo", json={"batch_id": "nonexist"})
        assert resp.status_code == 404

    def test_undo_only_works_once(self, app_client):
        client, main = app_client
        _seed_tab(main, url="http://example.com/once", suggested_category="read-later")

        resp = client.post("/api/triage/auto")
        batch_id = resp.json()["batch_id"]

        # First undo works
        resp = client.post("/api/triage/auto/undo", json={"batch_id": batch_id})
        assert resp.status_code == 200

        # Second undo fails
        resp = client.post("/api/triage/auto/undo", json={"batch_id": batch_id})
        assert resp.status_code == 404


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


class TestIgnoredDomains:
    def test_list_empty(self, app_client):
        client, main = app_client
        resp = client.get("/api/ignored-domains")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_add_domain(self, app_client):
        client, main = app_client
        resp = client.post("/api/ignored-domains", json={"domain": "mail.google.com"})
        assert resp.status_code == 200
        assert resp.json()["domain"] == "mail.google.com"

        # Verify it shows in list
        resp = client.get("/api/ignored-domains")
        domains = [d["domain"] for d in resp.json()]
        assert "mail.google.com" in domains

    def test_add_duplicate_domain(self, app_client):
        client, main = app_client
        client.post("/api/ignored-domains", json={"domain": "mail.google.com"})
        resp = client.post("/api/ignored-domains", json={"domain": "mail.google.com"})
        assert resp.status_code == 200  # no error, idempotent

        resp = client.get("/api/ignored-domains")
        assert len(resp.json()) == 1

    def test_add_strips_www(self, app_client):
        client, main = app_client
        resp = client.post("/api/ignored-domains", json={"domain": "www.example.com"})
        assert resp.json()["domain"] == "example.com"

    def test_add_empty_domain_fails(self, app_client):
        client, main = app_client
        resp = client.post("/api/ignored-domains", json={"domain": ""})
        assert resp.status_code == 400

    def test_delete_domain(self, app_client):
        client, main = app_client
        client.post("/api/ignored-domains", json={"domain": "mail.google.com"})
        resp = client.delete("/api/ignored-domains/mail.google.com")
        assert resp.status_code == 200

        resp = client.get("/api/ignored-domains")
        assert resp.json() == []

    @patch("main.summarize_tab")
    @patch("main.cluster_tabs")
    def test_capture_skips_ignored_domain(self, mock_cluster, mock_summarize, app_client):
        client, main = app_client
        mock_summarize.return_value = {"summary": "Test", "suggested_category": "read-later", "tags": []}
        mock_cluster.return_value = []

        # Add domain to ignore list
        client.post("/api/ignored-domains", json={"domain": "mail.google.com"})

        # Capture with ignored domain + normal domain
        resp = client.post("/api/capture", json={
            "window_title": "Test",
            "tabs": [
                {"url": "https://mail.google.com/mail/u/0/", "title": "Gmail"},
                {"url": "http://example.com/article", "title": "Article"},
            ],
        })
        data = resp.json()
        assert data["tab_count"] == 1  # only the non-ignored tab
        assert data["skipped"] == 1

    @patch("main.summarize_tab")
    @patch("main.cluster_tabs")
    def test_capture_after_removing_ignored_domain(self, mock_cluster, mock_summarize, app_client):
        client, main = app_client
        mock_summarize.return_value = {"summary": "Test", "suggested_category": "read-later", "tags": []}
        mock_cluster.return_value = []

        # Add then remove domain
        client.post("/api/ignored-domains", json={"domain": "mail.google.com"})
        client.delete("/api/ignored-domains/mail.google.com")

        # Capture - should now be accepted
        resp = client.post("/api/capture", json={
            "window_title": "Test",
            "tabs": [
                {"url": "https://mail.google.com/mail/u/0/", "title": "Gmail"},
            ],
        })
        assert resp.json()["tab_count"] == 1


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

    @patch("main.summarize_tab")
    @patch("main.cluster_tabs")
    def test_capture_creates_progress(self, mock_cluster, mock_summarize, app_client):
        client, main = app_client
        mock_summarize.return_value = {"summary": "Test", "suggested_category": "read-later", "tags": []}
        mock_cluster.return_value = []

        resp = client.post("/api/capture", json={
            "window_title": "Test",
            "tabs": [{"url": "http://example.com/p1", "title": "P1"}],
        })
        session_id = resp.json()["session_id"]
        assert session_id is not None

        # Check progress was created
        assert session_id in main._session_progress
        progress = main._session_progress[session_id]
        assert progress["total"] == 1
        assert progress["phase"] in ("summarizing", "done")


    @patch("main.summarize_tab")
    @patch("main.cluster_tabs")
    def test_capture_with_behavior_data(self, mock_cluster, mock_summarize, app_client):
        client, main_mod = app_client
        mock_summarize.return_value = {"summary": "Test", "suggested_category": "read-later", "tags": []}
        mock_cluster.return_value = []

        behavior = {
            "scroll_depth_pct": 72,
            "active_time_sec": 45,
            "scroll_events": 12,
            "click_count": 3,
            "keypress_count": 0,
            "selections": ["some text"],
        }
        resp = client.post("/api/capture", json={
            "window_title": "Test",
            "tabs": [{"url": "http://example.com/beh1", "title": "Behavior Tab", "behavior": behavior}],
        })
        assert resp.status_code == 200
        session_id = resp.json()["session_id"]

        # Verify behavior_data stored in DB
        import sqlite3
        conn = sqlite3.connect(main_mod.DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT behavior_data FROM tabs WHERE session_id = ?", (session_id,)).fetchone()
        conn.close()
        assert row is not None
        bd = json.loads(row["behavior_data"])
        assert bd["scroll_depth_pct"] == 72
        assert bd["active_time_sec"] == 45
        assert bd["selections"] == ["some text"]

    @patch("main.summarize_tab")
    @patch("main.cluster_tabs")
    def test_capture_without_behavior_data(self, mock_cluster, mock_summarize, app_client):
        client, main_mod = app_client
        mock_summarize.return_value = {"summary": "Test", "suggested_category": "read-later", "tags": []}
        mock_cluster.return_value = []

        resp = client.post("/api/capture", json={
            "window_title": "Test",
            "tabs": [{"url": "http://example.com/nobeh1", "title": "No Behavior"}],
        })
        assert resp.status_code == 200
        session_id = resp.json()["session_id"]

        import sqlite3
        conn = sqlite3.connect(main_mod.DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT behavior_data FROM tabs WHERE session_id = ?", (session_id,)).fetchone()
        conn.close()
        assert row["behavior_data"] is None


class TestProgress:
    def test_progress_no_session(self, app_client):
        client, main = app_client
        resp = client.get("/api/capture/999/progress")
        assert resp.status_code == 200
        data = resp.json()
        assert data["phase"] == "done"
        assert data["total"] == 0

    def test_progress_active_session(self, app_client):
        client, main = app_client
        # Manually set progress
        main._session_progress[42] = {"total": 10, "completed": 3, "phase": "summarizing", "clusters": 0}

        resp = client.get("/api/capture/42/progress")
        data = resp.json()
        assert data["total"] == 10
        assert data["completed"] == 3
        assert data["phase"] == "summarizing"


class TestTriageData:
    def test_triage_data_empty(self, app_client):
        client, main = app_client
        resp = client.get("/api/triage-data")
        assert resp.status_code == 200
        data = resp.json()
        assert data["sessions"] == []
        assert data["ignored_domains"] == []

    def test_triage_data_with_tabs(self, app_client):
        client, main = app_client
        _seed_tab(main, url="http://example.com/td1", title="TD1")
        _seed_tab(main, url="http://example.com/td2", title="TD2")

        resp = client.get("/api/triage-data")
        data = resp.json()
        assert len(data["sessions"]) == 1
        assert len(data["sessions"][0]["tabs"]) == 2

    def test_triage_data_includes_ignored_domains(self, app_client):
        client, main = app_client
        client.post("/api/ignored-domains", json={"domain": "spam.com"})

        resp = client.get("/api/triage-data")
        assert "spam.com" in resp.json()["ignored_domains"]


class TestHostedPage:
    def test_root_serves_html(self, app_client):
        client, main = app_client
        resp = client.get("/")
        assert resp.status_code == 200
        assert "TabTriage" in resp.text
        assert "text/html" in resp.headers["content-type"]
