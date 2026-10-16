"""Interface complète en mode démo : clics, glisser, renommage, effets."""

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

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


@pytest.fixture
def mixer(qapp, cfg):
    from core.demo import DemoProcesses, DemoReaper, DemoVoicemeeter
    from ui.main_window import MainWindow

    vm, reaper = DemoVoicemeeter(cfg), DemoReaper(cfg)
    window = MainWindow(cfg=cfg, vm=vm, reaper=reaper, processes=DemoProcesses(), persist=False)
    window.resize(1920, 1040)
    window.show()
    pump(qapp, 0.2)
    yield window, vm, reaper
    window.close()


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
    assert strip.value_label.text().startswith(f"{vm._strips[1].gain:.2f}")
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
    assert window.master.value_label.text() == f"{vm._headphone:.2f} dB"


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
        "Général", "Audio", "Apparence", "À propos"]
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
        dialog.set_theme("neon")
        dialog.done(0)
    assert styles.GREEN_NEON == "#00ff88"


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
    window.showNormal()
    window.show_from_tray()


def test_second_launch_shows_window(qapp):
    import main

    shown = []
    server = main.listen_for_show_requests(lambda: shown.append(True))
    try:
        assert main.request_show()
        pump(qapp, 0.3)
        assert shown
    finally:
        server.close()
    assert not main.request_show()                   # plus personne n'écoute


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
