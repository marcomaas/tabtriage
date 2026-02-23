// Background service worker: badge counter, tab reminders, polls backend for tabs to close
let backendUrl = 'http://localhost:5111';

// Load saved backend URL
chrome.storage.local.get('backendUrl', (data) => {
    if (data.backendUrl) backendUrl = data.backendUrl;
});
chrome.storage.onChanged.addListener((changes) => {
    if (changes.backendUrl) backendUrl = changes.backendUrl.newValue;
});

// ── Badge Counter ────────────────────────────────────────────
async function updateBadge() {
    try {
        const win = await chrome.windows.getCurrent();
        const tabs = await chrome.tabs.query({ windowId: win.id });
        const count = tabs.filter(t =>
            t.url &&
            !t.url.startsWith('chrome://') &&
            !t.url.startsWith('chrome-extension://') &&
            !t.url.startsWith('arc://') &&
            !t.url.startsWith('about:')
        ).length;
        chrome.action.setBadgeText({ text: count > 0 ? String(count) : '' });
        chrome.action.setBadgeBackgroundColor({ color: '#6366f1' });
    } catch (e) {
        // Window not available (e.g. during startup)
    }
}

// Update badge on tab events
chrome.tabs.onCreated.addListener(updateBadge);
chrome.tabs.onRemoved.addListener(updateBadge);
chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
    if (changeInfo.url) updateBadge();
});
chrome.windows.onFocusChanged.addListener((windowId) => {
    if (windowId !== chrome.windows.WINDOW_ID_NONE) updateBadge();
});

// Initial badge update
updateBadge();

// ── Tab Reminder (Notification) ──────────────────────────────
const REMINDER_DEFAULTS = { tabThreshold: 30, reminderEnabled: true, snoozedUntil: 0 };

chrome.alarms.create('tabReminder', { periodInMinutes: 30 });

chrome.alarms.onAlarm.addListener(async (alarm) => {
    if (alarm.name === 'pollClose') {
        pollCloseRequests();
    }
    if (alarm.name === 'pollReExtract') {
        pollReExtract();
    }
    if (alarm.name === 'tabReminder') {
        await checkTabReminder();
    }
});

async function checkTabReminder() {
    try {
        const settings = await chrome.storage.local.get(['tabThreshold', 'reminderEnabled', 'snoozedUntil']);
        const threshold = settings.tabThreshold ?? REMINDER_DEFAULTS.tabThreshold;
        const enabled = settings.reminderEnabled ?? REMINDER_DEFAULTS.reminderEnabled;
        const snoozedUntil = settings.snoozedUntil ?? 0;

        if (!enabled) return;
        if (Date.now() < snoozedUntil) return;

        const tabs = await chrome.tabs.query({});
        const realTabs = tabs.filter(t =>
            t.url &&
            !t.url.startsWith('chrome://') &&
            !t.url.startsWith('chrome-extension://') &&
            !t.url.startsWith('arc://') &&
            !t.url.startsWith('about:')
        );

        if (realTabs.length >= threshold) {
            chrome.notifications.create('tabReminder', {
                type: 'basic',
                iconUrl: 'icon128.png',
                title: 'TabTriage',
                message: `You have ${realTabs.length} tabs open. Ready to triage?`,
                buttons: [
                    { title: 'Open TabTriage' },
                    { title: 'Snooze 1h' }
                ],
                priority: 1
            });
        }
    } catch (e) {
        console.log('[TabTriage] Reminder check error:', e);
    }
}

chrome.notifications.onButtonClicked.addListener((notifId, btnIndex) => {
    if (notifId === 'tabReminder') {
        if (btnIndex === 0) {
            // Open TabTriage
            chrome.action.openPopup().catch(() => {
                // Fallback: open the triage page directly
                chrome.tabs.create({ url: backendUrl + '/' });
            });
        } else if (btnIndex === 1) {
            // Snooze 1 hour
            chrome.storage.local.set({ snoozedUntil: Date.now() + 60 * 60 * 1000 });
        }
        chrome.notifications.clear(notifId);
    }
});

// ── Poll for tabs to close ───────────────────────────────────
chrome.alarms.create('pollClose', { periodInMinutes: 0.05 }); // every 3 seconds
chrome.alarms.create('pollReExtract', { periodInMinutes: 0.05 }); // every 3 seconds

// Also poll on startup
pollCloseRequests();
pollReExtract();

// Normalize URL for comparison (remove trailing slashes, fragments)
function normalizeUrl(url) {
    try {
        const u = new URL(url);
        u.hash = '';
        if (u.pathname.length > 1 && u.pathname.endsWith('/')) {
            u.pathname = u.pathname.slice(0, -1);
        }
        return u.href;
    } catch (e) {
        return url;
    }
}

// Poll for tabs that should be closed
async function pollCloseRequests() {
    try {
        const resp = await fetch(backendUrl + '/api/tabs/pending-close');
        if (!resp.ok) return;
        const data = await resp.json();
        if (!data.urls || !data.urls.length) return;

        console.log('[TabTriage] Pending close URLs:', data.urls);

        // Check close scope setting: 'all' (default) or 'current'
        const settings = await chrome.storage.local.get('closeScope');
        const scope = settings.closeScope || 'all';

        let tabQuery = {};
        if (scope === 'current') {
            try {
                const win = await chrome.windows.getCurrent();
                tabQuery = { windowId: win.id };
            } catch (e) {
                // Fallback to all windows
            }
        }

        const tabs = await chrome.tabs.query(tabQuery);
        for (const url of data.urls) {
            const normalizedUrl = normalizeUrl(url);
            const match = tabs.find(t => {
                const normalizedTabUrl = normalizeUrl(t.url || '');
                return normalizedTabUrl === normalizedUrl;
            });

            if (match) {
                console.log('[TabTriage] Closing tab:', match.id, match.url);
                try {
                    await chrome.tabs.remove(match.id);
                } catch (e) {
                    console.log('[TabTriage] Failed to close tab:', e);
                }
            }
            // Confirm closure to backend (even if not found, to prevent infinite polling)
            fetch(backendUrl + '/api/tabs/confirm-close', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({url: url}),
            }).catch(() => {});
        }
    } catch (e) {
        // Backend not running - ignore
    }
}

// ── Poll for tabs needing content re-extraction ──────────────
async function pollReExtract() {
    try {
        const resp = await fetch(backendUrl + '/api/tabs/pending-re-extract');
        if (!resp.ok) return;
        const data = await resp.json();
        if (!data.tabs || !data.tabs.length) return;

        console.log('[TabTriage] Pending re-extract tabs:', data.tabs.length);

        for (const item of data.tabs) {
            const allTabs = await chrome.tabs.query({});
            const normalizedTarget = normalizeUrl(item.url);
            const match = allTabs.find(t => normalizeUrl(t.url || '') === normalizedTarget);

            if (match) {
                console.log('[TabTriage] Re-extracting content from open tab:', match.id, match.url);
                try {
                    // Inject readability first, then content.js
                    await chrome.scripting.executeScript({
                        target: { tabId: match.id },
                        files: ['readability.min.js']
                    });
                    const results = await chrome.scripting.executeScript({
                        target: { tabId: match.id },
                        files: ['content.js']
                    });

                    const content = results?.[0]?.result || null;
                    console.log('[TabTriage] Extracted content for tab', item.tab_id, ':', content ? content.length + ' chars' : 'null');

                    // Send content to backend
                    await fetch(backendUrl + '/api/tabs/' + item.tab_id + '/update-content', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ content: content })
                    });
                } catch (e) {
                    console.log('[TabTriage] Re-extract injection failed for tab', item.tab_id, ':', e.message);
                    // Report failure so trafilatura fallback can kick in
                    await fetch(backendUrl + '/api/tabs/' + item.tab_id + '/update-content', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ content: null })
                    }).catch(() => {});
                }
            } else {
                console.log('[TabTriage] Tab not open for re-extract:', item.url);
                // Tab not open — report null so trafilatura fallback handles it
                await fetch(backendUrl + '/api/tabs/' + item.tab_id + '/update-content', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ content: null })
                }).catch(() => {});
            }
        }
    } catch (e) {
        // Backend not running - ignore
    }
}
