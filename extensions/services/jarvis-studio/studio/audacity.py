"""Drive Audacity through mod-script-pipe (enabled by setup.sh)."""

import os
import shutil
import subprocess
import time
from pathlib import Path

UID = os.getuid()
TO_PIPE = Path(f"/tmp/audacity_script_pipe.to.{UID}")
FROM_PIPE = Path(f"/tmp/audacity_script_pipe.from.{UID}")


def _send(cmd: str, timeout: float = 120) -> str:
    with TO_PIPE.open("w") as to:
        to.write(cmd + "\n")
    lines, t0 = [], time.time()
    with FROM_PIPE.open("r") as fr:
        while time.time() - t0 < timeout:
            line = fr.readline()
            if line == "\n" and lines:
                break
            if line:
                lines.append(line.rstrip("\n"))
    out = "\n".join(lines)
    if "BatchCommand finished: Failed" in out:
        raise RuntimeError(f"Audacity: {cmd} failed: {out[-300:]}")
    return out


def running() -> bool:
    return TO_PIPE.exists() and FROM_PIPE.exists() and \
        subprocess.run(["pgrep", "-x", "audacity"], capture_output=True).returncode == 0


def start() -> str:
    if running():
        return "Audacity already running."
    if not shutil.which("audacity"):
        raise RuntimeError("Audacity is not installed")
    subprocess.Popen(["audacity"], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        time.sleep(1)
        if running():
            time.sleep(2)
            return "Audacity started."
    raise RuntimeError("Audacity's script pipe did not appear. Is mod-script-pipe enabled "
                       "(Edit > Preferences > Modules)?")


def build_project(stems: dict[str, Path], project: Path) -> str:
    """Open a new project with one track per stem and save it as .aup3."""
    start()
    _send("New:")
    time.sleep(1)
    for name, path in stems.items():
        _send(f'Import2: Filename="{path}"')
    _send(f'SaveProject2: Filename="{project}"')
    return f"Audacity project with {len(stems)} tracks: {project}"
