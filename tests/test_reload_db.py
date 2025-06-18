import importlib
from pathlib import Path

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import app.config as config


def test_reload_db(tmp_path):
    cfg = importlib.reload(config)
    old_engine = cfg.get_engine()
    old_url = str(old_engine.url)
    new_url = f"sqlite:///{tmp_path/'new.db'}"
    cfg.save_database_url(new_url)
    cfg.reload_settings()
    new_engine = cfg.get_engine()
    assert str(new_engine.url) == new_url
    assert str(old_engine.url) != str(new_engine.url)

