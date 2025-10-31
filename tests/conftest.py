from __future__ import annotations

import ctypes
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


def _has_libgl() -> bool:
    try:
        ctypes.CDLL("libGL.so.1")
        return True
    except OSError:
        return False


def pytest_collection_modifyitems(config, items):
    if _has_libgl():
        return
    skip = pytest.mark.skip(reason="libGL missing; skipping GUI tests in CI")
    for item in items:
        if "gui" in item.keywords:
            item.add_marker(skip)


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.append(ROOT)


def pytest_configure(config) -> None:
    config.addinivalue_line("markers", "gui: marks tests that require a GUI environment")
