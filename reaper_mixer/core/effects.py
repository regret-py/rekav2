"""
Moteurs d'effets : l'application qui applique les plugins aux strips.

REKA pilote toujours Voicemeeter (volumes, routing, VU) ; les effets peuvent
venir de plusieurs applications, au choix (config effects.host) :

- reaper   : REAPER, piloté par le pont Lua (liste, ajout, bypass, suppression
             et interface des plugins, projets). Le son passe par l'insert de
             Voicemeeter (pilote « Voicemeeter Insert Virtual ASIO »).
- flstudio : FL Studio, lancé et masqué par REKA ; les effets se règlent dans
             FL Studio lui-même (pas d'API de pilotage), le son passe aussi par
             l'insert de Voicemeeter, activé strip par strip (badge FX).
- apo      : Equalizer APO : REKA écrit sa configuration (plugins VST2 par
             périphérique Windows). Aucun aller-retour par l'insert : c'est le
             moteur qui ajoute le moins de délai.
- none     : pas d'effets, Voicemeeter seul (le délai le plus faible).

Tous les moteurs ont la même interface (EffectsHost) : la fenêtre principale
ne connaît que celle-ci. Une strip y est décrite par un TrackInfo (nom,
chaîne active, plugins), comme une piste REAPER.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

HOST_REAPER = "reaper"
HOST_FLSTUDIO = "flstudio"
HOST_APO = "apo"
HOST_NONE = "none"
DEFAULT_HOST = HOST_REAPER


@dataclass(frozen=True)
class HostInfo:
    key: str
    label: str          # nom court (« REAPER »)
    description: str    # une phrase pour le menu de sélection
    latency: str        # délai ajouté, en clair


HOSTS: dict[str, HostInfo] = {
    HOST_REAPER: HostInfo(
        HOST_REAPER, "REAPER",
        "Effets VST2/VST3/CLAP gérés depuis REKA : ajout, bypass, interface des plugins, projets.",
        "Insert Voicemeeter : + 2 tampons ASIO, plus la latence des plugins."),
    HOST_FLSTUDIO: HostInfo(
        HOST_FLSTUDIO, "FL Studio",
        "Les effets se règlent dans FL Studio (ouvert depuis REKA) ; REKA gère l'insert "
        "Voicemeeter de chaque strip.",
        "Insert Voicemeeter : + 2 tampons ASIO, plus la latence des plugins."),
    HOST_APO: HostInfo(
        HOST_APO, "Equalizer APO",
        "Plugins VST2 appliqués par Equalizer APO sur les périphériques Windows des strips, "
        "gérés depuis REKA.",
        "Pas d'aller-retour par l'insert : seulement la latence des plugins."),
    HOST_NONE: HostInfo(
        HOST_NONE, "Aucun (Voicemeeter seul)",
        "Pas d'effets : volumes, routing et VU-mètres uniquement.",
        "Aucun délai ajouté."),
}


def host_kind(cfg: dict) -> str:
    """Moteur d'effets choisi (effects.host), REAPER par défaut."""
    kind = str(cfg.get("effects", {}).get("host", DEFAULT_HOST) or DEFAULT_HOST).lower()
    if kind not in HOSTS:
        log.warning("Moteur d'effets « %s » inconnu, utilisation de REAPER.", kind)
        kind = DEFAULT_HOST
    return kind


def samples_to_ms(samples: int, samplerate: float) -> float:
    return 1000.0 * samples / samplerate if samplerate > 0 else 0.0


class EffectsHost(QObject):
    """Interface commune des moteurs d'effets (et comportement par défaut :
    aucun effet). Les signaux sont émis dans le thread de l'interface ou
    livrés par Qt dans celui-ci."""

    connection_changed = Signal(bool)
    tracks_changed = Signal(object)       # list[TrackInfo | None], une entrée par strip
    device_changed = Signal(object)       # AudioDevice (REAPER seulement)
    project_changed = Signal(str)         # projet ouvert (REAPER seulement)
    installed_fx_ready = Signal(object)   # list[(nom, ident)] ou None
    command_failed = Signal(str)

    kind = HOST_NONE
    process_key: str | None = None        # processus géré par ProcessManager
    manages_fx = False                    # liste / ajout / bypass / suppression de plugins
    has_projects = False                  # projets ouvrables depuis REKA (REAPER)
    has_audio_device = False              # carte son lisible (REAPER)
    add_label = "+ Ajouter un effet"      # texte de l'emplacement libre au survol
    empty_hint = ""                       # texte d'une strip sans effet

    def __init__(self, cfg: dict, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._cfg = cfg
        self.version = ""

    # ---------------------------------------------------------- description
    @property
    def label(self) -> str:
        return HOSTS[self.kind].label

    @property
    def is_connected(self) -> bool:
        return True

    @property
    def device(self):
        return None

    @property
    def installed_fx(self) -> list[tuple[str, str]] | None:
        return None

    @property
    def project_path(self) -> str:
        return ""

    @property
    def strip_tracks(self) -> list:
        return []

    def status(self, connected: bool) -> tuple[bool, str, str]:
        """(ok, texte, info-bulle) du badge de connexion du Master."""
        if connected:
            return True, f"{self.label} connecté", ""
        return False, f"{self.label} déconnecté", \
            f"{self.label} ne répond plus : les effets ne sont plus modifiables."

    def insert_strips(self, tracks: list, connected: bool) -> set[int]:
        """Strips dont le son doit passer par l'insert de Voicemeeter."""
        return set()

    def latency_report(self) -> str:
        """Délai ajouté par les effets, en clair (réglages → Audio)."""
        return HOSTS[self.kind].latency

    def strip_hint(self, strip: int) -> str:
        """Texte affiché dans la zone d'effets vide d'une strip."""
        return self.empty_hint

    def strip_latency_ms(self, strip: int) -> float | None:
        """Latence des plugins actifs d'une strip en ms, si le moteur la connaît."""
        return None

    def fx_latency_ms(self, strip: int, fx_index: int) -> float | None:
        """Latence propre d'un plugin en ms, si le moteur la connaît."""
        return None

    # ----------------------------------------------------------- démarrage
    def resource_path(self, exe: Path | None) -> Path | None:
        return None

    def prepare_launch(self, exe: Path) -> dict[str, str]:
        return {}

    def start(self) -> None:
        pass

    def ping(self, timeout_s: float) -> bool:
        return True

    def close(self, save_project: bool = True) -> None:
        pass

    # ------------------------------------------------------------ commandes
    def rename_track(self, strip: int, name: str) -> None:
        pass

    def add_fx(self, strip: int, fx_name: str) -> None:
        pass

    def remove_fx(self, strip: int, fx_index: int) -> None:
        pass

    def set_fx_enabled(self, strip: int, fx_index: int, enabled: bool) -> None:
        pass

    def set_chain_enabled(self, strip: int, enabled: bool) -> None:
        pass

    def toggle_fx_ui(self, strip: int, fx_index: int) -> None:
        pass

    def open_project(self, path) -> None:
        pass

    def save_project(self) -> None:
        pass

    def open_preferences(self) -> None:
        pass

    def request_installed_fx(self) -> None:
        self.installed_fx_ready.emit(None)

    def show_app(self) -> None:
        """Affiche l'application du moteur (FL Studio, éditeur d'Equalizer APO)."""


class NoEffects(EffectsHost):
    """Voicemeeter seul : pas d'effets, aucun délai ajouté."""

    kind = HOST_NONE
    empty_hint = "Pas d'effets"

    def status(self, connected: bool) -> tuple[bool, str, str]:
        return True, "Voicemeeter seul", "Aucun moteur d'effets : le son ne subit aucun délai ajouté."


def create_host(cfg: dict, processes=None) -> EffectsHost:
    """Moteur d'effets de la config (créé dans le thread de l'interface)."""
    kind = host_kind(cfg)
    if kind == HOST_REAPER:
        from core.reaper_ctrl import ReaperController
        return ReaperController(cfg)
    if kind == HOST_FLSTUDIO:
        from core.flstudio_ctrl import FlStudioController
        return FlStudioController(cfg, processes)
    if kind == HOST_APO:
        from core.apo_ctrl import ApoController
        return ApoController(cfg)
    return NoEffects(cfg)
