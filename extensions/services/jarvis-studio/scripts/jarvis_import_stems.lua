ardour {
	["type"]    = "EditorAction",
	name        = "Jarvis: Import StemDeck stems",
	license     = "MIT",
	author      = "Jarvis",
	description = [[Imports every WAV in this session's "stems" folder as its own track, at the start, and sets the tempo from stems/tempo.txt. Then adds a production setup: LSP EQ and compressor on each audio track and an LSP limiter on the master (all switched off), plus a Surge XT synth track and a DrumGizmo drum track.]]
}

local function jarvis_production_setup (Session)
	local LV2 = ARDOUR.PluginType.LV2
	local function add (route, id, active)
		local p = ARDOUR.LuaAPI.new_plugin (Session, id, LV2, "")
		if p:isnil () then return false end
		route:add_processor_by_index (p, -1, nil, true)
		if not active then p:deactivate () end
		return true
	end
	local function has (name)
		return not Session:route_by_name (name):isnil ()
	end
	local LSP = "http://lsp-plug.in/plugins/lv2/"
	-- Every audio track: EQ + compressor, added switched off so the sound is unchanged.
	for t in Session:get_tracks ():iter () do
		if t:data_type ():to_string () == "audio" and t:nth_plugin (0):isnil () then
			add (t, LSP .. "para_equalizer_x8_stereo", false)
			add (t, LSP .. "compressor_stereo", false)
		end
	end
	-- Master: limiter, switched off until mastering.
	local m = Session:master_out ()
	if not m:isnil () and m:nth_plugin (0):isnil () then add (m, LSP .. "limiter_stereo", false) end
	-- Instrument tracks.
	local midi, audio = ARDOUR.DataType ("midi"), ARDOUR.DataType ("audio")
	for _, inst in ipairs ({ { "Synth (Surge XT)", "Surge XT" }, { "Drums (DrumGizmo)", "DrumGizmo" } }) do
		if not has (inst[1]) then
			local pi = ARDOUR.LuaAPI.new_plugin_info (inst[2], LV2)
			if not pi:isnil () then
				Session:new_midi_track (ARDOUR.ChanCount (midi, 1), ARDOUR.ChanCount (audio, 2), true, pi, nil,
					ARDOUR.RouteGroup (), 1, inst[1], ARDOUR.PresentationInfo.max_order, ARDOUR.TrackMode.Normal, true, false)
			end
		end
	end
end

function factory () return function ()
	local dir = ARDOUR.LuaAPI.build_filename (Session:path (), "stems")
	local files = C.StringVector ()
	local listing = io.popen ('ls -1 "' .. dir .. '"/*.wav 2>/dev/null')
	for f in listing:lines () do files:push_back (f) end
	listing:close ()
	if files:size () == 0 then
		jarvis_production_setup (Session)
		Session:save_state ("", false, false, false, false, false)
		LuaDialog.Message ("Jarvis", "No WAV files in " .. dir .. ". Added the production setup only.", LuaDialog.MessageType.Info, LuaDialog.ButtonType.Close):run ()
		return
	end

	local tf = io.open (ARDOUR.LuaAPI.build_filename (dir, "tempo.txt"), "r")
	if tf then
		local bpm = tonumber (tf:read ("*l"))
		tf:close ()
		if bpm and bpm > 20 then
			pcall (function ()
				local tm = Temporal.TempoMap.write_copy ()
				tm:set_tempo (Temporal.Tempo (bpm, bpm, 4), Temporal.timepos_t (0))
				Temporal.TempoMap.update (tm)
			end)
		end
	end

	Editor:do_import (files,
		Editing.ImportDistinctFiles, Editing.ImportAsTrack, ARDOUR.SrcQuality.SrcBest,
		ARDOUR.MidiTrackNameSource.SMFFileAndTrackName, ARDOUR.MidiTempoMapDisposition.SMFTempoIgnore,
		Temporal.timepos_t (0), ARDOUR.PluginInfo (), ARDOUR.Track (), false)
	jarvis_production_setup (Session)
	Session:save_state ("", false, false, false, false, false)
end end
