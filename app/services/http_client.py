from __future__ import annotations

import threading
from contextlib import contextmanager
import time
from typing import Dict
from urllib.parse import urlparse
import os

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_session_lock = threading.Lock()
_session: requests.Session | None = None

# Throttle certain hosts that have shown instability under parallel access
_host_limits: Dict[str, int] = {
    "api.mouser.com": 1,
    "www.mouser.com": 1,
}
_host_semaphores: Dict[str, threading.Semaphore] = {}
_semaphores_lock = threading.Lock()

# Global concurrency limit across all hosts (env override: BOM_HTTP_MAX_CONCURRENCY)
try:
    _global_max = max(1, int(os.getenv("BOM_HTTP_MAX_CONCURRENCY", "2")))
except Exception:
    _global_max = 2
_global_sem = threading.Semaphore(_global_max)


def _build_session() -> requests.Session:
    s = requests.Session()
    retries = Retry(
        total=2,
        read=2,
        connect=2,
        backoff_factor=0.3,
        status_forcelist=(429, 500, 502, 503, 504),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retries, pool_connections=2, pool_maxsize=2)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    # Keep env proxies off by default to reduce surprises
    s.trust_env = False  # do not read HTTP(S)_PROXY from env
    # Mildly browsery UA to avoid trivial blocks
    s.headers.update({
        "User-Agent": "BOM-DB-Autosheet/1.0 (+requests)",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return s


def get_session() -> requests.Session:
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:
                _session = _build_session()
    return _session


def _key_for(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""


@contextmanager
def throttle(url: str):
    key = _key_for(url)
    sem: threading.Semaphore | None = None
    acquired_global = False
    try:
        _global_sem.acquire()
        acquired_global = True
    except Exception:
        pass
    if key and key in _host_limits:
        with _semaphores_lock:
            sem = _host_semaphores.get(key)
            if sem is None:
                sem = threading.Semaphore(max(1, int(_host_limits.get(key, 1))))
                _host_semaphores[key] = sem
        sem.acquire()
        try:
            yield
        finally:
            try:
                sem.release()
            except Exception:
                pass
            # Small cooldown to avoid hammering fragile hosts
            time.sleep(0.03)
    else:
        # No throttling for this host
        try:
            yield
        finally:
            # Mild cooldown anyway
            time.sleep(0.01)
    if acquired_global:
        try:
            _global_sem.release()
        except Exception:
            pass
