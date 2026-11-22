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
def studio_make_music(caption: str, duration: float = 30, lyrics: str | None = None) -> str:
    """Generate new music from a text description with ACE-Step 1.5 (CPU: ~2x duration)."""
    from studio.config import out_dir
    path = wf.generate_music(caption, out_dir("generated"), duration, lyrics)
    return f"Generated: {path}"


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
