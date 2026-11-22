"""Minimal OBS Studio controller over obs-websocket v5 (built into OBS 28+)."""

import base64
import hashlib
import json
import shutil
import subprocess
import time
import uuid

from websockets.sync.client import connect

from .config import OBS_HOST, OBS_PASSWORD, OBS_PORT


def _auth(password: str, salt: str, challenge: str) -> str:
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()


def request(req_type: str, data: dict | None = None) -> dict:
    with connect(f"ws://{OBS_HOST}:{OBS_PORT}", open_timeout=5) as ws:
        hello = json.loads(ws.recv())["d"]
        ident = {"rpcVersion": 1}
        if "authentication" in hello:
            a = hello["authentication"]
            ident["authentication"] = _auth(OBS_PASSWORD, a["salt"], a["challenge"])
        ws.send(json.dumps({"op": 1, "d": ident}))
        json.loads(ws.recv())  # Identified
        rid = str(uuid.uuid4())
        ws.send(json.dumps({"op": 6, "d": {"requestType": req_type, "requestId": rid,
                                           "requestData": data or {}}}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("op") == 7 and msg["d"].get("requestId") == rid:
                d = msg["d"]
                if not d["requestStatus"]["result"]:
                    raise RuntimeError(f"OBS {req_type}: {d['requestStatus'].get('comment')}")
                return d.get("responseData") or {}


def running() -> bool:
    try:
        request("GetVersion")
        return True
    except Exception:
        return False


def start() -> str:
    if running():
        return "OBS already running."
    if not shutil.which("obs"):
        raise RuntimeError("OBS is not installed")
    subprocess.Popen(["obs", "--minimize-to-tray"], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        time.sleep(1)
        if running():
            return "OBS started."
    raise RuntimeError("OBS did not open its websocket (check Tools > WebSocket Server Settings)")


def scenes() -> list[str]:
    return [s["sceneName"] for s in request("GetSceneList")["scenes"]]


def set_scene(name: str) -> str:
    for s in scenes():
        if name.lower() in s.lower():
            request("SetCurrentProgramScene", {"sceneName": s})
            return f"OBS scene: {s}"
    raise RuntimeError(f"No OBS scene matching '{name}'")


def record(action: str) -> str:
    """action: start | stop | status"""
    if action == "start":
        request("StartRecord")
        return "OBS recording started."
    if action == "stop":
        path = request("StopRecord").get("outputPath")
        return f"OBS recording saved: {path}"
    st = request("GetRecordStatus")
    return f"Recording: {st.get('outputActive')} ({st.get('outputTimecode')})"


def setup_performance_scene(camera: str = "/dev/video0", mic: bool = True) -> str:
    """Create (or reuse) a 'Jarvis Performance' scene with a webcam and the default mic.
    Turns the camera on, so only call this when the user asked for it."""
    scene = "Jarvis Performance"
    if scene not in scenes():
        request("CreateScene", {"sceneName": scene})
    have = {i["inputName"] for i in request("GetInputList")["inputs"]}
    made = []
    if camera and "Jarvis Camera" not in have:
        request("CreateInput", {"sceneName": scene, "inputName": "Jarvis Camera",
                                "inputKind": "v4l2_input", "inputSettings": {"device_id": camera},
                                "sceneItemEnabled": True})
        made.append(f"camera {camera}")
    if mic and "Jarvis Mic" not in have:
        request("CreateInput", {"sceneName": scene, "inputName": "Jarvis Mic",
                                "inputKind": "pulse_input_capture",
                                "inputSettings": {"device_id": "default"}, "sceneItemEnabled": True})
        made.append("microphone")
    request("SetCurrentProgramScene", {"sceneName": scene})
    return f"OBS scene '{scene}' ready" + (f" (added {', '.join(made)})" if made else "") + "."
