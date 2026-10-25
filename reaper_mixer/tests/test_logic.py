"""Logique indépendante de la plateforme : config, loi des faders, protocole du
pont, scan des plugins, contrôleur Voicemeeter (avec une fausse API)."""

import json
import sys
import types

import pytest

from conftest import pump


# --------------------------------------------------------------------- config
def test_config_merge_and_corrupted_file(appdata):
    from core import config

    cfg = config.load_config()
    assert config.user_config_path().is_file()
    assert cfg["voicemeeter"]["strip_maps"]["potato"] == [0, 1, 5, 6, 2, 7]

    config.user_config_path().write_text(json.dumps({"reaper": {"path": "C:/x.exe"}, "extra": 1}))
    cfg = config.load_config()
    assert cfg["reaper"]["path"] == "C:/x.exe" and cfg["reaper"]["manage_routing"] is True
    assert cfg["extra"] == 1

    config.user_config_path().write_text("{pas du json")
    cfg = config.load_config()
    assert (appdata / config.APP_NAME / "config.invalide.json").is_file()
    assert cfg["reaper"]["path"] == ""


# --------------------------------------------------------------- loi du fader
def test_fader_taper_matches_mockup_and_roundtrips():
    from ui.fader import db_to_ratio, format_db, ratio_to_db

    # Plage de Voicemeeter : +12 dB en haut, -60 dB en bas ; sous 0 dB, la loi de la maquette
    assert db_to_ratio(12) == 0 and db_to_ratio(-60) == 1 and db_to_ratio(20) == 0
    assert db_to_ratio(0) == pytest.approx(0.15)
    assert db_to_ratio(-6) == pytest.approx(0.15 + 0.85 * 0.152)
    assert db_to_ratio(-48) == pytest.approx(0.15 + 0.85 * 0.729)
    previous = -1.0
    for tenth in range(120, -601, -1):
        db = tenth / 10
        ratio = db_to_ratio(db)
        assert ratio > previous
        previous = ratio
        assert ratio_to_db(ratio) == pytest.approx(db, abs=1e-6)
    # Affichage identique à Voicemeeter : une décimale, -60.0 en bas (pas de « -inf »)
    assert [format_db(v) for v in (-60, -75, -12.37, -0.04, 0, 6, 12.5)] == [
        "-60.0", "-60.0", "-12.4", "0.0", "0.0", "6.0", "12.0"]


# ------------------------------------------------------------ pont REAPER
def test_bridge_state_parsing_and_escaping():
    from core.reaper_ctrl import _escape, display_fx_name, parse_state

    name = "Nom\tbizarre\\avec\nretour"
    text = "\n".join([
        "STATE 7", "SESSION abc", "ACK 12", "VERSION 7.22/win64",
        "DEVICE\tASIO\tVoicemeeter Insert Virtual ASIO\tVoicemeeter Insert Virtual ASIO\t48000\t256",
        "TRACK\t0\tMicrophone\t0\t2", "FX\t0\t0\tVST3: Pro-Q 4 (FabFilter)\t1\t0",
        f"FX\t0\t1\t{_escape(name)}\t0\t1", "TRACK\t1\tMusique\t1\t0",
        "ERR\t11\tREAPER ne trouve pas le plugin", "END 7", ""])
    state = parse_state(text)
    assert state.session == "abc" and state.ack == 12 and state.version == "7.22/win64"
    assert state.device.is_voicemeeter_insert and "48000 Hz" in state.device.describe()
    mic, music = state.tracks
    assert not mic.fx_enabled and music.fx_enabled and music.fx == ()
    assert mic.fx[0].display_name == "Pro-Q 4 (FabFilter)" and mic.fx[1].name == name
    assert not mic.fx[1].enabled and mic.fx[1].open
    assert state.errors == [(11, "REAPER ne trouve pas le plugin")]
    assert parse_state(text.replace("END 7", "END 6")) is None   # fichier incomplet
    assert parse_state("CLOSED\n") is None
    assert display_fx_name("VST3i: Serum (Xfer Records)") == "Serum (Xfer Records)"
    assert display_fx_name("JS: Volume") == "Volume"


def test_tracks_with_routing_and_strip_mapping():
    from core.reaper_ctrl import TrackInfo, parse_state, strip_tracks

    text = "\n".join([
        "STATE 3", "SESSION s", "ACK 0", "VERSION 7", "PROJECT\tC:/Projets/MAIN.rpp",
        "TRACK\t0\t\t1\t0\t0\t1\t1\t1",          # mono, entrée 1 (canal 0) : le micro
        "TRACK\t1\tVoix bis\t1\t0\t0\t1\t1\t1",  # même canal : ignorée (la 1re gagne)
        "TRACK\t2\tMusique\t1\t0\t1026\t1\t1\t1",
        "TRACK\t3\tMIDI\t1\t0\t4096\t1\t1\t1",
        "TRACK\t4\tSans sortie\t1\t0\t1028\t1\t1\t0",
        "TRACK\t5\tAncien format\t1\t0",         # pont d'avant : champs absents
        "END 3", ""])
    state = parse_state(text)
    assert state.project == "C:/Projets/MAIN.rpp"
    tracks = state.tracks
    assert [t.input_channel for t in tracks] == [0, 0, 2, None, 4, None]
    assert tracks[0].processes_live and not tracks[4].processes_live
    mapped = strip_tracks(tracks, [0, 2, 10, 18, 4, 26])
    assert [t.index if t else None for t in mapped] == [0, 2, None, None, 4, None]
    assert TrackInfo("x", True, ()).input_channel is None


def test_project_choice(appdata, cfg, tmp_path):
    from core.process_manager import reaper_project
    from ui.project_dialog import remember_project

    blank = reaper_project(cfg)               # défaut : projet vierge généré (aucun effet)
    assert blank.name == "session.rpp" and blank.is_file()
    assert "Microphone" in blank.read_text() and "<VST" not in blank.read_text()
    user = tmp_path / "MAIN.rpp"
    user.write_text("<REAPER_PROJECT\n>\n")
    assert reaper_project(cfg, str(user)) == user
    assert reaper_project(cfg, str(tmp_path / "absent.rpp")) == blank   # introuvable → vierge
    assert reaper_project(cfg, "") == blank

    remember_project(cfg, str(user), make_default=True, ask_on_start=True)
    assert cfg["reaper"]["project"] == str(user) and cfg["reaper"]["ask_project_on_start"]
    assert reaper_project(cfg) == user
    other = tmp_path / "Autre.rpp"
    other.write_text("x")
    remember_project(cfg, str(other), make_default=False, ask_on_start=False)
    assert cfg["reaper"]["recent_projects"] == [str(other), str(user)]
    assert cfg["reaper"]["project"] == str(user)
    remember_project(cfg, str(user), make_default=False, ask_on_start=False)   # case décochée
    assert cfg["reaper"]["project"] == "" and cfg["reaper"]["recent_projects"][0] == str(user)


def test_banana_edition(appdata, cfg):
    from core.process_manager import blank_project_path, reaper_project
    from core.voicemeeter_ctrl import edition, strip_channels, strip_map

    cfg["voicemeeter"]["kind"] = "Banana"
    assert edition(cfg).strip_count == 5 and not edition(cfg).gain_layers
    assert strip_map(cfg) == (0, 1, 3, 4, 2)
    assert strip_channels(cfg) == [0, 2, 6, 14, 4]      # 3 entrées physiques puis VAIO, AUX
    cfg["voicemeeter"]["strip_maps"]["banana"] = [0, 7, 1]   # 7 n'existe pas sur Banana
    assert strip_map(cfg) == (0, 1)
    cfg["voicemeeter"]["strip_maps"]["banana"] = [0, 1, 3, 4, 2]

    project = reaper_project(cfg)               # projet vierge propre à Banana
    assert project == blank_project_path(cfg) and project.name == "session-banana.rpp"
    text = project.read_text(encoding="utf-8")
    assert text.count("<TRACK") == 5 and "NAME Microphone" in text
    assert "REC 1 1030 1 2" in text and "HWOUT 14 0 1" in text and "<VST" not in text
    cfg["voicemeeter"]["kind"] = "inconnue"
    assert edition(cfg).kind == "potato" and blank_project_path(cfg).name == "session.rpp"


def test_executable_of_other_edition(tmp_path):
    from core.process_manager import find_executable, voicemeeter_spec

    folder = tmp_path / "VB" / "Voicemeeter"
    folder.mkdir(parents=True)
    potato, banana = folder / "voicemeeter8x64.exe", folder / "voicemeeterpro_x64.exe"
    potato.write_text("")
    banana.write_text("")
    # Chemin de Potato mémorisé, Banana choisie : on prend Banana dans le même dossier
    assert find_executable(voicemeeter_spec("banana"), str(potato)) == banana
    assert find_executable(voicemeeter_spec("potato"), str(potato)) == potato
    assert find_executable(voicemeeter_spec("potato"), str(folder)) == potato


def test_legacy_data_migration(appdata):
    from core import config

    legacy = appdata / "REAPER Mixer"
    legacy.mkdir()
    (legacy / "config.json").write_text(json.dumps({"reaper": {"path": "C:/R/reaper.exe"}}))
    (legacy / "session.rpp").write_text("<REAPER_PROJECT\n>\n")
    (legacy / "app.log").write_text("vieux journal")
    path = config.app_data_dir()
    assert path == appdata / "REKA"
    assert (path / "session.rpp").is_file() and not (path / "app.log").exists()
    assert config.load_config()["reaper"]["path"] == "C:/R/reaper.exe"


def test_startup_hook_replaces_legacy_block(tmp_path):
    from core.reaper_ctrl import STARTUP_BEGIN, install_startup_hook

    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    (scripts / "__startup.lua").write_text(
        "-- >>> REAPER Mixer (bloc gere automatiquement, ne pas modifier)\nancien\n"
        "-- <<< REAPER Mixer\n-- perso\n")
    content = install_startup_hook(tmp_path).read_text(encoding="utf-8")
    assert "REAPER Mixer" not in content and "ancien" not in content
    assert content.startswith(STARTUP_BEGIN) and content.rstrip().endswith("-- perso")


def test_startup_hook_keeps_user_script(tmp_path):
    from core.reaper_ctrl import STARTUP_BEGIN, install_startup_hook, remove_startup_block

    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    # Script perso en cp1252 (« é » = 0xE9) : ses octets doivent être conservés tels quels
    user_script = "-- perso été\nreaper.ShowConsoleMsg('hi')\n".encode("cp1252")
    (scripts / "__startup.lua").write_bytes(user_script)
    path = install_startup_hook(tmp_path)
    content = path.read_bytes()
    assert content.startswith(STARTUP_BEGIN.encode()) and content.count(STARTUP_BEGIN.encode()) == 1
    assert content.endswith(user_script)
    install_startup_hook(tmp_path)
    assert path.read_bytes() == content
    text = content.decode("utf-8", errors="surrogateescape")
    assert remove_startup_block(text).encode("utf-8", errors="surrogateescape") == user_script


def test_resource_path_detection(tmp_path, appdata):
    from core.reaper_ctrl import reaper_resource_path

    exe = tmp_path / "REAPER" / "reaper.exe"
    exe.parent.mkdir()
    exe.write_text("")
    assert reaper_resource_path(exe) == appdata / "REAPER"
    (exe.parent / "reaper.ini").write_text("")              # installation portable
    assert reaper_resource_path(exe) == exe.parent
    assert reaper_resource_path(exe, str(tmp_path / "x")) == tmp_path / "x"


# ---------------------------------------------------------------- plugins
def test_vst_sources_and_merge(tmp_path):
    from core import vst_scanner

    (tmp_path / "reaper-vstplugins64.ini").write_text(
        "[vstcache]\n"
        "FabFilter_Pro-Q_4.vst3=00D0,123{AB},Pro-Q 4 (FabFilter)\n"
        "reaeq.dll=0010,1919247729,ReaEQ (Cockos)\n"
        "WaveShell.dll=0010,0,<SHELL>\n"
        "Serum_x64.dll=0010,1,Serum (Xfer Records)!!!VSTi\n", encoding="utf-8")
    cache = vst_scanner.scan_reaper_cache(tmp_path)
    assert [(p.name, p.add_name) for p in cache][:2] == [
        ("Pro-Q 4 (FabFilter)", "VST3: Pro-Q 4 (FabFilter)"), ("ReaEQ (Cockos)", "VST: ReaEQ (Cockos)")]
    assert cache[2].instrument and cache[2].name == "Serum (Xfer Records)"

    vst3 = tmp_path / "VST3"
    (vst3 / "FabFilter" / "FabFilter Pro-C 3.vst3" / "Contents").mkdir(parents=True)
    (vst3 / "Other.vst3").write_text("")
    files = vst_scanner.scan_vst3_dirs([vst3])
    assert sorted(p.name for p in files) == ["FabFilter Pro-C 3", "Other"]

    reaper_list = vst_scanner.from_reaper_list([("VST3: Pro-Q 4 (FabFilter)", "x"), ("JS: Volume", "v")])
    merged = vst_scanner.merge(reaper_list, cache)
    assert [p.name for p in merged].count("Pro-Q 4 (FabFilter)") == 1
    assert {p.kind for p in reaper_list} == {"VST3", "JS"}


# --------------------------------------------------------- Voicemeeter
class FakeRemote:
    def __init__(self):
        self.params = {"Strip[0].Gain": -6.0, "Strip[0].Mute": 1.0, "Strip[0].A1": 1.0,
                       "Strip[0].GainLayer[5]": -12.0, "Bus[0].Gain": -3.0, "patch.insert[1]": 1.0}
        self.sets, self.logins, self.logouts, self.fail_login = [], 0, 0, 1
        self.dirty, self.version = True, "3.1.1.8"

    def login(self):
        self.logins += 1
        if self.fail_login:
            self.fail_login -= 1
            raise RuntimeError("Timeout logging into the api")

    def logout(self):
        self.logouts += 1

    def get(self, param, is_string=False):
        return self.params.get(param, 0.0)

    def set(self, param, value):
        self.sets.append((param, value))
        self.params[param] = value
        self.dirty = True

    @property
    def pdirty(self):
        dirty, self.dirty = self.dirty, False
        return dirty

    def get_level(self, kind, index):
        return {(1, 0): 1.0, (1, 10): 0.1, (3, 0): 0.25}.get((kind, index), 0.0)


def test_voicemeeter_controller(qapp, cfg, monkeypatch):
    from core.voicemeeter_ctrl import VoicemeeterController, input_channel

    assert [input_channel(s) for s in (0, 1, 2, 5, 6, 7)] == [0, 2, 4, 10, 18, 26]
    remote = FakeRemote()
    fake = types.ModuleType("voicemeeterlib")
    fake.api = lambda kind, **kwargs: remote
    monkeypatch.setitem(sys.modules, "voicemeeterlib", fake)

    vm = VoicemeeterController(cfg)
    params, levels = [], []
    vm.params_changed.connect(params.append)
    vm.levels_changed.connect(levels.append)
    with pytest.raises(RuntimeError):
        vm.login()
    assert remote.logouts == 1          # logout après un login raté
    vm.login()
    vm.login()                          # idempotent
    assert remote.logins == 2

    vm.start_polling()
    pump(qapp, 0.3)
    mic = params[-1].strips[0]
    assert mic.gain == -6.0 and mic.mute and mic.routes == (True, False, False, False)
    assert mic.sends[1] == -12.0 and params[-1].headphone_gain == -3.0
    assert levels[-1].strips[0][0] == 0.0 and levels[-1].strips[2][0] == pytest.approx(-20.0)

    vm.set_gain(2, -9.5)             # strip UI 2 = strip VM 5
    vm.set_route(2, 1, True)         # Mic Out = B1
    vm.set_send(2, 3, -4.0)          # Obs = A2 → GainLayer[1]
    vm.set_gain(1, 99)               # borné à +12
    vm.set_inserts({0, 2, 5})        # Microphone (VM 0), Appels (VM 5), Autres 2 (VM 7)
    pump(qapp, 0.2)
    sets = dict(remote.sets)
    assert sets["Strip[5].Gain"] == -9.5 and sets["Strip[5].B1"] == 1.0
    assert sets["Strip[5].GainLayer[1]"] == -4.0 and sets["Strip[1].Gain"] == 12.0
    assert remote.params["patch.insert[0]"] == 1.0 and remote.params["patch.insert[10]"] == 1.0
    assert remote.params["patch.insert[27]"] == 1.0
    assert "patch.insert[2]" not in remote.params        # Musique : pas touché
    assert params[-1].strips[2].gain == -9.5   # pas de retour en arrière du fader

    vm.set_inserts({2})              # le micro n'a plus de piste : rendu à l'origine
    pump(qapp, 0.2)
    assert remote.params["patch.insert[0]"] == 0.0 and remote.params["patch.insert[10]"] == 1.0

    vm.close(shutdown_app=True)
    assert remote.params["patch.insert[10]"] == 0.0 and remote.params["patch.insert[1]"] == 1.0
    assert ("Command.Shutdown", 1.0) in remote.sets and remote.logouts == 2


class CachingRemote(FakeRemote):
    """Comme voicemeeterlib : set() garde la valeur en cache, get() la renvoie
    (et l'efface) au lieu de demander à Voicemeeter."""

    def __init__(self):
        super().__init__()
        self.cache = {}
        self.fail_sets = 0

    def get(self, param, is_string=False):
        if param in self.cache:
            return self.cache.pop(param)
        return super().get(param, is_string)

    def set(self, param, value):
        if self.fail_sets:
            self.fail_sets -= 1
            raise RuntimeError("Voicemeeter ne répond pas")
        super().set(param, value)
        self.cache[param] = value


def _logged_in_vm(cfg, remote, monkeypatch):
    from core.voicemeeter_ctrl import VoicemeeterController

    remote.fail_login = 0
    fake = types.ModuleType("voicemeeterlib")
    fake.api = lambda kind, **kwargs: remote
    monkeypatch.setitem(sys.modules, "voicemeeterlib", fake)
    vm = VoicemeeterController(cfg)
    vm.login()
    return vm


def test_voicemeeter_external_changes_not_hidden_by_cache(qapp, cfg, monkeypatch):
    import core.voicemeeter_ctrl as vmc

    monkeypatch.setattr(vmc, "ECHO_GUARD_S", 0.05)
    remote = CachingRemote()
    vm = _logged_in_vm(cfg, remote, monkeypatch)
    params = []
    vm.params_changed.connect(params.append)
    vm.start_polling()
    vm.set_mute(0, False)                 # REKA démute le micro…
    pump(qapp, 0.3)
    assert not params[-1].strips[0].mute
    remote.params["Strip[0].Mute"] = 1.0  # … puis il est muté dans Voicemeeter (ou une macro)
    remote.dirty = True
    pump(qapp, 0.3)
    assert params[-1].strips[0].mute, "valeur en cache de voicemeeterlib relue à la place de la vraie"
    vm.close()


def test_voicemeeter_failed_writes_and_inserts_are_retried(qapp, cfg, monkeypatch):
    remote = CachingRemote()
    vm = _logged_in_vm(cfg, remote, monkeypatch)
    vm.set_gain(0, -3.0)
    vm.set_gain(2, -9.0)
    vm.set_inserts({0})
    remote.fail_sets = 2                  # Voicemeeter tombe pendant l'envoi
    with pytest.raises(RuntimeError):
        vm._flush()
    with pytest.raises(RuntimeError):
        vm._flush()
    assert not vm._inserts_saved          # insert non activé : pas noté comme fait
    vm._flush()                           # Voicemeeter revient : rien n'a été perdu
    assert remote.params["Strip[0].Gain"] == -3.0 and remote.params["Strip[5].Gain"] == -9.0
    assert remote.params["patch.insert[0]"] == 1.0 and remote.params["patch.insert[1]"] == 1.0
    # Voicemeeter relancé : ses inserts sont revenus à zéro, REKA les réactive
    remote.params["patch.insert[0]"] = remote.params["patch.insert[1]"] = 0.0
    vm._reassert_inserts()
    assert remote.params["patch.insert[0]"] == 1.0 and remote.params["patch.insert[1]"] == 1.0
    vm.close()
    assert remote.params["patch.insert[0]"] == 0.0    # rendu à son état d'origine
    assert remote.params["patch.insert[1]"] == 1.0    # il était déjà actif avant REKA


def test_reaper_retry_rewrites_commands(qapp, cfg, tmp_path):
    """« Réessayer » après la mort de REAPER : le thread de lecture du premier
    essai doit réécrire commands.txt pour le nouveau REAPER."""
    from pathlib import Path

    from core.reaper_ctrl import ReaperController

    cfg["reaper"]["resource_path"] = str(tmp_path / "res")
    ctrl = ReaperController(cfg)
    ctrl.prepare_launch(Path("reaper.exe"))
    ctrl.start()
    commands = ctrl.ipc_dir / "commands.txt"
    try:
        pump(qapp, 0.2)
        assert commands.is_file()
        ctrl.prepare_launch(Path("reaper.exe"))       # 2e essai : fichiers effacés
        assert not commands.is_file()
        pump(qapp, 0.3)
        assert commands.is_file() and "ensure_name" in commands.read_text(encoding="utf-8")
    finally:
        ctrl.close(save_project=False)


def test_unreadable_user_config_that_cannot_be_moved(cfg, appdata, monkeypatch):
    from core import config

    path = config.user_config_path()
    path.write_text("{ cassé", encoding="utf-8")

    def locked(*_args):
        raise PermissionError("fichier verrouillé")

    monkeypatch.setattr(config.os, "replace", locked)
    loaded = config.load_config()                  # pas d'exception : défauts
    assert loaded["voicemeeter"]["kind"] == "potato"


def test_voicemeeter_controller_banana(qapp, cfg, monkeypatch):
    from core.voicemeeter_ctrl import VoicemeeterController

    remote, kinds = FakeRemote(), []
    remote.fail_login = 0
    fake = types.ModuleType("voicemeeterlib")
    fake.api = lambda kind, **kwargs: kinds.append(kind) or remote
    monkeypatch.setitem(sys.modules, "voicemeeterlib", fake)
    cfg["voicemeeter"]["kind"] = "banana"
    cfg["voicemeeter"]["routing"][3]["bus"] = "A5"     # absent de Banana → A2 (par défaut)

    vm = VoicemeeterController(cfg)
    assert vm.strip_count == 5 and not vm.supports_sends
    params = []
    vm.params_changed.connect(params.append)
    vm.login()
    assert kinds == ["banana"]
    vm.start_polling()
    pump(qapp, 0.3)
    assert params[-1].strips[0].sends == (0.0, 0.0, 0.0, 0.0)   # pas de GainLayer lu

    vm.set_gain(2, -9.5)             # strip UI 2 = strip VM 3 (VAIO de Banana)
    vm.set_route(2, 3, True)         # Obs → A2
    vm.set_send(2, 3, -4.0)          # ignoré : Banana n'a pas de niveaux d'envoi
    vm.set_inserts({0, 2})
    pump(qapp, 0.2)
    sets = dict(remote.sets)
    assert sets["Strip[3].Gain"] == -9.5 and sets["Strip[3].A2"] == 1.0
    assert not any("GainLayer" in param for param, _ in remote.sets)
    assert remote.params["patch.insert[0]"] == 1.0
    assert remote.params["patch.insert[6]"] == 1.0 and remote.params["patch.insert[7]"] == 1.0
    vm.close()
    assert remote.params["patch.insert[6]"] == 0.0 and ("Command.Shutdown", 1.0) not in remote.sets


def test_intellipan_parameters(qapp, cfg, monkeypatch):
    from core.voicemeeter_ctrl import (VoicemeeterController, clamp_pad, pad_modes,
                                       pad_y_range)

    assert pad_modes(0) == ("color", "pan", "fx") and pad_modes(5) == ("pan",)
    assert pad_modes(3, physical_strips=3) == ("pan",)          # VAIO de Banana
    assert pad_y_range("color", True) == (0.0, 1.0) and pad_y_range("pan", False) == (-0.5, 0.5)
    assert clamp_pad("color", True, 2, -1) == (0.5, 0.0)

    remote = FakeRemote()
    remote.fail_login = 0
    remote.params.update({"Strip[0].Color_x": 0.25, "Strip[0].Color_y": 0.75,
                          "Strip[5].Pan_y": -0.2})
    fake = types.ModuleType("voicemeeterlib")
    fake.api = lambda kind, **kwargs: remote
    monkeypatch.setitem(sys.modules, "voicemeeterlib", fake)
    vm = VoicemeeterController(cfg)
    params = []
    vm.params_changed.connect(params.append)
    vm.login()
    vm.start_polling()
    pump(qapp, 0.3)
    mic, calls = params[-1].strips[0], params[-1].strips[2]
    assert mic.pad("color") == (0.25, 0.75) and mic.pad("fx") == (0.0, 0.0)
    assert calls.pads == (("pan", 0.0, -0.2),)                 # entrée virtuelle : Pan seul
    assert params[-1].to_state()["strips"][0]["pads"]["color"] == [0.25, 0.75]

    vm.set_pad(0, "color", -0.3, 0.4)        # Microphone = strip VM 0 (matérielle)
    vm.set_pad(2, "pan", 0.1, -0.45)         # Appels = strip VM 5 (virtuelle)
    vm.set_pad(2, "color", 0.2, 0.2)         # pas de Color sur une entrée virtuelle
    vm.set_pad(1, "fx", 9, 9)                # borné
    pump(qapp, 0.2)
    sets = dict(remote.sets)
    assert sets["Strip[0].Color_x"] == -0.3 and sets["Strip[0].Color_y"] == 0.4
    assert sets["Strip[5].Pan_x"] == 0.1 and sets["Strip[5].Pan_y"] == -0.45
    assert "Strip[5].Color_x" not in sets
    assert sets["Strip[1].fx_x"] == 0.5 and sets["Strip[1].fx_y"] == 1.0

    vm.apply_state({"strips": [{"pads": {"pan": [0.2, 0.3], "bidon": [1, 1]}}]})
    pump(qapp, 0.2)
    assert remote.params["Strip[0].Pan_x"] == 0.2 and remote.params["Strip[0].Pan_y"] == 0.3
    vm.close()


def test_color_themes(qapp):
    from PySide6.QtGui import QColor

    from ui import styles

    try:
        assert styles.set_theme("violet") == "violet"
        assert styles.GREEN_NEON == styles.THEMES["violet"][1]
        violet_hue = QColor(styles.THEMES["violet"][1]).hslHueF()
        assert abs(QColor(styles.GREEN_DIM).hslHueF() - violet_hue) < 0.05   # teintes tournées
        assert styles.RED_MUTE == "#ff2d2d" and styles.YELLOW_VU == "#ffcc00"
        assert styles.THEMES["violet"][1] in styles.build_stylesheet()
        for theme in [*styles.THEMES, "#0000ff", "#123456", "#3a0050"]:   # lisibilité
            styles.set_theme(theme)
            accent, hover = QColor(styles.GREEN_NEON), QColor(styles.GREEN_BRIGHT)
            ink = QColor(styles.ON_ACCENT_DARK)
            assert styles.contrast(accent, QColor(styles.BG_COLUMN)) >= styles.MIN_ACCENT_CONTRAST
            assert styles.contrast(accent, ink) >= 4.5 and styles.contrast(hover, ink) >= 4.5, theme
        assert styles.set_theme("#123456") == "#123456"            # personnalisé, éclairci
        assert styles.set_theme("n'importe quoi") == styles.DEFAULT_THEME
        assert styles.GREEN_NEON == "#3b82f6" and styles.BG_WINDOW == "#0f1115"
        assert styles.BG_COLUMN == "#161920" and styles.BORDER_COLUMN == "#2a2f3d"
        assert styles.set_theme("neon") == "neon" and styles.GREEN_NEON == "#00ff88"
    finally:
        styles.set_theme(styles.DEFAULT_THEME)


def test_blank_project_detection_and_legacy_strip_map(appdata, cfg, tmp_path):
    from core import config
    from core.process_manager import blank_project_path, is_blank_project
    from core.voicemeeter_ctrl import strip_map

    assert is_blank_project(blank_project_path(cfg))
    assert is_blank_project(config.app_data_dir() / "session-banana.rpp")
    assert not is_blank_project(tmp_path / "session.rpp")           # projet perso ailleurs
    assert not is_blank_project(config.app_data_dir() / "session_live.rpp")
    cfg["voicemeeter"]["strip_map"] = [0, 1, 2, 3, 4, 5]             # clé de la 0.2
    assert strip_map(cfg) == (0, 1, 2, 3, 4, 5)
    cfg["voicemeeter"]["strip_maps"]["potato"] = [7, 6]              # la nouvelle clé gagne
    assert strip_map(cfg) == (7, 6)


def test_other_edition_running(qapp, cfg, monkeypatch):
    from core import process_manager
    from core.process_manager import OtherEditionRunningError, ProcessManager
    from core.voicemeeter_ctrl import VoicemeeterController

    monkeypatch.setattr(process_manager, "running_voicemeeter_kind", lambda: "banana")
    with pytest.raises(OtherEditionRunningError) as error:
        ProcessManager(cfg).start_voicemeeter()      # Potato voulu, Banana ouvert
    assert error.value.running_kind == "banana" and "Banana" in error.value.hint

    remote = FakeRemote()
    remote.fail_login = 0
    remote.type = "banana"                           # l'API répond : c'est Banana qui tourne
    fake = types.ModuleType("voicemeeterlib")
    fake.api = lambda kind, **kwargs: remote
    monkeypatch.setitem(sys.modules, "voicemeeterlib", fake)
    vm = VoicemeeterController(cfg)
    with pytest.raises(OtherEditionRunningError):
        vm.login()
    assert remote.logouts == 1
    remote.type = "potato"
    vm.login()
    vm.close()


def test_app_volume_device_matching_and_demo():
    from core.app_volume import DemoAppVolumes, NullAppVolumes, match_device

    names = {
        "Voicemeeter Input (VB-Audio Voicemeeter VAIO)": 0,
        "Voicemeeter AUX Input (VB-Audio Voicemeeter AUX VAIO)": 1,
        "Voicemeeter VAIO3 Input (VB-Audio Voicemeeter VAIO3)": 2,
        "VoiceMeeter Aux Input (VB-Audio VoiceMeeter AUX VAIO)": 1,       # ancienne graphie
        "Haut-parleurs (Realtek(R) Audio)": None,
        "CABLE Input (VB-Audio Virtual Cable)": None,
    }
    for name, expected in names.items():
        assert [i for i in range(3) if match_device(name, i)] == ([] if expected is None else [expected])
    assert match_device("Mon câble perso", 1, ["", "câble perso", ""])     # voicemeeter.app_devices
    assert not match_device("Voicemeeter Input (VB-Audio Voicemeeter VAIO)", 0, ["autre"])

    demo = DemoAppVolumes()
    discord = demo.apps(0)[0]
    assert discord.name == "Discord" and 0 < discord.peak <= 1
    demo.set_volume(0, "discord.exe", 0.3)
    demo.set_mute(0, "discord.exe", True)
    discord = demo.apps(0)[0]
    assert discord.volume == 0.3 and discord.muted and discord.peak == 0.0
    assert demo.apps(2) == [] and demo.device_name(2)
    assert not NullAppVolumes().available and NullAppVolumes().apps(0) == []


# ------------------------------------------------ noms Windows des périphériques
def _vm_endpoint(ident, capture, name, original=""):
    from core.device_names import Endpoint

    return Endpoint(ident, capture, name, original, "VB-Audio Voicemeeter VAIO")


def test_device_names_one_driver_for_all_devices():
    """Voicemeeter récent : toutes ses entrées/sorties ont le même pilote."""
    from core.device_names import plan

    cfg = {"voicemeeter": {"kind": "potato"}}
    endpoints = [_vm_endpoint("r0", False, "Voicemeeter Input"),
                 _vm_endpoint("r1", False, "Voicemeeter AUX Input"),
                 _vm_endpoint("r2", False, "Voicemeeter VAIO3 Input"),
                 _vm_endpoint("r3", False, "Voicemeeter In 1"),
                 _vm_endpoint("c0", True, "Voicemeeter Out B1"),
                 _vm_endpoint("c1", True, "Voicemeeter Out B2"),
                 _vm_endpoint("c2", True, "Voicemeeter Out B3"),
                 _vm_endpoint("c3", True, "Voicemeeter Out A1")]
    changes, lost = plan(cfg, endpoints, {})
    assert [(e.id, name) for e, name, _key in changes] == [
        ("r0", "REKA insert"), ("r1", "REKA aux"), ("r2", "REKA VAIO3"),
        ("c0", "REKA B1"), ("c1", "REKA B2"), ("c2", "REKA B3")]
    assert lost == []

    # Déjà renommés : reconnus par ce que REKA a noté, rien à refaire
    renamed = [_vm_endpoint(e.id, e.capture, n) for e, n, _k in changes]
    memory = {e.id: k for e, _n, k in changes}
    assert plan(cfg, renamed, memory) == ([], [])

    # Option désactivée : noms d'origine
    cfg["voicemeeter"]["rename_devices"] = False
    back = [(e.id, n) for e, n, _k in plan(cfg, renamed, memory)[0]]
    assert ("r1", "Voicemeeter AUX Input") in back and ("c0", "Voicemeeter Out B1") in back


def test_device_names_repairs_wrong_names_from_pin_names():
    """Version précédente : toutes les entrées VAIO renommées « REKA jeux »."""
    from core.device_names import plan

    cfg = {"voicemeeter": {"kind": "potato", "device_names": {"B2": "Mon Discord"}}}
    endpoints = [_vm_endpoint("r0", False, "REKA jeux", "Voicemeeter Input"),
                 _vm_endpoint("r1", False, "REKA jeux", "Voicemeeter AUX Input"),
                 _vm_endpoint("r3", False, "REKA jeux", "Voicemeeter In 1"),
                 _vm_endpoint("c1", True, "REKA Micro", "Voicemeeter Out B2"),
                 _vm_endpoint("c3", True, "REKA Micro", "Voicemeeter Out A1")]
    changes, lost = plan(cfg, endpoints, {})
    assert [(e.id, n) for e, n, _k in changes] == [
        ("r0", "REKA insert"), ("r1", "REKA aux"), ("r3", "Voicemeeter In 1"),
        ("c1", "Mon Discord"), ("c3", "Voicemeeter Out A1")]
    assert lost == []


def test_device_names_never_guesses():
    from core.device_names import plan

    cfg = {"voicemeeter": {"kind": "potato"}}
    # Nom d'origine illisible : un nom REKA inconnu est signalé, pas deviné
    changes, lost = plan(cfg, [_vm_endpoint("r0", False, "REKA jeux")], {})
    assert changes == [] and [e.id for e in lost] == ["r0"]
    # Deux périphériques notés comme le même : aucun n'est renommé
    endpoints = [_vm_endpoint("c0", True, "Voicemeeter Out B1"), _vm_endpoint("c9", True, "Micro")]
    assert plan(cfg, endpoints, {"c9": "B1"})[0] == []
    # Nom de broche commun à plusieurs périphériques : ignoré
    endpoints = [_vm_endpoint("c0", True, "Voicemeeter Out B1", "Voicemeeter VAIO"),
                 _vm_endpoint("c1", True, "Voicemeeter Out B2", "Voicemeeter VAIO")]
    assert [n for _e, n, _k in plan(cfg, endpoints, {})[0]] == ["REKA B1", "REKA B2"]


def test_apo_finds_renamed_virtual_input():
    from core.apo_ctrl import Endpoint, resolve_device

    endpoints = [Endpoint("REKA insert (VB-Audio Voicemeeter VAIO)", "{aaa}", False),
                 Endpoint("REKA B1 (VB-Audio Voicemeeter VAIO)", "{bbb}", True),
                 Endpoint("REKA aux (VB-Audio Voicemeeter VAIO)", "{ccc}", False),
                 Endpoint("Voicemeeter In 1 (VB-Audio Voicemeeter VAIO)", "{ddd}", False)]
    assert resolve_device("Voicemeeter Input", endpoints)[0] == "{aaa}"
    assert resolve_device("Voicemeeter AUX Input", endpoints)[0] == "{ccc}"
