"""GUI package facade: re-exports commonly used widgets."""

from __future__ import annotations

# legacy widgets are in app/gui/legacy_widgets.py
from .legacy_widgets import (
    AssembliesPane,
    CustomersPane,
    ProjectsPane,
)

# ComplexPanel is inside the widgets subpackage
from .widgets.complex_panel import ComplexPanel

__all__ = [
    "AssembliesPane",
    "CustomersPane",
    "ProjectsPane",
    "ComplexPanel",
]
