# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

repo_root = Path(__file__).resolve().parent

datas = [('app/gui/icons', 'app/gui/icons')]
binaries = []
hiddenimports = []
tmp_ret = collect_all('PyQt6')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
hiddenimports += ["sqlmodel"]


def _add_tree(src: Path, dest_root: Path) -> None:
    if not src.exists():
        return
    for path in src.rglob('*'):
        if path.is_dir():
            continue
        if any(part == '__pycache__' for part in path.parts):
            continue
        rel = path.relative_to(src)
        datas.append((str(path), str(dest_root / rel)))


portable_roots = {
    repo_root / 'app': Path('internal/app'),
    repo_root / 'data': Path('internal/data'),
    repo_root / 'static': Path('internal/static'),
    repo_root / 'migrations': Path('internal/migrations'),
}

for src_dir, dest in portable_roots.items():
    _add_tree(src_dir, dest)

portable_files = [
    (repo_root / 'bom_template.csv', Path('internal/bom_template.csv')),
    (repo_root / 'agents.example.toml', Path('internal/agents.example.toml')),
    (repo_root / 'agents.local.toml', Path('internal/agents.local.toml')),
]

for src_file, dest_file in portable_files:
    if src_file.exists():
        datas.append((str(src_file), str(dest_file)))


block_cipher = None


a = Analysis(
    ['app\\gui\\__main__.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='BOM_DB',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='BOM_DB',
)
