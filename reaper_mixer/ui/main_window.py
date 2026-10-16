"""
Fenêtre principale : strips alignés à gauche, panneau master à droite, et le
câblage entre l'interface et les moteurs (Voicemeeter, REAPER, processus).

Fenêtre « comme Voicemeeter » : taille compacte par défaut (strips + master,
sans grand vide), position et taille mémorisées, réduction possible dans la
zone de notification (icône à côté de l'horloge).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QEvent, QPointF, QProcess, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QPainter, QPixmap, QRadialGradient
from PySide6.QtWidgets import (QApplication, QGraphicsOpacityEffect, QHBoxLayout, QLabel,
                               QMainWindow, QMenu, QMessageBox, QSystemTrayIcon, QWidget)

from core import config
from core.process_manager import is_blank_project, reaper_project
from core.voicemeeter_ctrl import DEFAULT_ROUTING, MixerLevels, MixerParams
from ui import styles
from ui.master_widget import MasterWidget
from ui.project_dialog import ProjectDialog
from ui.settings_dialog import SettingsDialog
from ui.strip_widget import StripWidget
from ui.vst_dialog import VstDialog

log = logging.getLogger(__name__)

STRIP_GAP = 12
FOCUS_PLUGIN_DELAY_MS = 400
TOAST_MS = 4500
COMPACT_HEIGHT = 860          # hauteur de la fenêtre compacte (comme Voicemeeter)
MIN_HEIGHT = 680
# Augmenté quand la disposition des strips change (0.4 : Intellipan) : la
# taille mémorisée, prévue pour l'ancienne disposition, est alors ignorée.
LAYOUT_VERSION = 2
DEFAULT_DARKNESS = 0.45       # assombrissement de l'image d'arrière-plan
DEFAULT_PANEL_OPACITY = 1.0


def background_image_path(cfg: dict) -> Path | None:
    """Image d'arrière-plan choisie (copiée dans %APPDATA%/REKA), ou None."""
    path = config.resolve_path(cfg.get("ui", {}).get("background_image"), base=config.app_data_dir())
    return path if path is not None and path.is_file() else None


def restart_command() -> tuple[str, list[str]]:
    """Commande qui relance REKA (exe ou script), avec --restart pour attendre
    que l'instance actuelle ait fini de s'arrêter."""
    if getattr(sys, "frozen", False):
        return sys.executable, ["--restart"]
    return sys.executable, [str(Path(sys.argv[0]).resolve()), "--restart"]


def launch_restart() -> bool:
    """Lance la nouvelle instance de REKA (détachée) ; l'appelant quitte ensuite."""
    program, args = restart_command()
    # Exe PyInstaller « onefile » : sans cette variable, la nouvelle instance
    # se croirait un sous-processus de celle-ci et réutiliserait son dossier
    # temporaire, supprimé dès que cette instance se ferme.
    os.environ["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    if not QProcess.startDetached(program, args):
        return False
    log.info("Redémarrage de REKA : %s %s", program, " ".join(args))
    return True


def running_config(cfg: dict, vm) -> dict:
    """La config vue par la session en cours : l'édition de Voicemeeter qui
    tourne, même si une autre a été choisie pour le prochain démarrage (le
    projet vierge doit correspondre aux canaux réellement câblés). Les autres
    sections sont partagées : ce qui y est écrit va dans la vraie config."""
    kind = getattr(getattr(vm, "edition", None), "kind", None)
    if not kind or cfg.get("voicemeeter", {}).get("kind") == kind:
        return cfg
    return {**cfg, "voicemeeter": {**cfg.get("voicemeeter", {}), "kind": kind}}


class Background(QWidget):
    """Arrière-plan : image personnalisée (cadrée pour remplir, assombrie), ou
    noir profond avec un halo radial vert très sombre au centre-droit."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image: QPixmap | None = None
        self._scaled: QPixmap | None = None
        self._darkness = DEFAULT_DARKNESS

    def set_image(self, path: Path | None, darkness: float) -> None:
        pixmap = QPixmap(str(path)) if path is not None else None
        self._image = pixmap if pixmap is not None and not pixmap.isNull() else None
        self._darkness = max(0.0, min(0.95, darkness))
        self._scaled = None
        self.update()

    @property
    def has_image(self) -> bool:
        return self._image is not None

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._scaled = None
        super().resizeEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(styles.BG_WINDOW))
        w, h = self.width(), self.height()
        if self._image is not None:
            if self._scaled is None:
                self._scaled = self._image.scaled(
                    self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap((w - self._scaled.width()) // 2, (h - self._scaled.height()) // 2,
                         self._scaled)
            p.fillRect(self.rect(), styles.color(styles.BG_BLACK, self._darkness))
            return
        gradient = QRadialGradient(QPointF(w * 0.68, h * 0.42), max(w, h) * 0.62)
        gradient.setColorAt(0.0, styles.color(styles.BG_GLOW, 0.95))
        gradient.setColorAt(0.55, styles.color(styles.BG_GLOW, 0.35))
        gradient.setColorAt(1.0, styles.color(styles.BG_GLOW, 0.0))
        p.fillRect(self.rect(), gradient)


class Toast(QLabel):
    """Message discret en bas de la fenêtre, qui s'efface tout seul."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.refresh_style()
        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)
        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setDuration(400)
        self._fade.finished.connect(self._after_fade)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self._animate(0.0))
        self.hide()

    def refresh_style(self) -> None:
        self.setStyleSheet(
            f"background: {styles.BG_COLUMN}; border: 1px solid {styles.GREEN_DIM};"
            f"border-radius: 10px; padding: 8px 14px; color: {styles.TEXT_PRIMARY};")

    def show_message(self, text: str) -> None:
        self.setText(text)
        width = min(560, self.parentWidget().width() - 40)
        self.setFixedWidth(width)
        self.adjustSize()
        parent = self.parentWidget()
        self.move((parent.width() - self.width()) // 2, parent.height() - self.height() - 24)
        self.raise_()
        self.show()
        self._animate(1.0)
        self._timer.start(TOAST_MS)

    def _animate(self, target: float) -> None:
        self._fade.stop()
        self._fade.setStartValue(self._effect.opacity())
        self._fade.setEndValue(target)
        self._fade.start()

    def _after_fade(self) -> None:
        if self._effect.opacity() <= 0.01:
            self.hide()


class MainWindow(QMainWindow):
    def __init__(self, cfg: dict, vm, reaper, processes, persist: bool = True,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cfg = cfg
        self._persist = persist          # False en mode démo : la config n'est pas touchée
        self._vm = vm
        self._reaper = reaper
        self._processes = processes
        self._params: MixerParams | None = None
        self._vm_ok = True
        self._reaper_ok = reaper.is_connected
        self._device_warned = False
        self._tray_hint_shown = False
        self._restore_maximized = False
        self._tracks: list = reaper.strip_tracks   # piste REAPER de chaque strip (ou None)

        ui_cfg = cfg.setdefault("ui", {})
        self.setWindowTitle(config.APP_NAME)

        self.background = Background()
        self.setCentralWidget(self.background)
        layout = QHBoxLayout(self.background)
        layout.setContentsMargins(12, 12, 14, 12)
        layout.setSpacing(STRIP_GAP)

        routing = cfg.get("voicemeeter", {}).get("routing", DEFAULT_ROUTING)
        labels = [route.get("label", route.get("bus", "?")) for route in routing]
        names = list(ui_cfg.get("strip_names", []))
        sends = bool(getattr(vm, "supports_sends", True))
        pad_modes = list(ui_cfg.get("pad_modes", []))
        self.strips: list[StripWidget] = []
        for index in range(vm.strip_count):
            name = names[index] if index < len(names) else f"Strip {index + 1}"
            strip = StripWidget(index, name, labels, sends_adjustable=sends,
                                pad_modes=vm.pad_modes(index), physical=vm.is_physical(index),
                                pad_mode=pad_modes[index] if index < len(pad_modes) else None)
            self._wire_strip(strip)
            layout.addWidget(strip)
            self.strips.append(strip)
        layout.addStretch(1)

        self.master = MasterWidget()
        self.master.headphoneChanged.connect(vm.set_headphone_gain)
        self.master.settingsRequested.connect(self.open_settings)
        self.master.projectRequested.connect(self.choose_project)
        self.master.minimizeRequested.connect(self.minimize)
        layout.addWidget(self.master)

        self.toast = Toast(self.background)
        self._tray = self._make_tray()
        self.apply_appearance()
        self._restore_geometry()

        # Moteurs → interface
        vm.params_changed.connect(self._on_params)
        vm.levels_changed.connect(self._on_levels)
        vm.connection_changed.connect(self._on_vm_connection)
        reaper.tracks_changed.connect(self._on_tracks)
        reaper.connection_changed.connect(self._on_reaper_connection)
        reaper.device_changed.connect(self._on_device)
        reaper.project_changed.connect(self._on_project)
        reaper.command_failed.connect(self.toast.show_message)

        self._refresh_status()
        self._on_tracks(self._tracks)
        self._on_project(reaper.project_path)
        if reaper.device is not None:
            self._on_device(reaper.device)

        # Démarrage : état sauvegardé, noms des strips dans Voicemeeter, scrutation
        state = cfg.get("state") or {}
        if state.get("strips"):
            vm.apply_state(state)
        for strip in self.strips:
            vm.set_label(strip.index, strip.strip_name())
        vm.start_polling()

    # ---------------------------------------------------------- câblage
    def _wire_strip(self, strip: StripWidget) -> None:
        vm, reaper = self._vm, self._reaper
        strip.gainChanged.connect(vm.set_gain)
        strip.muteToggled.connect(vm.set_mute)
        strip.routeToggled.connect(vm.set_route)
        strip.sendChanged.connect(vm.set_send)
        strip.padMoved.connect(vm.set_pad)
        strip.padModeChanged.connect(self._on_pad_mode)
        strip.renamed.connect(self._on_renamed)
        strip.chainToggled.connect(reaper.set_chain_enabled)
        strip.fxAddRequested.connect(self.add_effect)
        strip.fxOpenRequested.connect(self._open_effect)
        strip.fxBypassRequested.connect(reaper.set_fx_enabled)
        strip.fxRemoveRequested.connect(self._remove_effect)

    # ------------------------------------------------------- moteurs → UI
    def _on_params(self, params: MixerParams) -> None:
        self._params = params
        for strip, strip_params in zip(self.strips, params.strips):
            strip.set_params(strip_params)
        self.master.set_headphone(params.headphone_gain)

    def _on_levels(self, levels: MixerLevels) -> None:
        for strip, strip_levels in zip(self.strips, levels.strips):
            strip.set_levels(strip_levels)
        self.master.set_levels(levels.master)

    def _on_tracks(self, tracks: list) -> None:
        """tracks : la piste REAPER de chaque strip (None si aucune piste du
        projet n'écoute ses canaux)."""
        self._tracks = list(tracks)
        create = bool(self._cfg.get("reaper", {}).get("manage_routing", True))
        for strip in self.strips:
            track = tracks[strip.index] if strip.index < len(tracks) else None
            strip.set_track(track)
            strip.set_reaper_available(self._reaper_ok, track is not None or create)
        self.update_inserts()

    def _on_vm_connection(self, ok: bool) -> None:
        self._vm_ok = ok
        self._refresh_status()

    def _on_reaper_connection(self, ok: bool) -> None:
        self._reaper_ok = ok
        self._on_tracks(self._tracks)
        self._refresh_status()

    def _on_project(self, path: str) -> None:
        name = Path(path).stem if path else "…"
        if path and is_blank_project(path):
            name = "Projet vierge"
        self.master.set_project(name, path or "(en attente de REAPER)")

    def update_inserts(self) -> None:
        """Insert Voicemeeter activé seulement pour les strips dont la piste
        REAPER traite vraiment le son (armée, écoutée, avec une sortie) et
        quand REAPER tourne sur le pilote de l'insert : sinon la strip serait
        muette. Tout le reste passe sans effets."""
        manage = bool(self._cfg.get("voicemeeter", {}).get("manage_insert", True))
        device = self._reaper.device
        ready = (manage and self._reaper_ok and device is not None
                 and device.is_voicemeeter_insert)
        strips = {i for i, track in enumerate(self._tracks)
                  if ready and track is not None and track.processes_live}
        self._vm.set_inserts(strips)

    def _refresh_status(self) -> None:
        if not self._vm_ok:
            self.master.set_status(False, "Voicemeeter déconnecté",
                                   "Voicemeeter ne répond plus : faders et routing sont figés.")
        elif self._reaper_ok:
            self.master.set_status(True, "REAPER connecté")
        else:
            self.master.set_status(False, "REAPER déconnecté",
                                   "REAPER ne répond plus : les effets ne sont plus modifiables.")

    def _on_device(self, device) -> None:
        manage = bool(self._cfg.get("voicemeeter", {}).get("manage_insert", True))
        is_insert = device.is_voicemeeter_insert
        self.update_inserts()
        if manage and not is_insert and not self._device_warned:
            self._device_warned = True
            self.toast.show_message(
                "Les effets REAPER ne s'appliquent pas encore : choisis « Voicemeeter Insert "
                "Virtual ASIO » comme carte son de REAPER (⚙ Réglages → préférences audio).")

    # ------------------------------------------------------- actions UI
    def _on_pad_mode(self, index: int, mode: str) -> None:
        """Mode d'Intellipan choisi pour une strip : retenu pour la prochaine fois."""
        modes = self._cfg.setdefault("ui", {}).setdefault("pad_modes", [])
        modes.extend([None] * (index + 1 - len(modes)))
        modes[index] = mode

    def _on_renamed(self, index: int, name: str) -> None:
        names = self._cfg.setdefault("ui", {}).setdefault("strip_names", [])
        while len(names) <= index:
            names.append(f"Strip {len(names) + 1}")
        names[index] = name
        self._reaper.rename_track(index, name)
        self._vm.set_label(index, name)
        self._save_config()

    def add_effect(self, index: int) -> None:
        strip = self.strips[index]
        exe = self._processes.executable("reaper") if self._processes is not None else None
        dialog = VstDialog(strip.strip_name(), self._reaper, self._reaper.resource_path(exe),
                           self._cfg.get("vst", {}).get("extra_dirs", []), self)
        if dialog.exec() and dialog.selected is not None:
            log.info("Ajout de %s sur %s", dialog.selected.add_name, strip.strip_name())
            self._reaper.add_fx(index, dialog.selected.add_name)

    def _open_effect(self, index: int, fx_index: int) -> None:
        self._reaper.toggle_fx_ui(index, fx_index)
        name = self.strips[index].fx_name(fx_index)
        if name and self._processes is not None:
            # L'interface du plugin s'ouvre dans REAPER (caché) : on la ramène devant
            QTimer.singleShot(FOCUS_PLUGIN_DELAY_MS, self,
                              lambda: self._processes.focus_window("reaper", name))

    def _remove_effect(self, index: int, fx_index: int) -> None:
        name = self.strips[index].fx_name(fx_index) or "cet effet"
        answer = QMessageBox.question(
            self, "Supprimer l'effet",
            f"Supprimer « {name} » de « {self.strips[index].strip_name()} » ?\n"
            "Ses réglages seront perdus.")
        if answer == QMessageBox.StandardButton.Yes:
            self._reaper.remove_fx(index, fx_index)

    def open_settings(self) -> None:
        dialog = SettingsDialog(self._cfg, self._processes, self._reaper, self._vm, self,
                                persist=self._persist)
        dialog.optionsChanged.connect(lambda: self._on_tracks(self._tracks))
        dialog.projectRequested.connect(self.choose_project)
        dialog.appearanceChanged.connect(self.apply_appearance)
        dialog.windowResetRequested.connect(self.reset_window_size)
        # Redémarrage après la fermeture des réglages (pas depuis leur boucle modale)
        restart = []
        dialog.restartRequested.connect(lambda: restart.append(True))
        dialog.restartRequested.connect(dialog.accept)
        dialog.exec()
        dialog.deleteLater()
        if restart:
            self.restart_app()

    # ------------------------------------------------------------ apparence
    def apply_appearance(self) -> None:
        """Thème de couleur, arrière-plan et transparence des panneaux, d'après la config."""
        ui_cfg = self._cfg.get("ui", {})
        theme = ui_cfg.get("theme", styles.DEFAULT_THEME)
        theme_changed = theme != styles.current_theme()
        if theme_changed:
            styles.set_theme(theme)
        self.background.set_image(background_image_path(self._cfg),
                                  float(ui_cfg.get("background_darkness", DEFAULT_DARKNESS)))
        app = QApplication.instance()
        if app is not None:
            styles.refresh(app, float(ui_cfg.get("panel_opacity", DEFAULT_PANEL_OPACITY)))
        if theme_changed:
            # Widgets dessinés à la main : ils relisent la palette en se redessinant
            self.toast.refresh_style()
            for widget in self.findChildren(QWidget):
                widget.update()
            self.update()

    # -------------------------------------------------------------- fenêtre
    def compact_size(self) -> tuple[int, int]:
        """Taille « comme Voicemeeter » : juste les strips et le master."""
        width = self.background.layout().sizeHint().width()
        return width, COMPACT_HEIGHT

    def _restore_geometry(self) -> None:
        width, _ = self.compact_size()
        self.setMinimumSize(width, MIN_HEIGHT)
        ui_cfg = self._cfg.setdefault("ui", {})
        saved = ui_cfg.get("geometry")
        same_layout = ui_cfg.get("layout_version") == LAYOUT_VERSION
        ui_cfg["layout_version"] = LAYOUT_VERSION
        if saved and same_layout and self.restoreGeometry(QByteArray.fromBase64(saved.encode("ascii"))):
            return
        self.reset_window_size()

    def reset_window_size(self) -> None:
        """Fenêtre compacte, centrée sur l'écran."""
        if self.isVisible():
            self.showNormal()
        width, height = self.compact_size()
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            height = min(height, area.height() - 60)
            self.resize(width, height)
            frame = self.frameGeometry()
            frame.moveCenter(area.center())
            self.move(frame.topLeft())
        else:
            self.resize(width, height)

    def minimize(self) -> None:
        """Réduit REKA : dans la zone de notification si l'option est active
        (le son continue de tourner), sinon dans la barre des tâches."""
        if self._to_tray():
            # Fenêtre cachée : fermer une popup ne doit pas quitter REKA
            QApplication.setQuitOnLastWindowClosed(False)
            if self.isVisible() and not self.isMinimized():
                self._restore_maximized = self.isMaximized()
            self.hide()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self._tray.showMessage(config.APP_NAME, "REKA continue de tourner ici. "
                                       "Clique sur l'icône pour la rouvrir.",
                                       self.windowIcon(), 3000)
        else:
            self.showMinimized()

    def show_from_tray(self) -> None:
        if self._restore_maximized:
            self.showMaximized()
        else:
            self.showNormal()
        self.raise_()
        self.activateWindow()
        QApplication.setQuitOnLastWindowClosed(True)

    def quit_app(self) -> None:
        """« Quitter REKA » du menu de la zone de notification : la fenêtre peut
        être cachée, on quitte donc explicitement après l'avoir fermée."""
        if self.close():
            QApplication.quit()

    def _to_tray(self) -> bool:
        return (self._tray is not None
                and bool(self._cfg.get("ui", {}).get("minimize_to_tray", False)))

    def _make_tray(self) -> QSystemTrayIcon | None:
        """Icône dans la zone de notification (à côté de l'horloge)."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        app = QApplication.instance()
        tray = QSystemTrayIcon(app.windowIcon() if app else self.windowIcon(), self)
        tray.setToolTip(config.APP_NAME)
        menu = QMenu(self)
        show_action = QAction("Afficher REKA", menu)
        show_action.triggered.connect(self.show_from_tray)
        menu.addAction(show_action)
        menu.addSeparator()
        quit_action = QAction("Quitter REKA", menu)
        quit_action.triggered.connect(self.quit_app)
        menu.addAction(quit_action)
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        return tray

    def _on_tray_activated(self, reason) -> None:
        # Sous Windows, un double-clic envoie Trigger puis DoubleClick : le
        # double-clic ne fait donc qu'afficher (sinon il annulerait le clic).
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_from_tray()
        elif reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.isVisible() and not self.isMinimized():
                self.minimize()
            else:
                self.show_from_tray()

    def changeEvent(self, event) -> None:  # noqa: N802
        # Réduction par la barre de titre : direction la zone de notification si demandé
        if (event.type() == QEvent.Type.WindowStateChange and self.isMinimized()
                and self._to_tray()):
            QTimer.singleShot(0, self.minimize)
        super().changeEvent(event)

    def restart_app(self) -> None:
        """Relance REKA (changement d'édition de Voicemeeter) : la nouvelle
        instance attend que celle-ci ait arrêté proprement les moteurs."""
        if not self._persist:
            self.toast.show_message("Mode démo : pas de redémarrage.")
            return
        self._save_config()
        if not launch_restart():
            QMessageBox.warning(self, config.APP_NAME,
                                "Impossible de relancer REKA automatiquement : relance-la à la main.")
            return
        self.close()

    def choose_project(self) -> None:
        """Choix du projet REAPER ; il est ouvert tout de suite, sans redémarrer."""
        session_cfg = running_config(self._cfg, self._vm)
        dialog = ProjectDialog(session_cfg, current=self._reaper.project_path, parent=self)
        if not dialog.exec():
            return
        self._save_config()
        path = reaper_project(session_cfg, dialog.selected)
        if path is None:
            self.toast.show_message("Projet introuvable.")
            return
        current = self._reaper.project_path
        if current and Path(current).resolve() == path.resolve():
            return
        self._reaper.open_project(path)
        self.toast.show_message(f"Ouverture de « {path.stem} » dans REAPER…")

    # ------------------------------------------------------------ fermeture
    def closeEvent(self, event) -> None:  # noqa: N802
        if self._params is not None:
            state = self._params.to_state()
            if not getattr(self._vm, "supports_sends", True):
                # Banana n'a pas de niveaux d'envoi : on garde ceux réglés sur Potato
                previous = (self._cfg.get("state") or {}).get("strips", [])
                for strip, old in zip(state["strips"], previous):
                    if isinstance(old, dict) and "sends" in old:
                        strip["sends"] = old["sends"]
            self._cfg["state"] = state
        ui_cfg = self._cfg.setdefault("ui", {})
        ui_cfg["geometry"] = bytes(self.saveGeometry().toBase64()).decode("ascii")
        for legacy in ("width", "height"):   # remplacés par « geometry »
            ui_cfg.pop(legacy, None)
        self._save_config()
        if self._tray is not None:
            self._tray.hide()
        super().closeEvent(event)

    def _save_config(self) -> None:
        if not self._persist:
            return
        try:
            config.save_config(self._cfg)
        except OSError as exc:
            log.error("Config non sauvegardée : %s", exc)
