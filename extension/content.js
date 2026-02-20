// Content script: extract clean article text + metadata + media
(function() {
    try {
        // Extract OpenGraph metadata
        const ogImage = document.querySelector('meta[property="og:image"]')?.content ||
                        document.querySelector('meta[name="twitter:image"]')?.content || null;
        const ogDescription = document.querySelector('meta[property="og:description"]')?.content ||
                              document.querySelector('meta[name="description"]')?.content || null;

        // Extract media elements (audio/video)
        const media = [];
        document.querySelectorAll('video source, video[src], audio source, audio[src]').forEach(el => {
            const src = el.src || el.getAttribute('src');
            const type = el.closest('video') ? 'video' : 'audio';
            if (src && !src.startsWith('blob:')) {
                media.push({ type, src, poster: el.closest('video')?.poster || null });
            }
        });
        // Also check for embedded iframes (YouTube, Vimeo etc.)
        document.querySelectorAll('iframe[src]').forEach(el => {
            const src = el.src;
            if (src && (src.includes('youtube') || src.includes('vimeo') || src.includes('soundcloud'))) {
                media.push({ type: 'embed', src });
            }
        });

        // Clone for Readability
        const doc = document.cloneNode(true);

        // Remove navigation, footer, sidebar, cookie banners before Readability
        const removeSelectors = [
            'nav', 'header:not(article header)', 'footer',
            '[role="navigation"]', '[role="banner"]', '[role="complementary"]',
            '.nav', '.navbar', '.navigation', '.menu', '.sidebar',
            '.cookie-banner', '.cookie-consent', '.gdpr',
            '.ad', '.ads', '.advertisement', '.social-share',
            '.comments', '#comments', '.comment-section',
            '.related-articles', '.recommendations',
            '.newsletter-signup', '.popup', '.modal',
            '[data-testid="header"]', '[data-testid="footer"]',
        ];
        for (const sel of removeSelectors) {
            doc.querySelectorAll(sel).forEach(el => el.remove());
        }

        const reader = new Readability(doc, {
            charThreshold: 50,
            keepClasses: false,
        });
        const article = reader.parse();

        let text = '';
        if (article && article.textContent) {
            text = cleanText(article.textContent);
        } else {
            // Fallback: get main content area
            const main = document.querySelector('main, article, [role="main"], .content, .article-body');
            text = cleanText((main || document.body).innerText || '');
        }

        return JSON.stringify({
            text: text.substring(0, 50000),
            og_image: ogImage,
            og_description: ogDescription,
            media: media.slice(0, 10),
        });
    } catch (e) {
        try {
            return JSON.stringify({
                text: cleanText(document.body?.innerText || '').substring(0, 50000),
                og_image: null, og_description: null, media: [],
            });
        } catch (e2) { return null; }
    }

    function cleanText(raw) {
        return raw
            // Collapse multiple newlines
            .replace(/\n{3,}/g, '\n\n')
            // Remove lines that are just navigation text (very short, no punctuation)
            .split('\n')
            .filter(line => {
                const trimmed = line.trim();
                if (!trimmed) return true; // keep blank lines
                // Remove very short lines without punctuation (likely nav items)
                if (trimmed.length < 4 && !/[.!?:]/.test(trimmed)) return false;
                // Remove common UI/nav patterns
                if (/^(Menü|Menu|Suche|Search|Anmelden|Login|Registrieren|Sign up|Teilen|Share|Drucken|Print|Merken|Bookmark|Kommentare?|Comments?|Mehr|More|Zurück|Back|Weiter|Next|Vorheriger?|Previous|Schließen|Close|Cookie|Akzeptieren|Accept|Ablehnen|Reject|Newsletter|Abonnieren|Subscribe)$/i.test(trimmed))
                    return false;
                return true;
            })
            .join('\n')
            // Collapse whitespace within lines
            .replace(/[ \t]{2,}/g, ' ')
            // Remove leading/trailing whitespace
            .trim();
    }
})();
