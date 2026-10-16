"""
Moteurs simulés pour le mode démo (`--demo`) et l'auto-test (`--selftest`).

Mêmes signaux et méthodes que VoicemeeterController / ReaperController /
ProcessManager : l'interface tourne sans Voicemeeter ni REAPER, avec des
niveaux animés. Pratique pour découvrir la console ou vérifier un build.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from core.reaper_ctrl import AudioDevice, FxInfo, TrackInfo
from core.voicemeeter_ctrl import (DEFAULT_ROUTING, SILENCE_DB, MixerLevels, MixerParams,
                                   StripParams)

DEMO_FX = {
    0: ["VST3: Pro-G (FabFilter)", "VST3: Pro-Q 4 (FabFilter)", "VST3: Pro-C 3 (FabFilter)",
        "VST3: Pro-MB (FabFilter)", "VST3: Pro-MB (FabFilter)", "VST: ReaLimit (Cockos)"],
}
DEMO_PLUGINS = [
    ("VST3: Pro-Q 4 (FabFilter)", ""), ("VST3: Pro-C 3 (FabFilter)", ""),
    ("VST3: Pro-G (FabFilter)", ""), ("VST3: Pro-MB (FabFilter)", ""),
    ("VST3: Pro-L 2 (FabFilter)", ""), ("VST: ReaEQ (Cockos)", ""), ("VST: ReaComp (Cockos)", ""),
    ("VST: ReaLimit (Cockos)", ""), ("VST: ReaGate (Cockos)", ""), ("JS: Volume Adjustment", ""),
]


class DemoVoicemeeter(QObject):
    params_changed = Signal(object)
    levels_changed = Signal(object)
    connection_changed = Signal(bool)

    def __init__(self, cfg: dict, parent: QObject | None = None) -> None:
        super().__init__(parent)
        count = len(cfg.get("voicemeeter", {}).get("strip_map", [0] * 6))
        routes = len(cfg.get("voicemeeter", {}).get("routing", DEFAULT_ROUTING))
        # Comme la maquette : micro coupé avec tous les envois, le reste vers le casque
        self._strips = [
            StripParams(0.0, index == 0, tuple(index == 0 or r == 0 for r in range(routes)),
                        tuple(0.0 if index == 0 or r == 0 else -18.0 for r in range(routes)))
            for index in range(count)
        ]
        self._headphone = 0.0
        self._t0 = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

    @property
    def strip_count(self) -> int:
        return len(self._strips)

    def login(self) -> None:
        pass

    def start_polling(self) -> None:
        self._emit_params()
        self._timer.start()

    def release(self) -> None:
        self._timer.stop()

    def close(self, shutdown_app: bool = False) -> None:
        self._timer.stop()

    def set_gain(self, strip: int, db: float) -> None:
        self._update(strip, gain=db)

    def set_mute(self, strip: int, muted: bool) -> None:
        self._update(strip, mute=muted)

    def set_route(self, strip: int, route: int, enabled: bool) -> None:
        routes = list(self._strips[strip].routes)
        routes[route] = enabled
        self._update(strip, routes=tuple(routes))

    def set_send(self, strip: int, route: int, db: float) -> None:
        sends = list(self._strips[strip].sends)
        sends[route] = db
        self._update(strip, sends=tuple(sends))

    def set_label(self, strip: int, text: str) -> None:
        pass

    def set_headphone_gain(self, db: float) -> None:
        self._headphone = db
        self._emit_params()

    def set_inserts(self, enabled: bool) -> None:
        pass

    def apply_state(self, state: dict) -> None:
        pass

    def _update(self, strip: int, **changes) -> None:
        self._strips[strip] = replace(self._strips[strip], **changes)
        self._emit_params()

    def _emit_params(self) -> None:
        self.params_changed.emit(MixerParams(tuple(self._strips), self._headphone))

    def levels_at(self, t: float) -> MixerLevels:
        strips = []
        for index, strip in enumerate(self._strips):
            if strip.mute:
                strips.append((SILENCE_DB, SILENCE_DB))
                continue
            base = -18 + 9 * math.sin(t * (0.9 + index * 0.37) + index)
            strips.append((base + strip.gain + random.uniform(-3, 3),
                           base + strip.gain + random.uniform(-3, 3)))
        master = max(level for pair in strips for level in pair)
        return MixerLevels(tuple(strips), (master + self._headphone, master + self._headphone - 1))

    def _tick(self) -> None:
        self.levels_changed.emit(self.levels_at(time.monotonic() - self._t0))


class DemoReaper(QObject):
    connection_changed = Signal(bool)
    tracks_changed = Signal(object)
    device_changed = Signal(object)
    installed_fx_ready = Signal(object)
    command_failed = Signal(str)

    def __init__(self, cfg: dict, parent: QObject | None = None) -> None:
        super().__init__(parent)
        names = cfg.get("ui", {}).get("strip_names", [])
        count = len(cfg.get("voicemeeter", {}).get("strip_map", [0] * 6))
        self._tracks = [
            TrackInfo(names[i] if i < len(names) else f"Strip {i + 1}", i != 0,
                      tuple(FxInfo(n, True, False) for n in DEMO_FX.get(i, [])))
            for i in range(count)
        ]
        self.device = AudioDevice("ASIO", "Voicemeeter Insert Virtual ASIO",
                                  "Voicemeeter Insert Virtual ASIO", "48000", "256")
        self.installed_fx = list(DEMO_PLUGINS)
        self.is_connected = True
        self.version = "démo"
        QTimer.singleShot(0, self, self._emit)

    def resource_path(self, _exe) -> Path | None:
        return None

    def start(self) -> None:
        pass

    def ping(self, timeout_s: float) -> bool:
        return True

    def close(self, save_project: bool = True) -> None:
        pass

    def rename_track(self, strip: int, name: str) -> None:
        self._set(strip, name=name)

    def add_fx(self, strip: int, fx_name: str) -> None:
        self._set(strip, fx=self._tracks[strip].fx + (FxInfo(fx_name, True, False),))

    def remove_fx(self, strip: int, fx_index: int) -> None:
        fx = list(self._tracks[strip].fx)
        del fx[fx_index]
        self._set(strip, fx=tuple(fx))

    def set_fx_enabled(self, strip: int, fx_index: int, enabled: bool) -> None:
        fx = list(self._tracks[strip].fx)
        fx[fx_index] = replace(fx[fx_index], enabled=enabled)
        self._set(strip, fx=tuple(fx))

    def set_chain_enabled(self, strip: int, enabled: bool) -> None:
        self._set(strip, fx_enabled=enabled)

    def toggle_fx_ui(self, strip: int, fx_index: int) -> None:
        fx = list(self._tracks[strip].fx)
        fx[fx_index] = replace(fx[fx_index], open=not fx[fx_index].open)
        self._set(strip, fx=tuple(fx))

    def save_project(self) -> None:
        pass

    def open_preferences(self) -> None:
        self.command_failed.emit("Mode démo : pas de REAPER à configurer.")

    def request_installed_fx(self) -> None:
        QTimer.singleShot(0, self, lambda: self.installed_fx_ready.emit(list(DEMO_PLUGINS)))

    def _set(self, strip: int, **changes) -> None:
        self._tracks[strip] = replace(self._tracks[strip], **changes)
        self._emit()

    def _emit(self) -> None:
        self.tracks_changed.emit(list(self._tracks))


class DemoProcesses:
    def set_window_visible(self, key: str, visible: bool) -> None:
        pass

    def focus_window(self, key: str, title_contains: str) -> bool:
        return False

    def executable(self, key: str) -> None:
        return None

    def will_terminate(self, key: str) -> bool:
        return False

    def stop(self, key: str) -> None:
        pass

    def shutdown(self) -> None:
        pass
