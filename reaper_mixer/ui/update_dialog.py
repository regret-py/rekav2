"""
Fenêtre « Mise à jour disponible » : version en ligne, notes, puis au choix
installation automatique (téléchargement vérifié, remplacement de l'exe,
redémarrage), téléchargement dans le navigateur, plus tard, ou ignorer cette
version.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton,
                               QVBoxLayout, QWidget)

from core import config
from core.updater import (BuildInfo, UpdateDownloader, UpdateError, UpdateInfo, can_self_update,
                          install)

log = logging.getLogger(__name__)


class UpdateDialog(QDialog):
    """Après exec() : `installed` (le nouvel exe est en place, il faut
    redémarrer) ou `skipped` (ignorer cette version)."""

    def __init__(self, update: UpdateInfo, build: BuildInfo | None,
                 parent: QWidget | None = None, self_update: bool | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Mise à jour — {config.APP_NAME}")
        self.setMinimumWidth(560)
        self.update_info = update
        self.installed = False
        self.skipped = False
        self._downloader: UpdateDownloader | None = None
        self._self_update = can_self_update() if self_update is None else self_update

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        header = QHBoxLayout()
        logo = QLabel()
        pixmap = QPixmap(str(config.resource_path("assets/reka_mark.png")))
        if not pixmap.isNull():
            logo.setPixmap(pixmap.scaledToHeight(34, Qt.TransformationMode.SmoothTransformation))
        header.addWidget(logo)
        title = QLabel("Nouvelle version de REKA disponible")
        title.setObjectName("dialogTitle")
        header.addWidget(title)
        header.addStretch(1)
        layout.addLayout(header)

        current = build.describe() if build is not None else config.APP_VERSION
        versions = QLabel(f"En ligne : <b>{update.describe()}</b><br>Installée : {current}")
        versions.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(versions)
        if update.notes:
            notes = QLabel(f"Nouveautés : {update.notes}")
            notes.setObjectName("hint")
            notes.setWordWrap(True)
            notes.setTextFormat(Qt.TextFormat.PlainText)
            layout.addWidget(notes)

        self.status = QLabel(
            "REKA va télécharger la nouvelle version, la vérifier, puis redémarrer dessus. "
            "Tes réglages et ton projet REAPER sont conservés."
            if self._self_update else
            "Télécharge le nouveau REKA.exe et remplace l'ancien.")
        self.status.setObjectName("hint")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, max(1, update.size))
        self.progress.setFormat("%p %")
        self.progress.hide()
        layout.addWidget(self.progress)

        buttons = QHBoxLayout()
        self.skip_button = QPushButton("Ignorer cette version")
        self.skip_button.clicked.connect(self._skip)
        buttons.addWidget(self.skip_button)
        buttons.addStretch(1)
        self.later_button = QPushButton("Plus tard")
        self.later_button.clicked.connect(self.reject)
        buttons.addWidget(self.later_button)
        self.browser_button = QPushButton("Télécharger" if not self._self_update
                                          else "Navigateur")
        self.browser_button.setToolTip("Télécharger REKA.exe avec le navigateur")
        self.browser_button.clicked.connect(self._open_browser)
        buttons.addWidget(self.browser_button)
        self.install_button = QPushButton("Mettre à jour")
        self.install_button.setObjectName("primary")
        self.install_button.setDefault(True)
        self.install_button.clicked.connect(self.start_install)
        self.install_button.setVisible(self._self_update)
        if not self._self_update:
            self.browser_button.setObjectName("primary")
            self.browser_button.setDefault(True)
        buttons.addWidget(self.install_button)
        layout.addLayout(buttons)

    # ------------------------------------------------------------ actions
    def _skip(self) -> None:
        self.skipped = True
        self.reject()

    def _open_browser(self) -> None:
        QDesktopServices.openUrl(QUrl(self.update_info.url))

    def start_install(self) -> None:
        if self._downloader is not None:
            return
        self.install_button.setEnabled(False)
        self.skip_button.setEnabled(False)
        self.later_button.setText("Annuler")
        self.progress.show()
        self.status.setText("Téléchargement de la nouvelle version…")
        # Sans parent : le fil de téléchargement peut finir après la fermeture du dialogue
        self._downloader = UpdateDownloader(self.update_info)
        self._downloader.progress.connect(self._on_progress)
        self._downloader.finished.connect(self._on_downloaded)
        self._downloader.failed.connect(self._on_failed)
        self._downloader.start()

    def _on_progress(self, received: int, total: int) -> None:
        self.progress.setMaximum(max(1, total))
        self.progress.setValue(received)

    def _on_downloaded(self, path: Path) -> None:
        if self._downloader is None:        # annulé entre-temps
            return
        self.status.setText("Installation…")
        try:
            install(path, Path(sys.executable))
        except UpdateError as exc:
            log.warning("Mise à jour non installée : %s", exc)
            self._on_failed(f"{exc}\nLe nouvel exe est ici : {path}")
            return
        self.installed = True
        self.status.setText("Mise à jour installée : REKA redémarre.")
        self.accept()

    def _on_failed(self, message: str) -> None:
        if self._downloader is None:        # annulé : rien à afficher
            return
        self._downloader = None
        self.progress.hide()
        self.status.setText(f"⚠ {message}\nTu peux aussi télécharger REKA.exe dans le navigateur.")
        self.install_button.setEnabled(True)
        self.install_button.setText("Réessayer")
        self.skip_button.setEnabled(True)
        self.later_button.setText("Plus tard")

    def reject(self) -> None:
        if self._downloader is not None:
            self._downloader.cancel()
            self._downloader = None
        super().reject()
