"""
Volume par application, sur les entrées virtuelles de Voicemeeter.

Les applications (Discord, Spotify, un jeu…) jouent sur les périphériques
Windows « Voicemeeter Input », « Voicemeeter AUX Input », « Voicemeeter VAIO3
Input » : ce sont les strips virtuelles. Chaque application y a une session
audio Windows, avec son volume et son mute (les mêmes que dans le mélangeur de
volume de Windows) et un niveau de crête. On les lit et on les règle ici.

L'API Remote de Voicemeeter (Strip[i].AppGain) ne convient pas : écriture
seule, sans liste des applications, et réservée à Potato.

Trois moteurs, même interface :
- WindowsAppVolumes : le vrai (Core Audio via pycaw / comtypes, Windows) ;
- DemoAppVolumes    : applications simulées (--demo, --selftest, tests) ;
- NullAppVolumes    : indisponible (autre système, pycaw absent).

Les appels COM se font dans le thread de l'interface (là où COM est initialisé).
"""

from __future__ import annotations

import logging
import math
import time
import warnings
from dataclasses import dataclass, replace
from pathlib import Path

log = logging.getLogger(__name__)

SYSTEM_KEY = "system"
SYSTEM_NAME = "Sons système"
DEVICE_REFRESH_S = 10.0       # nouvelle recherche des périphériques au plus toutes les 10 s
# Mots-clés des périphériques Windows de chaque entrée virtuelle (VAIO, AUX, VAIO3),
# dans le nom complet, ex. « Voicemeeter AUX Input (VB-Audio Voicemeeter AUX VAIO) »
VIRTUAL_INPUT_NAMES = ("Voicemeeter Input", "Voicemeeter AUX Input", "Voicemeeter VAIO3 Input")


@dataclass(frozen=True)
class AppInfo:
    key: str              # exe en minuscules (« discord.exe »), ou « system »
    name: str             # nom affiché (« Discord »)
    exe: str              # chemin de l'exécutable (icône), ou ""
    volume: float         # 0 → 1, comme le mélangeur de volume de Windows
    muted: bool
    peak: float           # crête 0 → 1
    active: bool          # en train de jouer (session active)


def match_device(name: str, index: int, overrides: list[str] | None = None) -> bool:
    """Le périphérique Windows `name` est-il celui de l'entrée virtuelle `index`
    (0 = VAIO, 1 = AUX, 2 = VAIO3) ? `overrides` (config voicemeeter.app_devices)
    donne, par entrée, un morceau de nom à chercher à la place."""
    lower = name.lower()
    if overrides and index < len(overrides) and overrides[index]:
        return overrides[index].lower() in lower
    if "voicemeeter" not in lower:
        return False
    if index == 2:
        return "vaio3" in lower
    if index == 1:
        return "aux" in lower
    if index == 0:
        return "aux" not in lower and "vaio3" not in lower and ("vaio" in lower or "input" in lower)
    return False


def display_name(exe: str, fallback: str) -> str:
    """Nom lisible d'une application : description de l'exe (« Google Chrome »),
    sinon son nom de fichier."""
    if exe:
        try:
            import win32api   # pywin32, Windows seulement

            translations = win32api.GetFileVersionInfo(exe, "\\VarFileInfo\\Translation")
            if translations:
                lang, codepage = translations[0]
                description = win32api.GetFileVersionInfo(
                    exe, f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\FileDescription")
                if description and description.strip():
                    return description.strip()
        except Exception:
            pass
    stem = Path(fallback or exe).stem
    return stem[:1].upper() + stem[1:] if stem else "?"


class AppVolumeBackend:
    """Interface commune : `index` = entrée virtuelle (0 VAIO, 1 AUX, 2 VAIO3)."""

    available = False
    reason = ""

    def device_name(self, index: int) -> str | None:
        return None

    def apps(self, index: int) -> list[AppInfo]:
        return []

    def set_volume(self, index: int, key: str, volume: float) -> None:
        pass

    def set_mute(self, index: int, key: str, muted: bool) -> None:
        pass


class NullAppVolumes(AppVolumeBackend):
    def __init__(self, reason: str = "Disponible sous Windows seulement.") -> None:
        self.reason = reason


class DemoAppVolumes(AppVolumeBackend):
    """Applications simulées, avec des niveaux animés."""

    available = True
    APPS = {
        0: [("discord.exe", "Discord", 0.85), ("chrome.exe", "Google Chrome", 0.6)],
        1: [("spotify.exe", "Spotify", 0.45), (SYSTEM_KEY, SYSTEM_NAME, 1.0)],
        2: [],
    }

    def __init__(self) -> None:
        self._t0 = time.monotonic()
        self._apps: dict[int, list[AppInfo]] = {
            index: [AppInfo(key, name, "", volume, False, 0.0, key != SYSTEM_KEY)
                    for key, name, volume in entries]
            for index, entries in self.APPS.items()
        }

    def device_name(self, index: int) -> str | None:
        return f"{VIRTUAL_INPUT_NAMES[index]} (démo)" if index in self._apps else None

    def apps(self, index: int) -> list[AppInfo]:
        t = time.monotonic() - self._t0
        result = []
        for n, app in enumerate(self._apps.get(index, [])):
            level = 0.0 if app.muted or not app.active else \
                app.volume * (0.55 + 0.4 * math.sin(t * (2.1 + n) + n))
            result.append(replace(app, peak=max(0.0, min(1.0, level))))
        return result

    def set_volume(self, index: int, key: str, volume: float) -> None:
        self._update(index, key, volume=max(0.0, min(1.0, volume)))

    def set_mute(self, index: int, key: str, muted: bool) -> None:
        self._update(index, key, muted=bool(muted))

    def _update(self, index: int, key: str, **changes) -> None:
        apps = self._apps.get(index, [])
        for n, app in enumerate(apps):
            if app.key == key:
                apps[n] = replace(app, **changes)


class WindowsAppVolumes(AppVolumeBackend):
    """Sessions audio Windows (Core Audio) des entrées virtuelles de Voicemeeter."""

    available = True

    def __init__(self, overrides: list[str] | None = None) -> None:
        # Import ici, dans le thread de l'interface : comtypes y initialise COM
        from pycaw.api.audioclient import ISimpleAudioVolume
        from pycaw.api.audiopolicy import IAudioSessionControl2
        from pycaw.api.endpointvolume import IAudioMeterInformation
        from pycaw.constants import DEVICE_STATE, AudioSessionState, EDataFlow
        from pycaw.utils import AudioUtilities

        self._utilities = AudioUtilities
        self._control2 = IAudioSessionControl2
        self._simple_volume = ISimpleAudioVolume
        self._meter = IAudioMeterInformation
        self._render = EDataFlow.eRender.value
        self._active_devices = DEVICE_STATE.ACTIVE.value
        self._expired = AudioSessionState.Expired
        self._active_state = AudioSessionState.Active
        self._overrides = list(overrides or [])
        self._devices: dict[int, object] = {}       # index → AudioDevice (pycaw)
        self._devices_at = 0.0
        self._controls: dict[tuple[int, str], list] = {}   # (index, clé) → ISimpleAudioVolume
        self._names: dict[int, tuple[str, str, str]] = {}   # pid → (clé, nom, exe)
        self._find_devices()

    # ------------------------------------------------------------ périphériques
    def _find_devices(self) -> None:
        self._devices_at = time.monotonic()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")      # propriétés illisibles : sans importance ici
            devices = self._utilities.GetAllDevices(self._render, self._active_devices)
        found = {}
        for device in devices:
            name = str(device.FriendlyName or "")
            for index in range(len(VIRTUAL_INPUT_NAMES)):
                if index not in found and match_device(name, index, self._overrides):
                    found[index] = device
        if set(found) != set(self._devices):
            log.info("Volume par application : %s",
                     ", ".join(f"{i} = {d.FriendlyName}" for i, d in sorted(found.items()))
                     or "aucune entrée virtuelle de Voicemeeter trouvée")
        self._devices = found

    def _device(self, index: int):
        device = self._devices.get(index)
        if device is None and time.monotonic() - self._devices_at > DEVICE_REFRESH_S:
            self._find_devices()
            device = self._devices.get(index)
        return device

    def device_name(self, index: int) -> str | None:
        device = self._device(index)
        return str(device.FriendlyName) if device is not None else None

    # ---------------------------------------------------------------- sessions
    def apps(self, index: int) -> list[AppInfo]:
        device = self._device(index)
        if device is None:
            return []
        try:
            enumerator = device.AudioSessionManager.GetSessionEnumerator()
            count = enumerator.GetCount()
        except Exception as exc:          # périphérique débranché, service audio arrêté…
            log.debug("Sessions illisibles sur l'entrée %d : %s", index, exc)
            self._devices.pop(index, None)
            return []
        groups: dict[str, dict] = {}
        controls: dict[str, list] = {}
        for n in range(count):
            try:
                session = enumerator.GetSession(n).QueryInterface(self._control2)
                state = session.GetState()
                if state == self._expired:
                    continue
                key, name, exe = self._describe(session.GetProcessId())
                volume = session.QueryInterface(self._simple_volume)
                level, muted = float(volume.GetMasterVolume()), bool(volume.GetMute())
                try:
                    peak = float(session.QueryInterface(self._meter).GetPeakValue())
                except Exception:
                    peak = 0.0
            except Exception as exc:
                log.debug("Session audio ignorée : %s", exc)
                continue
            group = groups.setdefault(key, {"name": name, "exe": exe, "volume": level,
                                            "muted": True, "peak": 0.0, "active": False})
            group["volume"] = max(group["volume"], level)
            group["muted"] = group["muted"] and muted        # muet si toutes ses sessions le sont
            group["peak"] = max(group["peak"], peak)
            group["active"] = group["active"] or state == self._active_state
            controls.setdefault(key, []).append(volume)
        for key in [k for k in self._controls if k[0] == index]:
            del self._controls[key]
        self._controls.update({(index, key): ctrls for key, ctrls in controls.items()})
        apps = [AppInfo(key, g["name"], g["exe"], g["volume"], g["muted"], g["peak"], g["active"])
                for key, g in groups.items()]
        apps.sort(key=lambda app: (app.key == SYSTEM_KEY, app.name.lower()))
        return apps

    def _describe(self, pid: int) -> tuple[str, str, str]:
        if pid == 0:
            return SYSTEM_KEY, SYSTEM_NAME, ""
        cached = self._names.get(pid)
        if cached is not None:
            return cached
        import psutil

        exe, base = "", f"pid {pid}"
        try:
            process = psutil.Process(pid)
            base = process.name()
            try:
                exe = process.exe()
            except psutil.Error:
                exe = ""          # processus protégé : pas de chemin, pas d'icône
        except psutil.Error:
            pass
        described = ((exe or base).lower().rsplit("\\", 1)[-1], display_name(exe, base), exe)
        if len(self._names) > 512:
            self._names.clear()
        self._names[pid] = described
        return described

    def set_volume(self, index: int, key: str, volume: float) -> None:
        for control in self._controls.get((index, key), []):
            try:
                control.SetMasterVolume(max(0.0, min(1.0, float(volume))), None)
            except Exception as exc:     # session terminée entre-temps
                log.debug("Volume de %s non réglé : %s", key, exc)

    def set_mute(self, index: int, key: str, muted: bool) -> None:
        for control in self._controls.get((index, key), []):
            try:
                control.SetMute(1 if muted else 0, None)
            except Exception as exc:
                log.debug("Mute de %s non réglé : %s", key, exc)


def create_backend(cfg: dict) -> AppVolumeBackend:
    """Le moteur réel sous Windows, sinon un moteur indisponible (jamais d'exception)."""
    import sys

    if sys.platform != "win32":
        return NullAppVolumes()
    overrides = cfg.get("voicemeeter", {}).get("app_devices") or []
    try:
        return WindowsAppVolumes([str(o) for o in overrides])
    except Exception as exc:
        log.warning("Volume par application indisponible : %s", exc)
        return NullAppVolumes(f"Indisponible : {exc}")
