# jarvis-studio

An MCP server and command-line tool. It connects [StemDeck](https://github.com/stemdeckapp/stemdeck), the Ubuntu Studio apps and the local AI models on this computer to Jarvis (ODS Hermes). Everything runs locally. Outputs go to `~/Music/Jarvis/`.

## The five workflows

| # | Workflow | Ask Jarvis something like | Command line |
|---|---|---|---|
| 1 | **Practice**: StemDeck mix, rubberband time-stretch and Guitarix amp presets | "Loop the chorus of *song* without guitar at 80% and give me a clean amp" | `jarvis-studio practice "song" --mute guitar --section chorus --speed 0.8 --guitar-preset clean` |
| 2 | **Stems into a DAW**: an Audacity project (built automatically) and an Ardour session | "Put *song* in Audacity and Ardour" | `jarvis-studio daw "song"` |
| 3 | **Karaoke**: vocal-free backing, `.lrc` and FreeShow lyrics, the StemDeck Karaoke page, OBS recording | "Karaoke for *song*, one semitone down, and record it" | `jarvis-studio karaoke "song" --semitones -1 --record` |
| 4 | **AI re-production**: keep the vocals, ACE-Step makes a new backing, MOSS-TTS adds a spoken intro, then a -14 LUFS master and a video | "Make a lo-fi version of *song* with an intro" | `jarvis-studio produce "song" "lofi hip hop, warm keys" --intro "This is Jarvis..."` |
| 5 | **Sheet music**: stems to MIDI with basic-pitch, then PDFs with MuseScore | "Write out the bass and vocals of *song*" | `jarvis-studio sheet "song" --parts bass,vocals` |

Extra tools: `studio_split`, `studio_make_music` (ACE-Step text-to-music), `studio_speak` (MOSS-TTS-Nano), `studio_guitar`, `studio_obs` and `studio_stop`.

`song` can be a title or id that's already in StemDeck, an audio file path, or a YouTube or SoundCloud link. A link or file is split first. Only use audio you have the right to use.

## Dashboard

Open **http://127.0.0.1:7722**, or **Jarvis Studio** in the app menu. It runs as the `jarvis-studio-dashboard` user service and only answers on this computer.

- **Status bar**: StemDeck, the Hermes MCP server, the Hermes container, Lemonade, Guitarix, OBS, Audacity, Ardour, playback, AI models and free disk space.
- **Library**: every song in StemDeck with key, BPM and length. Pick one to see how present each part is. **Add a song** splits a file or link.
- **Workflow tabs**: forms for the five workflows plus **Create** (ACE-Step music, MOSS-TTS speech). Click a part to mute it, again to solo it.
- **Rigs**: open Guitarix and switch presets; open OBS, change scene, start or stop recording (asks first).
- **Jobs**: workflows run in the background (two at a time) with results and file buttons.
- **Outputs**: recent files in `~/Music/Jarvis`. Preview in the browser, play on the computer speakers, open PDFs and videos, or show in the folder.
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

- **Ardour** can't import audio without its window open. `daw` creates the session with the stems inside it and installs a Lua action. In Ardour, run **Edit > Lua Scripts > Script Manager > "Jarvis: Import StemDeck stems"** once; assign it to an action button so later imports take one click.
- **OBS** won't record until its first-run Auto-Configuration Wizard has been finished or cancelled once. Add a camera or screen source to a scene for Performance videos.
- **Sheet music** is an automatic transcription, so treat it as a draft. MuseScore 3 puts each part on a piano grand staff. MuseScore 4 engraves better.
- **Song sections** (chorus, verse) depend on StemDeck's experimental detection. If a song has none, use `--start` and `--end` in seconds.
- **Mixxx 2.5** can't load stem files. Mixxx 2.6 adds that.
