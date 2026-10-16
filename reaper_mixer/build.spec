# -*- mode: python ; coding: utf-8 -*-
#
# Build de l'exécutable Windows (un seul fichier, sans console) :
#     python -m PyInstaller --noconfirm --clean build.spec
# Résultat : dist/REKA.exe
#
# Vérification du build (rend la console en mode démo dans une image) :
#     "dist/REKA.exe" --selftest selftest/console.png

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    # Ressources lues à l'exécution via core.config.resource_path()
    datas=[
        ("config.json", "."),
        ("assets", "assets"),
    ],
    # Importés à l'intérieur de fonctions : on les déclare explicitement
    hiddenimports=["voicemeeterlib", "core.demo"],
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
    console=False,               # --windowed
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    disable_windowed_traceback=False,
)
