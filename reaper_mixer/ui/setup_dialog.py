"""
Menu de sélection des moteurs : édition de Voicemeeter et moteur d'effets
(REAPER, FL Studio, Equalizer APO ou aucun).

Affiché au premier lancement (avant de démarrer quoi que ce soit), avec
`REKA.exe --setup`, et depuis Réglages → Général. Chaque choix indique s'il
est installé sur ce PC et le délai qu'il ajoute au son.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QButtonGroup, QDialog, QGroupBox, QHBoxLayout, QLabel, QPushButton,
                               QRadioButton, QVBoxLayout, QWidget)

from core import config
from core.effects import HOST_APO, HOST_FLSTUDIO, HOST_NONE, HOST_REAPER, HOSTS, host_kind
from core.voicemeeter_ctrl import EDITIONS, edition

log = logging.getLogger(__name__)


def detect_editions(cfg: dict) -> dict[str, bool]:
    from core.process_manager import installed_voicemeeter_kinds

    try:
        installed = set(installed_voicemeeter_kinds(cfg.get("voicemeeter", {}).get("path")))
    except Exception as exc:          # détection seulement : jamais bloquante
        log.warning("Détection de Voicemeeter impossible : %s", exc)
        installed = set()
    return {kind: kind in installed for kind in EDITIONS}


def detect_hosts(cfg: dict) -> dict[str, bool]:
    from core.apo_ctrl import find_config_dir
    from core.process_manager import FLSTUDIO_SPEC, REAPER_SPEC, find_executable

    found = {HOST_NONE: True}
    for key, spec in ((HOST_REAPER, REAPER_SPEC), (HOST_FLSTUDIO, FLSTUDIO_SPEC)):
        try:
            found[key] = find_executable(spec, cfg.get(key, {}).get("path")) is not None
        except Exception as exc:
            log.warning("Détection de %s impossible : %s", spec.name, exc)
            found[key] = False
    try:
        found[HOST_APO] = find_config_dir(cfg) is not None
    except Exception as exc:
        log.warning("Détection d'Equalizer APO impossible : %s", exc)
        found[HOST_APO] = False
    return found


DIALOG_WIDTH = 600
TEXT_WIDTH = DIALOG_WIDTH - 2 * 18 - 30      # marges du dialogue et des groupes


def _wrapped(text: str, width: int, indent: int = 0) -> QLabel:
    """Texte replié à hauteur fixe : Qt calcule mal la hauteur des textes
    repliés dans des groupes, qui seraient sinon coupés."""
    label = QLabel(text)
    label.setObjectName("hint")
    label.setWordWrap(True)
    label.setContentsMargins(indent, 0, 0, 0)
    rect = label.fontMetrics().boundingRect(QRect(0, 0, width - indent, 10_000),
                                            Qt.TextFlag.TextWordWrap, text)
    label.setFixedHeight(rect.height() + 6)
    return label


def _status(installed: bool) -> QLabel:
    label = QLabel("✔ installé" if installed else "introuvable sur ce PC")
    label.setObjectName("ok" if installed else "warning")
    return label


class SetupDialog(QDialog):
    def __init__(self, cfg: dict, first_run: bool = False, parent: QWidget | None = None,
                 editions_found: dict[str, bool] | None = None,
                 hosts_found: dict[str, bool] | None = None) -> None:
        super().__init__(parent)
        self._cfg = cfg
        self.setWindowTitle(f"{config.APP_NAME} — moteurs audio")
        self.setFixedWidth(DIALOG_WIDTH)
        editions_found = editions_found if editions_found is not None else detect_editions(cfg)
        hosts_found = hosts_found if hosts_found is not None else detect_hosts(cfg)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        header = QHBoxLayout()
        logo = QLabel()
        pixmap = QPixmap(str(config.resource_path("assets/reka_mark.png")))
        if not pixmap.isNull():
            logo.setPixmap(pixmap.scaledToHeight(34, Qt.TransformationMode.SmoothTransformation))
        header.addWidget(logo)
        title = QLabel("Bienvenue dans REKA" if first_run else "Moteurs audio")
        title.setObjectName("dialogTitle")
        header.addWidget(title)
        header.addStretch(1)
        layout.addLayout(header)
        layout.addWidget(_wrapped(
            "Choisis la version de Voicemeeter à piloter et l'application qui applique les effets "
            "à tes strips. Tu pourras changer ces choix plus tard dans Réglages → Général.",
            DIALOG_WIDTH - 36))

        # --- Voicemeeter
        group = QGroupBox("Voicemeeter")
        box = QVBoxLayout(group)
        self.edition_group = QButtonGroup(self)
        current_kind = edition(cfg).kind
        if not any(editions_found.values()) or editions_found.get(current_kind):
            chosen_kind = current_kind
        else:
            chosen_kind = next(kind for kind, ok in editions_found.items() if ok)
        self.edition_radios: dict[str, QRadioButton] = {}
        for kind, ed in EDITIONS.items():
            row = QHBoxLayout()
            radio = QRadioButton(ed.label)
            radio.setProperty("kind", kind)
            radio.setChecked(kind == chosen_kind)
            self.edition_group.addButton(radio)
            self.edition_radios[kind] = radio
            row.addWidget(radio)
            row.addStretch(1)
            row.addWidget(_status(editions_found.get(kind, False)))
            box.addLayout(row)
        layout.addWidget(group)

        # --- Effets
        group = QGroupBox("Effets")
        box = QVBoxLayout(group)
        box.setSpacing(8)
        self.host_group = QButtonGroup(self)
        self.host_radios: dict[str, QRadioButton] = {}
        current_host = host_kind(cfg)
        for key, info in HOSTS.items():
            row = QHBoxLayout()
            radio = QRadioButton(info.label)
            radio.setProperty("host", key)
            radio.setChecked(key == current_host)
            self.host_group.addButton(radio)
            self.host_radios[key] = radio
            row.addWidget(radio)
            row.addStretch(1)
            row.addWidget(_status(hosts_found.get(key, False)))
            box.addLayout(row)
            box.addWidget(_wrapped(f"{info.description}\nDélai : {info.latency}", TEXT_WIDTH, 24))
        layout.addWidget(group)

        footer = QHBoxLayout()
        footer.addStretch(1)
        cancel = QPushButton("Quitter" if first_run else "Annuler")
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        self.continue_button = QPushButton("Continuer" if first_run else "Enregistrer")
        self.continue_button.setObjectName("primary")
        self.continue_button.setDefault(True)
        self.continue_button.clicked.connect(self.accept)
        footer.addWidget(self.continue_button)
        layout.addLayout(footer)

    @property
    def chosen_kind(self) -> str:
        button = self.edition_group.checkedButton()
        return button.property("kind") if button is not None else edition(self._cfg).kind

    @property
    def chosen_host(self) -> str:
        button = self.host_group.checkedButton()
        return button.property("host") if button is not None else host_kind(self._cfg)

    def accept(self) -> None:
        self._cfg.setdefault("voicemeeter", {})["kind"] = self.chosen_kind
        self._cfg.setdefault("effects", {})["host"] = self.chosen_host
        self._cfg["setup_done"] = True
        log.info("Moteurs choisis : Voicemeeter %s, effets %s.", self.chosen_kind, self.chosen_host)
        super().accept()
