"""
Moteurs d'effets : sélection, Equalizer APO (fichier de configuration),
FL Studio (insert par strip), menu de sélection et réglages de latence.
"""

import struct
from pathlib import Path

import pytest

from conftest import pump


def _dll(path: Path, machine: int) -> Path:
    """Fausse DLL avec un en-tête PE minimal (architecture `machine`)."""
    data = bytearray(256)
    data[0:2] = b"MZ"
    data[0x3C:0x40] = struct.pack("<I", 0x80)
    data[0x80:0x84] = b"PE\0\0"
    data[0x84:0x86] = struct.pack("<H", machine)
    path.write_bytes(bytes(data))
    return path


# ------------------------------------------------------------ sélection
def test_host_kind_and_factory(qapp, cfg):
    from core.apo_ctrl import ApoController
    from core.effects import NoEffects, create_host, host_kind
    from core.flstudio_ctrl import FlStudioController
    from core.reaper_ctrl import ReaperController

    assert host_kind(cfg) == "reaper" and isinstance(create_host(cfg), ReaperController)
    for kind, cls in (("flstudio", FlStudioController), ("apo", ApoController), ("none", NoEffects)):
        cfg["effects"]["host"] = kind
        host = create_host(cfg)
        assert isinstance(host, cls) and host.kind == kind
    cfg["effects"]["host"] = "inconnu"
    assert host_kind(cfg) == "reaper"


# ------------------------------------------------------------ Equalizer APO
def test_apo_config_written_and_settings_kept(qapp, cfg, tmp_path):
    from core.apo_ctrl import INCLUDE_LINE, ApoController, Endpoint

    config_dir = tmp_path / "EqualizerAPO" / "config"
    config_dir.mkdir(parents=True)
    # config.txt de l'utilisateur, avec BOM et fins de ligne Windows : intact sauf l'Include
    original = "﻿Preamp: -3 dB\r\nGraphicEQ: 25 0; 40 0\r\n"
    (config_dir / "config.txt").write_bytes(original.encode("utf-8"))
    cfg["effects"]["host"] = "apo"
    cfg["apo"]["config_dir"] = str(config_dir)
    endpoints = [Endpoint("Microphone (Shure MV7)", "{11111111-2222-3333-4444-555555555555}", True),
                 Endpoint("Voicemeeter Input (VB-Audio Voicemeeter VAIO)",
                          "{aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee}", False)]
    host = ApoController(cfg, endpoints=endpoints)
    tracks, errors = [], []
    host.tracks_changed.connect(tracks.append)
    host.command_failed.connect(errors.append)
    host.start()
    assert host.is_connected and host.status(True)[0]

    config_txt = (config_dir / "config.txt").read_bytes()
    assert config_txt.startswith(b"\xef\xbb\xbfPreamp: -3 dB\r\nGraphicEQ")
    assert config_txt.decode("utf-8-sig").count(INCLUDE_LINE) == 1
    host.start()                                   # pas d'Include en double
    assert (config_dir / "config.txt").read_bytes().decode("utf-8-sig").count(INCLUDE_LINE) == 1

    # Strip 2 (Appels = Voicemeeter Input) : périphérique par défaut, résolu en GUID ;
    # entrées matérielles (Microphone, Musique) : à choisir
    assert host.strip_device(2) == "Voicemeeter Input" and host.strip_device(0) == ""
    eq = _dll(tmp_path / "ReaEQ.dll", 0x8664)
    comp = _dll(tmp_path / "Comp x64.dll", 0x8664)
    host.add_fx(2, str(eq))
    host.add_fx(2, str(comp))
    text = (config_dir / "reka.txt").read_text(encoding="utf-8")
    assert "Device: {aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee}" in text
    assert f'VSTPlugin: Library "{eq}"' in text and f'VSTPlugin: Library "{comp}"' in text
    assert [f.display_name for f in tracks[-1][2].fx] == ["ReaEQ", "Comp x64"]
    assert cfg["apo"]["strips"][2]["plugins"][0]["path"] == str(eq)

    # Refus explicites : VST3, plugin 32 bits
    host.add_fx(2, str(tmp_path / "Pro-Q 4.vst3"))
    host.add_fx(2, str(_dll(tmp_path / "Old.dll", 0x14C)))
    assert len(errors) == 2 and "VST2" in errors[0] and "32 bits" in errors[1]

    # Réglages faits dans l'éditeur d'APO (ChunkData) : conservés à la réécriture
    edited = text.replace(f'VSTPlugin: Library "{comp}"', f'VSTPlugin: Library "{comp}" ChunkData "QUJD"')
    (config_dir / "reka.txt").write_text(edited, encoding="utf-8")
    host.set_fx_enabled(2, 0, False)               # bypass d'un plugin : ligne en commentaire
    text = (config_dir / "reka.txt").read_text(encoding="utf-8")
    assert f'VSTPlugin: Library "{comp}" ChunkData "QUJD"' in text
    assert f'# [REKA:off] VSTPlugin: Library "{eq}"' in text
    host.set_chain_enabled(2, False)               # bypass de la chaîne : tout en commentaire
    text = (config_dir / "reka.txt").read_text(encoding="utf-8")
    assert f'# [REKA:off] VSTPlugin: Library "{comp}" ChunkData "QUJD"' in text
    host.set_chain_enabled(2, True)
    host.remove_fx(2, 0)
    text = (config_dir / "reka.txt").read_text(encoding="utf-8")
    assert str(eq) not in text and f'VSTPlugin: Library "{comp}" ChunkData "QUJD"' in text

    # Micro : périphérique choisi par son nom, écrit avec son GUID
    host.set_device(0, "Microphone (Shure MV7)")
    host.add_fx(0, str(eq))
    text = (config_dir / "reka.txt").read_text(encoding="utf-8")
    assert "Device: {11111111-2222-3333-4444-555555555555}" in text
    assert text.rstrip().endswith("Device: all")

    # Relu depuis la config au prochain démarrage
    again = ApoController(cfg, endpoints=endpoints)
    assert [f.display_name for f in again.strip_tracks[2].fx] == ["Comp x64"]
    assert again.strip_device(0) == "Microphone (Shure MV7)"


def test_apo_missing_reports_clearly(qapp, cfg, tmp_path):
    from core.apo_ctrl import ApoController

    cfg["apo"]["config_dir"] = str(tmp_path / "absent")
    host = ApoController(cfg, endpoints=[])
    errors = []
    host.command_failed.connect(errors.append)
    if host.config_dir is not None:      # Equalizer APO réellement installé sur ce PC
        pytest.skip("Equalizer APO installé")
    assert not host.is_connected
    ok, text, _tip = host.status(False)
    assert not ok and "introuvable" in text
    host.set_chain_enabled(1, False)
    assert errors and "introuvable" in errors[0]


# ------------------------------------------------------------ FL Studio
class FakeProcesses:
    def __init__(self) -> None:
        self.running = True
        self.fronted = []

    def is_running(self, key):
        return self.running

    def bring_to_front(self, key):
        self.fronted.append(key)
        return self.running


def test_flstudio_inserts_per_strip(qapp, cfg):
    from core.flstudio_ctrl import FlStudioController, insert_pair

    cfg["effects"]["host"] = "flstudio"
    processes = FakeProcesses()
    host = FlStudioController(cfg, processes)
    connection, tracks = [], []
    host.connection_changed.connect(connection.append)
    host.tracks_changed.connect(tracks.append)
    host.start()
    assert host.is_connected and connection == [True]
    # Aucune strip à l'insert tant que l'utilisateur ne l'a pas demandé (sinon : muette)
    assert host.insert_strips(host.strip_tracks, True) == set()
    host.set_chain_enabled(0, True)
    assert host.insert_strips(tracks[-1], True) == {0}
    assert host.insert_strips(tracks[-1], False) == set()
    assert cfg["flstudio"]["inserts"][0] is True
    host.add_fx(0, "")
    assert processes.fronted == ["flstudio"]
    assert insert_pair(0) == "1-2" and "1-2" in host.wiring_help()
    processes.running = False
    host._check()
    assert not host.is_connected and connection == [True, False]
    host.close()


def test_flstudio_executable_found_in_versioned_folder(tmp_path):
    import os
    import time
    from dataclasses import replace

    from core import process_manager

    for version in ("FL Studio 21", "FL Studio 2024"):
        folder = tmp_path / "Image-Line" / version
        folder.mkdir(parents=True)
        (folder / "FL64.exe").write_text("")
    newest = tmp_path / "Image-Line" / "FL Studio 2024"
    os.utime(newest, (time.time() + 60, time.time() + 60))
    # Un dossier par version : le plus récent d'abord (« * » dans le chemin standard)
    spec = replace(process_manager.FLSTUDIO_SPEC,
                   install_dirs=(str(tmp_path / "Image-Line" / "FL Studio*"),))
    found = process_manager.find_executable(spec)
    assert found == newest / "FL64.exe"


# ------------------------------------------------------------ interface
def test_console_with_each_host(qapp, cfg, tmp_path):
    import main
    from core.demo import DemoProcesses, DemoVoicemeeter
    from ui.main_window import MainWindow

    for kind in ("flstudio", "apo", "none"):
        session = dict(cfg)
        session["effects"] = {"host": kind}
        session["apo"] = {"config_dir": str(tmp_path / "apo"), "strips": []}
        (tmp_path / "apo").mkdir(exist_ok=True)
        processes = DemoProcesses()
        host = main.demo_host(session, kind, processes)
        if kind == "apo":
            host._endpoints = []
        window = MainWindow(cfg=session, vm=DemoVoicemeeter(session), reaper=host,
                            processes=processes, persist=False)
        host.start()
        pump(qapp, 0.1)
        strip = window.strips[2]          # Appels : entrée virtuelle
        if kind == "none":
            assert not strip.fx_list._enabled and strip.fx_list._hint == "Pas d'effets"
            assert window.master.project.isHidden()
        elif kind == "flstudio":
            assert strip.fx_list._add_label == "+ Ouvrir FL Studio"
            assert window.master.badge._text == "FL Studio"
        else:
            assert window.strips[0].fx_list._hint == "Choisis le périphérique"
            assert strip.fx_list._hint == "" and strip.fx_list._enabled
        window.close()


def test_latency_shown_per_strip_and_plugin(qapp, mixer):
    window, vm, reaper = mixer
    reaper.set_chain_enabled(0, True)
    pump(qapp, 0.05)
    strip = window.strips[0]
    assert not strip.latency_label.isHidden() and strip.latency_label.text() == "+2.5 ms"
    pills = strip.fx_list._pills
    assert pills[-1].name == "ReaLimit (Cockos)" and "2.5 ms" in pills[-1].toolTip()
    reaper.add_fx(0, "VST3: Pro-L 2 (FabFilter)")       # 5 ms d'anticipation : point ambre
    pump(qapp, 0.05)
    assert strip.fx_list._pills[-1].slow and strip.latency_label.text() == "+7.5 ms"
    assert strip.latency_label.property("slow") is True


def test_buffer_preset_from_settings(qapp, mixer):
    from ui.settings_dialog import SettingsDialog

    window, vm, reaper = mixer
    dialog = SettingsDialog(window._cfg, None, reaper, vm, window, persist=False)
    assert "10.7 ms" in dialog.latency_info.text()
    dialog.buffer_choice.setCurrentIndex(dialog.buffer_choice.findData(128))
    dialog.buffer_apply.click()
    assert vm.buffering == 128 and window._cfg["voicemeeter"]["buffer"] == 128
    # Choix du moteur d'effets : pris en compte au redémarrage
    dialog.host_choice.setCurrentIndex(dialog.host_choice.findData("apo"))
    assert window._cfg["effects"]["host"] == "apo"
    assert not dialog.restart_button.isHidden() and "Equalizer APO" in dialog.edition_hint.text()
    dialog.host_choice.setCurrentIndex(dialog.host_choice.findData("reaper"))
    assert dialog.restart_button.isHidden()
    dialog.done(0)


def test_setup_dialog_saves_choices(qapp, cfg):
    from ui.setup_dialog import SetupDialog

    cfg["setup_done"] = False
    dialog = SetupDialog(cfg, first_run=True, editions_found={"potato": False, "banana": True},
                         hosts_found={"reaper": False, "flstudio": True, "apo": True, "none": True})
    # Potato absent, Banana installé : Banana proposé d'office
    assert dialog.edition_radios["banana"].isChecked()
    dialog.host_radios["apo"].setChecked(True)
    dialog.accept()
    assert cfg["voicemeeter"]["kind"] == "banana" and cfg["effects"]["host"] == "apo"
    assert cfg["setup_done"] is True
