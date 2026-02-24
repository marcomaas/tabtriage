"""Claude CLI summarizer, tagger, and clusterer for tab content."""

import json
import logging
import os
import subprocess

log = logging.getLogger(__name__)

CLAUDE_TIMEOUT = 300

# Clean environment for Claude CLI subprocess
# Strip CLAUDECODE (causes "nested session" error) but KEEP CLAUDE_CONFIG_DIR (needed for auth)
_clean_env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDECODE")}
_clean_env["PATH"] = "/usr/local/bin:/usr/bin:/bin"


def summarize_tab(title: str, url: str, content: str | None) -> dict:
    """Summarize a tab, generate tags, and suggest category."""
    if not content or len(content.strip()) < 100:
        return _summarize_from_title(title, url)

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


# Domains where content extraction typically fails
_DIFFICULT_DOMAINS = {
    "x.com": "Social-Media-Post (Tweet/Thread)",
    "twitter.com": "Social-Media-Post (Tweet/Thread)",
    "youtube.com": "YouTube-Video",
    "youtu.be": "YouTube-Video",
    "medium.com": "Medium-Artikel (Paywall)",
    "google.com": "Google-Suchergebnis oder Google-Dienst",
    "docs.google.com": "Google-Dokument",
    "linkedin.com": "LinkedIn-Post oder -Profil",
    "reddit.com": "Reddit-Diskussion",
    "github.com": "GitHub-Repository oder -Seite",
}


def _summarize_from_title(title: str, url: str) -> dict:
    """Fallback: summarize using only title + URL when no content is available."""
    from urllib.parse import urlparse

    domain = ""
    try:
        domain = urlparse(url).hostname.replace("www.", "")
    except Exception:
        pass

    domain_hint = ""
    for d, hint in _DIFFICULT_DOMAINS.items():
        if d in domain:
            domain_hint = f"\nHinweis: Dies ist ein {hint}. Die Seite liefert keinen extrahierbaren Text."
            break

    prompt = f"""Basierend auf dem Titel und der URL, erstelle eine Einschätzung dieses Browser-Tabs.
Es liegt kein extrahierter Seiteninhalt vor — nutze nur den Titel und die URL.

Titel: {title}
URL: {url}{domain_hint}

Antworte EXAKT in diesem Format (keine Markdown-Formatierung):
SUMMARY: [1-2 Sätze auf Deutsch: Was könnte der Inhalt behandeln, basierend auf Titel und URL]
CATEGORY: [genau eine von: read-later, reference, actionable, archive]
TAGS: [komma-getrennte Tags auf Deutsch, 2-4 Stück]

Kategorien:
- read-later: Artikel/Videos die man lesen/schauen sollte
- reference: Dokumentation, Tools, Ressourcen zum Nachschlagen
- actionable: Enthält konkrete Aufgaben oder Handlungsbedarf
- archive: Nicht mehr relevant, bereits erledigt, oder Spam"""

    try:
        result = subprocess.run(
            ["/usr/local/bin/claude", "-p", "--output-format", "text"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=120,
            env=_clean_env,
        )
        if result.returncode != 0:
            log.error("Title-only summarize failed (rc=%d): %s", result.returncode, result.stderr[:200])
            return {"summary": f"[Kein ausreichender Inhalt extrahiert für: {title}]", "suggested_category": "archive", "tags": []}

        return _parse_response(result.stdout.strip(), title)
    except Exception as e:
        log.error("Title-only summarize error: %s", e)
        return {"summary": f"[Kein ausreichender Inhalt extrahiert für: {title}]", "suggested_category": "archive", "tags": []}


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


def analyze_content(tabs: list[dict]) -> dict:
    """Deep content analysis across multiple tabs via Claude CLI."""
    if not tabs:
        return {"error": "Keine Tabs zur Analyse."}

    tab_lines = []
    for t in tabs:
        content_preview = (t.get("content") or "")[:2000]
        tags_str = ""
        if t.get("tags"):
            try:
                parsed = json.loads(t["tags"]) if isinstance(t["tags"], str) else t["tags"]
                tags_str = f" [Tags: {', '.join(parsed)}]"
            except Exception:
                pass
        tab_lines.append(
            f'---\nTitel: {t["title"]}\nURL: {t["url"]}\n'
            f'Summary: {(t.get("summary") or "")[:300]}{tags_str}\n'
            f'Inhalt: {content_preview}\n'
        )

    prompt = f"""Analysiere diese Artikel-Sammlung. Erstelle eine strukturierte Analyse.

ARTIKEL ({len(tabs)} Stück):
{chr(10).join(tab_lines)}

Antworte EXAKT als JSON (kein anderer Text):
{{
  "themes": ["Hauptthema 1", "Hauptthema 2", ...],
  "insights": ["Erkenntnis 1", "Erkenntnis 2", ...],
  "connections": ["Verbindung/Widerspruch 1", ...],
  "recommendations": ["Empfehlung 1", ...],
  "summary": "Ein Absatz der alles verbindet."
}}

Regeln:
- Alle Texte auf Deutsch
- 3-5 Themen, 3-5 Erkenntnisse, 2-4 Verbindungen, 2-3 Empfehlungen
- Konkret und nützlich, keine generischen Aussagen"""

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
            log.error("Insights analysis failed: %s", result.stderr[:200])
            return {"error": "Claude CLI Fehler"}

        text = result.stdout.strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
        return {"error": "Konnte Antwort nicht parsen", "raw": text[:500]}
    except subprocess.TimeoutExpired:
        return {"error": "Timeout bei der Analyse"}
    except Exception as e:
        log.error("Insights error: %s", e)
        return {"error": str(e)}


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
