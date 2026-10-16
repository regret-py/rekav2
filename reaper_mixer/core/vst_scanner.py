"""
Liste des plugins proposés dans le dialogue « Ajouter un effet ».

Trois sources, de la plus fiable à la moins fiable :
  1. la liste envoyée par REAPER lui-même (pont Lua, EnumInstalledFX) :
     exactement ce que REAPER sait charger, JS et plugins Cockos compris ;
  2. le cache de scan de REAPER (reaper-vstplugins64.ini) ;
  3. un scan des dossiers VST3 standards (C:\\Program Files\\Common Files\\VST3
     + dossiers ajoutés dans config.json → vst.extra_dirs), pour les plugins
     que REAPER n'a pas encore scannés.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_TYPE_PREFIX = re.compile(r"^(?P<kind>[A-Za-z0-9]+?)(?P<instrument>i?)\s*:\s*(?P<name>.+)$")
_VENDOR = re.compile(r"\(([^()]*)\)\s*$")


@dataclass(frozen=True)
class PluginInfo:
    name: str        # nom affiché, ex. « Pro-Q 4 (FabFilter) »
    add_name: str    # nom passé à REAPER (TrackFX_AddByName), ex. « VST3: Pro-Q 4 (FabFilter) »
    kind: str        # VST3, VST, CLAP, JS…
    vendor: str
    instrument: bool = False

    @property
    def key(self) -> str:
        """Clé de dédoublonnage (même plugin vu par plusieurs sources)."""
        return f"{self.kind.upper()}|{self.name.lower()}"


def _vendor(name: str) -> str:
    match = _VENDOR.search(name)
    return match.group(1).strip() if match else ""


def from_reaper_list(entries: list[tuple[str, str]]) -> list[PluginInfo]:
    """Liste du pont : [(« VST3: Pro-Q 4 (FabFilter) », ident), …]."""
    plugins = []
    for full_name, _ident in entries:
        match = _TYPE_PREFIX.match(full_name)
        if match:
            kind = match.group("kind").upper()
            name = match.group("name").strip()
            instrument = bool(match.group("instrument"))
        else:
            kind, name, instrument = "", full_name.strip(), False
        if name:
            plugins.append(PluginInfo(name, full_name, kind, _vendor(name), instrument))
    return plugins


def scan_reaper_cache(resource_path: Path) -> list[PluginInfo]:
    """Lit le cache de scan VST de REAPER (format « fichier=hash,uid,Nom (Éditeur) »)."""
    plugins = []
    for cache in ("reaper-vstplugins64.ini", "reaper-vstplugins.ini"):
        path = resource_path / cache
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            filename, sep, value = line.partition("=")
            if not sep or line.startswith("["):
                continue
            parts = value.split(",", 2)
            if len(parts) < 3:
                continue
            name = parts[2].strip()
            if not name or name.startswith("<"):  # <SHELL> : conteneur, pas un plugin
                continue
            instrument = name.endswith("!!!VSTi")
            name = name.removesuffix("!!!VSTi").strip()
            kind = "VST3" if filename.lower().endswith(".vst3") else "VST"
            plugins.append(PluginInfo(name, f"{kind}: {name}", kind, _vendor(name), instrument))
    return plugins


def default_vst3_dirs(extra: list[str] | None = None) -> list[Path]:
    dirs = []
    common = os.environ.get("CommonProgramFiles") or os.environ.get("CommonProgramW6432")
    if common:
        dirs.append(Path(common) / "VST3")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(Path(local) / "Programs" / "Common" / "VST3")
    dirs.extend(Path(os.path.expandvars(d)) for d in (extra or []))
    return dirs


def scan_vst3_dirs(dirs: list[Path]) -> list[PluginInfo]:
    """Cherche les .vst3 (fichiers ou dossiers « bundle ») sans entrer dedans."""
    plugins = []
    for root in dirs:
        if not root.is_dir():
            continue
        for _current, subdirs, files in os.walk(root):
            bundles = [d for d in subdirs if d.lower().endswith(".vst3")]
            subdirs[:] = [d for d in subdirs if not d.lower().endswith(".vst3")]
            for entry in bundles + [f for f in files if f.lower().endswith(".vst3")]:
                name = Path(entry).stem
                plugins.append(PluginInfo(name, f"VST3: {name}", "VST3", "", False))
    return plugins


def merge(*sources: list[PluginInfo]) -> list[PluginInfo]:
    """Fusionne les sources (la première gagne en cas de doublon), triées par nom."""
    seen: dict[str, PluginInfo] = {}
    for source in sources:
        for plugin in source:
            seen.setdefault(plugin.key, plugin)
    return sorted(seen.values(), key=lambda p: (p.name.lower(), p.kind))


def collect_plugins(reaper_list: list[tuple[str, str]] | None, resource_path: Path | None,
                    extra_dirs: list[str] | None = None) -> list[PluginInfo]:
    """Liste finale pour le dialogue d'ajout."""
    sources = []
    if reaper_list:
        sources.append(from_reaper_list(reaper_list))
    else:
        if resource_path is not None:
            sources.append(scan_reaper_cache(resource_path))
        sources.append(scan_vst3_dirs(default_vst3_dirs(extra_dirs)))
    plugins = merge(*sources)
    log.info("%d plugins disponibles (%s).", len(plugins),
             "liste de REAPER" if reaper_list else "cache + scan des dossiers")
    return plugins
