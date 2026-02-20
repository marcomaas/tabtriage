"""Claude CLI summarizer, tagger, and clusterer for tab content."""

import json
import logging
import os
import subprocess

log = logging.getLogger(__name__)

CLAUDE_TIMEOUT = 300

# Clean environment for Claude CLI subprocess (remove CLAUDECODE to avoid nesting check)
_clean_env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
_clean_env["PATH"] = "/usr/local/bin:/usr/bin:/bin"


def summarize_tab(title: str, url: str, content: str | None) -> dict:
    """Summarize a tab, generate tags, and suggest category."""
    if not content or len(content.strip()) < 100:
        return {
            "summary": f"[Kein ausreichender Inhalt extrahiert für: {title}]",
            "suggested_category": "archive",
            "tags": [],
        }

    text = content[:30000] if len(content) > 30000 else content

    prompt = f"""Analysiere diesen Browser-Tab und gib eine strukturierte Antwort.

Titel: {title}
URL: {url}

Inhalt (Auszug):
{text}

Antworte EXAKT in diesem Format (keine Markdown-Formatierung):
SUMMARY: [2-3 Sätze auf Deutsch, was der Inhalt behandelt und warum es relevant sein könnte]
CATEGORY: [genau eine von: read-later, reference, actionable, archive]
TAGS: [komma-getrennte Tags auf Deutsch, 3-6 Stück, z.B.: KI, Recht, Startup, Finanzen, Gesundheit, Tool, Tutorial, Nachrichten]

Kategorien:
- read-later: Artikel/Videos die man lesen/schauen sollte
- reference: Dokumentation, Tools, Ressourcen zum Nachschlagen
- actionable: Enthält konkrete Aufgaben oder Handlungsbedarf
- archive: Nicht mehr relevant, bereits erledigt, oder Spam

Tags sollen thematisch sein und für Filter/Suche nützlich. Nutze etablierte Begriffe."""

    try:
        result = subprocess.run(
            ["/usr/local/bin/claude", "-p", "--output-format", "text"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT,
            env=_clean_env,
        )
        if result.returncode != 0:
            log.error("Claude CLI failed (rc=%d): %s", result.returncode, result.stderr[:200])
            return {"summary": f"[Zusammenfassung fehlgeschlagen: {title}]", "suggested_category": "read-later", "tags": []}

        return _parse_response(result.stdout.strip(), title)
    except FileNotFoundError:
        log.error("Claude CLI not found.")
        return {"summary": "[Claude CLI nicht gefunden]", "suggested_category": "read-later", "tags": []}
    except subprocess.TimeoutExpired:
        log.error("Claude CLI timed out for: %s", title)
        return {"summary": f"[Timeout: {title}]", "suggested_category": "read-later", "tags": []}


def _parse_response(text: str, title: str) -> dict:
    summary = ""
    category = "read-later"
    tags = []

    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("SUMMARY:"):
            summary = line[len("SUMMARY:"):].strip()
        elif line.startswith("CATEGORY:"):
            cat = line[len("CATEGORY:"):].strip().lower()
            if cat in ("read-later", "reference", "actionable", "archive"):
                category = cat
        elif line.startswith("TAGS:"):
            raw = line[len("TAGS:"):].strip()
            tags = [t.strip() for t in raw.split(",") if t.strip()]

    if not summary:
        summary = text[:500] if text else f"[Keine Zusammenfassung für: {title}]"

    return {"summary": summary, "suggested_category": category, "tags": tags}


def cluster_tabs(tabs: list[dict], projects: list[dict]) -> list[dict]:
    """Cluster tabs by topic and suggest project assignments."""
    if not tabs:
        return []

    tab_lines = []
    for t in tabs:
        tags_str = ""
        if t.get("tags"):
            try:
                parsed = json.loads(t["tags"]) if isinstance(t["tags"], str) else t["tags"]
                tags_str = f" [Tags: {', '.join(parsed)}]"
            except:
                pass
        tab_lines.append(f'- ID={t["id"]}: "{t["title"]}" ({t["url"]}) — {(t.get("summary") or "")[:200]}{tags_str}')

    prompt = f"""Analysiere diese Browser-Tabs und gruppiere sie thematisch.
Ordne sie den passenden Projekten zu, wenn inhaltlich sinnvoll.

TABS:
{chr(10).join(tab_lines)}

VERFÜGBARE PROJEKTE (ID: Name):
{chr(10).join(f'- {p["id"]}: {p["name"]}' for p in projects[:50])}

Erstelle thematische Cluster und ordne Projekte zu.
Antworte NUR als JSON-Array (kein anderer Text):
[
  {{"tab_id": 1, "cluster_id": "news", "cluster_label": "Nachrichten & Aktuelles", "suggested_project_id": null}},
  {{"tab_id": 2, "cluster_id": "tools", "cluster_label": "KI-Tools", "suggested_project_id": "abc-123"}}
]

Regeln:
- cluster_id: kurzer slug (news, tools, finance, health, dev, learning, etc.)
- cluster_label: deutscher Anzeigename
- suggested_project_id: UUID wenn passend, sonst null
- Jeder Tab genau ein Cluster
- Clustere aggressiv: lieber zu wenige Cluster als zu viele"""

    try:
        result = subprocess.run(
            ["/usr/local/bin/claude", "-p", "--output-format", "text"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT,
            env=_clean_env,
        )
        if result.returncode != 0:
            log.error("Clustering failed: %s", result.stderr[:200])
            return []

        return _parse_clusters(result.stdout.strip(), tabs)
    except Exception as e:
        log.error("Clustering error: %s", e)
        return []


def _parse_clusters(text: str, tabs: list[dict]) -> list[dict]:
    start = text.find("[")
    end = text.rfind("]") + 1
    if start < 0 or end <= start:
        log.error("No JSON array in clustering response")
        return []

    try:
        clusters = json.loads(text[start:end])
        tab_ids = {t["id"] for t in tabs}
        return [
            {
                "tab_id": c["tab_id"],
                "cluster_id": c.get("cluster_id", "other"),
                "cluster_label": c.get("cluster_label", "Sonstiges"),
                "suggested_project_id": c.get("suggested_project_id"),
            }
            for c in clusters if c.get("tab_id") in tab_ids
        ]
    except json.JSONDecodeError as e:
        log.error("Failed to parse clustering JSON: %s", e)
        return []
