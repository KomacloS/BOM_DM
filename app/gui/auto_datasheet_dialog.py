from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional
from pathlib import Path
import tempfile, os

from PyQt6.QtCore import Qt, pyqtSignal, QObject, QThreadPool, QRunnable, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QTableWidget, QTableWidgetItem, QProgressBar,
    QHBoxLayout, QPushButton, QCheckBox, QMessageBox
)

from .. import services
from ..services.datasheet_search import search_web, NoSearchProviderConfigured
from ..services.gpt_rerank import choose_best_datasheet_url
from ..services.datasheet_validate import pdf_matches_request
from ..services.datasheet_rank import score_candidate, recommended_domains_for
from ..services.datasheet_html import find_pdfs_in_page
from ..services.datasheet_api import resolve_datasheet_api_first, get_part_description_api_first
from ..services.description_extract import infer_description_from_pdf
from . import state as app_state
import logging
import requests
from ..config import MAX_DATASHEET_MB, AUTO_DATASHEET_MAX_WORKERS


logger = logging.getLogger(__name__)


@dataclass
class WorkItem:
    part_id: int
    pn: str
    mfg: str | None
    desc: str | None


class _Signals(QObject):
    rowStatus = pyqtSignal(int, str)
    rowDone = pyqtSignal(int, bool, bool)  # row, attached, duplicate
    failed = pyqtSignal(int)  # part_id
    attached = pyqtSignal(int, str)  # part_id, canonical path
    openUrl = pyqtSignal(str)  # ask UI thread to open a URL
    manualLink = pyqtSignal(int, str)  # part_id, page url


class _Worker(QRunnable):
    def __init__(self, row: int, wi: WorkItem, auto_link_dupes: bool, sig: _Signals):
        super().__init__()
        self.row = row
        self.wi = wi
        self.auto = auto_link_dupes
        self.sig = sig
        self.manual_ok = True
        # Shared session to persist cookies (helps Mouser PDF fetch)
        import requests
        self._sess = requests.Session()

    def _log_outcome(self, outcome: str, **extra: object) -> None:
        """Emit a structured info log summarizing the worker result."""
        details = " ".join(f"{k}={extra[k]}" for k in sorted(extra)) if extra else ""
        if details:
            logger.info(
                "Auto-datasheet worker finished: row=%s part_id=%s outcome=%s %s",
                self.row,
                self.wi.part_id,
                outcome,
                details,
            )
        else:
            logger.info(
                "Auto-datasheet worker finished: row=%s part_id=%s outcome=%s",
                self.row,
                self.wi.part_id,
                outcome,
            )

    def run(self):
        logger.info("Auto-datasheet worker start: row=%s part_id=%s pn=%s", self.row, self.wi.part_id, self.wi.pn)
        try:
            logger.info(
                "API-first: configured -> mouser=%s digikey=%s nexar=%s",
                bool(os.getenv("MOUSER_API_KEY") or os.getenv("PROVIDER_MOUSER_KEY")),
                bool(os.getenv("DIGIKEY_ACCESS_TOKEN") or os.getenv("PROVIDER_DIGIKEY_ACCESS_TOKEN")),
                bool(os.getenv("NEXAR_ACCESS_TOKEN") or os.getenv("PROVIDER_OCTOPART_ACCESS_TOKEN")),
            )
            self.sig.rowStatus.emit(self.row, "Searching...")
            # API-first resolution (Mouser/Digi-Key/Nexar) before web search
            api_pdf_urls, api_page_urls = resolve_datasheet_api_first(self.wi.pn)
            # If the part has no description, try to fill from official API (e.g., Mouser)
            try:
                if not (self.wi.desc or "").strip():
                    api_desc = get_part_description_api_first(self.wi.pn)
                    if api_desc:
                        with app_state.get_session() as session:
                            services.update_part_description_if_empty(session, self.wi.part_id, api_desc)
            except Exception:
                pass
            src_page: Optional[str] = None
            if api_pdf_urls or api_page_urls:
                tmp = None
                had_api_pdf = bool(api_pdf_urls)
                api_referer = api_page_urls[0] if api_page_urls else (f"https://www.mouser.com/" if "mouser" in (self.wi.mfg or "").lower() else None)
                for idx, u in enumerate(api_pdf_urls, start=1):
                    self.sig.rowStatus.emit(self.row, f"Downloading API {idx}/{len(api_pdf_urls)}...")
                    logger.info("Auto-datasheet: downloading (API) %s", u)
                    tmp = self._download_pdf(u, trusted=True, referer=api_referer)
                    if tmp:
                        break
                # If no direct API PDF succeeded, try extracting PDFs from API product pages
                if not tmp and api_page_urls:
                    for jdx, page in enumerate(api_page_urls, start=1):
                        try:
                            self.sig.rowStatus.emit(self.row, f"API page {jdx}/{len(api_page_urls)}...")
                            logger.info("Auto-datasheet: scanning API page %s/%s %s", jdx, len(api_page_urls), page)
                            pdfs = find_pdfs_in_page(page, self.wi.pn, self.wi.mfg or "")
                        except Exception:
                            continue
                        for kdx, pdf_url in enumerate(pdfs, start=1):
                            logger.info("Auto-datasheet: downloading %s (API-extracted)", pdf_url)
                            # Treat PDFs extracted from distributor API product pages as trusted
                            tmp = self._download_pdf(pdf_url, trusted=True, referer=page)
                            if tmp:
                                break
                        if tmp:
                            break
                if tmp:
                    with app_state.get_session() as session:
                        dst, existed = services.register_datasheet_for_part(session, self.wi.part_id, Path(tmp))
                        canonical_path = str(dst)
                        if existed and not self.auto:
                            self.sig.rowStatus.emit(self.row, "Duplicate (review)")
                            self.sig.rowDone.emit(self.row, False, True)
                            self._log_outcome("duplicate_api", canonical=canonical_path)
                        else:
                            canonical = canonical_path
                            services.update_part_datasheet_url(session, self.wi.part_id, canonical)
                            # If part has no description, try to infer one from this validated PDF
                            try:
                                desc = infer_description_from_pdf(self.wi.pn, self.wi.mfg or "", Path(tmp))
                                if desc:
                                    services.update_part_description_if_empty(session, self.wi.part_id, desc)
                            except Exception:
                                pass
                            # Also persist a product link if we know one
                            try:
                                if api_page_urls:
                                    services.update_part_product_url(session, self.wi.part_id, api_page_urls[0])
                            except Exception:
                                pass
                            # notify listeners so UI can update immediately
                            self.sig.attached.emit(self.wi.part_id, canonical)
                            self.sig.rowStatus.emit(self.row, "Attached")
                            self.sig.rowDone.emit(self.row, True, existed)
                            self._log_outcome("attached_api", canonical=canonical, duplicate=existed)
                    # cleanup temporary file
                    try:
                        if tmp and os.path.exists(tmp):
                            os.remove(tmp)
                    except Exception:
                        pass
                    return
                # If API provided at least one PDF URL but none were downloadable as real PDFs,
                # stop here to avoid web search per user preference; optionally save product page link.
                if had_api_pdf:
                    first_page = api_page_urls[0] if api_page_urls else None
                    if first_page:
                        # Persist product page URL and expose link to UI; do not open browser
                        try:
                            with app_state.get_session() as session:
                                services.update_part_product_url(session, self.wi.part_id, first_page)
                        except Exception:
                            pass
                        self.sig.manualLink.emit(self.wi.part_id, first_page)
                        self.sig.rowStatus.emit(self.row, "Link saved")
                        self.sig.rowDone.emit(self.row, False, False)
                        self._log_outcome("api_page_link", page=first_page)
                    else:
                        self.sig.rowStatus.emit(self.row, "API PDF blocked")
                        self.sig.failed.emit(self.wi.part_id)
                        self.sig.rowDone.emit(self.row, False, False)
                        self._log_outcome("api_pdf_blocked")
                    return
                # If we have API product pages (even without API PDFs), save a link now and continue to web search
                elif api_page_urls:
                    first_page = api_page_urls[0]
                    try:
                        with app_state.get_session() as session:
                            services.update_part_product_url(session, self.wi.part_id, first_page)
                    except Exception:
                        pass
                    self.sig.manualLink.emit(self.wi.part_id, first_page)
                    self._log_outcome("api_page_hint", page=first_page)
            # Build exclusion list: avoid hosts already attempted via APIs
            exclude_hosts: set[str] = set()
            from urllib.parse import urlparse
            for u in list(api_pdf_urls) + list(api_page_urls):
                try:
                    h = (urlparse(u).netloc or "").lower()
                    if h:
                        exclude_hosts.add(h)
                except Exception:
                    pass

            cands: List[dict] = []
            for q in self._queries(self.wi, exclude_hosts):
                try:
                    for sr in search_web(q, count=10):
                        cands.append({"title": sr.title, "snippet": sr.snippet, "url": sr.url})
                except Exception:
                    continue
            if not cands:
                self.sig.rowStatus.emit(self.row, "No results")
                self.sig.failed.emit(self.wi.part_id)
                self.sig.rowDone.emit(self.row, False, False)
                self._log_outcome("no_search_results")
                return
            ranked = sorted(
                cands,
                key=lambda c: score_candidate(self.wi.pn, self.wi.mfg or "", c.get("title", ""), c.get("snippet", ""), c.get("url", "")),
                reverse=True,
            )
            pdfs = [c for c in ranked if c["url"].lower().endswith(".pdf")]
            shortlist = pdfs or ranked[:10]
            best = choose_best_datasheet_url(self.wi.pn, self.wi.mfg or "", self.wi.desc or "", shortlist) or None
            if not best:
                best = pdfs[0]["url"] if pdfs else None
            if not best:
                self.sig.rowStatus.emit(self.row, "No candidate")
                self.sig.failed.emit(self.wi.part_id)
                self.sig.rowDone.emit(self.row, False, False)
                self._log_outcome("no_candidate")
                return
            # Try API URLs first; then best + shortlist
            # Start with any direct PDF URLs returned by the API phase
            urls = list(api_pdf_urls)
            manual_urls = []
            seen = set()
            def _is_http(u: str) -> bool:
                try:
                    return isinstance(u, str) and u.lower().startswith(("http://", "https://"))
                except Exception:
                    return False

            for u in [best] + [u["url"] for u in shortlist]:
                if not u or u in seen or not _is_http(u):
                    continue
                seen.add(u)
                if u.lower().endswith('.pdf'):
                    urls.append(u)
                else:
                    manual_urls.append(u)
            tmp = None
            api_pdf_set = set(api_pdf_urls)
            for idx, u in enumerate(urls, start=1):
                self.sig.rowStatus.emit(self.row, f"Downloading {idx}/{len(urls)}...")
                logger.info("Auto-datasheet: downloading %s", u)
                # Only validate PDFs that came from web search; accept API PDFs without strict validation
                is_api = (u in api_pdf_set)
                tmp = self._download_pdf(u, trusted=is_api)
                if tmp and (not is_api):
                    # For web search direct PDFs, use the PDF URL as product link
                    src_page = u
                if tmp:
                    break
                if not tmp:
                    # Try to auto-extract PDF links from distributor/aggregator pages
                    extracted_any = False
                    for jdx, page_url in enumerate(manual_urls, start=1):
                        try:
                            self.sig.rowStatus.emit(self.row, f"Extracting PDF {jdx}/{len(manual_urls)}...")
                            pdfs = find_pdfs_in_page(page_url, self.wi.pn, self.wi.mfg or "")
                        except Exception:
                            continue
                        for kdx, pdf_url in enumerate(pdfs, start=1):
                            if pdf_url in seen:
                                continue
                            seen.add(pdf_url)
                            self.sig.rowStatus.emit(self.row, f"Downloading {kdx}/{len(pdfs)} from page...")
                            logger.info("Auto-datasheet: downloading %s (extracted)", pdf_url)
                            tmp = self._download_pdf(pdf_url)
                            if tmp:
                                src_page = page_url
                            if tmp:
                                extracted_any = True
                                break
                        if extracted_any:
                            break
                if not tmp:
                    # As a last resort, open the first page for manual download (UI thread)
                    if manual_urls:
                        if not self.manual_ok:
                            self.sig.rowStatus.emit(self.row, "Manual review required")
                            self.sig.failed.emit(self.wi.part_id)
                            self.sig.rowDone.emit(self.row, False, False)
                            self._log_outcome("manual_pages_disabled")
                            return
                        first = next((u for u in manual_urls if _is_http(u)), None)
                        if first:
                            try:
                                with app_state.get_session() as session:
                                    services.update_part_product_url(session, self.wi.part_id, first)
                            except Exception:
                                pass
                            self.sig.manualLink.emit(self.wi.part_id, first)
                            self.sig.rowStatus.emit(self.row, "Link saved")
                            # Mark done without attachment; UI shows link icon
                            self.sig.rowDone.emit(self.row, False, False)
                            self._log_outcome("manual_page_link", url=first)
                            return
                        else:
                            self.sig.rowStatus.emit(self.row, "No manual page available")
                            self.sig.failed.emit(self.wi.part_id)
                            self.sig.rowDone.emit(self.row, False, False)
                            self._log_outcome("manual_page_missing")
                            return
                    else:
                        self.sig.rowStatus.emit(self.row, "Download failed")
                        self.sig.failed.emit(self.wi.part_id)
                        self.sig.rowDone.emit(self.row, False, False)
                        self._log_outcome("download_failed")
                        return
            with app_state.get_session() as session:
                dst, existed = services.register_datasheet_for_part(session, self.wi.part_id, Path(tmp))
                if existed and not self.auto:
                    self.sig.rowStatus.emit(self.row, "Duplicate (review)")
                    self.sig.rowDone.emit(self.row, False, True)
                else:
                    canonical = str(dst)
                    services.update_part_datasheet_url(session, self.wi.part_id, canonical)
                    # If part has no description, try to infer one from this validated PDF
                    try:
                        desc = infer_description_from_pdf(self.wi.pn, self.wi.mfg or "", Path(tmp))
                        if desc:
                            services.update_part_description_if_empty(session, self.wi.part_id, desc)
                    except Exception:
                        pass
                    # Save product link for web-search success when available
                    try:
                        if src_page:
                            services.update_part_product_url(session, self.wi.part_id, src_page)
                    except Exception:
                        pass
                    # notify listeners so UI can update immediately
                    self.sig.attached.emit(self.wi.part_id, canonical)
                    self.sig.rowStatus.emit(self.row, "Attached")
                    self.sig.rowDone.emit(self.row, True, existed)
                    self._log_outcome("attached_web", canonical=canonical, duplicate=existed, source_page=src_page or "")
            # cleanup temporary file
            try:
                if tmp and os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
        except NoSearchProviderConfigured:
            self.sig.rowStatus.emit(self.row, "No search provider configured")
            self.sig.failed.emit(self.wi.part_id)
            self.sig.rowDone.emit(self.row, False, False)
            self._log_outcome("no_search_provider")
        except Exception as exc:
            # Avoid crashing if UI dialog/signals are already destroyed
            try:
                self.sig.rowStatus.emit(self.row, "Error")
            except RuntimeError:
                pass
            logger.exception("Auto-datasheet: unexpected error in worker")
            try:
                self.sig.failed.emit(self.wi.part_id)
            except RuntimeError:
                pass
            try:
                self.sig.rowDone.emit(self.row, False, False)
            except RuntimeError:
                pass
            self._log_outcome("error", error=str(exc))

    def _queries(self, wi: WorkItem, exclude_hosts: set[str] | None = None) -> List[str]:
        q: List[str] = []
        m = (wi.mfg or "").strip()
        d = (wi.desc or "").strip()
        exclude = " " + " ".join(f"-site:{h}" for h in sorted(exclude_hosts)) if exclude_hosts else ""
        q.append(f'"{wi.pn}" datasheet filetype:pdf{exclude}')
        if m:
            q.append(f'"{wi.pn}" {m} datasheet filetype:pdf{exclude}')
        q.append(f'"{wi.pn}" specification filetype:pdf{exclude}')
        if m:
            q.append(f'"{wi.pn}" {m} pdf{exclude}')
        if d:
            q.append(f'"{wi.pn}" {d} pdf{exclude}')
        # Preferred domains
        ex = exclude_hosts or set()
        for dom in recommended_domains_for(m, wi.pn)[:4]:
            dom_low = dom.lower()
            # Skip domains we already tried via APIs
            if any(d in dom_low or dom_low in d for d in ex):
                continue
            q.append(f'site:{dom} "{wi.pn}" filetype:pdf')
            q.append(f'site:{dom} "{wi.pn}" datasheet')
        return q

    def _download_pdf(self, url: str, trusted: bool = False, referer: Optional[str] = None) -> Optional[str]:
        try:
            # Heuristic headers for distributor/aggregator hosts
            ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
            headers = {
                "User-Agent": ua,
                "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }
            host = ""
            try:
                from urllib.parse import urlparse
                host = (urlparse(url).netloc or "").lower()
            except Exception:
                pass
            # Add Referer for known sites that may require it
            if "mouser.com" in host:
                headers.setdefault("Referer", referer or "https://www.mouser.com/")
            elif "digikey.com" in host:
                headers.setdefault("Referer", "https://www.digikey.com/")
            # Tune timeouts per host (connect, read)
            to = (10, 45)
            if any(h in host for h in ("mouser.com", "digikey.com", "farnell.com", "rs-online.com")):
                to = (10, int(os.getenv("BOM_DS_READ_TIMEOUT", 90)))

            # Separate connect/read timeouts; retry once on transient timeouts
            sess = self._sess
            if referer:
                headers["Referer"] = referer
            def _try_download(connect_read_timeout):
                return sess.get(url, stream=True, headers=headers, timeout=connect_read_timeout, allow_redirects=True)

            try:
                r = _try_download(to)
            except requests.ReadTimeout:
                logger.warning("Auto-datasheet: read timeout for %s; retrying once with extended timeout", url)
                r = _try_download((to[0], max(to[1], 120)))

            with r:
                if r.status_code != 200:
                    logger.warning("Auto-datasheet: HTTP %s for %s", r.status_code, url)
                    return None
                ctype = (r.headers.get("Content-Type") or "").lower()
                if "pdf" not in ctype and not url.lower().endswith(".pdf"):
                    logger.warning("Auto-datasheet: not a PDF content-type=%s url=%s", ctype, url)
                    return None
                size_limit = max(1, int(MAX_DATASHEET_MB)) * 1024 * 1024
                written = 0
                fd, path = tempfile.mkstemp(suffix=".pdf")
                with os.fdopen(fd, "wb") as f:
                    for chunk in r.iter_content(1024 * 64):
                        if not chunk:
                            continue
                        f.write(chunk)
                        written += len(chunk)
                        if written > size_limit:
                            logger.warning("Auto-datasheet: exceeded size limit %s MB for %s", MAX_DATASHEET_MB, url)
                            try:
                                f.close()
                                os.remove(path)
                            except Exception:
                                pass
                            return None
            # Quick integrity check: verify PDF magic header
            try:
                with open(path, "rb") as _pf:
                    magic = _pf.read(5)
                if magic != b"%PDF-":
                    logger.warning("Auto-datasheet: invalid PDF signature for %s; discarding", url)
                    try:
                        os.remove(path)
                    except Exception:
                        pass
                    return None
            except Exception:
                pass

            logger.info("Auto-datasheet: downloaded to temp %s", path)
            # Distributor/API PDFs are considered reliable; only validate for web-search results
            if trusted:
                ok, score = True, 2.0
            else:
                ok, score = pdf_matches_request(self.wi.pn, self.wi.mfg or "", self.wi.desc or "", Path(path), source_name=url)
            if not ok:
                logger.info(
                    "Auto-datasheet: validation failed (score=%.2f) for %s; discarding",
                    score,
                    url,
                )
                try:
                    os.remove(path)
                except Exception:
                    pass
                return None
            return path
        except requests.RequestException as e:
            logger.warning("Auto-datasheet: download failed for %s: %s", url, e)
            return None


class AutoDatasheetDialog(QDialog):
    attached = pyqtSignal(int, str)  # part_id, canonical path
    failed = pyqtSignal(int)  # part_id
    manualLink = pyqtSignal(int, str)  # part_id, page url
    def __init__(self, parent, work: List[WorkItem], on_locked_parts_changed):
        super().__init__(parent)
        self.setWindowTitle("Auto Datasheet")
        self.resize(900, 420)
        self.work = work
        self.on_locked_parts_changed = on_locked_parts_changed
        configured_workers = max(1, AUTO_DATASHEET_MAX_WORKERS)
        self._max_workers = max(1, min(configured_workers, len(work) or 1))
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(self._max_workers)
        logger.info(
            "Auto-datasheet dialog initialized: work_items=%d max_workers=%d (configured=%d)",
            len(work),
            self._max_workers,
            configured_workers,
        )
        self.sig = _Signals()
        self.sig.rowStatus.connect(self._row_status)
        self.sig.rowDone.connect(self._row_done)
        # re-emit worker events so parent can update row icon live
        self.sig.attached.connect(lambda pid, p: self.attached.emit(pid, p))
        self.sig.failed.connect(lambda pid: self.failed.emit(pid))
        self.sig.manualLink.connect(lambda pid, u: self.manualLink.emit(pid, u))
        # Open URL requests must happen on UI thread
        self.sig.openUrl.connect(lambda u: QDesktopServices.openUrl(QUrl.fromUserInput(u)))

        self.table = QTableWidget(len(work), 5)
        self.table.setHorizontalHeaderLabels(["PN", "Manufacturer", "Description", "Status", "Result"])
        for i, wi in enumerate(work):
            self.table.setItem(i, 0, QTableWidgetItem(wi.pn))
            self.table.setItem(i, 1, QTableWidgetItem(wi.mfg or ""))
            self.table.setItem(i, 2, QTableWidgetItem(wi.desc or ""))
            self.table.setItem(i, 3, QTableWidgetItem("Queued"))
            self.table.setItem(i, 4, QTableWidgetItem(""))

        self.progress = QProgressBar()
        self.progress.setRange(0, len(work))
        self.progress.setValue(0)
        self.auto_dupes = QCheckBox("Auto-link duplicates without asking")
        self.auto_dupes.setChecked(True)
        self.manual_pages = QCheckBox("Open distributor pages if PDF blocked")
        self.manual_pages.setChecked(True)
        self.btnStart = QPushButton("Start")
        self.btnCancel = QPushButton("Cancel")

        lo = QVBoxLayout(self)
        lo.addWidget(self.table)
        lo.addWidget(self.progress)
        lo2 = QHBoxLayout()
        lo2.addWidget(self.auto_dupes)
        lo2.addWidget(self.manual_pages)
        lo2.addStretch(1)
        lo2.addWidget(self.btnStart)
        lo2.addWidget(self.btnCancel)
        lo.addLayout(lo2)

        self.done = 0
        self.dup_queue: List[int] = []

        self.btnStart.clicked.connect(self._start)
        self.btnCancel.clicked.connect(self.reject)

    def _start(self):
        logger.info(
            "Auto-datasheet dispatch starting: work_items=%d concurrency=%d",
            len(self.work),
            self.pool.maxThreadCount(),
        )
        if len(self.work) > self.pool.maxThreadCount():
            logger.info(
                "Auto-datasheet queue depth=%d (exceeds concurrency)",
                len(self.work) - self.pool.maxThreadCount(),
            )
        if self.on_locked_parts_changed:
            self.on_locked_parts_changed({w.part_id for w in self.work}, lock=True)
        self.btnStart.setEnabled(False)
        self.btnCancel.setEnabled(False)
        auto = self.auto_dupes.isChecked()
        for i, wi in enumerate(self.work):
            worker = _Worker(i, wi, auto, self.sig)
            # pass manual-pages preference
            worker.manual_ok = self.manual_pages.isChecked()
            logger.debug(
                "Auto-datasheet: queue worker row=%s part_id=%s manual_ok=%s", i, wi.part_id, worker.manual_ok
            )
            self.pool.start(worker)
        try:
            logger.debug(
                "Auto-datasheet: dispatch complete active=%s queued=%s",
                self.pool.activeThreadCount(),
                max(0, len(self.work) - self.pool.activeThreadCount()),
            )
        except Exception:
            pass

    def _row_status(self, row: int, text: str):
        self.table.item(row, 3).setText(text)

    def _row_done(self, row: int, attached: bool, duplicate: bool):
        self.done += 1
        self.progress.setValue(self.done)
        self.table.item(row, 4).setText(
            "Attached" if attached else ("Duplicate" if duplicate else "")
        )
        if duplicate:
            self.dup_queue.append(row)
        logger.debug(
            "Auto-datasheet: row completed row=%s attached=%s duplicate=%s done=%s/%s",
            row,
            attached,
            duplicate,
            self.done,
            len(self.work),
        )
        if self.done == len(self.work):
            self._finish()

    def _finish(self):
        logger.info(
            "Auto-datasheet dialog finished: total=%d duplicates=%d auto_dupes=%s",
            len(self.work),
            len(self.dup_queue),
            self.auto_dupes.isChecked(),
        )
        if self.on_locked_parts_changed:
            self.on_locked_parts_changed({w.part_id for w in self.work}, lock=False)
        if self.dup_queue and not self.auto_dupes.isChecked():
            self._review_duplicates()
        self.accept()

    def _review_duplicates(self):
        ret = QMessageBox.question(
            self,
            "Duplicates found",
            f"{len(self.dup_queue)} duplicates found. Link all to existing file?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if ret == QMessageBox.StandardButton.Yes:
            pass
