"""Custom Qt widgets used by the GUI."""

from __future__ import annotations

# Import legacy widgets from app/gui/legacy_widgets.py
from ..legacy_widgets import (
    AssembliesPane,
    CustomersPane,
    ProjectsPane,
)

# Local widget(s) inside this package
from .complex_panel import ComplexPanel

__all__ = [
    "AssembliesPane",
    "CustomersPane",
    "ProjectsPane",
    "ComplexPanel",
]
