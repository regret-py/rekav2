"""
Gestion des processus Voicemeeter et REAPER : recherche, lancement, masquage
et arrêt.

L'utilisateur ne doit voir que REKA : les deux moteurs tournent avec
leur fenêtre principale masquée (ShowWindow SW_HIDE). Un chien de garde
re-masque ces fenêtres si un moteur les ré-affiche de lui-même. Seules les
fenêtres *principales* sont visées : les fenêtres de plugins VST ouvertes
depuis l'app restent visibles.

Qui arrête quoi à la fermeture :
  - un processus lancé par REKA (ou resté orphelin après un plantage,
    reconnu grâce à processes.json) est arrêté si terminate_on_exit = true ;
  - un Voicemeeter déjà ouvert par l'utilisateur est seulement « emprunté » :
    masqué pendant la session, puis ré-affiché en partant ;
  - un REAPER déjà ouvert par l'utilisateur est refusé (son projet perso
    serait piloté à sa place).

Test manuel (depuis le dossier reaper_mixer/) :
    python -m core.process_manager
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

import psutil

from core import config
from core.voicemeeter_ctrl import EDITIONS, edition, strip_channels

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes
    import winreg
    from ctypes import wintypes

    import pywintypes
    import win32con
    import win32gui
    import win32process

    # ShowWindowAsync ne bloque jamais, même si le processus cible est figé
    # (ShowWindow attendrait sa réponse). Instance user32 dédiée pour ne pas
    # toucher aux argtypes globaux de ctypes.windll.
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.ShowWindowAsync.argtypes = (wintypes.HWND, ctypes.c_int)
    _user32.ShowWindowAsync.restype = wintypes.BOOL

log = logging.getLogger(__name__)

POLL_INTERVAL_S = 0.05         # scrutation des fenêtres pendant un lancement
SETTLE_S = 3.0                 # re-masquage rapide juste après l'apparition
WATCHDOG_INTERVAL_S = 0.25     # période du chien de garde (re-masquage, crash)
KILL_WAIT_S = 3.0              # attente après un arrêt forcé
DEFAULT_WINDOW_TIMEOUT_S = 30.0
DEFAULT_CLOSE_TIMEOUT_S = 8.0
STATE_FILENAME = "processes.json"
SESSION_PROJECT_NAME = "session.rpp"


# --------------------------------------------------------------------------- #
# Description des applications pilotées
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class AppSpec:
    key: str                       # section correspondante dans config.json
    name: str                      # nom affiché à l'utilisateur
    exe_names: tuple[str, ...]     # par ordre de préférence (64 bits d'abord)
    install_dirs: tuple[str, ...]  # dossiers standards (%VARIABLES% développées)
    registry: tuple[tuple[str, str, str], ...]  # (ruche, clé, valeur) → dossier d'install
    window_classes: tuple[str, ...]  # classes Win32 de la fenêtre principale
    window_title: str              # repli : fragment du titre (insensible à la casse)
    download_url: str
    attach_foreign: bool           # accepter une instance ouverte par l'utilisateur ?


# Un exécutable par édition de Voicemeeter (Potato ≠ voicemeeterpro.exe,
# qui est l'exécutable de Banana).
_VOICEMEETER_EXES = {
    "basic": ("voicemeeter_x64.exe", "voicemeeter.exe"),
    "banana": ("voicemeeterpro_x64.exe", "voicemeeterpro.exe"),
    "potato": ("voicemeeter8x64.exe", "voicemeeter8.exe"),
}
_VOICEMEETER_URLS = {
    "basic": "https://vb-audio.com/Voicemeeter/index.htm",
    "banana": "https://vb-audio.com/Voicemeeter/banana.htm",
    "potato": "https://vb-audio.com/Voicemeeter/potato.htm",
}
# Clé de désinstallation commune aux trois éditions (la même que voicemeeterlib)
_VOICEMEETER_UNINSTALL_KEY = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    r"\VB:Voicemeeter {17359A74-1236-5467}"
)


def running_voicemeeter_kind() -> str | None:
    """Édition de Voicemeeter en cours d'exécution (basic, banana, potato), ou None."""
    kinds = {exe.lower(): kind for kind, exes in _VOICEMEETER_EXES.items() for exe in exes}
    for proc in psutil.process_iter(["name"]):
        kind = kinds.get((proc.info.get("name") or "").lower())
        if kind is not None:
            return kind
    return None


def installed_voicemeeter_kinds(configured: str | None = None) -> list[str]:
    """Éditions gérées par REKA (Potato, Banana) installées sur ce PC."""
    return [kind for kind in EDITIONS
            if find_executable(voicemeeter_spec(kind), configured) is not None]


def voicemeeter_spec(kind: str) -> AppSpec:
    kind = (kind or "potato").lower()
    if kind not in _VOICEMEETER_EXES:
        log.warning("Édition Voicemeeter inconnue « %s », utilisation de potato.", kind)
        kind = "potato"
    return AppSpec(
        key="voicemeeter",
        name=f"Voicemeeter {kind.capitalize()}",
        exe_names=_VOICEMEETER_EXES[kind],
        install_dirs=(
            r"%ProgramFiles(x86)%\VB\Voicemeeter",
            r"%ProgramFiles%\VB\Voicemeeter",
        ),
        registry=(("HKLM", _VOICEMEETER_UNINSTALL_KEY, "UninstallString"),),
        window_classes=("VBCABLE0Voicemeeter0MainQSF",),
        window_title="voicemeeter",
        download_url=_VOICEMEETER_URLS[kind],
        attach_foreign=True,
    )


REAPER_SPEC = AppSpec(
    key="reaper",
    name="REAPER",
    exe_names=("reaper.exe",),
    install_dirs=(
        r"%ProgramW6432%\REAPER (x64)",
        r"%ProgramFiles%\REAPER (x64)",
        r"%ProgramFiles%\REAPER",
        r"%ProgramFiles(x86)%\REAPER",
    ),
    registry=(
        ("HKLM", r"SOFTWARE\REAPER", ""),
        ("HKCU", r"SOFTWARE\REAPER", ""),
        ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\REAPER",
         "UninstallString"),
    ),
    window_classes=("REAPERwnd",),
    # Titre type « session.rpp - REAPER v7.xx/x64 » ; volontairement précis pour
    # ne pas viser d'autres fenêtres de REAPER.
    window_title="- reaper v",
    download_url="https://www.reaper.fm/download.php",
    attach_foreign=False,
)


FLSTUDIO_SPEC = AppSpec(
    key="flstudio",
    name="FL Studio",
    exe_names=("FL64.exe", "FL.exe"),
    # Un dossier par version (« FL Studio 21 », « FL Studio 2024 ») : le plus récent d'abord
    install_dirs=(
        r"%ProgramW6432%\Image-Line\FL Studio*",
        r"%ProgramFiles%\Image-Line\FL Studio*",
        r"%ProgramFiles(x86)%\Image-Line\FL Studio*",
    ),
    registry=(),
    window_classes=("TFruityLoopsMainForm",),
    window_title="fl studio",
    download_url="https://www.image-line.com/fl-studio-download/",
    attach_foreign=True,
)

APP_SPECS = {"reaper": REAPER_SPEC, "flstudio": FLSTUDIO_SPEC}


# --------------------------------------------------------------------------- #
# Erreurs
# --------------------------------------------------------------------------- #
class ProcessManagerError(Exception):
    """Erreur de démarrage présentable telle quelle à l'utilisateur."""

    hint: str = ""


class AppNotInstalledError(ProcessManagerError):
    def __init__(self, spec: AppSpec) -> None:
        super().__init__(f"{spec.name} est introuvable sur ce PC.")
        self.app_name = spec.name
        self.config_key = spec.key
        self.exe_name = spec.exe_names[0]
        self.download_url = spec.download_url


class ProcessConflictError(ProcessManagerError):
    def __init__(self, spec: AppSpec, pid: int) -> None:
        super().__init__(f"{spec.name} est déjà ouvert en dehors de REKA (PID {pid}).")
        self.hint = f"Ferme {spec.name}, puis clique sur « Réessayer »."


class ProcessStartError(ProcessManagerError):
    pass


class OtherEditionRunningError(ProcessManagerError):
    """Une autre édition de Voicemeeter tourne déjà : REKA piloterait les
    mauvaises strips (les index ne sont pas les mêmes d'une édition à l'autre)."""

    def __init__(self, running_kind: str, wanted_kind: str) -> None:
        running, wanted = running_kind.capitalize(), wanted_kind.capitalize()
        super().__init__(f"Voicemeeter {running} est ouvert, mais REKA est réglé sur "
                         f"Voicemeeter {wanted}.")
        self.running_kind = running_kind
        self.wanted_kind = wanted_kind
        self.hint = (f"Ferme Voicemeeter {running} puis clique sur « Réessayer », "
                     f"ou passe REKA sur {running}.")


class StartupAborted(Exception):
    """Le démarrage a été interrompu (fermeture de l'app pendant le lancement)."""


# --------------------------------------------------------------------------- #
# Projet REAPER
# --------------------------------------------------------------------------- #
def blank_project_path(cfg: dict) -> Path:
    """Fichier du projet vierge de l'édition de Voicemeeter choisie (les canaux
    d'insert ne sont pas les mêmes sur Potato et Banana)."""
    kind = edition(cfg).kind
    name = SESSION_PROJECT_NAME if kind == "potato" else f"session-{kind}.rpp"
    return config.app_data_dir() / name


def is_blank_project(path: str | Path) -> bool:
    """Projet vierge de REKA : session.rpp / session-<édition>.rpp de son
    dossier de données (un projet perso nommé « session… » ailleurs n'en est pas un)."""
    path = Path(path)
    names = {SESSION_PROJECT_NAME} | {f"session-{kind}.rpp" for kind in EDITIONS}
    if path.name.lower() not in names:
        return False
    return os.path.normcase(os.path.abspath(path.parent)) == \
        os.path.normcase(os.path.abspath(config.app_data_dir()))


def _rpp_string(text: str) -> str:
    """Chaîne au format .rpp : guillemets si besoin, avec le délimiteur libre."""
    if text and not any(c.isspace() for c in text) and not any(q in text for q in "\"'`"):
        return text
    for quote in ('"', "'", "`"):
        if quote not in text:
            return f"{quote}{text}{quote}"
    return '"' + text.replace('"', "'") + '"'


def build_blank_project(names: list[str], channels: list[int]) -> str:
    """Projet REAPER vierge : une piste par strip, entrée stéréo sur son canal
    d'insert, écoute de l'entrée sans enregistrement, sortie sur la même paire,
    pas d'envoi au master, aucun effet."""
    lines = ['<REAPER_PROJECT 0.1 "7.0/win64" 0', "  RIPPLE 0", "  MASTER_NCH 2 2",
             "  MASTER_VOLUME 1 0 -1 -1 1", "  MASTERMUTESOLO 0"]
    for name, channel in zip(names, channels):
        lines += ["  <TRACK", f"    NAME {_rpp_string(name)}",
                  f"    REC 1 {1024 + channel} 1 2 0 0 0 0", "    MAINSEND 0 0",
                  f"    HWOUT {channel} 0 1 0 0 0 0 -1:U -1", "  >"]
    lines.append(">")
    return "\n".join(lines) + "\n"


def blank_project(cfg: dict) -> Path | None:
    """Projet vierge de REKA (une piste câblée par strip, aucun effet) : créé au
    premier besoin, puis réutilisé (ses effets sont conservés). Un template
    personnalisé (reaper.template) est copié à la place s'il est défini."""
    project = blank_project_path(cfg)
    if project.is_file():
        return project
    project.parent.mkdir(parents=True, exist_ok=True)
    template = config.resolve_path(cfg.get("reaper", {}).get("template"))
    if template is not None and template.is_file():
        shutil.copyfile(template, project)
        log.info("Projet vierge créé depuis le template %s : %s", template, project)
        return project
    channels = strip_channels(cfg)
    names = list(cfg.get("ui", {}).get("strip_names", []))
    names += [f"Strip {i + 1}" for i in range(len(names), len(channels))]
    project.write_text(build_blank_project(names[:len(channels)], channels), encoding="utf-8")
    log.info("Projet vierge créé (%s) : %s", edition(cfg).label, project)
    return project


def reaper_project(cfg: dict, choice: str | None = None) -> Path | None:
    """Projet à ouvrir : `choice` s'il est donné, sinon le projet par défaut
    (reaper.project). Vide = projet vierge de REKA. Un projet introuvable
    (déplacé, supprimé) est remplacé par le projet vierge."""
    wanted = cfg.get("reaper", {}).get("project", "") if choice is None else choice
    path = config.resolve_path(wanted, base=config.app_data_dir())
    if path is not None:
        if path.is_file():
            return path
        log.warning("Projet REAPER introuvable (%s) : ouverture du projet vierge.", path)
    return blank_project(cfg)


# --------------------------------------------------------------------------- #
# Recherche des exécutables
# --------------------------------------------------------------------------- #
def find_executable(spec: AppSpec, configured: str | None = None) -> Path | None:
    """Trouve l'exécutable, dans l'ordre : chemin de config.json → processus
    déjà lancé → registre Windows → dossiers d'installation standards → PATH."""
    for source, candidate in _executable_candidates(spec, configured):
        if candidate.is_file():
            log.info("%s trouvé (%s) : %s", spec.name, source, candidate)
            return candidate
    return None


def _executable_candidates(spec: AppSpec, configured: str | None) -> Iterator[tuple[str, Path]]:
    # 1. Chemin configuré : fichier exe, ou dossier qui le contient. Un exe d'une
    #    autre édition (Potato mémorisé, Banana choisie) donne le dossier
    #    d'installation, où l'on cherche l'exe de l'édition voulue.
    path = config.resolve_path(configured)
    if path is not None:
        wanted_names = {exe.lower() for exe in spec.exe_names}
        if path.is_dir():
            yield from (("config.json", path / exe) for exe in spec.exe_names)
        elif path.name.lower() in wanted_names:
            yield "config.json", path
        else:
            yield from (("config.json", path.parent / exe) for exe in spec.exe_names)
        log.warning("%s : chemin configuré invalide (%s), recherche automatique.",
                    spec.name, path)

    # 2. Instance déjà lancée (couvre aussi les installations portables)
    wanted = {exe.lower() for exe in spec.exe_names}
    for proc in psutil.process_iter(["name", "exe"]):
        name, exe = proc.info.get("name"), proc.info.get("exe")
        if name and exe and name.lower() in wanted:
            yield "processus en cours", Path(exe)

    # 3. Registre (installeurs officiels)
    for hive, key, value in spec.registry:
        folder = _registry_install_dir(hive, key, value)
        if folder is not None:
            yield from (("registre", folder / exe) for exe in spec.exe_names)

    # 4. Dossiers d'installation standards (« * » : un dossier par version, le plus récent d'abord)
    for folder in spec.install_dirs:
        base = config.resolve_path(folder)
        if base is None:
            continue
        if "*" in base.name:
            try:
                matches = sorted(base.parent.glob(base.name), key=_mtime, reverse=True)
            except OSError:
                matches = []
        else:
            matches = [base]
        for match in matches:
            yield from (("dossier standard", match / exe) for exe in spec.exe_names)

    # 5. PATH système
    for exe in spec.exe_names:
        found = shutil.which(exe)
        if found:
            yield "PATH", Path(found)


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _registry_install_dir(hive: str, key: str, value: str) -> Path | None:
    """Lit une valeur du registre (vues 64 et 32 bits) et en déduit un dossier.

    Accepte un dossier, un exe, ou une ligne de commande de désinstallation
    du type « "C:\\...\\uninstall.exe" /S »."""
    if not IS_WINDOWS:
        return None
    root = {"HKLM": winreg.HKEY_LOCAL_MACHINE, "HKCU": winreg.HKEY_CURRENT_USER}[hive]
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(root, key, 0, winreg.KEY_READ | view) as handle:
                data, _ = winreg.QueryValueEx(handle, value)
        except OSError:
            continue
        if not isinstance(data, str) or not data.strip():
            continue
        raw = data.strip()
        if raw.startswith('"'):
            raw = raw[1:].split('"', 1)[0]
        exe_end = raw.lower().find(".exe")
        if exe_end >= 0:
            return Path(raw[: exe_end + 4]).parent
        return Path(raw)
    return None


# --------------------------------------------------------------------------- #
# Fenêtres Win32
# --------------------------------------------------------------------------- #
def _top_level_windows(pids: set[int]) -> dict[int, list[int]]:
    """Fenêtres de premier niveau (visibles ou non) des processus donnés."""
    found: dict[int, list[int]] = {}

    def collect(hwnd: int, _extra: object) -> bool:
        _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid in pids:
            found.setdefault(pid, []).append(hwnd)
        return True  # pywin32 lève une erreur si le callback renvoie False

    win32gui.EnumWindows(collect, None)
    return found


def _pick_main_windows(hwnds: list[int], spec: AppSpec) -> list[int]:
    """Garde les fenêtres principales : sans propriétaire (≠ dialogues, fenêtres
    de plugins) et reconnues par leur classe Win32. Le titre ne sert que de
    repli si aucune classe ne correspond, pour ne jamais masquer par erreur
    une boîte de dialogue (licence, etc.) qui attend l'utilisateur."""
    by_class: list[int] = []
    by_title: list[int] = []
    for hwnd in hwnds:
        try:
            if win32gui.GetWindow(hwnd, win32con.GW_OWNER):
                continue
            if win32gui.GetClassName(hwnd) in spec.window_classes:
                by_class.append(hwnd)
            elif spec.window_title and spec.window_title in win32gui.GetWindowText(hwnd).lower():
                by_title.append(hwnd)
        except pywintypes.error:
            continue  # fenêtre détruite entre-temps
    return by_class or by_title


def _set_windows_visible(hwnds: list[int], visible: bool) -> None:
    command = win32con.SW_SHOW if visible else win32con.SW_HIDE
    for hwnd in hwnds:
        try:
            if bool(win32gui.IsWindowVisible(hwnd)) != visible:
                _user32.ShowWindowAsync(hwnd, command)
        except pywintypes.error:
            pass


# --------------------------------------------------------------------------- #
# Processus pilotés
# --------------------------------------------------------------------------- #
@dataclass
class ManagedProcess:
    spec: AppSpec
    process: psutil.Process
    launched_by_us: bool
    popen: subprocess.Popen | None = None  # gardé pour éviter un ResourceWarning
    keep_hidden: bool = True
    exit_reported: bool = False

    @property
    def pid(self) -> int:
        return self.process.pid

    def is_alive(self) -> bool:
        try:
            if not self.process.is_running():
                return False
            # Pas de zombies sous Windows ; ailleurs (dev) on les écarte
            return IS_WINDOWS or self.process.status() != psutil.STATUS_ZOMBIE
        except psutil.AccessDenied:
            return True  # processus élevé : il existe, on ne peut juste pas l'inspecter
        except psutil.Error:
            return False

    def main_windows(self) -> list[int]:
        if not IS_WINDOWS:
            return []
        return _pick_main_windows(_top_level_windows({self.pid}).get(self.pid, []), self.spec)


class ProcessManager:
    """Lance, masque, surveille et arrête Voicemeeter et REAPER.

    Les méthodes start_* sont bloquantes (attente des fenêtres) : à appeler
    depuis un thread de travail, jamais depuis le thread UI. Elles sont
    idempotentes : un second appel ne relance pas un processus vivant.
    """

    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg
        self._procs: dict[str, ManagedProcess] = {}
        self._lock = threading.RLock()
        self._abort = threading.Event()
        self._watchdog_stop = threading.Event()
        self._watchdog: threading.Thread | None = None
        self._state_path = config.app_data_dir() / STATE_FILENAME

    # ----------------------------------------------------------- API publique
    def start_voicemeeter(self) -> None:
        kind = edition(self._cfg).kind
        current = self._get("voicemeeter")
        if current is None or not current.is_alive():
            running = running_voicemeeter_kind()
            if running is not None and running != kind:
                raise OtherEditionRunningError(running, kind)
        self._start(voicemeeter_spec(kind), args=[])

    def start_reaper(self, prepare: Callable[[Path], dict[str, str]] | None = None,
                     project: str | None = None) -> bool:
        """Lance REAPER sur un projet : `project` s'il est donné ("" = projet
        vierge de REKA), sinon le projet par défaut de la config.

        `prepare(exe)` est appelé avant le lancement avec le chemin de reaper.exe
        (installation du pont Lua) et renvoie des variables d'environnement à
        transmettre au processus.

        Renvoie True si un REAPER déjà lancé a été repris : il n'a pas reçu le
        projet en argument, il faudra l'ouvrir une fois le pont connecté."""
        path = reaper_project(self._cfg, project)
        args = ["-nosplash"] + ([str(path)] if path else [])
        return self._start(REAPER_SPEC, args=args, prepare=prepare)

    def start_flstudio(self) -> None:
        """Lance FL Studio (masqué), sur le projet flstudio.project s'il existe."""
        project = config.resolve_path(self._section("flstudio").get("project"))
        args = [str(project)] if project is not None and project.is_file() else []
        self._start(FLSTUDIO_SPEC, args=args)

    def executable(self, key: str) -> Path | None:
        """Chemin de l'exécutable connu pour un moteur (après un start_*)."""
        path = config.resolve_path(self._section(key).get("path"))
        return path if path is not None and path.is_file() else None

    def focus_window(self, key: str, title_contains: str) -> bool:
        """Met au premier plan une fenêtre secondaire visible d'un moteur dont le
        titre contient `title_contains` (ex. l'interface d'un plugin VST, qui
        s'ouvrirait sinon derrière la console)."""
        managed = self._get(key)
        if not IS_WINDOWS or managed is None or not managed.is_alive():
            return False
        needle = title_contains.lower()
        mains = set(managed.main_windows())
        for hwnd in _top_level_windows({managed.pid}).get(managed.pid, []):
            try:
                if (hwnd in mains or not win32gui.IsWindowVisible(hwnd)
                        or needle not in win32gui.GetWindowText(hwnd).lower()):
                    continue
                win32gui.SetForegroundWindow(hwnd)
                return True
            except pywintypes.error as exc:
                log.debug("Mise au premier plan impossible : %s", exc)
        return False

    def abort_startup(self) -> None:
        """Interrompt un start_* en cours (lève StartupAborted dans son thread)."""
        self._abort.set()

    def is_running(self, key: str) -> bool:
        managed = self._get(key)
        return managed is not None and managed.is_alive()

    def will_terminate(self, key: str) -> bool:
        """True si shutdown() arrêtera ce processus : les contrôleurs peuvent
        alors demander un arrêt propre au moteur avant (sauvegarde, etc.).
        FL Studio reste ouvert par défaut : son projet n'est pas sauvegardé
        par REKA, une fermeture forcée perdrait les réglages des effets."""
        managed = self._get(key)
        return bool(
            managed is not None
            and managed.launched_by_us
            and self._section(key).get("terminate_on_exit", key != "flstudio")
        )

    def set_window_visible(self, key: str, visible: bool) -> None:
        """Affiche ou re-masque la fenêtre principale d'un moteur (ex. pour
        régler la carte son de REAPER depuis les settings)."""
        managed = self._get(key)
        if managed is None or not managed.is_alive():
            return
        managed.keep_hidden = not visible
        if IS_WINDOWS:
            _set_windows_visible(managed.main_windows(), visible)
        log.info("%s : fenêtre %s.", managed.spec.name, "affichée" if visible else "masquée")

    def bring_to_front(self, key: str) -> bool:
        """Affiche la fenêtre principale d'un moteur et la met au premier plan
        (FL Studio : ses effets se règlent dans sa propre fenêtre)."""
        managed = self._get(key)
        if managed is None or not managed.is_alive():
            return False
        self.set_window_visible(key, True)
        if not IS_WINDOWS:
            return True
        for hwnd in managed.main_windows():
            try:
                if win32gui.IsIconic(hwnd):
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                win32gui.SetForegroundWindow(hwnd)
                return True
            except pywintypes.error as exc:
                log.debug("Mise au premier plan impossible : %s", exc)
        return True

    def stop(self, key: str) -> None:
        """Arrête un moteur s'il est à nous (et terminate_on_exit), sinon le
        laisse tourner et ré-affiche sa fenêtre."""
        managed = self._get(key)
        if managed is None or not managed.is_alive():
            return
        if self.will_terminate(key):
            self._terminate(managed)
        else:
            managed.keep_hidden = False
            if IS_WINDOWS:
                _set_windows_visible(managed.main_windows(), True)
            log.info("%s laissé ouvert (PID %d), fenêtre ré-affichée.",
                     managed.spec.name, managed.pid)
        self._save_state()

    def shutdown(self) -> None:
        """Arrête ce qu'on a lancé, ré-affiche ce qu'on a emprunté."""
        self._abort.set()
        self._watchdog_stop.set()
        if self._watchdog is not None:
            self._watchdog.join(timeout=2.0)

        with self._lock:
            keys = list(self._procs)
        # Ordre inverse du lancement : REAPER d'abord, Voicemeeter ensuite.
        # Ne restent ensuite dans processes.json que nos processus encore vivants
        # (terminate_on_exit = false) : ils seront repris au prochain lancement.
        for key in reversed(keys):
            self.stop(key)
        self._save_state()

    # -------------------------------------------------------------- lancement
    def _start(self, spec: AppSpec, args: list[str],
               prepare: Callable[[Path], dict[str, str]] | None = None) -> bool:
        """Lance (ou reprend) une application ; True si une instance déjà
        ouverte a été reprise (elle n'a pas reçu `args`)."""
        if self._abort.is_set():
            raise StartupAborted()
        current = self._get(spec.key)
        if current is not None and current.is_alive():
            return True

        section = self._section(spec.key)
        exe = find_executable(spec, section.get("path"))
        if exe is None:
            raise AppNotInstalledError(spec)
        section["path"] = str(exe)  # mémorisé : plus de recherche au prochain lancement
        env = prepare(exe) if prepare is not None else {}

        adopted = self._adopt_running(spec)
        managed = adopted or self._launch(spec, exe, args, env)
        with self._lock:
            self._procs[spec.key] = managed
        self._save_state()

        timeout = float(section.get("window_timeout_s", DEFAULT_WINDOW_TIMEOUT_S))
        self._hide_when_ready(managed, timeout)
        self._ensure_watchdog()
        return adopted is not None

    def _adopt_running(self, spec: AppSpec) -> ManagedProcess | None:
        """Reprend une instance déjà ouverte, si on en a le droit."""
        wanted = {exe.lower() for exe in spec.exe_names}
        proc = next(
            (p for p in psutil.process_iter(["name"])
             if (p.info.get("name") or "").lower() in wanted),
            None,
        )
        if proc is None:
            return None
        if self._was_launched_by_us(spec.key, proc):
            log.info("%s (PID %d) restait d'une session précédente : repris.",
                     spec.name, proc.pid)
            return ManagedProcess(spec, proc, launched_by_us=True)
        if not spec.attach_foreign:
            raise ProcessConflictError(spec, proc.pid)
        log.info("%s déjà ouvert (PID %d) : utilisé tel quel, "
                 "il sera ré-affiché à la fermeture.", spec.name, proc.pid)
        return ManagedProcess(spec, proc, launched_by_us=False)

    def _launch(self, spec: AppSpec, exe: Path, args: list[str],
                env: dict[str, str]) -> ManagedProcess:
        cmd = [str(exe), *args]
        log.info("Lancement de %s : %s", spec.name, subprocess.list2cmdline(cmd))
        # Pas de STARTUPINFO « masqué » : Windows l'appliquerait à la toute
        # première fenêtre affichée, qui peut être une boîte de licence que
        # l'utilisateur doit voir. Le masquage se fait par scrutation.
        kwargs: dict = {
            "cwd": str(exe.parent),
            "env": {**os.environ, **env},
            # Flux explicites : en mode --windowed, PyInstaller n'a pas de
            # stdin/stdout valides à transmettre au processus enfant.
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        try:
            popen = subprocess.Popen(cmd, **kwargs)
            proc = psutil.Process(popen.pid)
        except (OSError, psutil.Error) as exc:
            raise ProcessStartError(f"Impossible de lancer {spec.name} : {exc}") from exc
        return ManagedProcess(spec, proc, launched_by_us=True, popen=popen)

    def _hide_when_ready(self, managed: ManagedProcess, timeout_s: float) -> None:
        """Attend l'apparition de la fenêtre principale puis la masque.

        Une appli crée souvent sa fenêtre cachée puis l'affiche un peu plus
        tard : on continue donc à la re-masquer rapidement pendant SETTLE_S,
        le chien de garde prenant ensuite le relais."""
        if not IS_WINDOWS:
            return
        name = managed.spec.name
        deadline = time.monotonic() + timeout_s
        settle_until: float | None = None
        while time.monotonic() < (settle_until or deadline):
            if not managed.is_alive():
                raise ProcessStartError(f"{name} s'est fermé juste après son lancement.")
            windows = managed.main_windows()
            if windows:
                if managed.keep_hidden:
                    _set_windows_visible(windows, False)
                if settle_until is None:
                    log.info("%s prêt (PID %d), fenêtre principale masquée.", name, managed.pid)
                    settle_until = time.monotonic() + SETTLE_S
            if self._abort.wait(POLL_INTERVAL_S):
                raise StartupAborted()
        if settle_until is not None:
            return
        # Pas bloquant : la vraie vérification est la connexion (API / pont) qui suit,
        # et le chien de garde masquera la fenêtre dès qu'elle apparaîtra.
        log.warning("%s : fenêtre principale introuvable après %.0f s.", name, timeout_s)

    # ---------------------------------------------------------- surveillance
    def _ensure_watchdog(self) -> None:
        with self._lock:
            if self._watchdog is not None and self._watchdog.is_alive():
                return
            self._watchdog_stop.clear()
            self._watchdog = threading.Thread(
                target=self._watchdog_loop, name="process-watchdog", daemon=True)
            self._watchdog.start()

    def _watchdog_loop(self) -> None:
        while not self._watchdog_stop.wait(WATCHDOG_INTERVAL_S):
            try:
                self._watchdog_tick()
            except Exception:  # le chien de garde ne doit jamais mourir
                log.exception("Erreur du chien de garde des processus")

    def _watchdog_tick(self) -> None:
        with self._lock:
            procs = list(self._procs.values())
        to_hide: list[ManagedProcess] = []
        for managed in procs:
            if managed.is_alive():
                if managed.keep_hidden:
                    to_hide.append(managed)
            elif not managed.exit_reported:
                managed.exit_reported = True
                log.error("%s (PID %d) s'est arrêté de manière inattendue.",
                          managed.spec.name, managed.pid)
        if IS_WINDOWS and to_hide:
            # Une seule énumération des fenêtres pour tous les processus
            windows = _top_level_windows({m.pid for m in to_hide})
            for managed in to_hide:
                mains = _pick_main_windows(windows.get(managed.pid, []), managed.spec)
                _set_windows_visible(mains, False)

    # ----------------------------------------------------------------- arrêt
    def _terminate(self, managed: ManagedProcess) -> None:
        """Fermeture polie (WM_CLOSE), puis arrêt forcé après close_timeout_s."""
        name = managed.spec.name
        timeout = float(self._section(managed.spec.key).get(
            "close_timeout_s", DEFAULT_CLOSE_TIMEOUT_S))
        if managed.spec.key == "flstudio" and IS_WINDOWS:
            # FL Studio peut demander d'enregistrer le projet : fenêtre visible
            managed.keep_hidden = False
            _set_windows_visible(managed.main_windows(), True)
        try:
            if IS_WINDOWS:
                for hwnd in managed.main_windows():
                    try:
                        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                    except pywintypes.error:
                        pass  # fenêtre déjà détruite
            else:
                managed.process.terminate()
            managed.process.wait(timeout=timeout)
            log.info("%s fermé proprement.", name)
            return
        except psutil.NoSuchProcess:
            return
        except psutil.TimeoutExpired:
            log.warning("%s ne répond pas après %.0f s : arrêt forcé.", name, timeout)
        except psutil.Error as exc:
            log.warning("Fermeture polie de %s impossible (%s) : arrêt forcé.", name, exc)
        try:
            managed.process.kill()
            managed.process.wait(timeout=KILL_WAIT_S)
        except psutil.NoSuchProcess:
            pass
        except psutil.Error as exc:
            log.error("Impossible d'arrêter %s (PID %d) : %s", name, managed.pid, exc)

    # ------------------------------------------------------------- utilitaires
    def _get(self, key: str) -> ManagedProcess | None:
        with self._lock:
            return self._procs.get(key)

    def _section(self, key: str) -> dict:
        return self._cfg.setdefault(key, {})

    def _was_launched_by_us(self, key: str, proc: psutil.Process) -> bool:
        """Un processus est « à nous » si son PID et son heure de création
        correspondent à ceux notés dans processes.json (le PID seul pourrait
        avoir été réattribué par Windows)."""
        entry = self._load_state().get(key)
        if not isinstance(entry, dict):
            return False
        try:
            return (proc.pid == int(entry["pid"])
                    and abs(proc.create_time() - float(entry["create_time"])) < 0.5)
        except (psutil.Error, KeyError, TypeError, ValueError):
            return False

    def _load_state(self) -> dict:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_state(self) -> None:
        """Mémorise les processus qu'on a lancés, pour reconnaître des orphelins
        si l'app plante et laisse les moteurs tourner en arrière-plan."""
        state = {}
        with self._lock:
            procs = list(self._procs.items())
        for key, managed in procs:
            # create_time() est mis en cache par psutil : on vérifie la vie à part
            if managed.launched_by_us and managed.is_alive():
                state[key] = {"pid": managed.pid, "create_time": managed.process.create_time()}
        try:
            self._state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except OSError as exc:
            log.warning("Impossible d'écrire %s : %s", self._state_path, exc)


# --------------------------------------------------------------------------- #
# Test manuel
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG,
                        format="%(asctime)s %(levelname)-7s %(name)s : %(message)s")
    manager = ProcessManager(config.load_config())
    try:
        manager.start_voicemeeter()
        manager.start_reaper()  # sans pont Lua : test du lancement seul
        input("\nVoicemeeter et REAPER tournent, masqués. [Entrée] pour les afficher… ")
        manager.set_window_visible("voicemeeter", True)
        manager.set_window_visible("reaper", True)
        input("[Entrée] pour tout fermer… ")
    except ProcessManagerError as error:
        print(f"\nERREUR : {error} {error.hint}")
    except KeyboardInterrupt:
        pass
    finally:
        manager.shutdown()
