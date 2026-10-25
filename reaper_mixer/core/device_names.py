"""
Noms des périphériques Windows de Voicemeeter.

Voicemeeter crée ses périphériques Windows : les sorties virtuelles B1, B2, B3
(« Voicemeeter Out B1 »… côté enregistrement, là où Discord et OBS prennent le
micro) et les entrées virtuelles (« Voicemeeter Input », « AUX Input »,
« VAIO3 Input » côté lecture). REKA leur donne ses propres noms : « REKA Micro »,
« REKA Discord », « REKA Obs », et « REKA <nom de la strip> » pour les entrées.

C'est le même renommage que Panneau de configuration › Son › Propriétés : le
périphérique ne change pas, les applications gardent leur réglage.

- Chaque périphérique est reconnu par le nom de son pilote (« VB-Audio
  Voicemeeter AUX VAIO »), qui ne change pas quand on le renomme.
- Windows n'autorise le renommage qu'en administrateur : si REKA n'a pas le
  droit, il se relance une fois en administrateur avec --rename-devices (Windows
  demande l'autorisation), seulement quand un nom est à changer.
- Option désactivée : les noms « REKA … » reprennent leur nom d'origine ; un nom
  choisi à la main par l'utilisateur n'est pas touché.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from core.app_volume import match_device
from core.voicemeeter_ctrl import edition, strip_map

log = logging.getLogger(__name__)

PREFIX = "REKA "
MAX_NAME = 40
# Clés des périphériques : sorties B1..B3 (capture), entrées virtuelles in0..in2 (lecture)
ORIGINAL_NAMES = {
    "B1": "Voicemeeter Out B1", "B2": "Voicemeeter Out B2", "B3": "Voicemeeter Out B3",
    "in0": "Voicemeeter Input", "in1": "Voicemeeter AUX Input", "in2": "Voicemeeter VAIO3 Input",
}
DEFAULT_OUTPUT_NAMES = {"B1": "REKA Micro", "B2": "REKA Discord", "B3": "REKA Obs"}

# Propriétés Windows : nom court (celui qu'on renomme), nom complet, nom du pilote
PKEY_DEVICE_DESC = ("{a45c254e-df1c-4efd-8020-67d146a850e0}", 2)
PKEY_FRIENDLY_NAME = ("{a45c254e-df1c-4efd-8020-67d146a850e0}", 14)
PKEY_INTERFACE_NAME = ("{026e516e-b814-414b-83cd-856d6fef4822}", 2)
E_ACCESSDENIED = -2147024891          # 0x80070005
STGM_READWRITE = 2
ERROR_CANCELLED = 1223               # l'utilisateur a refusé l'élévation


@dataclass
class Endpoint:
    key: str          # B1, in0…
    name: str         # nom court actuel (« Voicemeeter Out B1 »)
    interface: str    # nom du pilote (« VB-Audio Voicemeeter VAIO »)
    device: object = None   # IMMDevice (Windows)


def endpoint_key(interface: str, capture: bool) -> str | None:
    """Périphérique Voicemeeter d'après le nom de son pilote : la VAIO porte B1
    et l'entrée virtuelle 1, l'AUX B2 et l'entrée 2, la VAIO3 B3 et l'entrée 3."""
    for index in range(3):
        if match_device(interface, index):
            return f"B{index + 1}" if capture else f"in{index}"
    return None


def enabled(cfg: dict) -> bool:
    return bool(cfg.get("voicemeeter", {}).get("rename_devices", True))


def wanted_names(cfg: dict) -> dict[str, str]:
    """Nom voulu pour chaque périphérique Voicemeeter (option activée)."""
    vm_cfg = cfg.get("voicemeeter", {})
    custom = vm_cfg.get("device_names", {})
    names = {key: str(custom.get(key) or default) for key, default in DEFAULT_OUTPUT_NAMES.items()}
    ed = edition(cfg)
    strip_names = cfg.get("ui", {}).get("strip_names", [])
    for virtual in range(3):
        names[f"in{virtual}"] = f"{PREFIX}Entrée {virtual + 1}"
    for ui_index, vm_strip in enumerate(strip_map(cfg)):
        virtual = vm_strip - ed.physical_strips
        if 0 <= virtual < 3 and ui_index < len(strip_names) and str(strip_names[ui_index]).strip():
            names[f"in{virtual}"] = PREFIX + str(strip_names[ui_index]).strip()
    for key in [k for k in names if str(custom.get(k, "")).strip()]:
        names[key] = str(custom[key]).strip()       # un nom donné dans config.json l'emporte
    return {key: name[:MAX_NAME] for key, name in names.items()}


def plan(cfg: dict, endpoints: list[Endpoint]) -> list[tuple[Endpoint, str]]:
    """Renommages à faire : vers les noms REKA (option activée), sinon retour au
    nom d'origine des périphériques que REKA avait renommés."""
    wanted = wanted_names(cfg) if enabled(cfg) else None
    changes = []
    for endpoint in endpoints:
        if wanted is not None:
            target = wanted.get(endpoint.key)
        elif endpoint.name.startswith(PREFIX):
            target = ORIGINAL_NAMES.get(endpoint.key)
        else:
            target = None
        if target and target != endpoint.name:
            changes.append((endpoint, target))
    return changes


# --------------------------------------------------------------------------- #
# Windows (Core Audio)
# --------------------------------------------------------------------------- #
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

        enumerator = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator,
                                               comtypes.CLSCTX_INPROC_SERVER)
        found = []
        for flow, capture in ((EDataFlow.eRender.value, False), (EDataFlow.eCapture.value, True)):
            collection = enumerator.EnumAudioEndpoints(flow, DEVICE_STATE.ACTIVE.value)
            for i in range(collection.GetCount()):
                device = collection.Item(i)
                store = device.OpenPropertyStore(0)         # lecture
                key = endpoint_key(_read(store, PKEY_INTERFACE_NAME), capture)
                if key:
                    found.append(Endpoint(key, _read(store, PKEY_DEVICE_DESC),
                                          _read(store, PKEY_INTERFACE_NAME), device))
        return found
    except Exception as exc:
        log.warning("Périphériques Voicemeeter introuvables : %s", exc)
        return []


def _write(endpoint: Endpoint, name: str) -> None:
    from ctypes import byref

    from comtypes.automation import VT_LPWSTR
    from pycaw.api.mmdeviceapi.depend.structures import PROPVARIANT

    store = endpoint.device.OpenPropertyStore(STGM_READWRITE)
    for pkey, text in ((PKEY_DEVICE_DESC, name),
                       (PKEY_FRIENDLY_NAME, f"{name} ({endpoint.interface})" if endpoint.interface else name)):
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


def rename(changes: list[tuple[Endpoint, str]]) -> tuple[int, bool]:
    """Applique les renommages : (nombre faits, accès refusé ?)."""
    done, denied = 0, False
    for endpoint, name in changes:
        try:
            _write(endpoint, name)
            done += 1
            log.info("Périphérique « %s » renommé en « %s ».", endpoint.name, name)
        except Exception as exc:
            if getattr(exc, "hresult", None) == E_ACCESSDENIED or "accès" in str(exc).lower() \
                    or "access" in str(exc).lower():
                denied = True
            else:
                log.warning("Renommage de « %s » impossible : %s", endpoint.name, exc)
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
    done, denied = rename(plan(cfg, list_endpoints()))
    log.info("Renommage en administrateur : %d périphérique(s)%s.", done,
             ", accès refusé" if denied else "")
    return 1 if denied else 0


class DeviceRenamer:
    """Garde les périphériques Voicemeeter à jour depuis la console (thread UI)."""

    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg
        self._asked = False      # une seule demande d'élévation par session

    def apply(self) -> None:
        if sys.platform != "win32":
            return
        changes = plan(self._cfg, list_endpoints())
        if not changes:
            return
        _done, denied = rename(changes)
        if denied and not self._asked:
            self._asked = True
            launch_elevated()
