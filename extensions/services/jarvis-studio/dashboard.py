"""jarvis-studio dashboard: a local web page for the music studio.

Serves http://127.0.0.1:7722 (this computer only). It shows what's running,
the StemDeck library, the five workflows as forms, background jobs, outputs
in ~/Music/Jarvis and the shared activity log (Hermes + dashboard).

Browser safety: the server only answers requests whose Host is localhost /
127.0.0.1, and every POST must be JSON with an X-Jarvis header. Another
website can't send that without a CORS preflight, which this server never
approves, so it can't drive the studio from your browser.
"""

import datetime
import itertools
import json
import os
import re
import shutil
import subprocess
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from studio import audio, guitarix, obs
from studio import stemdeck as sd
from studio import workflows as wf
from studio.config import (ACESTEP_DIR, BASIC_PITCH, MOSS_DIR, OUT_ROOT, STEMDECK_URL,
                           out_dir)

HERE = Path(__file__).resolve().parent
BIND = os.environ.get("JARVIS_DASHBOARD_BIND", "127.0.0.1")
PORT = int(os.environ.get("JARVIS_DASHBOARD_PORT", "7722"))
MCP_HEALTH = (f"http://{os.environ.get('JARVIS_STUDIO_BIND', '127.0.0.1')}:"
              f"{os.environ.get('JARVIS_STUDIO_PORT', '7721')}/health")
LEMONADE = os.environ.get("JARVIS_LEMONADE_URL", "http://127.0.0.1:13305")
AUDIT = Path(os.environ.get("JARVIS_AUDIT_LOG", "~/ods/data/jarvis-studio/audit.log")).expanduser()
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}", f"[::1]:{PORT}"}
SHOW_EXT = {".wav", ".mp3", ".flac", ".mp4", ".pdf", ".mid", ".lrc", ".txt", ".aup3", ".ardour"}


def audit(event: str, **fields) -> None:
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": datetime.datetime.now().isoformat(timespec="seconds"), "event": event,
           "via": "dashboard", **fields}
    with AUDIT.open("a") as f:
        f.write(json.dumps(rec, default=str) + "\n")


# --- background jobs ------------------------------------------------------
# Two workers: a long AI job can run while you make practice tracks.
POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="studio-job")
JOBS: dict[str, dict] = {}
JOB_IDS = itertools.count(1)
LOCK = threading.Lock()


def _csv(v) -> list[str] | None:
    if not v:
        return None
    if isinstance(v, list):
        return [str(x) for x in v if str(x).strip()] or None
    return [x.strip() for x in str(v).split(",") if x.strip()] or None


def _num(v, cast=float, default=None):
    if v in (None, ""):
        return default
    return cast(v)


def _make_music(a):
    path = wf.generate_music(a["caption"], out_dir("generated"), _num(a.get("duration"), float, 30),
                             a.get("lyrics") or None)
    return f"Generated: {path}"


def _speak(a):
    dest = out_dir("speech") / f"speech-{datetime.datetime.now():%Y%m%d-%H%M%S}.wav"
    wf.speak(a["text"], dest, a.get("voice") or None)
    msg = f"Saved {dest}"
    if a.get("play"):
        msg += "\n" + audio.play(dest)
    return msg


def _split(a):
    job = sd.wait(sd.submit(a["source"]))
    return (f"Split '{job['title']}' ({job.get('key')}, {job.get('bpm')} BPM). "
            f"Parts: {', '.join(sd.stem_names(job))}.")


WORKFLOWS = {
    "split": ("Split song", _split),
    "practice": ("Practice track", lambda a: wf.practice(
        a["song"], _csv(a.get("mute")), _csv(a.get("solo")), a.get("section") or None,
        _num(a.get("start")), _num(a.get("end")), _num(a.get("speed"), float, 1.0),
        _num(a.get("semitones"), int, 0), bool(a.get("click")), _num(a.get("count_in"), int, 0),
        bool(a.get("loop", True)), bool(a.get("play", True)), a.get("guitar_preset") or None)),
    "daw": ("Stems to DAW", lambda a: wf.to_daw(a["song"], a.get("daw") or "both")),
    "karaoke": ("Karaoke", lambda a: wf.karaoke(
        a["song"], bool(a.get("keep_backing_vocals")), _num(a.get("semitones"), int, 0),
        bool(a.get("record")), bool(a.get("open_ui", False)))),
    "produce": ("AI re-production", lambda a: wf.ai_produce(
        a["song"], a["style"], _csv(a.get("keep")), _num(a.get("strength"), float, 0.45),
        a.get("intro") or None, None, a.get("cover_image") or None, bool(a.get("video", True)))),
    "sheet": ("Sheet music", lambda a: wf.sheet_music(a["song"], _csv(a.get("parts")))),
    "make_music": ("Make music", _make_music),
    "speak": ("Speak", _speak),
}

PATH_RE = re.compile(re.escape(str(OUT_ROOT)) + r"/[^\s'\"]+")


def _files_in(text: str) -> list[dict]:
    seen, out = set(), []
    for m in PATH_RE.findall(text or ""):
        p = Path(m.rstrip(".,)"))
        if p.exists() and p not in seen:
            seen.add(p)
            out.append(_file_info(p))
    return out


def _run_job(jid: str) -> None:
    job = JOBS[jid]
    job.update(status="running", started=time.time())
    try:
        result = WORKFLOWS[job["workflow"]][1](job["args"])
        job.update(status="done", result=result, files=_files_in(result))
        audit(job["workflow"] + "_done", job=jid)
    except Exception as e:
        job.update(status="failed", result=f"{type(e).__name__}: {e}",
                   trace=traceback.format_exc()[-2000:])
        audit(job["workflow"] + "_error", job=jid, error=str(e))
    job["finished"] = time.time()


def start_job(workflow: str, args: dict) -> dict:
    if workflow not in WORKFLOWS:
        raise ValueError(f"Unknown workflow '{workflow}'")
    with LOCK:
        jid = str(next(JOB_IDS))
        JOBS[jid] = {"id": jid, "workflow": workflow, "label": WORKFLOWS[workflow][0],
                     "args": args, "status": "queued", "created": time.time(),
                     "started": None, "finished": None, "result": "", "files": []}
    audit(workflow, args=args, job=jid)
    POOL.submit(_run_job, jid)
    return JOBS[jid]


# --- status checks --------------------------------------------------------
def _http_ok(url: str) -> bool:
    try:
        return httpx.get(url, timeout=2).status_code < 400
    except httpx.HTTPError:
        return False


def _pgrep(pattern: str) -> bool:
    return subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode == 0


def _docker_up(name: str) -> bool | None:
    if not shutil.which("docker"):
        return None
    r = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", name],
                       capture_output=True, text=True, timeout=5)
    return r.stdout.strip() == "true"


def _playing() -> bool:
    try:
        os.killpg(int(audio.PID_FILE.read_text()), 0)
        return True
    except (OSError, ValueError):
        return False


def _lemonade() -> dict:
    try:
        r = httpx.get(f"{LEMONADE}/api/v1/health", timeout=2).json()
        models = [m.get("model_name") for m in r.get("all_models_loaded", [])]
        return {"ok": True, "detail": ", ".join(filter(None, models)) or "no model loaded"}
    except Exception:
        return {"ok": False, "detail": "not answering"}


CHECKS = {
    "stemdeck": ("StemDeck", lambda: {"ok": sd.health(), "detail": STEMDECK_URL, "url": STEMDECK_URL}),
    "mcp": ("Jarvis Studio MCP", lambda: {"ok": _http_ok(MCP_HEALTH), "detail": "for Hermes"}),
    "hermes": ("Hermes agent", lambda: {"ok": _docker_up("ods-hermes"), "detail": "ods-hermes"}),
    "lemonade": ("Lemonade", lambda: {**_lemonade(), "url": LEMONADE}),
    "guitarix": ("Guitarix", lambda: {"ok": guitarix.running(), "detail": "amp sim", "idle_ok": True}),
    "obs": ("OBS", lambda: {"ok": obs.running(), "detail": "recorder", "idle_ok": True}),
    "audacity": ("Audacity", lambda: {"ok": _pgrep("^(/usr/bin/)?audacity"), "detail": "editor", "idle_ok": True}),
    "ardour": ("Ardour", lambda: {"ok": _pgrep("lib/ardour9/ardour-"),
                                  "detail": "DAW", "idle_ok": True}),
    "player": ("Playback", lambda: {"ok": _playing(), "detail": "playing" if _playing() else "idle",
                                    "idle_ok": True}),
}
MODELS = {"ACE-Step 1.5": ACESTEP_DIR / "checkpoints", "MOSS-TTS-Nano": MOSS_DIR,
          "basic-pitch": BASIC_PITCH}


def status_snapshot() -> dict:
    with ThreadPoolExecutor(max_workers=len(CHECKS)) as ex:
        futs = {k: ex.submit(fn) for k, (_, fn) in CHECKS.items()}
        services = []
        for k, (label, _) in CHECKS.items():
            try:
                res = futs[k].result(timeout=8)
            except Exception as e:
                res = {"ok": False, "detail": str(e)[:80]}
            services.append({"id": k, "label": label, **res})
    disk = shutil.disk_usage(OUT_ROOT if OUT_ROOT.exists() else Path.home())
    return {"services": services,
            "models": [{"label": k, "ok": p.exists()} for k, p in MODELS.items()],
            "disk_free_gb": round(disk.free / 1e9, 1), "out_root": str(OUT_ROOT),
            "jobs_running": sum(j["status"] in ("queued", "running") for j in JOBS.values())}


# --- files ----------------------------------------------------------------
def _safe_path(rel: str) -> Path:
    p = (OUT_ROOT / rel).resolve()
    if not p.is_relative_to(OUT_ROOT.resolve()) or not p.exists():
        raise FileNotFoundError(rel)
    return p


def _file_info(p: Path) -> dict:
    st = p.stat()
    rel = p.resolve().relative_to(OUT_ROOT.resolve())
    return {"path": str(rel), "name": p.name, "kind": rel.parts[0] if len(rel.parts) > 1 else "",
            "song": rel.parts[1] if len(rel.parts) > 2 else "", "ext": p.suffix.lower(),
            "size": st.st_size, "mtime": st.st_mtime, "dir": p.is_dir()}


def recent_outputs(limit: int = 80) -> list[dict]:
    if not OUT_ROOT.exists():
        return []
    files = []
    for p in OUT_ROOT.rglob("*"):
        if "/acestep/" in str(p) or "/stems/" in str(p) or "/midi/" in str(p):
            if p.suffix.lower() != ".mid":
                continue
        if p.is_file() and p.suffix.lower() in SHOW_EXT and not p.name.endswith(".raw.wav"):
            files.append(p)
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [_file_info(p) for p in files[:limit]]


def tail_audit(n: int = 40) -> list[dict]:
    if not AUDIT.exists():
        return []
    lines = AUDIT.read_text().splitlines()[-n:]
    out = []
    for ln in reversed(lines):
        try:
            rec = json.loads(ln)
        except json.JSONDecodeError:
            continue
        rec.setdefault("via", "hermes")
        out.append(rec)
    return out


# --- HTTP -----------------------------------------------------------------
async def body(request: Request) -> dict:
    if request.headers.get("x-jarvis") != "1" or \
            not request.headers.get("content-type", "").startswith("application/json"):
        raise PermissionError("missing X-Jarvis header")
    return await request.json()


def ok(data=None, **kw) -> JSONResponse:
    return JSONResponse(data if data is not None else kw)


async def index(request):
    return FileResponse(HERE / "static" / "dashboard.html",
                        headers={"Cache-Control": "no-store"})


async def api_status(request):
    import anyio
    return ok(await anyio.to_thread.run_sync(status_snapshot))


async def api_songs(request):
    import anyio
    return ok(await anyio.to_thread.run_sync(sd.list_songs))


async def api_song(request):
    import anyio
    job = await anyio.to_thread.run_sync(sd.get, request.path_params["id"])
    keep = ("job_id", "title", "status", "duration", "bpm", "key", "stems", "sections",
            "stem_presence", "stage", "progress", "source")
    return ok({k: job.get(k) for k in keep})


async def api_jobs(request):
    jobs = sorted(JOBS.values(), key=lambda j: j["created"], reverse=True)[:40]
    return ok([{k: v for k, v in j.items() if k != "trace"} for j in jobs])


async def api_run(request):
    data = await body(request)
    return ok(start_job(data.get("workflow", ""), data.get("args") or {}))


async def api_stop(request):
    await body(request)
    audit("stop")
    return ok(message=audio.stop())


async def api_play(request):
    data = await body(request)
    p = _safe_path(data["path"])
    audit("play", path=data["path"])
    return ok(message=audio.play(p, loop=bool(data.get("loop"))))


async def api_open(request):
    data = await body(request)
    p = _safe_path(data.get("path") or ".")
    target = p if p.is_dir() or data.get("file") else p.parent
    subprocess.Popen(["xdg-open", str(target)], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return ok(message=f"Opened {target.name or target}")


async def api_guitarix(request):
    import anyio
    if request.method == "GET":
        def info():
            if not guitarix.running():
                return {"running": False, "presets": {}}
            cur = None
            try:
                cur = guitarix._call("get", ["system.current_preset"])
            except Exception:
                pass
            return {"running": True, "presets": guitarix.presets(), "current": cur}
        return ok(await anyio.to_thread.run_sync(info))
    data = await body(request)
    audit("guitarix", args=data)
    if data.get("action") == "start":
        msg = await anyio.to_thread.run_sync(guitarix.start)
    else:
        msg = await anyio.to_thread.run_sync(guitarix.set_preset, data.get("preset", ""))
    return ok(message=msg)


async def api_obs(request):
    import anyio
    if request.method == "GET":
        def info():
            if not obs.running():
                return {"running": False}
            st = obs.request("GetRecordStatus")
            return {"running": True, "recording": st.get("outputActive"),
                    "timecode": st.get("outputTimecode"), "scenes": obs.scenes(),
                    "scene": obs.request("GetCurrentProgramScene").get("currentProgramSceneName")}
        return ok(await anyio.to_thread.run_sync(info))
    data = await body(request)
    action = data.get("action")
    audit("obs", args=data)
    fn = {"start_app": obs.start, "record_start": lambda: obs.record("start"),
          "record_stop": lambda: obs.record("stop"),
          "scene": lambda: obs.set_scene(data.get("scene", ""))}.get(action)
    if not fn:
        raise ValueError(f"Unknown OBS action '{action}'")
    return ok(message=await anyio.to_thread.run_sync(fn))


async def api_outputs(request):
    import anyio
    return ok(await anyio.to_thread.run_sync(recent_outputs))


async def api_activity(request):
    return ok(tail_audit())


async def files(request):
    p = _safe_path(request.path_params["path"])
    if p.is_dir():
        raise FileNotFoundError(request.path_params["path"])
    return FileResponse(p)


async def on_error(request, exc):
    status = 403 if isinstance(exc, PermissionError) else \
        404 if isinstance(exc, FileNotFoundError) else 400
    return JSONResponse({"error": str(exc)}, status_code=status)


class HostGuard:
    """Reject DNS-rebinding and cross-site requests."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            host = headers.get(b"host", b"").decode()
            origin = headers.get(b"origin", b"").decode()
            bad = host not in ALLOWED_HOSTS or (origin and origin.split("//", 1)[-1] not in ALLOWED_HOSTS)
            if bad:
                await PlainTextResponse("forbidden", status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)


routes = [
    Route("/", index),
    Route("/api/status", api_status),
    Route("/api/songs", api_songs),
    Route("/api/songs/{id}", api_song),
    Route("/api/jobs", api_jobs),
    Route("/api/run", api_run, methods=["POST"]),
    Route("/api/stop", api_stop, methods=["POST"]),
    Route("/api/play", api_play, methods=["POST"]),
    Route("/api/open", api_open, methods=["POST"]),
    Route("/api/guitarix", api_guitarix, methods=["GET", "POST"]),
    Route("/api/obs", api_obs, methods=["GET", "POST"]),
    Route("/api/outputs", api_outputs),
    Route("/api/activity", api_activity),
    Route("/files/{path:path}", files),
    Route("/health", lambda r: Response('{"status":"ok"}', media_type="application/json")),
]
app = Starlette(routes=routes, exception_handlers={
    e: on_error for e in (ValueError, KeyError, RuntimeError, PermissionError, FileNotFoundError,
                          OSError, httpx.HTTPError)})

if __name__ == "__main__":
    uvicorn.run(HostGuard(app), host=BIND, port=PORT, log_level="warning")
