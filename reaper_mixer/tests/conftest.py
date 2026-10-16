"""Configuration commune des tests : chemins, Qt hors écran, %APPDATA% temporaire."""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def appdata(tmp_path, monkeypatch):
    """Chaque test a son propre %APPDATA% : la vraie config n'est jamais touchée."""
    path = tmp_path / "appdata"
    path.mkdir()
    monkeypatch.setenv("APPDATA", str(path))
    return path


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    from ui import styles

    app = QApplication.instance() or QApplication([])
    styles.apply_theme(app)
    return app


@pytest.fixture
def cfg(appdata):
    from core import config

    return config.load_config()


def pump(app, seconds: float) -> None:
    """Fait tourner la boucle Qt (les signaux venant d'autres threads arrivent en file)."""
    import time

    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
