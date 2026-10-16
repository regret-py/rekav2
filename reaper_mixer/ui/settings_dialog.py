"""
Dialogue de réglages : projet REAPER, chemins des moteurs, carte son de
REAPER, affichage temporaire des fenêtres de Voicemeeter / REAPER, options,
journaux.

Les options s'appliquent tout de suite ; tout est écrit dans la config à la
fermeture. Les chemins des moteurs ne servent qu'au prochain démarrage.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget)

from core import config
from ui.project_dialog import default_project

log = logging.getLogger(__name__)


def project_label(path: str) -> str:
    if not path:
        return "Projet vierge REKA (aucun effet)"
    if Path(path).name.lower() == "session.rpp":
        return f"Projet vierge REKA — {path}"
    return f"{Path(path).stem} — {path}"


class SettingsDialog(QDialog):
    optionsChanged = Signal()      # insert / création de pistes modifiés
    projectRequested = Signal()    # « Changer de projet… »

    def __init__(self, cfg: dict, processes, reaper, vm, parent: QWidget | None = None,
                 persist: bool = True) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Réglages — {config.APP_NAME}")
        self.setMinimumWidth(580)
        self._cfg = cfg
        self._persist = persist
        self._processes = processes
        self._reaper = reaper
        self._vm = vm

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        header = QHBoxLayout()
        logo = QLabel()
        pixmap = QPixmap(str(config.resource_path("assets/reka_mark.png")))
        if not pixmap.isNull():
            logo.setPixmap(pixmap.scaledToHeight(30, Qt.TransformationMode.SmoothTransformation))
        header.addWidget(logo)
        title = QLabel("Réglages")
        title.setObjectName("dialogTitle")
        header.addWidget(title)
        header.addStretch(1)
        layout.addLayout(header)

        layout.addWidget(self._project_group())
        layout.addWidget(self._engines_group())
        layout.addWidget(self._reaper_group())
        layout.addWidget(self._options_group())

        footer = QHBoxLayout()
        logs = QPushButton("Ouvrir le dossier des journaux")
        logs.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(config.app_data_dir()))))
        footer.addWidget(logs)
        version = QLabel(f"{config.APP_NAME} {config.APP_VERSION}")
        version.setObjectName("hint")
        footer.addStretch(1)
        footer.addWidget(version)
        close = QPushButton("Fermer")
        close.setObjectName("primary")
        close.setDefault(True)
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        layout.addLayout(footer)

        if reaper is not None:
            reaper.device_changed.connect(self._show_device)
            reaper.project_changed.connect(self._show_project)
        self._show_device(reaper.device if reaper is not None else None)
        self._show_project(reaper.project_path if reaper is not None else "")

    # ------------------------------------------------------------ groupes
    def _project_group(self) -> QGroupBox:
        group = QGroupBox("Projet REAPER")
        box = QVBoxLayout(group)
        self.project_open = QLabel()
        self.project_open.setWordWrap(True)
        self.project_open.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(self.project_open)
        self.project_default = QLabel()
        self.project_default.setObjectName("hint")
        self.project_default.setWordWrap(True)
        box.addWidget(self.project_default)
        change = QPushButton("Changer de projet…")
        change.clicked.connect(self._change_project)
        change.setEnabled(self._reaper is not None)
        box.addWidget(change)
        return group

    def _engines_group(self) -> QGroupBox:
        group = QGroupBox("Moteurs audio")
        form = QFormLayout(group)
        self.vm_path = self._path_row(form, "Voicemeeter", "voicemeeter")
        self.reaper_path = self._path_row(form, "REAPER", "reaper")
        hint = QLabel("Laisse vide pour la détection automatique. "
                      "Pris en compte au prochain démarrage.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        form.addRow(hint)
        return group

    def _path_row(self, form: QFormLayout, label: str, key: str) -> QLineEdit:
        row = QHBoxLayout()
        edit = QLineEdit(self._cfg.get(key, {}).get("path", ""))
        edit.setPlaceholderText("Détection automatique")
        row.addWidget(edit, 1)
        browse = QPushButton("Parcourir…")
        browse.clicked.connect(lambda: self._browse(edit, label))
        row.addWidget(browse)
        form.addRow(label, row)
        return edit

    def _reaper_group(self) -> QGroupBox:
        group = QGroupBox("Carte son de REAPER")
        box = QVBoxLayout(group)
        self.device_label = QLabel()
        self.device_label.setWordWrap(True)
        box.addWidget(self.device_label)
        self.device_hint = QLabel()
        self.device_hint.setWordWrap(True)
        box.addWidget(self.device_hint)

        prefs = QPushButton("Ouvrir les préférences audio de REAPER")
        prefs.clicked.connect(self._open_reaper_prefs)
        prefs.setEnabled(self._reaper is not None)
        box.addWidget(prefs)

        self.show_reaper = QCheckBox("Afficher la fenêtre de REAPER pendant les réglages")
        self.show_reaper.toggled.connect(lambda on: self._show_engine("reaper", on))
        box.addWidget(self.show_reaper)
        self.show_vm = QCheckBox("Afficher la fenêtre de Voicemeeter pendant les réglages")
        self.show_vm.toggled.connect(lambda on: self._show_engine("voicemeeter", on))
        box.addWidget(self.show_vm)
        return group

    def _options_group(self) -> QGroupBox:
        group = QGroupBox("Options")
        box = QVBoxLayout(group)
        vm_cfg, reaper_cfg = self._cfg.setdefault("voicemeeter", {}), self._cfg.setdefault("reaper", {})
        self.manage_insert = QCheckBox(
            "Faire passer les strips par leurs effets REAPER (insert Voicemeeter automatique)")
        self.manage_insert.setChecked(bool(vm_cfg.get("manage_insert", True)))
        self.manage_insert.toggled.connect(lambda on: self._set_option("voicemeeter", "manage_insert", on))
        box.addWidget(self.manage_insert)
        self.manage_routing = QCheckBox(
            "Créer une piste REAPER quand on ajoute un effet à une strip qui n'en a pas")
        self.manage_routing.setChecked(bool(reaper_cfg.get("manage_routing", True)))
        self.manage_routing.toggled.connect(lambda on: self._set_option("reaper", "manage_routing", on))
        box.addWidget(self.manage_routing)
        self.close_vm = QCheckBox("Fermer Voicemeeter en quittant")
        self.close_vm.setChecked(bool(vm_cfg.get("terminate_on_exit", True)))
        box.addWidget(self.close_vm)
        self.close_reaper = QCheckBox("Fermer REAPER en quittant")
        self.close_reaper.setChecked(bool(reaper_cfg.get("terminate_on_exit", True)))
        box.addWidget(self.close_reaper)
        return group

    # ------------------------------------------------------------ actions
    def _browse(self, edit: QLineEdit, label: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self, f"Localiser {label}", edit.text(),
                                              "Exécutable (*.exe)")
        if path:
            edit.setText(path)

    def _show_engine(self, key: str, visible: bool) -> None:
        if self._processes is not None:
            self._processes.set_window_visible(key, visible)

    def _open_reaper_prefs(self) -> None:
        self.show_reaper.setChecked(True)
        self._reaper.open_preferences()

    def _change_project(self) -> None:
        self.projectRequested.emit()
        self._show_project(self._reaper.project_path if self._reaper is not None else "")

    def _set_option(self, section: str, key: str, value: bool) -> None:
        self._cfg.setdefault(section, {})[key] = value
        self.optionsChanged.emit()

    def _show_project(self, path: str) -> None:
        self.project_open.setText(f"Ouvert : {project_label(path)}" if path
                                  else "Ouvert : (en attente de REAPER)")
        default = default_project(self._cfg)
        ask = self._cfg.get("reaper", {}).get("ask_project_on_start", False)
        self.project_default.setText(
            f"Au démarrage : {project_label(default)}"
            + (" — choix demandé à chaque démarrage" if ask else ""))

    def _show_device(self, device) -> None:
        if device is None:
            self.device_label.setText("Carte son de REAPER : inconnue (REAPER non connecté)")
            self.device_hint.setText("")
            return
        self.device_label.setText(f"Carte son de REAPER : {device.describe()}")
        if device.is_voicemeeter_insert:
            self.device_hint.setObjectName("ok")
            self.device_hint.setText("✔ Les effets REAPER s'appliquent aux strips.")
        else:
            self.device_hint.setObjectName("warning")
            self.device_hint.setText(
                "⚠ Pour que les effets s'appliquent, choisis le pilote ASIO "
                "« Voicemeeter Insert Virtual ASIO » dans les préférences audio de REAPER.")
        self.device_hint.style().unpolish(self.device_hint)
        self.device_hint.style().polish(self.device_hint)

    def done(self, result: int) -> None:
        # Les fenêtres des moteurs ne restent visibles que pendant les réglages
        if self.show_reaper.isChecked():
            self._show_engine("reaper", False)
        if self.show_vm.isChecked():
            self._show_engine("voicemeeter", False)
        if self._reaper is not None:
            for signal, slot in ((self._reaper.device_changed, self._show_device),
                                 (self._reaper.project_changed, self._show_project)):
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        self._save()
        super().done(result)

    def _save(self) -> None:
        vm_cfg, reaper_cfg = self._cfg.setdefault("voicemeeter", {}), self._cfg.setdefault("reaper", {})
        vm_cfg["path"] = self.vm_path.text().strip()
        reaper_cfg["path"] = self.reaper_path.text().strip()
        vm_cfg["terminate_on_exit"] = self.close_vm.isChecked()
        reaper_cfg["terminate_on_exit"] = self.close_reaper.isChecked()
        if not self._persist:
            return
        try:
            config.save_config(self._cfg)
        except OSError as exc:
            log.error("Réglages non sauvegardés : %s", exc)
