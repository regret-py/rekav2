"""
Musique en cours : titre, artiste, pochette, et commandes précédent /
lecture-pause / suivant de l'application qui joue (Spotify, Deezer, un
navigateur, le Lecteur multimédia…).

Ce sont les contrôles média de Windows, ceux de l'écran de verrouillage et de
la fenêtre du volume (GlobalSystemMediaTransportControls), lus avec
Python/WinRT : rien à régler dans les applications.

Trois moteurs, même interface :
- WindowsMedia : le vrai ; les appels WinRT bloquent, ils tournent donc dans
  un thread qui scrute la session en cours toutes les secondes ;
- DemoMedia    : morceaux simulés (--demo, --selftest, tests) ;
- NullMedia    : indisponible (autre système, winrt absent) : lecteur caché.

Les résultats remontent par le signal `changed`, livré dans le thread UI.
"""

from __future__ import annotations

import contextlib
import importlib.util
import logging
import sys
import threading
from dataclasses import dataclass, replace

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

POLL_S = 1.0                  # scrutation de la session en cours
AFTER_COMMAND_S = 0.25        # relecture rapide après précédent / pause / suivant
RETRY_S = 3.0                 # après une erreur de WinRT
WINRT_TIMEOUT_S = 3.0         # une opération WinRT qui ne répond pas est abandonnée
COVER_READS = 4               # la pochette arrive souvent un peu après le titre : relue 4 fois
MAX_COVER_BYTES = 4 * 1024 * 1024
# Modules WinRT utilisés (à embarquer dans l'exe : voir build.spec)
WINRT_MODULES = ("winrt.windows.media.control", "winrt.windows.storage.streams",
                 "winrt.windows.foundation")
PLAYING = 4                   # GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING
ASYNC_COMPLETED, ASYNC_ERROR = 1, 3   # Windows.Foundation.AsyncStatus

# Noms lisibles des applications (identifiant Windows de l'application, en minuscules)
APP_NAMES = {
    "spotify": "Spotify", "deezer": "Deezer", "chrome": "Chrome", "msedge": "Edge",
    "firefox": "Firefox", "308046b0af4a39cb": "Firefox", "opera": "Opera", "brave": "Brave",
    "vivaldi": "Vivaldi", "vlc": "VLC", "itunes": "iTunes", "applemusic": "Apple Music",
    "microsoft.zunemusic": "Lecteur multimédia", "tidal": "TIDAL", "amazon music": "Amazon Music",
    "discord": "Discord",
}


@dataclass(frozen=True)
class MediaInfo:
    title: str
    artist: str = ""
    album: str = ""
    app: str = ""                 # application qui joue (« Spotify »)
    playing: bool = False
    can_previous: bool = True
    can_next: bool = True
    can_play_pause: bool = True
    cover: bytes = b""            # image de la pochette (PNG, JPEG…), b"" si aucune


def app_label(app_id: str) -> str:
    """Nom affiché de l'application d'après son identifiant Windows :
    « Spotify.exe », « SpotifyAB.SpotifyMusic_…!Spotify », « MSEdge »…"""
    name = (app_id or "").split("!")[-1].replace("/", "\\").rsplit("\\", 1)[-1].strip()
    if name.lower().endswith(".exe"):
        name = name[:-4]
    key = name.lower()
    for known, label in APP_NAMES.items():
        if key == known or key.startswith(known):
            return label
    return name


class MediaBackend(QObject):
    """Interface commune : `changed` (MediaInfo, ou None si rien ne joue)."""

    changed = Signal(object)
    available = False

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._current: MediaInfo | None = None

    @property
    def current(self) -> MediaInfo | None:
        return self._current

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def play_pause(self) -> None:
        pass

    def next(self) -> None:
        pass

    def previous(self) -> None:
        pass

    def _publish(self, info: MediaInfo | None) -> None:
        if info != self._current:
            self._current = info
            self.changed.emit(info)


class NullMedia(MediaBackend):
    def __init__(self, reason: str = "Disponible sous Windows seulement.",
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.reason = reason


class DemoMedia(MediaBackend):
    """Quelques morceaux simulés, avec des pochettes dessinées."""

    available = True
    TRACKS = (
        ("Néons de minuit", "Les Ondes", "Nuit blanche", "Spotify", "#3b82f6", "#a66bff"),
        ("Ligne de basse", "Studio 206", "Sessions", "Deezer", "#ff4fb8", "#ff9a1f"),
        ("Retour au calme", "Regrettable", "REKA", "Chrome", "#00e5ff", "#00ff88"),
    )

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._index = 0
        self._playing = True
        self._covers: dict[int, bytes] = {}

    def start(self) -> None:
        self._emit()

    def play_pause(self) -> None:
        self._playing = not self._playing
        self._emit()

    def next(self) -> None:
        self._index = (self._index + 1) % len(self.TRACKS)
        self._emit()

    def previous(self) -> None:
        self._index = (self._index - 1) % len(self.TRACKS)
        self._emit()

    def _emit(self) -> None:
        title, artist, album, app, *_colors = self.TRACKS[self._index]
        self._publish(MediaInfo(title, artist, album, app, self._playing, cover=self._cover()))

    def _cover(self) -> bytes:
        """Pochette dessinée (dégradé et initiales), en PNG comme une vraie."""
        if self._index not in self._covers:
            from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
            from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter

            title, artist, _album, _app, start, end = self.TRACKS[self._index]
            image = QImage(240, 240, QImage.Format.Format_ARGB32)
            p = QPainter(image)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            gradient = QLinearGradient(0, 0, 240, 240)
            gradient.setColorAt(0, QColor(start))
            gradient.setColorAt(1, QColor(end))
            p.fillRect(image.rect(), gradient)
            font = QFont()
            font.setPixelSize(84)
            font.setBold(True)
            p.setFont(font)
            p.setPen(QColor(255, 255, 255, 220))
            initials = "".join(word[0] for word in (title.split()[0], artist.split()[0])).upper()
            p.drawText(QRectF(0, 0, 240, 240), Qt.AlignmentFlag.AlignCenter, initials)
            p.end()
            data = QByteArray()
            buffer = QBuffer(data)
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            image.save(buffer, "PNG")
            self._covers[self._index] = bytes(data)
        return self._covers[self._index]


# --------------------------------------------------------------------------- #
# Windows : contrôles média du système (WinRT)
# --------------------------------------------------------------------------- #
def winrt_wait(operation, timeout: float = WINRT_TIMEOUT_S):
    """Résultat d'une opération asynchrone WinRT, en bloquant au plus `timeout` s."""
    waiter = getattr(operation, "wait", None)
    if waiter is None:
        return operation.get()
    status = int(waiter(timeout))
    if status not in (ASYNC_COMPLETED, ASYNC_ERROR):
        with contextlib.suppress(Exception):
            operation.cancel()
        raise TimeoutError("Windows ne répond pas (contrôles média)")
    return operation.get_results()        # lève l'erreur de Windows si l'opération a échoué


def run_in_mta(function, timeout: float = 2 * WINRT_TIMEOUT_S):
    """Exécute `function()` dans un thread WinRT (appartement multithread, comme
    le lecteur) et renvoie son résultat : le thread de l'interface est un
    appartement « STA » où les attentes WinRT bloquantes sont déconseillées."""
    outcome: dict[str, object] = {}

    def target() -> None:
        try:
            from winrt import runtime

            with contextlib.suppress(Exception):
                runtime.init_apartment(runtime.ApartmentType.MULTI_THREADED)
            outcome["result"] = function()
        except BaseException as exc:      # renvoyée à l'appelant
            outcome["error"] = exc

    thread = threading.Thread(target=target, name="winrt-call", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError("Windows ne répond pas (contrôles média)")
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("result")


def probe_media_controls() -> str:
    """Les contrôles média de Windows répondent-ils ? (auto-test de l'exe)"""
    def probe() -> str:
        from winrt.windows.media.control import \
            GlobalSystemMediaTransportControlsSessionManager as Manager

        session = winrt_wait(Manager.request_async()).get_current_session()
        return "une session en cours" if session is not None else "aucune session en cours"

    return run_in_mta(probe)


def read_cover(reference) -> bytes:
    """Octets de la pochette (IRandomAccessStreamReference de Windows), b"" si aucune."""
    if reference is None:
        return b""
    from winrt.windows.storage.streams import Buffer, InputStreamOptions

    stream = winrt_wait(reference.open_read_async())
    try:
        size = int(stream.size)
        if size <= 0 or size > MAX_COVER_BYTES:
            return b""
        buffer = Buffer(size)
        result = winrt_wait(stream.read_async(buffer, size, InputStreamOptions.READ_AHEAD))
        return bytes(memoryview(result))[: int(result.length)]
    finally:
        with contextlib.suppress(Exception):
            stream.close()


class WindowsMedia(MediaBackend):
    available = True

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._lock = threading.Lock()
        self._commands: list[str] = []
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="media-poll", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=WINRT_TIMEOUT_S + 1)
            self._thread = None

    def play_pause(self) -> None:
        self._command("play_pause")

    def next(self) -> None:
        self._command("next")

    def previous(self) -> None:
        self._command("previous")

    def _command(self, name: str) -> None:
        with self._lock:
            self._commands.append(name)
        self._wake.set()

    # ------------------------------------------------ thread de scrutation
    def _run(self) -> None:
        try:
            from winrt import runtime

            with contextlib.suppress(Exception):     # déjà initialisé : rien à faire
                runtime.init_apartment(runtime.ApartmentType.MULTI_THREADED)
            from winrt.windows.media.control import \
                GlobalSystemMediaTransportControlsSessionManager as Manager
        except Exception as exc:
            log.warning("Musique en cours indisponible (WinRT) : %s", exc)
            return
        manager = None
        cover_key, cover, cover_reads = None, b"", 0
        failing = False
        while not self._stop.is_set():
            delay = POLL_S
            try:
                if manager is None:
                    manager = winrt_wait(Manager.request_async())
                session = manager.get_current_session()
                if self._run_commands(session):
                    delay = AFTER_COMMAND_S
                info, thumbnail = self._read(session)
                key = None if info is None else (info.app, info.title, info.artist, info.album)
                if key != cover_key:
                    cover_key, cover, cover_reads = key, b"", COVER_READS
                if info is not None and cover_reads > 0:
                    cover_reads -= 1
                    with contextlib.suppress(Exception):   # pochette illisible : sans pochette
                        cover = read_cover(thumbnail) or cover
                self._publish(None if info is None else replace(info, cover=cover))
                if failing:
                    log.info("Musique en cours : contrôles média de Windows de nouveau joignables.")
                failing = False
            except Exception as exc:
                if not failing:
                    log.warning("Musique en cours : contrôles média de Windows injoignables : %s", exc)
                failing, manager, delay = True, None, RETRY_S
            self._wake.wait(delay)
            self._wake.clear()

    def _run_commands(self, session) -> bool:
        with self._lock:
            commands, self._commands = self._commands, []
        if session is None:
            return False
        actions = {"play_pause": session.try_toggle_play_pause_async,
                   "next": session.try_skip_next_async,
                   "previous": session.try_skip_previous_async}
        for name in commands:
            try:
                winrt_wait(actions[name]())
            except Exception as exc:
                log.warning("Musique en cours : commande « %s » refusée : %s", name, exc)
        return bool(commands)

    @staticmethod
    def _read(session) -> tuple[MediaInfo | None, object]:
        """(morceau de la session en cours, référence de sa pochette)."""
        if session is None:
            return None, None
        props = winrt_wait(session.try_get_media_properties_async())
        title = str(getattr(props, "title", "") or "").strip()
        if props is None or not title:
            return None, None
        playback = session.get_playback_info()
        controls = getattr(playback, "controls", None)

        def enabled(name: str) -> bool:
            return bool(getattr(controls, name, True)) if controls is not None else True

        status = getattr(playback, "playback_status", 0)
        info = MediaInfo(
            title=title,
            artist=str(props.artist or "").strip(),
            album=str(props.album_title or "").strip(),
            app=app_label(str(session.source_app_user_model_id or "")),
            playing=int(status) == PLAYING,
            can_previous=enabled("is_previous_enabled"),
            can_next=enabled("is_next_enabled"),
            can_play_pause=enabled("is_play_pause_toggle_enabled"),
        )
        return info, props.thumbnail


def create_media_backend(cfg: dict) -> MediaBackend:
    """Le moteur réel sous Windows, sinon un moteur indisponible (jamais d'exception)."""
    if sys.platform != "win32":
        return NullMedia()
    try:
        missing = [name for name in WINRT_MODULES if importlib.util.find_spec(name) is None]
    except (ImportError, ValueError) as exc:
        missing = [str(exc)]
    if missing:
        log.warning("Musique en cours indisponible : modules absents (%s).", ", ".join(missing))
        return NullMedia("Modules Windows (winrt) absents.")
    return WindowsMedia()
