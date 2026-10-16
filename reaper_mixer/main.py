"""
REAPER Mixer — point d'entrée.

Séquence de démarrage :
  1. Lecture de config.json (défauts + copie utilisateur dans %APPDATA%)
  2. Lancement + masquage de Voicemeeter Potato
  3. Installation du pont Lua, puis lancement + masquage de REAPER
  4. Login à l'API Remote de Voicemeeter (nouvel essai toutes les 5 s)
  5. Attente du pont REAPER (battement de cœur)
  6. Affichage de la fenêtre principale

Les étapes 2 à 5 sont bloquantes : elles tournent dans un QThread pendant
qu'un écran de chargement reste affiché et réactif.

À la fermeture : sauvegarde de la config, arrêt propre des contrôleurs,
puis arrêt des processus lancés par l'app.

Options :
  --demo            interface avec des moteurs simulés (sans Voicemeeter ni REAPER)
  --selftest IMAGE  rend la console en démo dans IMAGE puis quitte (vérif de build)
"""

from __future__ import annotations

import argparse
import logging
import os
import platform
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QLockFile, QRectF, QThread, Qt, QtMsgType, QUrl, Signal, qInstallMessageHandler
from PySide6.QtGui import QColor, QDesktopServices, QFontDatabase, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QSplashScreen

from core import config
from core.process_manager import (AppNotInstalledError, ProcessManager, ProcessManagerError,
                                  StartupAborted)
from core.reaper_ctrl import ReaperController
from core.voicemeeter_ctrl import VoicemeeterController
from ui import styles
from ui.main_window import MainWindow

log = logging.getLogger("reaper_mixer")

LOG_FORMAT = "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s : %(message)s"
LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_BACKUPS = 3
BRIDGE_PING_TIMEOUT_S = 1.0  # attente d'un battement de cœur du pont
BRIDGE_RETRY_S = 0.5         # pause entre deux attentes
STARTUP_CANCEL_WAIT_MS = 5000
WINDOWS_APP_ID = "ReaperMixer.Console"


# --------------------------------------------------------------------------- #
# Journalisation et erreurs non gérées
# --------------------------------------------------------------------------- #
def setup_logging() -> Path:
    """Logs dans %APPDATA%/REAPER Mixer/app.log (rotation 3 × 2 Mo)."""
    log_path = config.app_data_dir() / "app.log"
    formatter = logging.Formatter(LOG_FORMAT)
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    file_handler = RotatingFileHandler(
        log_path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # En développement on double dans la console. Dans l'exe --windowed,
    # sys.stderr vaut None : pas de console.
    if sys.stderr is not None:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root.addHandler(console)
    return log_path


def apply_log_level(name: str) -> None:
    level = logging.getLevelName(str(name).upper())
    if not isinstance(level, int):
        log.warning("log_level « %s » invalide, INFO conservé.", name)
        return
    logging.getLogger().setLevel(level)


def install_exception_hooks() -> None:
    """Toute exception non rattrapée (thread principal ou secondaire) finit
    dans app.log au lieu de disparaître silencieusement."""

    def main_hook(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        log.critical("Exception non gérée", exc_info=(exc_type, exc, tb))

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        name = args.thread.name if args.thread else "?"
        log.critical("Exception non gérée dans le thread %s", name,
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = main_hook
    threading.excepthook = thread_hook


def qt_message_handler(mode: QtMsgType, _context, message: str) -> None:
    """Redirige les messages internes de Qt vers le logging Python."""
    level = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }.get(mode, logging.INFO)
    logging.getLogger("qt").log(level, message)


def set_windows_app_id() -> None:
    """Identifiant d'application Windows : la barre des tâches affiche notre
    icône (et pas celle de python.exe en développement)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_ID)
    except (AttributeError, OSError) as exc:
        log.debug("AppUserModelID non défini : %s", exc)


# --------------------------------------------------------------------------- #
# Écran de chargement
# --------------------------------------------------------------------------- #
class SplashScreen(QSplashScreen):
    """Affiché pendant le lancement des moteurs audio."""

    WIDTH, HEIGHT = 460, 220

    def __init__(self) -> None:
        super().__init__(self._render())
        font = QApplication.font()
        font.setPixelSize(12)
        self.setFont(font)

    def set_status(self, text: str) -> None:
        self.showMessage(text, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
                         QColor(styles.GREEN_LABEL))

    def mousePressEvent(self, event) -> None:  # noqa: N802 (API Qt)
        # Un clic ferme un QSplashScreen par défaut : on l'ignore, sinon
        # plus rien ne serait visible pendant le démarrage.
        event.accept()

    @classmethod
    def _render(cls) -> QPixmap:
        screen = QApplication.primaryScreen()
        ratio = screen.devicePixelRatio() if screen else 1.0
        pixmap = QPixmap(round(cls.WIDTH * ratio), round(cls.HEIGHT * ratio))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(QColor(styles.BG_BLACK))

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor(styles.GREEN_DIM))
        painter.drawRoundedRect(QRectF(0.5, 0.5, cls.WIDTH - 1, cls.HEIGHT - 1), 6, 6)

        title_font = QApplication.font()
        title_font.setPixelSize(28)
        painter.setFont(title_font)
        painter.setPen(QColor(styles.GREEN_NEON))
        painter.drawText(QRectF(0, 60, cls.WIDTH, 40), Qt.AlignmentFlag.AlignCenter,
                         config.APP_NAME)

        sub_font = QApplication.font()
        sub_font.setPixelSize(11)
        painter.setFont(sub_font)
        painter.setPen(QColor(styles.GREEN_LABEL))
        painter.drawText(QRectF(0, 100, cls.WIDTH, 20), Qt.AlignmentFlag.AlignCenter,
                         f"v{config.APP_VERSION}")
        painter.end()
        return pixmap


# --------------------------------------------------------------------------- #
# Démarrage des moteurs (thread de travail)
# --------------------------------------------------------------------------- #
class StartupTimeout(Exception):
    def __init__(self, what: str, timeout_s: float, hint: str = "") -> None:
        super().__init__(f"Pas de réponse de {what} après {timeout_s:.0f} s.")
        self.hint = hint


class StartupWorker(QThread):
    """Lance Voicemeeter et REAPER puis attend que les deux répondent.

    Les contrôleurs sont créés dans le thread principal (affinité Qt) ; ce
    thread n'appelle que leurs méthodes bloquantes de connexion.
    """

    progress = Signal(str)
    failed = Signal(object)  # l'exception
    succeeded = Signal()

    def __init__(self, cfg: dict, processes: ProcessManager,
                 vm: VoicemeeterController, reaper: ReaperController) -> None:
        super().__init__()
        self._cfg = cfg
        self._processes = processes
        self._vm = vm
        self._reaper = reaper
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()
        self._processes.abort_startup()

    def run(self) -> None:
        threading.current_thread().name = "startup"
        try:
            self._boot()
        except StartupAborted:
            log.info("Démarrage interrompu.")
        except (ProcessManagerError, StartupTimeout) as exc:
            log.error("Échec du démarrage : %s", exc)  # cas prévus, message explicite
            self.failed.emit(exc)
        except Exception as exc:  # imprévu : trace complète dans le journal
            log.exception("Échec du démarrage")
            self.failed.emit(exc)
        else:
            log.info("Moteurs prêts.")
            self.succeeded.emit()

    def _boot(self) -> None:
        vm_cfg, reaper_cfg = self._cfg["voicemeeter"], self._cfg["reaper"]

        self.progress.emit("Lancement de Voicemeeter…")
        self._processes.start_voicemeeter()

        self.progress.emit("Lancement de REAPER…")
        self._processes.start_reaper(prepare=self._reaper.prepare_launch)

        self.progress.emit("Connexion à Voicemeeter…")
        self._retry("l'API Remote de Voicemeeter", self._vm.login,
                    timeout_s=float(vm_cfg.get("login_timeout_s", 30)),
                    interval_s=float(vm_cfg.get("login_retry_s", 5)))

        self.progress.emit("Connexion à REAPER…")
        self._reaper.start()
        self._retry("REAPER", self._ping_reaper,
                    timeout_s=float(reaper_cfg.get("bridge_timeout_s", 60)),
                    interval_s=BRIDGE_RETRY_S,
                    hint=("Au premier lancement, REAPER peut mettre une minute à analyser "
                          "tes plugins : patiente puis clique sur « Réessayer ». "
                          "Vérifie aussi qu'aucune fenêtre de REAPER (licence, message…) "
                          "n'attend une réponse."))

    def _ping_reaper(self) -> None:
        if not self._reaper.ping(timeout_s=BRIDGE_PING_TIMEOUT_S):
            raise TimeoutError("le pont REAPER Mixer ne répond pas encore")

    def _retry(self, what: str, action: Callable[[], object], *,
               timeout_s: float, interval_s: float, hint: str = "") -> None:
        """Répète `action` jusqu'à ce qu'elle réussisse ou que le délai expire."""
        deadline = time.monotonic() + timeout_s
        attempt = 0
        while True:
            attempt += 1
            try:
                action()
                return
            except StartupAborted:
                raise
            except Exception as exc:
                if time.monotonic() + interval_s > deadline:
                    raise StartupTimeout(what, timeout_s, hint) from exc
                log.info("En attente de %s (essai %d) : %s", what, attempt, exc)
            if self._cancel.wait(interval_s):
                raise StartupAborted()


# --------------------------------------------------------------------------- #
# Application
# --------------------------------------------------------------------------- #
class MixerApp:
    """Orchestre démarrage, fenêtre principale et arrêt."""

    def __init__(self, qt_app: QApplication, cfg: dict, log_path: Path) -> None:
        self._qt = qt_app
        self._cfg = cfg
        self._log_path = log_path
        self._processes = ProcessManager(cfg)
        self._vm = VoicemeeterController(cfg)
        self._reaper = ReaperController(cfg)
        self._splash = SplashScreen()
        self._worker: StartupWorker | None = None
        self._window: MainWindow | None = None
        self._stopped = False

    def start(self) -> None:
        self._splash.show()
        self._boot()

    # --------------------------------------------------------------- démarrage
    def _boot(self) -> None:
        self._splash.set_status("Démarrage…")
        self._worker = StartupWorker(self._cfg, self._processes, self._vm, self._reaper)
        self._worker.progress.connect(self._splash.set_status)
        self._worker.succeeded.connect(self._on_ready)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_ready(self) -> None:
        try:
            config.save_config(self._cfg)  # chemins auto-détectés mémorisés
            self._window = MainWindow(cfg=self._cfg, vm=self._vm,
                                      reaper=self._reaper, processes=self._processes)
        except Exception as exc:
            # Sans fenêtre, l'app tournerait à vide : on prévient et on quitte
            log.exception("Impossible de créer la fenêtre principale")
            self._splash.hide()
            QMessageBox.critical(None, config.APP_NAME,
                                 f"Impossible d'afficher la console :\n{exc}\n\n"
                                 f"Détails dans le journal :\n{self._log_path}")
            self._qt.quit()
            return
        self._window.show()
        self._splash.finish(self._window)
        # À partir d'ici, fermer la fenêtre principale quitte l'application
        self._qt.setQuitOnLastWindowClosed(True)

    def _on_failed(self, exc: Exception) -> None:
        self._splash.hide()
        if isinstance(exc, AppNotInstalledError):
            retry = self._ask_missing_app(exc)
        else:
            retry = self._ask_retry(exc)
        if retry:
            self._splash.show()
            self._boot()
        else:
            self._qt.quit()

    def _ask_missing_app(self, exc: AppNotInstalledError) -> bool:
        """Popup « application introuvable » : télécharger, localiser ou réessayer."""
        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(config.APP_NAME)
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        box.setText(
            f"<b>{exc.app_name}</b> est introuvable sur ce PC.<br><br>"
            f"Télécharge-le sur <a href='{exc.download_url}'>{exc.download_url}</a>, "
            "installe-le, puis clique sur « Réessayer ».")
        box.setInformativeText(
            f"S'il est déjà installé ailleurs, clique sur « Localiser… » "
            f"et sélectionne {exc.exe_name}.")
        download = box.addButton("Télécharger", QMessageBox.ButtonRole.ActionRole)
        locate = box.addButton("Localiser…", QMessageBox.ButtonRole.ActionRole)
        retry = box.addButton("Réessayer", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Quitter", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(download)

        while True:
            box.exec()
            clicked = box.clickedButton()
            if clicked is download:
                QDesktopServices.openUrl(QUrl(exc.download_url))
                continue  # la popup reste là le temps d'installer
            if clicked is locate:
                path, _ = QFileDialog.getOpenFileName(
                    None, f"Localiser {exc.exe_name}",
                    os.environ.get("ProgramFiles", ""), "Exécutable (*.exe)")
                if not path:
                    continue
                self._cfg[exc.config_key]["path"] = path
                config.save_config(self._cfg)
                log.info("%s localisé manuellement : %s", exc.app_name, path)
                return True
            return clicked is retry

    def _ask_retry(self, exc: Exception) -> bool:
        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle(config.APP_NAME)
        box.setText("Le démarrage a échoué.")
        details = [str(exc) or type(exc).__name__]
        hint = getattr(exc, "hint", "")
        if hint:
            details.append(hint)
        details.append(f"Détails dans le journal :\n{self._log_path}")
        box.setInformativeText("\n\n".join(details))
        retry = box.addButton("Réessayer", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Quitter", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(retry)
        box.exec()
        return box.clickedButton() is retry

    # ------------------------------------------------------------------ arrêt
    def shutdown(self) -> None:
        """Appelé une seule fois, sur QApplication.aboutToQuit."""
        if self._stopped:
            return
        self._stopped = True
        log.info("Arrêt en cours…")

        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            if not self._worker.wait(STARTUP_CANCEL_WAIT_MS):
                log.warning("Le thread de démarrage ne s'est pas arrêté à temps.")

        # Ordre important : REAPER se ferme pendant que Voicemeeter (donc le
        # pilote ASIO de l'insert) tourne encore, sinon REAPER afficherait une
        # erreur de carte son en partant.
        _safely(self._reaper.close, save_project=True)   # sauvegarde du projet
        _safely(self._vm.release)                         # inserts rendus
        _safely(self._processes.stop, "reaper")
        _safely(self._vm.close, shutdown_app=self._processes.will_terminate("voicemeeter"))
        # La fenêtre a écrit son état dans la config (closeEvent)
        _safely(config.save_config, self._cfg)
        _safely(self._processes.shutdown)


def _safely(func: Callable, *args, **kwargs) -> None:
    """Exécute une étape d'arrêt sans qu'un échec n'empêche les suivantes."""
    try:
        func(*args, **kwargs)
    except Exception:
        log.exception("Erreur pendant l'arrêt (%s)", getattr(func, "__qualname__", func))


# --------------------------------------------------------------------------- #
# Point d'entrée
# --------------------------------------------------------------------------- #
def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=config.APP_NAME, add_help=False)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--selftest", metavar="IMAGE")
    args, _qt_args = parser.parse_known_args(argv[1:])
    return args


def run_demo(app: QApplication, cfg: dict, selftest: str | None) -> int:
    """Console avec des moteurs simulés ; avec --selftest, capture puis quitte."""
    from core.demo import DemoProcesses, DemoReaper, DemoVoicemeeter

    vm, reaper, processes = DemoVoicemeeter(cfg), DemoReaper(cfg), DemoProcesses()
    window = MainWindow(cfg=cfg, vm=vm, reaper=reaper, processes=processes, persist=False)
    if selftest is None:
        app.setQuitOnLastWindowClosed(True)
        window.show()
        return app.exec()
    return run_selftest(app, cfg, window, vm, reaper, processes, Path(selftest))


def run_selftest(app: QApplication, cfg: dict, window: MainWindow, vm, reaper, processes,
                 image: Path) -> int:
    """Vérifie qu'un build fonctionne : polices, rendu de la console et des
    dialogues. Écrit IMAGE (+ _reglages / _vst) et un rapport IMAGE.txt."""
    from ui.settings_dialog import SettingsDialog
    from ui.vst_dialog import VstDialog

    def pump(seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.02)

    report = [f"{config.APP_NAME} {config.APP_VERSION}",
              f"Python {platform.python_version()} — {platform.platform()}",
              f"Plateforme Qt : {app.platformName()}"]
    problems = []
    if styles.FONT_FAMILY not in QFontDatabase.families():
        problems.append("police Inter non chargée")
    image.parent.mkdir(parents=True, exist_ok=True)
    window.resize(1920, 1040)
    window.show()
    pump(1.2)  # quelques niveaux pour remplir les VU
    shots = {image: window}

    settings = SettingsDialog(cfg, processes, reaper, vm, window, persist=False)
    settings.show()
    vst = VstDialog(window.strips[1].strip_name(), reaper, None, [], window)
    vst.show()
    pump(0.4)
    shots[image.with_name(f"{image.stem}_reglages{image.suffix}")] = settings
    shots[image.with_name(f"{image.stem}_vst{image.suffix}")] = vst
    for path, widget in shots.items():
        if widget.grab().save(str(path)):
            report.append(f"OK  {path.name} ({widget.width()}×{widget.height()})")
        else:
            problems.append(f"capture impossible : {path.name}")
    settings.hide()
    vst.hide()

    report.extend(f"ERREUR {p}" for p in problems)
    report.append("RÉSULTAT : " + ("ÉCHEC" if problems else "OK"))
    image.with_suffix(".txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    log.info("Auto-test : %s", " | ".join(report))
    return 1 if problems else 0


def main() -> int:
    args = parse_args(sys.argv)
    log_path = setup_logging()
    install_exception_hooks()
    log.info("=== %s %s — Python %s, %s ===", config.APP_NAME, config.APP_VERSION,
             platform.python_version(), platform.platform())

    set_windows_app_id()
    qInstallMessageHandler(qt_message_handler)
    app = QApplication(sys.argv)
    app.setApplicationName(config.APP_NAME)
    app.setApplicationVersion(config.APP_VERSION)
    icon_path = config.resource_path("assets/icon.ico")
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    # Pendant le démarrage, fermer une popup ne doit pas quitter l'app
    app.setQuitOnLastWindowClosed(False)

    try:
        cfg = config.load_config()
    except (OSError, ValueError) as exc:
        log.critical("Configuration illisible : %s", exc)
        QMessageBox.critical(None, config.APP_NAME,
                             f"Configuration par défaut illisible :\n{exc}\n\n"
                             "Réinstalle l'application.")
        return 1
    apply_log_level(cfg.get("log_level", "INFO"))
    styles.apply_theme(app)

    if args.demo or args.selftest:
        return run_demo(app, cfg, args.selftest)

    # Une seule instance : deux consoles se disputeraient Voicemeeter et REAPER
    lock = QLockFile(str(config.app_data_dir() / "instance.lock"))
    if not lock.tryLock(100):
        QMessageBox.information(None, config.APP_NAME, f"{config.APP_NAME} est déjà ouvert.")
        return 0

    mixer = MixerApp(app, cfg, log_path)
    app.aboutToQuit.connect(mixer.shutdown)
    mixer.start()
    exit_code = app.exec()

    lock.unlock()
    log.info("=== %s arrêté (code %d) ===", config.APP_NAME, exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
