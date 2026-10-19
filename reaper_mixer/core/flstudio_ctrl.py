"""
Moteur d'effets FL Studio.

FL Studio n'a pas d'API de pilotage externe comme le pont Lua de REAPER : ses
effets se règlent dans FL Studio lui-même. REKA s'occupe du reste :

  - il lance FL Studio au démarrage (masqué, comme REAPER), sur le projet
    choisi (flstudio.project), et le ferme en partant ;
  - « + » / « Ouvrir FL Studio » ramène sa fenêtre au premier plan ;
  - le badge FX d'une strip fait passer son son par l'insert de Voicemeeter
    (donc par FL Studio) ou non. Activé strip par strip : tant que le mixer de
    FL Studio n'est pas câblé, une strip envoyée à l'insert serait muette.

Câblage à faire une fois dans FL Studio (voir le tutoriel des réglages) :
Options → Audio settings → « Voicemeeter Insert Virtual ASIO », puis, pour
chaque piste du mixer : entrée = canaux d'insert de la strip, sortie = les
mêmes canaux.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QTimer

from core.effects import HOST_FLSTUDIO, EffectsHost
from core.reaper_ctrl import TrackInfo
from core.voicemeeter_ctrl import edition, strip_channels

log = logging.getLogger(__name__)

PROCESS_CHECK_MS = 1000


def insert_pair(channel: int) -> str:
    """Canaux d'insert d'une strip, numérotés comme dans FL Studio (« 1-2 »)."""
    return f"{channel + 1}-{channel + 2}"


class FlStudioController(EffectsHost):
    kind = HOST_FLSTUDIO
    process_key = "flstudio"
    add_label = "+ Ouvrir FL Studio"
    empty_hint = "Effets dans FL Studio"

    def __init__(self, cfg: dict, processes=None, parent: QObject | None = None) -> None:
        super().__init__(cfg, parent)
        self._processes = processes
        self._channels = strip_channels(cfg)
        self._connected = False
        self._timer = QTimer(self)
        self._timer.setInterval(PROCESS_CHECK_MS)
        self._timer.timeout.connect(self._check)

    # ------------------------------------------------------------ état
    @property
    def is_connected(self) -> bool:
        return self._connected

    def _inserts(self) -> list[bool]:
        flags = list(self._cfg.setdefault("flstudio", {}).get("inserts") or [])
        return [bool(f) for f in flags] + [False] * (len(self._channels) - len(flags))

    @property
    def strip_tracks(self) -> list[TrackInfo | None]:
        names = self._cfg.get("ui", {}).get("strip_names", [])
        inserts = self._inserts()
        return [TrackInfo(names[i] if i < len(names) else f"Strip {i + 1}", inserts[i], (),
                          index=i, rec_input=channel)
                for i, channel in enumerate(self._channels)]

    def insert_strips(self, tracks: list, connected: bool) -> set[int]:
        if not connected:
            return set()
        return {i for i, track in enumerate(tracks) if track is not None and track.fx_enabled}

    def status(self, connected: bool) -> tuple[bool, str, str]:
        if connected:
            return True, "FL Studio", "FL Studio tourne. Clic sur « + » d'une strip pour l'afficher."
        return False, "FL Studio fermé", "FL Studio ne tourne plus : le son passe sans effets."

    def latency_report(self) -> str:
        return ("Insert Voicemeeter : 2 tampons ASIO (taille réglée dans FL Studio : Options → "
                "Audio settings → Buffer length), plus la latence des plugins.")

    def wiring_help(self) -> str:
        """Câblage du mixer de FL Studio, strip par strip."""
        names = self._cfg.get("ui", {}).get("strip_names", [])
        lines = [f"« {names[i] if i < len(names) else f'Strip {i + 1}'} » : entrée et sortie "
                 f"{insert_pair(channel)}" for i, channel in enumerate(self._channels)]
        return (f"Dans FL Studio ({edition(self._cfg).label.split(' (')[0]}) : Options → Audio "
                "settings → « Voicemeeter Insert Virtual ASIO ». Puis une piste du mixer par strip :\n"
                + "\n".join(lines))

    # ------------------------------------------------------------ cycle de vie
    def start(self) -> None:
        self._check()
        self._timer.start()
        self.tracks_changed.emit(self.strip_tracks)

    def close(self, save_project: bool = True) -> None:
        self._timer.stop()

    # ------------------------------------------------------------ commandes
    def set_chain_enabled(self, strip: int, enabled: bool) -> None:
        inserts = self._inserts()
        if 0 <= strip < len(inserts):
            inserts[strip] = enabled
            self._cfg.setdefault("flstudio", {})["inserts"] = inserts
            log.info("FL Studio : strip %d %s", strip, "envoyée à l'insert" if enabled else "sans effets")
            self.tracks_changed.emit(self.strip_tracks)

    def add_fx(self, strip: int, fx_name: str) -> None:
        self.show_app()

    def toggle_fx_ui(self, strip: int, fx_index: int) -> None:
        self.show_app()

    def show_app(self) -> None:
        if self._processes is None or not self._processes.bring_to_front("flstudio"):
            self.command_failed.emit("FL Studio ne tourne pas : relance REKA pour le démarrer.")

    def _check(self) -> None:
        # Fenêtre principale exigée : un FL Studio bloqué sur une erreur au lancement
        # ne traite pas le son, l'insert rendrait les strips muettes
        processes = self._processes
        ready = getattr(processes, "is_ready", None) or getattr(processes, "is_running", None)
        alive = bool(ready is not None and ready("flstudio"))
        if alive != self._connected:
            self._connected = alive
            log.info("FL Studio %s.", "lancé" if alive else "fermé")
            self.connection_changed.emit(alive)
