"""
Choix du projet REAPER ouvert par REKA : projet vierge (aucun effet), projets
récents, ou n'importe quel .rpp. On peut le définir comme projet par défaut et
demander le choix à chaque démarrage.

Chaque strip affiche la piste du projet qui écoute ses canaux Voicemeeter :
un projet existant est utilisé tel quel, son câblage n'est jamais modifié.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
                               QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
                               QWidget)

from core import config
from core.process_manager import blank_project_path

MAX_RECENT = 8
BLANK_LABEL = "Projet vierge REKA — aucun effet"


def default_project(cfg: dict) -> str:
    return cfg.get("reaper", {}).get("project", "") or ""


def remember_project(cfg: dict, path: str, make_default: bool, ask_on_start: bool) -> None:
    """Enregistre le choix dans la config : projets récents, défaut, question au démarrage."""
    section = cfg.setdefault("reaper", {})
    if path:
        recent = [p for p in section.get("recent_projects", []) if not _same(p, path)]
        section["recent_projects"] = [path, *recent][:MAX_RECENT]
    current_default = section.get("project", "") or ""
    if make_default:
        section["project"] = path
    elif path and current_default and _same(path, current_default):
        section["project"] = ""      # case décochée sur le projet par défaut : retour au vierge
    section["ask_project_on_start"] = ask_on_start


def _same(a: str, b: str) -> bool:
    return Path(a).as_posix().lower() == Path(b).as_posix().lower()


class ProjectDialog(QDialog):
    def __init__(self, cfg: dict, current: str = "", startup: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Projet REAPER — {config.APP_NAME}")
        self.setMinimumSize(520, 420)
        self._cfg = cfg
        self._current = current
        self.selected: str = default_project(cfg)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        title = QLabel("Quel projet REAPER ouvrir ?" if startup else "Projet REAPER")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        hint = QLabel("Chaque strip affiche la piste du projet qui écoute ses canaux. "
                      "Le câblage de ton projet n'est jamais modifié.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _item: self._accept())
        self.list.currentItemChanged.connect(lambda *_: self._on_selection())
        layout.addWidget(self.list, 1)

        self.path_label = QLabel()
        self.path_label.setObjectName("hint")
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.path_label)

        browse_row = QHBoxLayout()
        browse = QPushButton("Parcourir…")
        browse.clicked.connect(self._browse)
        browse_row.addWidget(browse)
        browse_row.addStretch(1)
        layout.addLayout(browse_row)

        self.make_default = QCheckBox("Ouvrir ce projet automatiquement au démarrage (par défaut)")
        layout.addWidget(self.make_default)
        self.ask = QCheckBox("Me demander quel projet ouvrir à chaque démarrage")
        self.ask.setChecked(bool(cfg.get("reaper", {}).get("ask_project_on_start", False)))
        layout.addWidget(self.ask)

        buttons = QDialogButtonBox()
        self.open_button = QPushButton("Ouvrir")
        self.open_button.setObjectName("primary")
        self.open_button.setDefault(True)
        buttons.addButton(self.open_button, QDialogButtonBox.ButtonRole.AcceptRole)
        cancel = buttons.addButton("Annuler", QDialogButtonBox.ButtonRole.RejectRole)
        cancel.setAutoDefault(False)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._fill()

    # ------------------------------------------------------------ liste
    def _fill(self) -> None:
        default = default_project(self._cfg)
        recents = [p for p in self._cfg.get("reaper", {}).get("recent_projects", [])
                   if Path(p).is_file()]
        if default and Path(default).is_file() and not any(_same(default, p) for p in recents):
            recents.insert(0, default)
        self.list.clear()
        self._add_item(BLANK_LABEL, "")
        for path in recents:
            self._add_item(Path(path).stem, path)
        # Sélection initiale : le projet ouvert s'il est connu, sinon le défaut
        target = self._current or default
        for row in range(self.list.count()):
            if self._matches(self.list.item(row).data(Qt.ItemDataRole.UserRole), target):
                self.list.setCurrentRow(row)
                break
        else:
            self.list.setCurrentRow(0)

    def _add_item(self, label: str, path: str) -> QListWidgetItem:
        tags = []
        if self._matches(path, default_project(self._cfg)):
            tags.append("★ par défaut")
        if self._current and self._matches(path, self._current):
            tags.append("● ouvert")
        item = QListWidgetItem(label + (f"    {' · '.join(tags)}" if tags else ""))
        item.setData(Qt.ItemDataRole.UserRole, path)
        item.setToolTip(path or "Projet REKA de départ, sans aucun effet")
        self.list.addItem(item)
        return item

    def _matches(self, path: str, other: str) -> bool:
        """"" (projet vierge) correspond aussi au fichier du projet vierge."""
        blank = str(blank_project_path(self._cfg))
        a = path or blank
        b = other or blank
        return _same(a, b)

    def _browse(self) -> None:
        start = str(Path(self.selected).parent) if self.selected else str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "Choisir un projet REAPER", start,
                                              "Projets REAPER (*.rpp)")
        if not path:
            return
        for row in range(self.list.count()):
            if self._matches(self.list.item(row).data(Qt.ItemDataRole.UserRole), path):
                self.list.setCurrentRow(row)
                return
        self.list.setCurrentItem(self._add_item(Path(path).stem, path))

    def _on_selection(self) -> None:
        item = self.list.currentItem()
        self.open_button.setEnabled(item is not None)
        if item is None:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        self.path_label.setText(path or "Projet de départ de REKA : 6 pistes câblées, aucun effet.")
        self.make_default.setChecked(self._matches(path, default_project(self._cfg)))

    def _accept(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        self.selected = item.data(Qt.ItemDataRole.UserRole)
        remember_project(self._cfg, self.selected, self.make_default.isChecked(),
                         self.ask.isChecked())
        self.accept()
