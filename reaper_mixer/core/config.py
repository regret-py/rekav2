"""
Configuration de REKA : emplacements des fichiers et lecture/écriture de
config.json.

- Le config.json livré avec l'application contient les valeurs par défaut.
  Une fois buildé avec PyInstaller (--onefile), il est extrait dans un dossier
  temporaire en lecture seule : on ne le modifie donc jamais.
- La config de l'utilisateur vit dans %APPDATA%/REKA/config.json.
  Elle est créée au premier lancement, puis fusionnée avec les défauts à chaque
  démarrage : les nouvelles clés ajoutées par une mise à jour apparaissent
  toutes seules, sans écraser les réglages existants.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import threading
from pathlib import Path
from typing import Any

APP_NAME = "REKA"
APP_VERSION = "0.3.0"
APP_AUTHORS = "Regrettable & Zerka"
CONFIG_FILENAME = "config.json"
LEGACY_DIR_NAME = "REAPER Mixer"   # ancien nom de l'application (données reprises)

log = logging.getLogger(__name__)

# Les sauvegardes peuvent venir de plusieurs endroits (fermeture, settings…)
_save_lock = threading.Lock()


# --------------------------------------------------------------------------- #
# Emplacements
# --------------------------------------------------------------------------- #
def resource_dir() -> Path:
    """Dossier des ressources livrées avec l'app (assets/, config.json par défaut).

    En mode exe PyInstaller, c'est le dossier d'extraction temporaire
    (sys._MEIPASS) ; en développement, la racine du projet reaper_mixer/.
    """
    bundle = getattr(sys, "_MEIPASS", None)
    return Path(bundle) if bundle else Path(__file__).resolve().parent.parent


def resource_path(relative: str | Path) -> Path:
    """Chemin absolu d'une ressource livrée, ex. resource_path("assets/icon.ico")."""
    return resource_dir() / relative


def app_data_dir() -> Path:
    """Dossier de données utilisateur : %APPDATA%/REKA (créé si besoin).

    Au premier lancement sous ce nom, les données de l'ancien dossier
    « REAPER Mixer » (config, projet de session…) sont reprises.
    Hors Windows (dev uniquement), repli sur ~/.config/REKA.
    """
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / ".config"
    path = root / APP_NAME
    if not path.exists():
        legacy = root / LEGACY_DIR_NAME
        if legacy.is_dir():
            # Fichiers verrouillés ou temporaires de l'ancienne version : ignorés
            try:
                shutil.copytree(legacy, path, ignore=shutil.ignore_patterns(
                    "instance.lock", "*.log", "*.log.*", "bridge"))
            except OSError as exc:  # on repart d'un dossier neuf plutôt que d'échouer
                log.warning("Reprise des données de %s impossible : %s", legacy, exc)
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_path(value: str | Path | None, base: Path | None = None) -> Path | None:
    """Convertit un chemin lu dans config.json en Path absolu.

    - vide / None → None (= « à détecter automatiquement »)
    - ~ et les variables %APPDATA%, %ProgramFiles%… sont développés
    - un chemin relatif est résolu par rapport à `base`
      (par défaut : le dossier des ressources de l'application)
    """
    if not value:
        return None
    path = Path(os.path.expandvars(os.path.expanduser(str(value))))
    if path.is_absolute():
        return path
    return (base or resource_dir()) / path


def user_config_path() -> Path:
    return app_data_dir() / CONFIG_FILENAME


# --------------------------------------------------------------------------- #
# Lecture / écriture
# --------------------------------------------------------------------------- #
def load_config() -> dict[str, Any]:
    """Charge la config : défauts livrés + surcharge utilisateur.

    Lève OSError/ValueError si le config.json livré est absent ou invalide
    (installation cassée). Une config utilisateur corrompue est mise de côté
    et remplacée par les défauts, sans bloquer le démarrage.
    """
    defaults = _read_json(resource_path(CONFIG_FILENAME))

    user_path = user_config_path()
    user: dict[str, Any] = {}
    if user_path.is_file():
        try:
            user = _read_json(user_path)
        except (OSError, ValueError) as exc:
            broken = user_path.with_name("config.invalide.json")
            log.error("Config utilisateur illisible (%s) : renommée en %s, "
                      "retour aux valeurs par défaut.", exc, broken.name)
            os.replace(user_path, broken)

    cfg = _deep_merge(defaults, user)
    if not user_path.is_file():
        save_config(cfg)
        log.info("Config utilisateur créée : %s", user_path)
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    """Écrit la config utilisateur de façon atomique (fichier temporaire puis
    remplacement), pour ne jamais laisser un config.json à moitié écrit."""
    path = user_config_path()
    tmp = path.with_suffix(".tmp")
    data = json.dumps(cfg, indent=2, ensure_ascii=False) + "\n"
    with _save_lock:
        tmp.write_text(data, encoding="utf-8")
        os.replace(tmp, path)
    log.debug("Config sauvegardée : %s", path)


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} doit contenir un objet JSON")
    return data


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Fusion récursive : les dicts sont fusionnés clé par clé, tout le reste
    (listes, nombres, textes) est remplacé par la valeur de `override`."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
