# -*- mode: python ; coding: utf-8 -*-
#
# Build de l'exécutable Windows (un seul fichier, sans console) :
#     python -m PyInstaller --noconfirm --clean build.spec
# Résultat : dist/REKA.exe
#
# Vérification du build (rend la console en mode démo dans une image) :
#     "dist/REKA.exe" --selftest selftest/console.png

import os
import re

try:
    from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct,
                                                     StringTable, VarFileInfo, VarStruct,
                                                     VSVersionInfo)
except ImportError:             # build hors Windows : pas de ressource de version
    VSVersionInfo = None

# Version et développeurs lus dans core/config.py (sans importer l'application)
with open(os.path.join(SPECPATH, "core", "config.py"), encoding="utf-8") as source:
    _config_source = source.read()


def _constant(name):
    return re.search(rf'^{name} = "([^"]*)"', _config_source, re.MULTILINE).group(1)


# Musique en cours : WinRT (importé dans un thread, donc invisible pour l'analyse)
# et la DLL C++ livrée à côté de ses modules
try:
    from PyInstaller.utils.hooks import collect_dynamic_libs
    WINRT_BINARIES = collect_dynamic_libs("winrt")
except Exception:              # build hors Windows : pas de WinRT
    WINRT_BINARIES = []
WINRT_MODULES = ["winrt.runtime", "winrt.windows.foundation", "winrt.windows.media.control",
                 "winrt.windows.storage.streams"]

APP_VERSION = _constant("APP_VERSION")
APP_AUTHORS = _constant("APP_AUTHORS")
_numbers = tuple(int(n) for n in re.findall(r"\d+", APP_VERSION))[:4]
_numbers += (0,) * (4 - len(_numbers))

# Propriétés de REKA.exe (clic droit > Propriétés > Détails) : version et crédits
version_info = None if VSVersionInfo is None else VSVersionInfo(
    ffi=FixedFileInfo(filevers=_numbers, prodvers=_numbers),
    kids=[
        StringFileInfo([StringTable("040C04B0", [          # français, Unicode
            StringStruct("CompanyName", APP_AUTHORS),
            StringStruct("FileDescription", "REKA"),       # nom affiché par le Gestionnaire des tâches
            StringStruct("FileVersion", APP_VERSION),
            StringStruct("InternalName", "REKA"),
            StringStruct("LegalCopyright", f"© {APP_AUTHORS}"),
            StringStruct("OriginalFilename", "REKA.exe"),
            StringStruct("ProductName", "REKA"),
            StringStruct("ProductVersion", APP_VERSION),
            StringStruct("Comments", f"Console de mixage Voicemeeter + REAPER, par {APP_AUTHORS}"),
        ])]),
        VarFileInfo([VarStruct("Translation", [0x040C, 1200])]),
    ],
)

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=WINRT_BINARIES,
    # Ressources lues à l'exécution via core.config.resource_path()
    datas=[
        ("config.json", "."),
        ("assets", "assets"),
        # Numéro de build et release (écrit par le build GitHub) : mises à jour
        *([("build_info.json", ".")] if os.path.isfile(os.path.join(SPECPATH, "build_info.json")) else []),
    ],
    # Importés à l'intérieur de fonctions : on les déclare explicitement
    hiddenimports=["voicemeeterlib", "core.demo", "pycaw.utils", "pycaw.api.endpointvolume",
                   *WINRT_MODULES],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc", "PySide6.QtQml", "PySide6.QtQuick",
              "PySide6.QtWebEngineCore", "PySide6.QtPdf"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="REKA",
    icon="assets/icon.ico",
    version=version_info,
    console=False,               # --windowed
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    disable_windowed_traceback=False,
)
