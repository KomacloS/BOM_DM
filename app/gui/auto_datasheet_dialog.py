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
from ..services.datasheet_api import resolve_datasheet_api_first
from . import state as app_state
import requests
import logging
from ..config import MAX_DATASHEET_MB


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

    def run(self):
        logging.info("Auto-datasheet worker start: row=%s part_id=%s pn=%s", self.row, self.wi.part_id, self.wi.pn)
        try:
            logging.info(
                "API-first: configured -> mouser=%s digikey=%s nexar=%s",
                bool(os.getenv("MOUSER_API_KEY") or os.getenv("PROVIDER_MOUSER_KEY")),
                bool(os.getenv("DIGIKEY_ACCESS_TOKEN") or os.getenv("PROVIDER_DIGIKEY_ACCESS_TOKEN")),
                bool(os.getenv("NEXAR_ACCESS_TOKEN") or os.getenv("PROVIDER_OCTOPART_ACCESS_TOKEN")),
            )
            self.sig.rowStatus.emit(self.row, "Searching...")
            # API-first resolution (Mouser/Digi-Key/Nexar) before web search
            api_pdf_urls, api_page_urls = resolve_datasheet_api_first(self.wi.pn)
            if api_pdf_urls or api_page_urls:
                tmp = None
                for idx, u in enumerate(api_pdf_urls, start=1):
                    self.sig.rowStatus.emit(self.row, f"Downloading API {idx}/{len(api_pdf_urls)}...")
                    logging.info("Auto-datasheet: downloading (API) %s", u)
                    tmp = self._download_pdf(u)
                    if tmp:
                        break
                # If no direct API PDF succeeded, try extracting PDFs from API product pages
                if not tmp and api_page_urls:
                    for jdx, page in enumerate(api_page_urls, start=1):
                        try:
                            self.sig.rowStatus.emit(self.row, f"API page {jdx}/{len(api_page_urls)}...")
                            logging.info("Auto-datasheet: scanning API page %s/%s %s", jdx, len(api_page_urls), page)
                            pdfs = find_pdfs_in_page(page, self.wi.pn, self.wi.mfg or "")
                        except Exception:
                            continue
                        for kdx, pdf_url in enumerate(pdfs, start=1):
                            logging.info("Auto-datasheet: downloading %s (API-extracted)", pdf_url)
                            tmp = self._download_pdf(pdf_url)
                            if tmp:
                                break
                        if tmp:
                            break
                if tmp:
                    with app_state.get_session() as session:
                        dst, existed = services.register_datasheet_for_part(session, self.wi.part_id, Path(tmp))
                        if existed and not self.auto:
                            self.sig.rowStatus.emit(self.row, "Duplicate (review)")
                            self.sig.rowDone.emit(self.row, False, True)
                        else:
                            canonical = str(dst)
                            services.update_part_datasheet_url(session, self.wi.part_id, canonical)
                            # notify listeners so UI can update immediately
                            self.sig.attached.emit(self.wi.part_id, canonical)
                            self.sig.rowStatus.emit(self.row, "Attached")
                            self.sig.rowDone.emit(self.row, True, existed)
                    # cleanup temporary file
                    try:
                        if tmp and os.path.exists(tmp):
                            os.remove(tmp)
                    except Exception:
                        pass
                    return
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
            for idx, u in enumerate(urls, start=1):
                self.sig.rowStatus.emit(self.row, f"Downloading {idx}/{len(urls)}...")
                logging.info("Auto-datasheet: downloading %s", u)
                tmp = self._download_pdf(u)
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
                            logging.info("Auto-datasheet: downloading %s (extracted)", pdf_url)
                            tmp = self._download_pdf(pdf_url)
                            if tmp:
                                extracted_any = True
                                break
                        if extracted_any:
                            break
                if not tmp:
                    # As a last resort, open the first page for manual download (UI thread)
                    if self.manual_ok and manual_urls:
                        first = next((u for u in manual_urls if _is_http(u)), None)
                        if first:
                            self.sig.openUrl.emit(first)
                            self.sig.manualLink.emit(self.wi.part_id, first)
                        else:
                            # nothing usable to open
                            self.sig.rowStatus.emit(self.row, "No manual page available")
                            self.sig.failed.emit(self.wi.part_id)
                            self.sig.rowDone.emit(self.row, False, False)
                            return
                        self.sig.rowStatus.emit(self.row, "Manual: opened page in browser")
                        # Mark done without attachment; UI shows link icon
                        self.sig.rowDone.emit(self.row, False, False)
                        return
                    else:
                        self.sig.rowStatus.emit(self.row, "Download failed")
                        self.sig.failed.emit(self.wi.part_id)
                        self.sig.rowDone.emit(self.row, False, False)
                        return
            with app_state.get_session() as session:
                dst, existed = services.register_datasheet_for_part(session, self.wi.part_id, Path(tmp))
                if existed and not self.auto:
                    self.sig.rowStatus.emit(self.row, "Duplicate (review)")
                    self.sig.rowDone.emit(self.row, False, True)
                else:
                    canonical = str(dst)
                    services.update_part_datasheet_url(session, self.wi.part_id, canonical)
                    # notify listeners so UI can update immediately
                    self.sig.attached.emit(self.wi.part_id, canonical)
                    self.sig.rowStatus.emit(self.row, "Attached")
                    self.sig.rowDone.emit(self.row, True, existed)
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
        except Exception:
            self.sig.rowStatus.emit(self.row, "Error")
            logging.exception("Auto-datasheet: unexpected error in worker")
            self.sig.failed.emit(self.wi.part_id)
            self.sig.rowDone.emit(self.row, False, False)

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

    def _download_pdf(self, url: str) -> Optional[str]:
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
                headers.setdefault("Referer", "https://www.mouser.com/")
            elif "digikey.com" in host:
                headers.setdefault("Referer", "https://www.digikey.com/")
            # Tune timeouts per host (connect, read)
            to = (10, 45)
            if any(h in host for h in ("mouser.com", "digikey.com", "farnell.com", "rs-online.com")):
                to = (10, int(os.getenv("BOM_DS_READ_TIMEOUT", 90)))

            # Separate connect/read timeouts; retry once on transient timeouts
            def _try_download(connect_read_timeout):
                return requests.get(url, stream=True, headers=headers, timeout=connect_read_timeout)

            try:
                r = _try_download(to)
            except requests.ReadTimeout:
                logging.warning("Auto-datasheet: read timeout for %s; retrying once with extended timeout", url)
                r = _try_download((to[0], max(to[1], 120)))

            with r:
                if r.status_code != 200:
                    logging.warning("Auto-datasheet: HTTP %s for %s", r.status_code, url)
                    return None
                ctype = (r.headers.get("Content-Type") or "").lower()
                if "pdf" not in ctype and not url.lower().endswith(".pdf"):
                    logging.warning("Auto-datasheet: not a PDF content-type=%s url=%s", ctype, url)
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
                            logging.warning("Auto-datasheet: exceeded size limit %s MB for %s", MAX_DATASHEET_MB, url)
                            try:
                                f.close()
                                os.remove(path)
                            except Exception:
                                pass
                            return None
            logging.info("Auto-datasheet: downloaded to temp %s", path)
            # Validate the PDF matches the requested PN/manufacturer
            ok, score = pdf_matches_request(self.wi.pn, self.wi.mfg or "", self.wi.desc or "", Path(path), source_name=url)
            if not ok:
                logging.info(
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
            logging.warning("Auto-datasheet: download failed for %s: %s", url, e)
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
        self.pool = QThreadPool.globalInstance()
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
        if self.on_locked_parts_changed:
            self.on_locked_parts_changed({w.part_id for w in self.work}, lock=True)
        self.btnStart.setEnabled(False)
        self.btnCancel.setEnabled(False)
        auto = self.auto_dupes.isChecked()
        for i, wi in enumerate(self.work):
            worker = _Worker(i, wi, auto, self.sig)
            # pass manual-pages preference
            worker.manual_ok = self.manual_pages.isChecked()
            self.pool.start(worker)

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
        if self.done == len(self.work):
            self._finish()

    def _finish(self):
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
