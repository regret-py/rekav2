"""Interface complète en mode démo : clics, glisser, renommage, effets."""

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox

from conftest import pump

LEFT = Qt.MouseButton.LeftButton
NONE = Qt.MouseButton.NoButton


def send(widget, kind, pos, button=LEFT, buttons=LEFT):
    event = QMouseEvent(kind, QPointF(*pos), widget.mapToGlobal(QPointF(*pos)), button, buttons,
                        Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, event)


def click(widget, pos):
    send(widget, QEvent.Type.MouseButtonPress, pos)
    send(widget, QEvent.Type.MouseButtonRelease, pos, LEFT, NONE)


def drag(widget, start, end, steps=10):
    send(widget, QEvent.Type.MouseButtonPress, start)
    for i in range(1, steps + 1):
        pos = (start[0] + (end[0] - start[0]) * i / steps, start[1] + (end[1] - start[1]) * i / steps)
        send(widget, QEvent.Type.MouseMove, pos, NONE, LEFT)
    send(widget, QEvent.Type.MouseButtonRelease, end, LEFT, NONE)


def test_routing_knob_click_and_drag(qapp, mixer):
    window, vm, _ = mixer
    knob = window.strips[1].knobs[1]
    center = (knob.width() / 2, knob.height() / 2)
    assert not knob.isActive()
    click(knob, center)
    pump(qapp, 0.05)
    assert vm._strips[1].routes[1] and knob.isActive()

    before = vm._strips[1].sends[1]
    drag(knob, center, (center[0], center[1] + 60))
    pump(qapp, 0.05)
    assert vm._strips[1].sends[1] < before and vm._strips[1].routes[1]   # glisser ≠ bascule


def test_fader_mute_and_value_label(qapp, mixer):
    window, vm, _ = mixer
    strip = window.strips[1]
    fader = strip.fader
    y = fader._y_for(fader.value())
    drag(fader, (fader._rail_x(), y), (fader._rail_x(), y + 200))
    pump(qapp, 0.05)
    assert vm._strips[1].gain < -10
    assert strip.value_label.text().startswith(f"{vm._strips[1].gain:.1f}")
    send(fader, QEvent.Type.MouseButtonDblClick, (fader._rail_x(), fader._y_for(fader.value())))
    pump(qapp, 0.05)
    assert vm._strips[1].gain == 0.0

    QTest.mouseClick(strip.mute, LEFT)
    pump(qapp, 0.05)
    assert vm._strips[1].mute and strip.mute.isChecked()


def test_effects_pills_badge_and_rename(qapp, mixer):
    window, _, reaper = mixer
    mic = window.strips[0]
    assert len(mic.fx_list._pills) == 6
    QTest.mouseClick(mic.fx_list._pills[1], LEFT)
    pump(qapp, 0.05)
    assert reaper._tracks[0].fx[1].open

    assert not mic.fx_badge.isChecked()
    QTest.mouseClick(mic.fx_badge, LEFT)
    pump(qapp, 0.05)
    assert reaper._tracks[0].fx_enabled

    name = window.strips[1].name
    send(name, QEvent.Type.MouseButtonDblClick, (name.width() / 2, name.height() / 2))
    name._edit.selectAll()
    QTest.keyClicks(name._edit, "Spotify")
    QTest.keyClick(name._edit, Qt.Key.Key_Return)
    pump(qapp, 0.05)
    assert reaper._tracks[1].name == "Spotify"
    assert window._cfg["ui"]["strip_names"][1] == "Spotify"


def test_add_and_remove_effect(qapp, mixer):
    window, _, reaper = mixer
    strip = window.strips[1]

    def pick():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QDialog)
        QTest.keyClicks(dialog.search, "pro-q")
        assert dialog.list.count() == 1
        dialog._accept()

    QTimer.singleShot(150, pick)
    send(strip.fx_list, QEvent.Type.MouseButtonPress, (strip.fx_list.width() / 2, 5))
    pump(qapp, 0.3)
    assert [fx.display_name for fx in reaper._tracks[1].fx] == ["Pro-Q 4 (FabFilter)"]

    def confirm():
        box = QApplication.activeModalWidget()
        assert isinstance(box, QMessageBox)
        box.button(QMessageBox.StandardButton.Yes).click()

    QTimer.singleShot(150, confirm)
    strip.fxRemoveRequested.emit(1, 0)
    pump(qapp, 0.3)
    assert reaper._tracks[1].fx == ()


def test_headphone_knob_smooth(qapp, mixer):
    window, vm, _ = mixer
    knob = window.master.knob
    event = QWheelEvent(QPointF(30, 30), knob.mapToGlobal(QPointF(30, 30)), QPoint(0, 0),
                        QPoint(0, -480), NONE, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(knob, event)
    assert vm._headphone < 0 and knob._timer.isActive()      # interpolation en cours
    pump(qapp, 0.5)
    assert not knob._timer.isActive()
    assert window.master.value_label.text() == f"{vm._headphone:.1f} dB"


def test_project_button_switches_project(qapp, mixer, tmp_path):
    window, _, reaper = mixer
    user = tmp_path / "MAIN.rpp"
    user.write_text("<REAPER_PROJECT\n>\n")

    def pick():
        dialog = QApplication.activeModalWidget()
        assert dialog is not None and dialog.list.count() >= 1     # projet vierge en tête
        dialog.list.setCurrentItem(dialog._add_item(user.stem, str(user)))
        dialog.make_default.setChecked(True)
        dialog._accept()

    QTimer.singleShot(150, pick)
    QTest.mouseClick(window.master.project, LEFT)
    pump(qapp, 0.3)
    assert reaper.project_path == str(user)
    assert window._cfg["reaper"]["project"] == str(user)
    assert window._cfg["reaper"]["recent_projects"][0] == str(user)
    assert window.master.project._name == "MAIN"


def test_inserts_follow_strip_tracks(qapp, mixer):
    window, vm, reaper = mixer
    assert vm.inserts == set(range(6))          # chaque strip a une piste qui traite le son
    tracks = reaper.strip_tracks
    tracks[1] = None                            # projet sans piste pour « Musique »
    reaper.tracks_changed.emit(tracks)
    pump(qapp, 0.05)
    assert vm.inserts == {0, 2, 3, 4, 5}
    assert not window.strips[1].fx_badge.isEnabled() and window.strips[0].fx_badge.isEnabled()
    window._cfg["voicemeeter"]["manage_insert"] = False
    window.update_inserts()
    assert vm.inserts == set()


def test_custom_background_and_panel_opacity(qapp, mixer, tmp_path, appdata):
    from PySide6.QtGui import QColor, QPixmap

    from ui.settings_dialog import SettingsDialog

    window, vm, reaper = mixer
    image = tmp_path / "Fond.PNG"
    pixmap = QPixmap(40, 30)
    pixmap.fill(QColor("red"))
    assert pixmap.save(str(image), "PNG")

    dialog = SettingsDialog(window._cfg, None, reaper, vm, window, persist=False)
    dialog.appearanceChanged.connect(window.apply_appearance)
    assert [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())] == [
        "Général", "Canaux", "Audio", "Apparence", "Couleurs", "À propos"]
    assert not dialog.remove_background.isEnabled()
    dialog.set_background(image)                # copiée dans %APPDATA%/REKA
    assert window._cfg["ui"]["background_image"] == "background.png"
    assert (appdata / "REKA" / "background.png").is_file() and window.background.has_image
    dialog.darkness.setValue(70)
    assert window._cfg["ui"]["background_darkness"] == 0.7 and window.background._darkness == 0.7
    dialog.opacity.setValue(60)
    assert window._cfg["ui"]["panel_opacity"] == 0.6 and "rgba(" in qapp.styleSheet()

    dialog._clear_background()
    assert window._cfg["ui"]["background_image"] == "" and not window.background.has_image
    assert not list((appdata / "REKA").glob("background.*"))
    dialog.opacity.setValue(100)
    dialog.done(0)


def test_minimize_and_compact_window(qapp, mixer):
    window, _, _ = mixer
    QTest.mouseClick(window.master.minimize, LEFT)
    pump(qapp, 0.05)
    assert window.isMinimized()

    class FakeTray:
        def __init__(self):
            self.messages = []

        def showMessage(self, *args):  # noqa: N802 (API Qt)
            self.messages.append(args)

        def hide(self):
            pass

    window._tray = FakeTray()                   # zone de notification simulée
    window._cfg["ui"]["minimize_to_tray"] = True
    window.show_from_tray()
    window.minimize()
    pump(qapp, 0.05)
    assert not window.isVisible() and not qapp.quitOnLastWindowClosed()
    assert len(window._tray.messages) == 1      # l'astuce n'est montrée qu'une fois
    window.show_from_tray()
    pump(qapp, 0.05)
    assert window.isVisible() and not window.isMinimized() and qapp.quitOnLastWindowClosed()

    window.reset_window_size()                  # « Taille compacte (comme Voicemeeter) »
    pump(qapp, 0.05)
    assert window.width() == window.compact_size()[0] < 1920


def test_edition_choice_needs_restart(qapp, mixer):
    from ui.settings_dialog import SettingsDialog

    window, vm, reaper = mixer
    dialog = SettingsDialog(window._cfg, None, reaper, vm, window, persist=False)
    assert dialog.restart_button.isHidden()     # Potato tourne déjà
    banana = next(b for b in dialog.edition_group.buttons() if b.property("kind") == "banana")
    banana.setChecked(True)
    assert window._cfg["voicemeeter"]["kind"] == "banana"
    assert not dialog.restart_button.isHidden() and "Banana" in dialog.edition_hint.text()
    restarts = []
    dialog.restartRequested.connect(lambda: restarts.append(True))
    dialog.restart_button.click()
    assert restarts == [True]
    dialog.done(0)

    # Depuis la console : les réglages se ferment, puis REKA redémarre (en démo : un message)
    window._cfg["voicemeeter"]["kind"] = "potato"

    def choose_banana():
        settings = QApplication.activeModalWidget()
        assert settings is not None and settings.isVisible()
        next(b for b in settings.edition_group.buttons() if b.property("kind") == "banana").click()
        settings.restart_button.click()

    QTimer.singleShot(150, choose_banana)
    window.open_settings()
    pump(qapp, 0.05)
    assert QApplication.activeModalWidget() is None and window.isVisible()
    assert window.toast.text() == "Mode démo : pas de redémarrage."


def test_banana_console(qapp, cfg):
    from core.demo import DemoProcesses, DemoReaper, DemoVoicemeeter
    from ui.main_window import MainWindow

    cfg["voicemeeter"]["kind"] = "banana"
    vm, reaper = DemoVoicemeeter(cfg), DemoReaper(cfg)
    window = MainWindow(cfg=cfg, vm=vm, reaper=reaper, processes=DemoProcesses(), persist=False)
    window.show()
    pump(qapp, 0.2)
    assert len(window.strips) == 5 and len(reaper.strip_tracks) == 5
    knob = window.strips[1].knobs[1]
    center = (knob.width() / 2, knob.height() / 2)
    before = vm._strips[1].sends
    drag(knob, center, (center[0], center[1] + 60))   # pas de niveau d'envoi sur Banana
    click(knob, center)                               # mais la bascule fonctionne
    pump(qapp, 0.05)
    assert vm._strips[1].sends == before and vm._strips[1].routes[1]
    window.close()


def test_intellipan_pad(qapp, mixer):
    from ui.strip_widget import VU_BAR_WIDTH, VU_GAP

    window, vm, _ = mixer
    pad = window.strips[1].pad                     # Musique : entrée matérielle
    assert pad.modes() == ("color", "pan", "fx") and pad.mode() == "color"
    assert window.strips[2].pad.modes() == ("pan",)   # Appels : entrée virtuelle
    assert window.strips[1].vu.width() == 2 * VU_BAR_WIDTH + VU_GAP

    area = pad._area()
    drag(pad, (area.center().x(), area.bottom() - 2), (area.right() - 1, area.top() + 1))
    pump(qapp, 0.05)
    assert vm._strips[1].pad("color") == (0.5, 1.0)          # coin haut droit
    send(pad, QEvent.Type.MouseButtonDblClick, (area.center().x(), area.center().y()))
    pump(qapp, 0.05)
    assert vm._strips[1].pad("color") == (0.0, 0.0)          # double-clic : neutre

    pad.set_mode("pan")                            # (menu du mode)
    click(pad, (area.left() + 1, area.center().y()))
    pump(qapp, 0.05)
    assert vm._strips[1].pad("pan")[0] == -0.5 and vm._strips[1].pad("color") == (0.0, 0.0)
    assert window._cfg["ui"]["pad_modes"][1] == "pan"
    vm.set_pad(1, "pan", 0.25, 0.5)                # valeur venant de Voicemeeter
    pump(qapp, 0.05)
    assert pad.value() == (0.25, 0.5)


def test_theme_switch_from_settings(qapp, mixer):
    from ui import styles
    from ui.settings_dialog import SettingsDialog

    window, vm, reaper = mixer
    dialog = SettingsDialog(window._cfg, None, reaper, vm, window, persist=False)
    dialog.appearanceChanged.connect(window.apply_appearance)
    try:
        dialog.theme_swatches["orange"].click()
        assert window._cfg["ui"]["theme"] == "orange" and styles.current_theme() == "orange"
        assert styles.GREEN_NEON == styles.THEMES["orange"][1]
        assert styles.THEMES["orange"][1] in qapp.styleSheet()
        dialog.set_theme("#00aaff")                # couleur personnalisée
        assert styles.current_theme() == "#00aaff" and not dialog.theme_swatches["orange"].isChecked()
        assert "personnalisée" in dialog.theme_label.text()
    finally:
        dialog.set_theme("studio")
        dialog.done(0)
    assert styles.GREEN_NEON == "#3b82f6"


def test_tray_double_click_and_restore_maximized(qapp, mixer):
    from PySide6.QtWidgets import QSystemTrayIcon

    class FakeTray:
        def showMessage(self, *args):  # noqa: N802 (API Qt)
            pass

        def hide(self):
            pass

    window, _, _ = mixer
    window._tray = FakeTray()
    window._cfg["ui"]["minimize_to_tray"] = True
    reason = QSystemTrayIcon.ActivationReason
    window.showMaximized()
    pump(qapp, 0.05)
    window.minimize()
    pump(qapp, 0.05)
    assert not window.isVisible()
    window._on_tray_activated(reason.Trigger)        # double-clic Windows : Trigger…
    window._on_tray_activated(reason.DoubleClick)    # … puis DoubleClick
    pump(qapp, 0.05)
    assert window.isVisible() and window.isMaximized()

    window.showMinimized()                           # bouton « _ » de la barre de titre
    pump(qapp, 0.1)
    assert not window.isVisible()                    # → zone de notification
    window.show_from_tray()
    pump(qapp, 0.05)
    assert window.isVisible() and window.isMaximized() and not window.isMinimized()

    window._cfg["ui"]["minimize_to_tray"] = False    # sans zone de notification
    window._on_tray_activated(reason.Trigger)
    window._on_tray_activated(reason.DoubleClick)
    pump(qapp, 0.05)
    assert window.isVisible() and window.isMaximized() and not window.isMinimized()
    window.showNormal()


def test_restart_failure_is_reported(qapp, monkeypatch):
    from ui import main_window

    monkeypatch.setattr(main_window, "restart_command",
                        lambda: ("/chemin/introuvable/REKA.exe", ["--restart"]))
    assert main_window.launch_restart() is False


def test_pad_drag_stops_when_button_released_elsewhere(qapp, mixer):
    window, vm, _ = mixer
    pad = window.strips[1].pad
    area = pad._area()
    moves = []
    pad.moved.connect(lambda *args: moves.append(args))
    send(pad, QEvent.Type.MouseButtonPress, (area.center().x(), area.center().y()))
    assert pad.isInteracting()
    moves.clear()
    # Relâchement jamais reçu (menu ouvert pendant le glisser) : survol sans bouton
    send(pad, QEvent.Type.MouseMove, (area.left() + 2, area.top() + 2), NONE, NONE)
    send(pad, QEvent.Type.MouseMove, (area.right() - 2, area.top() + 2), NONE, NONE)
    assert moves == [] and not pad.isInteracting()


def _request_show_in_subprocess(qapp) -> str:
    """Lance request_show() dans un autre processus (comme un 2e REKA.exe) en
    laissant tourner la boucle Qt d'ici, qui doit lui répondre."""
    import os
    import subprocess
    import sys
    import time
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    # Comme un vrai lancement : l'application Qt existe avant la demande
    code = ("from PySide6.QtWidgets import QApplication; app = QApplication([]); "
            "import main; print(main.request_show())")
    proc = subprocess.Popen([sys.executable, "-c", code],
                            cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"}, text=True)
    deadline = time.monotonic() + 30
    while proc.poll() is None and time.monotonic() < deadline:
        pump(qapp, 0.05)
    out, _ = proc.communicate(timeout=10)
    print(out)                                       # visible si le test échoue
    return out.strip().splitlines()[-1] if out.strip() else ""


def test_second_launch_shows_window(qapp):
    import main

    answers = [True, False]                          # ouverte, puis en train de se fermer
    shown = []

    def on_show():
        shown.append(True)
        return answers.pop(0)

    server = main.listen_for_show_requests(on_show)
    try:
        assert _request_show_in_subprocess(qapp) == "True" and shown == [True]
        assert _request_show_in_subprocess(qapp) == "False"   # « closing » : on attendra
    finally:
        server.close()
    assert _request_show_in_subprocess(qapp) == "False"       # plus personne n'écoute


def test_banana_keeps_potato_sends_and_blank_project(qapp, cfg):
    from core.demo import DemoProcesses, DemoReaper, DemoVoicemeeter
    from ui.main_window import MainWindow, running_config

    cfg["state"] = {"strips": [{"gain": 0, "sends": [-1.0, -2.0, -3.0, -4.0]}]}
    cfg["voicemeeter"]["kind"] = "banana"
    vm, reaper = DemoVoicemeeter(cfg), DemoReaper(cfg)
    window = MainWindow(cfg=cfg, vm=vm, reaper=reaper, processes=DemoProcesses(), persist=False)
    window.show()
    pump(qapp, 0.1)
    cfg["voicemeeter"]["kind"] = "potato"             # choisi pour le prochain démarrage
    assert running_config(cfg, vm)["voicemeeter"]["kind"] == "banana"
    assert running_config(cfg, vm)["reaper"] is cfg["reaper"]
    window.close()
    assert cfg["state"]["strips"][0]["sends"] == [-1.0, -2.0, -3.0, -4.0]


def test_state_saved_on_close(qapp, mixer):
    window, vm, _ = mixer
    vm.set_route(1, 2, True)
    pump(qapp, 0.05)
    window.close()
    assert window._cfg["state"]["strips"][1]["routes"][2] is True


# ------------------------------------------------- volume par application
def test_apps_button_and_mixer(qapp, mixer):
    window, _, _ = mixer
    with_apps = [strip.index for strip in window.strips if strip.apps is not None]
    assert with_apps == [2, 3, 5]                        # Appels, Bureau, Autres 2 (virtuelles)
    window.refresh_app_counts()
    assert [window.strips[i].apps.count() for i in with_apps] == [2, 2, 0]

    QTest.mouseClick(window.strips[2].apps, LEFT)
    pump(qapp, 0.3)
    popup = window._app_mixer
    assert popup is not None and popup.isVisible() and list(popup.rows) == ["discord.exe", "chrome.exe"]
    row = popup.rows["discord.exe"]
    row.slider.setValue(40)                              # comme un glisser
    assert window._apps.apps(0)[0].volume == 0.4 and row.percent.text() == "40 %"
    QTest.mouseClick(row.mute, LEFT)
    pump(qapp, 0.3)
    assert window._apps.apps(0)[0].muted and row.mute.isChecked() and row.peak.level() == 0.0
    popup.close()
    pump(qapp, 0.1)
    assert window._app_mixer is None

    window.open_app_mixer(5)                             # Autres 2 : aucune application
    pump(qapp, 0.2)
    assert not window._app_mixer.rows and window._app_mixer.empty.isVisible()
    window._app_mixer.close()
    pump(qapp, 0.1)


def test_no_apps_button_without_backend(qapp, cfg):
    from core.demo import DemoProcesses, DemoReaper, DemoVoicemeeter
    from ui.main_window import MainWindow

    window = MainWindow(cfg=cfg, vm=DemoVoicemeeter(cfg), reaper=DemoReaper(cfg),
                        processes=DemoProcesses(), persist=False)
    assert all(strip.apps is None for strip in window.strips)
    window.close()


# ------------------------------------------------------------ mises à jour
def _fake_updater():
    from PySide6.QtCore import QObject, Signal

    from core.updater import BuildInfo, UpdateInfo

    class Updater(QObject):
        available = Signal(object)
        up_to_date = Signal()
        failed = Signal(str)

        def __init__(self):
            super().__init__()
            self.build = BuildInfo("0.5.0", 5, "c", "o/r", "build-x")
            self.latest = None
            self.enabled = True
            self.checks = []

        def check(self, manual=False):
            self.checks.append(manual)
            return True

    update = UpdateInfo("0.6.0", 7, "c", "https://example.invalid/REKA.exe", "0" * 64, 10,
                        "Notes", "")
    return Updater(), update


@pytest.fixture
def updating(qapp, cfg):
    from core.app_volume import DemoAppVolumes
    from core.demo import DemoProcesses, DemoReaper, DemoVoicemeeter
    from ui.main_window import MainWindow

    updater, update = _fake_updater()
    window = MainWindow(cfg=cfg, vm=DemoVoicemeeter(cfg), reaper=DemoReaper(cfg),
                        processes=DemoProcesses(), persist=False, apps=DemoAppVolumes(),
                        updater=updater)
    window.show()
    pump(qapp, 0.1)
    yield window, updater, update
    window.close()


def test_update_banner_and_skip(qapp, updating):
    window, updater, update = updating
    assert window.master.update_button.isHidden()
    updater.latest = update
    updater.available.emit(update)
    pump(qapp, 0.05)
    assert not window.master.update_button.isHidden()
    assert "0.6.0" in window.master.update_button.text()

    shown = []

    def skip():
        dialog = QApplication.activeModalWidget()
        shown.append(dialog.update_info.describe())
        dialog.skip_button.click()

    QTimer.singleShot(150, skip)
    QTest.mouseClick(window.master.update_button, LEFT)
    pump(qapp, 0.3)
    assert shown == ["0.6.0 (build 7)"]
    assert window._cfg["update"]["skipped_build"] == 7 and window.master.update_button.isHidden()
    updater.available.emit(update)                       # vérification automatique suivante
    pump(qapp, 0.05)
    assert window.master.update_button.isHidden()        # version ignorée : plus de pill


def test_update_installed_directly_in_background(qapp, updating, monkeypatch):
    """Mise à jour directe en cours de route : installée sans rien demander, la
    pill propose ensuite de redémarrer."""
    from pathlib import Path

    from PySide6.QtCore import QObject, Signal

    import ui.main_window as main_window

    window, updater, update = updating
    installed, asked = [], []

    class Downloader(QObject):
        finished = Signal(object)
        failed = Signal(str)

        def __init__(self, info):
            super().__init__()

        def start(self):
            self.finished.emit(Path(f"REKA-{update.build}.exe"))

    monkeypatch.setattr(main_window, "UpdateDownloader", Downloader)
    monkeypatch.setattr(main_window, "can_self_update", lambda: True)
    monkeypatch.setattr(main_window, "install", lambda path, current: installed.append(path.name))
    monkeypatch.setattr(window, "_persist", True)
    updater.available.emit(update)
    pump(qapp, 0.05)
    assert installed == ["REKA-7.exe"] and window._installed is update
    assert not window.master.update_button.isHidden()
    updater.available.emit(update)                       # même version : rien à refaire
    pump(qapp, 0.05)
    assert installed == ["REKA-7.exe"]

    monkeypatch.setattr(QMessageBox, "question",
                        lambda *args: asked.append(args[2]) or QMessageBox.StandardButton.No)
    QTest.mouseClick(window.master.update_button, LEFT)
    assert asked and "0.6.0" in asked[0]

    # Option désactivée : la pill demande, comme avant
    window._installed = window._update = None
    window._cfg["update"]["auto_install"] = False
    updater.available.emit(update)
    pump(qapp, 0.05)
    assert installed == ["REKA-7.exe"] and window._update is update


def test_about_tab_update_check(qapp, updating):
    from ui.settings_dialog import SettingsDialog

    window, updater, update = updating
    dialog = SettingsDialog(window._cfg, None, None, window._vm, window, persist=False,
                            updater=updater)
    titles = [label.text() for label in dialog.findChildren(QLabel, "dialogTitle")]
    assert "REKA 0.5.0 (build 5)" in titles
    assert dialog.check_update.isEnabled() and dialog.install_update.isHidden()
    dialog.check_update.click()
    assert updater.checks == [True] and not dialog.check_update.isEnabled()
    updater.up_to_date.emit()
    assert dialog.update_status.text() == "REKA est à jour." and dialog.check_update.isEnabled()
    updater.failed.emit("hors ligne")
    assert "hors ligne" in dialog.update_status.text()
    updater.available.emit(update)
    assert not dialog.install_update.isHidden() and "0.6.0" in dialog.update_status.text()
    dialog.auto_update.setChecked(False)
    assert window._cfg["update"]["check_on_start"] is False
    dialog.done(0)


def test_install_from_settings_opens_update_dialog(qapp, updating):
    window, updater, update = updating
    updater.latest = update                              # trouvée par une recherche manuelle
    seen = []

    def in_settings():
        settings = QApplication.activeModalWidget()
        settings.tabs.setCurrentIndex(3)
        settings._on_update_available(update)
        QTimer.singleShot(150, in_update)
        settings.install_update.click()

    def in_update():
        dialog = QApplication.activeModalWidget()
        seen.append(type(dialog).__name__)
        dialog.reject()

    QTimer.singleShot(150, in_settings)
    window.open_settings()
    pump(qapp, 0.3)
    assert seen == ["UpdateDialog"]


def test_font_widget_styles_and_rename_from_settings(qapp, mixer):
    from ui import styles
    from ui.settings_dialog import SettingsDialog

    window, vm, reaper = mixer
    dialog = SettingsDialog(window._cfg, None, reaper, vm, window, persist=False)
    dialog.appearanceChanged.connect(window.apply_appearance)
    dialog.stripRenamed.connect(window.rename_strip)
    try:
        dialog.fader_choice.setCurrentIndex(dialog.fader_choice.findData("vintage"))
        dialog.vu_choice.setCurrentIndex(dialog.vu_choice.findData("continu"))
        assert styles.FADER_STYLE == "vintage" and styles.VU_STYLE == "continu"
        assert window._cfg["ui"]["fader_style"] == "vintage"
        dialog.font_choice.setCurrentText("Segoe UI")
        dialog.font_choice.currentFontChanged.emit(dialog.font_choice.currentFont())
        assert styles.FONT_FAMILY == window._cfg["ui"]["font_family"]
        assert styles.FONT_FAMILY in qapp.styleSheet()
        window.grab()                               # rendu avec les nouveaux styles
        dialog.name_edits[0].setText("Voix")
        dialog.name_edits[0].editingFinished.emit()
        assert window.strips[0].name.text() == "Voix"
        assert window._cfg["ui"]["strip_names"][0] == "Voix"
    finally:
        for key in ("font_family", "fader_style", "vu_style"):
            window._cfg["ui"].pop(key, None)
        window.apply_appearance()
        dialog.done(0)
    assert styles.FONT_FAMILY == styles.DEFAULT_FONT and styles.FADER_STYLE == "console"


def test_gains_follow_voicemeeter_range_above_0db(qapp, mixer):
    """Un gain réglé dans Voicemeeter (jusqu'à +12 dB) s'affiche tel quel, et la
    molette part de cette valeur au lieu de la ramener à 0 dB."""
    from dataclasses import replace

    from core.voicemeeter_ctrl import MixerParams

    window, vm, _ = mixer
    strip = window.strips[0]
    strips = list(vm._strips)
    strips[0] = replace(strips[0], gain=6.0)
    window._on_params(MixerParams(tuple(strips), 9.5))
    pump(qapp, 0.05)
    assert strip.value_label.text().startswith("6.0") and strip.fader.value() == 6.0
    assert strip.fader._y_for(6.0) < strip.fader._y_for(0.0)       # au-dessus de 0 dB
    assert window.master.value_label.text() == "9.5 dB"
    event = QWheelEvent(QPointF(20, 20), strip.fader.mapToGlobal(QPointF(20, 20)), QPoint(0, 0),
                        QPoint(0, 120), NONE, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(strip.fader, event)
    pump(qapp, 0.05)
    assert vm._strips[0].gain == 6.5


def test_dialogs_do_not_pile_up(qapp, mixer):
    """Chaque ouverture du dialogue d'ajout d'effet (ou de projet) est libérée."""
    from ui.project_dialog import ProjectDialog
    from ui.vst_dialog import VstDialog

    window, _vm, _reaper = mixer
    for _ in range(3):
        QTimer.singleShot(50, lambda: QApplication.activeModalWidget().reject())
        window.add_effect(1)
        QTimer.singleShot(50, lambda: QApplication.activeModalWidget().reject())
        window.choose_project()
    pump(qapp, 0.05)
    assert not window.findChildren(VstDialog) and not window.findChildren(ProjectDialog)


def wheel(widget, delta, modifiers=Qt.KeyboardModifier.NoModifier):
    pos = QPointF(widget.width() / 2, widget.height() / 2)
    event = QWheelEvent(pos, widget.mapToGlobal(pos), QPoint(0, 0), QPoint(0, delta), NONE,
                        modifiers, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)


def test_knobs_move_by_half_db(qapp, mixer):
    """Knobs d'envoi et du casque : un cran de molette = 0,5 dB, glisser = pas de 0,5 dB,
    y compris avec une molette « haute résolution » (crans fractionnés)."""
    window, vm, _ = mixer
    knob = window.strips[1].knobs[0]
    assert vm._strips[1].sends[0] == 0.0
    wheel(knob, -120)
    assert vm._strips[1].sends[0] == -0.5
    for _ in range(15):                    # 15 × 8 = un cran entier
        wheel(knob, -8)
    assert vm._strips[1].sends[0] == -1.0
    wheel(knob, -240)
    assert vm._strips[1].sends[0] == -2.0

    center = (knob.width() / 2, knob.height() / 2)
    for dy in (13, 37, 61):
        drag(knob, center, (center[0], center[1] + dy))
        value = vm._strips[1].sends[0]
        assert value * 2 == round(value * 2), value          # multiple de 0,5 dB
    assert vm._strips[1].sends[0] < -2.0

    master = window.master.knob
    wheel(master, 120)
    assert vm._headphone == 0.5
    wheel(master, -360)
    assert vm._headphone == -1.0
    assert window.master.value_label.text() == "-1.0 dB"


def test_wheel_not_undone_by_stale_voicemeeter_values(qapp, mixer):
    """Les valeurs relues dans Voicemeeter avant le réglage (encore en route vers
    l'UI) ne ramènent plus le knob ou le fader en arrière pendant la molette."""
    from dataclasses import replace

    from core.voicemeeter_ctrl import MixerParams

    window, vm, _ = mixer
    strip = window.strips[2]
    stale = MixerParams(tuple(vm._strips), vm._headphone)
    knob = strip.knobs[2]                  # Discord
    start = vm._strips[2].sends[2]
    for step in range(1, 5):
        wheel(knob, 120)
        window._on_params(stale)           # relecture antérieure au réglage
        assert knob.value() == start + 0.5 * step
    assert vm._strips[2].sends[2] == start + 2.0

    fader = strip.fader
    for step in range(1, 4):
        wheel(fader, 120)
        window._on_params(stale)
    assert vm._strips[2].gain == 1.5 and fader.value() == 1.5

    # Plus tard, Voicemeeter reprend la main (réglage fait dans Voicemeeter)
    knob._wheel_until = fader._wheel_until = 0.0
    strips = list(vm._strips)
    strips[2] = replace(strips[2], gain=-3.0, sends=(0.0, 0.0, -6.0, 0.0))
    window._on_params(MixerParams(tuple(strips), 0.0))
    assert fader.value() == -3.0 and knob.value() == -6.0


def test_engine_buttons_show_and_hide_windows(qapp, mixer):
    """Boutons Voicemeeter / REAPER du Master : clic = fenêtre affichée, re-clic =
    cachée ; les réglages ne recachent pas une fenêtre affichée par le Master."""
    from ui.settings_dialog import SettingsDialog

    window, vm, reaper = mixer
    processes = window._processes
    buttons = window.master.engines
    assert list(buttons) == ["voicemeeter", "reaper"]
    QTest.mouseClick(buttons["voicemeeter"], LEFT)
    assert processes.is_window_visible("voicemeeter") and buttons["voicemeeter"].is_shown()
    assert not buttons["reaper"].is_shown()
    assert "cacher" in buttons["voicemeeter"].toolTip()

    dialog = SettingsDialog(window._cfg, processes, reaper, vm, window, persist=False)
    assert dialog.show_vm.isChecked() and not dialog.show_reaper.isChecked()
    dialog.show_reaper.setChecked(True)            # affichée le temps des réglages
    assert processes.is_window_visible("reaper")
    dialog.done(0)
    window.refresh_engine_buttons()
    assert processes.is_window_visible("voicemeeter") and not processes.is_window_visible("reaper")
    assert not buttons["reaper"].is_shown()

    QTest.mouseClick(buttons["voicemeeter"], LEFT)
    assert not processes.is_window_visible("voicemeeter") and not buttons["voicemeeter"].is_shown()
    QTest.mouseClick(buttons["reaper"], LEFT)
    assert processes.is_window_visible("reaper") and buttons["reaper"].is_shown()
    assert buttons["voicemeeter"].width() > buttons["reaper"].width() > 40


def test_no_reaper_button_with_other_hosts(qapp, cfg):
    import main
    from core.demo import DemoProcesses, DemoVoicemeeter
    from ui.main_window import MainWindow

    session = dict(cfg)
    session["effects"] = {"host": "none"}
    processes = DemoProcesses()
    window = MainWindow(cfg=session, vm=DemoVoicemeeter(session),
                        reaper=main.demo_host(session, "none", processes),
                        processes=processes, persist=False)
    try:
        assert list(window.master.engines) == ["voicemeeter"]
    finally:
        window.close()


def test_element_colors_per_console_and_channel(qapp, mixer):
    """Couleur de chaque élément : pour toute la console, puis un canal en particulier
    (prioritaire), appliquée tout de suite ; « Défaut » et « Tout réinitialiser » reviennent au thème."""
    from PySide6.QtGui import QColor

    from ui import styles
    from ui.settings_dialog import SettingsDialog

    window, vm, reaper = mixer
    dialog = SettingsDialog(window._cfg, window._processes, reaper, vm, window, persist=False)
    dialog.appearanceChanged.connect(window.apply_appearance)
    try:
        tabs = [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]
        assert "Couleurs" in tabs
        assert "send2" in dialog.color_buttons and "headphone" in dialog.color_buttons
        assert dialog.color_labels["send2"] == "Knob Discord"

        dialog.set_element_color("mute", None, "#00ff00")
        dialog.set_element_color("send2", None, "#ff00ff")
        assert window._cfg["ui"]["colors"] == {"mute": "#00ff00", "send2": "#ff00ff"}
        assert all("#00ff00" in strip.mute.styleSheet() for strip in window.strips)
        assert styles.accent("send2", 0) == "#ff00ff" and styles.accent("send1", 0) == styles.GREEN_NEON
        assert dialog.color_buttons["mute"].custom and dialog.color_resets["mute"].isEnabled()

        # Canal 2 seulement : prioritaire sur toute la console
        dialog.color_scope_choice.setCurrentIndex(dialog.color_scope_choice.findData(1))
        assert "headphone" not in dialog.color_buttons
        assert dialog.color_buttons["mute"].color == "#00ff00" and not dialog.color_buttons["mute"].custom
        dialog.set_element_color("mute", 1, "#123456")
        dialog.set_element_color("panel", 1, "#402000")
        dialog.set_element_color("name", 1, "#ffcc00")
        assert "#123456" in window.strips[1].mute.styleSheet()
        assert "#00ff00" in window.strips[0].mute.styleSheet()
        assert "64, 32, 0" in window.strips[1].styleSheet() and not window.strips[0].styleSheet()
        assert "#ffcc00" in window.strips[1].name._label.styleSheet()
        dialog.set_element_color("vu", styles.MASTER_SCOPE, "#ff8800")
        assert styles.accent("vu", styles.MASTER_SCOPE) == "#ff8800" and styles.accent("vu", 1) == styles.GREEN_NEON
        window.grab()                                    # rendu avec les couleurs choisies
        window.strips[1].mute.setChecked(True)
        image = window.strips[1].mute.grab().toImage()
        assert image.pixelColor(image.width() // 2, 3) == QColor("#123456")

        dialog.color_resets["mute"].click()               # « Défaut » : comme toute la console
        assert "#00ff00" in window.strips[1].mute.styleSheet()
        dialog.reset_element_colors()
        assert window._cfg["ui"]["colors"] == {}
        assert not window.strips[1].mute.styleSheet() and not window.strips[1].styleSheet()
        assert styles.accent("send2", 0) == styles.GREEN_NEON
    finally:
        window._cfg["ui"]["colors"] = {}
        window.apply_appearance()
        dialog.done(0)


def test_media_player_in_master(qapp, mixer):
    """Musique en cours (démo) : pochette, titre, précédent / pause / suivant, option
    pour le cacher ; caché quand les contrôles média de Windows manquent."""
    window, _vm, _reaper = mixer
    media = window.master.media
    backend = window._media
    assert media.isVisible() and media.cover.has_cover()
    assert media.title.text() == "Néons de minuit" and media.artist.text() == "Les Ondes"
    assert media.play.is_playing() and "Spotify" in media.toolTip()

    QTest.mouseClick(media.next, LEFT)
    assert media.title.text() == "Ligne de basse" and backend.current.app == "Deezer"
    QTest.mouseClick(media.play, LEFT)
    assert not media.play.is_playing() and not backend.current.playing
    QTest.mouseClick(media.previous, LEFT)
    assert media.title.text() == "Néons de minuit"
    window.grab()

    window._cfg["ui"]["show_media"] = False
    window.apply_appearance()
    assert media.isHidden()
    window._cfg["ui"]["show_media"] = True
    window.apply_appearance()
    assert media.isVisible()

    backend._publish(None)                  # plus rien ne joue
    assert media.title.text() == "Rien en lecture" and not media.play.isEnabled()
    assert not media.cover.has_cover()


def test_media_player_hidden_without_backend(qapp, cfg):
    from core.demo import DemoProcesses, DemoReaper, DemoVoicemeeter
    from ui.main_window import MainWindow

    window = MainWindow(cfg=cfg, vm=DemoVoicemeeter(cfg), reaper=DemoReaper(cfg),
                        processes=DemoProcesses(), persist=False)
    try:
        window.show()
        assert window.master.media.isHidden()
    finally:
        window.close()
