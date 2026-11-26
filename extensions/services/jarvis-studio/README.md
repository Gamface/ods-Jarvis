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

Extra tools: `studio_split`, `studio_make_music` (new songs, see below), `studio_make_image` (pictures: album covers, posters, thumbnails, with Stable Diffusion through the local Lemonade Server; about 20 s at 512×512 on the Intel graphics with Lemonade's Vulkan sd.cpp backend (`lemonade config set sdcpp.backend=vulkan`; about 35 s on CPU), saved with their prompt to `~/Music/Jarvis/images`; image and song jobs take turns so they can't run out of memory together), `studio_make_sfx` (sound effects from a description with ThinkSound-SFX through Lemonade's Vulkan backend, 1-30 s; a 5 s clip takes about 30 s on the Intel graphics; saved as WAV with the prompt to `~/Music/Jarvis/sfx`), `studio_speak` (MOSS-TTS-Nano), `studio_guitar`, `studio_obs`, `studio_stop` and `studio_workspace` (the `~/Studio` pipeline, see [agents/README.md](agents/README.md)).

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
| V6 | **Explainer** (any length, background job): scene by scene with a Kokoro voice (or MOSS), a picture, photo or slide per scene, chapter cards, captions, `chapters.txt` for YouTube and a thumbnail. Each scene's voice, picture and clip is kept, so fixing one scene only remakes that scene. Jobs queue and run one after another. 12 style presets (cinematic, documentary, 3D cartoon, anime, watercolor storybook, comic, claymation, pencil sketch, flat, retro/VHS, whiteboard, social) set the picture look, colour grade, captions (word-by-word highlight, pop, box, subtle), transitions (dip, flash, whoosh, cut) and camera motion; scenes can have their own motion, voice and pause | "Make a 10-minute explainer about how stems help you practice" | dashboard **Video → Explainer** |
| + | **Captions**: Whisper subtitles (`.srt`) for any video, optionally burned in | "Caption this video" | `jarvis-studio captions talk.mp4 --style pop` |

Hermes tools: `video_music_video`, `video_perform_start`, `video_perform_finish`, `video_performance`, `video_obs_scene`, `video_auto_edit`, `video_shorts`, `video_narrated`, `video_explainer`, `video_explainer_scene`, `video_styles` and `video_captions`, plus `studio_jobs` and `studio_job_cancel` for the background queue. The dashboard has the same forms under **Video studio**.

Timings on this computer: a 45 s music video about 18 s, a 30 s beat-cut edit with 20 cuts about 8 s, a 3-paragraph narrated video about 35 s, a 10 s caption job about 10 s. An explainer with text slides takes about 1 s of work per second of video; each new picture adds about 30 s, so a 10-minute explainer with a picture per scene is roughly 45-75 minutes.

**Background queue:** long jobs are JSON files in `~/.local/state/jarvis-studio/queue` (or `JARVIS_STUDIO_STATE`); the MCP server runs them one at a time, oldest first, even with no chat open. A tool call returns a job id straight away instead of holding the connection. Jobs that were running when the server restarted are marked interrupted; run them again and finished scenes are reused. Explainer projects live in `~/Videos/Jarvis/explainers/<title>/` (`project.json` is the plan; `cache/` holds the scene pieces).

**Camera:** OBS starts with an empty scene. Run `jarvis-studio obs-scene` (or **Set up camera scene** in the dashboard) once to add a "Jarvis Performance" scene with `/dev/video0` and the default microphone. It turns the camera on.

**Whisper:** Lemonade's Vulkan build of whisper.cpp crashes on this machine, so jarvis-studio loads `Whisper-Small` with the CPU backend (`JARVIS_WHISPER_BACKEND=cpu`).

## Book studio

Novels, nonfiction ebooks, children's picture books and puzzle books. Hermes writes the words with the `book_*` tools; the studio keeps the project, makes the puzzles, draws picture-book pages and builds the files. Projects live in `~/Documents/Jarvis/books/<title>/` (`JARVIS_BOOKS_OUT`).

- **Plan, then approve:** `book_new` → `book_plan` (story bible / ebook plan / character sheet, and an outline) → the user approves the outline (in chat, or **Approve outline** on the dashboard). `book_write` refuses until then.
- **One chapter at a time:** `book_context` gives Hermes the plan, characters, outline, continuity notes and the end of the previous chapter, trimmed to about 22,000 characters so a small context window is enough. `book_write` saves the chapter with one or two lines of continuity notes; long chapters are saved in parts with `append=True`. Hermes calls these tools through its `tool_call` bridge, where a chapter full of quotes can break the JSON, so it writes the chapter to `~/ods/data/hermes/books-inbox/` (its `/opt/data/books-inbox/`) with `write_file` and passes `text_file=` instead; only that folder is read.
- **Picture books:** one exact look sentence per character and one style line go into every page's picture prompt, with the same seed, through SD-Turbo (about 30 s a page, upscaled to 2400 px). Full-bleed pages with the text on a panel.
- **Puzzle books:** word search, sudoku, logic grids and whodunit cases are made in code (`studio/books/puzzles.py`). Sudoku cells are removed only while the puzzle has one solution; for logic grids and cases a solver keeps adding true clues until exactly one answer is left, then drops every clue that isn't needed. Answer key at the back; tool replies never show the answers.
- **Files (`book_build`, a background job):** a print-ready PDF (also the KDP manuscript: trim size, mirrored margins with KDP's inside margin for the real page count, page numbers, a contents page with page numbers for ebooks, padded to 24 pages), an EPUB 3, a Markdown manuscript, and in `kdp/` the wraparound paperback cover (spine width from page count and paper, spine text from 80 pages, barcode area left clear), a 1600x2560 ebook cover and `listing.md` (description in KDP's allowed HTML, 7 keywords, 3 categories, print settings). PDFs are printed by headless Google Chrome; page counts come from `pdfinfo`.
- **Dashboard → Book studio:** start a book, read and edit the plan and outline, approve it, read or fix any chapter, see page pictures, add puzzles, build and open the files.

Hermes tools: `book_new`, `book_plan`, `book_context`, `book_write`, `book_puzzles`, `book_listing`, `book_build`, `book_status`. Nothing is uploaded to KDP; the user publishes.

## Dashboard

Open **http://127.0.0.1:7722**, or **Jarvis Studio** in the app menu. It runs as the `jarvis-studio-dashboard` user service and only answers on this computer.

- **Status bar**: StemDeck, the Hermes MCP server, the Hermes container, Lemonade, Guitarix, OBS, Audacity, Ardour, playback, AI models and free disk space.
- **Library**: every song in StemDeck with key, BPM and length. Pick one to see how present each part is. **Add a song** splits a file or link.
- **Music / Video / Photo / Book studio** switch. Music studio tabs, in order: **Create** (ACE-Step songs, MOSS-TTS speech), **Stems** (a mixer for the selected song: play every part in sync, mute/solo, set levels, save the mix to `~/Music/Jarvis/mixes` or export WAV stems to `~/Music/Jarvis/stems`), **DAW**, **AI remix**, **Practice**, **Karaoke** and **Sheet**. Video studio has the five video workflows plus **Captions**. Click a part to mute it, again to solo it.
- **Rigs**: open Guitarix and switch presets; open OBS, change scene, start or stop recording (asks first).
- **Jobs**: workflows run in the background (two at a time) with results and file buttons.
- **Outputs**: recent files in `~/Music/Jarvis` and `~/Videos/Jarvis`. Preview audio, watch videos in a pop-up player, play on the computer speakers, open PDFs, thumbnails and timelines, or show in the folder.
- **Activity**: the shared audit log, so you see what Hermes did as well as the dashboard.
- **Photo studio** (the third mode, beside Music and Video): a gallery of AI pictures and your own photos in `~/Music/Jarvis/images`, with search and filters. **Make a picture** runs `studio_make_image` (SD-Turbo via Lemonade). **Add photos** takes drag-and-drop uploads or copies a file or folder into `images/my-photos`. Pick a picture to rename it, open it in GIMP or Krita, make variations from its prompt and seed, use it as a music-video background, AI re-production cover, narrated slides or beat-cut folder, or move it to the Trash.

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
| Plugins | LSP Plugins and DrumGizmo (Ubuntu Studio packages); Surge XT 1.3.4 installed per user from its GitHub release (`~/.lv2`, `~/.vst3`, `~/.clap`, data in `~/.local/share/surge-xt`); DrumGizmo kit DRSKit in `~/Music/DrumGizmo/DRSKit`, set as DrumGizmo's default kit in `~/.config/drumgizmo/drumgizmo.conf` |

## Known limits

- **Book pictures** come from SD-Turbo at 512 px, upscaled to 2400 px: fine on screen and in ebooks, soft in print at 8.5 in. Use your own pictures (`image=` per page) for a sharper print book. SD-Turbo sometimes adds fake lettering; draw that page again with `new_picture=True`.
- **Chapters don't force a right-hand page** in the print PDF (Chrome ignores `break-before: right`); each chapter starts on a new page.

- **Kdenlive** opens beat-cut edits through **File > Import > OpenTimelineIO**. The `.otio` files are checked with the OpenTimelineIO library, but the Kdenlive import itself hasn't been tried yet.
- **AI video generation** isn't practical here: there's no NVIDIA GPU. The video tools edit, compose and caption your own footage and the studio's audio.
- **Ardour** can't import audio without its window open. `daw` creates the session with the stems inside it and installs a Lua action. In Ardour, run **Edit > Lua Scripts > Script Manager > "Jarvis: Import StemDeck stems"** once; assign it to an action button so later imports take one click. The same action adds a production setup: an LSP Parametric EQ and LSP Compressor on every audio track and an LSP Limiter on the master (all added switched off, so the sound is unchanged until you turn them on), plus a "Synth (Surge XT)" and a "Drums (DrumGizmo)" MIDI track. Running it again adds nothing twice. Sessions are created with `ardour9-new_session`, so they have a stereo master bus.
- **OBS** won't record until its first-run Auto-Configuration Wizard has been finished or cancelled once. Add a camera or screen source to a scene for Performance videos.
- **Sheet music** is an automatic transcription, so treat it as a draft. MuseScore 3 puts each part on a piano grand staff. MuseScore 4 engraves better.
- **Song sections** (chorus, verse) depend on StemDeck's experimental detection. If a song has none, use `--start` and `--end` in seconds.
- **Mixxx 2.5** can't load stem files. Mixxx 2.6 adds that.

## Project NOMAD AI relay (optional)

`scripts/nomad_ai_relay.py` (user unit `nomad-ai-relay.service`) lets Project NOMAD's AI chat use the same OpenRouter model as Hermes. NOMAD can't send an API key, so the relay listens only on NOMAD's Docker network gateway (`172.18.0.1:8765`), adds `OPENROUTER_API_KEY` from Hermes's `.env` and pins the model to Hermes's `model.default`. In NOMAD, the remote AI URL is `http://172.18.0.1:8765` (set via `POST /api/ollama/configure-remote`). Install: `sed "s#@HERE@#$PWD#g" nomad-ai-relay.service > ~/.config/systemd/user/nomad-ai-relay.service && systemctl --user enable --now nomad-ai-relay`.

