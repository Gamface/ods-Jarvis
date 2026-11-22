# jarvis-studio

An MCP server, command-line tool and local dashboard for a music **and video** studio. It connects [StemDeck](https://github.com/stemdeckapp/stemdeck), the Ubuntu Studio apps and the local AI models on this computer to Jarvis (ODS Hermes). Everything runs locally. Audio goes to `~/Music/Jarvis/` and video to `~/Videos/Jarvis/`.

## The five workflows

| # | Workflow | Ask Jarvis something like | Command line |
|---|---|---|---|
| 1 | **Practice**: StemDeck mix, rubberband time-stretch and Guitarix amp presets | "Loop the chorus of *song* without guitar at 80% and give me a clean amp" | `jarvis-studio practice "song" --mute guitar --section chorus --speed 0.8 --guitar-preset clean` |
| 2 | **Stems into a DAW**: an Audacity project (built automatically) and an Ardour session | "Put *song* in Audacity and Ardour" | `jarvis-studio daw "song"` |
| 3 | **Karaoke**: vocal-free backing, `.lrc` and FreeShow lyrics, the StemDeck Karaoke page, OBS recording | "Karaoke for *song*, one semitone down, and record it" | `jarvis-studio karaoke "song" --semitones -1 --record` |
| 4 | **AI re-production**: keep the vocals, ACE-Step makes a new backing, MOSS-TTS adds a spoken intro, then a -14 LUFS master and a video | "Make a lo-fi version of *song* with an intro" | `jarvis-studio produce "song" "lofi hip hop, warm keys" --intro "This is Jarvis..."` |
| 5 | **Sheet music**: stems to MIDI with basic-pitch, then PDFs with MuseScore | "Write out the bass and vocals of *song*" | `jarvis-studio sheet "song" --parts bass,vocals` |

Extra tools: `studio_split`, `studio_make_music` (new songs, see below), `studio_speak` (MOSS-TTS-Nano), `studio_guitar`, `studio_obs`, `studio_stop` and `studio_workspace` (the `~/Studio` pipeline, see [agents/README.md](agents/README.md)).

### Writing new songs

ACE-Step 1.5 writes full songs on this computer: sung when you give lyrics (with `[verse]`, `[chorus]` and `[bridge]` tags), instrumental when you don't. You can set the length (10 s to 10 min), tempo, key and vocal language. Each song is saved to `~/Music/Jarvis/songs/<title>.wav` with a `.txt` of its settings and lyrics. By default it's also split in StemDeck, so it appears in the library for practice, karaoke, sheet music and every video workflow.

- Ask Jarvis: "Write lyrics for a late-night R&B song, then make it 90 BPM in A minor."
- Dashboard: **Music studio › Create › New song**.
- Command line: `jarvis-studio make "soulful R&B, warm Rhodes, female vocals" --title "Late Night Signal" --lyrics lyrics.txt --duration 90 --bpm 90 --key "A minor"`

A 30 s sung song takes about 1.5 min including the split, and roughly 2× the length on CPU in general.

`song` can be a title or id that's already in StemDeck, an audio file path, or a YouTube or SoundCloud link. A link or file is split first. Only use audio you have the right to use.

## Video studio

Built on ffmpeg (hardware H.264 on the Intel GPU through VAAPI), Inkscape for titles and thumbnails, OBS for recording, Kdenlive (OpenTimelineIO timelines) and Whisper in Lemonade for captions. Videos go to `~/Videos/Jarvis/`.

| # | Workflow | Ask Jarvis something like | Command line |
|---|---|---|---|
| V1 | **Music video**: a moving waveform lane per part, a spectrum or one big waveform, plus a title card, synced lyrics and a thumbnail | "Make a vertical spectrum video of the chorus of *song*" | `jarvis-studio music-video "song" --style spectrum --aspect 9:16 --section chorus` |
| V2 | **Performance video**: OBS records while the backing plays. Jarvis finds where the backing starts in the take, mixes in the studio track and adds lyrics | "Record a karaoke take of *song*" … "finish the performance" | `jarvis-studio perform-start "song"` then `perform-finish`, or `performance take.mp4 "song"` |
| V3 | **Beat-cut edit**: a folder of clips and photos cut every 2, 4 or 8 beats, plus an `.otio` timeline for Kdenlive | "Cut my trip videos to *song*, fast, 30 seconds" | `jarvis-studio edit ~/Videos/trip "song" --pace fast --max 30` |
| V4 | **Shorts**: 9:16, 1:1 and 16:9 clips of the chorus or loudest part, with captions, -14 LUFS audio and thumbnails. Never posts | "Make a 30 second Short and a square clip from this video" | `jarvis-studio shorts video.mp4 --platforms shorts,square --seconds 30` |
| V5 | **Narrated video**: MOSS-TTS reads your script, one slide or photo per paragraph, captions from the script, music ducked under the voice | "Make a video explaining how this song was made" | `jarvis-studio narrate script.txt "Title" --music "song"` |
| + | **Captions**: Whisper subtitles (`.srt`) for any video, optionally burned in | "Caption this video" | `jarvis-studio captions talk.mp4 --style pop` |

Hermes tools: `video_music_video`, `video_perform_start`, `video_perform_finish`, `video_performance`, `video_obs_scene`, `video_auto_edit`, `video_shorts`, `video_narrated` and `video_captions`. The dashboard has the same forms under **Video studio**.

Timings on this computer: a 45 s music video about 18 s, a 30 s beat-cut edit with 20 cuts about 8 s, a 3-paragraph narrated video about 35 s, a 10 s caption job about 10 s.

**Camera:** OBS starts with an empty scene. Run `jarvis-studio obs-scene` (or **Set up camera scene** in the dashboard) once to add a "Jarvis Performance" scene with `/dev/video0` and the default microphone. It turns the camera on.

**Whisper:** Lemonade's Vulkan build of whisper.cpp crashes on this machine, so jarvis-studio loads `Whisper-Small` with the CPU backend (`JARVIS_WHISPER_BACKEND=cpu`).

## Dashboard

Open **http://127.0.0.1:7722**, or **Jarvis Studio** in the app menu. It runs as the `jarvis-studio-dashboard` user service and only answers on this computer.

- **Status bar**: StemDeck, the Hermes MCP server, the Hermes container, Lemonade, Guitarix, OBS, Audacity, Ardour, playback, AI models and free disk space.
- **Library**: every song in StemDeck with key, BPM and length. Pick one to see how present each part is. **Add a song** splits a file or link.
- **Music studio / Video studio** switch: forms for the five music workflows plus **Create** (ACE-Step music, MOSS-TTS speech), and the five video workflows plus **Captions**. Click a part to mute it, again to solo it.
- **Rigs**: open Guitarix and switch presets; open OBS, change scene, start or stop recording (asks first).
- **Jobs**: workflows run in the background (two at a time) with results and file buttons.
- **Outputs**: recent files in `~/Music/Jarvis` and `~/Videos/Jarvis`. Preview audio, watch videos in a pop-up player, play on the computer speakers, open PDFs, thumbnails and timelines, or show in the folder.
- **Activity**: the shared audit log, so you see what Hermes did as well as the dashboard.

Requests must come from `localhost`, and every action needs a JSON body with an `X-Jarvis` header, so other websites can't drive it from your browser.

## Timings on this computer (i9-12900H, CPU only)

| Step | Time |
|---|---|
| Split a 45 s song into 6 stems | 20 s |
| Practice track (mix, key change and stretch) | a few seconds |
| Sheet music for 3 parts | 15 s |
| AI re-production of a 45 s song (new backing, intro, master, video) | 3 min 20 s |

## Setup

```bash
bash setup.sh
```

The script needs no sudo and is safe to run again. It does five things:

1. Makes a venv in `~/ods/data/jarvis-studio/venv`.
2. Writes a token and settings to `studio.env` (mode 600).
3. Turns on Audacity's `mod-script-pipe` and OBS's websocket server, both local only.
4. Installs the `jarvis-studio` systemd user service, listening on the ODS Docker bridge `172.19.0.1:7721`, and the `jarvis-studio-dashboard` service on `127.0.0.1:7722`.
5. Installs the `jarvis-studio` command and a **Jarvis Studio** app-menu launcher.

Add the entry the script prints to `~/ods/data/hermes/config.yaml` under `mcp_servers:`, then run `docker restart ods-hermes`.

### Dependencies, installed separately

| What | Where |
|---|---|
| StemDeck, run from source as the `stemdeck.service` user unit | `127.0.0.1:8000`, library in `~/Music/StemDeck` |
| ACE-Step 1.5 | `~/ai-models/ACE-Step-1.5` |
| MOSS-TTS-Nano | `~/ai-models/MOSS-TTS-Nano` |
| basic-pitch (ONNX, with TensorFlow removed) | `~/ai-models/basic-pitch` |
| Ubuntu Studio apps | Audacity, Ardour 9, Guitarix, OBS, MuseScore 3, rubberband, ffmpeg |

## Known limits

- **Kdenlive** opens beat-cut edits through **File > Import > OpenTimelineIO**. The `.otio` files are checked with the OpenTimelineIO library, but the Kdenlive import itself hasn't been tried yet.
- **AI video generation** isn't practical here: there's no NVIDIA GPU. The video tools edit, compose and caption your own footage and the studio's audio.
- **Ardour** can't import audio without its window open. `daw` creates the session with the stems inside it and installs a Lua action. In Ardour, run **Edit > Lua Scripts > Script Manager > "Jarvis: Import StemDeck stems"** once; assign it to an action button so later imports take one click.
- **OBS** won't record until its first-run Auto-Configuration Wizard has been finished or cancelled once. Add a camera or screen source to a scene for Performance videos.
- **Sheet music** is an automatic transcription, so treat it as a draft. MuseScore 3 puts each part on a piano grand staff. MuseScore 4 engraves better.
- **Song sections** (chorus, verse) depend on StemDeck's experimental detection. If a song has none, use `--start` and `--end` in seconds.
- **Mixxx 2.5** can't load stem files. Mixxx 2.6 adds that.
