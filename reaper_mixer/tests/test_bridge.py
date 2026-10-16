"""Bout en bout : ReaperController (Python) ↔ pont Lua, exécuté dans un faux
REAPER (tests/fake_reaper.lua) avec Lua 5.4, la version de Lua de REAPER."""

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from conftest import pump

LUA = shutil.which("lua5.4") or shutil.which("lua54")
pytestmark = pytest.mark.skipif(LUA is None, reason="Lua 5.4 absent")
HERE = Path(__file__).parent


def test_bridge_end_to_end(qapp, cfg, tmp_path):
    from core.reaper_ctrl import ReaperController

    cfg["reaper"]["resource_path"] = str(tmp_path / "reaper_res")
    ctrl = ReaperController(cfg)
    events = {"tracks": [], "fail": [], "fx": [], "device": []}
    ctrl.tracks_changed.connect(events["tracks"].append)
    ctrl.command_failed.connect(events["fail"].append)
    ctrl.installed_fx_ready.connect(events["fx"].append)
    ctrl.device_changed.connect(events["device"].append)

    env = ctrl.prepare_launch(Path("reaper.exe"))
    startup = tmp_path / "reaper_res" / "Scripts" / "__startup.lua"
    stop = tmp_path / "stop"
    proc = subprocess.Popen([LUA, str(HERE / "fake_reaper.lua"), str(startup), str(stop)],
                            env={**os.environ, **env},
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def wait_for(condition, timeout=6.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            pump(qapp, 0.05)
            if condition():
                return True
        return False

    try:
        ctrl.start()
        assert ctrl.ping(6.0), "pas de battement de cœur du pont"
        assert wait_for(lambda: events["tracks"] and [t.name for t in events["tracks"][-1]] ==
                        ["Microphone", "Musique", "Appels", "Bureau", "Autres 1", "Autres 2"])
        assert wait_for(lambda: events["device"]) and events["device"][-1].is_voicemeeter_insert

        ctrl.add_fx(0, "VST3: Pro-Q 4 (FabFilter)")
        ctrl.add_fx(0, "VST: ReaLimit (Cockos)")
        ctrl.add_fx(1, "VST3: Plugin\tqui n'existe pas")
        assert wait_for(lambda: [f.display_name for f in events["tracks"][-1][0].fx] ==
                        ["Pro-Q 4 (FabFilter)", "ReaLimit (Cockos)"])
        assert wait_for(lambda: events["fail"]) and "n'existe pas" in events["fail"][0]

        ctrl.set_fx_enabled(0, 1, False)
        ctrl.toggle_fx_ui(0, 0)
        ctrl.set_chain_enabled(0, False)
        ctrl.remove_fx(0, 1)
        ctrl.rename_track(2, "Discord")
        assert wait_for(lambda: (lambda t: not t[0].fx_enabled and len(t[0].fx) == 1
                                 and t[0].fx[0].open and t[2].name == "Discord")(events["tracks"][-1]))

        ctrl.request_installed_fx()
        assert wait_for(lambda: events["fx"])
        assert events["fx"][0][0] == ("VST3: Pro-Q 4 (FabFilter)", "FabFilter Pro-Q 4.vst3")

        ctrl.close(save_project=True)
    finally:
        stop.write_text("stop")
        proc.wait(10)

    summary = (Path(env["REAPER_MIXER_IPC"]) / "fake_summary.txt").read_text()
    # Piste 1 : entrée stéréo 1/2 (1024), armée, monitoring seul, pas de master,
    # sortie matérielle 1/2 ; piste 3 (strip VM 5) : canaux 11/12 (1024 + 10)
    for expected in ("t1in=1024", "t1arm=1", "t1mode=2", "t1mon=1", "t1main=0", "t1dst=0",
                     "t3in=1034", "tracks=6"):
        assert expected in summary, summary
    assert "saved=0" not in summary
    assert (Path(env["REAPER_MIXER_IPC"]) / "state.txt").read_text().startswith("CLOSED")
