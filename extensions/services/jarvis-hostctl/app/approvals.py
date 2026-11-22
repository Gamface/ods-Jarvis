"""Out-of-band approval: ask the user on the NUC (kdialog) and on the phone (ntfy).

The first answer wins. No answer within the timeout counts as "deny".
The model never sees or controls this channel.
"""

import json
import os
import queue
import secrets
import subprocess
import threading
import time
import urllib.request

NTFY_URL = os.environ.get("JARVIS_NTFY_URL", "").rstrip("/")
NTFY_TOKEN = os.environ.get("JARVIS_NTFY_TOKEN", "")
NTFY_TOPIC = os.environ.get("JARVIS_NTFY_APPROVAL_TOPIC", "jarvis-approvals")
LOCAL_POPUP = os.environ.get("JARVIS_LOCAL_APPROVAL", "1") == "1"


def _ask_local(text: str, answers: queue.Queue, stop: threading.Event) -> None:
    proc = subprocess.Popen(
        ["kdialog", "--title", "Jarvis needs approval", "--yesno", text,
         "--yes-label", "Approve", "--no-label", "Deny"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    while proc.poll() is None:
        if stop.wait(0.3):
            proc.terminate()
            return
    answers.put(("NUC popup", proc.returncode == 0))


def _ntfy_request(path: str, data: bytes | None = None, headers: dict | None = None):
    req = urllib.request.Request(f"{NTFY_URL}/{path}", data=data, headers=headers or {})
    if NTFY_TOKEN:
        req.add_header("Authorization", f"Bearer {NTFY_TOKEN}")
    return req


def _ask_phone(text: str, req_id: str, answers: queue.Queue, stop: threading.Event,
               timeout: int) -> None:
    reply = f"{NTFY_TOPIC}-reply"
    auth = f", headers.Authorization=Bearer {NTFY_TOKEN}" if NTFY_TOKEN else ""
    actions = (
        f"http, Approve, {NTFY_URL}/{reply}, body=approve {req_id}, clear=true{auth}; "
        f"http, Deny, {NTFY_URL}/{reply}, body=deny {req_id}, clear=true{auth}"
    )
    since = int(time.time())
    urllib.request.urlopen(_ntfy_request(
        NTFY_TOPIC, text.encode(),
        {"Title": "Jarvis needs approval", "Priority": "high", "Tags": "warning",
         "Actions": actions},
    ), timeout=10).close()
    with urllib.request.urlopen(
        _ntfy_request(f"{reply}/json?since={since}"), timeout=timeout
    ) as stream:
        for line in stream:
            if stop.is_set():
                return
            event = json.loads(line)
            words = event.get("message", "").split()
            if event.get("event") == "message" and len(words) == 2 and words[1] == req_id:
                answers.put(("phone", words[0] == "approve"))
                return


def request(text: str, timeout: int = 180) -> tuple[bool, str]:
    """Block until the user approves/denies, or timeout. Returns (approved, via)."""
    req_id = secrets.token_hex(4)
    answers: queue.Queue = queue.Queue()
    stop = threading.Event()
    channels = []
    if LOCAL_POPUP:
        channels.append(threading.Thread(target=_ask_local, args=(text, answers, stop), daemon=True))
    if NTFY_URL:
        channels.append(threading.Thread(
            target=_ask_phone, args=(text, req_id, answers, stop, timeout), daemon=True))
    if not channels:
        return False, "no approval channel configured"
    for t in channels:
        t.start()
    try:
        via, approved = answers.get(timeout=timeout)
    except queue.Empty:
        return False, f"no answer within {timeout}s"
    finally:
        stop.set()
    return approved, via
