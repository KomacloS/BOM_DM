"""Custom Qt widgets used by the GUI."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path as _Path

_legacy_path = _Path(__file__).resolve().parent.parent / 'widgets.py'
_spec = importlib.util.spec_from_file_location('app.gui._legacy_widgets', _legacy_path)
if _spec is None or _spec.loader is None:
    raise ImportError('Unable to load legacy widgets module')
_legacy_widgets = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_legacy_widgets)
sys.modules.setdefault('app.gui._legacy_widgets', _legacy_widgets)

from .complex_panel import ComplexPanel  # noqa: E402

AssembliesPane = _legacy_widgets.AssembliesPane
CustomersPane = _legacy_widgets.CustomersPane
ProjectsPane = _legacy_widgets.ProjectsPane

__all__ = [
    'AssembliesPane',
    'CustomersPane',
    'ProjectsPane',
    'ComplexPanel',
]
