"""
Pilotage de REAPER à travers le pont Lua (assets/reka_bridge.lua).

Pourquoi un pont plutôt que l'OSC natif de REAPER : l'OSC ne sait ni compter
les effets d'une piste, ni insérer un plugin par son nom, et il faut l'activer
à la main dans les préférences. Le pont utilise directement l'API de REAPER
(TrackFX_AddByName, TrackFX_Delete…) et ne demande aucun réglage.

Fonctionnement :
  - prepare_launch() copie le pont dans %APPDATA%/REKA/bridge et ajoute à
    Scripts/__startup.lua de REAPER un petit bloc qui le charge, mais
    seulement si REAPER a été lancé par l'app (variable REKA_IPC) ;
  - un thread lit state.txt (~20 fois/s) : projet, pistes, effets, carte son,
    accusés de réception, et sert de battement de cœur (connecté / déconnecté) ;
  - les commandes sont numérotées et réécrites dans commands.txt tant que le
    pont n'en a pas accusé réception.

Strips ↔ pistes : la piste d'une strip est la première piste REAPER dont
l'entrée commence sur le premier canal d'insert Voicemeeter de la strip. Ça
marche avec n'importe quel projet de l'utilisateur, sans toucher à son câblage.
"""

from __future__ import annotations

import logging
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject

from core import config
from core.effects import HOST_REAPER, EffectsHost, samples_to_ms
from core.voicemeeter_ctrl import strip_channels

log = logging.getLogger(__name__)

BRIDGE_FILENAME = "reka_bridge.lua"
IPC_ENV = "REKA_IPC"
# ASCII uniquement : le fichier de l'utilisateur peut être dans n'importe quel encodage
STARTUP_BEGIN = "-- >>> REKA (bloc gere automatiquement, ne pas modifier)"
STARTUP_END = "-- <<< REKA"
# Blocs posés par les versions précédentes (nom « REAPER Mixer ») : retirés
LEGACY_STARTUP_MARKERS = (("-- >>> REAPER Mixer", "-- <<< REAPER Mixer"),)
POLL_INTERVAL_S = 0.05
HEARTBEAT_TIMEOUT_S = 3.0
FXLIST_TIMEOUT_S = 15.0
SAVE_TIMEOUT_S = 4.0
ACTION_PREFERENCES = 40016   # « Options: Preferences… »

_PREFIX = re.compile(r"^(VST3i?|VSTi?|CLAPi?|JS|AUi?|DXi?|LV2i?|VST2i?)\s*:\s*", re.IGNORECASE)


def display_fx_name(name: str) -> str:
    """« VST3: Pro-Q 4 (FabFilter) » → « Pro-Q 4 (FabFilter) »."""
    return _PREFIX.sub("", name).strip()


@dataclass(frozen=True)
class FxInfo:
    name: str          # nom complet donné par REAPER
    enabled: bool
    open: bool
    latency: int = -1  # latence du plugin (PDC) en échantillons, -1 = inconnue

    @property
    def display_name(self) -> str:
        return display_fx_name(self.name)


@dataclass(frozen=True)
class TrackInfo:
    name: str
    fx_enabled: bool   # chaîne d'effets active (I_FXEN)
    fx: tuple[FxInfo, ...]
    index: int = 0
    rec_input: int = -1    # I_RECINPUT brut (-1 = aucune entrée)
    armed: bool = True
    monitor: int = 1       # I_RECMON (0 = pas d'écoute de l'entrée)
    hw_outputs: int = 1    # nombre de sorties matérielles

    @property
    def input_channel(self) -> int | None:
        """Premier canal (0-based) écouté, ou None (pas d'entrée, entrée MIDI)."""
        if self.rec_input < 0 or self.rec_input & 4096:
            return None
        return self.rec_input & 1023

    @property
    def processes_live(self) -> bool:
        """Vrai si la piste traite l'entrée en direct et renvoie le son :
        sans ça, activer l'insert Voicemeeter couperait la strip."""
        return self.armed and self.monitor > 0 and self.hw_outputs > 0


def strip_tracks(tracks: list[TrackInfo], channels: list[int]) -> list[TrackInfo | None]:
    """Piste de chaque strip : la première dont l'entrée commence sur son canal
    (même règle que find_track() dans le pont Lua)."""
    result = []
    for channel in channels:
        result.append(next((t for t in tracks if t.input_channel == channel), None))
    return result


@dataclass(frozen=True)
class AudioDevice:
    mode: str
    input: str
    output: str
    samplerate: str
    blocksize: str

    @property
    def is_voicemeeter_insert(self) -> bool:
        """Vrai si REAPER tourne sur le driver « Voicemeeter Insert Virtual ASIO »."""
        ident = f"{self.input} {self.output}".lower()
        return "voicemeeter" in ident and "insert" in ident

    def describe(self) -> str:
        device = self.input or self.output or "aucune"
        details = ", ".join(x for x in (self.mode, f"{self.samplerate} Hz" if self.samplerate else "",
                                        f"{self.blocksize} échantillons" if self.blocksize else "") if x)
        return f"{device} ({details})" if details else device


@dataclass
class _BridgeState:
    n: str
    session: str
    ack: int
    version: str
    device: AudioDevice | None
    tracks: list[TrackInfo]
    errors: list[tuple[int, str]]
    project: str = ""
    latency: tuple[int, int] | None = None   # latences d'entrée / sortie de la carte son


def _escape(value: object) -> str:
    return (str(value).replace("\\", "\\\\").replace("\t", "\\t")
            .replace("\r", "").replace("\n", "\\n"))


_UNESCAPE = re.compile(r"\\(.)")


def _unescape(value: str) -> str:
    return _UNESCAPE.sub(lambda m: {"\\": "\\", "t": "\t", "n": "\n"}.get(m.group(1), m.group(0)),
                         value)


def parse_state(text: str) -> _BridgeState | None:
    """Analyse state.txt ; None si le fichier est incomplet."""
    lines = text.split("\n")
    if not lines or not lines[0].startswith("STATE "):
        return None
    n = lines[0][6:].strip()
    if f"END {n}" not in lines:
        return None
    state = _BridgeState(n=n, session="", ack=0, version="", device=None, tracks=[], errors=[])
    fx_by_track: dict[int, list[FxInfo]] = {}
    track_rows: list[dict] = []
    for line in lines[1:]:
        # « CLÉ<tab>champs… » pour les enregistrements, « CLÉ valeur » pour le reste
        key, _, rest = line.partition("\t")
        if " " in key:
            key, _, rest = line.partition(" ")
        fields = [_unescape(f) for f in rest.split("\t")]
        try:
            if key == "SESSION":
                state.session = rest.strip()
            elif key == "ACK":
                state.ack = int(rest)
            elif key == "VERSION":
                state.version = _unescape(rest)
            elif key == "PROJECT":
                state.project = fields[0]
            elif key == "DEVICE" and len(fields) >= 5:
                state.device = AudioDevice(*fields[:5])
            elif key == "TRACK" and len(fields) >= 3:
                # index, nom, chaîne active, nb d'effets, entrée, armée, écoute, sorties
                extra = [int(float(f)) for f in fields[4:8]] if len(fields) >= 8 else []
                row = {"index": int(fields[0]), "name": fields[1], "fx_enabled": fields[2] == "1"}
                if extra:
                    row.update(rec_input=extra[0], armed=extra[1] != 0, monitor=extra[2],
                               hw_outputs=extra[3])
                track_rows.append(row)
            elif key == "FX" and len(fields) >= 5:
                pdc = int(float(fields[5])) if len(fields) >= 6 and fields[5] else -1
                fx_by_track.setdefault(int(fields[0]), []).append(
                    FxInfo(name=fields[2], enabled=fields[3] == "1", open=fields[4] == "1",
                           latency=pdc))
            elif key == "LATENCY" and len(fields) >= 2:
                state.latency = (int(float(fields[0])), int(float(fields[1])))
            elif key == "ERR" and len(fields) >= 2:
                state.errors.append((int(fields[0]), fields[1]))
        except ValueError:
            log.debug("Ligne d'état ignorée : %r", line)
    for row in sorted(track_rows, key=lambda r: r["index"]):
        state.tracks.append(TrackInfo(fx=tuple(fx_by_track.get(row["index"], [])), **row))
    return state


def reaper_resource_path(exe: Path | None, configured: str | None = None) -> Path:
    """Dossier de ressources de REAPER (Scripts, reaper.ini, cache des plugins).

    Installation portable : reaper.ini à côté de reaper.exe. Sinon %APPDATA%/REAPER."""
    path = config.resolve_path(configured)
    if path is not None:
        return path
    if exe is not None and (exe.parent / "reaper.ini").is_file():
        return exe.parent
    return config.app_data_dir().parent / "REAPER"


def track_latency_samples(track: TrackInfo | None) -> int:
    """Latence (PDC) des plugins actifs d'une piste, en échantillons."""
    if track is None or not track.fx_enabled:
        return 0
    return sum(max(0, fx.latency) for fx in track.fx if fx.enabled)


class ReaperController(EffectsHost):
    kind = HOST_REAPER
    process_key = "reaper"
    manages_fx = True
    has_projects = True
    has_audio_device = True

    def __init__(self, cfg: dict, parent: QObject | None = None) -> None:
        super().__init__(cfg, parent)
        # Une strip est désignée auprès du pont par son premier canal d'insert
        self._channels = strip_channels(cfg)
        self._ipc_dir = config.app_data_dir() / "bridge"
        self._session = uuid.uuid4().hex[:12]
        self._lock = threading.Lock()
        self._seq = 0
        self._pending: list[tuple[int, str]] = []
        self._commands_dirty = True
        self._rev = 0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._ready = threading.Event()        # pont vivant et à l'écoute de notre session
        self._ack = 0
        self._ack_changed = threading.Condition()
        # Utilisés par le thread de lecture
        self._last_n: str | None = None
        self._last_beat = 0.0
        self._connected = False
        self._tracks: list[TrackInfo | None] | None = None
        self._device: AudioDevice | None = None
        self._project = ""
        self._seen_errors: set[int] = set()
        self._fx_request: tuple[str, float] | None = None
        self._installed_fx: list[tuple[str, str]] | None = None
        self._error_file_logged = False
        self._io_latency: tuple[int, int] | None = None

    # --------------------------------------------------------- préparation
    @property
    def ipc_dir(self) -> Path:
        return self._ipc_dir

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def device(self) -> AudioDevice | None:
        return self._device

    @property
    def installed_fx(self) -> list[tuple[str, str]] | None:
        return self._installed_fx

    @property
    def project_path(self) -> str:
        """Projet ouvert dans REAPER (vide tant que REAPER n'a pas répondu)."""
        return self._project

    @property
    def strip_tracks(self) -> list[TrackInfo | None]:
        return list(self._tracks or [None] * len(self._channels))

    def resource_path(self, exe: Path | None) -> Path:
        return reaper_resource_path(exe, self._cfg.get("reaper", {}).get("resource_path"))

    # ------------------------------------------------------------ insert
    def insert_strips(self, tracks: list, connected: bool) -> set[int]:
        """Insert seulement pour les strips dont la piste traite vraiment le son
        (armée, écoutée, avec une sortie), et quand REAPER tourne sur le pilote
        de l'insert : sinon la strip serait muette."""
        device = self._device
        if not connected or device is None or not device.is_voicemeeter_insert:
            return set()
        return {i for i, track in enumerate(tracks) if track is not None and track.processes_live}

    # ----------------------------------------------------------- latence
    def _samplerate(self) -> float:
        try:
            return float(self._device.samplerate) if self._device is not None else 0.0
        except ValueError:
            return 0.0

    def strip_latency_ms(self, strip: int) -> float | None:
        tracks = self._tracks or []
        rate = self._samplerate()
        if strip >= len(tracks) or tracks[strip] is None or rate <= 0:
            return None
        return samples_to_ms(track_latency_samples(tracks[strip]), rate)

    def fx_latency_ms(self, strip: int, fx_index: int) -> float | None:
        tracks = self._tracks or []
        rate = self._samplerate()
        if strip >= len(tracks) or tracks[strip] is None or rate <= 0:
            return None
        fx = tracks[strip].fx
        if not 0 <= fx_index < len(fx) or fx[fx_index].latency < 0:
            return None
        return samples_to_ms(fx[fx_index].latency, rate)

    def latency_report(self) -> str:
        device, rate = self._device, self._samplerate()
        if device is None or rate <= 0:
            return "Latence inconnue : REAPER n'a pas encore indiqué sa carte son."
        if self._io_latency is not None and sum(self._io_latency) > 0:
            buffers = sum(self._io_latency)
            detail = f"entrée {self._io_latency[0]} + sortie {self._io_latency[1]} échantillons"
        else:
            try:
                buffers = 2 * int(device.blocksize)
            except ValueError:
                buffers = 0
            detail = f"2 × {device.blocksize} échantillons"
        text = (f"Aller-retour par REAPER : {samples_to_ms(buffers, rate):.1f} ms "
                f"({detail} à {device.samplerate} Hz)")
        worst = max(((self.strip_latency_ms(i) or 0.0, i) for i in range(len(self._channels))),
                    default=(0.0, 0))
        if worst[0] >= 0.05:
            names = self._cfg.get("ui", {}).get("strip_names", [])
            name = names[worst[1]] if worst[1] < len(names) else f"Strip {worst[1] + 1}"
            text += f", plus {worst[0]:.1f} ms de plugins sur « {name} »"
        return text + "."

    def prepare_launch(self, exe: Path) -> dict[str, str]:
        """Installe le pont avant le lancement de REAPER ; renvoie les
        variables d'environnement à passer au processus."""
        self._ipc_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(config.resource_path(f"assets/{BRIDGE_FILENAME}"),
                        self._ipc_dir / BRIDGE_FILENAME)
        for stale in ("commands.txt", "state.txt", "fxlist.txt", "bridge_error.txt"):
            (self._ipc_dir / stale).unlink(missing_ok=True)
        install_startup_hook(self.resource_path(exe))
        # Nouvel essai (« Réessayer ») : le thread de lecture du premier essai
        # tourne encore ; il doit réécrire commands.txt (qui vient d'être effacé)
        # et attendre le battement de cœur du nouveau REAPER.
        with self._lock:
            self._commands_dirty = True
        self._last_beat = 0.0
        self._last_n = None
        self._error_file_logged = False
        if not self._connected:
            self._ready.clear()
        return {IPC_ENV: str(self._ipc_dir)}

    # ----------------------------------------------------------- connexion
    def start(self) -> None:
        """Démarre la lecture de l'état ; les pistes sans nom prennent celui de leur strip."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._queue_names()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="reaper-bridge", daemon=True)
        self._thread.start()

    def ping(self, timeout_s: float) -> bool:
        """True dès que le pont répond à notre session (attend au plus timeout_s)."""
        return self._ready.wait(timeout_s)

    def close(self, save_project: bool = True) -> None:
        if save_project and self._connected:
            seq = self._send("save")
            if not self._wait_ack(seq, SAVE_TIMEOUT_S):
                log.warning("REAPER n'a pas confirmé la sauvegarde du projet.")
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    # ------------------------------------------------------ commandes (UI)
    # Les strips sont désignées par leur canal : le pont retrouve leur piste.
    def rename_track(self, strip: int, name: str) -> None:
        self._send("rename", self._channels[strip], name)

    def add_fx(self, strip: int, fx_name: str) -> None:
        """Ajoute un plugin ; si la strip n'a pas de piste, le pont en crée une
        (câblée sur son insert) quand reaper.manage_routing est activé."""
        create = bool(self._cfg.get("reaper", {}).get("manage_routing", True))
        self._send("add_fx", self._channels[strip], self._strip_name(strip), int(create), fx_name)

    def remove_fx(self, strip: int, fx_index: int) -> None:
        self._send("remove_fx", self._channels[strip], fx_index)

    def set_fx_enabled(self, strip: int, fx_index: int, enabled: bool) -> None:
        self._send("fx_enabled", self._channels[strip], fx_index, int(enabled))

    def set_chain_enabled(self, strip: int, enabled: bool) -> None:
        self._send("chain_enabled", self._channels[strip], int(enabled))

    def toggle_fx_ui(self, strip: int, fx_index: int) -> None:
        self._send("open_fx", self._channels[strip], fx_index)

    def open_project(self, path: str | Path) -> None:
        """Sauvegarde le projet en cours puis ouvre `path` dans REAPER, à chaud."""
        log.info("Ouverture du projet REAPER : %s", path)
        self._send("open_project", str(path))
        self._queue_names()

    def save_project(self) -> None:
        self._send("save")

    def open_preferences(self) -> None:
        self._send("action", ACTION_PREFERENCES)

    def request_installed_fx(self) -> None:
        """Demande la liste des plugins installés ; réponse via installed_fx_ready."""
        request = uuid.uuid4().hex[:8]
        with self._lock:
            self._fx_request = (request, time.monotonic())
        self._send("list_fx", request)

    def _strip_name(self, strip: int) -> str:
        names = self._cfg.get("ui", {}).get("strip_names", [])
        return names[strip] if strip < len(names) else f"Strip {strip + 1}"

    def _queue_names(self) -> None:
        for strip, channel in enumerate(self._channels):
            self._send("ensure_name", channel, self._strip_name(strip))

    def _send(self, verb: str, *args: object) -> int:
        with self._lock:
            self._seq += 1
            line = "\t".join([str(self._seq), verb, *(_escape(a) for a in args)])
            self._pending.append((self._seq, line))
            self._commands_dirty = True
            seq = self._seq
        self._wake.set()
        return seq

    def _wait_ack(self, seq: int, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        with self._ack_changed:
            while self._ack < seq:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._connected:
                    return False
                self._ack_changed.wait(remaining)
        return True

    # --------------------------------------------------- thread de lecture
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._write_commands()
                self._read_state()
                self._read_fxlist()
                self._check_bridge_error()
            except Exception:
                log.exception("Erreur dans la liaison avec REAPER")
            self._wake.wait(POLL_INTERVAL_S)
            self._wake.clear()
        self._write_commands()  # dernières commandes (sauvegarde…)

    def _write_commands(self) -> None:
        with self._lock:
            if not self._commands_dirty:
                return
            self._rev += 1
            body = "\n".join(line for _, line in self._pending)
            text = f"CMDS {self._session} {self._rev}\n" + (body + "\n" if body else "") \
                + f"END {self._rev}\n"
            self._commands_dirty = False
        try:
            (self._ipc_dir / "commands.txt").write_text(text, encoding="utf-8", newline="\n")
        except OSError as exc:  # fichier verrouillé un instant par REAPER : on retente
            log.debug("commands.txt non écrit (%s), nouvel essai.", exc)
            with self._lock:
                self._commands_dirty = True

    def _read_state(self) -> None:
        now = time.monotonic()
        try:
            text = (self._ipc_dir / "state.txt").read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if text.startswith("CLOSED"):
            self._last_beat = 0.0
        state = parse_state(text) if text else None
        if state is not None and state.n != self._last_n:
            self._last_n = state.n
            if state.session == self._session:
                self._last_beat = now
                self._apply_state(state)
        self._set_connected(self._last_beat > 0 and now - self._last_beat < HEARTBEAT_TIMEOUT_S)

    def _apply_state(self, state: _BridgeState) -> None:
        self.version = state.version
        self._io_latency = state.latency
        with self._lock:
            pruned = [p for p in self._pending if p[0] > state.ack]
            if len(pruned) != len(self._pending):
                self._pending = pruned
                self._commands_dirty = True
        with self._ack_changed:
            self._ack = max(self._ack, state.ack)
            self._ack_changed.notify_all()

        for seq, message in state.errors:
            if seq not in self._seen_errors:
                self._seen_errors.add(seq)
                log.error("REAPER : %s", message)
                self.command_failed.emit(message)
        if state.device is not None and state.device != self._device:
            self._device = state.device
            log.info("Carte son de REAPER : %s", state.device.describe())
            self.device_changed.emit(state.device)
        if state.project != self._project:
            self._project = state.project
            log.info("Projet REAPER ouvert : %s", state.project or "(sans fichier)")
            self.project_changed.emit(state.project)
        tracks = strip_tracks(state.tracks, self._channels)
        if tracks != self._tracks:
            self._tracks = tracks
            self.tracks_changed.emit(list(tracks))
        self._ready.set()

    def _read_fxlist(self) -> None:
        with self._lock:
            request = self._fx_request
        if request is None:
            return
        request_id, started = request
        try:
            text = (self._ipc_dir / "fxlist.txt").read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        lines = text.split("\n")
        if lines and lines[0] == f"FXLIST {request_id}" and f"END {request_id}" in lines:
            body = lines[1:lines.index(f"END {request_id}")]
            if body == ["UNSUPPORTED"]:
                result = None
            else:
                result = []
                for line in body:
                    name, _, ident = line.partition("\t")
                    if name:
                        result.append((_unescape(name), _unescape(ident)))
                self._installed_fx = result
            self._finish_fx_request(result)
        elif time.monotonic() - started > FXLIST_TIMEOUT_S:
            log.warning("REAPER n'a pas envoyé la liste des plugins.")
            self._finish_fx_request(None)

    def _finish_fx_request(self, result: list[tuple[str, str]] | None) -> None:
        with self._lock:
            self._fx_request = None
        self.installed_fx_ready.emit(result)

    def _check_bridge_error(self) -> None:
        if self._error_file_logged:
            return
        path = self._ipc_dir / "bridge_error.txt"
        if path.is_file():
            self._error_file_logged = True
            log.error("Le pont REAPER n'a pas pu démarrer : %s",
                      path.read_text(encoding="utf-8", errors="replace").strip())

    def _set_connected(self, connected: bool) -> None:
        if connected != self._connected:
            self._connected = connected
            log.info("REAPER %s.", "connecté" if connected else "déconnecté")
            if not connected:
                with self._ack_changed:
                    self._ack_changed.notify_all()
            self.connection_changed.emit(connected)


# --------------------------------------------------------------------------- #
# Chargement du pont au démarrage de REAPER
# --------------------------------------------------------------------------- #
def startup_block() -> str:
    """Bloc ajouté à Scripts/__startup.lua : il ne fait rien quand REAPER
    est lancé normalement (variable d'environnement absente)."""
    return f"""{STARTUP_BEGIN}
do
  local dir = os.getenv("{IPC_ENV}")
  if dir and dir ~= "" then
    local ok, err = pcall(dofile, dir .. "/{BRIDGE_FILENAME}")
    if not ok then
      local f = io.open(dir .. "/bridge_error.txt", "w")
      if f then f:write(tostring(err)) f:close() end
    end
  end
end
{STARTUP_END}
"""


def install_startup_hook(resource_path: Path) -> Path:
    """Ajoute (ou met à jour) notre bloc dans __startup.lua, sans toucher au
    reste du fichier de l'utilisateur. Le bloc est placé en tête : une erreur
    dans le code de l'utilisateur ne peut pas l'empêcher de s'exécuter."""
    scripts = resource_path / "Scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    path = scripts / "__startup.lua"
    # surrogateescape : les octets du script de l'utilisateur sont réécrits à
    # l'identique, quel que soit son encodage
    current = (path.read_bytes().decode("utf-8", errors="surrogateescape")
               if path.is_file() else "")
    cleaned = remove_startup_block(current)
    block = startup_block()
    new = (block + "\n" + cleaned.lstrip("\n")) if cleaned.strip() else block
    if new != current:
        path.write_bytes(new.encode("utf-8", errors="surrogateescape"))
        log.info("Chargement du pont ajouté à %s", path)
    return path


def remove_startup_block(text: str) -> str:
    """Retire notre bloc, et ceux posés par les anciennes versions de l'app."""
    for begin, end_marker in ((STARTUP_BEGIN, STARTUP_END), *LEGACY_STARTUP_MARKERS):
        start = text.find(begin)
        if start < 0:
            continue
        end = text.find(end_marker, start + len(begin))
        if end < 0:
            text = text[:start]
        else:
            text = text[:start] + text[end + len(end_marker):].lstrip("\n")
    return text
