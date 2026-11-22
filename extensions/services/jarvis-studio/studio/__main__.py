"""Command line:  python -m studio <workflow> [options]   (see --help)"""

import argparse
import sys

from . import audio
from . import stemdeck as sd
from . import workflows as wf


def _list(v):
    return [x.strip() for x in v.split(",") if x.strip()] if v else None


VIDEO_CMDS = {"music-video", "perform-start", "perform-finish", "performance", "obs-scene",
              "edit", "shorts", "narrate", "captions"}


def video(a) -> str:
    from . import obs
    from .video import workflows as v
    if a.cmd == "music-video":
        return v.music_video(a.song, a.style, a.aspect, a.title, a.subtitle, a.background,
                             not a.no_lyrics, a.section, None, None, a.audio)
    if a.cmd == "perform-start":
        return v.perform_start(a.song, a.mode, a.semitones, a.scene)
    if a.cmd == "perform-finish":
        return v.perform_finish()
    if a.cmd == "performance":
        return v.performance(a.recording, a.song, a.backing, not a.no_lyrics)
    if a.cmd == "obs-scene":
        return obs.start() + "\n" + obs.setup_performance_scene(a.camera, not a.no_mic)
    if a.cmd == "edit":
        return v.auto_edit(a.clips, a.song, a.aspect, a.pace, a.title, a.section, None, a.max,
                           a.lyrics, None, a.bpm)
    if a.cmd == "shorts":
        return v.shorts(a.source, _list(a.platforms), a.seconds, a.start, None, a.caption, a.song, a.title)
    if a.cmd == "narrate":
        return v.narrated(a.script, a.title, a.images, a.voice, a.music, a.aspect)
    return v.captions(a.video, a.style, not a.no_burn, a.language)


def main() -> int:
    p = argparse.ArgumentParser(prog="jarvis-studio", description="Jarvis music and video studio workflows")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("library", help="list songs in StemDeck")
    s = sub.add_parser("split", help="split a file or URL into stems")
    s.add_argument("source")

    s = sub.add_parser("practice", help="1: make and play a practice track")
    s.add_argument("song")
    s.add_argument("--mute"), s.add_argument("--solo"), s.add_argument("--section")
    s.add_argument("--start", type=float), s.add_argument("--end", type=float)
    s.add_argument("--speed", type=float, default=1.0)
    s.add_argument("--semitones", type=int, default=0)
    s.add_argument("--click", action="store_true")
    s.add_argument("--count-in", type=int, default=0)
    s.add_argument("--no-loop", action="store_true"), s.add_argument("--no-play", action="store_true")
    s.add_argument("--guitar-preset")

    sub.add_parser("stop", help="stop playback")

    s = sub.add_parser("daw", help="2: stems into Audacity and/or Ardour")
    s.add_argument("song"), s.add_argument("--daw", default="both", choices=["audacity", "ardour", "both"])
    s.add_argument("--no-gui", action="store_true")

    s = sub.add_parser("karaoke", help="3: karaoke backing + lyrics (+ OBS recording)")
    s.add_argument("song"), s.add_argument("--backing-vocals", action="store_true")
    s.add_argument("--semitones", type=int, default=0)
    s.add_argument("--record", action="store_true"), s.add_argument("--no-ui", action="store_true")

    s = sub.add_parser("produce", help="4: AI re-production (ACE-Step + MOSS + video)")
    s.add_argument("song"), s.add_argument("style")
    s.add_argument("--keep"), s.add_argument("--strength", type=float, default=0.45)
    s.add_argument("--intro"), s.add_argument("--voice"), s.add_argument("--cover")
    s.add_argument("--no-video", action="store_true")

    s = sub.add_parser("sheet", help="5: sheet music PDFs and MIDI")
    s.add_argument("song"), s.add_argument("--parts"), s.add_argument("--no-pdf", action="store_true")

    s = sub.add_parser("make", help="write a new song with ACE-Step (adds it to the library)")
    s.add_argument("caption"), s.add_argument("--title"), s.add_argument("--lyrics", help="text or a .txt path")
    s.add_argument("--duration", type=float, default=60), s.add_argument("--bpm", type=int)
    s.add_argument("--key"), s.add_argument("--language"), s.add_argument("--seed", type=int)
    s.add_argument("--no-library", action="store_true"), s.add_argument("--play", action="store_true")

    s = sub.add_parser("status", help="~/Studio pipeline status")

    # Video studio
    s = sub.add_parser("music-video", help="V1: music video for a song")
    s.add_argument("song"), s.add_argument("--style", default="stems", choices=["stems", "spectrum", "waves"])
    s.add_argument("--aspect", default="16:9"), s.add_argument("--title"), s.add_argument("--subtitle")
    s.add_argument("--background"), s.add_argument("--section"), s.add_argument("--audio")
    s.add_argument("--no-lyrics", action="store_true")

    s = sub.add_parser("perform-start", help="V2: start an OBS take with the backing playing")
    s.add_argument("song"), s.add_argument("--mode", default="karaoke")
    s.add_argument("--semitones", type=int, default=0), s.add_argument("--scene")
    sub.add_parser("perform-finish", help="V2: stop the take and make the video")
    s = sub.add_parser("performance", help="V2: make a video from an existing recording")
    s.add_argument("recording"), s.add_argument("song"), s.add_argument("--backing")
    s.add_argument("--no-lyrics", action="store_true")
    s = sub.add_parser("obs-scene", help="create the OBS webcam + mic scene (turns the camera on)")
    s.add_argument("--camera", default="/dev/video0"), s.add_argument("--no-mic", action="store_true")

    s = sub.add_parser("edit", help="V3: beat-cut clips/photos to a song (+ Kdenlive OTIO)")
    s.add_argument("clips"), s.add_argument("song"), s.add_argument("--aspect", default="16:9")
    s.add_argument("--pace", default="medium", choices=["slow", "medium", "fast"])
    s.add_argument("--title"), s.add_argument("--section"), s.add_argument("--max", type=float)
    s.add_argument("--lyrics", action="store_true"), s.add_argument("--bpm", type=float)

    s = sub.add_parser("shorts", help="V4: social clips from a video or song")
    s.add_argument("source"), s.add_argument("--platforms", default="shorts,square")
    s.add_argument("--seconds", type=float, default=30), s.add_argument("--start", type=float)
    s.add_argument("--caption", default="auto"), s.add_argument("--song"), s.add_argument("--title")

    s = sub.add_parser("narrate", help="V5: narrated video from a script")
    s.add_argument("script", help="text or a .txt path"), s.add_argument("title")
    s.add_argument("--images"), s.add_argument("--voice"), s.add_argument("--music")
    s.add_argument("--aspect", default="16:9")

    s = sub.add_parser("captions", help="Whisper subtitles for a video")
    s.add_argument("video"), s.add_argument("--style", default="bottom", choices=["bottom", "pop"])
    s.add_argument("--no-burn", action="store_true"), s.add_argument("--language")

    a = p.parse_args()
    if a.cmd == "make":
        from pathlib import Path
        lyr = a.lyrics
        if lyr and "\n" not in lyr and Path(lyr).expanduser().is_file():
            lyr = Path(lyr).expanduser().read_text()
        print(wf.make_song(a.caption, a.title, lyr, a.duration, a.bpm, a.key, a.language, a.seed,
                           not a.no_library, a.play))
        return 0
    if a.cmd == "status":
        from . import workspace
        print(workspace.status())
        return 0
    if a.cmd in VIDEO_CMDS:
        print(video(a))
        return 0
    if a.cmd == "library":
        out = sd.dump(sd.list_songs())
    elif a.cmd == "split":
        job = sd.wait(sd.submit(a.source))
        out = f"{job['job_id']} {job['title']} {job.get('key')} {job.get('bpm')} BPM"
    elif a.cmd == "practice":
        out = wf.practice(a.song, _list(a.mute), _list(a.solo), a.section, a.start, a.end,
                          a.speed, a.semitones, a.click, a.count_in, not a.no_loop,
                          not a.no_play, a.guitar_preset)
    elif a.cmd == "stop":
        out = audio.stop()
    elif a.cmd == "daw":
        out = wf.to_daw(a.song, a.daw, not a.no_gui)
    elif a.cmd == "karaoke":
        out = wf.karaoke(a.song, a.backing_vocals, a.semitones, a.record, not a.no_ui)
    elif a.cmd == "produce":
        out = wf.ai_produce(a.song, a.style, _list(a.keep), a.strength, a.intro, a.voice,
                            a.cover, not a.no_video)
    else:
        out = wf.sheet_music(a.song, _list(a.parts), not a.no_pdf)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
