--[[
  REAPER Mixer — pont entre l'application et REAPER.

  Chargé au démarrage de REAPER par Scripts/__startup.lua, uniquement quand
  REAPER est lancé par REAPER Mixer (variable d'environnement REAPER_MIXER_IPC).
  Lancé à la main, REAPER n'exécute rien de ce fichier.

  Échanges par fichiers texte dans le dossier REAPER_MIXER_IPC :
    commands.txt  écrit par l'app   : commandes numérotées
    state.txt     écrit par le pont : pistes, effets, carte son, accusés (~5 fois/s)
    fxlist.txt    écrit par le pont : plugins installés, sur demande
  Une ligne par enregistrement, champs séparés par des tabulations,
  « \ », tabulation et retour à la ligne échappés en \\, \t, \n.
  Chaque fichier se termine par « END <n> » : un fichier lu pendant son
  écriture est simplement ignoré jusqu'au tour suivant.
]]

local DIR = os.getenv("REAPER_MIXER_IPC")
if not DIR or DIR == "" then return end
DIR = DIR:gsub("\\", "/"):gsub("/+$", "")

local CMD_PATH = DIR .. "/commands.txt"
local STATE_PATH = DIR .. "/state.txt"
local FXLIST_PATH = DIR .. "/fxlist.txt"

local STATE_PERIOD = 0.2     -- s : écriture de l'état, sert aussi de battement de cœur
local AUTOSAVE_PERIOD = 30   -- s : sauvegarde du projet s'il a été modifié
local MAX_ERRORS = 20

local session = nil          -- identifiant de la session de l'app
local last_seq = 0           -- dernière commande exécutée
local state_n = 0
local last_state_t = 0
local last_autosave_t = reaper.time_precise()
local errors = {}            -- { {seq, message}, ... }

-- ------------------------------------------------------------------ texte
local UNESCAPE = { ["\\"] = "\\", t = "\t", n = "\n" }

local function unescape(s)
  return (s:gsub("\\(.)", UNESCAPE))
end

local function escape(s)
  s = tostring(s or "")
  s = s:gsub("\\", "\\\\"):gsub("\t", "\\t"):gsub("\r", ""):gsub("\n", "\\n")
  return s
end

local function split(line)
  local fields = {}
  for field in (line .. "\t"):gmatch("([^\t]*)\t") do
    fields[#fields + 1] = unescape(field)
  end
  return fields
end

local function read_file(path)
  local f = io.open(path, "rb")
  if not f then return nil end
  local data = f:read("a")
  f:close()
  return data
end

local function write_file(path, data)
  local f = io.open(path, "wb")
  if not f then return false end
  f:write(data)
  f:close()
  return true
end

local function add_error(seq, message)
  errors[#errors + 1] = { seq, message }
  if #errors > MAX_ERRORS then table.remove(errors, 1) end
end

-- ----------------------------------------------------------------- projet
local function track(index)
  local tr = reaper.GetTrack(0, tonumber(index))
  if not tr then error("piste " .. tostring(index) .. " introuvable", 0) end
  return tr
end

local function ensure_tracks(count)
  count = tonumber(count)
  while reaper.CountTracks(0) < count do
    reaper.InsertTrackAtIndex(reaper.CountTracks(0), true)
  end
end

local function save_project()
  -- Un projet sans fichier ouvrirait « Enregistrer sous » (invisible) : on évite
  local _, path = reaper.EnumProjects(-1)
  if path and path ~= "" then reaper.Main_SaveProject(0, false) end
end

-- Nomme la piste et, si demandé, la câble pour traiter en direct l'insert
-- Voicemeeter : entrée stéréo in_ch → effets → sortie matérielle out_ch.
local function setup_track(index, name, in_ch, out_ch, manage)
  ensure_tracks(index + 1)
  local tr = track(index)
  reaper.GetSetMediaTrackInfo_String(tr, "P_NAME", name, true)
  if manage ~= "1" then return end
  reaper.SetMediaTrackInfo_Value(tr, "I_RECINPUT", 1024 + tonumber(in_ch))  -- 1024 = stéréo
  reaper.SetMediaTrackInfo_Value(tr, "I_RECMODE", 2)     -- aucun enregistrement
  reaper.SetMediaTrackInfo_Value(tr, "I_RECMON", 1)      -- écoute de l'entrée
  reaper.SetMediaTrackInfo_Value(tr, "I_RECARM", 1)
  reaper.SetMediaTrackInfo_Value(tr, "B_MAINSEND", 0)    -- pas d'envoi au master
  local send = 0
  if reaper.GetTrackNumSends(tr, 1) == 0 then
    send = reaper.CreateTrackSend(tr, nil)              -- nil = sortie matérielle
  end
  reaper.SetTrackSendInfo_Value(tr, 1, send, "I_SRCCHAN", 0)
  reaper.SetTrackSendInfo_Value(tr, 1, send, "I_DSTCHAN", tonumber(out_ch))
  reaper.SetTrackSendInfo_Value(tr, 1, send, "D_VOL", 1.0)
  reaper.SetTrackSendInfo_Value(tr, 1, send, "B_MUTE", 0)
end

local function write_fxlist(request)
  local lines = { "FXLIST " .. request }
  if reaper.EnumInstalledFX then
    local i = 0
    while true do
      local ok, name, ident = reaper.EnumInstalledFX(i)
      if not ok then break end
      lines[#lines + 1] = escape(name) .. "\t" .. escape(ident)
      i = i + 1
    end
  else
    lines[#lines + 1] = "UNSUPPORTED"  -- REAPER < 6.37
  end
  lines[#lines + 1] = "END " .. request
  write_file(FXLIST_PATH, table.concat(lines, "\n") .. "\n")
end

-- -------------------------------------------------------------- commandes
local HANDLERS = {
  tracks = function(a) ensure_tracks(a[1]) end,
  track_setup = function(a) setup_track(tonumber(a[1]), a[2], a[3], a[4], a[5]) end,
  rename = function(a)
    reaper.GetSetMediaTrackInfo_String(track(a[1]), "P_NAME", a[2], true)
  end,
  add_fx = function(a)
    if reaper.TrackFX_AddByName(track(a[1]), a[2], false, -1) < 0 then
      error("REAPER ne trouve pas le plugin « " .. a[2] .. " »", 0)
    end
    save_project()
  end,
  remove_fx = function(a)
    reaper.TrackFX_Delete(track(a[1]), tonumber(a[2]))
    save_project()
  end,
  fx_enabled = function(a)
    reaper.TrackFX_SetEnabled(track(a[1]), tonumber(a[2]), a[3] == "1")
    save_project()
  end,
  chain_enabled = function(a)
    reaper.SetMediaTrackInfo_Value(track(a[1]), "I_FXEN", a[2] == "1" and 1 or 0)
    save_project()
  end,
  open_fx = function(a)  -- bascule la fenêtre flottante du plugin
    local tr, fx = track(a[1]), tonumber(a[2])
    reaper.TrackFX_Show(tr, fx, reaper.TrackFX_GetOpen(tr, fx) and 2 or 3)
  end,
  save = function() save_project() end,
  list_fx = function(a) write_fxlist(a[1]) end,
  action = function(a) reaper.Main_OnCommand(tonumber(a[1]), 0) end,
}

local function process_commands()
  local data = read_file(CMD_PATH)
  if not data then return false end
  local cmd_session, rev = data:match("^CMDS (%S+) (%d+)\n")
  if not cmd_session or not data:find("\nEND " .. rev .. "\n", 1, true) then
    return false  -- fichier absent ou en cours d'écriture
  end
  if cmd_session ~= session then  -- nouvelle session de l'app : on repart de zéro
    session, last_seq, errors = cmd_session, 0, {}
  end
  local changed = false
  for line in data:gmatch("([^\n]*)\n") do
    local seq_text, rest = line:match("^(%d+)\t(.*)$")
    local seq = tonumber(seq_text)
    if seq and seq > last_seq then
      local args = split(rest)
      local verb = table.remove(args, 1)
      local handler = HANDLERS[verb]
      if handler then
        local ok, err = pcall(handler, args)
        if not ok then add_error(seq, tostring(err)) end
      else
        add_error(seq, "commande inconnue : " .. tostring(verb))
      end
      last_seq = seq
      changed = true
    end
  end
  return changed
end

-- ------------------------------------------------------------------- état
local function device_line()
  if not reaper.GetAudioDeviceInfo then return nil end
  local fields = { "DEVICE" }
  for _, key in ipairs({ "MODE", "IDENT_IN", "IDENT_OUT", "SRATE", "BSIZE" }) do
    local _, value = reaper.GetAudioDeviceInfo(key, "")
    fields[#fields + 1] = escape(value)
  end
  return table.concat(fields, "\t")
end

local function build_state()
  state_n = state_n + 1
  local out = {
    "STATE " .. state_n,
    "SESSION " .. (session or "-"),
    "ACK " .. last_seq,
    "VERSION " .. escape(reaper.GetAppVersion()),
  }
  out[#out + 1] = device_line()
  for t = 0, reaper.CountTracks(0) - 1 do
    local tr = reaper.GetTrack(0, t)
    local _, name = reaper.GetSetMediaTrackInfo_String(tr, "P_NAME", "", false)
    local count = reaper.TrackFX_GetCount(tr)
    local chain_on = reaper.GetMediaTrackInfo_Value(tr, "I_FXEN") ~= 0
    out[#out + 1] = table.concat({ "TRACK", t, escape(name), chain_on and 1 or 0, count }, "\t")
    for i = 0, count - 1 do
      local _, fx_name = reaper.TrackFX_GetFXName(tr, i, "")
      out[#out + 1] = table.concat({
        "FX", t, i, escape(fx_name),
        reaper.TrackFX_GetEnabled(tr, i) and 1 or 0,
        reaper.TrackFX_GetOpen(tr, i) and 1 or 0,
      }, "\t")
    end
  end
  for _, e in ipairs(errors) do
    out[#out + 1] = "ERR\t" .. e[1] .. "\t" .. escape(e[2])
  end
  out[#out + 1] = "END " .. state_n
  return table.concat(out, "\n") .. "\n"
end

-- ------------------------------------------------------------- boucle
local function tick()
  local now = reaper.time_precise()
  if process_commands() or now - last_state_t >= STATE_PERIOD then
    write_file(STATE_PATH, build_state())
    last_state_t = now
  end
  if now - last_autosave_t >= AUTOSAVE_PERIOD then
    last_autosave_t = now
    if reaper.IsProjectDirty(0) ~= 0 then save_project() end
  end
end

local function loop()
  local ok, err = pcall(tick)
  if not ok then add_error(0, "pont : " .. tostring(err)) end
  reaper.defer(loop)
end

reaper.atexit(function() write_file(STATE_PATH, "CLOSED\n") end)
loop()
