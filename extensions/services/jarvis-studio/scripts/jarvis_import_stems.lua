ardour {
	["type"]    = "EditorAction",
	name        = "Jarvis: Import StemDeck stems",
	license     = "MIT",
	author      = "Jarvis",
	description = [[Imports every WAV in this session's "stems" folder as its own track, at the start, and sets the tempo from stems/tempo.txt.]]
}

function factory () return function ()
	local dir = ARDOUR.LuaAPI.build_filename (Session:path (), "stems")
	local files = C.StringVector ()
	local listing = io.popen ('ls -1 "' .. dir .. '"/*.wav 2>/dev/null')
	for f in listing:lines () do files:push_back (f) end
	listing:close ()
	if files:size () == 0 then
		LuaDialog.Message ("Jarvis", "No WAV files in " .. dir, LuaDialog.MessageType.Warning, LuaDialog.ButtonType.Close):run ()
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
	Session:save_state ("", false, false, false, false, false)
end end
