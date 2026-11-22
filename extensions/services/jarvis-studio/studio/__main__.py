"""Command line:  python -m studio <workflow> [options]   (see --help)"""

import argparse
import sys

from . import audio
from . import stemdeck as sd
from . import workflows as wf


def _list(v):
    return [x.strip() for x in v.split(",") if x.strip()] if v else None


def main() -> int:
    p = argparse.ArgumentParser(prog="jarvis-studio", description="Jarvis music studio workflows")
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

    a = p.parse_args()
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
