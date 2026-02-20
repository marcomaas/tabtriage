// Background service worker: polls backend for tabs to close
let backendUrl = 'http://localhost:5111';

// Load saved backend URL
chrome.storage.local.get('backendUrl', (data) => {
    if (data.backendUrl) backendUrl = data.backendUrl;
});
chrome.storage.onChanged.addListener((changes) => {
    if (changes.backendUrl) backendUrl = changes.backendUrl.newValue;
});

// Use chrome.alarms to keep service worker alive (Manifest V3)
chrome.alarms.create('pollClose', { periodInMinutes: 0.05 }); // every 3 seconds

chrome.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name === 'pollClose') {
        pollCloseRequests();
    }
});

// Also poll on startup
pollCloseRequests();

// Normalize URL for comparison (remove trailing slashes, fragments)
function normalizeUrl(url) {
    try {
        const u = new URL(url);
        // Remove fragment
        u.hash = '';
        // Remove trailing slash from pathname (but keep root /)
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

        // Find and close matching tabs
        const tabs = await chrome.tabs.query({});
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
                // Confirm closure to backend
                fetch(backendUrl + '/api/tabs/confirm-close', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({url: url}),
                }).catch(() => {});
            } else {
                console.log('[TabTriage] No matching tab found for:', url);
                // URL not found - still confirm to prevent infinite polling
                fetch(backendUrl + '/api/tabs/confirm-close', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({url: url}),
                }).catch(() => {});
            }
        }
    } catch (e) {
        // Backend not running - ignore
    }
}
