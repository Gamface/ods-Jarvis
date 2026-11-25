"""jarvis-studio: MCP server giving ODS Hermes the music studio on this computer.

StemDeck (stems), Guitarix (amp sim), OBS (recording), Audacity and Ardour
(sessions), ACE-Step (AI music), MOSS-TTS-Nano (voice), basic-pitch + MuseScore
(sheet music). Everything stays on this machine; outputs go to ~/Music/Jarvis.
Requests need the bearer token in JARVIS_STUDIO_TOKEN.
"""

import datetime
import functools
import hmac
import json
import os
from pathlib import Path

import uvicorn
from mcp.server.fastmcp import FastMCP

from studio import audio, guitarix, obs
from studio import stemdeck as sd
from studio import workflows as wf

TOKEN = os.environ["JARVIS_STUDIO_TOKEN"]
BIND = os.environ.get("JARVIS_STUDIO_BIND", "127.0.0.1")
PORT = int(os.environ.get("JARVIS_STUDIO_PORT", "7721"))
AUDIT = Path(os.environ.get("JARVIS_AUDIT_LOG", "~/ods/data/jarvis-studio/audit.log")).expanduser()

mcp = FastMCP("jarvis-studio", host=BIND, port=PORT, stateless_http=True)


def audit(event: str, **fields) -> None:
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": datetime.datetime.now().isoformat(timespec="seconds"), "event": event, **fields}
    with AUDIT.open("a") as f:
        f.write(json.dumps(rec, default=str) + "\n")


def guarded(name):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            audit(name, args=kw)
            try:
                return fn(*a, **kw)
            except Exception as e:  # report, don't crash the tool call
                audit(name + "_error", error=str(e))
                return f"FAILED: {e}"
        return wrapper
    return deco


@mcp.tool()
@guarded("library")
def studio_library() -> str:
    """List songs already split in StemDeck (id, title, key, BPM)."""
    return sd.dump(sd.list_songs())


@mcp.tool()
@guarded("split")
def studio_split(source: str) -> str:
    """Split a song into stems with StemDeck. `source` is a file path on this
    computer or a YouTube/SoundCloud URL the user has the right to use. On CPU
    this takes about 1-2x the song's length."""
    job = sd.wait(sd.submit(source))
    return (f"Split '{job['title']}' ({job.get('key')}, {job.get('bpm')} BPM). "
            f"Parts: {', '.join(sd.stem_names(job))}. Sections: "
            + ", ".join(str(s.get('label')) for s in sd.sections(job)))


@mcp.tool()
@guarded("practice")
def studio_practice(song: str, mute: list[str] | None = None, solo: list[str] | None = None,
                    section: str | None = None, speed: float = 1.0, semitones: int = 0,
                    click: bool = False, count_in: int = 0, loop: bool = True,
                    guitar_preset: str | None = None) -> str:
    """Make and play a practice track. song: title, id, file path or URL.
    mute/solo: parts from vocals, drums, bass, guitar, piano, other.
    section: e.g. 'chorus' or 'verse 2'. speed: 0.5-1.0 slows down (pitch kept).
    semitones: -6..6 key change. guitar_preset: e.g. 'clean' to open Guitarix."""
    return wf.practice(song, mute, solo, section, None, None, speed, semitones, click,
                       count_in, loop, True, guitar_preset)


@mcp.tool()
@guarded("stop")
def studio_stop() -> str:
    """Stop the practice/karaoke audio Jarvis is playing."""
    return audio.stop()


@mcp.tool()
@guarded("to_daw")
def studio_to_daw(song: str, daw: str = "both") -> str:
    """Put a song's stems into a DAW: daw = 'audacity', 'ardour' or 'both'."""
    return wf.to_daw(song, daw)


@mcp.tool()
@guarded("karaoke")
def studio_karaoke(song: str, keep_backing_vocals: bool = False, semitones: int = 0,
                   record: bool = False) -> str:
    """Karaoke: vocal-free backing track, lyrics as .lrc and a FreeShow text file,
    StemDeck opened for scrolling lyrics. record=True also starts OBS recording
    (it may turn on the camera, so confirm with the user first)."""
    return wf.karaoke(song, keep_backing_vocals, semitones, record)


@mcp.tool()
@guarded("obs")
def studio_obs(action: str, scene: str | None = None) -> str:
    """Control OBS: action = start_app | record_start | record_stop | status | scene."""
    if action == "start_app":
        return obs.start()
    if action == "scene":
        return obs.set_scene(scene or "")
    return obs.record({"record_start": "start", "record_stop": "stop"}.get(action, "status"))


@mcp.tool()
@guarded("guitarix")
def studio_guitar(preset: str | None = None, list_presets: bool = False) -> str:
    """Open Guitarix (amp simulator) and optionally load a preset by name."""
    msg = guitarix.start()
    if list_presets:
        return msg + "\n" + sd.dump(guitarix.presets())
    return msg + ("\n" + guitarix.set_preset(preset) if preset else "")


@mcp.tool()
@guarded("ai_produce")
def studio_ai_produce(song: str, style: str, keep: list[str] | None = None,
                      strength: float = 0.45, intro_text: str | None = None,
                      cover_image: str | None = None) -> str:
    """Re-imagine a song: keep some parts (default vocals), have ACE-Step make a new
    backing in `style` (e.g. 'lofi hip hop, warm keys'), optionally add a spoken
    intro (MOSS-TTS), master to -14 LUFS and render a video. Slow on CPU:
    roughly 2-3x the song length."""
    return wf.ai_produce(song, style, keep, strength, intro_text, None, cover_image)


@mcp.tool()
@guarded("make_music")
def studio_make_music(caption: str, title: str | None = None, lyrics: str | None = None,
                      duration: float = 60, bpm: int | None = None, key: str | None = None,
                      language: str | None = None, seed: int | None = None,
                      add_to_library: bool = True, play: bool = False) -> str:
    """Write a new song with ACE-Step 1.5 on this computer. caption: genre, mood,
    instruments and voice (e.g. 'soulful R&B, warm Rhodes, female vocals, 90s').
    lyrics: with [verse]/[chorus]/[bridge] tags for a sung song; leave empty for an
    instrumental. bpm, key (e.g. 'A minor'), language ('en', 'es'...) are optional.
    add_to_library splits it in StemDeck so practice, karaoke and video tools can use it.
    Takes about 2x the length on CPU (plus the split): say so before starting."""
    return wf.make_song(caption, title, lyrics, duration, bpm, key, language, seed,
                        add_to_library, play)


@mcp.tool()
@guarded("make_image")
def studio_make_image(prompt: str, title: str | None = None, size: str = "512x512",
                      model: str | None = None, seed: int | None = None) -> str:
    """Make a picture (album cover, poster, thumbnail, concept art) on this computer with
    Stable Diffusion through Lemonade. prompt: subject, style, mood, colours, e.g.
    'album cover, snowy mountain town at dusk, warm lights, cinematic'. size: 512x512
    (default), 768x512, 512x768 or 768x768. model: SD-Turbo (default, fast) or SDXL-Turbo
    if downloaded. Takes about 20 s at 512x512 (about 50 s at 768x768) on the Intel
    graphics via Vulkan. Saved to ~/Music/Jarvis/images and shown in
    the dashboard's Studio box. Waits if a song is being made."""
    from studio import images
    return images.make_image(prompt, title, size, model, seed)


@mcp.tool()
@guarded("make_sfx")
def studio_make_sfx(prompt: str, title: str | None = None, duration: float = 5,
                    seed: int | None = None) -> str:
    """Make a sound effect from a description, on this computer (ThinkSound through Lemonade):
    e.g. 'heavy wooden door creaking open in a stone hall', 'crowd applause in a small
    theatre', 'rain on a tin roof with distant thunder'. Good for stage plays, videos and
    podcasts. duration: 1-30 seconds (default 5). Saved as WAV to ~/Music/Jarvis/sfx and shown
    in the dashboard's Studio box. Waits if a song or picture is being made."""
    from studio import sfx
    return sfx.make_sfx(prompt, title, duration, seed)


@mcp.tool()
@guarded("speak")
def studio_speak(text: str, voice_wav: str | None = None, play: bool = True) -> str:
    """Say text with MOSS-TTS-Nano (optionally cloning the voice in voice_wav)."""
    from studio.config import out_dir
    dest = out_dir("speech") / f"speech-{datetime.datetime.now():%Y%m%d-%H%M%S}.wav"
    wf.speak(text, dest, voice_wav)
    return f"Saved {dest}" + ("\n" + audio.play(dest) if play else "")


@mcp.tool()
@guarded("sheet_music")
def studio_sheet_music(song: str, parts: list[str] | None = None) -> str:
    """Transcribe parts of a song to MIDI (basic-pitch) and engrave PDFs (MuseScore)."""
    return wf.sheet_music(song, parts)


@mcp.tool()
@guarded("workspace")
def studio_workspace(action: str, path: str = "", content: str | None = None,
                     slug: str | None = None, stage: str | None = None, title: str | None = None,
                     note: str = "") -> str:
    """The ~/Studio workspace (pipeline, research, community, clients, reports).
    action: status | tree | read | write | append | new_item | move.
    path is relative to ~/Studio. new_item needs title; move needs slug and stage
    (1-ideas, 2-scripts, 3-production, 4-review, 5-published, rejected). Nothing is deleted."""
    from studio import workspace as ws
    if action == "status":
        return ws.status()
    if action == "tree":
        return ws.tree(path)
    if action == "read":
        return ws.read(path)
    if action in ("write", "append"):
        return ws.write(path, content or "", append=action == "append")
    if action == "new_item":
        return ws.new_item(title or "untitled", stage or "1-ideas")
    if action == "move":
        return ws.move(slug or "", stage or "", note)
    raise ValueError("action must be status, tree, read, write, append, new_item or move")


# --- Video studio ----------------------------------------------------------
from studio.video import workflows as vwf  # noqa: E402


@mcp.tool()
@guarded("video_music_video")
def video_music_video(song: str, style: str = "stems", aspect: str = "16:9",
                      title: str | None = None, subtitle: str | None = None,
                      background_image: str | None = None, section: str | None = None,
                      lyrics: bool = True, audio_file: str | None = None) -> str:
    """Music video for a StemDeck song. style: stems (a moving waveform lane per part),
    spectrum (bars) or waves. aspect: 16:9, 9:16 or 1:1. section: e.g. 'chorus' for part of
    the song. audio_file: use another mix as the soundtrack (e.g. an AI production master.wav).
    About 20 s per 45 s of song."""
    return vwf.music_video(song, style, aspect, title, subtitle, background_image, lyrics,
                           section, None, None, audio_file)


@mcp.tool()
@guarded("video_perform_start")
def video_perform_start(song: str, mode: str = "karaoke", semitones: int = 0,
                        scene: str | None = None) -> str:
    """Start a performance take: OBS starts recording, then the backing plays on the speakers.
    mode: karaoke (no lead vocal), full, or no-<part> (e.g. no-guitar). This records the
    camera/screen, so confirm with the user first. Finish with video_perform_finish."""
    return vwf.perform_start(song, mode, semitones, scene)


@mcp.tool()
@guarded("video_perform_finish")
def video_perform_finish(lyrics: bool = True) -> str:
    """Stop the take, then line the recording up with the studio backing, mix, add lyrics."""
    return vwf.perform_finish(lyrics)


@mcp.tool()
@guarded("video_performance")
def video_performance(recording: str, song: str, lyrics: bool = True) -> str:
    """Turn an existing recording (phone or OBS video of you playing/singing along to a song)
    into a finished video: auto-aligned studio backing, levelled audio, lyrics, title."""
    return vwf.performance(recording, song, None, lyrics)


@mcp.tool()
@guarded("video_obs_scene")
def video_obs_scene(camera: str = "/dev/video0", mic: bool = True) -> str:
    """Create an OBS 'Jarvis Performance' scene with the webcam and microphone. Turns the
    camera on: only when the user asks."""
    return obs.start() + "\n" + obs.setup_performance_scene(camera, mic)


@mcp.tool()
@guarded("video_auto_edit")
def video_auto_edit(clips: str, song: str, aspect: str = "16:9", pace: str = "medium",
                    title: str | None = None, section: str | None = None,
                    max_seconds: float | None = None, lyrics: bool = False) -> str:
    """Cut a folder (or comma-separated list) of video clips and photos to the beat of a song
    (StemDeck title or an audio file). pace: slow, medium or fast. Also writes an
    OpenTimelineIO timeline to fine-tune in Kdenlive."""
    return vwf.auto_edit(clips, song, aspect, pace, title, section, None, max_seconds, lyrics)


@mcp.tool()
@guarded("video_shorts")
def video_shorts(source: str, platforms: list[str] | None = None, seconds: float = 30,
                 start: float | None = None, caption: str = "auto", song: str | None = None,
                 title: str | None = None) -> str:
    """Social clips from a video file or a StemDeck song. platforms: shorts, reels, tiktok
    (9:16), square, instagram (1:1), youtube (16:9). Picks the chorus or loudest part unless
    start is given. caption: auto, lyrics, speech or none. Makes thumbnails too. Never posts."""
    return vwf.shorts(source, platforms, seconds, start, None, caption, song, title)


@mcp.tool()
@guarded("video_narrated")
def video_narrated(script: str, title: str, images: str | None = None,
                   voice_wav: str | None = None, music: str | None = None,
                   aspect: str = "16:9", voice_speed: float = 1.0) -> str:
    """Narrated video: script text (blank line between paragraphs) or a .txt path is read by
    MOSS-TTS; one slide per paragraph from your photos/clips folder (or generated text
    slides); captions; optional music bed (StemDeck song or audio file), ducked under the voice.
    images: a folder, or exact files comma-separated in slide order. voice_speed: 1.0 normal,
    1.15 to 1.3 for a punchy ad, 0.85 calmer (0.5 to 2.0, pitch unchanged)."""
    return vwf.narrated(script, title, images, voice_wav, music, aspect, voice_speed=voice_speed)


@mcp.tool()
@guarded("video_captions")
def video_captions(video: str, style: str = "bottom", burn: bool = True,
                   language: str | None = None) -> str:
    """Subtitles for any video with Whisper (local, via Lemonade): an .srt file, plus a copy
    with captions in the picture if burn=True. style: bottom or pop (big, short-form)."""
    return vwf.captions(video, style, burn, language)


# --- long explainers and the background queue ---------------------------------
from studio import jobqueue  # noqa: E402
from studio.video import explainer  # noqa: E402  (registers the "explainer" job)
from studio.video import styles as vstyles  # noqa: E402


def _estimate(proj: dict) -> str:
    n = len(proj["scenes"])
    pics = sum(1 for s in proj["scenes"] if not s.get("image") and (s.get("picture") or proj.get("style")))
    words = sum(len(s["say"].split()) for s in proj["scenes"])
    mins = (words / 150 * 0.7 * 60 + pics * 30 + n * 8 + words / 150 * 60 * 0.6) / 60
    return (f"{n} scenes, about {words / 150 / proj['voice_speed']:.1f} min of narration, "
            f"{pics} new pictures to make; roughly {max(1, round(mins))} min of work if nothing is cached")


@mcp.tool()
@guarded("video_explainer")
def video_explainer(title: str, script: str | None = None, scenes: list | str | None = None,
                    aspect: str = "16:9", voice: str | None = None, voice_speed: float = 1.0,
                    music: str | None = None, style: str | None = None, seed: int | None = None,
                    intro: bool = True, captions: str | None = None, transition: str | None = None,
                    motion: str | None = None, sfx: bool = True) -> str:
    """Long explainer video (any length), made in the background. Returns a job id at once;
    check it with studio_jobs. Best: pass scenes, a list of {"say": narration (1-3 sentences),
    "picture": what to show (subject only; the style adds the look; no text), "chapter": section
    title on its first scene (card + YouTube chapter), optional "text" (slide headline when no
    picture), "image" (existing picture/clip path), "motion" (zoom-in, zoom-out, pan-left,
    pan-right, pan-up, pan-down, still), "voice" (another voice for dialogue), "pause" (extra
    seconds after)}. Or pass script: plain text, "# Heading" lines start chapters.
    style: a preset (see video_styles: cinematic, documentary, 3d-cartoon, anime, storybook,
    comic, clay, sketch, flat, retro, whiteboard, social) that sets picture look, colour grade,
    captions, transitions and motion; or free text for a custom picture look; none = text slides.
    captions: highlight (word by word), pop, box, subtle, none. transition: cut, dip, flash,
    whoosh. motion: zoom, pan, mixed, still. sfx: whoosh on chapter cards.
    voice: Kokoro (bm_george default, bf_emma, am_michael, af_heart...), "moss", or a WAV.
    music: StemDeck song, audio file, or "auto" to write a matching instrumental (slow; needs
    about 14 GB free). Same title again reuses every unchanged scene."""
    proj = explainer.create(title, script, scenes, aspect, voice, voice_speed, music, style, seed, intro,
                            captions, transition, motion, sfx)
    job = jobqueue.add("explainer", proj["title"], {"title": proj["title"]})
    return (f"Queued explainer '{proj['title']}': {_estimate(proj)}.\n{jobqueue.summary(job)}\n"
            "Check progress with studio_jobs(job_id). It appears in the dashboards when done.")


@mcp.tool()
@guarded("video_explainer_scene")
def video_explainer_scene(title: str, scene: int | None = None, say: str | None = None,
                          picture: str | None = None, image: str | None = None, text: str | None = None,
                          chapter: str | None = None, new_picture: bool = False, remove: bool = False,
                          rebuild: bool = True, motion: str | None = None, voice: str | None = None,
                          pause: float | None = None) -> str:
    """See or fix one scene of an explainer. Without scene: lists every scene (number, words,
    visual). With scene (1-based): change its say/picture/image/text/chapter ("" clears a
    field), new_picture=True for a different picture from the same prompt, remove=True to drop
    it. rebuild=True queues a rebuild that only remakes what changed."""
    if scene is None:
        return explainer.describe(title)
    explainer.update_scene(title, scene, say, picture, image, text, chapter, new_picture, remove,
                           motion, voice, pause)
    if not rebuild:
        return f"Scene {scene} updated (not rebuilt yet).\n" + explainer.describe(title)
    job = jobqueue.add("explainer", title, {"title": explainer._load(title)["title"]})
    return f"Scene {scene} updated; rebuilding only what changed.\n{jobqueue.summary(job)}"


@mcp.tool()
@guarded("video_styles")
def video_styles() -> str:
    """The video style presets for video_explainer(style=...), with their caption look,
    transitions and camera motion."""
    return vstyles.catalog()


@mcp.tool()
@guarded("jobs")
def studio_jobs(job_id: str | None = None) -> str:
    """Background job status. With job_id: that job (progress, step, result or problem).
    Without: the queue and recent jobs, newest first."""
    if job_id:
        return jobqueue.summary(jobqueue.get(job_id))
    jobs = jobqueue.list_jobs(15)
    if not jobs:
        return "No background jobs yet."
    return "\n".join(jobqueue.summary(j).split("\n")[0] for j in jobs)


@mcp.tool()
@guarded("job_cancel")
def studio_job_cancel(job_id: str, run_again: bool = False) -> str:
    """Cancel a queued or running job (a running one stops after its current step; finished
    scenes are kept). run_again=True puts a failed or cancelled job back in the queue."""
    job = jobqueue.retry(job_id) if run_again else jobqueue.cancel(job_id)
    return jobqueue.summary(job)


# --- books ----------------------------------------------------------------------
from studio.books import build as book_build_mod  # noqa: E402  (registers the "book" job)
from studio.books import project as books  # noqa: E402
from studio.books import puzzles as book_puzzles_mod  # noqa: E402


@mcp.tool()
@guarded("book_new")
def book_new(title: str, kind: str = "novel", brief: str = "", audience: str = "", author: str = "",
             trim: str | None = None, paper: str | None = None, style: str | None = None,
             target_words: int | None = None, replace: bool = False) -> str:
    """Start a book project. kind: novel, ebook (nonfiction), picture (children's picture book) or
    puzzle (puzzle book). brief: what it's about, in the user's words. trim: 5x8, 5.5x8.5, 6x9
    (novel/ebook default), 7x10, 8x10, 8x8, 8.5x8.5 (picture default), 8.5x11 (puzzle default).
    paper: white, cream (novels), color (picture books). style: picture look for picture books.
    target_words: words per chapter (novel 2500, ebook 1500) or per page (picture 40).
    Next: book_plan (plan + outline), then the user approves the outline."""
    b = books.create(title, kind, brief, audience, author, trim, paper, style, target_words, replace)
    nxt = ("Next: add puzzles with book_puzzles." if b["kind"] == "puzzle" else
           "Next: write the plan (story bible / ebook plan / character sheet) and the outline with book_plan, "
           "then show the outline to the user for approval.")
    return f"Started '{b['title']}' ({books.KINDS[b['kind']]['label']}, {b['trim']} in). {nxt}"


@mcp.tool()
@guarded("book_plan")
def book_plan(title: str, plan: str | None = None, outline: list | str | None = None,
              characters: dict | str | None = None, style: str | None = None, cover: str | None = None,
              dedication: str | None = None, author: str | None = None, subtitle: str | None = None,
              target_words: int | None = None, approve: bool = False) -> str:
    """Save the book's plan and outline. plan: the story bible (novel: premise, characters, setting,
    tone, ending), ebook plan (reader, promise, key ideas) or theme sheet. outline: a list of
    {"title", "summary"} per chapter (picture books: one per page, with "picture"). characters:
    {"Name": "one exact sentence of how they look"} (used in every picture). cover: what the front
    cover picture shows. A changed outline needs approval again. approve=True ONLY when the user
    has said the outline is OK (they can also approve on the dashboard's Books tab)."""
    b = books.set_plan(title, plan, outline, characters, style, cover, dedication, author, subtitle,
                       target_words, approve)
    rows = books.chapter_rows(b)
    head = (f"Saved. Outline ({len(rows)}):\n" + "\n".join(f"{r['n']}. {r['title']} — {r['summary']}" for r in rows)
            if rows else "Saved the plan. Add the outline next.")
    tail = ("\nApproved: write chapter 1 (book_context, then book_write)." if b["approved"] else
            "\nShow this outline to the user and ask them to approve it or ask for changes. Nothing is written "
            "until it's approved." if rows else "")
    return head + tail


@mcp.tool()
@guarded("book_context")
def book_context(title: str, chapter: int | None = None) -> str:
    """Everything needed to write the next chapter or page, trimmed to fit: plan, characters,
    outline, continuity notes, the end of the previous chapter and the task. Without chapter it
    picks the first one that isn't finished. Call it before each book_write."""
    return books.context(title, chapter)


@mcp.tool()
@guarded("book_write")
def book_write(title: str, chapter: int | None = None, text: str = "", notes: str | None = None,
               append: bool = False, chapter_title: str | None = None, picture: str | None = None,
               image: str | None = None, new_picture: bool = False, pages: list | None = None) -> str:
    """Save a chapter (or picture-book page) after the outline is approved. text: plain text,
    blank line between paragraphs, no chapter heading (## for sub-headings in ebooks). notes:
    one or two lines of continuity facts for later chapters. append=True adds to what's there
    (for long chapters written in parts); without it the text replaces the chapter. Picture
    books: picture = one sentence of what the page shows, using the characters' names (no words
    in pictures); image = an existing picture file instead; new_picture=True draws it again; or
    pages = [{"text", "picture"}, ...] for every page at once."""
    if pages:
        b = books.write_pages(title, pages)
        return f"Saved {len(pages)} pages.\n" + books.describe(title)
    if chapter is None:
        raise ValueError("Say which chapter (1-based).")
    b = books.write(title, chapter, text, notes, append, chapter_title, picture, image, new_picture)
    c = b["chapters"][chapter - 1]
    target = b["target_words"]
    left = [r["n"] for r in books.chapter_rows(b) if r["words"] < max(10, target * 0.7)]
    msg = f"Saved {'page' if b['kind'] == 'picture' else 'chapter'} {chapter}: {c['words']} words"
    if b["kind"] != "picture":
        msg += f" (target {target})"
        if c["words"] < target * 0.7:
            msg += ". It's short: call book_context and keep going with append=True"
    msg += "." + (f" Still to write: {', '.join(map(str, left[:15]))}." if left else
                  " Everything is written: book_listing for the KDP listing, then book_build.")
    return msg


@mcp.tool()
@guarded("book_puzzles")
def book_puzzles(title: str, type: str | None = None, count: int = 1, difficulty: str = "medium",
                 words: list | str | None = None, names: list | None = None, categories: list | None = None,
                 suspects: list | None = None, weapons: list | None = None, rooms: list | None = None,
                 titles: list | str | None = None, story_for: int | None = None, story: str | None = None,
                 remove: int | None = None) -> str:
    """Add puzzles to a puzzle book; they're made in code with exactly one answer and an answer
    key at the back. type: wordsearch, sudoku, logic (logic grid) or whodunit (mystery case).
    difficulty: easy, medium, hard. count: how many. wordsearch needs words: one list shared by
    all (12-18 picked per puzzle) or a list of lists, one per puzzle (themes). logic: optional
    names and categories [{"name": "Pet", "values": [...], "verb": "owns the {}"}]. whodunit:
    optional suspects, weapons, rooms. titles: puzzle titles. story_for=N with story="..." adds
    a short story or case file above puzzle N (the reply shows the setup, never the answer).
    remove=N drops puzzle N."""
    b, made = books.add_puzzles(title, type, count, difficulty, words, names, categories, suspects,
                                weapons, rooms, titles, story_for, story, remove)
    lines = [f"{p['n']}. " + book_puzzles_mod.public_view(p) for p in made]
    head = (f"Added {len(made)} puzzles" if made else "Updated") + f"; the book has {len(b['puzzles'])}."
    return head + ("\n" + "\n".join(lines) if lines else "") + "\nBuild with book_build when ready."


@mcp.tool()
@guarded("book_listing")
def book_listing(title: str, description: str | None = None, keywords: list | str | None = None,
                 categories: list | str | None = None, subtitle: str | None = None,
                 back_text: str | None = None) -> str:
    """The Amazon KDP listing kit: description (plain text or KDP's simple HTML: b, i, u, em,
    strong, br, p, h4-h6, ul, ol, li; up to 4000 characters), 7 keywords (phrases buyers type; no
    'bestseller', 'free' or 'Amazon'), up to 3 categories, a subtitle and back-cover text (about
    150 words for 6x9). Saved into kdp/listing.md and printed on the cover at the next build."""
    b, notes = books.set_listing(title, description, keywords, categories, subtitle, back_text)
    L = b["listing"]
    return (f"Listing saved: {len(L.get('description', ''))} character description, "
            f"{len(L.get('keywords', []))} keywords, {len(L.get('categories', []))} categories."
            + ("\n" + "\n".join(notes) if notes else ""))


@mcp.tool()
@guarded("book_build")
def book_build(title: str, formats: str | None = None) -> str:
    """Make the book's files in the background (returns a job id; check with studio_jobs):
    epub, pdf (print-ready interior, also the KDP manuscript), kdp (wraparound paperback cover
    sized to the page count, ebook cover and listing kit). Default: novels and ebooks epub+pdf+kdp,
    picture books pdf+kdp+epub (draws the pictures first, about 30 s each), puzzle books pdf+kdp.
    Unwritten chapters make a draft build. Files appear in the dashboard's Books tab."""
    b = books.load(title)
    fm = book_build_mod.formats_for(b, formats)
    job = jobqueue.add("book", b["title"], {"title": b["title"], "formats": fm})
    return f"Queued the build of '{b['title']}' ({', '.join(fm)}).\n{jobqueue.summary(job)}"


@mcp.tool()
@guarded("book_status")
def book_status(title: str | None = None) -> str:
    """Without title: every book project (kind, status, size). With title: its plan state,
    outline/chapters with word counts (or puzzles), approval, and the files built so far."""
    if title:
        return books.describe(title)
    rows = books.list_books()
    if not rows:
        return "No books yet. Start one with book_new."
    return "\n".join(f"{r['title']} — {r['label']}, {r['status_text']}, "
                     + (f"{r['puzzles']} puzzles" if r["kind"] == "puzzle" else f"{r['chapters']} parts, {r['words']} words")
                     for r in rows)


class BearerAuth:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            if scope["path"] == "/health":
                await send({"type": "http.response.start", "status": 200,
                            "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": b'{"status":"ok"}'})
                return
            given = dict(scope["headers"]).get(b"authorization", b"").decode()
            if not hmac.compare_digest(given, f"Bearer {TOKEN}"):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await self.app(scope, receive, send)


if __name__ == "__main__":
    jobqueue.start_worker()
    uvicorn.run(BearerAuth(mcp.streamable_http_app()), host=BIND, port=PORT)
