from __future__ import annotations

from typing import List, Dict, Optional, Tuple
import os
import requests
import logging
import base64
from .http_client import get_session, throttle


def head_is_pdf(url: str, timeout: int = 10) -> bool:
    try:
        with throttle(url):
            r = get_session().head(url, allow_redirects=True, timeout=timeout)
        ctype = r.headers.get("Content-Type", "")
        return "pdf" in ctype.lower()
    except requests.RequestException:
        return False


def _mouser_collect(parts: list) -> Tuple[List[str], List[str]]:
    """Return (pdf_urls, product_pages) from Mouser parts array."""
    pdfs: list[str] = []
    pages: list[str] = []
    for p in parts or []:
        ds = p.get("DataSheetUrl") or p.get("DataSheetURL") or p.get("DataSheet")
        if isinstance(ds, str) and ds:
            pdfs.append(ds)
        # Product detail page for fallback extraction
        pd = p.get("ProductDetailUrl") or p.get("ProductDetailURL") or p.get("ProductURL")
        if isinstance(pd, str) and pd:
            pages.append(pd)
    # de-dup while preserving order
    def _dedup(seq: List[str]) -> List[str]:
        seen = set(); out=[]
        for u in seq:
            if u not in seen:
                out.append(u); seen.add(u)
        return out
    return _dedup(pdfs), _dedup(pages)

def _normalize_mouser_domain(u: str) -> str:
    try:
        from urllib.parse import urlparse, urlunparse
        pr = urlparse(u)
        host = (pr.netloc or "").lower()
        if host.startswith("www.mouser.") and host != "www.mouser.com":
            pr = pr._replace(scheme="https", netloc="www.mouser.com")
            return urlunparse(pr)
    except Exception:
        pass
    return u


def mouser_candidate_urls(mpn: str, api_key: str) -> Tuple[List[str], List[str]]:
    """Query Mouser and return (pdf_urls, product_pages)."""
    headers = {"accept": "application/json", "Content-Type": "application/json"}
    params = {"apiKey": api_key}
    pdfs: list[str] = []
    pages: list[str] = []

    # 1) partnumber with manufacturerPartNumber
    try:
        logging.info("API-first: Mouser partnumber(manufacturerPartNumber) for %s", mpn)
        url = "https://api.mouser.com/api/v1/search/partnumber"
        payload = {"SearchByPartRequest": {"manufacturerPartNumber": mpn}}
        with throttle(url):
            r = get_session().post(url, json=payload, headers=headers, params=params, timeout=15)
        r.raise_for_status()
        data = r.json() or {}
        p = (data.get("SearchResults") or {}).get("Parts", []) or []
        a_pdfs, a_pages = _mouser_collect(p)
        pdfs.extend(a_pdfs); pages.extend(a_pages)
    except requests.RequestException as e:
        logging.info("API-first: Mouser partnumber(manufacturerPartNumber) failed: %s", e)

    # 2) Keyword search
    try:
        logging.info("API-first: Mouser keyword for %s", mpn)
        url = "https://api.mouser.com/api/v1/search/keyword"
        payload = {"SearchByKeywordRequest": {"keyword": mpn}}
        with throttle(url):
            r = get_session().post(url, json=payload, headers=headers, params=params, timeout=15)
        r.raise_for_status()
        data = r.json() or {}
        p = (data.get("SearchResults") or {}).get("Parts", []) or []
        a_pdfs, a_pages = _mouser_collect(p)
        pdfs.extend(a_pdfs); pages.extend(a_pages)
        logging.info("API-first: Mouser keyword parts=%s pdfs=%s pages=%s", len(p), len(a_pdfs), len(a_pages))
    except requests.RequestException as e:
        logging.info("API-first: Mouser keyword request failed: %s", e)

    # 3) partnumber with mouserPartNumber (last resort)
    try:
        logging.info("API-first: Mouser partnumber(mouserPartNumber) for %s", mpn)
        url = "https://api.mouser.com/api/v1/search/partnumber"
        payload = {"SearchByPartRequest": {"mouserPartNumber": mpn}}
        with throttle(url):
            r = get_session().post(url, json=payload, headers=headers, params=params, timeout=15)
        r.raise_for_status()
        data = r.json() or {}
        p = (data.get("SearchResults") or {}).get("Parts", []) or []
        a_pdfs, a_pages = _mouser_collect(p)
        pdfs.extend(a_pdfs); pages.extend(a_pages)
        logging.info("API-first: Mouser partnumber(mouserPartNumber) parts=%s pdfs=%s pages=%s", len(p), len(a_pdfs), len(a_pages))
    except requests.RequestException as e:
        logging.info("API-first: Mouser partnumber(mouserPartNumber) failed: %s", e)

    # If we still have no product pages, fall back to a Mouser search page to extract from
    if not pages:
        pages.append(f"https://www.mouser.com/c/?q={mpn}")

    # Normalize regional Mouser TLDs to www.mouser.com to reduce locale blockers
    pages = [_normalize_mouser_domain(p) for p in pages]

    # Validate pdfs via HEAD and de-dup
    valid_pdfs: List[str] = []
    seen = set()
    for u in pdfs:
        if u in seen:
            continue
        seen.add(u)
        try:
            # Accept URLs ending with .pdf even if HEAD fails; otherwise require HEAD confirmation
            if u.lower().endswith(".pdf") or head_is_pdf(u):
                valid_pdfs.append(u)
        except Exception:
            # keep .pdf endings regardless
            if isinstance(u, str) and u.lower().endswith(".pdf"):
                valid_pdfs.append(u)
    # de-dup pages
    page_seen=set(); page_uniq=[]
    for u in pages:
        if u not in page_seen:
            page_uniq.append(u); page_seen.add(u)
    return valid_pdfs, page_uniq


def _extract_mouser_description(parts: list) -> Optional[str]:
    """Extract a concise description from Mouser parts payload.

    Tries common fields across Mouser responses and returns the first
    non-empty string found. Tolerant to key name variations.
    """
    if not parts:
        return None
    def _cand(p: dict) -> List[str]:
        keys = [
            "Description",
            "ProductDescription",
            "DescriptionText",
            "ProductAttributeDescription",
            "Category",
            "ManufacturerPartNumber",
        ]
        out: List[str] = []
        for k in keys:
            v = p.get(k)
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
        return out
    for p in parts:
        for s in _cand(p):
            if s and len(s) >= 4:
                return s[:200]
    return None


def get_part_description_api_first(mpn: str) -> Optional[str]:
    """Return a short description using official APIs (Mouser first).

    Only uses formal APIs; returns None if not available or on errors.
    """
    m_key = (os.getenv("MOUSER_API_KEY") or os.getenv("PROVIDER_MOUSER_KEY") or "").strip()
    if not m_key:
        return None
    headers = {"accept": "application/json", "Content-Type": "application/json"}
    params = {"apiKey": m_key}
    # Try exact partnumber first
    try:
        url = "https://api.mouser.com/api/v1/search/partnumber"
        payload = {"SearchByPartRequest": {"manufacturerPartNumber": mpn}}
        with throttle(url):
            r = get_session().post(url, json=payload, headers=headers, params=params, timeout=15)
        r.raise_for_status()
        data = r.json() or {}
        p = (data.get("SearchResults") or {}).get("Parts", []) or []
        d = _extract_mouser_description(p)
        if d:
            return d
    except requests.RequestException:
        pass
    # Fallback: keyword search
    try:
        url = "https://api.mouser.com/api/v1/search/keyword"
        payload = {"SearchByKeywordRequest": {"keyword": mpn}}
        r = requests.post(url, json=payload, headers=headers, params=params, timeout=15)
        r.raise_for_status()
        data = r.json() or {}
        p = (data.get("SearchResults") or {}).get("Parts", []) or []
        d = _extract_mouser_description(p)
        if d:
            return d
    except requests.RequestException:
        pass
    return None


def _digikey_get_token(client_id: str, client_secret: str) -> Optional[str]:
    """Obtain an OAuth2 client-credentials access token from Digi-Key."""
    try:
        token_url = "https://api.digikey.com/v1/oauth2/token"
        basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        headers = {
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {"grant_type": "client_credentials"}
        with throttle(token_url):
            r = get_session().post(token_url, headers=headers, data=data, timeout=15)
        if r.status_code >= 400:
            logging.info("API-first: Digi-Key token error %s: %s", r.status_code, (r.text or "")[:200])
            return None
        tok = (r.json() or {}).get("access_token")
        return tok
    except requests.RequestException:
        return None


def digikey_media_urls(mpn: str, access_token: str, client_id: Optional[str] = None, client_secret: Optional[str] = None) -> List[str]:
    """Try Digi-Key APIs to fetch media (PDF) URLs for an MPN.

    Strategy:
      1. Ensure we have an access token, generating via client-credentials if needed.
      2. Try v4 keyword search endpoint to obtain product(s) and media.
      3. Fallback to legacy v3 search endpoint if v4 is unavailable.
    """
    # Ensure token present
    token = (access_token or "").strip()
    if (not token) and client_id and client_secret:
        token = _digikey_get_token(client_id, client_secret) or ""
        if token:
            try:
                os.environ.setdefault("DIGIKEY_ACCESS_TOKEN", token)
            except Exception:
                pass
    if not token:
        return []

    headers_common = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    urls: List[str] = []

    # Locale/correlation headers per Digi-Key guidance
    def _apply_locale_headers(h: dict) -> dict:
        h.setdefault("X-DIGIKEY-Locale-Site", os.getenv("DIGIKEY_LOCALE_SITE", "US"))
        h.setdefault("X-DIGIKEY-Locale-Language", os.getenv("DIGIKEY_LOCALE_LANGUAGE", "en"))
        h.setdefault("X-DIGIKEY-Locale-Currency", os.getenv("DIGIKEY_LOCALE_CURRENCY", "USD"))
        h.setdefault("X-DIGIKEY-Correlation-Id", os.getenv("DIGIKEY_CORR_ID", "bomdb-autods"))
        return h

    # Extract a usable Digi-Key product number from a v4 product object
    def _dk_product_number(p: dict) -> Optional[str]:
        for key in (
            "productNumber", "ProductNumber",
            "digiKeyPartNumber", "DigiKeyPartNumber",
            "digiKeyProductNumber", "DigiKeyProductNumber",
            "dkPartNumber", "DKPartNumber",
        ):
            v = p.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return None

    # ---- v4 KeywordSearch ----
    try:
        # Product Information v4: POST /products/v4/search/keyword
        v4_url = "https://api.digikey.com/products/v4/search/keyword"
        headers = dict(headers_common)
        if client_id:
            headers["X-DIGIKEY-Client-Id"] = client_id
        headers = _apply_locale_headers(headers)
        payload = {"keywords": mpn, "recordCount": 5}
        r = requests.post(v4_url, headers=headers, json=payload, timeout=15)
        if r.ok:
            data = r.json() or {}
            products = data.get("products") or data.get("Products") or []
            prod_numbers: List[str] = []
            for p in products:
                # v4: PrimaryDatasheet → DatasheetUrl
                ds = p.get("datasheetUrl") or p.get("DatasheetUrl")
                if isinstance(ds, str) and ds:
                    urls.append(ds)
                pn = _dk_product_number(p)
                if pn:
                    prod_numbers.append(pn)
            # Sweep media for top candidates
            for pn in prod_numbers[:3]:
                try:
                    media_url = "https://api.digikey.com/products/v4/search/{}/media".format(
                        requests.utils.quote(pn, safe="")
                    )
                    h2 = dict(headers_common)
                    if client_id:
                        h2["X-DIGIKEY-Client-Id"] = client_id
                    h2 = _apply_locale_headers(h2)
                    mr = requests.get(media_url, headers=h2, timeout=15)
                    if not mr.ok:
                        continue
                    mdata = mr.json() or {}
                    medias = mdata.get("media") or mdata.get("Media") or mdata.get("medias") or []
                    for m in medias:
                        link = m.get("url") or m.get("Url") or m.get("link")
                        mime = (m.get("mimeType") or m.get("MimeType") or "").lower()
                        if isinstance(link, str) and (link.lower().endswith(".pdf") or "pdf" in mime):
                            urls.append(link)
                except requests.RequestException:
                    continue
            if urls:
                # De-dup
                seen=set(); out=[]
                for u in urls:
                    if u not in seen:
                        out.append(u); seen.add(u)
                return out
        else:
            logging.info("API-first: Digi-Key v4 search HTTP %s", r.status_code)
    except requests.RequestException:
        pass

    # ---- v4 ProductDetails fallback ----
    try:
        pd_url = "https://api.digikey.com/products/v4/search/{}/productdetails".format(
            requests.utils.quote(mpn, safe="")
        )
        headers = dict(headers_common)
        if client_id:
            headers["X-DIGIKEY-Client-Id"] = client_id
        headers = _apply_locale_headers(headers)
        r = requests.get(pd_url, headers=headers, timeout=15)
        if r.ok:
            data = r.json() or {}
            products = data.get("products") or data.get("Products") or []
            prod_numbers: List[str] = []
            for p in products:
                ds = p.get("datasheetUrl") or p.get("DatasheetUrl")
                if isinstance(ds, str) and ds:
                    urls.append(ds)
                pn = _dk_product_number(p)
                if pn:
                    prod_numbers.append(pn)
            # Sweep media for first candidate(s)
            for pn in prod_numbers[:2]:
                try:
                    media_url = "https://api.digikey.com/products/v4/search/{}/media".format(
                        requests.utils.quote(pn, safe="")
                    )
                    h2 = dict(headers_common)
                    if client_id:
                        h2["X-DIGIKEY-Client-Id"] = client_id
                    h2 = _apply_locale_headers(h2)
                    mr = requests.get(media_url, headers=h2, timeout=15)
                    if not mr.ok:
                        continue
                    mdata = mr.json() or {}
                    medias = mdata.get("media") or mdata.get("Media") or mdata.get("medias") or []
                    for m in medias:
                        link = m.get("url") or m.get("Url") or m.get("link")
                        mime = (m.get("mimeType") or m.get("MimeType") or "").lower()
                        if isinstance(link, str) and (link.lower().endswith(".pdf") or "pdf" in mime):
                            urls.append(link)
                except requests.RequestException:
                    continue
    except requests.RequestException:
        pass

    # De-dup
    seen=set(); out=[]
    for u in urls:
        if u not in seen:
            out.append(u); seen.add(u)
    return out


def nexar_document_urls(mpn: str, access_token: str) -> List[str]:
    """Query Nexar (Octopart) GraphQL for datasheet/document URLs.

    Tolerant to schema drift by trying `results` or `hits` collections and
    using either `documents` (new) or `datasheets` (legacy) fields.
    """
    urls: List[str] = []
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    endpoint = "https://api.nexar.com/graphql"

    def _collect_doc_url(u: Optional[str]):
        try:
            if not u:
                return
            s = str(u)
            if s.lower().endswith(".pdf"):
                urls.append(s)
        except Exception:
            pass

    # Minimal supply search: bestDatasheet only, avoids account-specific fields
    q = "query($mpn:String!){ supSearch(q:$mpn, limit:5){ results{ part{ bestDatasheet{ url } } } } }"
    try:
        logging.info("API-first: Nexar supSearch (minimal) for %s", mpn)
        r = requests.post(
            endpoint,
            headers=headers,
            json={"query": q, "variables": {"mpn": mpn}},
            timeout=15,
        )
        if r.status_code >= 400:
            logging.info("API-first: Nexar supSearch error %s: %s", r.status_code, (r.text or "")[:200])
        r.raise_for_status()
        data = r.json() or {}
        sup = (data.get("data") or {}).get("supSearch") or {}
        items = sup.get("results") or []
        for res in items:
            part = res.get("part") or {}
            _collect_doc_url(((part.get("bestDatasheet") or {}).get("url")))
    except requests.RequestException as e:
        logging.info("API-first: Nexar supSearch failed: %s", e)

    # De-dup
    seen: set[str] = set()
    uniq: List[str] = []
    for u in urls:
        if u not in seen:
            uniq.append(u)
            seen.add(u)
    return uniq


def resolve_datasheet_api_first(mpn: str) -> Tuple[List[str], List[str]]:
    """Return (pdf_urls, page_urls) using official APIs before web search.

    Tries Mouser (PDF + product pages), then Digi-Key (PDFs), then Nexar (PDFs).
    """
    pdf_urls: list[str] = []
    page_urls: list[str] = []
    # Support both our local env and Part-DB-like names
    m_key = (os.getenv("MOUSER_API_KEY") or os.getenv("PROVIDER_MOUSER_KEY") or "").strip()
    if m_key:
        try:
            logging.info("API-first: querying Mouser for %s", mpn)
            mpdf, mpages = mouser_candidate_urls(mpn, m_key)
            pdf_urls.extend(mpdf)
            page_urls.extend(mpages)
            if not mpdf and not mpages:
                logging.info("API-first: Mouser returned no datasheet for %s", mpn)
        except requests.RequestException:
            logging.info("API-first: Mouser request error for %s", mpn)
    else:
        logging.info("API-first: Mouser not configured (no MOUSER_API_KEY)")
    dk_token = (
        os.getenv("DIGIKEY_ACCESS_TOKEN")
        or os.getenv("PROVIDER_DIGIKEY_ACCESS_TOKEN")
        or ""
    ).strip()
    if dk_token:
        try:
            logging.info("API-first: querying Digi-Key for %s", mpn)
            dk_cid = os.getenv("DIGIKEY_CLIENT_ID") or os.getenv("PROVIDER_DIGIKEY_CLIENT_ID")
            dk_cs = os.getenv("DIGIKEY_CLIENT_SECRET") or os.getenv("PROVIDER_DIGIKEY_CLIENT_SECRET")
            for u in digikey_media_urls(mpn, dk_token, dk_cid, dk_cs):
                if head_is_pdf(u):
                    pdf_urls.append(u)
        except requests.RequestException:
            logging.info("API-first: Digi-Key request error for %s", mpn)
    else:
        logging.info("API-first: Digi-Key not configured (no DIGIKEY_ACCESS_TOKEN)")
    # Nexar/Octopart
    nx_token = (
        os.getenv("NEXAR_ACCESS_TOKEN")
        or os.getenv("PROVIDER_OCTOPART_ACCESS_TOKEN")
        or ""
    ).strip()
    if nx_token:
        try:
            logging.info("API-first: querying Nexar for %s", mpn)
            for u in nexar_document_urls(mpn, nx_token):
                if head_is_pdf(u):
                    pdf_urls.append(u)
        except requests.RequestException:
            logging.info("API-first: Nexar request error for %s", mpn)
    else:
        logging.info("API-first: Nexar not configured (no NEXAR_ACCESS_TOKEN)")
    # De-duplicate while preserving order
    def _dedup(seq: List[str]) -> List[str]:
        seen=set(); out=[]
        for u in seq:
            if u not in seen:
                out.append(u); seen.add(u)
        return out
    return _dedup(pdf_urls), _dedup(page_urls)
