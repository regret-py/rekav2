"""
Dialogue d'ajout d'un plugin sur un strip : recherche, filtre par type,
double-clic ou « Ajouter » pour l'insérer à la fin de la chaîne.

La liste vient de REAPER (pont Lua) quand il est connecté, sinon du cache de
scan de REAPER et des dossiers VST3 (voir core/vst_scanner.py).
"""

from __future__ import annotations

import unicodedata

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
                               QWidget)

from core import vst_scanner
from core.vst_scanner import PluginInfo

ALL_TYPES = "Tous les types"
REAPER_WAIT_MS = 4000


def _fold(text: str) -> str:
    """Minuscules sans accents, pour une recherche tolérante."""
    return "".join(c for c in unicodedata.normalize("NFD", text.lower())
                   if unicodedata.category(c) != "Mn")


class VstDialog(QDialog):
    def __init__(self, strip_name: str, reaper, resource_path, extra_dirs: list[str] | None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Ajouter un effet — {strip_name}")
        self.resize(460, 560)
        self._reaper = reaper
        self._resource_path = resource_path
        self._extra_dirs = extra_dirs
        self._plugins: list[PluginInfo] = []
        self.selected: PluginInfo | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        title = QLabel(f"Ajouter un effet sur « {strip_name} »")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Rechercher un plugin…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._refilter)
        filters.addWidget(self.search, 1)
        self.kind = QComboBox()
        self.kind.addItem(ALL_TYPES)
        self.kind.currentTextChanged.connect(self._refilter)
        filters.addWidget(self.kind)
        layout.addLayout(filters)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _item: self._accept())
        self.list.currentItemChanged.connect(lambda *_: self._update_buttons())
        layout.addWidget(self.list, 1)

        self.status = QLabel()
        self.status.setObjectName("hint")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QDialogButtonBox()
        self.add_button = QPushButton("Ajouter")
        self.add_button.setObjectName("primary")
        self.add_button.setDefault(True)
        buttons.addButton(self.add_button, QDialogButtonBox.ButtonRole.AcceptRole)
        cancel = buttons.addButton("Annuler", QDialogButtonBox.ButtonRole.RejectRole)
        cancel.setAutoDefault(False)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._load()
        self.search.setFocus()

    # ---------------------------------------------------------- chargement
    def _load(self) -> None:
        cached = self._reaper.installed_fx if self._reaper is not None else None
        if cached:
            self._set_plugins(vst_scanner.collect_plugins(cached, None))
            return
        if self._reaper is not None and self._reaper.is_connected:
            self.status.setText("Chargement de la liste depuis REAPER…")
            self._reaper.installed_fx_ready.connect(self._on_reaper_list)
            self._reaper.request_installed_fx()
            # Filet de sécurité si REAPER ne répond pas (le contexte `self`
            # annule le minuteur si le dialogue est fermé avant)
            QTimer.singleShot(REAPER_WAIT_MS, self,
                              lambda: self._plugins or self._on_reaper_list(None))
        else:
            self._on_reaper_list(None)

    def _on_reaper_list(self, entries) -> None:
        if self._plugins:
            return
        if self._reaper is not None:
            try:
                self._reaper.installed_fx_ready.disconnect(self._on_reaper_list)
            except (RuntimeError, TypeError):
                pass
        plugins = vst_scanner.collect_plugins(entries, self._resource_path, self._extra_dirs)
        self._set_plugins(plugins)
        if not entries:
            self.status.setText("Liste issue du scan de REAPER et des dossiers VST3. "
                                "Si un plugin manque, ouvre REAPER une fois pour qu'il le détecte.")

    def _set_plugins(self, plugins: list[PluginInfo]) -> None:
        self._plugins = plugins
        kinds = sorted({p.kind for p in plugins if p.kind})
        self.kind.blockSignals(True)
        self.kind.clear()
        self.kind.addItem(ALL_TYPES)
        self.kind.addItems(kinds)
        self.kind.blockSignals(False)
        if not self.status.text() or self.status.text().startswith("Chargement"):
            self.status.setText(f"{len(plugins)} plugins disponibles.")
        self._refilter()

    # ------------------------------------------------------------ filtrage
    def _refilter(self) -> None:
        words = _fold(self.search.text()).split()
        kind = self.kind.currentText()
        self.list.clear()
        for plugin in self._plugins:
            if kind not in (ALL_TYPES, "") and plugin.kind != kind:
                continue
            haystack = _fold(f"{plugin.name} {plugin.vendor} {plugin.kind}")
            if any(word not in haystack for word in words):
                continue
            suffix = " · instrument" if plugin.instrument else ""
            item = QListWidgetItem(f"{plugin.name}    {plugin.kind}{suffix}")
            item.setData(Qt.ItemDataRole.UserRole, plugin)
            item.setToolTip(plugin.add_name)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.add_button.setEnabled(self.list.currentItem() is not None)

    def _accept(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        self.selected = item.data(Qt.ItemDataRole.UserRole)
        self.accept()
