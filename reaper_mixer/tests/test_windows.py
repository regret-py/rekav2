"""Fonctions Win32 du gestionnaire de processus, sur le vrai Windows : le Bloc-notes
joue le rôle d'un moteur (lancement, masquage, ré-affichage, fermeture)."""

import os
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows uniquement")


def test_registry_lookup():
    from core.process_manager import _registry_install_dir

    found = _registry_install_dir("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion",
                                  "ProgramFilesDir")
    assert found is not None
    assert str(found).lower() == os.environ["ProgramW6432"].lower()


def test_launch_hide_show_close_notepad():
    import win32gui

    from core.process_manager import AppSpec, ProcessManager

    spec = AppSpec(key="notepad", name="Bloc-notes", exe_names=("notepad.exe",),
                   install_dirs=(r"%SystemRoot%\System32",), registry=(),
                   window_classes=("Notepad",), window_title="bloc-notes",
                   download_url="", attach_foreign=True)
    cfg = {"notepad": {"window_timeout_s": 20, "close_timeout_s": 10}}
    manager = ProcessManager(cfg)
    try:
        manager._start(spec, args=[])
        managed = manager._get("notepad")
        assert managed.launched_by_us and Path(cfg["notepad"]["path"]).name.lower() == "notepad.exe"
        windows = managed.main_windows()
        assert windows, "fenêtre du Bloc-notes introuvable"

        def visible():
            return any(win32gui.IsWindowVisible(h) for h in managed.main_windows())

        def wait(condition, timeout=5.0):
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                if condition():
                    return True
                time.sleep(0.05)
            return False

        assert wait(lambda: not visible()), "fenêtre non masquée"
        manager.set_window_visible("notepad", True)
        assert wait(visible), "fenêtre non ré-affichée"
        manager.set_window_visible("notepad", False)
        assert wait(lambda: not visible())
        # Si la fenêtre réapparaît d'elle-même, le chien de garde la re-masque
        win32gui.ShowWindow(windows[0], 5)  # SW_SHOW
        assert wait(lambda: not visible(), 3.0), "le chien de garde n'a pas re-masqué"
        assert manager.will_terminate("notepad")
    finally:
        manager.shutdown()
    assert not managed.is_alive(), "Bloc-notes toujours ouvert après shutdown()"


def test_app_volume_backend_loads():
    """Le moteur Core Audio se charge et parcourt les périphériques sans erreur
    (la machine de test n'a pas forcément Voicemeeter ni de carte son)."""
    from core.app_volume import WindowsAppVolumes, create_backend

    backend = WindowsAppVolumes()
    for index in range(3):
        name = backend.device_name(index)
        apps = backend.apps(index)
        assert name is None or "voicemeeter" in name.lower() or name
        assert isinstance(apps, list)
    assert create_backend({}).available
