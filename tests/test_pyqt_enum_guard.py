from pathlib import Path


def test_no_legacy_qmessagebox_enums():
    root = Path(__file__).resolve().parents[1]
    disallowed = ["QMessageBox.AcceptRole", "QMessageBox.Cancel"]
    offenders = []
    current = Path(__file__).resolve()
    for path in root.rglob("*.py"):
        if not path.is_file():
            continue
        if path.resolve() == current:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for token in disallowed:
            if token in text:
                offenders.append((path.relative_to(root), token))
    assert not offenders, f"Legacy QMessageBox enums found: {offenders}"
