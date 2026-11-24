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
                   aspect: str = "16:9") -> str:
    """Narrated video: script text (blank line between paragraphs) or a .txt path is read by
    MOSS-TTS; one slide per paragraph from your photos/clips folder (or generated text
    slides); captions; optional music bed (StemDeck song or audio file), ducked under the voice."""
    return vwf.narrated(script, title, images, voice_wav, music, aspect)


@mcp.tool()
@guarded("video_captions")
def video_captions(video: str, style: str = "bottom", burn: bool = True,
                   language: str | None = None) -> str:
    """Subtitles for any video with Whisper (local, via Lemonade): an .srt file, plus a copy
    with captions in the picture if burn=True. style: bottom or pop (big, short-form)."""
    return vwf.captions(video, style, burn, language)


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
    uvicorn.run(BearerAuth(mcp.streamable_http_app()), host=BIND, port=PORT)
