// TabTriage Behavior Tracker
// Injected on all pages, collects: scroll depth, active time, text selections
// Respects the behaviorTracking setting — if disabled, stays dormant
(function() {
    if (window.__ttTracker) return; // prevent double-injection
    window.__ttTracker = true;

    let enabled = true; // default on, checked async below

    const data = {
        maxScrollPct: 0,
        activeTimeMs: 0,
        selections: [],
        scrollEvents: 0,
        clickCount: 0,
        keypressCount: 0,
        lastActive: Date.now(),
        isActive: !document.hidden,
    };

    // Check setting (async — listeners already installed but guarded)
    if (typeof chrome !== 'undefined' && chrome.storage) {
        chrome.storage.local.get('behaviorTracking', (s) => {
            if (s.behaviorTracking === false) enabled = false;
        });
        chrome.storage.onChanged.addListener((changes) => {
            if (changes.behaviorTracking) enabled = changes.behaviorTracking.newValue !== false;
        });
    }

    // ── Scroll depth ──
    function updateScroll() {
        if (!enabled) return;
        const scrollHeight = Math.max(
            document.body.scrollHeight,
            document.documentElement.scrollHeight
        );
        const viewportHeight = window.innerHeight;
        const scrollTop = window.scrollY || document.documentElement.scrollTop;

        if (scrollHeight <= viewportHeight) {
            data.maxScrollPct = 100; // page fits in viewport
        } else {
            const pct = Math.round(((scrollTop + viewportHeight) / scrollHeight) * 100);
            if (pct > data.maxScrollPct) data.maxScrollPct = pct;
        }
        data.scrollEvents++;
    }

    window.addEventListener('scroll', updateScroll, { passive: true });
    // Initial check
    setTimeout(updateScroll, 1000);

    // ── Active time tracking ──
    let activeStart = document.hidden ? 0 : Date.now();

    function onVisible() {
        if (!enabled) return;
        if (!document.hidden) {
            activeStart = Date.now();
            data.isActive = true;
        } else {
            if (activeStart > 0) {
                data.activeTimeMs += Date.now() - activeStart;
            }
            activeStart = 0;
            data.isActive = false;
        }
    }
    document.addEventListener('visibilitychange', onVisible);

    // ── Interaction counts ──
    document.addEventListener('click', () => { if (enabled) data.clickCount++; }, { passive: true });
    document.addEventListener('keypress', () => { if (enabled) data.keypressCount++; }, { passive: true });

    // ── Text selections ──
    document.addEventListener('mouseup', () => {
        if (!enabled) return;
        const sel = window.getSelection();
        if (sel && sel.toString().trim().length > 3) {
            const text = sel.toString().trim().substring(0, 200);
            // Avoid duplicates
            if (!data.selections.some(s => s === text)) {
                data.selections.push(text);
                // Keep max 10 selections
                if (data.selections.length > 10) data.selections.shift();
            }
        }
    }, { passive: true });

    // ── Expose data for capture ──
    // The popup/capture script reads this via chrome.scripting.executeScript
    window.__ttGetBehavior = function() {
        if (!enabled) return null;
        // Finalize active time
        if (data.isActive && activeStart > 0) {
            data.activeTimeMs += Date.now() - activeStart;
            activeStart = Date.now(); // reset for continued tracking
        }
        return {
            scroll_depth_pct: data.maxScrollPct,
            active_time_sec: Math.round(data.activeTimeMs / 1000),
            scroll_events: data.scrollEvents,
            click_count: data.clickCount,
            keypress_count: data.keypressCount,
            selections: data.selections,
        };
    };
})();
