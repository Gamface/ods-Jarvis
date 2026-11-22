"""Prepare an Ardour session from stems.

Ardour 9 cannot import audio without its window open, so this creates an empty
session with the stems inside it, installs the "Jarvis: Import StemDeck stems"
Lua action, and opens Ardour. One click on that action (Edit > Lua Scripts >
Script Manager, or a toolbar button once assigned) builds the tracks.
"""

import shutil
import subprocess
from pathlib import Path

from .audio import run
from .config import SCRIPTS_DIR

ARDOUR_SCRIPTS = Path("~/.config/ardour9/scripts").expanduser()


def install_action() -> Path:
    ARDOUR_SCRIPTS.mkdir(parents=True, exist_ok=True)
    dest = ARDOUR_SCRIPTS / "jarvis_import_stems.lua"
    shutil.copy2(SCRIPTS_DIR / "jarvis_import_stems.lua", dest)
    return dest


def build_session(stems: dict[str, Path], session_dir: Path, name: str,
                  bpm: float | None = None, open_gui: bool = True) -> str:
    install_action()
    if not (session_dir / f"{name}.ardour").exists():
        session_dir.parent.mkdir(parents=True, exist_ok=True)
        run(["ardour9-new_session", "-s", "48000", str(session_dir), name])  # with a stereo master bus
    stem_dir = session_dir / "stems"
    stem_dir.mkdir(exist_ok=True)
    order = ["vocals", "drums", "bass", "guitar", "piano", "other"]
    ranked = sorted(stems.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else 99)
    for i, (stem, path) in enumerate(ranked, 1):
        shutil.copy2(path, stem_dir / f"{i:02d}-{stem}.wav")
    if bpm:
        (stem_dir / "tempo.txt").write_text(f"{bpm}\n")
    msg = (f"Ardour session ready: {session_dir / (name + '.ardour')}. In Ardour run "
           "Edit > Lua Scripts > Script Manager > 'Jarvis: Import StemDeck stems' "
           "(assign it to an action button once) to build one track per stem, plus a Surge XT synth "
           "track, a DrumGizmo drum track (DRSKit) and LSP EQ/compressor/limiter, added switched off.")
    if open_gui:
        subprocess.Popen(["ardour", str(session_dir / f"{name}.ardour")], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        msg = "Opening Ardour. " + msg
    return msg
