"""Shared fixtures for TabTriage tests."""

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add backend to path so we can import modules
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))


@pytest.fixture
def db(tmp_path):
    """In-memory SQLite DB initialized with schema + hostname column."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    schema = (Path(__file__).parent.parent / "schema.sql").read_text()
    conn.executescript(schema)

    conn.commit()
    return conn


@pytest.fixture
def config(tmp_path):
    """Test config pointing to tmp paths."""
    db_path = str(tmp_path / "test.db")
    html_path = str(tmp_path / "triage.html")
    cfg = {
        "backend_port": 5111,
        "backend_host": "0.0.0.0",
        "notion_config": "/dev/null",
        "triage_html_path": html_path,
        "db_path": db_path,
        "claude_timeout": 300,
        "max_content_length": 50000,
    }
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(cfg))
    return cfg
