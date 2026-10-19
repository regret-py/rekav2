"""
Moteur d'effets Equalizer APO.

Equalizer APO applique des filtres et des plugins VST2 directement dans le
moteur audio de Windows, sur les périphériques où il est installé (choisis
dans son « Configurator »). Pas d'aller-retour par l'insert de Voicemeeter :
c'est le moteur d'effets qui ajoute le moins de délai.

Ce que fait REKA :
  - il écrit `reka.txt` dans le dossier de configuration d'Equalizer APO :
    pour chaque strip, une sélection de périphérique (« Device: ») suivie de
    ses plugins (« VSTPlugin: Library … ») ; un plugin ou une chaîne en
    bypass est écrit en commentaire ;
  - il ajoute une seule ligne « Include: reka.txt » à la fin de `config.txt`,
    sans toucher au reste de la configuration de l'utilisateur ;
  - les réglages faits dans l'éditeur d'Equalizer APO (ChunkData des plugins)
    sont relus et conservés quand REKA réécrit le fichier.

Périphérique Windows de chaque strip :
  - entrées virtuelles : « Voicemeeter Input », « Voicemeeter AUX Input »,
    « Voicemeeter VAIO3 Input » (là où jouent les applications) ;
  - entrées matérielles (micro…) : le périphérique de capture choisi dans
    REKA. Voicemeeter doit alors l'ouvrir en partage (MME, ou WDM sans le
    mode exclusif) : sinon Windows, donc Equalizer APO, ne voit pas le son.
Sous Windows, le nom est résolu en GUID de périphérique, sans ambiguïté.
"""

from __future__ import annotations

import logging
import os
import re
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QProcess

from core import config
from core.effects import HOST_APO, EffectsHost
from core.reaper_ctrl import FxInfo, TrackInfo
from core.voicemeeter_ctrl import edition, strip_map

log = logging.getLogger(__name__)

CONFIG_FILE = "config.txt"
REKA_FILE = "reka.txt"
INCLUDE_LINE = f"Include: {REKA_FILE}"
HEADER = ("# Généré par REKA : ce fichier est réécrit à chaque changement fait dans REKA.\n"
          "# Les réglages des plugins (ChunkData) faits dans l'éditeur d'Equalizer APO sont conservés.\n")
DISABLED = "# [REKA:off] "
REGISTRY_KEY = r"SOFTWARE\EqualizerAPO"
DOWNLOAD_URL = "https://sourceforge.net/projects/equalizerapo/"
VIRTUAL_DEVICES = ("Voicemeeter Input", "Voicemeeter AUX Input", "Voicemeeter VAIO3 Input")
_GUID = re.compile(r"\{[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}")
_LIBRARY = re.compile(r'^VSTPlugin:\s*Library\s+(?:"([^"]*)"|(\S+))\s*(.*)$', re.IGNORECASE)
_MACHINE_X64, _MACHINE_ARM64 = 0x8664, 0xAA64


# --------------------------------------------------------------------------- #
# Installation d'Equalizer APO
# --------------------------------------------------------------------------- #
def _registry_value(name: str) -> str | None:
    if sys.platform != "win32":
        return None
    import winreg

    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, REGISTRY_KEY, 0,
                                winreg.KEY_READ | view) as key:
                value, _ = winreg.QueryValueEx(key, name)
        except OSError:
            continue
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def find_config_dir(cfg: dict) -> Path | None:
    """Dossier de configuration d'Equalizer APO (apo.config_dir, sinon le
    registre, sinon l'emplacement standard), ou None s'il est introuvable."""
    candidates = [config.resolve_path(cfg.get("apo", {}).get("config_dir"))]
    registry = _registry_value("ConfigPath")
    if registry:
        candidates.append(Path(registry))
    install = _registry_value("InstallPath")
    if install:
        candidates.append(Path(install) / "config")
    program_files = os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles")
    if program_files:
        candidates.append(Path(program_files) / "EqualizerAPO" / "config")
    for path in candidates:
        if path is not None and path.is_dir():
            return path
    return None


def find_editor(config_dir: Path | None) -> Path | None:
    """Éditeur de configuration d'Equalizer APO (Editor.exe)."""
    folders = []
    install = _registry_value("InstallPath")
    if install:
        folders.append(Path(install))
    if config_dir is not None:
        folders.append(config_dir.parent)
    for folder in folders:
        exe = folder / "Editor.exe"
        if exe.is_file():
            return exe
    return None


def dll_machine(path: Path) -> int | None:
    """Architecture d'une DLL (champ Machine de l'en-tête PE), None si illisible."""
    try:
        with open(path, "rb") as handle:
            if handle.read(2) != b"MZ":
                return None
            handle.seek(0x3C)
            offset = struct.unpack("<I", handle.read(4))[0]
            handle.seek(offset)
            if handle.read(4) != b"PE\0\0":
                return None
            return struct.unpack("<H", handle.read(2))[0]
    except (OSError, struct.error):
        return None


def check_plugin(path: Path) -> str | None:
    """Raison pour laquelle Equalizer APO ne pourra pas charger ce plugin, ou None."""
    if path.suffix.lower() != ".dll":
        return "Equalizer APO ne charge que des plugins VST2 (.dll), pas les VST3."
    if not path.is_file():
        return f"Fichier introuvable : {path}"
    machine = dll_machine(path)
    if machine is not None and machine not in (_MACHINE_X64, _MACHINE_ARM64):
        return "Ce plugin est en 32 bits : Equalizer APO ne charge que des plugins 64 bits."
    return None


def default_vst2_dirs(extra: list[str] | None = None) -> list[Path]:
    dirs = []
    for variable in ("ProgramW6432", "ProgramFiles"):
        base = os.environ.get(variable)
        if base:
            dirs += [Path(base) / "VSTPlugins", Path(base) / "Steinberg" / "VSTPlugins",
                     Path(base) / "Common Files" / "VST2", Path(base) / "Common Files" / "Steinberg" / "VST2"]
    common = os.environ.get("CommonProgramW6432") or os.environ.get("CommonProgramFiles")
    if common:
        dirs += [Path(common) / "VST2", Path(common) / "Steinberg" / "VST2"]
    dirs.extend(Path(os.path.expandvars(d)) for d in (extra or []))
    unique = []
    for path in dirs:
        if path not in unique:
            unique.append(path)
    return unique


def scan_vst2(dirs: list[Path], limit: int = 2000):
    """Plugins VST2 (.dll) des dossiers donnés, pour le dialogue d'ajout."""
    from core.vst_scanner import PluginInfo

    plugins = []
    for root in dirs:
        if not root.is_dir():
            continue
        for current, _subdirs, files in os.walk(root):
            for name in files:
                if name.lower().endswith(".dll"):
                    path = Path(current) / name
                    plugins.append(PluginInfo(path.stem, str(path), "VST2", Path(current).name))
                    if len(plugins) >= limit:
                        return plugins
    return sorted(plugins, key=lambda p: p.name.lower())


# --------------------------------------------------------------------------- #
# Périphériques Windows
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Endpoint:
    name: str        # « Microphone (Shure MV7) »
    guid: str        # « {b3f8…} »
    capture: bool


def list_endpoints() -> list[Endpoint]:
    """Périphériques audio actifs de Windows (lecture et capture)."""
    if sys.platform != "win32":
        return []
    try:
        import warnings

        from pycaw.constants import DEVICE_STATE, EDataFlow
        from pycaw.utils import AudioUtilities

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            devices = AudioUtilities.GetAllDevices(EDataFlow.eAll.value, DEVICE_STATE.ACTIVE.value)
    except Exception as exc:
        log.warning("Liste des périphériques Windows indisponible : %s", exc)
        return []
    endpoints = []
    for device in devices:
        ident = str(getattr(device, "id", "") or "")
        guid = _GUID.findall(ident)
        if guid:
            endpoints.append(Endpoint(str(device.FriendlyName or ""), guid[-1].lower(),
                                      ident.startswith("{0.0.1.")))
    return sorted(endpoints, key=lambda e: (e.capture, e.name.lower()))


def resolve_device(device: str, endpoints: list[Endpoint]) -> tuple[str, str]:
    """(motif écrit après « Device: », nom lisible) d'un périphérique : son GUID
    si on le retrouve, sinon le texte tel quel."""
    device = " ".join(device.replace(";", " ").split())
    if not device:
        return "", ""
    if _GUID.fullmatch(device):
        name = next((e.name for e in endpoints if e.guid == device.lower()), device)
        return device.lower(), name
    lower = device.lower()
    exact = [e for e in endpoints if e.name.lower() == lower]
    starts = [e for e in endpoints if e.name.lower().startswith(lower + " (")]
    found = exact or starts
    if len(found) == 1:
        return found[0].guid, found[0].name
    return device, device


# --------------------------------------------------------------------------- #
# Fichier reka.txt
# --------------------------------------------------------------------------- #
@dataclass
class ApoPlugin:
    path: str
    enabled: bool = True

    @property
    def name(self) -> str:
        return Path(self.path.replace("\\", "/")).stem or self.path


@dataclass
class ApoChain:
    device: str = ""                 # nom du périphérique Windows, ou son GUID
    enabled: bool = True
    plugins: list[ApoPlugin] = field(default_factory=list)


def _quote(path: str) -> str:
    return f'"{path}"'


def _library_key(path: str) -> str:
    return os.path.normcase(path.strip().strip('"')).replace("/", "\\").lower()


def parse_settings(text: str) -> dict[tuple[str, str, int], str]:
    """Réglages des plugins déjà présents dans reka.txt :
    (motif du périphérique, bibliothèque, n-ième occurrence) → paramètres
    après le chemin (ChunkData…), pour les conserver à la réécriture."""
    settings: dict[tuple[str, str, int], str] = {}
    device = ""
    seen: dict[tuple[str, str], int] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith(DISABLED.strip()):
            line = line[len(DISABLED.strip()):].strip()
        elif line.startswith("#"):
            continue
        if line.lower().startswith("device:"):
            device = line.split(":", 1)[1].strip().lower()
            continue
        match = _LIBRARY.match(line)
        if match:
            library = _library_key(match.group(1) or match.group(2) or "")
            occurrence = seen.get((device, library), 0)
            seen[(device, library)] = occurrence + 1
            if match.group(3).strip():
                settings[(device, library, occurrence)] = match.group(3).strip()
    return settings


def render(chains: list[ApoChain | None], names: list[str], endpoints: list[Endpoint],
           previous: str = "") -> str:
    """Contenu de reka.txt pour les chaînes des strips."""
    settings = parse_settings(previous)
    lines = [HEADER.rstrip("\n")]
    for index, chain in enumerate(chains):
        if chain is None or not chain.plugins:
            continue
        pattern, readable = resolve_device(chain.device, endpoints)
        name = names[index] if index < len(names) else f"Strip {index + 1}"
        if not pattern:
            lines += ["", f"# {name} : aucun périphérique choisi, effets non appliqués."]
            continue
        lines += ["", f"# {name} → {readable}" + ("" if chain.enabled else " (bypass)"),
                  f"Device: {pattern}"]
        seen: dict[str, int] = {}
        for plugin in chain.plugins:
            library = _library_key(plugin.path)
            occurrence = seen.get(library, 0)
            seen[library] = occurrence + 1
            extra = settings.get((pattern.lower(), library, occurrence), "")
            line = f"VSTPlugin: Library {_quote(plugin.path)}" + (f" {extra}" if extra else "")
            lines.append(line if chain.enabled and plugin.enabled else DISABLED + line)
    lines += ["", "Device: all", ""]
    return "\n".join(lines)


def ensure_include(config_dir: Path) -> bool:
    """Ajoute « Include: reka.txt » à la fin de config.txt s'il n'y est pas.
    Le reste du fichier (et son encodage) est conservé. True si modifié."""
    path = config_dir / CONFIG_FILE
    raw = path.read_bytes() if path.is_file() else b""
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig", errors="surrogateescape")
    for line in text.splitlines():
        if line.strip().lower().replace(" ", "") == INCLUDE_LINE.lower().replace(" ", ""):
            return False
    newline = "\r\n" if "\r\n" in text or not text else "\n"
    if text and not text.endswith(("\n", "\r")):
        text += newline
    text += f"# Effets des strips de REKA{newline}{INCLUDE_LINE}{newline}"
    data = text.encode("utf-8", errors="surrogateescape")
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + data)
    log.info("Equalizer APO : « %s » ajouté à %s", INCLUDE_LINE, path)
    return True


def chains_from_config(cfg: dict, count: int) -> list[ApoChain]:
    rows = cfg.get("apo", {}).get("strips") or []
    chains = []
    for index in range(count):
        row = rows[index] if index < len(rows) and isinstance(rows[index], dict) else {}
        plugins = [ApoPlugin(str(p.get("path", "")), bool(p.get("enabled", True)))
                   for p in row.get("plugins", []) if isinstance(p, dict) and p.get("path")]
        chains.append(ApoChain(str(row.get("device", "") or ""), bool(row.get("enabled", True)),
                               plugins))
    return chains


def default_devices(cfg: dict) -> list[str]:
    """Périphérique Windows par défaut de chaque strip : les entrées virtuelles
    ont le leur ; les entrées matérielles sont à choisir."""
    ed = edition(cfg)
    result = []
    for vm_strip in strip_map(cfg):
        virtual = vm_strip - ed.physical_strips
        result.append(VIRTUAL_DEVICES[virtual] if 0 <= virtual < len(VIRTUAL_DEVICES) else "")
    return result


# --------------------------------------------------------------------------- #
# Contrôleur
# --------------------------------------------------------------------------- #
class ApoController(EffectsHost):
    kind = HOST_APO
    manages_fx = True

    def __init__(self, cfg: dict, parent: QObject | None = None,
                 endpoints: list[Endpoint] | None = None) -> None:
        super().__init__(cfg, parent)
        self._count = len(strip_map(cfg))
        self._chains = chains_from_config(cfg, self._count)
        defaults = default_devices(cfg)
        for chain, default in zip(self._chains, defaults):
            if not chain.device:
                chain.device = default
        self._endpoints = endpoints
        self._config_dir = find_config_dir(cfg)
        self.version = "Equalizer APO"

    # ------------------------------------------------------------ état
    @property
    def config_dir(self) -> Path | None:
        return self._config_dir

    @property
    def is_connected(self) -> bool:
        return self._config_dir is not None

    @property
    def strip_tracks(self) -> list[TrackInfo | None]:
        names = self._names()
        return [TrackInfo(names[i], chain.enabled,
                          tuple(FxInfo(f"VST: {p.name}", p.enabled, False) for p in chain.plugins),
                          index=i)
                for i, chain in enumerate(self._chains)]

    def strip_device(self, strip: int) -> str:
        return self._chains[strip].device if 0 <= strip < len(self._chains) else ""

    def endpoints(self) -> list[Endpoint]:
        if self._endpoints is None:
            self._endpoints = list_endpoints()
        return self._endpoints

    def status(self, connected: bool) -> tuple[bool, str, str]:
        if connected:
            return True, "Equalizer APO", f"Configuration : {self._config_dir / REKA_FILE}"
        return False, "Equalizer APO introuvable", \
            f"Installe Equalizer APO ({DOWNLOAD_URL}) sur les périphériques des strips."

    def strip_hint(self, strip: int) -> str:
        if not self.strip_device(strip):
            return "Choisis le périphérique"
        return ""

    def latency_report(self) -> str:
        return ("Equalizer APO traite le son dans Windows : aucun tampon ajouté, seulement la "
                "latence propre des plugins.")

    # ------------------------------------------------------------ commandes
    def set_device(self, strip: int, device: str) -> None:
        if 0 <= strip < len(self._chains):
            self._chains[strip].device = device.strip()
            self._changed()

    def add_fx(self, strip: int, fx_name: str) -> None:
        problem = check_plugin(Path(fx_name))
        if problem:
            self.command_failed.emit(problem)
            return
        self._chains[strip].plugins.append(ApoPlugin(fx_name))
        self._changed()

    def remove_fx(self, strip: int, fx_index: int) -> None:
        plugins = self._chains[strip].plugins
        if 0 <= fx_index < len(plugins):
            del plugins[fx_index]
            self._changed()

    def set_fx_enabled(self, strip: int, fx_index: int, enabled: bool) -> None:
        plugins = self._chains[strip].plugins
        if 0 <= fx_index < len(plugins):
            plugins[fx_index].enabled = enabled
            self._changed()

    def set_chain_enabled(self, strip: int, enabled: bool) -> None:
        self._chains[strip].enabled = enabled
        self._changed()

    def rename_track(self, strip: int, name: str) -> None:
        if self._config_dir is not None:
            self._write()      # le nom de la strip figure en commentaire

    def toggle_fx_ui(self, strip: int, fx_index: int) -> None:
        self.show_app()

    def show_app(self) -> None:
        """Ouvre l'éditeur d'Equalizer APO (réglage des plugins)."""
        editor = find_editor(self._config_dir)
        if editor is None or not QProcess.startDetached(str(editor), [], str(editor.parent)):
            self.command_failed.emit("Éditeur d'Equalizer APO introuvable : ouvre-le depuis le "
                                     "menu Démarrer pour régler les plugins.")

    def start(self) -> None:
        if self._config_dir is None:
            log.warning("Equalizer APO introuvable : effets non appliqués.")
            return
        self._write()
        self.tracks_changed.emit(self.strip_tracks)

    # ------------------------------------------------------------ interne
    def _names(self) -> list[str]:
        names = list(self._cfg.get("ui", {}).get("strip_names", []))
        return names + [f"Strip {i + 1}" for i in range(len(names), self._count)]

    def _changed(self) -> None:
        self._cfg.setdefault("apo", {})["strips"] = [
            {"device": c.device, "enabled": c.enabled,
             "plugins": [{"path": p.path, "enabled": p.enabled} for p in c.plugins]}
            for c in self._chains]
        self._write()
        self.tracks_changed.emit(self.strip_tracks)

    def _write(self) -> None:
        if self._config_dir is None:
            self.command_failed.emit("Equalizer APO est introuvable : installe-le, ou indique son "
                                     "dossier de configuration dans les réglages.")
            return
        path = self._config_dir / REKA_FILE
        try:
            previous = path.read_text(encoding="utf-8-sig", errors="replace") if path.is_file() else ""
            text = render(self._chains, self._names(), self.endpoints(), previous)
            if text != previous:
                path.write_text(text, encoding="utf-8")
            ensure_include(self._config_dir)
        except OSError as exc:
            log.error("Equalizer APO : configuration non écrite (%s)", exc)
            self.command_failed.emit(
                f"Configuration d'Equalizer APO non écrite ({exc.strerror or exc}). "
                "Son dossier doit être modifiable par ton compte : réinstalle Equalizer APO "
                "ou donne-toi les droits sur son dossier « config ».")
