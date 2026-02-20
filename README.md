# TabTriage

**Stop losing tabs. Start making decisions.**

TabTriage captures all open browser tabs in a window, extracts their full text content, summarizes each one with AI, clusters them by topic, and gives you a fast triage interface to decide what to do with each one — read later, archive, send to your project management, or just close.

![TabTriage Triage View](docs/screenshot-triage.png)

## The Problem

You have 47 tabs open. Some are articles you want to read. Some are tools you need to remember. Some are just... there. You're afraid to close them because you might lose something important. So they stay open, eating memory, causing guilt, and making your browser slower every day.

## The Solution

One click captures everything. AI reads and summarizes each tab. You make quick decisions in a clean interface. Tabs get routed to the right place. Then you close them — guilt-free.

## Features

- **One-Click Capture** — Chrome/Arc extension captures all tabs in the current window
- **Full Text Extraction** — Uses Mozilla Readability to extract clean article text, even on cluttered pages
- **AI Summaries & Tags** — Claude generates 2-3 sentence summaries and topic tags for each tab
- **Smart Clustering** — Tabs are automatically grouped by topic ("Finance", "AI Tools", "News")
- **Triage Interface** — Clean, fast UI with inline editing, bulk operations, and keyboard shortcuts
- **Notion Integration** — Route tabs to Links DB, Backlog, Tasks, or specific project pages
- **Tab Closing** — Close triaged tabs directly from the interface (extension handles it)
- **Archive & Analytics** — Track what you've processed, see your browsing patterns
- **Multi-Machine** — Works across multiple computers with hostname tracking
- **Deduplication** — Automatically skips duplicate URLs within 24 hours
- **Mobile-Friendly** — Swipe right to save+close, swipe left to dismiss

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────────┐
│ Chrome Extension │────▶│  FastAPI Backend  │────▶│  Static HTML Page   │
│  (Manifest V3)   │     │   (Port 5111)     │     │   (Dropbox/local)   │
│                  │◀────│                   │     │                     │
│ - Tab capture    │poll │ - SQLite + FTS5   │     │ - Triage/Archive/   │
│ - Content extract│     │ - Claude CLI      │     │   Analytics views   │
│ - Tab closing    │     │ - Notion API      │     │ - Inline editing    │
└─────────────────┘     └──────────────────┘     └─────────────────────┘
```

## Quick Start

### Prerequisites

- Python 3.11+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated
- Chrome, Arc, or any Chromium-based browser
- (Optional) Notion account with API key for routing

### 1. Clone & Install

```bash
git clone https://github.com/marcomaas/tabtriage.git
cd tabtriage
pip install fastapi uvicorn httpx
```

### 2. Configure

```bash
cp config/config.example.json config/config.json
# Edit config.json with your paths
```

Key settings in `config/config.json`:
```json
{
  "backend_port": 5111,
  "backend_host": "0.0.0.0",
  "db_path": "/path/to/tabtriage.db",
  "triage_html_path": "/path/to/output/index.html",
  "notion_config": "/path/to/notion.json",
  "claude_timeout": 300,
  "max_content_length": 50000
}
```

### 3. Initialize Database

```bash
sqlite3 data/tabtriage.db < schema.sql
```

### 4. Start Backend

```bash
cd backend
python3.11 main.py
```

### 5. Load Extension

1. Open `chrome://extensions` in your browser
2. Enable "Developer mode" (top right toggle)
3. Click "Load unpacked"
4. Select the `extension/` folder
5. Pin the TabTriage extension to your toolbar

### 6. Capture Your First Window

1. Open a browser window with some tabs
2. Click the TabTriage extension icon
3. Click "Capture This Window"
4. The triage page opens automatically

## Notion Integration (Optional)

Create a `notion.json` config file:

```json
{
  "api_key": "ntn_your_api_key",
  "databases": {
    "links": "your-links-db-id",
    "parken": "your-backlog-db-id",
    "tasks": "your-tasks-db-id"
  }
}
```

Routing options per tab:
- **Links DB** — Save as a web clip with summary and full text
- **Backlog** — Create a backlog card for later processing
- **Tasks (Today)** — Create a task with status "Next Action"
- **Tasks (Someday)** — Create a task with status "Someday/Maybe"
- **Project** — Append a bookmark to an existing project page

## Usage

### Triage View
- Click a card to expand it and see controls
- Choose category: Read Later, Reference, Actionable, Archive
- Assign to a Notion project via autocomplete
- Click "Save", "Save & Close" (closes the browser tab), or "Dismiss"

### Bulk Operations
- Check multiple tabs or click "Select All" on a cluster
- Use the bulk bar to set category, Notion target, and project for all at once

### Keyboard Shortcuts
- `/` — Focus search
- `Escape` — Close fulltext overlay

### Mobile
- Swipe right on a card to save + close
- Swipe left to dismiss

## Tech Stack

- **Extension**: Chrome Manifest V3, Mozilla Readability.js
- **Backend**: Python 3.11, FastAPI, SQLite with FTS5, Claude Code CLI
- **Frontend**: Vanilla HTML/CSS/JS (single-file, no build step)
- **AI**: Claude (via CLI) for summarization, tagging, and clustering

## License

[MIT](LICENSE) — Marco Maas, 2026
