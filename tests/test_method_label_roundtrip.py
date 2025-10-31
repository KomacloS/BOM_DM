from __future__ import annotations

from app.services.test_resolution import method_enum_to_label, method_label_to_enum


def test_method_label_enum_roundtrip() -> None:
    pairs = [
        ("macro", "Macro"),
        ("python", "Python code"),
        ("quick_test", "Quick test (QT)"),
        ("complex", "Complex"),
    ]
    for enum_name, label in pairs:
        assert method_enum_to_label(enum_name) == label
        assert method_label_to_enum(label) == enum_name
