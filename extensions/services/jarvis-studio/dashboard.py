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
from studio.video import workflows as vwf
from studio import jobqueue
from studio.video import explainer
from studio.video.common import VIDEO_OUT, vaapi_ok
from studio.config import (safe_name, ACESTEP_DIR, BASIC_PITCH, MOSS_DIR, OUT_ROOT, STEMDECK_URL,
                           out_dir)

HERE = Path(__file__).resolve().parent
BIND = os.environ.get("JARVIS_DASHBOARD_BIND", "127.0.0.1")
PORT = int(os.environ.get("JARVIS_DASHBOARD_PORT", "7722"))
MCP_HEALTH = (f"http://{os.environ.get('JARVIS_STUDIO_BIND', '127.0.0.1')}:"
              f"{os.environ.get('JARVIS_STUDIO_PORT', '7721')}/health")
LEMONADE = os.environ.get("JARVIS_LEMONADE_URL", "http://127.0.0.1:13305")
AUDIT = Path(os.environ.get("JARVIS_AUDIT_LOG", "~/ods/data/jarvis-studio/audit.log")).expanduser()
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}", f"[::1]:{PORT}"}
SHOW_EXT = {".png", ".jpg", ".wav", ".mp3", ".flac", ".mp4", ".pdf", ".mid", ".lrc", ".txt", ".aup3", ".ardour"}


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
    return wf.make_song(a["caption"], a.get("title") or None, a.get("lyrics") or None,
                        _num(a.get("duration"), float, 60), _num(a.get("bpm"), int),
                        a.get("key") or None, a.get("language") or None, _num(a.get("seed"), int),
                        bool(a.get("library", True)), bool(a.get("play")))


def _speak(a):
    dest = out_dir("speech") / f"speech-{datetime.datetime.now():%Y%m%d-%H%M%S}.wav"
    wf.speak(a["text"], dest, a.get("voice") or None)
    msg = f"Saved {dest}"
    if a.get("play"):
        msg += "\n" + audio.play(dest)
    return msg


def _make_image(a):
    from studio import images
    return images.make_image(a.get("prompt", ""), a.get("title") or None, a.get("size") or "512x512",
                             a.get("model") or None, _num(a.get("seed"), int))


def _stems_export(a):
    job = sd.ensure_song(a["song"])
    names = _csv(a.get("parts")) or sd.stem_names(job)
    d = out_dir("stems", safe_name(job.get("title") or job["job_id"]))
    got = sd.download_stems(job, d, names)
    return f"Stems for '{job['title']}' in {d}:\n" + "\n".join(str(p) for p in got.values())


def _stems_mix(a):
    job = sd.ensure_song(a["song"])
    names = sd.stem_names(job)
    raw = a.get("gains") or {}
    gains = {n: max(0.0, min(2.0, float(raw.get(n, 1.0)))) for n in names}
    semis = _num(a.get("semitones"), int, 0)
    if all(g == 0 for g in gains.values()):
        raise ValueError("Every part is muted")
    d = out_dir("mixes", safe_name(job.get("title") or job["job_id"]))
    tag = "mix-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + (f"-{semis:+d}st" if semis else "")
    path = sd.render_mix(job, d / f"{tag}.wav", gains, semis)
    levels = ", ".join(f"{n} {int(round(g * 100))}%" for n, g in gains.items())
    return f"Mix of '{job['title']}' ({levels}): {path}"


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
    "stems_export": ("Export stems", _stems_export),
    "stems_mix": ("Stem mix", _stems_mix),
    "sheet": ("Sheet music", lambda a: wf.sheet_music(a["song"], _csv(a.get("parts")))),
    "make_music": ("New song", _make_music),
    "speak": ("Speak", _speak),
    "make_image": ("New picture", _make_image),
    # Video studio
    "v_music_video": ("Music video", lambda a: vwf.music_video(
        a["song"], a.get("style") or "stems", a.get("aspect") or "16:9", a.get("title") or None,
        a.get("subtitle") or None, a.get("background") or None, bool(a.get("lyrics", True)),
        a.get("section") or None, None, None, a.get("audio") or None)),
    "v_perform_start": ("Performance take", lambda a: vwf.perform_start(
        a["song"], a.get("mode") or "karaoke", _num(a.get("semitones"), int, 0), a.get("scene") or None)),
    "v_perform_finish": ("Finish performance", lambda a: vwf.perform_finish(bool(a.get("lyrics", True)))),
    "v_performance": ("Performance video", lambda a: vwf.performance(
        a["recording"], a["song"], None, bool(a.get("lyrics", True)))),
    "v_obs_scene": ("OBS camera scene", lambda a: obs.start() + "\n" + obs.setup_performance_scene(
        a.get("camera") or "/dev/video0", bool(a.get("mic", True)))),
    "v_auto_edit": ("Beat-cut edit", lambda a: vwf.auto_edit(
        a["clips"], a["song"], a.get("aspect") or "16:9", a.get("pace") or "medium",
        a.get("title") or None, a.get("section") or None, None, _num(a.get("max_len")),
        bool(a.get("lyrics")))),
    "v_shorts": ("Shorts", lambda a: vwf.shorts(
        a["source"], _csv(a.get("platforms")), _num(a.get("seconds"), float, 30), _num(a.get("start")),
        None, a.get("caption") or "auto", a.get("song") or None, a.get("title") or None)),
    "v_narrated": ("Narrated video", lambda a: vwf.narrated(
        a["script"], a["title"], a.get("images") or None, a.get("voice") or None,
        a.get("music") or None, a.get("aspect") or "16:9",
        voice_speed=float(a.get("voice_speed") or 1.0))),
    "v_captions": ("Captions", lambda a: vwf.captions(
        a["video"], a.get("style") or "bottom", bool(a.get("burn", True)), a.get("language") or None)),
}

ROOTS = {"music": OUT_ROOT, "video": VIDEO_OUT}
# Paths may contain spaces (song titles); they end at a newline or a " (" note.
PATH_RE = re.compile("(?:" + "|".join(re.escape(str(r)) for r in ROOTS.values()) +
                     r")/[^\n]+?\.(?:mp4|wav|mp3|png|jpg|pdf|mid|lrc|txt|aup3|ardour|otio|srt)\b")


def _files_in(text: str) -> list[dict]:
    seen, out = set(), []
    for m in PATH_RE.findall(text or ""):
        p = Path(m)
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


def _pictures() -> dict:
    """Text-to-image: SD-Turbo in Lemonade. It loads only while a picture is being made."""
    from studio import images
    try:
        models = httpx.get(f"{LEMONADE}/api/v1/models", params={"show_all": "true"}, timeout=3).json()["data"]
        loaded = httpx.get(f"{LEMONADE}/api/v1/health", timeout=3).json().get("all_models_loaded", [])
    except Exception:
        return {"ok": False, "detail": "Lemonade not answering"}
    have = {m["id"] for m in models if m.get("downloaded")}
    model = images.DEFAULT_MODEL
    if model not in have:
        return {"ok": False, "detail": f"{model} not downloaded in Lemonade"}
    busy = any(m.get("model_name") == model for m in loaded)
    return {"ok": True, "detail": f"{model}: {'making a picture' if busy else 'ready, loads on demand'}"}


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
    "pictures": ("Pictures", _pictures),
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
            "models": [{"label": k, "ok": p.exists()} for k, p in MODELS.items()]
            + [{"label": "GPU video encode", "ok": vaapi_ok()}],
            "disk_free_gb": round(disk.free / 1e9, 1), "out_root": str(OUT_ROOT),
            "jobs_running": sum(j["status"] in ("queued", "running") for j in JOBS.values())}


# --- files ----------------------------------------------------------------
def _safe_path(rel: str) -> Path:
    """'music/…' or 'video/…' -> a real path inside that output folder (nothing outside)."""
    root_key, _, rest = (rel or "").partition("/")
    root = ROOTS.get(root_key)
    if root is None:
        raise FileNotFoundError(rel)
    root = root.resolve()
    p = (root / rest).resolve()
    if not p.is_relative_to(root) or not p.exists():
        raise FileNotFoundError(rel)
    return p


def _file_info(p: Path) -> dict:
    st = p.stat()
    rp = p.resolve()
    key, root = next((k, r.resolve()) for k, r in ROOTS.items() if rp.is_relative_to(r.resolve()))
    rel = rp.relative_to(root)
    return {"path": f"{key}/{rel}", "root": key, "abs": str(rp), "name": p.name,
            "kind": rel.parts[0] if len(rel.parts) > 1 else "",
            "song": rel.parts[1] if len(rel.parts) > 2 else "", "ext": p.suffix.lower(),
            "size": st.st_size, "mtime": st.st_mtime, "dir": p.is_dir()}


VIDEO_SHOW = {".mp4", ".otio", ".srt"}


def recent_outputs(limit: int = 100) -> list[dict]:
    files = []
    if OUT_ROOT.exists():
        for p in OUT_ROOT.rglob("*"):
            if "/acestep/" in str(p) or "/stems/" in str(p) or "/midi/" in str(p):
                if p.suffix.lower() != ".mid":
                    continue
            if p.suffix.lower() in (".png", ".jpg") and "/images/" not in str(p):
                continue  # only finished pictures, not frames or artwork inside other jobs
            if p.suffix.lower() == ".txt" and "/images/" in str(p):
                continue  # prompt notes beside each picture
            if p.is_file() and p.suffix.lower() in SHOW_EXT and not p.name.endswith(".raw.wav"):
                files.append(p)
    if VIDEO_OUT.exists():
        for p in VIDEO_OUT.rglob("*"):
            if "/segments/" in str(p) or "/stems/" in str(p) or "/cache/" in str(p) \
                    or p.name.startswith(".") or not p.is_file():
                continue
            if p.suffix.lower() in VIDEO_SHOW or (p.suffix == ".png" and p.name.startswith("thumbnail")):
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


async def api_queue(request):
    """Background queue (run by the studio MCP server) and explainer projects."""
    projects = explainer.list_projects()
    videos = {pr["title"]: pr["video"] for pr in projects}
    jobs = [dict(j, video=videos.get(j["title"])) for j in jobqueue.list_jobs(40)]
    return ok(jobs=jobs, projects=projects[:30])


async def api_queue_add(request):
    data = await body(request)
    music = data.get("music") or None
    proj = explainer.create(data.get("title", ""), data.get("script") or None, None,
                            data.get("aspect") or "16:9", data.get("voice") or None,
                            float(data.get("voice_speed") or 1.0), music,
                            data.get("style") or None, None, True,
                            data.get("captions") or None, data.get("transition") or None,
                            data.get("motion") or None, bool(data.get("sfx", True)))
    job = jobqueue.add("explainer", proj["title"], {"title": proj["title"]}, via="dashboard")
    audit("queue_add", title=proj["title"], scenes=len(proj["scenes"]))
    return ok(job=job, scenes=len(proj["scenes"]), message=f"Queued '{proj['title']}' ({len(proj['scenes'])} scenes)")


async def api_queue_action(request):
    data = await body(request)
    jid, action = data.get("id", ""), data.get("action")
    if action == "cancel":
        job = jobqueue.cancel(jid)
    elif action == "retry":
        job = jobqueue.retry(jid)
    elif action == "rebuild":
        title = explainer._load(data.get("title", ""))["title"]
        job = jobqueue.add("explainer", title, {"title": title}, via="dashboard")
    else:
        raise ValueError("action must be cancel, retry or rebuild")
    audit("queue_" + action, id=jid or job["id"])
    return ok(job=job, message=jobqueue.summary(job).split("\n")[0])


async def style_sample(request):
    name = request.path_params["name"]
    p = HERE / "static" / "styles" / f"{name}.jpg"
    if not re.fullmatch(r"[a-z0-9-]{1,30}", name) or not p.is_file():
        return PlainTextResponse("not found", status_code=404)
    return FileResponse(p, headers={"Cache-Control": "max-age=86400"})


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
    p = _safe_path(data.get("path") or "music/")
    target = p if p.is_dir() or data.get("file") else p.parent
    subprocess.Popen(["xdg-open", str(target)], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return ok(message=f"Opened {target.name or target}")


# --- photo studio ---------------------------------------------------------
PHOTOS = OUT_ROOT / "images"          # AI pictures land here (studio_make_image)
MY_PHOTOS = PHOTOS / "my-photos"      # photos you add by hand
PHOTO_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
MAX_PHOTO = 50 * 1024 * 1024
EDITORS = {"gimp": "gimp", "krita": "krita"}


def _photo_notes(p: Path) -> dict:
    """The .txt that studio_make_image writes beside each picture: Title/Prompt/Model/Seed."""
    t = p.with_suffix(".txt")
    info = {}
    if t.is_file():
        for line in t.read_text(errors="replace").splitlines():
            for part in line.split("  "):
                k, sep, v = part.partition(":")
                if sep and k.strip() in ("Title", "Prompt", "Model", "Size", "Seed", "Made"):
                    info[k.strip().lower()] = v.strip()
    return info


def list_photos() -> list[dict]:
    if not PHOTOS.exists():
        return []
    out = []
    for p in PHOTOS.rglob("*"):
        if p.is_file() and p.suffix.lower() in PHOTO_EXT:
            f = _file_info(p)
            notes = _photo_notes(p)
            f.update(notes=notes, source="ai" if notes.get("prompt") else "mine",
                     album=str(p.parent.relative_to(PHOTOS)) if p.parent != PHOTOS else "")
            out.append(f)
    out.sort(key=lambda f: f["mtime"], reverse=True)
    return out


def _photo_path(rel: str) -> Path:
    p = _safe_path(rel)
    if not p.is_relative_to(PHOTOS.resolve()) or p.suffix.lower() not in PHOTO_EXT:
        raise PermissionError("Only pictures in the photo studio")
    return p


def _clean_name(name: str) -> str:
    stem, ext = os.path.splitext(Path(name).name)
    stem = re.sub(r"[^\w .()-]+", "_", stem).strip(" .") or "photo"
    return stem[:80] + ext.lower()


def _store(name: str, data: bytes) -> Path:
    if Path(name).suffix.lower() not in PHOTO_EXT:
        raise ValueError(f"{name}: not a picture ({', '.join(sorted(PHOTO_EXT))})")
    if len(data) > MAX_PHOTO:
        raise ValueError(f"{name}: larger than 50 MB")
    from studio.images import _unique
    MY_PHOTOS.mkdir(parents=True, exist_ok=True)
    dest = _unique(MY_PHOTOS / _clean_name(name))
    dest.write_bytes(data)
    return dest


async def api_photos(request):
    import anyio
    return ok(await anyio.to_thread.run_sync(list_photos))


async def api_photos_upload(request):
    # Multipart, so no JSON check; the X-Jarvis header still blocks other websites.
    if request.headers.get("x-jarvis") != "1":
        raise PermissionError("missing X-Jarvis header")
    form = await request.form(max_files=100, max_part_size=MAX_PHOTO)
    saved, errors = [], []
    for item in form.getlist("files"):
        try:
            saved.append(_store(item.filename or "photo.jpg", await item.read()).name)
        except ValueError as e:
            errors.append(str(e))
    audit("photos_add", count=len(saved))
    msg = f"Added {len(saved)} photo{'s' if len(saved) != 1 else ''}" + (f". Skipped: {'; '.join(errors)}" if errors else "")
    return ok(message=msg, saved=saved)


async def api_photos_import(request):
    data = await body(request)
    src = Path(str(data.get("path", ""))).expanduser()
    if not src.exists():
        raise FileNotFoundError(f"Not found: {src}")
    files = [src] if src.is_file() else sorted(p for p in src.iterdir() if p.is_file() and p.suffix.lower() in PHOTO_EXT)
    if not files:
        raise ValueError("No pictures there")
    saved, errors = [], []
    for f in files[:500]:
        try:
            saved.append(_store(f.name, f.read_bytes()).name)
        except ValueError as e:
            errors.append(str(e))
    audit("photos_add", source=str(src), count=len(saved))
    return ok(message=f"Copied {len(saved)} photo{'s' if len(saved) != 1 else ''} into the photo studio"
              + (f". Skipped: {'; '.join(errors[:5])}" if errors else ""))


async def api_photos_action(request):
    data = await body(request)
    p = _photo_path(data.get("path", ""))
    action = data.get("action")
    audit("photo_" + str(action), path=data.get("path"))
    if action in EDITORS:
        if not shutil.which(EDITORS[action]):
            raise RuntimeError(f"{action} is not installed")
        subprocess.Popen([EDITORS[action], str(p)], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return ok(message=f"Opening {p.name} in {action.title()}")
    if action == "trash":  # recoverable from the desktop's Trash
        for f in (p, p.with_suffix(".txt")):
            if f.exists():
                subprocess.run(["gio", "trash", str(f)], check=True, capture_output=True)
        return ok(message=f"Moved {p.name} to the Trash")
    if action == "rename":
        new = _clean_name(str(data.get("name", "")).strip() + p.suffix)
        dest = p.with_name(new)
        if dest.exists():
            raise ValueError(f"{new} already exists")
        p.rename(dest)
        if p.with_suffix(".txt").exists():
            p.with_suffix(".txt").rename(dest.with_suffix(".txt"))
        return ok(message=f"Renamed to {dest.name}", path=_file_info(dest)["path"])
    raise ValueError(f"Unknown photo action '{action}'")


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
    Route("/api/queue", api_queue),
    Route("/api/queue/add", api_queue_add, methods=["POST"]),
    Route("/api/queue/action", api_queue_action, methods=["POST"]),
    Route("/api/photos", api_photos),
    Route("/api/photos/upload", api_photos_upload, methods=["POST"]),
    Route("/api/photos/import", api_photos_import, methods=["POST"]),
    Route("/api/photos/action", api_photos_action, methods=["POST"]),
    Route("/api/activity", api_activity),
    Route("/styles/{name}.jpg", style_sample),
    Route("/files/{path:path}", files),
    Route("/health", lambda r: Response('{"status":"ok"}', media_type="application/json")),
]
app = Starlette(routes=routes, exception_handlers={
    e: on_error for e in (ValueError, KeyError, RuntimeError, PermissionError, FileNotFoundError,
                          OSError, httpx.HTTPError, subprocess.SubprocessError)})

if __name__ == "__main__":
    uvicorn.run(HostGuard(app), host=BIND, port=PORT, log_level="warning")
