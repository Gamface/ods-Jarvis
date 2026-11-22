"""jarvis-hostctl: MCP server giving the ODS Hermes agent control of this computer.

Tools: run_command, screenshot, request_control, mouse_click, mouse_move,
scroll, type_text, press_keys, ask_approval.

Safety:
  * Shell commands outside the read-only allowlist need the user's approval
    (NUC popup or phone notification) before they run.
  * Mouse/keyboard tools only work during a time-limited control grant the
    user approves for a stated task.
  * Every action is appended to an audit log.
  * Requests need the bearer token in JARVIS_HOSTCTL_TOKEN.
"""

import datetime
import hmac
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

import uvicorn
from mcp.server.fastmcp import FastMCP, Image

import approvals
import policy

TOKEN = os.environ["JARVIS_HOSTCTL_TOKEN"]
BIND = os.environ.get("JARVIS_HOSTCTL_BIND", "127.0.0.1")
PORT = int(os.environ.get("JARVIS_HOSTCTL_PORT", "7720"))
SCALE = float(os.environ.get("JARVIS_SCREEN_SCALE", "1.0"))
AUDIT = Path(os.environ.get("JARVIS_AUDIT_LOG", "~/ods/data/jarvis-hostctl/audit.log")).expanduser()
EXTRA_SAFE = frozenset(os.environ.get("JARVIS_SAFE_COMMANDS", "").split())
MAX_OUTPUT = 20000

mcp = FastMCP("jarvis-hostctl", host=BIND, port=PORT, stateless_http=True)
_grant = {"until": 0.0, "task": ""}

KEYS = {
    "esc": 1, "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9, "9": 10,
    "0": 11, "minus": 12, "equal": 13, "backspace": 14, "tab": 15, "q": 16, "w": 17,
    "e": 18, "r": 19, "t": 20, "y": 21, "u": 22, "i": 23, "o": 24, "p": 25,
    "enter": 28, "ctrl": 29, "a": 30, "s": 31, "d": 32, "f": 33, "g": 34, "h": 35,
    "j": 36, "k": 37, "l": 38, "shift": 42, "z": 44, "x": 45, "c": 46, "v": 47,
    "b": 48, "n": 49, "m": 50, "slash": 53, "alt": 56, "space": 57, "f1": 59,
    "f2": 60, "f3": 61, "f4": 62, "f5": 63, "f6": 64, "f7": 65, "f8": 66, "f9": 67,
    "f10": 68, "f11": 87, "f12": 88, "home": 102, "up": 103, "pageup": 104,
    "left": 105, "right": 106, "end": 107, "down": 108, "pagedown": 109,
    "insert": 110, "delete": 111, "super": 125, "period": 52, "comma": 51,
}
BUTTONS = {"left": "0xC0", "right": "0xC1", "middle": "0xC2"}


def audit(event: str, **fields) -> None:
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": datetime.datetime.now().isoformat(timespec="seconds"), "event": event, **fields}
    with AUDIT.open("a") as f:
        f.write(json.dumps(record) + "\n")


def ydotool(*args: str) -> None:
    subprocess.run(["ydotool", *args], check=True, timeout=10, capture_output=True)


def require_grant() -> None:
    if time.time() > _grant["until"]:
        raise PermissionError(
            "No active control grant. Call request_control(task, minutes) first; "
            "the user must approve it.")


def to_logical(x: int, y: int) -> tuple[str, str]:
    return str(round(x / SCALE)), str(round(y / SCALE))


@mcp.tool()
def run_command(command: str, cwd: str = "~", timeout_seconds: int = 60) -> str:
    """Run a bash command on the host as the user. Read-only commands run
    immediately; anything that could change the system waits for the user's
    approval (NUC popup or phone), which can take a few minutes."""
    decision, reason = policy.classify(command, EXTRA_SAFE)
    if decision == "ask":
        approved, via = approvals.request(f"Run this command?\n\n{command}\n\nReason asked: {reason}")
        audit("command_approval", command=command, approved=approved, via=via)
        if not approved:
            return f"NOT RUN: user did not approve ({via})."
    proc = subprocess.run(
        ["bash", "-lc", command], cwd=os.path.expanduser(cwd), capture_output=True,
        text=True, timeout=timeout_seconds,
    )
    audit("command", command=command, exit=proc.returncode, auto=decision == "auto")
    out = (proc.stdout + ("\n[stderr]\n" + proc.stderr if proc.stderr else ""))[-MAX_OUTPUT:]
    return f"exit code {proc.returncode}\n{out}"


@mcp.tool()
def screenshot() -> Image:
    """Capture the full screen. Coordinates for mouse tools use this image's pixels."""
    path = Path(tempfile.mkdtemp(prefix="jarvis-shot-")) / "screen.png"
    subprocess.run(["spectacle", "-b", "-n", "-f", "-o", str(path)], check=True, timeout=20)
    audit("screenshot")
    data = path.read_bytes()
    path.unlink()
    path.parent.rmdir()
    return Image(data=data, format="png")


@mcp.tool()
def request_control(task: str, minutes: int = 10) -> str:
    """Ask the user to allow mouse/keyboard control for a task, for up to
    `minutes` (max 30). Required before mouse_* / scroll / type_text / press_keys."""
    minutes = max(1, min(minutes, 30))
    approved, via = approvals.request(
        f"Allow Jarvis to control the mouse and keyboard for {minutes} min?\n\nTask: {task}")
    audit("control_grant", task=task, minutes=minutes, approved=approved, via=via)
    if not approved:
        return f"Control NOT granted ({via})."
    _grant.update(until=time.time() + minutes * 60, task=task)
    return f"Control granted for {minutes} minutes (approved via {via})."


@mcp.tool()
def ask_approval(action: str) -> str:
    """Ask the user to approve a risky step BEFORE doing it: sending a message or
    email, buying anything, deleting data, submitting a form, changing accounts."""
    approved, via = approvals.request(f"Jarvis wants to:\n\n{action}")
    audit("action_approval", action=action, approved=approved, via=via)
    return f"APPROVED via {via}" if approved else f"DENIED ({via}). Do not do it."


@mcp.tool()
def mouse_move(x: int, y: int) -> str:
    """Move the pointer to screenshot pixel (x, y)."""
    require_grant()
    lx, ly = to_logical(x, y)
    ydotool("mousemove", "--absolute", "-x", lx, "-y", ly)
    audit("mouse_move", x=x, y=y)
    return "ok"


@mcp.tool()
def mouse_click(x: int, y: int, button: str = "left", double: bool = False) -> str:
    """Click at screenshot pixel (x, y). button: left | right | middle."""
    require_grant()
    lx, ly = to_logical(x, y)
    ydotool("mousemove", "--absolute", "-x", lx, "-y", ly)
    ydotool("click", *(["--repeat", "2"] if double else []), BUTTONS[button])
    audit("mouse_click", x=x, y=y, button=button, double=double)
    return "ok"


@mcp.tool()
def scroll(amount: int) -> str:
    """Scroll the wheel: negative = up, positive = down."""
    require_grant()
    ydotool("mousemove", "--wheel", "-x", "0", "-y", str(-amount))
    audit("scroll", amount=amount)
    return "ok"


@mcp.tool()
def type_text(text: str) -> str:
    """Type text at the current focus."""
    require_grant()
    ydotool("type", "--", text)
    audit("type_text", chars=len(text))
    return "ok"


@mcp.tool()
def press_keys(combo: str) -> str:
    """Press a key or combination, e.g. 'enter', 'ctrl+c', 'alt+tab', 'super'."""
    require_grant()
    codes = [KEYS[k.strip().lower()] for k in combo.split("+")]
    seq = [f"{c}:1" for c in codes] + [f"{c}:0" for c in reversed(codes)]
    ydotool("key", *seq)
    audit("press_keys", combo=combo)
    return "ok"


class BearerAuth:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            given = headers.get(b"authorization", b"").decode()
            if scope["path"] != "/health" and not hmac.compare_digest(given, f"Bearer {TOKEN}"):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
            if scope["path"] == "/health":
                await send({"type": "http.response.start", "status": 200,
                            "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": b'{"status":"ok"}'})
                return
        await self.app(scope, receive, send)


if __name__ == "__main__":
    uvicorn.run(BearerAuth(mcp.streamable_http_app()), host=BIND, port=PORT)
