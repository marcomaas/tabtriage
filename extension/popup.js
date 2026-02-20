let BACKEND = 'http://localhost:5111';

document.addEventListener('DOMContentLoaded', () => {
    const statusEl = document.getElementById('status');
    const captureBtn = document.getElementById('captureBtn');
    const tabCountEl = document.getElementById('tabCount');
    const backendInput = document.getElementById('backendUrl');

    // Load saved backend URL
    chrome.storage.local.get('backendUrl', (data) => {
        if (data.backendUrl) {
            BACKEND = data.backendUrl;
            backendInput.value = BACKEND;
        }
    });

    // Save backend URL on change
    backendInput.addEventListener('change', () => {
        BACKEND = backendInput.value.replace(/\/+$/, '');
        backendInput.value = BACKEND;
        chrome.storage.local.set({ backendUrl: BACKEND });
    });

    // Count tabs on popup open
    chrome.tabs.query({ currentWindow: true }).then(tabs => {
        const filtered = tabs.filter(t =>
            t.url &&
            !t.url.startsWith('chrome://') &&
            !t.url.startsWith('chrome-extension://') &&
            !t.url.startsWith('arc://') &&
            !t.url.startsWith('about:')
        );
        tabCountEl.textContent = `${filtered.length} Tabs in diesem Fenster`;
    }).catch(err => {
        tabCountEl.textContent = 'Tabs konnten nicht gezählt werden';
        console.error('Tab count error:', err);
    });

    // Capture button handler
    captureBtn.addEventListener('click', async () => {
        captureBtn.disabled = true;
        statusEl.textContent = 'Tabs werden erfasst...';
        statusEl.className = '';

        try {
            const tabs = await chrome.tabs.query({ currentWindow: true });
            const validTabs = tabs.filter(t =>
                t.url &&
                !t.url.startsWith('chrome://') &&
                !t.url.startsWith('chrome-extension://') &&
                !t.url.startsWith('arc://') &&
                !t.url.startsWith('about:')
            );

            statusEl.textContent = `${validTabs.length} Tabs gefunden. Inhalte werden extrahiert...`;

            const tabData = [];
            let extracted = 0;
            for (const tab of validTabs) {
                try {
                    await chrome.scripting.executeScript({
                        target: { tabId: tab.id },
                        files: ['readability.min.js']
                    });
                    const results = await chrome.scripting.executeScript({
                        target: { tabId: tab.id },
                        files: ['content.js']
                    });
                    const content = results?.[0]?.result || null;
                    tabData.push({
                        url: tab.url,
                        title: tab.title || tab.url,
                        content: content,
                        favicon: tab.favIconUrl || null,
                    });
                } catch (e) {
                    console.warn('Skip tab:', tab.url, e.message);
                    tabData.push({
                        url: tab.url,
                        title: tab.title || tab.url,
                        content: null,
                        favicon: tab.favIconUrl || null,
                    });
                }
                extracted++;
                statusEl.textContent = `Extrahiere ${extracted}/${validTabs.length}...`;
            }

            statusEl.textContent = `${tabData.length} Tabs extrahiert. Sende an Backend...`;

            const win = await chrome.windows.getCurrent();
            const resp = await fetch(BACKEND + '/api/capture', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    window_title: `Window ${win.id}`,
                    tabs: tabData,
                }),
            });

            if (!resp.ok) {
                const text = await resp.text();
                throw new Error(`Backend ${resp.status}: ${text.substring(0, 100)}`);
            }
            const result = await resp.json();

            statusEl.textContent = `${result.tab_count} Tabs erfasst! Session #${result.session_id}`;
            statusEl.className = 'success';
            captureBtn.textContent = 'Fertig!';

            setTimeout(() => {
                chrome.tabs.create({
                    url: 'file:///Users/marcomaas/Library/CloudStorage/Dropbox-Privat/claude/projekte/TabTriage/index.html'
                });
            }, 500);

        } catch (e) {
            console.error('Capture error:', e);
            statusEl.textContent = 'Fehler: ' + e.message;
            statusEl.className = 'error';
            captureBtn.disabled = false;
        }
    });
});
