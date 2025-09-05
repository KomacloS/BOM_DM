from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional
import os, requests

@dataclass
class SearchResult:
    title: str
    snippet: str
    url: str

class SearchProviderError(RuntimeError):
    ...

class NoSearchProviderConfigured(RuntimeError):
    ...

def _env(key: str) -> Optional[str]:
    v = os.environ.get(key) or os.getenv(key)
    return v.strip() if v else None

def _is_pdf_url(u: str) -> bool:
    return u.lower().endswith('.pdf')

# ---- Providers ----
def search_web(query: str, count: int = 10) -> List[SearchResult]:
    """Dispatch to the configured provider."""
    if _env("BING_SEARCH_KEY"):
        return _bing_search(query, count)
    if _env("GOOGLE_API_KEY") and _env("GOOGLE_CSE_ID"):
        return _google_cse_search(query, count)
    if _env("SERPAPI_KEY"):
        return _serpapi_search(query, count)
    if _env("BRAVE_API_KEY"):
        return _brave_search(query, count)
    raise NoSearchProviderConfigured(
        "Configure at least one search provider: BING_SEARCH_KEY, or GOOGLE_API_KEY + GOOGLE_CSE_ID, or SERPAPI_KEY, or BRAVE_API_KEY."
    )

def _bing_search(query: str, count: int) -> List[SearchResult]:
    key = _env("BING_SEARCH_KEY"); assert key
    headers = {"Ocp-Apim-Subscription-Key": key}
    params = {"q": query, "count": min(count, 15), "responseFilter": "Webpages", "textDecorations": False}
    r = requests.get("https://api.bing.microsoft.com/v7.0/search", headers=headers, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    items = data.get("webPages", {}).get("value", [])
    out: List[SearchResult] = []
    for it in items:
        out.append(SearchResult(title=it.get("name", ""), snippet=it.get("snippet", ""), url=it.get("url", "")))
    return out

def _google_cse_search(query: str, count: int) -> List[SearchResult]:
    key = _env("GOOGLE_API_KEY"); cse = _env("GOOGLE_CSE_ID")
    r = requests.get("https://www.googleapis.com/customsearch/v1", params={
        "key": key,
        "cx": cse,
        "q": query,
        "num": min(count, 10)
    }, timeout=15)
    r.raise_for_status()
    data = r.json()
    out: List[SearchResult] = []
    for it in data.get("items", []) or []:
        out.append(SearchResult(title=it.get("title", ""), snippet=it.get("snippet", ""), url=it.get("link", "")))
    return out

def _serpapi_search(query: str, count: int) -> List[SearchResult]:
    key = _env("SERPAPI_KEY")
    r = requests.get("https://serpapi.com/search.json", params={"engine":"google","q":query,"num":min(count,10),"api_key":key}, timeout=15)
    r.raise_for_status()
    data = r.json()
    out: List[SearchResult] = []
    for it in data.get("organic_results", []) or []:
        out.append(SearchResult(title=it.get("title", ""), snippet=it.get("snippet", ""), url=it.get("link", "")))
    return out

def _brave_search(query: str, count: int) -> List[SearchResult]:
    key = _env("BRAVE_API_KEY")
    r = requests.get("https://api.search.brave.com/res/v1/web/search", headers={"X-Subscription-Token": key}, params={"q": query, "count": min(count, 10)}, timeout=15)
    r.raise_for_status()
    data = r.json()
    out: List[SearchResult] = []
    for it in data.get("web", {}).get("results", []) or []:
        out.append(SearchResult(title=it.get("title", ""), snippet=it.get("description", ""), url=it.get("url", "")))
    return out
