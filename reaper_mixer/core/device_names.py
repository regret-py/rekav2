"""
Noms des périphériques Windows de Voicemeeter.

Voicemeeter crée ses périphériques Windows : les entrées virtuelles
(« Voicemeeter Input », « AUX Input », « VAIO3 Input », côté lecture) et les
sorties virtuelles (« Voicemeeter Out B1/B2/B3 », côté enregistrement, là où
Discord et OBS prennent le micro). REKA leur donne ses noms : « REKA insert »,
« REKA aux », « REKA VAIO3 », « REKA B1 », « REKA B2 », « REKA B3 ».
Les autres (« Voicemeeter In 1…5 », « Out A1…A5 ») gardent leur nom.

C'est le même renommage que Panneau de configuration › Son › Propriétés : le
périphérique ne change pas, les applications gardent leur réglage.

Reconnaître un périphérique : toutes les sorties et entrées de Voicemeeter
peuvent avoir le même pilote (« VB-Audio Voicemeeter VAIO »), qui ne suffit
donc pas. On se fie, dans l'ordre, à :
  1. son nom d'origine, donné par le pilote (nom de la broche, que le
     renommage ne change pas) ;
  2. son nom actuel, s'il est encore celui d'origine ;
  3. ce que REKA a noté en le renommant (devices.json).
Un périphérique non reconnu n'est jamais renommé, et deux périphériques
reconnus comme le même ne le sont pas non plus.

- Windows n'autorise le renommage qu'en administrateur : si REKA n'a pas le
  droit, il se relance une fois en administrateur avec --rename-devices (Windows
  demande l'autorisation), seulement quand un nom est à changer.
- Un périphérique au nom « REKA … » qui ne doit pas (ou plus) l'avoir reprend
  son nom d'origine (option désactivée, ou renommé par erreur).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from core import config

log = logging.getLogger(__name__)

PREFIX = "REKA "
MAX_NAME = 40
MEMORY_FILE = "devices.json"
# Nom d'origine de chaque périphérique renommé : (nom, capture) → clé
ORIGINAL_NAMES = {
    "in0": ("Voicemeeter Input", False),
    "in1": ("Voicemeeter AUX Input", False),
    "in2": ("Voicemeeter VAIO3 Input", False),
    "B1": ("Voicemeeter Out B1", True),
    "B2": ("Voicemeeter Out B2", True),
    "B3": ("Voicemeeter Out B3", True),
}
DEFAULT_NAMES = {
    "in0": "REKA insert", "in1": "REKA aux", "in2": "REKA VAIO3",
    "B1": "REKA B1", "B2": "REKA B2", "B3": "REKA B3",
}

# Propriétés Windows : nom court (celui qu'on renomme), nom complet, nom du pilote
PKEY_DEVICE_DESC = ("{a45c254e-df1c-4efd-8020-67d146a850e0}", 2)
PKEY_FRIENDLY_NAME = ("{a45c254e-df1c-4efd-8020-67d146a850e0}", 14)
PKEY_INTERFACE_NAME = ("{026e516e-b814-414b-83cd-856d6fef4822}", 2)
E_ACCESSDENIED = -2147024891          # 0x80070005
STGM_READWRITE = 2
CLSCTX_ALL = 23


@dataclass
class Endpoint:
    id: str           # identifiant Windows du périphérique
    capture: bool     # côté enregistrement (sorties B)
    name: str         # nom court actuel (« Voicemeeter Out B1 », « REKA B1 »…)
    original: str     # nom donné par le pilote (broche), "" si illisible
    interface: str    # nom du pilote (« VB-Audio Voicemeeter VAIO »)
    device: object = None   # IMMDevice (Windows)


def _key_of(name: str, capture: bool) -> str | None:
    lower = name.strip().lower()
    for key, (original, is_capture) in ORIGINAL_NAMES.items():
        if is_capture == capture and lower == original.lower():
            return key
    return None


def identify(endpoint: Endpoint, memory: dict[str, str]) -> str | None:
    """Clé du périphérique (in0…in2, B1…B3), ou None s'il n'en a pas ou s'il
    n'est pas reconnu avec certitude."""
    if endpoint.original:
        # Le pilote donne le nom d'origine : lui seul fait foi
        return _key_of(endpoint.original, endpoint.capture)
    return _key_of(endpoint.name, endpoint.capture) or memory.get(endpoint.id)


def enabled(cfg: dict) -> bool:
    return bool(cfg.get("voicemeeter", {}).get("rename_devices", True))


def wanted_names(cfg: dict) -> dict[str, str]:
    """Nom voulu pour chaque périphérique (voicemeeter.device_names peut les changer)."""
    custom = cfg.get("voicemeeter", {}).get("device_names", {}) or {}
    return {key: (str(custom.get(key) or "").strip() or default)[:MAX_NAME]
            for key, default in DEFAULT_NAMES.items()}


def plan(cfg: dict, endpoints: list[Endpoint],
         memory: dict[str, str]) -> tuple[list[tuple[Endpoint, str, str | None]], list[Endpoint]]:
    """(renommages à faire : (périphérique, nouveau nom, clé), périphériques au
    nom REKA dont on ne sait pas retrouver le nom d'origine)."""
    wanted = wanted_names(cfg) if enabled(cfg) else {}
    # Un nom de broche partagé par plusieurs périphériques n'identifie rien
    shared = Counter((e.original.lower(), e.capture) for e in endpoints if e.original)
    for endpoint in endpoints:
        if endpoint.original and shared[(endpoint.original.lower(), endpoint.capture)] > 1:
            endpoint.original = ""
    keys = {e.id: identify(e, memory) for e in endpoints}
    claimed = Counter(k for k in keys.values() if k)
    changes, lost = [], []
    for endpoint in endpoints:
        key = keys[endpoint.id]
        if key and claimed[key] > 1:
            log.warning("« %s » : plusieurs périphériques reconnus comme %s, aucun n'est renommé.",
                        endpoint.name, key)
            key = None
        if key and key in wanted:
            target = wanted[key]
        elif endpoint.name.startswith(PREFIX):
            # Nom REKA à retirer : on remet celui d'origine, si on le connaît
            target = endpoint.original or (ORIGINAL_NAMES[key][0] if key else "")
            if not target:
                lost.append(endpoint)
                continue
        else:
            continue
        if target != endpoint.name:
            changes.append((endpoint, target, key))
    return changes, lost


# --------------------------------------------------------------------------- #
# Mémoire des périphériques renommés (%APPDATA%/REKA/devices.json)
# --------------------------------------------------------------------------- #
def load_memory() -> dict[str, str]:
    try:
        data = json.loads((config.app_data_dir() / MEMORY_FILE).read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items() if v in ORIGINAL_NAMES} \
            if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_memory(memory: dict[str, str]) -> None:
    try:
        (config.app_data_dir() / MEMORY_FILE).write_text(json.dumps(memory, indent=2),
                                                         encoding="utf-8")
    except OSError as exc:
        log.warning("devices.json non écrit : %s", exc)


# --------------------------------------------------------------------------- #
# Windows (Core Audio)
# --------------------------------------------------------------------------- #
def _com_interfaces():
    """IDeviceTopology / IConnector / IPart (absents de pycaw) : juste le début
    de leur table de méthodes, dans l'ordre du SDK Windows."""
    from ctypes import HRESULT, POINTER, c_int
    from ctypes.wintypes import BOOL, LPWSTR, UINT

    from comtypes import COMMETHOD, GUID, IUnknown

    class IPart(IUnknown):
        _iid_ = GUID("{AE2DE0E4-5BCA-4F2D-AA46-5D13F8FDB3A9}")
        _methods_ = [COMMETHOD([], HRESULT, "GetName", (["out"], POINTER(LPWSTR), "name"))]

    class IConnector(IUnknown):
        _iid_ = GUID("{9C2C4058-23F5-41DE-877A-DF3AF236A09E}")

    IConnector._methods_ = [
        COMMETHOD([], HRESULT, "GetType", (["out"], POINTER(c_int), "type")),
        COMMETHOD([], HRESULT, "GetDataFlow", (["out"], POINTER(c_int), "flow")),
        COMMETHOD([], HRESULT, "ConnectTo", (["in"], POINTER(IConnector), "to")),
        COMMETHOD([], HRESULT, "Disconnect"),
        COMMETHOD([], HRESULT, "IsConnected", (["out"], POINTER(BOOL), "connected")),
        COMMETHOD([], HRESULT, "GetConnectedTo", (["out"], POINTER(POINTER(IConnector)), "to")),
    ]

    class IDeviceTopology(IUnknown):
        _iid_ = GUID("{2A07407E-6497-4A18-9787-32F79BD0D98F}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetConnectorCount", (["out"], POINTER(UINT), "count")),
            COMMETHOD([], HRESULT, "GetConnector", (["in"], UINT, "index"),
                      (["out"], POINTER(POINTER(IConnector)), "connector")),
        ]

    return IDeviceTopology, IPart


def _pin_name(device, interfaces) -> str:
    """Nom d'origine du périphérique : celui de la broche du pilote à laquelle il
    est relié (le renommage dans Windows ne le change pas). "" si illisible."""
    topology_type, part_type = interfaces
    try:
        topology = device.Activate(topology_type._iid_, CLSCTX_ALL, None)
        topology = topology.QueryInterface(topology_type)
        if topology.GetConnectorCount() < 1:
            return ""
        pin = topology.GetConnector(0).GetConnectedTo()
        name = pin.QueryInterface(part_type).GetName() or ""
    except Exception as exc:
        log.debug("Nom de broche illisible : %s", exc)
        return ""
    # Une broche sans nom propre porte celui de sa catégorie (« Speakers »…)
    return name.strip() if name.strip().lower().startswith("voicemeeter") else ""


def _key(fmtid: str, pid: int):
    from comtypes import GUID
    from pycaw.api.mmdeviceapi.depend.structures import PROPERTYKEY

    key = PROPERTYKEY()
    key.fmtid, key.pid = GUID(fmtid), pid
    return key


def _read(store, pkey) -> str:
    try:
        value = store.GetValue(_key(*pkey))
        text = value.GetValue()
        return text if isinstance(text, str) else ""
    except Exception:
        return ""


def list_endpoints() -> list[Endpoint]:
    """Périphériques Windows actifs de Voicemeeter (vide hors Windows)."""
    if sys.platform != "win32":
        return []
    try:
        import comtypes
        from pycaw.api.mmdeviceapi import IMMDeviceEnumerator
        from pycaw.constants import CLSID_MMDeviceEnumerator, DEVICE_STATE, EDataFlow

        interfaces = _com_interfaces()
        enumerator = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator,
                                               comtypes.CLSCTX_INPROC_SERVER)
        found = []
        for flow, capture in ((EDataFlow.eRender.value, False), (EDataFlow.eCapture.value, True)):
            collection = enumerator.EnumAudioEndpoints(flow, DEVICE_STATE.ACTIVE.value)
            for i in range(collection.GetCount()):
                device = collection.Item(i)
                store = device.OpenPropertyStore(0)         # lecture
                interface = _read(store, PKEY_INTERFACE_NAME)
                if "voicemeeter" not in interface.lower():
                    continue
                found.append(Endpoint(str(device.GetId()), capture, _read(store, PKEY_DEVICE_DESC),
                                      _pin_name(device, interfaces), interface, device))
        for e in found:
            log.debug("Périphérique Voicemeeter : « %s » (origine « %s », %s)", e.name,
                      e.original or "?", "enregistrement" if e.capture else "lecture")
        return found
    except Exception as exc:
        log.warning("Périphériques Voicemeeter introuvables : %s", exc)
        return []


def _write(endpoint: Endpoint, name: str) -> None:
    from ctypes import byref

    from comtypes.automation import VT_LPWSTR
    from pycaw.api.mmdeviceapi.depend.structures import PROPVARIANT

    store = endpoint.device.OpenPropertyStore(STGM_READWRITE)
    full = f"{name} ({endpoint.interface})" if endpoint.interface else name
    for pkey, text in ((PKEY_DEVICE_DESC, name), (PKEY_FRIENDLY_NAME, full)):
        value = PROPVARIANT()
        value.vt = VT_LPWSTR
        value.union.pwszVal = text
        try:
            store.SetValue(byref(_key(*pkey)), byref(value))
        except Exception:
            if pkey == PKEY_DEVICE_DESC:
                raise
            # Le nom complet est recomposé par Windows sur certaines versions
    store.Commit()


def rename(changes: list[tuple[Endpoint, str, str | None]]) -> tuple[int, bool]:
    """Applique les renommages et note les périphériques renommés : (nombre
    faits, accès refusé ?)."""
    done, denied = 0, False
    memory = load_memory()
    for endpoint, name, key in changes:
        try:
            _write(endpoint, name)
        except Exception as exc:
            if getattr(exc, "hresult", None) == E_ACCESSDENIED:
                denied = True
            else:
                log.warning("Renommage de « %s » impossible : %s", endpoint.name, exc)
            continue
        done += 1
        if key:
            memory[endpoint.id] = key
        log.info("Périphérique « %s » renommé en « %s ».", endpoint.name, name)
    if done:
        save_memory(memory)
    return done, denied


def elevated_command() -> tuple[str, str]:
    """(programme, arguments) de la relance en administrateur."""
    if getattr(sys, "frozen", False):
        return sys.executable, "--rename-devices"
    return sys.executable, f'"{Path(sys.argv[0]).resolve()}" --rename-devices'


def launch_elevated() -> bool:
    """Relance REKA en administrateur pour renommer (Windows demande l'accord)."""
    import ctypes

    program, args = elevated_command()
    os.environ["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", program, args, None, 0)
    if result <= 32:
        log.info("Renommage des périphériques en administrateur refusé ou impossible (%s).", result)
        return False
    return True


def run_elevated(cfg: dict) -> int:
    """--rename-devices : renomme puis quitte (instance lancée en administrateur)."""
    changes, _lost = plan(cfg, list_endpoints(), load_memory())
    done, denied = rename(changes)
    log.info("Renommage en administrateur : %d périphérique(s)%s.", done,
             ", accès refusé" if denied else "")
    return 1 if denied else 0


class DeviceRenamer:
    """Garde les périphériques Voicemeeter à jour depuis la console (thread UI)."""

    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg
        self._asked = False      # une seule demande d'élévation par session

    def apply(self) -> list[str]:
        """Renomme ce qui doit l'être ; renvoie les noms REKA dont l'origine est
        introuvable (à renommer à la main)."""
        if sys.platform != "win32":
            return []
        changes, lost = plan(self._cfg, list_endpoints(), load_memory())
        if changes:
            _done, denied = rename(changes)
            if denied and not self._asked:
                self._asked = True
                launch_elevated()
        if lost:
            log.warning("Périphériques au nom REKA d'origine inconnue : %s",
                        ", ".join(e.name for e in lost))
        return [e.name for e in lost]
