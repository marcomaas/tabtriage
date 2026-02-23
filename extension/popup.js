let BACKEND = 'http://localhost:5111';

document.addEventListener('DOMContentLoaded', () => {
    const statusEl = document.getElementById('status');
    const captureBtn = document.getElementById('captureBtn');
    const tabCountEl = document.getElementById('tabCount');
    const backendInput = document.getElementById('backendUrl');
    const reminderCheckbox = document.getElementById('reminderEnabled');
    const thresholdInput = document.getElementById('tabThreshold');
    const progressArea = document.getElementById('progressArea');
    const progressBar = document.getElementById('progressBar');
    const progressLabel = document.getElementById('progressLabel');
    const closeScopeSelect = document.getElementById('closeScope');
    const behaviorCheckbox = document.getElementById('behaviorTracking');

    // Load saved backend URL
    chrome.storage.local.get('backendUrl', (data) => {
        if (data.backendUrl) {
            BACKEND = data.backendUrl;
            backendInput.value = BACKEND;
        }
    });

    // Load reminder + close scope + behavior settings
    chrome.storage.local.get(['reminderEnabled', 'tabThreshold', 'closeScope', 'behaviorTracking'], (data) => {
        if (data.reminderEnabled !== undefined) reminderCheckbox.checked = data.reminderEnabled;
        if (data.tabThreshold !== undefined) thresholdInput.value = data.tabThreshold;
        if (data.closeScope) closeScopeSelect.value = data.closeScope;
        if (data.behaviorTracking !== undefined) behaviorCheckbox.checked = data.behaviorTracking;
    });

    // Save backend URL on change
    backendInput.addEventListener('change', () => {
        BACKEND = backendInput.value.replace(/\/+$/, '');
        backendInput.value = BACKEND;
        chrome.storage.local.set({ backendUrl: BACKEND });
    });

    // Save reminder settings
    reminderCheckbox.addEventListener('change', () => {
        chrome.storage.local.set({ reminderEnabled: reminderCheckbox.checked });
    });
    thresholdInput.addEventListener('change', () => {
        const val = Math.max(5, Math.min(200, parseInt(thresholdInput.value) || 30));
        thresholdInput.value = val;
        chrome.storage.local.set({ tabThreshold: val });
    });
    closeScopeSelect.addEventListener('change', () => {
        chrome.storage.local.set({ closeScope: closeScopeSelect.value });
    });
    behaviorCheckbox.addEventListener('change', () => {
        chrome.storage.local.set({ behaviorTracking: behaviorCheckbox.checked });
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

    // Progress polling
    function pollProgress(sessionId) {
        progressArea.style.display = 'block';
        let interval = setInterval(async () => {
            try {
                const r = await fetch(BACKEND + '/api/capture/' + sessionId + '/progress');
                if (!r.ok) { clearInterval(interval); return; }
                const data = await r.json();
                const pct = data.total > 0 ? Math.round((data.completed / data.total) * 100) : 0;

                if (data.phase === 'summarizing') {
                    progressLabel.textContent = `Summarizing ${data.completed}/${data.total}...`;
                    progressBar.style.width = pct + '%';
                } else if (data.phase === 'clustering') {
                    progressLabel.textContent = 'Clustering themes...';
                    progressBar.style.width = '90%';
                } else if (data.phase === 'done') {
                    progressLabel.textContent = `Done! ${data.clusters || 0} clusters found.`;
                    progressBar.style.width = '100%';
                    clearInterval(interval);
                    setTimeout(() => {
                        progressLabel.innerHTML = `<a href="${BACKEND}/" target="_blank" style="color:#6366f1;font-weight:600">Open Triage &rarr;</a>`;
                    }, 1000);
                }
            } catch (e) {
                clearInterval(interval);
            }
        }, 2000);
    }

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

            // Check if behavior tracking is enabled
            const settings = await chrome.storage.local.get('behaviorTracking');
            const trackBehavior = settings.behaviorTracking !== false; // default: on

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
                    // Collect behavior data from tracker.js (if enabled)
                    let behavior = null;
                    if (trackBehavior) {
                        try {
                            const bResults = await chrome.scripting.executeScript({
                                target: { tabId: tab.id },
                                func: () => window.__ttGetBehavior ? window.__ttGetBehavior() : null,
                            });
                            behavior = bResults?.[0]?.result || null;
                        } catch (be) {
                            // tracker may not be injected on some pages
                        }
                    }
                    tabData.push({
                        url: tab.url,
                        title: tab.title || tab.url,
                        content: content,
                        favicon: tab.favIconUrl || null,
                        behavior: behavior,
                    });
                } catch (e) {
                    console.warn('Skip tab:', tab.url, e.message);
                    tabData.push({
                        url: tab.url,
                        title: tab.title || tab.url,
                        content: null,
                        favicon: tab.favIconUrl || null,
                        behavior: null,
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

            if (result.status === 'all_duplicates') {
                statusEl.textContent = result.message || `Alle ${result.skipped} Tabs bereits erfasst.`;
                statusEl.className = '';
                captureBtn.disabled = false;
                captureBtn.textContent = 'Capture This Window';
                // Still open triage page to see existing tabs
                setTimeout(() => { chrome.tabs.create({ url: BACKEND + '/' }); }, 500);
                return;
            }
            const skipMsg = result.skipped > 0 ? ` (${result.skipped} bereits bekannt)` : '';
            statusEl.textContent = `${result.tab_count} neue Tabs erfasst!${skipMsg}`;
            statusEl.className = 'success';
            captureBtn.textContent = 'Fertig!';

            // Start polling progress
            if (result.session_id) {
                pollProgress(result.session_id);
            }

            // Open triage page after short delay
            setTimeout(() => {
                chrome.tabs.create({ url: BACKEND + '/' });
            }, 500);

        } catch (e) {
            console.error('Capture error:', e);
            statusEl.textContent = 'Fehler: ' + e.message;
            statusEl.className = 'error';
            captureBtn.disabled = false;
        }
    });
});
