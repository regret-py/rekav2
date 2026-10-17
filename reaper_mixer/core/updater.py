"""
Mises à jour de REKA.

Chaque build GitHub embarque `build_info.json` (version, numéro de build,
dépôt, release) et publie, à côté de REKA.exe, un `version.json` qui décrit
l'exe en ligne (numéro de build, taille, empreinte SHA-256, notes).

- REKA lit ce version.json (au démarrage puis régulièrement) : si le build en
  ligne est plus récent, l'interface le signale.
- « Mettre à jour » télécharge le nouvel exe, vérifie taille et empreinte,
  puis le met à la place de l'exe en cours : Windows interdit d'écraser un exe
  qui tourne mais permet de le renommer (REKA.exe → REKA.old.exe). REKA
  redémarre ensuite sur le nouvel exe, qui supprime l'ancien.

En développement (python main.py), pas de build_info.json : pas de vérification.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sys
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, Signal

from core import config

log = logging.getLogger(__name__)

BUILD_INFO_FILE = "build_info.json"
VERSION_FILE = "version.json"
OLD_SUFFIX = ".old.exe"
FETCH_TIMEOUT_S = 10.0
DOWNLOAD_TIMEOUT_S = 30.0
CHUNK = 64 * 1024
MAX_VERSION_FILE = 64 * 1024


class UpdateError(Exception):
    """Erreur présentable telle quelle à l'utilisateur."""


@dataclass(frozen=True)
class BuildInfo:
    version: str
    build: int
    commit: str
    repo: str          # « owner/dépôt » GitHub
    tag: str           # release où sont publiés REKA.exe et version.json

    def describe(self) -> str:
        return f"{self.version} (build {self.build})"


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    build: int
    commit: str
    url: str           # REKA.exe en ligne
    sha256: str
    size: int
    notes: str
    published: str

    def describe(self) -> str:
        return f"{self.version} (build {self.build})"


def load_build_info(path: Path | None = None) -> BuildInfo | None:
    """Infos du build en cours, embarquées par le build GitHub ; None en développement."""
    path = path or config.resource_path(BUILD_INFO_FILE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return BuildInfo(str(data["version"]), int(data["build"]), str(data.get("commit", "")),
                         str(data["repo"]), str(data["tag"]))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.warning("build_info.json illisible : %s", exc)
        return None


def version_url(cfg: dict, build: BuildInfo | None) -> str | None:
    """Adresse du version.json à consulter (config update.url, sinon la release du build)."""
    custom = str(cfg.get("update", {}).get("url", "") or "").strip()
    if custom:
        return custom
    if build is None or not build.repo or not build.tag:
        return None
    return f"https://github.com/{build.repo}/releases/download/{build.tag}/{VERSION_FILE}"


def parse_update(data: dict) -> UpdateInfo:
    try:
        update = UpdateInfo(str(data["version"]), int(data["build"]), str(data.get("commit", "")),
                            str(data["url"]), str(data["sha256"]).lower(), int(data["size"]),
                            str(data.get("notes", "")), str(data.get("published", "")))
    except (KeyError, TypeError, ValueError) as exc:
        raise UpdateError(f"Description de la mise à jour invalide ({exc}).") from exc
    if not update.url.lower().startswith(("https://", "http://")) or len(update.sha256) != 64:
        raise UpdateError("Description de la mise à jour invalide (adresse ou empreinte).")
    return update


def is_newer(update: UpdateInfo, build: BuildInfo | None, skipped: int = 0) -> bool:
    """Le build en ligne est-il plus récent que celui qui tourne (et pas ignoré) ?"""
    if build is None:
        return False
    return update.build > build.build and update.build != skipped


def _open(url: str, timeout: float):
    request = urllib.request.Request(url, headers={
        "User-Agent": f"{config.APP_NAME}/{config.APP_VERSION}",
        "Cache-Control": "no-cache",
    })
    return urllib.request.build_opener().open(request, timeout=timeout)


def fetch_update(url: str, timeout: float = FETCH_TIMEOUT_S) -> UpdateInfo:
    """Lit le version.json en ligne. Lève UpdateError (réseau, format)."""
    try:
        with _open(url, timeout) as response:
            raw = response.read(MAX_VERSION_FILE + 1)
    except Exception as exc:
        raise UpdateError(f"Impossible de joindre le serveur de mises à jour ({exc}).") from exc
    if len(raw) > MAX_VERSION_FILE:
        raise UpdateError("Description de la mise à jour trop grande.")
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise UpdateError("Description de la mise à jour illisible.") from exc
    if not isinstance(data, dict):
        raise UpdateError("Description de la mise à jour invalide.")
    return parse_update(data)


def update_dir() -> Path:
    return config.app_data_dir() / "update"


def download(update: UpdateInfo, dest: Path,
             progress: Callable[[int, int], None] | None = None,
             cancelled: Callable[[], bool] | None = None) -> Path:
    """Télécharge l'exe dans `dest` et vérifie taille + SHA-256. Lève UpdateError."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".part")
    digest, received = hashlib.sha256(), 0
    try:
        with _open(update.url, DOWNLOAD_TIMEOUT_S) as response, open(partial, "wb") as out:
            while True:
                if cancelled is not None and cancelled():
                    raise UpdateError("Téléchargement annulé.")
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                received += len(chunk)
                if received > update.size:
                    raise UpdateError("Fichier téléchargé plus gros que prévu.")
                digest.update(chunk)
                out.write(chunk)
                if progress is not None:
                    progress(received, update.size)
    except UpdateError:
        partial.unlink(missing_ok=True)
        raise
    except Exception as exc:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"Téléchargement interrompu ({exc}).") from exc
    if received != update.size or digest.hexdigest() != update.sha256:
        partial.unlink(missing_ok=True)
        raise UpdateError("Le fichier téléchargé est incomplet ou abîmé : réessaie.")
    os.replace(partial, dest)
    return dest


def old_exe_path(current: Path) -> Path:
    return current.with_name(current.stem + OLD_SUFFIX)


def install(new_exe: Path, current: Path) -> None:
    """Met `new_exe` à la place de `current` (l'exe en cours) : l'exe en cours
    est renommé en .old.exe (Windows le permet même s'il tourne), puis le
    nouveau prend son nom. En cas d'échec, l'ancien est remis en place."""
    old = old_exe_path(current)
    try:
        old.unlink(missing_ok=True)         # reste d'une mise à jour précédente
    except OSError as exc:
        raise UpdateError(f"Impossible de remplacer REKA dans {current.parent} ({exc}).") from exc
    try:
        os.replace(current, old)
    except OSError as exc:
        raise UpdateError(f"Impossible de remplacer REKA dans {current.parent} ({exc}).") from exc
    try:
        shutil.move(str(new_exe), str(current))
    except Exception as exc:
        try:
            if current.exists():
                current.unlink()
            os.replace(old, current)
        except OSError:
            log.exception("Mise à jour : ancien exe non remis en place (%s)", old)
        raise UpdateError(f"Impossible d'installer la mise à jour ({exc}).") from exc
    log.info("Mise à jour installée : %s (ancien exe : %s)", current, old)


def cleanup_previous(current: Path | None = None) -> bool:
    """Supprime l'ancien exe et les téléchargements restants ; False si l'ancien
    exe est encore verrouillé (l'ancienne instance finit de se fermer)."""
    if current is None:
        if not getattr(sys, "frozen", False):
            return True
        current = Path(sys.executable)
    done = True
    old = old_exe_path(current)
    if old.exists():
        try:
            old.unlink()
            log.info("Ancien exe supprimé : %s", old)
        except OSError:
            done = False
    folder = update_dir()
    if folder.is_dir():
        shutil.rmtree(folder, ignore_errors=True)
    return done


def can_self_update() -> bool:
    """Remplacement automatique possible : exe PyInstaller (pas en développement)."""
    return bool(getattr(sys, "frozen", False)) and Path(sys.executable).suffix.lower() == ".exe"


class UpdateChecker(QObject):
    """Vérifie en arrière-plan si une mise à jour existe."""

    available = Signal(object)        # UpdateInfo
    up_to_date = Signal()
    failed = Signal(str)

    def __init__(self, cfg: dict, build: BuildInfo | None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._cfg = cfg
        self.build = build
        self.latest: UpdateInfo | None = None
        self._busy = threading.Lock()

    @property
    def enabled(self) -> bool:
        return version_url(self._cfg, self.build) is not None and self.build is not None

    def check(self, manual: bool = False) -> bool:
        """Lance une vérification ; False si impossible (développement) ou déjà en cours.
        `manual` : demandée par l'utilisateur (une version ignorée est quand même proposée)."""
        url = version_url(self._cfg, self.build)
        if url is None or self.build is None or not self._busy.acquire(blocking=False):
            return False
        threading.Thread(target=self._run, args=(url, manual), name="update-check",
                         daemon=True).start()
        return True

    def _run(self, url: str, manual: bool) -> None:
        try:
            update = fetch_update(url)
        except UpdateError as exc:
            log.info("Vérification des mises à jour : %s", exc)
            self.failed.emit(str(exc))
            return
        finally:
            self._busy.release()
        skipped = 0 if manual else int(self._cfg.get("update", {}).get("skipped_build", 0) or 0)
        if is_newer(update, self.build, skipped):
            log.info("Mise à jour disponible : %s (en cours : %s)",
                     update.describe(), self.build.describe())
            self.latest = update
            self.available.emit(update)
        else:
            self.up_to_date.emit()


class UpdateDownloader(QObject):
    """Téléchargement en arrière-plan, avec progression."""

    progress = Signal(int, int)       # reçu, total (octets)
    finished = Signal(object)         # Path de l'exe vérifié
    failed = Signal(str)

    def __init__(self, update: UpdateInfo, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._update = update
        self._cancel = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, name="update-download", daemon=True).start()

    def cancel(self) -> None:
        self._cancel.set()

    def _run(self) -> None:
        dest = update_dir() / f"REKA-{self._update.build}.exe"
        try:
            path = download(self._update, dest, self.progress.emit, self._cancel.is_set)
        except UpdateError as exc:
            log.warning("Mise à jour non téléchargée : %s", exc)
            self.failed.emit(str(exc))
            return
        self.finished.emit(path)
