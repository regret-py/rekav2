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
    assert cfg["voicemeeter"]["strip_map"] == [0, 1, 5, 6, 2, 7]

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
    from ui.fader import db_to_ratio, ratio_to_db

    assert db_to_ratio(0) == 0 and db_to_ratio(-60) == 1 and db_to_ratio(12) == 0
    assert db_to_ratio(-6) == pytest.approx(0.152) and db_to_ratio(-48) == pytest.approx(0.729)
    previous = -1.0
    for tenth in range(0, 601):
        db = -tenth / 10
        ratio = db_to_ratio(db)
        assert ratio > previous
        previous = ratio
        assert ratio_to_db(ratio) == pytest.approx(db, abs=1e-6)


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


def test_startup_hook_keeps_user_script(tmp_path):
    from core.reaper_ctrl import STARTUP_BEGIN, install_startup_hook, remove_startup_block

    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    (scripts / "__startup.lua").write_text("-- perso\nreaper.ShowConsoleMsg('hi')\n")
    path = install_startup_hook(tmp_path)
    content = path.read_text()
    assert content.startswith(STARTUP_BEGIN) and content.count(STARTUP_BEGIN) == 1
    assert content.rstrip().endswith("reaper.ShowConsoleMsg('hi')")
    install_startup_hook(tmp_path)
    assert path.read_text() == content
    assert remove_startup_block(content).strip() == "-- perso\nreaper.ShowConsoleMsg('hi')"


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
    vm.set_inserts(True)
    pump(qapp, 0.2)
    sets = dict(remote.sets)
    assert sets["Strip[5].Gain"] == -9.5 and sets["Strip[5].B1"] == 1.0
    assert sets["Strip[5].GainLayer[1]"] == -4.0 and sets["Strip[1].Gain"] == 12.0
    assert remote.params["patch.insert[10]"] == 1.0 and remote.params["patch.insert[27]"] == 1.0
    assert params[-1].strips[2].gain == -9.5   # pas de retour en arrière du fader

    vm.close(shutdown_app=True)
    assert remote.params["patch.insert[0]"] == 0.0 and remote.params["patch.insert[1]"] == 1.0
    assert ("Command.Shutdown", 1.0) in remote.sets and remote.logouts == 2
