# TabTriage — Publishing Guide

## 1. GitHub Repository

### Repo erstellen
1. Gehe zu https://github.com/new
2. Repository name: `tabtriage`
3. Description: "AI-powered browser tab triage — capture, summarize, categorize, close."
4. Visibility: **Public**
5. KEIN Template, kein README (haben wir schon)
6. Click "Create repository"

### Lokales Repo pushen
```bash
cd /Users/marcomaas/browserballet
git init
git add -A
git commit -m "Initial commit: TabTriage v1.0

Chrome extension + FastAPI backend for AI-powered browser tab triage.
Captures tabs, extracts content, generates Claude summaries, and provides
a fast triage interface with Notion integration."

git remote add origin git@github.com:marcomaas/tabtriage.git
git branch -M main
git push -u origin main
```

### Empfohlene GitHub-Settings
- **About**: "AI-powered browser tab triage — capture, summarize, categorize, close."
- **Topics**: `chrome-extension`, `tab-manager`, `ai`, `claude`, `productivity`, `fastapi`, `notion`
- **Website**: (optional, z.B. Link zur Chrome Web Store Seite)

---

## 2. Chrome Web Store

### Developer Account registrieren
1. Gehe zu https://chrome.google.com/webstore/devconsole
2. Einloggen mit Google-Account
3. "Register" klicken
4. Einmalige Gebühr: **$5 USD** (Kreditkarte)
5. Developer-Vereinbarung akzeptieren
6. Account-Verifizierung abwarten (kann einige Minuten dauern)

### Extension packen
```bash
cd /Users/marcomaas/browserballet
# ZIP erstellen (nur extension/-Ordner, OHNE Backend/Config)
cd extension
zip -r ../tabtriage-extension.zip . -x ".*"
cd ..
```

### Extension hochladen
1. Chrome Web Store Developer Dashboard: https://chrome.google.com/webstore/devconsole
2. "New Item" klicken
3. ZIP-Datei (`tabtriage-extension.zip`) hochladen
4. Formular ausfüllen:

| Feld | Wert |
|------|------|
| **Language** | English (United States) |
| **Store Listing — Title** | TabTriage — AI Tab Triage |
| **Short Description** | (aus `chrome-web-store-listing.md`) |
| **Detailed Description** | (aus `chrome-web-store-listing.md`) |
| **Category** | Productivity |
| **Icon** | `extension/icon128.png` (128x128) |
| **Screenshots** | Min. 1 Screenshot, 1280x800 oder 640x400 |
| **Single Purpose** | "Tab management and organization with AI summarization" |

5. **Privacy Practices**:
   - "Does your extension use remote code?" → **No**
   - "Does your extension handle personal or sensitive user data?" → **Yes** (tab URLs and page content)
   - Data use disclosure:
     - Tab URLs: "Used for tab management functionality"
     - Page content: "Extracted locally, processed via local AI, never sent to external servers"
   - **Host permissions justification**: "Needs access to tab content for full-text extraction using Mozilla Readability"

6. **Distribution**:
   - Visibility: **Public**
   - Regions: All regions

7. "Submit for review" klicken
   - Review dauert typischerweise **1-3 Werktage**
   - Bei Ablehnung: Feedback lesen, anpassen, erneut einreichen

### Häufige Ablehnungsgründe
- **Fehlende Privacy Policy**: Erstelle eine einfache Privacy Policy Seite (kann ein GitHub Gist sein)
- **Zu breite Permissions**: `activeTab` statt `<all_urls>` wenn möglich
- **Remote Code**: Kein Code von externen Servern laden
- **Single Purpose**: Beschreibung muss klar einen Zweck kommunizieren

### Privacy Policy (minimal, als GitHub Gist oder Repo-Datei)
```
Privacy Policy for TabTriage

TabTriage captures browser tab URLs and page content solely for the purpose
of local text extraction and AI summarization. All data is processed locally
on your machine. No data is transmitted to external servers except:

- Notion API (only if you configure Notion integration)
- Claude CLI (runs locally on your machine)

TabTriage does not collect, store, or share any personal information
with third parties. All captured tab data is stored in a local SQLite
database on your machine.

Contact: marco@vongoeler.de
```

---

## 3. Screenshots erstellen

### Empfohlene Screenshots (1280x800)

1. **Triage-Hauptansicht**: Browser mit TabTriage-Seite offen, geclusterte Tabs sichtbar
2. **Extension-Popup**: Das Popup mit "Capture This Window" Button
3. **Detail-Ansicht**: Ein expandierter Tab mit Summary, Tags, Kategorie-Auswahl
4. **Archiv/Suche**: Die Archiv-Ansicht oder Suchergebnisse

### Screenshot-Tool
```bash
# macOS: Cmd+Shift+4, dann Space für Fenster-Screenshot
# Oder: Cmd+Shift+5 für Screenshot-Toolbar mit Optionen
# Tipp: Fenster auf exakt 1280x800 setzen für Store-konforme Screenshots
```

---

## 4. Checkliste vor Veröffentlichung

### Code
- [x] MIT License vorhanden
- [x] README.md mit Anleitung
- [x] .gitignore (keine Secrets committen!)
- [x] config.example.json (ohne echte Pfade/Keys)
- [ ] schema.sql im Repo
- [ ] Privacy Policy erstellen (Gist oder `docs/privacy-policy.md`)
- [ ] Screenshots erstellen (min. 1 für Store)

### GitHub
- [ ] Repo erstellen (public)
- [ ] Initial commit + push
- [ ] Topics/Tags setzen
- [ ] About-Beschreibung setzen

### Chrome Web Store
- [ ] Developer Account ($5)
- [ ] Extension ZIP erstellen
- [ ] Store Listing ausfüllen
- [ ] Privacy Policy URL angeben
- [ ] Screenshots hochladen
- [ ] Submit for review

### Marketing
- [ ] LinkedIn Post veröffentlichen
- [ ] (Optional) Twitter/X Post
- [ ] (Optional) Hacker News / Reddit post
