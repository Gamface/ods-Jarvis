"""Background job queue for long studio work (explainer videos, batches).

Jobs are small JSON files in STATE_DIR/queue, so both the MCP server and the dashboard can
see and add them. Only the MCP server runs the worker: one job at a time, oldest first.
A tool call returns a job id at once instead of holding the connection open for an hour.
"""

import datetime
import fcntl
import json
import os
import secrets
import threading
import time
import traceback
from contextlib import contextmanager
from pathlib import Path

from .config import STATE_DIR

QUEUE_DIR = STATE_DIR / "queue"
KEEP_FINISHED = 200  # older finished jobs are pruned
HANDLERS: dict[str, tuple[str, callable]] = {}  # kind -> (label, fn(args, progress) -> str)
FINISHED = {"done", "failed", "cancelled"}


def handler(kind: str, label: str):
    def deco(fn):
        HANDLERS[kind] = (label, fn)
        return fn
    return deco


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


@contextmanager
def _locked():
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    with open(QUEUE_DIR / ".lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _path(jid: str) -> Path:
    if not jid or not all(c.isalnum() or c == "-" for c in jid):
        raise ValueError("bad job id")
    return QUEUE_DIR / f"{jid}.json"


def _read(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def _write(job: dict) -> None:
    p = _path(job["id"])
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(job, indent=1, default=str))
    os.replace(tmp, p)


def get(jid: str) -> dict:
    job = _read(_path(jid))
    if not job:
        raise KeyError(f"No job {jid}")
    return job


def list_jobs(limit: int = 50) -> list[dict]:
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    jobs = [j for j in (_read(p) for p in QUEUE_DIR.glob("*.json")) if j]
    jobs.sort(key=lambda j: j.get("created", ""), reverse=True)
    return jobs[:limit]


def add(kind: str, title: str, args: dict, via: str = "hermes") -> dict:
    if kind not in HANDLERS and kind not in KNOWN_KINDS:
        raise ValueError(f"unknown job kind {kind}")
    jid = datetime.datetime.now().strftime("%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    job = {"id": jid, "kind": kind, "label": KNOWN_KINDS.get(kind, kind), "title": title,
           "args": args, "via": via, "status": "queued", "created": _now(), "started": None,
           "finished": None, "progress": 0.0, "step": "waiting in the queue", "result": None,
           "error": None}
    with _locked():
        _write(job)
    return job


def cancel(jid: str) -> dict:
    with _locked():
        job = get(jid)
        if job["status"] == "queued":
            job.update(status="cancelled", finished=_now(), step="cancelled")
        elif job["status"] == "running":
            job["cancel"] = True  # the job stops at its next step
            job["step"] = "stopping after the current step…"
        _write(job)
    return job


def retry(jid: str) -> dict:
    with _locked():
        job = get(jid)
        if job["status"] not in ("failed", "cancelled"):
            raise ValueError("Only failed or cancelled jobs can be run again")
        job.update(status="queued", started=None, finished=None, error=None, result=None,
                   progress=0.0, step="waiting in the queue", cancel=False)
        _write(job)
    return job


def position(jid: str) -> int:
    queued = sorted((j for j in list_jobs(500) if j["status"] == "queued"), key=lambda j: j["created"])
    return next((i + 1 for i, j in enumerate(queued) if j["id"] == jid), 0)


class Cancelled(Exception):
    pass


def summary(job: dict) -> str:
    s = f"Job {job['id']} ({job['label']}: {job['title']}): {job['status']}"
    if job["status"] == "queued":
        s += f", number {position(job['id'])} in the queue"
    if job["status"] == "running":
        s += f", {round(job.get('progress', 0) * 100)}% — {job.get('step', '')}"
    if job.get("result"):
        s += f"\n{job['result']}"
    if job.get("error"):
        s += f"\nProblem: {job['error']}"
    return s


# --- worker (MCP server only) -------------------------------------------------
KNOWN_KINDS = {"explainer": "Explainer video", "explainer_redo": "Explainer scene redo", "book": "Book"}


def _run_one(job: dict) -> None:
    label, fn = HANDLERS[job["kind"]]
    last = [0.0]

    def progress(step: str, frac: float | None = None) -> None:
        with _locked():
            cur = get(job["id"])
            if cur.get("cancel"):
                raise Cancelled()
            cur["step"] = step
            if frac is not None:
                cur["progress"] = round(max(0.0, min(1.0, frac)), 3)
            _write(cur)
        last[0] = time.time()

    try:
        result = fn(job["args"], progress)
        final = {"status": "done", "result": result, "progress": 1.0, "step": "finished"}
    except Cancelled:
        final = {"status": "cancelled", "step": "cancelled (finished scenes are kept)"}
    except Exception as e:  # report on the job, keep the worker alive
        traceback.print_exc()
        final = {"status": "failed", "error": str(e) or e.__class__.__name__,
                 "step": "failed (finished scenes are kept; run it again to continue)"}
    with _locked():
        cur = get(job["id"])
        cur.update(final, finished=_now())
        _write(cur)


def _prune() -> None:
    done = [j for j in list_jobs(10_000) if j["status"] in FINISHED]
    for j in done[KEEP_FINISHED:]:
        _path(j["id"]).unlink(missing_ok=True)


def _worker() -> None:
    with _locked():  # jobs that were running when the server stopped
        for j in list_jobs(10_000):
            if j["status"] == "running":
                j.update(status="failed", finished=_now(), error="interrupted (the studio server restarted)",
                         step="interrupted; run it again to continue (finished scenes are kept)")
                _write(j)
    while True:
        job = None
        with _locked():
            queued = sorted((j for j in list_jobs(10_000) if j["status"] == "queued"),
                            key=lambda j: j["created"])
            for j in queued:
                if j["kind"] in HANDLERS:
                    j.update(status="running", started=_now(), step="starting")
                    _write(j)
                    job = j
                    break
        if job:
            _run_one(job)
            _prune()
        else:
            time.sleep(3)


def start_worker() -> threading.Thread:
    t = threading.Thread(target=_worker, name="studio-queue", daemon=True)
    t.start()
    return t
