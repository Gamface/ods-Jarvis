"""Guitarix amp simulator over its JSON-RPC port (guitarix --rpcport)."""

import json
import shutil
import socket
import subprocess
import time

from .config import GUITARIX_PORT


def _call(method: str, params: list | None = None) -> object:
    with socket.create_connection(("127.0.0.1", GUITARIX_PORT), timeout=5) as s:
        f = s.makefile("rwb")
        msg = {"jsonrpc": "2.0", "method": method, "params": params or [], "id": 1}
        f.write((json.dumps(msg) + "\n").encode())
        f.flush()
        line = f.readline().decode()
    # Guitarix answers void methods with invalid JSON: {"...","result": }
    reply = json.loads(line.replace('"result": }', '"result": null}'))
    if "error" in reply:
        raise RuntimeError(f"Guitarix: {reply['error'].get('message')}")
    return reply.get("result")


def running() -> bool:
    try:
        _call("get", ["system.current_bank"])
        return True
    except OSError:
        return False


def start(gui: bool = True) -> str:
    """Start Guitarix with remote control on, if it isn't already running."""
    if running():
        return "Guitarix already running."
    if not shutil.which("guitarix"):
        raise RuntimeError("guitarix is not installed")
    cmd = ["pw-jack", "guitarix", f"--rpcport={GUITARIX_PORT}"] + ([] if gui else ["--nogui"])
    subprocess.Popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(30):
        time.sleep(1)
        if running():
            return "Guitarix started. Plug in your guitar and pick its input in qpwgraph if needed."
    raise RuntimeError("Guitarix did not answer on its remote-control port")


def presets() -> dict[str, list[str]]:
    return {b["name"]: b["presets"] for b in _call("banks")}


def set_preset(query: str) -> str:
    """Load the first preset whose name contains `query` (e.g. 'clean', 'bass', 'acoustic')."""
    q = query.lower()
    for bank, names in presets().items():
        for name in names:
            if q in name.lower() or q == bank.lower():
                _call("setpreset", [bank, name])
                return f"Guitarix preset: {bank} / {name}"
    raise RuntimeError(f"No Guitarix preset matching '{query}'")
