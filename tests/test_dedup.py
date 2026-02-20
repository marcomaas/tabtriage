"""Tests for dedup logic in the capture endpoint."""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))


@pytest.fixture
def test_db(tmp_path):
    """Create a real SQLite DB with schema for dedup tests."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    schema = (Path(__file__).parent.parent / "schema.sql").read_text()
    conn.executescript(schema)
    conn.commit()
    conn.close()
    return str(db_path)


@pytest.fixture
def app_client(tmp_path, test_db):
    """Create a FastAPI TestClient with mocked config."""
    html_path = str(tmp_path / "triage.html")
    # Write a minimal template
    template_path = Path(__file__).parent.parent / "backend" / "triage_template.html"
    if not template_path.exists():
        # Create minimal template for tests
        (tmp_path / "template.html").write_text("<!--/*DATA_PLACEHOLDER*/[]-->")
        tpl = tmp_path / "template.html"
    else:
        tpl = template_path

    config = {
        "backend_port": 5111,
        "backend_host": "0.0.0.0",
        "notion_config": "/dev/null",
        "triage_html_path": html_path,
        "db_path": test_db,
        "claude_timeout": 300,
        "max_content_length": 50000,
    }

    with patch.dict("sys.modules", {}):
        pass

    # Patch config and imports before importing main
    config_json = json.dumps(config)
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(config_json)

    # We need to patch at module level before import
    import importlib

    with patch("summarizer.subprocess.run") as mock_claude, \
         patch("notion_client.Path") as mock_nc_path, \
         patch.object(Path, "read_text", wraps=Path.read_text) as _:

        # Mock notion config loading
        mock_nc_path.return_value.read_text.return_value = config_json

        # Reimport main with patched config
        import main
        main.DB_PATH = test_db
        main.TRIAGE_HTML_PATH = html_path

        # Mock _generate_triage_html to avoid template issues
        main._generate_triage_html = MagicMock()

        # Mock summarize_tab to be instant
        mock_claude.return_value = MagicMock(
            returncode=0,
            stdout="SUMMARY: Test.\nCATEGORY: read-later\nTAGS: test",
            stderr="",
        )

        from fastapi.testclient import TestClient
        client = TestClient(main.app)
        yield client, main


class TestDedup:
    def test_same_url_within_session_skipped(self, app_client):
        client, main = app_client
        resp = client.post("/api/capture", json={
            "window_title": "Test",
            "tabs": [
                {"url": "http://example.com/a", "title": "A"},
                {"url": "http://example.com/a", "title": "A duplicate"},
            ],
        })
        data = resp.json()
        assert data["tab_count"] == 1
        assert data["skipped"] == 1

    def test_url_within_24h_skipped(self, app_client):
        client, main = app_client
        # First capture
        resp1 = client.post("/api/capture", json={
            "window_title": "Session 1",
            "tabs": [{"url": "http://example.com/b", "title": "B"}],
        })
        assert resp1.json()["tab_count"] == 1

        # Second capture same URL - should skip
        resp2 = client.post("/api/capture", json={
            "window_title": "Session 2",
            "tabs": [{"url": "http://example.com/b", "title": "B again"}],
        })
        assert resp2.json()["tab_count"] == 0
        assert resp2.json()["status"] == "all_duplicates"

    def test_tabtriage_own_url_skipped(self, app_client):
        client, main = app_client
        resp = client.post("/api/capture", json={
            "window_title": "Test",
            "tabs": [
                {"url": "file:///Users/test/TabTriage/index.html", "title": "TabTriage"},
                {"url": "http://example.com/real", "title": "Real Tab"},
            ],
        })
        data = resp.json()
        assert data["tab_count"] == 1
        assert data["skipped"] == 1

    def test_all_dupes_deletes_session(self, app_client):
        client, main = app_client
        # Capture
        client.post("/api/capture", json={
            "window_title": "S1",
            "tabs": [{"url": "http://example.com/c", "title": "C"}],
        })
        # All dupes → session not created
        resp = client.post("/api/capture", json={
            "window_title": "S2",
            "tabs": [{"url": "http://example.com/c", "title": "C"}],
        })
        assert resp.json()["session_id"] is None

    def test_old_url_accepted(self, app_client):
        """URL older than 24h should be accepted again."""
        client, main = app_client
        import sqlite3 as sq

        # Insert a tab with old timestamp
        conn = sq.connect(main.DB_PATH)
        conn.execute("INSERT INTO sessions (id, window_title, hostname) VALUES (999, 'old', 'test')")
        conn.execute(
            "INSERT INTO tabs (session_id, url, title, captured_at) VALUES (999, 'http://example.com/old', 'Old', datetime('now', '-2 days'))"
        )
        conn.commit()
        conn.close()

        # Should accept since >24h old
        resp = client.post("/api/capture", json={
            "window_title": "New",
            "tabs": [{"url": "http://example.com/old", "title": "Old revisited"}],
        })
        assert resp.json()["tab_count"] == 1
