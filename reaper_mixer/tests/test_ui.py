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


def test_state_saved_on_close(qapp, mixer):
    window, vm, _ = mixer
    vm.set_route(1, 2, True)
    pump(qapp, 0.05)
    window.close()
    assert window._cfg["state"]["strips"][1]["routes"][2] is True
