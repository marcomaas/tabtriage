# Chrome Web Store Listing — TabTriage

## Kurzbeschreibung (132 Zeichen max)

Capture all tabs, get AI summaries, triage fast. Stop tab hoarding — close tabs guilt-free with smart categorization.

## Detaillierte Beschreibung

### English

**TabTriage — AI-Powered Tab Triage**

Stop losing tabs. Start making decisions.

You have 47 tabs open. Some are articles you want to read. Some are tools you need to remember. Some are just... there. You're afraid to close them because you might lose something important.

TabTriage fixes this in one click:

1. Click the extension icon → "Capture This Window"
2. Every tab's full text content is extracted using Mozilla Readability
3. AI (Claude) reads each page and generates a 2-3 sentence summary + topic tags
4. Tabs are automatically clustered by topic ("Finance", "AI Tools", "News")
5. A clean triage interface lets you decide: Read Later, Reference, Actionable, or Archive
6. Optionally route tabs to Notion (Links DB, Backlog, Tasks, or specific projects)
7. Close triaged tabs directly from the interface

**Key Features:**
- Full text extraction even on cluttered pages (Mozilla Readability)
- AI-generated summaries and topic tags (Claude)
- Smart topic clustering across all captured tabs
- Triage interface with inline editing and bulk operations
- Notion integration for routing (Links, Backlog, Tasks, Projects)
- Close browser tabs directly after triaging
- Full-text search across all captured sessions
- Archive view with browsing pattern analytics
- Multi-machine support with hostname tracking
- Automatic deduplication (same URL within 24h)
- Mobile-friendly with swipe gestures
- Works with Chrome, Arc, and any Chromium-based browser

**How it works:**
TabTriage consists of a Chrome extension (this) and a local Python backend. The extension captures tabs and extracts content. The backend stores everything in SQLite, generates AI summaries via Claude CLI, and serves a triage HTML page.

**Requirements:**
- Python 3.11+ running locally
- Claude Code CLI installed and authenticated
- Self-hosted backend (FastAPI on localhost)

**Privacy:**
All data stays on your machine. Tab content is processed locally via Claude CLI. No data is sent to third-party servers (except Notion, if you configure it). The extension only communicates with your local backend.

**Open Source:**
TabTriage is MIT-licensed. Source code: https://github.com/marcomaas/tabtriage

---

### Deutsch

**TabTriage — KI-gesteuerte Tab-Triage**

Schluss mit Tab-Horten. Fang an, Entscheidungen zu treffen.

Du hast 47 Tabs offen. Manche sind Artikel, die du lesen willst. Manche sind Tools, die du dir merken willst. Manche sind einfach... da. Du traust dich nicht, sie zu schließen, weil du etwas Wichtiges verlieren könntest.

TabTriage löst das mit einem Klick:

1. Extension-Icon klicken → "Capture This Window"
2. Der Volltext jedes Tabs wird per Mozilla Readability extrahiert
3. KI (Claude) liest jede Seite und erstellt eine 2-3 Satz-Zusammenfassung + Themen-Tags
4. Tabs werden automatisch nach Thema gruppiert ("Finanzen", "KI-Tools", "News")
5. Ein übersichtliches Triage-Interface zum Entscheiden: Später lesen, Referenz, Actionable, Archiv
6. Optional Weiterleitung an Notion (Links-DB, Backlog, Tasks, Projekte)
7. Triaged Tabs direkt aus dem Interface schließen

**Open Source & Privacy:**
Alle Daten bleiben auf deinem Rechner. MIT-Lizenz.

## Store-Assets (benötigt)

### Icon
- 128x128 PNG (Store-Icon)
- Bereits vorhanden: `extension/icon128.png`

### Screenshots (min. 1, max. 5)
- Format: 1280x800 oder 640x400 PNG/JPG
- Empfohlen:
  1. Triage-Übersicht mit geclusterten Tabs (Hauptansicht)
  2. Detail-Ansicht eines einzelnen Tabs mit Summary + Kategorien
  3. Extension-Popup beim Capturen
  4. Archiv-/Suchansicht
  5. Notion-Routing in Aktion

### Promo-Grafiken (optional)
- Small Promo Tile: 440x280 PNG
- Marquee Promo Tile: 1400x560 PNG

## Kategorie & Tags

- **Kategorie**: Productivity
- **Tags**: tab manager, AI, summarizer, productivity, bookmarks, tab organizer
