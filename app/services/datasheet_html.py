from __future__ import annotations

import re
from typing import List
from urllib.parse import urljoin, urlparse
import os
import logging
import asyncio

import requests

from .datasheet_rank import score_candidate


def _find_pdf_hrefs(html: str) -> List[str]:
    # naive extraction of href/src/pdf URLs; handles single/double/no quotes
    urls: list[str] = []
    # Also catch data-href/data-url/data-download attributes
    for m in re.finditer(r"([a-zA-Z0-9_-]*(?:href|src|url|download))\s*=\s*([\'\"]?)([^\'\">\s]+)(\2)", html, re.I):
        u = m.group(3)
        if ".pdf" in u.lower():
            urls.append(u)
    # JS window.open('...pdf') patterns
    for m in re.finditer(r"window\.open\((['\"])([^\1]+?\.pdf)\1\)", html, re.I):
        urls.append(m.group(2))
    # Any explicit http(s) PDF URLs in the text/JSON blobs
    for m in re.finditer(r"https?://[^'\"<>\s]+?\.pdf\b", html, re.I):
        urls.append(m.group(0))
    # De-dup in order
    seen = set()
    uniq = []
    for u in urls:
        if u not in seen:
            uniq.append(u)
            seen.add(u)
    return uniq


def _find_datasheet_anchors(html: str) -> List[str]:
    # Capture <a ...>text</a> where text hints at datasheet; extract href even if not .pdf
    urls: list[str] = []
    for m in re.finditer(r"<a[^>]+href=([\'\"])([^\1>]+)\1[^>]*>(.*?)</a>", html, re.I | re.S):
        href = m.group(2)
        text = re.sub(r"\s+", " ", (m.group(3) or "").strip()).lower()
        if "datasheet" in text or "data sheet" in text:
            urls.append(href)
    # De-dup
    seen = set(); out=[]
    for u in urls:
        if u not in seen:
            out.append(u); seen.add(u)
    return out


def find_pdfs_in_page(url: str, pn: str, mfg: str | None, timeout: tuple[int, int] = (10, 30)) -> List[str]:
    """Fetch an HTML page and return absolute PDF links ranked by quality.

    Uses a normal HTTP GET by default. For certain domains (e.g., Mouser) and
    when headless is enabled via env, tries to render the page with JS using
    requests_html + pyppeteer to expose dynamically inserted PDF links.
    """
    headers = {
        # Use a browser-like UA to reduce gating by distributor sites
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    netloc = (urlparse(url).netloc or "").lower()
    want_headless = False
    # Enable headless rendering for Mouser by default unless explicitly disabled
    if ("mouser." in netloc) and os.getenv("BOM_HEADLESS_MOUSER", "1") not in ("0", "false", "no"):
        want_headless = True
    # Global override
    if os.getenv("BOM_HEADLESS_ALL", "0") in ("1", "true", "yes"):
        want_headless = True

    html = None
    if want_headless:
        try:
            # If a system Chromium/Chrome/Edge is provided, use it directly via pyppeteer
            browser_path = os.getenv("BOM_HEADLESS_BROWSER_PATH", "").strip()
            if browser_path and os.path.exists(browser_path):
                async def _render_with_pyppeteer() -> str:
                    from pyppeteer import launch  # type: ignore
                    args = ["--no-sandbox", "--disable-dev-shm-usage"]
                    browser = await launch(executablePath=browser_path, headless=True, args=args)
                    page = await browser.newPage()
                    # Apply headers/User-Agent similar to requests
                    await page.setUserAgent(headers.get("User-Agent", "Mozilla/5.0"))
                    await page.setExtraHTTPHeaders({k: v for k, v in headers.items() if k.lower() != "user-agent"})
                    await page.goto(url, timeout=max(timeout[1], 60) * 1000, waitUntil="networkidle2")
                    await asyncio.sleep(2.0)
                    content = await page.content()
                    await browser.close()
                    return content

                try:
                    # Ensure a suitable event loop exists on Windows
                    if os.name == "nt":
                        try:
                            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined]
                        except Exception:
                            pass
                    html = asyncio.get_event_loop().run_until_complete(_render_with_pyppeteer())
                    logging.getLogger(__name__).info("datasheet_html: headless rendered via system browser %s", url)
                except RuntimeError:
                    # No running loop
                    loop = asyncio.new_event_loop()
                    try:
                        html = loop.run_until_complete(_render_with_pyppeteer())
                        logging.getLogger(__name__).info("datasheet_html: headless rendered via system browser %s", url)
                    finally:
                        loop.close()
                except Exception as e:
                    logging.getLogger(__name__).info(
                        "datasheet_html: system-browser headless failed for %s (%s: %s)",
                        url,
                        e.__class__.__name__,
                        str(e)[:200],
                    )
                    html = None
            else:
                # Fallback to requests_html (may download Chromium on first run)
                from requests_html import HTMLSession  # type: ignore
                sess = HTMLSession()
                resp = sess.get(url, headers=headers, timeout=timeout[0] + timeout[1])
                # render() downloads Chromium on first run; increase timeout/sleep to allow dynamic content
                resp.html.render(timeout=max(timeout[1], 60), sleep=2.0)
                html = resp.html.html
                logging.getLogger(__name__).info("datasheet_html: headless rendered %s", url)
                try:
                    sess.close()
                except Exception:
                    pass
        except Exception as e:
            logging.getLogger(__name__).info(
                "datasheet_html: headless render failed; falling back for %s (%s: %s)",
                url,
                e.__class__.__name__,
                str(e)[:200],
            )
            html = None
    if html is None:
        r = requests.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
        html = r.text

    raw = _find_pdf_hrefs(html)
    # Include anchors that look like datasheet links even if not ending with .pdf
    raw += _find_datasheet_anchors(html)
    abs_urls = [urljoin(url, u) if not urlparse(u).netloc else u for u in raw]
    ranked = sorted(
        abs_urls,
        key=lambda u: score_candidate(pn, mfg or "", title=u, snippet="", url=u),
        reverse=True,
    )
    return ranked
