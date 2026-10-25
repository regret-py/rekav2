"""
Fenêtre principale : strips alignés à gauche, panneau master à droite, et le
câblage entre l'interface et les moteurs (Voicemeeter, REAPER, processus).

Fenêtre « comme Voicemeeter » : taille compacte par défaut (strips + master,
sans grand vide), position et taille mémorisées, réduction possible dans la
zone de notification (icône à côté de l'horloge).

Aussi : volume par application (bouton « Apps » des strips virtuelles) et
mises à jour (vérification au démarrage puis régulière, pill dans le Master).
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
from shiboken6 import delete as delete_now

from core import config
from core.app_volume import AppVolumeBackend, NullAppVolumes
from core.effects import HOST_APO, HOST_NONE, HOST_REAPER
from core.media import MediaBackend, NullMedia
from core.process_manager import is_blank_project, reaper_project
from core.updater import UpdateChecker, UpdateInfo
from core.voicemeeter_ctrl import DEFAULT_ROUTING, MixerLevels, MixerParams
from ui import styles
from ui.app_mixer import AppMixerPopup
from ui.master_widget import MasterWidget
from ui.project_dialog import ProjectDialog
from ui.settings_dialog import SettingsDialog
from ui.strip_widget import StripWidget
from ui.update_dialog import UpdateDialog
from ui.vst_dialog import VstDialog

log = logging.getLogger(__name__)

STRIP_GAP = 12
FOCUS_PLUGIN_DELAY_MS = 400
TOAST_MS = 4500
COMPACT_HEIGHT = 860          # hauteur de la fenêtre compacte (comme Voicemeeter)
MIN_HEIGHT = 680
TITLE_BAR_ALLOWANCE = 40      # barre de titre Windows (le cadre n'est connu qu'une fois affiché)
# Augmenté quand la disposition des strips change (0.4 : Intellipan) : la
# taille mémorisée, prévue pour l'ancienne disposition, est alors ignorée.
LAYOUT_VERSION = 2
DEFAULT_DARKNESS = 0.45       # assombrissement de l'image d'arrière-plan
DEFAULT_PANEL_OPACITY = 1.0
APP_COUNT_MS = 2000           # nombre d'applications affiché sur les boutons « Apps »
ENGINE_STATE_MS = 2000        # état des boutons Voicemeeter / REAPER (fenêtre affichée ?)
ENGINE_NAMES = {"voicemeeter": "Voicemeeter", "reaper": "REAPER"}
MEDIA_RESYNC_MS = 1500        # lecture / pause : état relu si l'application n'a pas réagi
UPDATE_FIRST_CHECK_MS = 8000  # première vérification des mises à jour après l'ouverture
DEFAULT_UPDATE_HOURS = 6


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
    result = QProcess.startDetached(program, args)
    # PySide6 renvoie (succès, pid) ; d'autres versions un simple booléen
    started = result[0] if isinstance(result, tuple) else bool(result)
    if not started:
        log.error("Impossible de relancer REKA : %s %s", program, " ".join(args))
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
                 apps: AppVolumeBackend | None = None, updater: UpdateChecker | None = None,
                 media: MediaBackend | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cfg = cfg
        self._persist = persist          # False en mode démo : la config n'est pas touchée
        self._vm = vm
        self._reaper = reaper
        self._processes = processes
        self._apps = apps or NullAppVolumes()
        self._media = media or NullMedia()
        self._updater = updater
        self._update: UpdateInfo | None = None
        self._app_mixer: AppMixerPopup | None = None
        self._params: MixerParams | None = None
        self._vm_ok = True
        self._reaper_ok = reaper.is_connected
        self._device_warned = False
        self._tray_hint_shown = False
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
            virtual = vm.virtual_input(index)
            strip = StripWidget(index, name, labels, sends_adjustable=sends,
                                pad_modes=vm.pad_modes(index), physical=vm.is_physical(index),
                                pad_mode=pad_modes[index] if index < len(pad_modes) else None,
                                apps_button=self._apps.available and virtual is not None)
            self._wire_strip(strip)
            layout.addWidget(strip)
            self.strips.append(strip)
        layout.addStretch(1)

        self.master = MasterWidget()
        self.master.headphoneChanged.connect(vm.set_headphone_gain)
        self.master.settingsRequested.connect(self.open_settings)
        self.master.projectRequested.connect(self._on_project_button)
        self.master.minimizeRequested.connect(self.minimize)
        self.master.updateRequested.connect(self.open_update)
        self.master.engineRequested.connect(self.toggle_engine_window)
        # Musique en cours (contrôles média de Windows)
        self.master.media.previousRequested.connect(self._media.previous)
        self.master.media.nextRequested.connect(self._media.next)
        self.master.media.playPauseRequested.connect(self._on_media_play)
        self._media.changed.connect(self.master.media.set_info)
        # REAPER seulement s'il applique les effets (FL Studio / APO : bouton Projet)
        self.master.set_engines([(key, ENGINE_NAMES[key]) for key in ("voicemeeter", "reaper")
                                 if key == "voicemeeter" or reaper.kind == HOST_REAPER])
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
        for strip in self.strips:
            strip.configure_effects(reaper.add_label, reaper.strip_hint(strip.index),
                                    reaper.manages_fx, *self._chain_tooltips())

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

        # Volume par application : nombre d'applications sur chaque bouton « Apps »
        self._app_timer = QTimer(self)
        self._app_timer.setInterval(APP_COUNT_MS)
        self._app_timer.timeout.connect(self.refresh_app_counts)
        if any(strip.apps is not None for strip in self.strips):
            self._app_timer.start()
            QTimer.singleShot(0, self.refresh_app_counts)

        # Fenêtres de Voicemeeter / REAPER affichées (bouton du Master, réglages)
        self._engine_timer = QTimer(self)
        self._engine_timer.setInterval(ENGINE_STATE_MS)
        self._engine_timer.timeout.connect(self.refresh_engine_buttons)
        self._engine_timer.start()
        self.refresh_engine_buttons()

        # Mises à jour : au démarrage (option), puis régulièrement
        self._update_timer = QTimer(self)
        if updater is not None:
            updater.available.connect(self._on_update_available)
            hours = float(cfg.get("update", {}).get("interval_hours", DEFAULT_UPDATE_HOURS) or 0)
            if cfg.get("update", {}).get("check_on_start", True) and updater.enabled:
                QTimer.singleShot(UPDATE_FIRST_CHECK_MS, updater.check)
                if hours > 0:
                    self._update_timer.setInterval(int(hours * 3600 * 1000))
                    self._update_timer.timeout.connect(updater.check)
                    self._update_timer.start()
            if updater.latest is not None:
                self._on_update_available(updater.latest)

    # ---------------------------------------------------------- câblage
    def _wire_strip(self, strip: StripWidget) -> None:
        vm, reaper = self._vm, self._reaper
        strip.gainChanged.connect(vm.set_gain)
        strip.muteToggled.connect(vm.set_mute)
        strip.routeToggled.connect(vm.set_route)
        strip.sendChanged.connect(vm.set_send)
        strip.padMoved.connect(vm.set_pad)
        strip.padModeChanged.connect(self._on_pad_mode)
        strip.appsRequested.connect(self.open_app_mixer)
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

    def _chain_tooltips(self) -> tuple[str, str]:
        """Info-bulles du badge FX (chaîne active / en bypass) selon le moteur."""
        if self._reaper.kind == "flstudio":
            return ("Son envoyé à FL Studio (insert Voicemeeter) — clic pour le faire passer "
                    "sans effets",
                    "Son sans effets — clic pour l'envoyer à FL Studio (piste du mixer câblée "
                    "sur l'insert de la strip)")
        return ("Effets actifs — clic pour tout bypasser", "Effets en bypass — clic pour les réactiver")

    def _on_tracks(self, tracks: list) -> None:
        """tracks : la piste (REAPER) ou la chaîne d'effets (autres moteurs) de
        chaque strip ; None si aucune piste du projet n'écoute ses canaux."""
        self._tracks = list(tracks)
        host = self._reaper
        create = (host.kind != HOST_REAPER
                  or bool(self._cfg.get("reaper", {}).get("manage_routing", True)))
        for strip in self.strips:
            track = tracks[strip.index] if strip.index < len(tracks) else None
            strip.set_track(track)
            strip.set_fx_hint(host.strip_hint(strip.index))
            strip.set_reaper_available(self._reaper_ok and host.kind != HOST_NONE,
                                       track is not None or create)
            strip.set_latency(host.strip_latency_ms(strip.index),
                              [host.fx_latency_ms(strip.index, i)
                               for i in range(len(track.fx) if track is not None else 0)])
        self.update_inserts()

    def _on_vm_connection(self, ok: bool) -> None:
        self._vm_ok = ok
        self._refresh_status()

    def _on_reaper_connection(self, ok: bool) -> None:
        self._reaper_ok = ok
        self._on_tracks(self._tracks)
        self._refresh_status()

    def _on_project(self, path: str) -> None:
        host = self._reaper
        if not host.has_projects:
            # Pas de projet REKA : le bouton ouvre l'application du moteur
            names = {"flstudio": ("FL Studio", "Clic : afficher FL Studio pour régler ses effets"),
                     HOST_APO: ("Equalizer APO", "Clic : ouvrir l'éditeur d'Equalizer APO")}
            name, tip = names.get(host.kind, ("Voicemeeter seul", "Aucun moteur d'effets"))
            self.master.set_project(name, "", tip)
            self.master.project.setVisible(host.kind != HOST_NONE)
            return
        name = Path(path).stem if path else "…"
        if path and is_blank_project(path):
            name = "Projet vierge"
        self.master.set_project(name, path or "(en attente de REAPER)")

    def _on_project_button(self) -> None:
        if self._reaper.has_projects:
            self.choose_project()
        else:
            self._reaper.show_app()

    def update_inserts(self) -> None:
        """Insert Voicemeeter activé seulement pour les strips dont le moteur
        d'effets traite vraiment le son (REAPER : piste armée, écoutée, avec une
        sortie, et carte son sur le pilote de l'insert ; FL Studio : badge FX
        allumé) : sinon la strip serait muette. Tout le reste passe sans effets."""
        manage = bool(self._cfg.get("voicemeeter", {}).get("manage_insert", True))
        strips = self._reaper.insert_strips(self._tracks, self._reaper_ok) if manage else set()
        self._vm.set_inserts(strips)

    def _refresh_status(self) -> None:
        if not self._vm_ok:
            self.master.set_status(False, "Voicemeeter déconnecté",
                                   "Voicemeeter ne répond plus : faders et routing sont figés.")
        else:
            self.master.set_status(*self._reaper.status(self._reaper_ok))

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

    # ------------------------------------------------- volume par application
    def refresh_app_counts(self) -> None:
        if not self.isVisible() or self.isMinimized():
            return
        for strip in self.strips:
            if strip.apps is not None:
                index = self._vm.virtual_input(strip.index)
                strip.apps.set_count(len(self._apps.apps(index)) if index is not None else 0)

    def open_app_mixer(self, index: int) -> None:
        strip = self.strips[index]
        virtual = self._vm.virtual_input(index)
        if virtual is None or strip.apps is None:
            return
        if self._app_mixer is not None:
            self._app_mixer.close()
        self._app_mixer = AppMixerPopup(self._apps, virtual, strip.strip_name(), self)
        self._app_mixer.destroyed.connect(self._on_app_mixer_closed)
        self._app_mixer.show_under(strip.apps)

    def _on_app_mixer_closed(self) -> None:
        self._app_mixer = None
        QTimer.singleShot(0, self.refresh_app_counts)

    # ------------------------------------------------------ musique en cours
    def _on_media_play(self) -> None:
        self._media.play_pause()
        # Le bouton a basculé tout de suite ; si l'application n'a pas suivi, on le remet
        QTimer.singleShot(MEDIA_RESYNC_MS, self, lambda: self.master.media.set_info(self._media.current))

    def _update_media(self) -> None:
        """Lecteur du Master : affiché si les contrôles média répondent et si
        l'option est active (réglages → Apparence)."""
        show = self._media.available and bool(self._cfg.get("ui", {}).get("show_media", True))
        if show:
            self._media.start()
            self.master.media.set_info(self._media.current)
        else:
            self._media.stop()
        self.master.media.setVisible(show)

    # ------------------------------------------- fenêtres de Voicemeeter / REAPER
    def _engine_visible(self, key: str) -> bool:
        query = getattr(self._processes, "is_window_visible", None)
        return bool(query(key)) if query is not None else False

    def refresh_engine_buttons(self) -> None:
        for key in self.master.engines:
            self.master.set_engine_shown(key, self._engine_visible(key))

    def toggle_engine_window(self, key: str) -> None:
        """Bouton du Master : affiche la fenêtre du moteur (au premier plan), ou
        la recache si elle est déjà affichée."""
        name = ENGINE_NAMES.get(key, key)
        if self._processes is None:
            return
        if self._engine_visible(key):
            self._processes.set_window_visible(key, False)
        elif not self._processes.bring_to_front(key):
            self.toast.show_message(f"{name} ne tourne pas : relance REKA pour le démarrer.")
        self.refresh_engine_buttons()

    # ------------------------------------------------------- mises à jour
    def _on_update_available(self, update: UpdateInfo) -> None:
        skipped = int(self._cfg.get("update", {}).get("skipped_build", 0) or 0)
        if update.build == skipped and self._update is None:
            return
        first = self._update is None or self._update.build != update.build
        self._update = update
        self.master.set_update(update.version)
        if first:
            self.toast.show_message(f"REKA {update.version} est disponible : "
                                    "clique sur « Mise à jour » dans le panneau Master.")
            if self._tray is not None and not self.isVisible():
                self._tray.showMessage(config.APP_NAME, f"REKA {update.version} est disponible.",
                                       self.windowIcon(), 5000)

    def open_update(self) -> None:
        # Mise à jour signalée, ou trouvée par une recherche manuelle (même ignorée)
        update = self._update or (self._updater.latest if self._updater is not None else None)
        if update is None:
            return
        dialog = UpdateDialog(update, self._updater.build if self._updater else None, self)
        dialog.exec()
        skipped, installed = dialog.skipped, dialog.installed
        delete_now(dialog)        # tout de suite : pas de suppression différée en suspens
        if skipped:
            self._cfg.setdefault("update", {})["skipped_build"] = update.build
            self._save_config()
            self._update = None
            self.master.set_update(None)
        elif installed:
            self.restart_app()

    def rename_strip(self, index: int, name: str) -> None:
        """Renomme un canal (depuis les réglages) : strip, REAPER, Voicemeeter, config."""
        name = name.strip()[:30]
        if not name or not 0 <= index < len(self.strips) or self.strips[index].name.text() == name:
            return
        self.strips[index].name.setText(name)
        self._on_renamed(index, name)

    def _on_renamed(self, index: int, name: str) -> None:
        names = self._cfg.setdefault("ui", {}).setdefault("strip_names", [])
        while len(names) <= index:
            names.append(f"Strip {len(names) + 1}")
        names[index] = name
        self._reaper.rename_track(index, name)
        self._vm.set_label(index, name)
        self._save_config()

    def add_effect(self, index: int) -> None:
        strip, host = self.strips[index], self._reaper
        if not host.manages_fx:
            host.add_fx(index, "")              # FL Studio : sa fenêtre s'ouvre
            return
        extra_dirs = self._cfg.get("vst", {}).get("extra_dirs", [])
        if host.kind == HOST_APO:
            if not host.strip_device(index) and not self.choose_apo_device(index):
                return
            from core.apo_ctrl import default_vst2_dirs, scan_vst2
            dialog = VstDialog(strip.strip_name(), None, None, extra_dirs, self,
                               plugins=scan_vst2(default_vst2_dirs(extra_dirs)),
                               browse_filter="Plugin VST2 64 bits (*.dll)",
                               note="Equalizer APO charge les plugins VST2 64 bits (.dll).")
        else:
            exe = self._processes.executable("reaper") if self._processes is not None else None
            dialog = VstDialog(strip.strip_name(), host, host.resource_path(exe), extra_dirs, self)
        selected = dialog.selected if dialog.exec() else None
        delete_now(dialog)        # sinon chaque ouverture garderait un dialogue (et sa liste) en mémoire
        if selected is not None:
            log.info("Ajout de %s sur %s", selected.add_name, strip.strip_name())
            host.add_fx(index, selected.add_name)

    def choose_apo_device(self, index: int) -> bool:
        """Périphérique Windows d'une strip pour Equalizer APO (micro…)."""
        from PySide6.QtWidgets import QInputDialog

        host = self._reaper
        names = [e.name for e in host.endpoints() if e.capture] + \
                [e.name for e in host.endpoints() if not e.capture]
        current = host.strip_device(index)
        if current and current not in names:
            names.insert(0, current)
        name, ok = QInputDialog.getItem(
            self, "Périphérique Windows",
            f"Périphérique Windows de « {self.strips[index].strip_name()} »\n"
            "(celui où Equalizer APO est installé, par exemple ton micro) :",
            names, names.index(current) if current in names else 0, True)
        if not ok or not name.strip():
            return False
        host.set_device(index, name.strip())
        return True

    def _open_effect(self, index: int, fx_index: int) -> None:
        self._reaper.toggle_fx_ui(index, fx_index)
        name = self.strips[index].fx_name(fx_index)
        if self._reaper.kind == HOST_REAPER and name and self._processes is not None:
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
                                persist=self._persist, updater=self._updater)
        dialog.optionsChanged.connect(lambda: self._on_tracks(self._tracks))
        dialog.projectRequested.connect(self.choose_project)
        dialog.appearanceChanged.connect(self.apply_appearance)
        dialog.windowResetRequested.connect(self.reset_window_size)
        dialog.stripRenamed.connect(self.rename_strip)
        dialog.latencyChanged.connect(lambda: self._on_tracks(self._tracks))
        # Redémarrage après la fermeture des réglages (pas depuis leur boucle modale)
        restart = []
        dialog.restartRequested.connect(lambda: restart.append(True))
        dialog.restartRequested.connect(dialog.accept)
        # « Installer la mise à jour » (onglet À propos) : après la fermeture des réglages
        update = []
        dialog.updateRequested.connect(lambda: update.append(True))
        dialog.updateRequested.connect(dialog.accept)
        dialog.exec()
        delete_now(dialog)        # tout de suite : pas de suppression différée en suspens
        self.refresh_engine_buttons()   # « Afficher la fenêtre… » des réglages
        if restart:
            self.restart_app()
        elif update:
            self.open_update()

    # ------------------------------------------------------------ apparence
    def apply_appearance(self) -> None:
        """Thème de couleur, arrière-plan et transparence des panneaux, d'après la config."""
        ui_cfg = self._cfg.get("ui", {})
        theme = ui_cfg.get("theme", styles.DEFAULT_THEME)
        theme_changed = theme != styles.current_theme()
        if theme_changed:
            styles.set_theme(theme)
        if styles.configure(ui_cfg):          # police, style des faders / VU
            theme_changed = True
        self.background.set_image(background_image_path(self._cfg),
                                  float(ui_cfg.get("background_darkness", DEFAULT_DARKNESS)))
        app = QApplication.instance()
        if app is not None:
            styles.refresh(app, float(ui_cfg.get("panel_opacity", DEFAULT_PANEL_OPACITY)))
        for strip in self.strips:             # couleurs par élément (feuilles de style)
            strip.apply_colors()
        self.master.apply_colors()
        self._update_media()
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
        # Hauteur minimale : celle prévue, sauf sur un petit écran (1280×720 logiques
        # à 150 %) où elle doit tenir au-dessus de la barre des tâches
        layout_min = self.background.layout().minimumSize().height()
        area = self._screen_area()
        fit = area.height() - TITLE_BAR_ALLOWANCE if area is not None else MIN_HEIGHT
        self.setMinimumSize(width, max(layout_min, min(MIN_HEIGHT, fit)))
        ui_cfg = self._cfg.setdefault("ui", {})
        saved = ui_cfg.get("geometry")
        if ui_cfg.get("layout_version") != LAYOUT_VERSION:
            saved = None          # taille prévue pour l'ancienne disposition des strips
        if saved and self.restoreGeometry(QByteArray.fromBase64(saved.encode("ascii"))):
            return
        self.reset_window_size()

    def _screen_area(self):
        screen = self.screen() or QApplication.primaryScreen()
        return screen.availableGeometry() if screen is not None else None

    def reset_window_size(self) -> None:
        """Fenêtre compacte, centrée dans l'espace libre de l'écran (barre des
        tâches exclue), barre de titre comprise."""
        if self.isVisible():
            self.showNormal()
        width, height = self.compact_size()
        area = self._screen_area()
        if area is None:
            self.resize(width, height)
            return
        height = max(self.minimumHeight(), min(height, area.height() - TITLE_BAR_ALLOWANCE))
        self.resize(width, height)
        left = area.x() + max(0, (area.width() - self.frameGeometry().width()) // 2)
        top = area.y() + max(0, (area.height() - height - TITLE_BAR_ALLOWANCE) // 2)
        self.move(left, top)

    def minimize(self) -> None:
        """Réduit REKA : dans la zone de notification si l'option est active
        (le son continue de tourner), sinon dans la barre des tâches."""
        if self._to_tray():
            # Fenêtre cachée : fermer une popup ne doit pas quitter REKA
            QApplication.setQuitOnLastWindowClosed(False)
            self.hide()           # l'état « agrandie » est conservé par Qt
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self._tray.showMessage(config.APP_NAME, "REKA continue de tourner ici. "
                                       "Clique sur l'icône pour la rouvrir.",
                                       self.windowIcon(), 3000)
        else:
            self.showMinimized()

    def show_from_tray(self) -> None:
        """Ré-affiche la fenêtre (zone de notification, barre des tâches, second
        lancement) dans son état d'avant : agrandie si elle l'était."""
        if not self.isVisible() or self.isMinimized():
            # Qt garde WindowMaximized pendant la réduction : on n'enlève que « réduite »
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
            self.show()
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
        accepted, selected = bool(dialog.exec()), dialog.selected
        delete_now(dialog)
        if not accepted:
            return
        self._save_config()
        path = reaper_project(session_cfg, selected)
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
        ui_cfg["layout_version"] = LAYOUT_VERSION    # la taille enregistrée va avec cette disposition
        for legacy in ("width", "height"):   # remplacés par « geometry »
            ui_cfg.pop(legacy, None)
        self._save_config()
        self._media.stop()
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
