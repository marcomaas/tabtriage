# TabTriage - Browser Tab Content Triage Tool

## Überblick
Erfasst alle Tabs eines Chrome/Arc-Fensters inkl. Volltext (Readability.js),
summarized per Claude CLI, clustert thematisch, auto-tagged, und bietet eine
Triage-UI zum Kategorisieren und Weiterleiten an Notion-Projekte/Tasks.

## Architektur
- **Chrome Extension** (Manifest V3): Popup → Readability.js + Content Script → Backend
- **Python Backend** (FastAPI, Port 5111): Capture → Dedup → SQLite → Claude Summarize + Tag → Cluster → HTML generieren
- **Statische HTML-Triage-Seite** in Dropbox: 3 Views (Triage/Archiv/Analyse), Inline-Editing, Bulk-Ops, Fulltext-Overlay, Mobile Swipe

## Projektstruktur
```
/Users/marcomaas/browserballet/
├── backend/
│   ├── main.py                 # FastAPI (Port 5111, Host 0.0.0.0)
│   ├── triage_template.html    # HTML-Template (extern, nicht inline)
│   ├── models.py               # Pydantic Models (legacy, inline in main.py)
│   ├── notion_client.py        # Notion API (Links, Parken, Tasks, Projekt-Append)
│   └── summarizer.py           # Claude CLI: Summary + Tags + Clustering
├── extension/
│   ├── manifest.json           # Manifest V3 (permissions: tabs, scripting, storage, alarms)
│   ├── popup.html/js           # Extension Popup mit Backend-URL-Config
│   ├── background.js           # Service Worker: pollt /api/tabs/pending-close (chrome.alarms)
│   ├── content.js              # Readability-Extraktion + OG-Metadaten + Media
│   ├── readability.min.js      # Mozilla Readability v0.6
│   └── icon16/48/128.png       # Extension Icons
├── config/config.json          # Ports, Pfade, Host-Binding
├── data/tabtriage.db           # SQLite + FTS5
├── schema.sql                  # DB-Schema
└── TabTriage.app/              # macOS App-Bundle (Start/Stop toggle)
```

## Wichtige Patterns & Bugs

### Claude CLI im Subprocess
- Vollständiger Pfad: `/usr/local/bin/claude`
- CLAUDECODE env-var muss entfernt werden (sonst "nested session" Fehler)
- summarizer.py verwendet `_clean_env` ohne CLAUDE*-Variablen
- Timeout: 300s pro Tab

### HTML Template
- Template: `backend/triage_template.html` (extern, nicht inline in main.py!)
- Daten-Einbettung: `/*DATA_PLACEHOLDER*/[]` → **inklusive der Klammern ersetzen!**
- Output: `~/Library/CloudStorage/Dropbox-Privat/claude/projekte/TabTriage/index.html`

### FastAPI Route-Ordering
- Statische Routen (`/api/tabs/pending-close`) MÜSSEN VOR parametrischen (`/api/tabs/{tab_id}`) stehen!
- Sonst wird "pending-close" als tab_id geparst → 422 Error

### Dedup
- Innerhalb einer Session: gleiche URL wird übersprungen
- Über Sessions: URL die in letzten 24h erfasst wurde, wird übersprungen
- TabTriage-eigene Seite (`TabTriage/index.html`) wird automatisch ignoriert

### Content-Extraktion
- chrome.scripting.executeScript: erst readability.min.js, dann content.js
- content.js ist IIFE, returnt JSON-String: `{text, og_image, og_description, media}`
- Readability < 100 Zeichen → Fallback auf `document.body.innerText` (SPAs, Paywalls)
- Fallback-Selektoren: `main, article, [role="main"], #__next, #app, #root`
- summarizer.py: Kein Content → Title-only Fallback via Claude (`_summarize_from_title`)
- `_DIFFICULT_DOMAINS` dict gibt Claude Kontext-Hints (x.com, youtube, medium etc.)

## Notion-Integration
Konfiguration aus: `/Users/marcomaas/kur-app/config/notion.json`

| DB | ID | Zweck |
|---|---|---|
| Tasks | 57ec1483-fcbe-4da4-877d-47054c33d0fe | Todos (Next Action / Someday/Maybe) |
| Projects | d725daaf-c5b5-479c-bac5-aa79ee02ebbd | Echte Projekt-DB (~52 Projekte, Autocomplete) |
| Links | aus notion.json | Web Clipper Mastertabelle |
| Parken | aus notion.json | Backlog-Karten (Dashboard=Backlog) |

Routing-Ziele: links, parken, project (append), todo-today, todo-someday
Notion NIEMALS löschen - nur appenden (globale Regel).

## API Endpoints

| Route | Methode | Beschreibung |
|---|---|---|
| `/api/capture` | POST | Tabs empfangen, dedup, speichern, summarize/cluster im Background |
| `/api/sessions` | GET | Alle Sessions mit Tab-Count |
| `/api/tabs/pending-close` | GET | URLs die geschlossen werden sollen (Extension pollt) |
| `/api/tabs/confirm-close` | POST | Extension bestätigt Tab-Schließung |
| `/api/tabs/request-close-bulk` | POST | Mehrere Tabs zum Schließen markieren |
| `/api/tabs/{id}` | GET | Tab-Detail |
| `/api/tabs/{id}/content` | GET | Volltext eines Tabs |
| `/api/tabs/{id}/request-close` | POST | Einzelnen Tab zum Schließen markieren |
| `/api/tabs/{id}/star` | POST | Star/Unstar |
| `/api/triage` | POST | Einzelne Triage-Entscheidung |
| `/api/triage/bulk` | POST | Bulk-Triage |
| `/api/search` | GET | FTS5-Volltextsuche |
| `/api/notion/projects` | GET | Projekte aus Projects-DB für Dropdown (paginiert) |
| `/api/notion/projects` | POST | Neues Projekt in Projects-DB anlegen |

## Sessions-DB
- Hostname pro Session (via `platform.node()`)
- Datum/Uhrzeit in UI angezeigt

## Triage-UI Features
- 3 Views: Triage (offene), Archiv (gespeicherte), Analyse (Charts)
- Karten klappen auf per Klick → Controls sichtbar
- Speichern/Speichern+Schließen/Verwerfen/Nur Schließen
- Gespeicherte Items animieren weg (done → hiding)
- Bulk-Bar bei Mehrfachauswahl
- Cluster-Gruppierung mit Projekt-Vorschlag
- Tags als Chips auf jeder Karte
- Fulltext als Overlay-Modal
- Mobile: Swipe rechts=Save+Close, links=Dismiss
- Suche über Titel, Summary, URL, Tags, Cluster
- Analytics: Quellen, Tags, Kategorien, Cluster, Timeline

## Starten
```bash
# Backend manuell (OHNE LaunchAgent wegen CLAUDECODE-Bug)
cd ~/browserballet/backend && python3.11 main.py

# Extension laden
chrome://extensions → Developer Mode → Load Unpacked → ~/browserballet/extension

# Triage-Seite
open ~/Library/CloudStorage/Dropbox-Privat/claude/projekte/TabTriage/index.html
```

## Bekannte Issues
- LaunchAgent startet Backend mit CLAUDECODE env-var → Claude CLI nested session Fehler
- Manche Seiten (NYTimes, OpenAI) liefern keinen Content (CSP/Paywall)
- Python muss `python3.11` sein (python3 = 3.9.6, zu alt für FastAPI)
