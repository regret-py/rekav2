"""
Contrôle de Voicemeeter (Potato ou Banana) via l'API Remote (voicemeeterlib).

- Un thread de scrutation (30 Hz par défaut) est le seul à parler à l'API :
  il envoie les écritures demandées par l'UI, relit les paramètres quand
  Voicemeeter signale un changement, et lit les niveaux à chaque tour.
- L'UI n'appelle que des méthodes non bloquantes (set_*) : elles déposent la
  valeur voulue, et seule la dernière valeur d'un paramètre est envoyée.
- Les résultats remontent par signaux Qt, livrés dans le thread UI.

Correspondance par défaut (config.json → voicemeeter.strip_maps / routing) :
    Potato : strips UI 0..5 → strips VM 0, 1, 5, 6, 2, 7
    Banana : strips UI 0..4 → strips VM 0, 1, 3, 4, 2 (5 entrées seulement)
    knobs Monitor / Mic Out / Discord / Obs → bus A1 / B1 / B2 / A2

Intellipan (le carré avec le petit cube rouge) : entrées matérielles = modes
Voix (Color_x/y), Position (Pan_x/y) et Effets (fx_x/y) ; entrées virtuelles =
Position seulement (Pan 5.1). Plages de l'API Remote : x de -0,5 à +0,5,
y de 0 à 1 (Pan des entrées virtuelles : -0,5 à +0,5).
"""

from __future__ import annotations

import contextlib
import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Iterable

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

CHANNELS_PER_BUS = 8
LEVEL_POSTFADER = 1          # types de niveaux de VBVMR_GetLevel
LEVEL_OUTPUT = 3
SILENCE_DB = -100.0
GAIN_MIN_DB, GAIN_MAX_DB = -60.0, 12.0
ECHO_GUARD_S = 0.5           # après une écriture, on garde notre valeur le temps que VM l'applique
ERROR_RETRY_S = 1.0

DEFAULT_ROUTING = (
    {"label": "Monitor", "bus": "A1"},
    {"label": "Mic Out", "bus": "B1"},
    {"label": "Discord", "bus": "B2"},
    {"label": "Obs", "bus": "A2"},
)


@dataclass(frozen=True)
class Edition:
    """Ce qui change d'une édition de Voicemeeter à l'autre."""

    kind: str
    label: str
    physical_strips: int           # entrées matérielles : 2 canaux chacune
    virtual_strips: int            # entrées virtuelles : 8 canaux chacune
    buses: tuple[str, ...]         # dans l'ordre de l'API (index de Bus[i])
    gain_layers: bool              # niveaux d'envoi par bus (Strip[i].GainLayer[j])
    default_strip_map: tuple[int, ...]

    @property
    def strip_count(self) -> int:
        return self.physical_strips + self.virtual_strips


EDITIONS = {
    "potato": Edition("potato", "Voicemeeter Potato (8 entrées)", 5, 3,
                      ("A1", "A2", "A3", "A4", "A5", "B1", "B2", "B3"), True, (0, 1, 5, 6, 2, 7)),
    "banana": Edition("banana", "Voicemeeter Banana (5 entrées)", 3, 2,
                      ("A1", "A2", "A3", "B1", "B2"), False, (0, 1, 3, 4, 2)),
}
DEFAULT_KIND = "potato"
# Compatibilité : correspondance Potato par défaut
DEFAULT_STRIP_MAP = EDITIONS[DEFAULT_KIND].default_strip_map
PHYSICAL_STRIPS = EDITIONS[DEFAULT_KIND].physical_strips


def edition(cfg: dict) -> Edition:
    """Édition de Voicemeeter choisie (voicemeeter.kind), Potato par défaut."""
    kind = str(cfg.get("voicemeeter", {}).get("kind", DEFAULT_KIND)).lower()
    if kind not in EDITIONS:
        log.warning("Édition Voicemeeter « %s » non gérée, utilisation de Potato.", kind)
        kind = DEFAULT_KIND
    return EDITIONS[kind]


def strip_map(cfg: dict) -> tuple[int, ...]:
    """Strips Voicemeeter affichées, dans l'ordre de l'UI, pour l'édition choisie
    (voicemeeter.strip_maps.<édition>) ; les index hors édition sont ignorés."""
    ed = edition(cfg)
    section = cfg.get("voicemeeter", {})
    custom = section.get("strip_maps", {}).get(ed.kind)
    legacy = section.get("strip_map")      # clé des versions 0.2 (Potato seulement)
    if ed.kind == "potato" and legacy and tuple(custom or ()) in ((), ed.default_strip_map):
        custom = legacy
    strips = tuple(int(s) for s in (custom or ed.default_strip_map))
    valid = tuple(s for s in strips if 0 <= s < ed.strip_count)
    if len(valid) != len(strips):
        log.warning("strip_maps.%s : strips hors de %s ignorées.", ed.kind, ed.label)
    return valid or ed.default_strip_map


def input_channel(strip: int, physical_strips: int = PHYSICAL_STRIPS) -> int:
    """Premier canal d'une strip Voicemeeter dans les tableaux de niveaux et
    d'insert : 2 canaux par entrée physique, puis 8 par entrée virtuelle."""
    if strip < physical_strips:
        return 2 * strip
    return 2 * physical_strips + 8 * (strip - physical_strips)


def strip_channels(cfg: dict) -> list[int]:
    """Premier canal d'insert de chaque strip de l'UI."""
    ed = edition(cfg)
    return [input_channel(s, ed.physical_strips) for s in strip_map(cfg)]


def amp_to_db(value: float) -> float:
    return 20.0 * math.log10(value) if value > 1e-5 else SILENCE_DB


def _clamp_gain(db: float) -> float:
    """Gain envoyé à Voicemeeter : sa plage, au dixième de dB (sa précision d'affichage)."""
    return round(max(GAIN_MIN_DB, min(GAIN_MAX_DB, float(db))), 1)


# --- Intellipan -------------------------------------------------------------
@dataclass(frozen=True)
class PadMode:
    key: str          # nom court, stocké dans la config
    label: str        # affiché sur le pad
    title: str        # menu et info-bulle
    param: str        # préfixe des paramètres : Color → Color_x / Color_y


PAD_MODES = {
    "color": PadMode("color", "VOIX", "Voix (couleur du son)", "Color"),
    "pan": PadMode("pan", "POSITION", "Position (panoramique)", "Pan"),
    "fx": PadMode("fx", "EFFETS", "Effets (écho, modulation)", "fx"),
}
PHYSICAL_PAD_MODES = ("color", "pan", "fx")
VIRTUAL_PAD_MODES = ("pan",)
PAD_X_RANGE = (-0.5, 0.5)


def pad_modes(vm_strip: int, physical_strips: int = PHYSICAL_STRIPS) -> tuple[str, ...]:
    """Modes d'Intellipan d'une strip Voicemeeter (Color et fx : entrées matérielles)."""
    return PHYSICAL_PAD_MODES if vm_strip < physical_strips else VIRTUAL_PAD_MODES


def pad_y_range(mode: str, physical: bool) -> tuple[float, float]:
    """Plage de y : 0 → 1, sauf le Pan 5.1 des entrées virtuelles (-0,5 → +0,5)."""
    return (-0.5, 0.5) if mode == "pan" and not physical else (0.0, 1.0)


def clamp_pad(mode: str, physical: bool, x: float, y: float) -> tuple[float, float]:
    low, high = pad_y_range(mode, physical)
    return (round(max(PAD_X_RANGE[0], min(PAD_X_RANGE[1], float(x))), 3),
            round(max(low, min(high, float(y))), 3))


@dataclass(frozen=True)
class StripParams:
    gain: float
    mute: bool
    routes: tuple[bool, ...]   # un booléen par knob de routing
    sends: tuple[float, ...]   # niveau (dB) de chaque knob de routing
    # Intellipan : (mode, x, y) pour chaque mode de la strip
    pads: tuple[tuple[str, float, float], ...] = ()

    def pad(self, mode: str) -> tuple[float, float] | None:
        for key, x, y in self.pads:
            if key == mode:
                return x, y
        return None


@dataclass(frozen=True)
class MixerParams:
    strips: tuple[StripParams, ...]
    headphone_gain: float

    def to_state(self) -> dict:
        """Forme sauvegardée dans config.json (section « state »)."""
        return {
            "strips": [
                {"gain": s.gain, "mute": s.mute, "routes": list(s.routes), "sends": list(s.sends),
                 "pads": {mode: [x, y] for mode, x, y in s.pads}}
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
        self._edition = edition(cfg)
        self._kind = self._edition.kind
        self._strips = strip_map(cfg)
        # Bus de chaque knob : nom (« B1 ») et index dans l'API de l'édition
        routing = section.get("routing", DEFAULT_ROUTING)
        self._route_names = tuple(
            _bus_name(route.get("bus", ""), self._edition,
                      DEFAULT_ROUTING[i]["bus"] if i < len(DEFAULT_ROUTING) else "A1")
            for i, route in enumerate(routing))
        self._route_buses = tuple(self._edition.buses.index(n) for n in self._route_names)
        self._headphone_bus = self._edition.buses.index(
            _bus_name(section.get("headphone_bus", "A1"), self._edition, "A1"))
        self._interval = 1.0 / max(1.0, float(section.get("poll_hz", 30)))

        self._remote = None          # objet voicemeeterlib, créé au login
        self._logged_in = False
        self._lock = threading.Lock()
        self._writes: dict[str, float | str] = {}
        self._inserts_wanted: frozenset[int] | None = None
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
        running = None
        with contextlib.suppress(Exception):
            running = str(self._remote.type).lower()
        if running in ("basic", "banana", "potato") and running != self._kind:
            # Une autre édition tourne : ses strips n'ont pas les mêmes index
            with contextlib.suppress(Exception):
                self._remote.logout()
            from core.process_manager import OtherEditionRunningError
            raise OtherEditionRunningError(running, self._kind)
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
            self._apply_inserts(frozenset())
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

    @property
    def edition(self) -> Edition:
        return self._edition

    @property
    def supports_sends(self) -> bool:
        """Niveaux d'envoi par bus (knobs) : Potato uniquement."""
        return self._edition.gain_layers

    def set_gain(self, strip: int, db: float) -> None:
        self._queue(f"Strip[{self._strips[strip]}].Gain", _clamp_gain(db))

    def set_mute(self, strip: int, muted: bool) -> None:
        self._queue(f"Strip[{self._strips[strip]}].Mute", 1.0 if muted else 0.0)

    def set_route(self, strip: int, route: int, enabled: bool) -> None:
        bus = self._route_names[route]
        self._queue(f"Strip[{self._strips[strip]}].{bus}", 1.0 if enabled else 0.0)

    def set_send(self, strip: int, route: int, db: float) -> None:
        if not self.supports_sends:
            return  # Banana : pas de GainLayer
        layer = self._route_buses[route]
        self._queue(f"Strip[{self._strips[strip]}].GainLayer[{layer}]", _clamp_gain(db))

    def set_label(self, strip: int, text: str) -> None:
        self._queue(f"Strip[{self._strips[strip]}].Label", text[:30])

    def set_headphone_gain(self, db: float) -> None:
        self._queue(f"Bus[{self._headphone_bus}].Gain", _clamp_gain(db))

    def pad_modes(self, strip: int) -> tuple[str, ...]:
        return pad_modes(self._strips[strip], self._edition.physical_strips)

    def is_physical(self, strip: int) -> bool:
        return self._strips[strip] < self._edition.physical_strips

    def virtual_input(self, strip: int) -> int | None:
        """Entrée virtuelle d'une strip (0 VAIO, 1 AUX, 2 VAIO3), None si matérielle."""
        vm_strip = self._strips[strip]
        return None if vm_strip < self._edition.physical_strips else \
            vm_strip - self._edition.physical_strips

    def set_pad(self, strip: int, mode: str, x: float, y: float) -> None:
        """Position du petit cube de l'Intellipan (mode : color, pan ou fx)."""
        if mode not in self.pad_modes(strip):
            return
        x, y = clamp_pad(mode, self.is_physical(strip), x, y)
        prefix = f"Strip[{self._strips[strip]}].{PAD_MODES[mode].param}"
        self._queue(f"{prefix}_x", x)
        self._queue(f"{prefix}_y", y)

    def set_inserts(self, strips: Iterable[int]) -> None:
        """Active l'insert Voicemeeter des strips données (leur son passe alors
        par REAPER, donc par les effets) et rend les autres à leur état d'origine."""
        with self._lock:
            self._inserts_wanted = frozenset(strips)

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
            pads = strip.get("pads")
            if isinstance(pads, dict):
                for mode, xy in pads.items():
                    if isinstance(xy, (list, tuple)) and len(xy) == 2:
                        self.set_pad(index, mode, xy[0], xy[1])
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
            modes = pad_modes(vm_strip, self._edition.physical_strips)
            strips.append(StripParams(
                gain=round(self._get(f"{prefix}.Gain"), 2),
                mute=self._get(f"{prefix}.Mute") >= 0.5,
                routes=tuple(self._get(f"{prefix}.{name}") >= 0.5 for name in self._route_names),
                sends=tuple(round(self._get(f"{prefix}.GainLayer[{bus}]"), 2)
                            if self.supports_sends else 0.0
                            for bus in self._route_buses),
                pads=tuple((mode, round(self._get(f"{prefix}.{PAD_MODES[mode].param}_x"), 3),
                            round(self._get(f"{prefix}.{PAD_MODES[mode].param}_y"), 3))
                           for mode in modes),
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
            channel = input_channel(vm_strip, self._edition.physical_strips)
            strips.append((amp_to_db(get_level(LEVEL_POSTFADER, channel)),
                           amp_to_db(get_level(LEVEL_POSTFADER, channel + 1))))
        base = self._headphone_bus * CHANNELS_PER_BUS
        master = (amp_to_db(get_level(LEVEL_OUTPUT, base)),
                  amp_to_db(get_level(LEVEL_OUTPUT, base + 1)))
        self.levels_changed.emit(MixerLevels(tuple(strips), master))

    def _apply_inserts(self, wanted: frozenset[int]) -> None:
        """Insert activé pour les strips UI de `wanted` ; les autres retrouvent
        la valeur qu'elles avaient avant que l'app n'y touche."""
        for index, vm_strip in enumerate(self._strips):
            first = input_channel(vm_strip, self._edition.physical_strips)
            enable = index in wanted
            changed = False
            for channel in (first, first + 1):
                param = f"patch.insert[{channel}]"
                if enable:
                    if channel not in self._inserts_saved:
                        self._inserts_saved[channel] = float(self._remote.get(param))
                        self._remote.set(param, 1.0)
                        changed = True
                elif channel in self._inserts_saved:
                    self._remote.set(param, self._inserts_saved.pop(channel))
                    changed = True
            if changed:
                log.info("Insert Voicemeeter de la strip %d (VM %d) %s.", index, vm_strip,
                         "activé" if enable else "rendu à son état d'origine")

    def _set_connected(self, connected: bool) -> None:
        if connected != self._connected:
            self._connected = connected
            self.connection_changed.emit(connected)


def _bus_name(name: str, ed: Edition, fallback: str = "A1") -> str:
    """Nom de bus valide pour l'édition ; un bus absent (A4 réglé sur Potato,
    puis passage à Banana) est remplacé par `fallback` plutôt que de bloquer
    le démarrage."""
    name = str(name).upper()
    if name in ed.buses:
        return name
    log.warning("Bus « %s » absent de %s (bus : %s) : %s utilisé à la place.",
                name, ed.label, ", ".join(ed.buses), fallback)
    return fallback
