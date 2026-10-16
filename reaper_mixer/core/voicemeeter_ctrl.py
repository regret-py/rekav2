"""
Contrôle de Voicemeeter Potato via l'API Remote (voicemeeterlib).

- Un thread de scrutation (30 Hz par défaut) est le seul à parler à l'API :
  il envoie les écritures demandées par l'UI, relit les paramètres quand
  Voicemeeter signale un changement, et lit les niveaux à chaque tour.
- L'UI n'appelle que des méthodes non bloquantes (set_*) : elles déposent la
  valeur voulue, et seule la dernière valeur d'un paramètre est envoyée.
- Les résultats remontent par signaux Qt, livrés dans le thread UI.

Correspondance par défaut (config.json → voicemeeter.strip_map / routing) :
    strips UI 0..5 → strips VM 0, 1, 5, 6, 2, 7
    knobs Monitor / Mic Out / Discord / Obs → bus A1 / B1 / B2 / A2
"""

from __future__ import annotations

import contextlib
import logging
import math
import threading
import time
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

# Ordre des bus de Voicemeeter Potato (index utilisés par GainLayer[j])
BUS_NAMES = ("A1", "A2", "A3", "A4", "A5", "B1", "B2", "B3")
PHYSICAL_STRIPS = 5          # Potato : 5 entrées physiques (2 canaux) puis 3 virtuelles (8 canaux)
CHANNELS_PER_BUS = 8
LEVEL_POSTFADER = 1          # types de niveaux de VBVMR_GetLevel
LEVEL_OUTPUT = 3
SILENCE_DB = -100.0
GAIN_MIN_DB, GAIN_MAX_DB = -60.0, 12.0
ECHO_GUARD_S = 0.5           # après une écriture, on garde notre valeur le temps que VM l'applique
ERROR_RETRY_S = 1.0

DEFAULT_STRIP_MAP = (0, 1, 5, 6, 2, 7)
DEFAULT_ROUTING = (
    {"label": "Monitor", "bus": "A1"},
    {"label": "Mic Out", "bus": "B1"},
    {"label": "Discord", "bus": "B2"},
    {"label": "Obs", "bus": "A2"},
)


def input_channel(strip: int) -> int:
    """Premier canal d'une strip Voicemeeter dans les tableaux de niveaux et
    d'insert (Potato : 2 canaux par entrée physique, 8 par entrée virtuelle)."""
    if strip < PHYSICAL_STRIPS:
        return 2 * strip
    return 2 * PHYSICAL_STRIPS + 8 * (strip - PHYSICAL_STRIPS)


def amp_to_db(value: float) -> float:
    return 20.0 * math.log10(value) if value > 1e-5 else SILENCE_DB


def _clamp_gain(db: float) -> float:
    return round(max(GAIN_MIN_DB, min(GAIN_MAX_DB, float(db))), 2)


@dataclass(frozen=True)
class StripParams:
    gain: float
    mute: bool
    routes: tuple[bool, ...]   # un booléen par knob de routing
    sends: tuple[float, ...]   # niveau (dB) de chaque knob de routing


@dataclass(frozen=True)
class MixerParams:
    strips: tuple[StripParams, ...]
    headphone_gain: float

    def to_state(self) -> dict:
        """Forme sauvegardée dans config.json (section « state »)."""
        return {
            "strips": [
                {"gain": s.gain, "mute": s.mute, "routes": list(s.routes), "sends": list(s.sends)}
                for s in self.strips
            ],
            "headphone_db": self.headphone_gain,
        }


@dataclass(frozen=True)
class MixerLevels:
    strips: tuple[tuple[float, float], ...]  # (gauche, droite) en dB, post-fader
    master: tuple[float, float]              # sortie du bus casque


class VoicemeeterController(QObject):
    params_changed = Signal(object)      # MixerParams
    levels_changed = Signal(object)      # MixerLevels
    connection_changed = Signal(bool)

    def __init__(self, cfg: dict, parent: QObject | None = None) -> None:
        super().__init__(parent)
        section = cfg.get("voicemeeter", {})
        self._kind = section.get("kind", "potato")
        self._strips = tuple(int(i) for i in section.get("strip_map", DEFAULT_STRIP_MAP))
        self._route_buses = tuple(
            _bus_index(route["bus"]) for route in section.get("routing", DEFAULT_ROUTING))
        self._headphone_bus = _bus_index(section.get("headphone_bus", "A1"))
        self._interval = 1.0 / max(1.0, float(section.get("poll_hz", 30)))

        self._remote = None          # objet voicemeeterlib, créé au login
        self._logged_in = False
        self._lock = threading.Lock()
        self._writes: dict[str, float | str] = {}
        self._inserts_wanted: bool | None = None
        # Utilisés seulement par le thread de scrutation (puis par close())
        self._echo: dict[str, tuple[float, float | str]] = {}
        self._inserts_saved: dict[int, float] = {}
        self._last_params: MixerParams | None = None
        self._connected = False
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ------------------------------------------------------------- connexion
    def login(self) -> None:
        """Connexion à l'API Remote (bloquant). Idempotent.

        Lève une exception si Voicemeeter n'est pas encore prêt : main.py
        retente toutes les quelques secondes."""
        if self._logged_in:
            return
        if self._remote is None:
            try:
                import voicemeeterlib  # lit le registre et charge la DLL dès l'import
            except Exception as exc:
                raise ConnectionError(f"API Voicemeeter indisponible : {exc}") from exc
            # La lib trace chaque erreur avec la pile complète : on journalise
            # nous-mêmes, sobrement, pour ne pas inonder app.log à 30 Hz.
            logging.getLogger("voicemeeterlib").setLevel(logging.CRITICAL)
            self._remote = voicemeeterlib.api(self._kind, timeout=5)
        try:
            self._remote.login()
        except Exception:
            # Le login de la DLL a pu réussir avant l'échec : sans logout, le
            # prochain essai serait refusé (« déjà connecté »).
            with contextlib.suppress(Exception):
                self._remote.logout()
            raise
        self._logged_in = True
        log.info("Connecté à %s (version %s).", self._remote, self._remote.version)

    def start_polling(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        if not self._logged_in:
            raise RuntimeError("start_polling() avant login()")
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="voicemeeter-poll", daemon=True)
        self._thread.start()

    def release(self) -> None:
        """Arrête la scrutation, envoie les derniers réglages et rend les inserts
        dans leur état d'origine (avant la fermeture de REAPER : le son des
        strips ne doit pas rester aiguillé vers un REAPER éteint)."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if not self._logged_in:
            return
        try:
            self._flush()                 # derniers mouvements de l'utilisateur
            self._apply_inserts(False)
        except Exception as exc:
            log.warning("Voicemeeter : derniers réglages non envoyés : %s", exc)

    def close(self, shutdown_app: bool = False) -> None:
        """release(), puis, si demandé, extinction propre de Voicemeeter (il
        sauvegarde ses réglages), et déconnexion de l'API."""
        self.release()
        if not self._logged_in:
            return
        try:
            if shutdown_app:
                log.info("Arrêt de Voicemeeter demandé (Command.Shutdown).")
                self._remote.set("Command.Shutdown", 1.0)
        except Exception as exc:
            log.warning("Fermeture de la connexion Voicemeeter incomplète : %s", exc)
        finally:
            with contextlib.suppress(Exception):
                self._remote.logout()
            self._logged_in = False

    # ------------------------------------------------- commandes (thread UI)
    @property
    def strip_count(self) -> int:
        return len(self._strips)

    def set_gain(self, strip: int, db: float) -> None:
        self._queue(f"Strip[{self._strips[strip]}].Gain", _clamp_gain(db))

    def set_mute(self, strip: int, muted: bool) -> None:
        self._queue(f"Strip[{self._strips[strip]}].Mute", 1.0 if muted else 0.0)

    def set_route(self, strip: int, route: int, enabled: bool) -> None:
        bus = BUS_NAMES[self._route_buses[route]]
        self._queue(f"Strip[{self._strips[strip]}].{bus}", 1.0 if enabled else 0.0)

    def set_send(self, strip: int, route: int, db: float) -> None:
        layer = self._route_buses[route]
        self._queue(f"Strip[{self._strips[strip]}].GainLayer[{layer}]", _clamp_gain(db))

    def set_label(self, strip: int, text: str) -> None:
        self._queue(f"Strip[{self._strips[strip]}].Label", text[:30])

    def set_headphone_gain(self, db: float) -> None:
        self._queue(f"Bus[{self._headphone_bus}].Gain", _clamp_gain(db))

    def set_inserts(self, enabled: bool) -> None:
        """Active (ou rend) l'insert Voicemeeter des strips pilotées : leur son
        passe alors par REAPER, et donc par les effets."""
        with self._lock:
            self._inserts_wanted = enabled

    def apply_state(self, state: dict) -> None:
        """Ré-applique un état sauvegardé par MixerParams.to_state()."""
        for index, strip in enumerate(state.get("strips", [])[: self.strip_count]):
            if "gain" in strip:
                self.set_gain(index, strip["gain"])
            if "mute" in strip:
                self.set_mute(index, bool(strip["mute"]))
            for route, enabled in enumerate(strip.get("routes", [])[: len(self._route_buses)]):
                self.set_route(index, route, bool(enabled))
            for route, db in enumerate(strip.get("sends", [])[: len(self._route_buses)]):
                self.set_send(index, route, db)
        if "headphone_db" in state:
            self.set_headphone_gain(state["headphone_db"])

    def _queue(self, param: str, value: float | str) -> None:
        with self._lock:
            self._writes[param] = value

    # ------------------------------------------- thread de scrutation
    def _run(self) -> None:
        failing = False
        must_read = True
        next_tick = time.monotonic()
        while not self._stop.is_set():
            try:
                self._flush()
                if must_read or self._remote.pdirty:
                    self._read_params()
                    must_read = False
                self._read_levels()
            except Exception as exc:
                if not failing:
                    log.warning("Voicemeeter ne répond plus : %s", exc)
                failing, must_read = True, True
                self._set_connected(False)
                if self._stop.wait(ERROR_RETRY_S):
                    break
                next_tick = time.monotonic()
                continue
            if failing:
                log.info("Voicemeeter répond de nouveau.")
                failing = False
            self._set_connected(True)

            next_tick += self._interval
            delay = next_tick - time.monotonic()
            if delay < 0:  # on a pris du retard : on repart du temps présent
                next_tick, delay = time.monotonic(), 0.0
            self._stop.wait(delay)

    def _flush(self) -> None:
        with self._lock:
            writes, self._writes = self._writes, {}
            inserts, self._inserts_wanted = self._inserts_wanted, None
        expire = time.monotonic() + ECHO_GUARD_S
        for param, value in writes.items():
            self._remote.set(param, value)
            self._echo[param] = (expire, value)
        if inserts is not None:
            self._apply_inserts(inserts)

    def _get(self, param: str) -> float:
        """Lit un paramètre ; juste après une écriture, renvoie la valeur écrite
        (Voicemeeter met un instant à l'appliquer : sinon le fader sauterait)."""
        echo = self._echo.get(param)
        if echo is not None:
            if echo[0] > time.monotonic():
                return float(echo[1])
            del self._echo[param]
        return float(self._remote.get(param))

    def _read_params(self) -> None:
        strips = []
        for vm_strip in self._strips:
            prefix = f"Strip[{vm_strip}]"
            strips.append(StripParams(
                gain=round(self._get(f"{prefix}.Gain"), 2),
                mute=self._get(f"{prefix}.Mute") >= 0.5,
                routes=tuple(self._get(f"{prefix}.{BUS_NAMES[bus]}") >= 0.5
                             for bus in self._route_buses),
                sends=tuple(round(self._get(f"{prefix}.GainLayer[{bus}]"), 2)
                            for bus in self._route_buses),
            ))
        params = MixerParams(
            strips=tuple(strips),
            headphone_gain=round(self._get(f"Bus[{self._headphone_bus}].Gain"), 2),
        )
        if params != self._last_params:
            self._last_params = params
            self.params_changed.emit(params)

    def _read_levels(self) -> None:
        get_level = self._remote.get_level
        strips = []
        for vm_strip in self._strips:
            channel = input_channel(vm_strip)
            strips.append((amp_to_db(get_level(LEVEL_POSTFADER, channel)),
                           amp_to_db(get_level(LEVEL_POSTFADER, channel + 1))))
        base = self._headphone_bus * CHANNELS_PER_BUS
        master = (amp_to_db(get_level(LEVEL_OUTPUT, base)),
                  amp_to_db(get_level(LEVEL_OUTPUT, base + 1)))
        self.levels_changed.emit(MixerLevels(tuple(strips), master))

    def _apply_inserts(self, enabled: bool) -> None:
        changed = False
        for vm_strip in self._strips:
            first = input_channel(vm_strip)
            for channel in (first, first + 1):
                param = f"patch.insert[{channel}]"
                if enabled:
                    if channel not in self._inserts_saved:
                        self._inserts_saved[channel] = float(self._remote.get(param))
                    self._remote.set(param, 1.0)
                    changed = True
                elif channel in self._inserts_saved:
                    self._remote.set(param, self._inserts_saved.pop(channel))
                    changed = True
        if changed:
            log.info("Insert Voicemeeter %s pour les strips pilotées.",
                     "activé" if enabled else "rendu à son état d'origine")

    def _set_connected(self, connected: bool) -> None:
        if connected != self._connected:
            self._connected = connected
            self.connection_changed.emit(connected)


def _bus_index(name: str) -> int:
    name = str(name).upper()
    if name not in BUS_NAMES:
        raise ValueError(f"Bus Voicemeeter inconnu « {name} » (attendu : {', '.join(BUS_NAMES)})")
    return BUS_NAMES.index(name)
