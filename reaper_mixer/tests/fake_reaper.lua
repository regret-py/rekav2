-- Faux REAPER minimal pour exécuter le pont hors de REAPER.
-- Usage : REAPER_MIXER_IPC=<dir> lua5.4 fake_reaper.lua <bridge.lua> <stopfile>
local bridge, stopfile = arg[1], arg[2]
local now = 0.0
local tracks = {}
local saved = 0
local deferred, atexit_fn
local installed = {
  { "VST3: Pro-Q 4 (FabFilter)", "FabFilter Pro-Q 4.vst3" },
  { "VST: ReaLimit (Cockos)", "realimit.dll" },
  { "JS: Volume Adjustment", "utility/volume" },
}

local function new_track()
  return { name = "", values = { I_FXEN = 1, B_MAINSEND = 1 }, fx = {}, sends = {} }
end

reaper = {}
function reaper.time_precise() return now end
function reaper.GetTrack(_, i) return tracks[i + 1] end
function reaper.CountTracks() return #tracks end
function reaper.InsertTrackAtIndex(i) table.insert(tracks, i + 1, new_track()) end
function reaper.GetSetMediaTrackInfo_String(tr, key, value, set)
  assert(key == "P_NAME")
  if set then tr.name = value end
  return true, tr.name
end
function reaper.SetMediaTrackInfo_Value(tr, key, v) tr.values[key] = v; return true end
function reaper.GetMediaTrackInfo_Value(tr, key) return tr.values[key] or 0 end
function reaper.GetTrackNumSends(tr, cat) assert(cat == 1); return #tr.sends end
function reaper.CreateTrackSend(tr, dest) assert(dest == nil); tr.sends[#tr.sends + 1] = {}; return #tr.sends - 1 end
function reaper.SetTrackSendInfo_Value(tr, cat, idx, key, v) assert(cat == 1); tr.sends[idx + 1][key] = v; return true end
function reaper.TrackFX_AddByName(tr, name)
  for _, p in ipairs(installed) do
    if p[1] == name then tr.fx[#tr.fx + 1] = { name = name, enabled = true, open = false }; return #tr.fx - 1 end
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
function reaper.EnumProjects() return 0, "/tmp/session.rpp" end
function reaper.Main_SaveProject() saved = saved + 1 end
function reaper.IsProjectDirty() return 0 end
function reaper.Main_OnCommand(id) last_action = id end
function reaper.GetAppVersion() return "7.22/linux-test" end
local device = { MODE = "ASIO", IDENT_IN = "Voicemeeter Insert Virtual ASIO", IDENT_OUT = "Voicemeeter Insert Virtual ASIO", SRATE = "48000", BSIZE = "256" }
function reaper.GetAudioDeviceInfo(key) return true, device[key] end
function reaper.EnumInstalledFX(i)
  local p = installed[i + 1]
  if not p then return false end
  return true, p[1], p[2]
end
function reaper.ShowConsoleMsg(s) end
function reaper.defer(fn) deferred = fn end
function reaper.atexit(fn) atexit_fn = fn end

dofile(bridge)

while true do
  local f = io.open(stopfile, "r")
  if f then f:close(); break end
  local fn = deferred
  deferred = nil
  if not fn then break end
  fn()
  now = now + 0.034
  os.execute("sleep 0.03")
end
-- Résumé pour le test Python
local out = io.open(os.getenv("REAPER_MIXER_IPC") .. "/fake_summary.txt", "w")
local t1 = tracks[1]
out:write(string.format("saved=%d action=%s tracks=%d t1in=%s t1arm=%s t1mode=%s t1mon=%s t1main=%s t1dst=%s t3in=%s\n",
  saved, tostring(last_action), #tracks, t1.values.I_RECINPUT, t1.values.I_RECARM, t1.values.I_RECMODE,
  t1.values.I_RECMON, t1.values.B_MAINSEND, t1.sends[1] and t1.sends[1].I_DSTCHAN, tracks[3].values.I_RECINPUT))
out:close()
if atexit_fn then atexit_fn() end
