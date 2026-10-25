"""
Réglages de REKA, en onglets :
  - Général    : projet REAPER, édition de Voicemeeter (Potato / Banana),
                 chemins des moteurs
  - Audio      : carte son de REAPER, affichage des moteurs, options
  - Apparence  : thème de couleur, image d'arrière-plan, transparence des
                 panneaux, fenêtre
  - Couleurs   : couleur de chaque élément (knobs, Muet, VU…), pour toute
                 la console, un canal ou le Master
  - À propos   : version, développeurs, mises à jour, journaux

Les options s'appliquent tout de suite ; tout est écrit dans la config à la
fermeture. Les chemins et l'édition de Voicemeeter demandent un redémarrage.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from PySide6.QtCore import QPointF, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QConicalGradient, QDesktopServices, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QCheckBox, QColorDialog, QDialog,
                               QComboBox, QFileDialog, QFontComboBox, QFormLayout, QGroupBox,
                               QFrame, QHBoxLayout, QLabel, QScrollArea,
                               QLineEdit, QPushButton, QRadioButton, QSlider, QTabWidget,
                               QVBoxLayout, QWidget)

from core import config
from core.process_manager import is_blank_project
from core.effects import HOST_APO, HOST_FLSTUDIO, HOST_REAPER, HOSTS, host_kind
from core.voicemeeter_ctrl import EDITIONS, edition, strip_map
from ui import styles
from ui.project_dialog import default_project

log = logging.getLogger(__name__)

IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.bmp *.webp *.gif)"
BACKGROUND_STEM = "background"


def project_label(path: str) -> str:
    if not path:
        return "Projet vierge REKA (aucun effet)"
    if is_blank_project(path):
        return f"Projet vierge REKA — {path}"
    return f"{Path(path).stem} — {path}"


class Swatch(QAbstractButton):
    """Pastille de couleur d'un thème ; « custom » = dégradé arc-en-ciel."""

    SIZE = 30

    def __init__(self, theme: str, color: str | None, tooltip: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.color = color
        self.setCheckable(True)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(self.width() / 2, self.height() / 2)
        selected = self.isChecked() or (self.theme == "custom" and self.color is not None)
        if selected:
            p.setPen(QPen(QColor(styles.TEXT_PRIMARY), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(center, self.SIZE / 2 - 1.5, self.SIZE / 2 - 1.5)
        radius = self.SIZE / 2 - 5.5 if selected else self.SIZE / 2 - 3
        if self.underMouse() and not selected:
            radius += 1
        if self.color is not None:
            p.setBrush(QColor(self.color))
        else:
            rainbow = QConicalGradient(center, 90)
            for stop, hue in ((0.0, 0), (0.17, 60), (0.33, 120), (0.5, 180), (0.67, 240),
                              (0.83, 300), (1.0, 359)):
                rainbow.setColorAt(stop, QColor.fromHsv(hue, 200, 255))
            p.setBrush(rainbow)
        p.setPen(QPen(QColor(0, 0, 0, 120), 1))
        p.drawEllipse(center, radius, radius)

    def enterEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(event)


class ColorButton(QAbstractButton):
    """Rectangle de couleur cliquable (choix d'une couleur d'élément) ; liseré
    clair quand la couleur a été choisie pour cette portée."""

    def __init__(self, color: str, custom: bool, tooltip: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.color = color
        self.custom = custom
        self.setFixedSize(52, 22)
        self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(styles.TEXT_PRIMARY if self.custom or self.underMouse()
                             else styles.BORDER_COLUMN), 1.5 if self.custom else 1))
        p.setBrush(QColor(self.color))
        p.drawRoundedRect(rect, 6, 6)

    def enterEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(event)


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("hint")
    label.setWordWrap(True)
    return label


class SettingsDialog(QDialog):
    optionsChanged = Signal()        # insert / création de pistes modifiés
    projectRequested = Signal()      # « Changer de projet… »
    appearanceChanged = Signal()     # arrière-plan / transparence modifiés
    windowResetRequested = Signal()  # « Taille compacte »
    restartRequested = Signal()      # changement d'édition de Voicemeeter
    updateRequested = Signal()       # « Installer la mise à jour »
    stripRenamed = Signal(int, str)  # nom d'un canal modifié
    latencyChanged = Signal()        # tampon de Voicemeeter / périphériques APO modifiés

    BUFFER_CHOICES = ((0, "Ne pas modifier"), (512, "Standard — 512 échantillons"),
                      (256, "Réduit — 256 échantillons"), (128, "Minimal — 128 échantillons (PC puissant)"))

    def __init__(self, cfg: dict, processes, reaper, vm, parent: QWidget | None = None,
                 persist: bool = True, updater=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Réglages — {config.APP_NAME}")
        self.setMinimumWidth(600)
        self.resize(620, 760)
        self._cfg = cfg
        self._persist = persist
        self._processes = processes
        self._reaper = reaper
        self._vm = vm
        self._updater = updater
        # Édition et moteur d'effets réellement en cours (un choix ne s'applique qu'au redémarrage)
        self._running_kind = getattr(getattr(vm, "edition", None), "kind", None) or edition(cfg).kind
        self._running_host = getattr(reaper, "kind", None) or host_kind(cfg)
        self.show_reaper: QCheckBox | None = None
        # Fenêtres déjà affichées (bouton du Master) : laissées affichées en partant
        self._shown_at_open = {key: self._window_visible(key) for key in ("voicemeeter", "reaper")}
        self.device_label: QLabel | None = None
        self.project_open: QLabel | None = None

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

        self.tabs = QTabWidget()
        general = [self._engines_group()]
        if self._running_host == HOST_REAPER:
            general.insert(0, self._project_group())
        self.tabs.addTab(self._tab(*general), "Général")
        self.tabs.addTab(self._tab(self._names_group()), "Canaux")
        audio = {HOST_REAPER: self._reaper_group, HOST_FLSTUDIO: self._flstudio_group,
                 HOST_APO: self._apo_group}.get(self._running_host)
        self.tabs.addTab(self._tab(*([audio()] if audio else []), self._latency_group(),
                                   self._options_group()), "Audio")
        self.tabs.addTab(self._tab(self._theme_group(), self._widgets_group(), self._background_group(),
                                   self._window_group()), "Apparence")
        self.tabs.addTab(self._tab(self._colors_group()), "Couleurs")
        self.tabs.addTab(self._about_tab(), "À propos")
        layout.addWidget(self.tabs, 1)

        footer = QHBoxLayout()
        credits = _hint(f"{config.APP_NAME} {config.APP_VERSION} · par {config.APP_AUTHORS}")
        credits.setWordWrap(False)
        footer.addWidget(credits)
        footer.addStretch(1)
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
        self._show_edition()
        self._show_latency()
        self._show_background()

    @staticmethod
    def _tab(*groups: QWidget) -> QWidget:
        """Page d'onglet, avec défilement si son contenu dépasse la fenêtre
        (sinon Qt écraserait les lignes pour tout faire tenir)."""
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 8, 6, 0)
        box.setSpacing(12)
        for group in groups:
            box.addWidget(group)
        box.addStretch(1)
        scroll = QScrollArea()
        scroll.setObjectName("tabScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(page)
        return scroll

    # ------------------------------------------------------------ Général
    def _project_group(self) -> QGroupBox:
        group = QGroupBox("Projet REAPER")
        box = QVBoxLayout(group)
        self.project_open = QLabel()
        self.project_open.setWordWrap(True)
        self.project_open.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(self.project_open)
        self.project_default = _hint("")
        box.addWidget(self.project_default)
        change = QPushButton("Changer de projet…")
        change.clicked.connect(self._change_project)
        change.setEnabled(self._reaper is not None)
        box.addWidget(change)
        return group

    def _engines_group(self) -> QGroupBox:
        group = QGroupBox("Moteurs audio")
        form = QFormLayout(group)

        editions = QHBoxLayout()
        self.edition_group = QButtonGroup(self)
        for kind, ed in EDITIONS.items():
            radio = QRadioButton(ed.label)
            radio.setProperty("kind", kind)
            radio.setChecked(kind == edition(self._cfg).kind)
            self.edition_group.addButton(radio)
            editions.addWidget(radio)
        editions.addStretch(1)
        self.edition_group.buttonToggled.connect(self._on_edition)
        form.addRow("Voicemeeter", editions)
        self.host_choice = QComboBox()
        for key, info in HOSTS.items():
            self.host_choice.addItem(info.label, key)
            self.host_choice.setItemData(self.host_choice.count() - 1,
                                         f"{info.description}\nDélai : {info.latency}",
                                         Qt.ItemDataRole.ToolTipRole)
        self.host_choice.setCurrentIndex(max(0, self.host_choice.findData(host_kind(self._cfg))))
        self.host_choice.currentIndexChanged.connect(self._on_host)
        form.addRow("Effets", self.host_choice)
        restart_row = QHBoxLayout()
        self.edition_hint = _hint("")
        restart_row.addWidget(self.edition_hint, 1)
        self.restart_button = QPushButton("Redémarrer REKA")
        self.restart_button.clicked.connect(self._restart)
        restart_row.addWidget(self.restart_button)
        form.addRow(restart_row)

        self.vm_path = self._path_row(form, "Voicemeeter", "voicemeeter")
        self.reaper_path = self._path_row(form, "REAPER", "reaper")
        self.fl_path = self._path_row(form, "FL Studio", "flstudio")
        self.fl_project = self._path_row(form, "Projet FL", "flstudio", "project",
                                         "Projet FL Studio (*.flp)", "Aucun (FL Studio s'ouvre vide)")
        self.apo_dir = self._path_row(form, "Equalizer APO", "apo", "config_dir", None,
                                      "Dossier « config » détecté automatiquement")
        form.addRow(_hint("Laisse vide pour la détection automatique. "
                          "Pris en compte au prochain démarrage."))
        return group

    def _path_row(self, form: QFormLayout, label: str, section: str, key: str = "path",
                  file_filter: str | None = "Exécutable (*.exe)",
                  placeholder: str = "Détection automatique") -> QLineEdit:
        row = QHBoxLayout()
        edit = QLineEdit(str(self._cfg.get(section, {}).get(key, "") or ""))
        edit.setPlaceholderText(placeholder)
        row.addWidget(edit, 1)
        browse = QPushButton("Parcourir…")
        browse.clicked.connect(lambda: self._browse(edit, label, file_filter))
        row.addWidget(browse)
        form.addRow(label, row)
        return edit

    # -------------------------------------------------------------- Audio
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
        self.show_reaper.setChecked(self._shown_at_open["reaper"])
        self.show_reaper.toggled.connect(lambda on: self._show_engine("reaper", on))
        box.addWidget(self.show_reaper)
        return group

    def _flstudio_group(self) -> QGroupBox:
        group = QGroupBox("FL Studio")
        box = QVBoxLayout(group)
        help_text = self._reaper.wiring_help() if hasattr(self._reaper, "wiring_help") else ""
        wiring = QLabel(help_text)
        wiring.setWordWrap(True)
        wiring.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(wiring)
        box.addWidget(_hint("Le badge FX d'une strip envoie son son à FL Studio (insert Voicemeeter). "
                            "Allume-le seulement quand la piste du mixer est câblée : sinon la strip "
                            "devient muette."))
        show = QPushButton("Afficher FL Studio")
        show.clicked.connect(lambda: self._reaper.show_app())
        box.addWidget(show)
        return group

    def _apo_group(self) -> QGroupBox:
        group = QGroupBox("Equalizer APO")
        box = QVBoxLayout(group)
        host = self._reaper
        folder = getattr(host, "config_dir", None)
        status = QLabel(f"Configuration : {folder}" if folder is not None else
                        "⚠ Equalizer APO est introuvable : installe-le, puis indique son dossier "
                        "« config » dans Réglages → Général si besoin.")
        status.setObjectName("hint" if folder is not None else "warning")
        status.setWordWrap(True)
        box.addWidget(status)
        form = QFormLayout()
        names = list(self._cfg.get("ui", {}).get("strip_names", []))
        endpoints = [e.name for e in host.endpoints()] if hasattr(host, "endpoints") else []
        self.apo_devices: list[QComboBox] = []
        for index in range(len(strip_map(self._cfg))):
            combo = QComboBox()
            combo.setEditable(True)
            combo.addItems(endpoints)
            combo.setCurrentText(host.strip_device(index) if hasattr(host, "strip_device") else "")
            combo.lineEdit().setPlaceholderText("Périphérique Windows (où APO est installé)")
            combo.lineEdit().editingFinished.connect(
                lambda i=index, c=combo: self._set_apo_device(i, c.currentText()))
            combo.activated.connect(lambda _row, i=index, c=combo: self._set_apo_device(i, c.currentText()))
            self.apo_devices.append(combo)
            form.addRow(names[index] if index < len(names) else f"Strip {index + 1}", combo)
        box.addLayout(form)
        box.addWidget(_hint("Installe Equalizer APO sur ces périphériques (son « Configurator »). "
                            "Pour le micro, Voicemeeter doit ouvrir l'entrée en partage : MME, ou "
                            "WDM sans le mode exclusif (case ci-dessous)."))
        self.wdm_shared = QCheckBox("Entrées WDM de Voicemeeter en mode partagé (requis pour "
                                    "Equalizer APO sur le micro)")
        self.wdm_shared.setChecked(bool(self._cfg.get("voicemeeter", {}).get("wdm_shared", False)))
        self.wdm_shared.toggled.connect(self._set_wdm_shared)
        box.addWidget(self.wdm_shared)
        editor = QPushButton("Ouvrir l'éditeur d'Equalizer APO")
        editor.clicked.connect(lambda: host.show_app())
        box.addWidget(editor)
        return group

    def _latency_group(self) -> QGroupBox:
        group = QGroupBox("Délai de la voix")
        box = QVBoxLayout(group)
        self.latency_info = QLabel()
        self.latency_info.setWordWrap(True)
        box.addWidget(self.latency_info)
        row = QHBoxLayout()
        row.addWidget(QLabel("Tampon de Voicemeeter"))
        self.buffer_choice = QComboBox()
        for samples, label in self.BUFFER_CHOICES:
            self.buffer_choice.addItem(label, samples)
        current = int(self._cfg.get("voicemeeter", {}).get("buffer", 0) or 0)
        self.buffer_choice.setCurrentIndex(max(0, self.buffer_choice.findData(current)))
        row.addWidget(self.buffer_choice, 1)
        self.buffer_apply = QPushButton("Appliquer")
        self.buffer_apply.clicked.connect(self._apply_buffer)
        row.addWidget(self.buffer_apply)
        box.addLayout(row)
        self.buffer_hint = _hint(
            "Plus le tampon est petit, plus la voix arrive vite (≈ 2,7 ms de moins par tranche de "
            "128 échantillons à 48 kHz), mais plus il faut de CPU : si le son craque, remonte d'un "
            "cran. Voicemeeter redémarre son moteur audio (coupure d'une seconde). Sur le micro, "
            "évite les plugins marqués d'un point ambre (anticipation).")
        box.addWidget(self.buffer_hint)
        return group

    def _options_group(self) -> QGroupBox:
        group = QGroupBox("Options")
        box = QVBoxLayout(group)
        vm_cfg, reaper_cfg = self._cfg.setdefault("voicemeeter", {}), self._cfg.setdefault("reaper", {})
        self.manage_insert = QCheckBox(
            "Faire passer les strips par leurs effets (insert Voicemeeter automatique)")
        self.manage_insert.setChecked(bool(vm_cfg.get("manage_insert", True)))
        self.manage_insert.toggled.connect(lambda on: self._set_option("voicemeeter", "manage_insert", on))
        box.addWidget(self.manage_insert)
        self.manage_routing = QCheckBox(
            "Créer une piste REAPER quand on ajoute un effet à une strip qui n'en a pas")
        self.manage_routing.setChecked(bool(reaper_cfg.get("manage_routing", True)))
        self.manage_routing.toggled.connect(lambda on: self._set_option("reaper", "manage_routing", on))
        box.addWidget(self.manage_routing)
        self.show_vm = QCheckBox("Afficher la fenêtre de Voicemeeter pendant les réglages")
        self.show_vm.setChecked(self._shown_at_open["voicemeeter"])
        self.show_vm.toggled.connect(lambda on: self._show_engine("voicemeeter", on))
        box.addWidget(self.show_vm)
        self.close_vm = QCheckBox("Fermer Voicemeeter en quittant")
        self.close_vm.setChecked(bool(vm_cfg.get("terminate_on_exit", True)))
        box.addWidget(self.close_vm)
        host_section = self._running_host if self._running_host in (HOST_REAPER, HOST_FLSTUDIO) \
            else HOST_REAPER
        self.close_reaper = QCheckBox(f"Fermer {HOSTS[host_section].label} en quittant")
        self.close_reaper.setChecked(bool(self._cfg.get(host_section, {}).get(
            "terminate_on_exit", host_section != HOST_FLSTUDIO)))
        if host_section == HOST_FLSTUDIO:
            self.close_reaper.setToolTip("Décoché : FL Studio reste ouvert et réapparaît quand tu "
                                         "quittes REKA, pour enregistrer ton projet.")
        self.close_reaper.setVisible(self._running_host in (HOST_REAPER, HOST_FLSTUDIO))
        box.addWidget(self.close_reaper)
        self._close_section = host_section
        # Options propres à REAPER / à l'insert
        self.manage_routing.setVisible(self._running_host == HOST_REAPER)
        self.manage_insert.setVisible(self._running_host in (HOST_REAPER, HOST_FLSTUDIO))
        return group

    # ---------------------------------------------------------- Apparence
    def _theme_group(self) -> QGroupBox:
        group = QGroupBox("Thème de couleur")
        box = QVBoxLayout(group)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.theme_group = QButtonGroup(self)
        self.theme_swatches: dict[str, Swatch] = {}
        for key, (name, accent) in styles.THEMES.items():
            swatch = Swatch(key, accent, name)
            self.theme_group.addButton(swatch)
            self.theme_swatches[key] = swatch
            row.addWidget(swatch)
        self.custom_swatch = Swatch("custom", None, "Couleur personnalisée…")
        self.custom_swatch.setCheckable(False)
        self.custom_swatch.clicked.connect(self._choose_custom_theme)
        row.addWidget(self.custom_swatch)
        row.addStretch(1)
        box.addLayout(row)
        self.theme_label = _hint("")
        box.addWidget(self.theme_label)
        self.theme_group.buttonClicked.connect(lambda button: self.set_theme(button.theme))
        self._show_theme()
        return group

    def _widgets_group(self) -> QGroupBox:
        group = QGroupBox("Police et widgets")
        form = QFormLayout(group)
        self.font_choice = QFontComboBox()
        self.font_choice.setCurrentFont(styles.font(11))
        self.font_choice.setCurrentText(styles.FONT_FAMILY)
        self.font_choice.currentFontChanged.connect(
            lambda f: self._set_ui("font_family", f.family()))
        reset_font = QPushButton("Inter")
        reset_font.setToolTip("Police d'origine")
        reset_font.clicked.connect(lambda: self.font_choice.setCurrentText(styles.DEFAULT_FONT))
        row = QHBoxLayout()
        row.addWidget(self.font_choice, 1)
        row.addWidget(reset_font)
        form.addRow("Police", row)
        self.fader_choice = self._style_combo(styles.FADER_STYLES, styles.FADER_STYLE, "fader_style")
        form.addRow("Faders", self.fader_choice)
        self.vu_choice = self._style_combo(styles.VU_STYLES, styles.VU_STYLE, "vu_style")
        form.addRow("VU-mètres", self.vu_choice)
        return group

    def _style_combo(self, choices: dict, current: str, key: str) -> QComboBox:
        combo = QComboBox()
        for value, label in choices.items():
            combo.addItem(label, value)
        combo.setCurrentIndex(max(0, combo.findData(current)))
        combo.currentIndexChanged.connect(lambda _i: self._set_ui(key, combo.currentData()))
        return combo

    def _names_group(self) -> QGroupBox:
        group = QGroupBox("Noms des canaux")
        form = QFormLayout(group)
        names = list(self._cfg.get("ui", {}).get("strip_names", []))
        count = int(getattr(self._vm, "strip_count", 0) or len(names))
        self.name_edits: list[QLineEdit] = []
        for index in range(count):
            edit = QLineEdit(names[index] if index < len(names) else f"Strip {index + 1}")
            edit.setMaxLength(30)
            edit.editingFinished.connect(
                lambda index=index, edit=edit: self.stripRenamed.emit(index, edit.text().strip()))
            self.name_edits.append(edit)
            form.addRow(f"Canal {index + 1}", edit)
        form.addRow(_hint("Astuce : double-clic ou clic droit sur le nom d'un canal pour le renommer."))
        return group

    def _background_group(self) -> QGroupBox:
        group = QGroupBox("Arrière-plan")
        box = QVBoxLayout(group)
        self.background_label = QLabel()
        self.background_label.setWordWrap(True)
        box.addWidget(self.background_label)
        buttons = QHBoxLayout()
        choose = QPushButton("Choisir une image…")
        choose.clicked.connect(self._choose_background)
        buttons.addWidget(choose)
        self.remove_background = QPushButton("Fond d'origine")
        self.remove_background.clicked.connect(self._clear_background)
        buttons.addWidget(self.remove_background)
        buttons.addStretch(1)
        box.addLayout(buttons)

        ui_cfg = self._cfg.setdefault("ui", {})
        self.darkness = self._slider(box, "Assombrir l'image", 0, 90,
                                     round(float(ui_cfg.get("background_darkness", 0.45)) * 100),
                                     lambda v: self._set_ui("background_darkness", v / 100))
        self.opacity = self._slider(box, "Opacité des panneaux", 40, 100,
                                    round(float(ui_cfg.get("panel_opacity", 1.0)) * 100),
                                    lambda v: self._set_ui("panel_opacity", v / 100))
        box.addWidget(_hint("Baisse l'opacité des panneaux pour voir l'image à travers les strips."))
        return group

    def _slider(self, box: QVBoxLayout, label: str, low: int, high: int, value: int,
                on_change) -> QSlider:
        row = QHBoxLayout()
        caption = QLabel(label)
        caption.setMinimumWidth(150)
        row.addWidget(caption)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(low, high)
        slider.setValue(max(low, min(high, value)))
        row.addWidget(slider, 1)
        percent = QLabel(f"{slider.value()} %")
        percent.setMinimumWidth(40)
        row.addWidget(percent)
        slider.valueChanged.connect(lambda v: (percent.setText(f"{v} %"), on_change(v)))
        box.addLayout(row)
        return slider

    def _window_group(self) -> QGroupBox:
        group = QGroupBox("Fenêtre")
        box = QVBoxLayout(group)
        compact = QPushButton("Taille compacte (comme Voicemeeter)")
        compact.clicked.connect(self.windowResetRequested)
        box.addWidget(compact)
        self.minimize_to_tray = QCheckBox(
            "Réduire dans la zone de notification (à côté de l'horloge)")
        self.minimize_to_tray.setChecked(bool(self._cfg.get("ui", {}).get("minimize_to_tray", False)))
        self.minimize_to_tray.toggled.connect(lambda on: self._cfg.setdefault("ui", {})
                                              .__setitem__("minimize_to_tray", on))
        box.addWidget(self.minimize_to_tray)
        box.addWidget(_hint("REKA reste ouverte et le son continue : clique sur l'icône REKA "
                            "près de l'horloge pour la rouvrir."))
        self.show_media = QCheckBox("Afficher la musique en cours dans le Master "
                                    "(pochette, précédent / pause / suivant)")
        self.show_media.setChecked(bool(self._cfg.get("ui", {}).get("show_media", True)))
        self.show_media.toggled.connect(lambda on: self._set_ui("show_media", on))
        box.addWidget(self.show_media)
        return group

    # ----------------------------------------------------------- Couleurs
    def _colors_group(self) -> QGroupBox:
        group = QGroupBox("Couleur de chaque élément")
        box = QVBoxLayout(group)
        row = QHBoxLayout()
        row.addWidget(QLabel("Appliquer à"))
        self.color_scope_choice = QComboBox()
        self.color_scope_choice.addItem("Toute la console", None)
        names = list(self._cfg.get("ui", {}).get("strip_names", []))
        count = int(getattr(self._vm, "strip_count", 0) or len(names))
        for index in range(count):
            name = names[index] if index < len(names) else f"Strip {index + 1}"
            self.color_scope_choice.addItem(f"Canal {index + 1} — {name}", index)
        self.color_scope_choice.addItem("Master", styles.MASTER_SCOPE)
        self.color_scope_choice.currentIndexChanged.connect(lambda _i: self._show_colors())
        row.addWidget(self.color_scope_choice, 1)
        box.addLayout(row)
        self.color_form = QFormLayout()
        box.addLayout(self.color_form)
        self.color_buttons: dict[str, ColorButton] = {}
        self.color_resets: dict[str, QPushButton] = {}
        self.color_labels: dict[str, str] = {}
        box.addWidget(_hint("Clic sur une couleur pour la changer, « Défaut » pour revenir à celle "
                            "du thème. La couleur d'un canal passe avant celle de toute la console."))
        reset_all = QPushButton("Tout réinitialiser")
        reset_all.clicked.connect(self.reset_element_colors)
        reset_row = QHBoxLayout()
        reset_row.addStretch(1)
        reset_row.addWidget(reset_all)
        box.addLayout(reset_row)
        self._show_colors()
        return group

    def _color_elements(self, scope) -> list[tuple[str, str]]:
        """(élément, nom affiché) proposés pour une portée (console, canal, Master)."""
        routing = self._cfg.get("voicemeeter", {}).get("routing", [])
        labels = dict(styles.ELEMENT_LABELS)
        for route in range(4):
            label = routing[route].get("label", f"Envoi {route + 1}") if route < len(routing) else ""
            labels[f"send{route}"] = f"Knob {label}" if label else ""
        if scope == styles.MASTER_SCOPE:
            keys = styles.MASTER_ELEMENTS
        elif scope is None:
            keys = styles.STRIP_ELEMENTS + tuple(k for k in styles.MASTER_ELEMENTS
                                                 if k not in styles.STRIP_ELEMENTS)
        else:
            keys = styles.STRIP_ELEMENTS
        return [(key, labels[key]) for key in keys if labels.get(key)]

    def _color_scope(self):
        return self.color_scope_choice.currentData()

    def _show_colors(self) -> None:
        """Lignes des éléments de la portée choisie (console, canal, Master)."""
        while self.color_form.rowCount():
            self.color_form.removeRow(0)
        self.color_buttons, self.color_resets, self.color_labels = {}, {}, {}
        scope = self._color_scope()
        for element, label in self._color_elements(scope):
            button = ColorButton(styles.element_default(element), False, label)
            button.clicked.connect(lambda _c=False, e=element, n=label: self._choose_element_color(e, n))
            reset = QPushButton("Défaut")
            reset.setToolTip("Couleur du thème" if scope is None else "Comme toute la console")
            reset.clicked.connect(lambda _c=False, e=element: self.set_element_color(e, self._color_scope(), None))
            row = QHBoxLayout()
            row.addWidget(button)
            row.addWidget(reset)
            row.addStretch(1)
            self.color_form.addRow(label, row)
            self.color_buttons[element], self.color_resets[element] = button, reset
            self.color_labels[element] = label
        self._refresh_colors()

    def _refresh_colors(self) -> None:
        """Couleurs affichées (en place : un bouton cliqué n'est pas détruit)."""
        scope = self._color_scope()
        colors = self._cfg.get("ui", {}).get("colors", {}) or {}
        for element, button in self.color_buttons.items():
            custom = styles.color_key(element, scope) in colors
            button.color = (styles.element_color(element, scope, colors)
                            or styles.element_default(element))
            button.custom = custom
            button.update()
            self.color_resets[element].setEnabled(custom)

    def _choose_element_color(self, element: str, label: str) -> None:
        scope = self._color_scope()
        colors = self._cfg.get("ui", {}).get("colors", {}) or {}
        current = QColor(styles.element_color(element, scope, colors) or styles.element_default(element))
        color = QColorDialog.getColor(current, self, f"Couleur — {label}")
        if color.isValid():
            self.set_element_color(element, scope, color.name())

    def set_element_color(self, element: str, scope, value: str | None) -> None:
        """Couleur d'un élément (None : retour à la couleur du thème / de la console),
        appliquée tout de suite."""
        colors = self._cfg.setdefault("ui", {}).setdefault("colors", {})
        key = styles.color_key(element, scope)
        if value is None:
            colors.pop(key, None)
        elif QColor(value).isValid():
            colors[key] = QColor(value).name()
        self.appearanceChanged.emit()
        self._refresh_colors()

    def reset_element_colors(self) -> None:
        self._cfg.setdefault("ui", {})["colors"] = {}
        self.appearanceChanged.emit()
        self._refresh_colors()

    # ----------------------------------------------------------- À propos
    def _about_tab(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 12, 0, 0)
        box.setSpacing(10)
        logo = QLabel()
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = QPixmap(str(config.resource_path("assets/reka_logo.png")))
        if not pixmap.isNull():
            logo.setPixmap(pixmap.scaledToWidth(300, Qt.TransformationMode.SmoothTransformation))
        box.addWidget(logo)
        build = getattr(self._updater, "build", None)
        name = QLabel(f"{config.APP_NAME} {build.describe() if build else config.APP_VERSION}")
        name.setObjectName("dialogTitle")
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(name)
        credits = QLabel(f"Développé par <b>{config.APP_AUTHORS}</b>")
        credits.setObjectName("credits")
        credits.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(credits)
        thanks = QLabel(f"Merci à <b>{config.APP_THANKS}</b> pour sa contribution")
        thanks.setObjectName("credits")
        thanks.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(thanks)
        about = _hint("Console de mixage pour Voicemeeter et REAPER.")
        about.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(about)
        box.addWidget(self._update_group())
        logs = QPushButton("Ouvrir le dossier des journaux")
        logs.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(config.app_data_dir()))))
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(logs)
        row.addStretch(1)
        box.addLayout(row)
        box.addStretch(1)
        return page

    def _update_group(self) -> QGroupBox:
        group = QGroupBox("Mises à jour")
        box = QVBoxLayout(group)
        update_cfg = self._cfg.setdefault("update", {})
        self.auto_update = QCheckBox("Vérifier les mises à jour au démarrage")
        self.auto_update.setChecked(bool(update_cfg.get("check_on_start", True)))
        self.auto_update.toggled.connect(lambda on: update_cfg.__setitem__("check_on_start", on))
        box.addWidget(self.auto_update)
        row = QHBoxLayout()
        self.update_status = _hint("")
        row.addWidget(self.update_status, 1)
        self.check_update = QPushButton("Rechercher une mise à jour")
        self.check_update.clicked.connect(self._check_update)
        row.addWidget(self.check_update)
        self.install_update = QPushButton("Installer…")
        self.install_update.setObjectName("primary")
        self.install_update.clicked.connect(self.updateRequested)
        self.install_update.hide()
        row.addWidget(self.install_update)
        box.addLayout(row)
        enabled = self._updater is not None and self._updater.enabled
        self.auto_update.setEnabled(enabled)
        self.check_update.setEnabled(enabled)
        if not enabled:
            self.update_status.setText("Version de développement : pas de mise à jour automatique.")
        elif self._updater.latest is not None:
            self._on_update_available(self._updater.latest)
        if enabled:
            self._updater.available.connect(self._on_update_available)
            self._updater.up_to_date.connect(self._on_up_to_date)
            self._updater.failed.connect(self._on_update_failed)
        return group

    def _check_update(self) -> None:
        if self._updater is not None and self._updater.check(manual=True):
            self.check_update.setEnabled(False)
            self.update_status.setText("Recherche en cours…")

    def _on_update_available(self, update) -> None:
        self.check_update.setEnabled(True)
        self.update_status.setText(f"REKA {update.describe()} est disponible.")
        self.install_update.show()

    def _on_up_to_date(self) -> None:
        self.check_update.setEnabled(True)
        self.update_status.setText("REKA est à jour.")
        self.install_update.hide()

    def _on_update_failed(self, message: str) -> None:
        self.check_update.setEnabled(True)
        self.update_status.setText(f"Vérification impossible : {message}")

    # ------------------------------------------------------------ actions
    def _browse(self, edit: QLineEdit, label: str, file_filter: str | None = "Exécutable (*.exe)") -> None:
        if file_filter is None:
            path = QFileDialog.getExistingDirectory(self, f"Dossier de {label}", edit.text())
        else:
            path, _ = QFileDialog.getOpenFileName(self, f"Localiser {label}", edit.text(), file_filter)
        if path:
            edit.setText(path)

    def _set_apo_device(self, index: int, name: str) -> None:
        host = self._reaper
        if hasattr(host, "strip_device") and host.strip_device(index) != name.strip():
            host.set_device(index, name)
            self.latencyChanged.emit()

    def _set_wdm_shared(self, shared: bool) -> None:
        self._cfg.setdefault("voicemeeter", {})["wdm_shared"] = shared
        if hasattr(self._vm, "set_wdm_exclusive"):
            self._vm.set_wdm_exclusive(not shared)

    def _apply_buffer(self) -> None:
        samples = int(self.buffer_choice.currentData() or 0)
        self._cfg.setdefault("voicemeeter", {})["buffer"] = samples
        if samples and hasattr(self._vm, "set_buffering"):
            self._vm.set_buffering(samples)
            self.buffer_hint.setText(f"Tampon de {samples} échantillons appliqué : Voicemeeter "
                                     "redémarre son moteur audio. Si le son craque, remonte d'un cran.")
        self.latencyChanged.emit()
        self._show_latency()

    def _show_latency(self) -> None:
        host = self._reaper
        text = host.latency_report() if host is not None else HOSTS[self._running_host].latency
        self.latency_info.setText(f"{HOSTS[self._running_host].label} — {text}")

    def _on_host(self, _index: int) -> None:
        self._cfg.setdefault("effects", {})["host"] = self.host_choice.currentData()
        self._show_edition()

    def _show_engine(self, key: str, visible: bool) -> None:
        if self._processes is not None:
            self._processes.set_window_visible(key, visible)

    def _window_visible(self, key: str) -> bool:
        query = getattr(self._processes, "is_window_visible", None)
        return bool(query(key)) if query is not None else False

    def _open_reaper_prefs(self) -> None:
        self.show_reaper.setChecked(True)
        self._reaper.open_preferences()

    def _change_project(self) -> None:
        self.projectRequested.emit()
        self._show_project(self._reaper.project_path if self._reaper is not None else "")

    def _set_option(self, section: str, key: str, value: bool) -> None:
        self._cfg.setdefault(section, {})[key] = value
        self.optionsChanged.emit()

    def _set_ui(self, key: str, value) -> None:
        self._cfg.setdefault("ui", {})[key] = value
        self.appearanceChanged.emit()

    def _on_edition(self, button, checked: bool) -> None:
        if checked:
            self._cfg.setdefault("voicemeeter", {})["kind"] = button.property("kind")
            self._show_edition()

    def _restart(self) -> None:
        self._save()
        self.restartRequested.emit()

    def _show_edition(self) -> None:
        chosen = edition(self._cfg)
        host = host_kind(self._cfg)
        edition_pending = chosen.kind != self._running_kind
        host_pending = host != self._running_host
        self.restart_button.setVisible(edition_pending or host_pending)
        if edition_pending:
            self.edition_hint.setText(
                f"Redémarre REKA pour passer à {chosen.label.split(' (')[0]}. "
                "Ferme l'autre édition de Voicemeeter si elle tourne encore.")
        elif host_pending:
            self.edition_hint.setText(f"Redémarre REKA pour utiliser {HOSTS[host].label} "
                                      "comme moteur d'effets.")
        else:
            self.edition_hint.setText(f"{chosen.label} : {len(strip_map(self._cfg))} strips, "
                                      f"effets : {HOSTS[host].label}.")

    def set_theme(self, theme: str) -> None:
        """Thème choisi (clé de styles.THEMES ou « #rrggbb »), appliqué tout de suite."""
        if styles.theme_accent(theme) is None:
            return
        self._set_ui("theme", theme)
        self._show_theme()

    def _choose_custom_theme(self) -> None:
        current = styles.theme_accent(self._cfg.get("ui", {}).get("theme")) or QColor(styles.GREEN_NEON)
        color = QColorDialog.getColor(current, self, "Couleur du thème")
        if color.isValid():
            self.set_theme(color.name())

    def _show_theme(self) -> None:
        theme = self._cfg.get("ui", {}).get("theme", styles.DEFAULT_THEME)
        if theme in styles.THEMES:
            self.theme_swatches[theme].setChecked(True)
            self.custom_swatch.color = None
            self.theme_label.setText(f"Thème : {styles.THEMES[theme][0]}")
        else:
            self.theme_group.setExclusive(False)
            for swatch in self.theme_swatches.values():
                swatch.setChecked(False)
            self.theme_group.setExclusive(True)
            self.custom_swatch.color = theme if styles.theme_accent(theme) else None
            self.theme_label.setText(f"Thème : couleur personnalisée {theme}")
        self.custom_swatch.update()

    def _choose_background(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choisir une image d'arrière-plan",
                                              str(Path.home()), IMAGE_FILTER)
        if path:
            self.set_background(Path(path))

    def set_background(self, source: Path) -> None:
        """Copie l'image dans %APPDATA%/REKA (elle reste valable même si
        l'original est déplacé) et l'applique."""
        target = config.app_data_dir() / f"{BACKGROUND_STEM}{source.suffix.lower()}"
        self._remove_background_files(keep=target if source.resolve() == target.resolve() else None)
        if source.resolve() != target.resolve():
            shutil.copyfile(source, target)
        self._set_ui("background_image", target.name)
        self._show_background()

    def _clear_background(self) -> None:
        self._remove_background_files()
        self._set_ui("background_image", "")
        self._show_background()

    @staticmethod
    def _remove_background_files(keep: Path | None = None) -> None:
        for old in config.app_data_dir().glob(f"{BACKGROUND_STEM}.*"):
            if keep is None or old != keep:
                old.unlink(missing_ok=True)

    def _show_background(self) -> None:
        name = self._cfg.get("ui", {}).get("background_image", "")
        has_image = bool(name)
        self.background_label.setText("Image personnalisée" if has_image else "Fond d'origine REKA")
        self.remove_background.setEnabled(has_image)
        self.darkness.setEnabled(has_image)

    def _show_project(self, path: str) -> None:
        if self.project_open is None:
            return
        self.project_open.setText(f"Ouvert : {project_label(path)}" if path
                                  else "Ouvert : (en attente de REAPER)")
        default = default_project(self._cfg)
        ask = self._cfg.get("reaper", {}).get("ask_project_on_start", False)
        self.project_default.setText(
            f"Au démarrage : {project_label(default)}"
            + (" — choix demandé à chaque démarrage" if ask else ""))

    def _show_device(self, device) -> None:
        if self.device_label is None:
            return
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
        # (sauf celles déjà affichées par les boutons du Master)
        for key, box in (("reaper", self.show_reaper), ("voicemeeter", self.show_vm)):
            if box is not None and box.isChecked() and not self._shown_at_open[key]:
                self._show_engine(key, False)
        connections = []
        if self._reaper is not None:
            connections += [(self._reaper.device_changed, self._show_device),
                            (self._reaper.project_changed, self._show_project)]
        if self._updater is not None and self._updater.enabled:
            connections += [(self._updater.available, self._on_update_available),
                            (self._updater.up_to_date, self._on_up_to_date),
                            (self._updater.failed, self._on_update_failed)]
        for signal, slot in connections:
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
        fl_cfg = self._cfg.setdefault("flstudio", {})
        fl_cfg["path"] = self.fl_path.text().strip()
        fl_cfg["project"] = self.fl_project.text().strip()
        self._cfg.setdefault("apo", {})["config_dir"] = self.apo_dir.text().strip()
        vm_cfg["terminate_on_exit"] = self.close_vm.isChecked()
        if not self.close_reaper.isHidden():
            self._cfg.setdefault(self._close_section, {})["terminate_on_exit"] = \
                self.close_reaper.isChecked()
        if not self._persist:
            return
        try:
            config.save_config(self._cfg)
        except OSError as exc:
            log.error("Réglages non sauvegardés : %s", exc)
