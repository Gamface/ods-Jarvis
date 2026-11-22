"""Local audio helpers: tempo change, looping, playback through PipeWire."""

import os
import signal
import subprocess
from pathlib import Path

from .config import STATE_DIR

PID_FILE = STATE_DIR / "player.pid"


def run(cmd: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {proc.stderr[-800:]}")
    return proc


def change_speed(src: Path, dest: Path, speed: float) -> Path:
    """Change tempo without changing pitch (rubberband, high quality)."""
    if abs(speed - 1.0) < 1e-3:
        return src
    if not 0.25 <= speed <= 2.0:
        raise ValueError("speed must be between 0.25 and 2.0")
    run(["rubberband", "-q", "--fine", "-T", f"{speed:.4f}", str(src), str(dest)])
    return dest


def duration(path: Path) -> float:
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", str(path)]).stdout.strip()
    return float(out or 0)


def stop() -> str:
    """Stop whatever jarvis-studio is playing."""
    if not PID_FILE.exists():
        return "Nothing playing."
    try:
        pgid = int(PID_FILE.read_text())
        os.killpg(pgid, signal.SIGTERM)
        msg = "Stopped."
    except (ProcessLookupError, ValueError):
        msg = "Nothing playing."
    PID_FILE.unlink(missing_ok=True)
    return msg


def play(path: Path, loop: bool = False) -> str:
    """Play a file on the default output (in the background). loop=True repeats until stop()."""
    stop()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    quoted = "'" + str(path).replace("'", "'\\''") + "'"
    script = f"while pw-play {quoted}; do :; done" if loop else f"pw-play {quoted}"
    proc = subprocess.Popen(["bash", "-c", script], start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    PID_FILE.write_text(str(proc.pid))
    return f"Playing{' on loop' if loop else ''}: {path.name}"


def mix_files(inputs: list[tuple[Path, float]], dest: Path, delays: list[float] | None = None,
              loudnorm: bool = True) -> Path:
    """Mix files with per-file gain (and optional start delay in seconds) into one WAV."""
    cmd = ["ffmpeg", "-y", "-v", "error"]
    for p, _ in inputs:
        cmd += ["-i", str(p)]
    parts, labels = [], []
    for i, (_, gain) in enumerate(inputs):
        d = int((delays[i] if delays else 0) * 1000)
        parts.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,"
                     f"adelay={d}|{d},volume={gain}[a{i}]")
        labels.append(f"[a{i}]")
    chain = ";".join(parts) + ";" + "".join(labels) + \
        f"amix=inputs={len(inputs)}:normalize=0:duration=longest"
    chain += ",loudnorm=I=-14:TP=-1:LRA=11[out]" if loudnorm else "[out]"
    cmd += ["-filter_complex", chain, "-map", "[out]", "-ar", "48000", str(dest)]
    run(cmd)
    return dest
