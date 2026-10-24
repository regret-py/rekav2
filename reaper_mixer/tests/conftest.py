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


@pytest.fixture
def mixer(qapp, cfg):
    """Console en démo (Voicemeeter et REAPER simulés), affichée."""
    from core.app_volume import DemoAppVolumes
    from core.demo import DemoProcesses, DemoReaper, DemoVoicemeeter
    from core.media import DemoMedia
    from ui.main_window import MainWindow

    vm, reaper = DemoVoicemeeter(cfg), DemoReaper(cfg)
    window = MainWindow(cfg=cfg, vm=vm, reaper=reaper, processes=DemoProcesses(), persist=False,
                        apps=DemoAppVolumes(), media=DemoMedia())
    window.resize(1920, 1040)
    window.show()
    pump(qapp, 0.2)
    yield window, vm, reaper
    window.close()
