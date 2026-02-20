# Privacy Policy — TabTriage

*Last updated: February 20, 2026*

## What TabTriage Does

TabTriage is a browser extension and local backend that captures browser tab URLs and page content for the purpose of AI-powered summarization and organization.

## Data Collection & Processing

**All data is processed and stored locally on your machine.** Specifically:

- **Tab URLs and titles**: Captured when you click "Capture This Window"
- **Page content**: Extracted locally in your browser using Mozilla Readability
- **AI summaries**: Generated locally via Claude CLI running on your machine
- **Triage decisions**: Stored in a local SQLite database

## Data Storage

All captured data is stored in a SQLite database on your local filesystem. No data is stored on external servers.

## Third-Party Services

TabTriage does **not** transmit data to external servers, with two optional exceptions:

1. **Claude CLI** (Anthropic): Runs locally on your machine. Your Claude CLI configuration determines how API calls are handled.
2. **Notion API** (optional): If you configure Notion integration, triaged tabs can be routed to your Notion workspace. Only the data you explicitly choose to send (title, URL, summary) is transmitted to Notion.

## Data Sharing

TabTriage does **not** sell, share, or transmit your browsing data to any third party.

## Permissions

The extension requests the following permissions:
- **activeTab / tabs**: To read tab URLs and titles in the current window
- **scripting**: To inject content extraction scripts (Mozilla Readability) into pages
- **alarms**: To periodically poll for tab close requests
- **host permissions (localhost)**: To communicate with the local backend

## Contact

For questions about this privacy policy, contact: marco@vongoeler.de

## Open Source

TabTriage is open source under the MIT license. You can inspect all code at:
https://github.com/marcomaas/tabtriage
