import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("BOM_SETTINGS_PATH", os.path.join(os.getcwd(), "_pytest_settings.toml"))
os.environ.setdefault("CE_APP_EXE", sys.executable)

collect_ignore = ["app/domain/test_resolution.py"]
