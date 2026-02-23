"""Server-side content extraction using trafilatura + httpx."""

import logging

import httpx
import trafilatura

log = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def extract_content(url: str) -> dict | None:
    """Fetch URL and extract article text server-side.

    Returns dict with text, og_image, og_description or None on failure.
    """
    try:
        resp = httpx.get(
            url,
            follow_redirects=True,
            timeout=15,
            headers={"User-Agent": _USER_AGENT},
        )
        resp.raise_for_status()
    except Exception as e:
        log.warning("Failed to fetch %s: %s", url, e)
        return None

    html = resp.text
    if not html or len(html) < 100:
        return None

    text = trafilatura.extract(
        html, include_comments=False, include_tables=True
    )

    og_image = None
    og_description = None
    try:
        metadata = trafilatura.extract_metadata(html)
        if metadata:
            og_image = metadata.image
            og_description = metadata.description
    except Exception:
        pass

    if not text or len(text.strip()) < 50:
        return None

    return {
        "text": text,
        "og_image": og_image,
        "og_description": og_description,
    }
