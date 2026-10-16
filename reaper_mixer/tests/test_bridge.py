"""Bout en bout : ReaperController (Python) ↔ pont Lua, exécuté dans un faux
REAPER (tests/fake_reaper.lua) avec Lua 5.4, la version de Lua de REAPER.

Le faux REAPER démarre sur un projet « d'utilisateur » : pistes sans nom,
piste 1 en mono sur l'entrée 1 (canal 0), pistes 2 et 3 sur 3/4 et 5/6."""

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
    events = {"tracks": [], "fail": [], "fx": [], "device": [], "project": []}
    ctrl.tracks_changed.connect(events["tracks"].append)
    ctrl.command_failed.connect(events["fail"].append)
    ctrl.installed_fx_ready.connect(events["fx"].append)
    ctrl.device_changed.connect(events["device"].append)
    ctrl.project_changed.connect(events["project"].append)

    env = ctrl.prepare_launch(Path("reaper.exe"))
    startup = tmp_path / "reaper_res" / "Scripts" / "__startup.lua"
    stop = tmp_path / "stop"
    summary_path = Path(env["REKA_IPC"]) / "fake_summary.txt"
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

    def names(tracks):
        return [t.name if t is not None else None for t in tracks]

    def summary():
        return summary_path.read_text() if summary_path.is_file() else ""

    try:
        ctrl.start()
        assert ctrl.ping(6.0), "pas de battement de cœur du pont"
        # Strips : Microphone (canal 0), Musique (2), Appels (10), Bureau (18),
        # Autres 1 (4), Autres 2 (26) → pistes du projet qui écoutent ces canaux
        assert wait_for(lambda: events["tracks"] and names(events["tracks"][-1]) ==
                        ["Microphone", "Musique", None, None, "Autres 1", None]), events["tracks"][-1:]
        assert events["project"][-1] == "C:/Projets/MAIN.rpp"
        assert wait_for(lambda: events["device"]) and events["device"][-1].is_voicemeeter_insert
        mic = events["tracks"][-1][0]
        assert mic.input_channel == 0 and mic.processes_live

        # Effets sur la piste existante du micro (câblage d'origine intact)
        ctrl.add_fx(0, "VST3: Pro-Q 4 (FabFilter)")
        ctrl.add_fx(0, "VST: ReaLimit (Cockos)")
        assert wait_for(lambda: [f.display_name for f in events["tracks"][-1][0].fx] ==
                        ["Pro-Q 4 (FabFilter)", "ReaLimit (Cockos)"])
        # Strip sans piste (Appels, canal 10) : la piste est créée à l'ajout
        ctrl.add_fx(2, "JS: Volume Adjustment")
        assert wait_for(lambda: events["tracks"][-1][2] is not None
                        and events["tracks"][-1][2].name == "Appels"
                        and events["tracks"][-1][2].fx[0].display_name == "Volume Adjustment")
        # Plugin inconnu → erreur remontée
        ctrl.add_fx(1, "VST3: Plugin\tqui n'existe pas")
        assert wait_for(lambda: events["fail"]) and "n'existe pas" in events["fail"][0]
        # Création interdite : erreur explicite, pas de piste
        cfg["reaper"]["manage_routing"] = False
        ctrl.add_fx(3, "JS: Volume Adjustment")
        assert wait_for(lambda: len(events["fail"]) >= 2) and "aucune piste" in events["fail"][1]

        ctrl.set_fx_enabled(0, 1, False)
        ctrl.toggle_fx_ui(0, 0)
        ctrl.set_chain_enabled(0, False)
        ctrl.remove_fx(0, 1)
        ctrl.rename_track(1, "Spotify")
        assert wait_for(lambda: (lambda t: not t[0].fx_enabled and len(t[0].fx) == 1
                                 and t[0].fx[0].open and t[1].name == "Spotify")(events["tracks"][-1]))

        ctrl.request_installed_fx()
        assert wait_for(lambda: events["fx"])
        assert events["fx"][0][0] == ("VST3: Pro-Q 4 (FabFilter)", "FabFilter Pro-Q 4.vst3")

        ctrl.open_preferences()
        assert wait_for(lambda: "action=40016" in summary())
        # Le câblage d'origine du projet n'a pas bougé (piste 1 : mono, canal 0)
        text = summary()
        assert "t1=Microphone|in0|arm1|mon1|mode0|main1|dst0" in text, text
        assert "t4=Appels|in1034|arm1|mon1|mode2|main0|dst10" in text, text

        # Ouverture d'un autre projet à chaud
        ctrl.open_project("D:/Autre/Voix.rpp")
        assert wait_for(lambda: events["project"][-1] == "D:/Autre/Voix.rpp")
        assert wait_for(lambda: names(events["tracks"][-1]) ==
                        ["Microphone", None, None, None, None, None])
        assert "opened=D:/Autre/Voix.rpp" in summary()

        ctrl.close(save_project=True)
    finally:
        stop.write_text("stop")
        proc.wait(10)

    assert "saved=0" not in summary()
    assert (Path(env["REKA_IPC"]) / "state.txt").read_text().startswith("CLOSED")
