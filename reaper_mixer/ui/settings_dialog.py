"""
Dialogue de réglages : chemins des moteurs, carte son de REAPER, affichage
temporaire des fenêtres de Voicemeeter / REAPER, options, journaux.

Les changements sont écrits dans la config à la fermeture ; les chemins ne
servent qu'au prochain démarrage.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget)

from core import config

log = logging.getLogger(__name__)


class SettingsDialog(QDialog):
    def __init__(self, cfg: dict, processes, reaper, vm, parent: QWidget | None = None,
                 persist: bool = True) -> None:
        super().__init__(parent)
        self.setWindowTitle("Réglages — REAPER Mixer")
        self.setMinimumWidth(560)
        self._cfg = cfg
        self._persist = persist
        self._processes = processes
        self._reaper = reaper
        self._vm = vm

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        title = QLabel("Réglages")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

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
        self._show_device(reaper.device if reaper is not None else None)

    # ------------------------------------------------------------ groupes
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
        group = QGroupBox("REAPER")
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
            "Faire passer les strips par REAPER (insert Voicemeeter automatique)")
        self.manage_insert.setChecked(bool(vm_cfg.get("manage_insert", True)))
        self.manage_insert.toggled.connect(self._apply_insert_option)
        box.addWidget(self.manage_insert)
        self.manage_routing = QCheckBox("Configurer automatiquement les pistes de REAPER")
        self.manage_routing.setChecked(bool(reaper_cfg.get("manage_routing", True)))
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

    def _apply_insert_option(self, enabled: bool) -> None:
        device = self._reaper.device if self._reaper is not None else None
        if self._vm is not None:
            self._vm.set_inserts(enabled and device is not None and device.is_voicemeeter_insert)

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
            try:
                self._reaper.device_changed.disconnect(self._show_device)
            except (RuntimeError, TypeError):
                pass
        self._save()
        super().done(result)

    def _save(self) -> None:
        if not self._persist:
            return
        vm_cfg, reaper_cfg = self._cfg.setdefault("voicemeeter", {}), self._cfg.setdefault("reaper", {})
        vm_cfg["path"] = self.vm_path.text().strip()
        reaper_cfg["path"] = self.reaper_path.text().strip()
        vm_cfg["manage_insert"] = self.manage_insert.isChecked()
        reaper_cfg["manage_routing"] = self.manage_routing.isChecked()
        vm_cfg["terminate_on_exit"] = self.close_vm.isChecked()
        reaper_cfg["terminate_on_exit"] = self.close_reaper.isChecked()
        try:
            config.save_config(self._cfg)
        except OSError as exc:
            log.error("Réglages non sauvegardés : %s", exc)
