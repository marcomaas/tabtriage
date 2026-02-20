"""Tests for summarizer.py — parsing, CLI mocking, error handling."""

import subprocess
from unittest.mock import patch, MagicMock

import pytest

from summarizer import summarize_tab, _parse_response, cluster_tabs


class TestParseResponse:
    def test_valid_response(self):
        text = (
            "SUMMARY: Dies ist ein Artikel über KI-Entwicklung.\n"
            "CATEGORY: read-later\n"
            "TAGS: KI, Technologie, Forschung"
        )
        result = _parse_response(text, "Test")
        assert result["summary"] == "Dies ist ein Artikel über KI-Entwicklung."
        assert result["suggested_category"] == "read-later"
        assert result["tags"] == ["KI", "Technologie", "Forschung"]

    def test_missing_summary_uses_raw_text(self):
        text = "Some random text without proper format"
        result = _parse_response(text, "Test")
        assert result["summary"] == "Some random text without proper format"
        assert result["suggested_category"] == "read-later"
        assert result["tags"] == []

    def test_invalid_category_defaults_to_read_later(self):
        text = (
            "SUMMARY: Test summary.\n"
            "CATEGORY: invalid-cat\n"
            "TAGS: Tag1"
        )
        result = _parse_response(text, "Test")
        assert result["suggested_category"] == "read-later"

    def test_valid_categories(self):
        for cat in ("read-later", "reference", "actionable", "archive"):
            text = f"SUMMARY: Test.\nCATEGORY: {cat}\nTAGS: x"
            result = _parse_response(text, "Test")
            assert result["suggested_category"] == cat

    def test_empty_text(self):
        result = _parse_response("", "Fallback Title")
        assert "Fallback Title" in result["summary"]

    def test_multiline_summary_takes_first_line(self):
        text = "SUMMARY: First line of summary.\nMore text.\nCATEGORY: archive\nTAGS: a, b"
        result = _parse_response(text, "Test")
        assert result["summary"] == "First line of summary."


class TestSummarizeTab:
    def test_short_content_returns_fallback(self):
        result = summarize_tab("Title", "http://example.com", "short")
        assert "[Kein ausreichender Inhalt" in result["summary"]
        assert result["suggested_category"] == "archive"
        assert result["tags"] == []

    def test_none_content_returns_fallback(self):
        result = summarize_tab("Title", "http://example.com", None)
        assert "[Kein ausreichender Inhalt" in result["summary"]

    @patch("summarizer.subprocess.run")
    def test_successful_summarization(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="SUMMARY: Great article.\nCATEGORY: reference\nTAGS: Python, Dev",
            stderr="",
        )
        result = summarize_tab("Title", "http://example.com", "x" * 200)
        assert result["summary"] == "Great article."
        assert result["suggested_category"] == "reference"
        mock_run.assert_called_once()

    @patch("summarizer.subprocess.run")
    def test_cli_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=300)
        result = summarize_tab("Title", "http://example.com", "x" * 200)
        assert "[Timeout" in result["summary"]

    @patch("summarizer.subprocess.run")
    def test_cli_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        result = summarize_tab("Title", "http://example.com", "x" * 200)
        assert "[Claude CLI nicht gefunden]" in result["summary"]

    @patch("summarizer.subprocess.run")
    def test_cli_nonzero_exit(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        result = summarize_tab("Title", "http://example.com", "x" * 200)
        assert "[Zusammenfassung fehlgeschlagen" in result["summary"]


class TestClusterTabs:
    def test_empty_list(self):
        assert cluster_tabs([], []) == []

    @patch("summarizer.subprocess.run")
    def test_valid_clustering(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='[{"tab_id": 1, "cluster_id": "dev", "cluster_label": "Entwicklung", "suggested_project_id": null}]',
            stderr="",
        )
        tabs = [{"id": 1, "title": "Test", "url": "http://x.com", "summary": "Dev article"}]
        result = cluster_tabs(tabs, [])
        assert len(result) == 1
        assert result[0]["cluster_id"] == "dev"

    @patch("summarizer.subprocess.run")
    def test_invalid_json_returns_empty(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="not json at all", stderr="")
        tabs = [{"id": 1, "title": "Test", "url": "http://x.com", "summary": "s"}]
        result = cluster_tabs(tabs, [])
        assert result == []

    @patch("summarizer.subprocess.run")
    def test_filters_unknown_tab_ids(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='[{"tab_id": 1, "cluster_id": "a", "cluster_label": "A"}, {"tab_id": 999, "cluster_id": "b", "cluster_label": "B"}]',
            stderr="",
        )
        tabs = [{"id": 1, "title": "Test", "url": "http://x.com", "summary": "s"}]
        result = cluster_tabs(tabs, [])
        assert len(result) == 1
        assert result[0]["tab_id"] == 1
