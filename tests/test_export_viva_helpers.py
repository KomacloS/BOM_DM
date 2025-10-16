from datetime import datetime

from app.services import export_viva


def test_sanitize_token_replaces_invalid_characters():
    assert export_viva.sanitize_token("Proj:01/Alpha", "FALLBACK") == "Proj_01_Alpha"
    assert export_viva.sanitize_token("   ", "FALLBACK") == "FALLBACK"


def test_build_export_folder_name_uses_timestamp():
    ts = datetime(2024, 5, 6, 7, 8)
    name = export_viva.build_export_folder_name("Code", "RevA", timestamp=ts)
    assert name == "VIVA_Code_RevA_20240506_0708"


def test_build_export_paths_returns_expected_structure(tmp_path):
    ts = datetime(2024, 1, 2, 3, 4)
    base = tmp_path / "exports"
    paths = export_viva.build_export_paths(base, "ACME-100", "Rev B", timestamp=ts)
    expected_folder = base / "VIVA_ACME-100_Rev B_20240102_0304"
    assert paths.folder == expected_folder
    assert paths.bom_txt == expected_folder / "BOM_to_VIVA.txt"
    assert paths.mdb_path == expected_folder / "bom_complexes.mdb"
