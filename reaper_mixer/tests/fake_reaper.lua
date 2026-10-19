-- Faux REAPER minimal pour exécuter le pont hors de REAPER.
-- Usage : REKA_IPC=<dir> lua5.4 fake_reaper.lua <__startup.lua> <stopfile>
--
-- Projet de départ : comme un projet d'utilisateur (MAIN.rpp) — pistes sans
-- nom, piste 1 en mono sur l'entrée 1, pistes 2 et 3 en stéréo 3/4 et 5/6.
local startup, stopfile = arg[1], arg[2]
local now = 0.0
local tracks = {}
local saved = 0
local project = "C:/Projets/MAIN.rpp"
local last_action, opened = nil, nil
local deferred, atexit_fn
local installed = {
  { "VST3: Pro-Q 4 (FabFilter)", "FabFilter Pro-Q 4.vst3" },
  { "VST: ReaLimit (Cockos)", "realimit.dll" },
  { "JS: Volume Adjustment", "utility/volume" },
}

local function new_track(rec_input, hw_dst)
  local tr = { name = "", fx = {}, sends = {},
               values = { I_FXEN = 1, B_MAINSEND = 1, I_RECINPUT = rec_input or 0,
                          I_RECARM = 1, I_RECMON = 1, I_RECMODE = 0 } }
  if hw_dst then tr.sends[1] = { I_DSTCHAN = hw_dst } end
  return tr
end

local function load_user_project()
  tracks = { new_track(0, 0), new_track(1026, 2), new_track(1028, 4) }
end
load_user_project()

reaper = {}
function reaper.time_precise() return now end
function reaper.GetTrack(_, i) return tracks[i + 1] end
function reaper.CountTracks() return #tracks end
function reaper.InsertTrackAtIndex(i) table.insert(tracks, i + 1, new_track(0, nil)) end
function reaper.GetSetMediaTrackInfo_String(tr, key, value, set)
  assert(key == "P_NAME")
  if set then tr.name = value end
  return true, tr.name
end
function reaper.SetMediaTrackInfo_Value(tr, key, v) tr.values[key] = v; return true end
function reaper.GetMediaTrackInfo_Value(tr, key) return (tr.values[key] or 0) + 0.0 end
function reaper.GetTrackNumSends(tr, cat) assert(cat == 1); return #tr.sends end
function reaper.CreateTrackSend(tr, dest) assert(dest == nil); tr.sends[#tr.sends + 1] = {}; return #tr.sends - 1 end
function reaper.SetTrackSendInfo_Value(tr, cat, idx, key, v) assert(cat == 1); tr.sends[idx + 1][key] = v; return true end
function reaper.TrackFX_AddByName(tr, name)
  for _, p in ipairs(installed) do
    if p[1] == name then
      -- ReaLimit : 2,5 ms d'anticipation à 48 kHz (120 échantillons)
      local pdc = name:find("ReaLimit") and 120 or 0
      tr.fx[#tr.fx + 1] = { name = name, enabled = true, open = false, pdc = pdc }
      return #tr.fx - 1
    end
  end
  return -1
end
function reaper.TrackFX_Delete(tr, i) table.remove(tr.fx, i + 1); return true end
function reaper.TrackFX_SetEnabled(tr, i, e) tr.fx[i + 1].enabled = e end
function reaper.TrackFX_GetEnabled(tr, i) return tr.fx[i + 1].enabled end
function reaper.TrackFX_GetOpen(tr, i) return tr.fx[i + 1].open end
function reaper.TrackFX_Show(tr, i, flag) tr.fx[i + 1].open = (flag == 3) end
function reaper.TrackFX_GetCount(tr) return #tr.fx end
function reaper.TrackFX_GetFXName(tr, i) return true, tr.fx[i + 1].name end
function reaper.EnumProjects() return 0, project end
function reaper.Main_SaveProject() saved = saved + 1 end
function reaper.Main_openProject(name)
  assert(name:sub(1, 9) == "noprompt:", "ouverture sans « noprompt: »")
  opened = name:sub(10)
  project = opened
  tracks = { new_track(1024, 0) }   -- autre projet : une seule piste, stéréo 1/2
end
function reaper.IsProjectDirty() return 0 end
function reaper.Main_OnCommand(id) last_action = id end
function reaper.GetAppVersion() return "7.22/linux-test" end
local device = { MODE = "ASIO", IDENT_IN = "Voicemeeter Potato Insert Virtual ASIO",
                 IDENT_OUT = "Voicemeeter Potato Insert Virtual ASIO", SRATE = "48000", BSIZE = "256" }
function reaper.GetAudioDeviceInfo(key) return true, device[key] end
function reaper.TrackFX_GetNamedConfigParm(tr, i, key)
  if key == "pdc" then return true, tostring(tr.fx[i + 1].pdc or 0) end
  return false, ""
end
function reaper.GetInputOutputLatency() return 256, 288 end
function reaper.EnumInstalledFX(i)
  local p = installed[i + 1]
  if not p then return false end
  return true, p[1], p[2]
end
function reaper.ShowConsoleMsg(s) end
function reaper.defer(fn) deferred = fn end
function reaper.atexit(fn) atexit_fn = fn end

dofile(startup)

-- Résumé pour le test Python, réécrit à chaque tour
local function summary()
  local parts = { "saved=" .. saved, "action=" .. tostring(last_action),
                  "opened=" .. tostring(opened), "tracks=" .. #tracks }
  for i, tr in ipairs(tracks) do
    parts[#parts + 1] = string.format("t%d=%s|in%d|arm%d|mon%d|mode%d|main%d|dst%s", i, tr.name,
      tr.values.I_RECINPUT, tr.values.I_RECARM, tr.values.I_RECMON, tr.values.I_RECMODE or 0,
      tr.values.B_MAINSEND, tostring(tr.sends[1] and tr.sends[1].I_DSTCHAN))
  end
  local out = io.open(os.getenv("REKA_IPC") .. "/fake_summary.txt", "w")
  out:write(table.concat(parts, " ") .. "\n")
  out:close()
end

while true do
  local f = io.open(stopfile, "r")
  if f then f:close(); break end
  local fn = deferred
  deferred = nil
  if not fn then break end
  fn()
  summary()
  now = now + 0.034
  os.execute("sleep 0.03")
end
if atexit_fn then atexit_fn() end
