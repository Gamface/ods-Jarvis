"""Small client for the local StemDeck server's HTTP API."""

import json
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx

from .config import STEMDECK_URL

STEMS = ("vocals", "drums", "bass", "guitar", "piano", "other")


class StemDeckError(RuntimeError):
    pass


def _client(timeout: float = 60) -> httpx.Client:
    return httpx.Client(base_url=STEMDECK_URL, timeout=timeout)


def health() -> bool:
    try:
        with _client(5) as c:
            return c.get("/api/health").status_code == 200
    except httpx.HTTPError:
        return False


def list_songs() -> list[dict]:
    with _client() as c:
        r = c.get("/api/jobs")
        r.raise_for_status()
        return [
            {k: j.get(k) for k in ("job_id", "title", "status", "duration", "bpm", "key")}
            for j in r.json()
        ]


def get(job_id: str) -> dict:
    with _client() as c:
        r = c.get(f"/api/jobs/{job_id}")
        if r.status_code == 404:
            raise StemDeckError(f"No song with id {job_id}")
        r.raise_for_status()
        return r.json()


def find(query: str) -> dict:
    """Return the job whose id equals `query`, or whose title contains it."""
    songs = list_songs()
    for s in songs:
        if s["job_id"] == query:
            return get(s["job_id"])
    q = query.lower()
    hits = [s for s in songs if q in (s["title"] or "").lower() and s["status"] == "done"]
    if not hits:
        raise StemDeckError(f"No finished song matching '{query}'. Known: "
                            + ", ".join(s["title"] or s["job_id"] for s in songs[:20]))
    return get(hits[0]["job_id"])


def submit(source: str, vocal_split: bool = False) -> str:
    """Start separating a local audio file path or a YouTube/SoundCloud URL."""
    with _client(600) as c:
        if source.startswith(("http://", "https://")):
            r = c.post("/api/jobs", json={"url": source, "vocal_split": vocal_split})
        else:
            p = Path(source).expanduser()
            if not p.is_file():
                raise StemDeckError(f"File not found: {p}")
            with p.open("rb") as f:
                r = c.post("/api/jobs", files={"file": (p.name, f)}, data={"stems": "[]"})
        if r.status_code >= 400:
            raise StemDeckError(f"StemDeck refused the song: {r.text[:300]}")
        return r.json()["job_id"]


def wait(job_id: str, timeout: float = 3600, poll: float = 5) -> dict:
    t0 = time.time()
    while True:
        job = get(job_id)
        if job["status"] == "done":
            return job
        if job["status"] in ("failed", "error", "cancelled", "stopped"):
            raise StemDeckError(f"Separation {job['status']}: {job.get('stage')}")
        if time.time() - t0 > timeout:
            raise StemDeckError(f"Still separating after {timeout:.0f}s ({job.get('stage')})")
        time.sleep(poll)


def ensure_song(song: str) -> dict:
    """`song` may be an existing title/id, a file path or a URL."""
    if song.startswith(("http://", "https://")) or Path(song).expanduser().is_file():
        return wait(submit(song))
    return find(song)


def download(path: str, dest: Path, params: dict | None = None) -> Path:
    url = path + ("?" + urlencode(params) if params else "")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with _client(900) as c, c.stream("GET", url) as r:
        if r.status_code >= 400:
            raise StemDeckError(f"Download failed ({r.status_code}): {r.read()[:300]!r}")
        with dest.open("wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    return dest


def stem_names(job: dict) -> list[str]:
    return [s["name"] for s in job.get("stems", [])]


def download_stems(job: dict, dest_dir: Path, names: list[str] | None = None) -> dict[str, Path]:
    out = {}
    for name in names or stem_names(job):
        out[name] = download(f"/api/jobs/{job['job_id']}/stems/{name}.wav", dest_dir / f"{name}.wav")
    return out


def render_mix(job: dict, dest: Path, gains: dict[str, float], semitones: int = 0,
               start: float | None = None, end: float | None = None,
               click: bool = False, count_in: int = 0) -> Path:
    """Server-side mixdown: only stems with gain > 0, optional key shift, trim and click."""
    stems = [s for s in stem_names(job) if gains.get(s, 1.0) > 0]
    if not stems and not click:
        raise StemDeckError("That would mute every part.")
    params = {
        "stems": ",".join(stems),
        "gains": ",".join(f"{gains.get(s, 1.0):.3f}" for s in stems),
        "click": str(bool(click)).lower(),
        "count_in": int(count_in),
    }
    if semitones:
        params["pitches"] = ",".join(str(int(semitones)) for _ in stems)
    if start is not None:
        params["start"] = start
    if end is not None:
        params["end"] = end
    return download(f"/api/jobs/{job['job_id']}/mixdown.wav", dest, params)


def lyrics(job: dict) -> dict | None:
    with _client() as c:
        r = c.get(f"/api/jobs/{job['job_id']}/lyrics")
        if r.status_code >= 400:
            return None
        return r.json()


def lookup_lyrics(job: dict) -> dict | None:
    with _client(120) as c:
        r = c.post(f"/api/jobs/{job['job_id']}/lyrics/lookup")
        if r.status_code >= 400:
            return None
    return lyrics(job)


def sections(job: dict) -> list[dict]:
    return job.get("sections") or []


def section_bounds(job: dict, name: str) -> tuple[float, float]:
    """Find a section such as 'chorus' or 'verse 2'. Returns (start, end) seconds."""
    secs = sections(job)
    words = name.lower().split()
    nth = int(words.pop()) if len(words) > 1 and words[-1].isdigit() else 1
    want = " ".join(words)
    seen = 0
    for i, s in enumerate(secs):
        label = str(s.get("label") or s.get("kind") or s.get("name") or "").lower()
        if label == want or label.startswith(want):
            seen += 1
            if seen < nth:
                continue
            start = float(s.get("start", 0))
            end = s.get("end")
            if end is None:
                end = secs[i + 1]["start"] if i + 1 < len(secs) else job.get("duration")
            return start, float(end)
    names = ", ".join(str(s.get("label") or s.get("kind")) for s in secs) or "none detected"
    raise StemDeckError(f"No section '{name}'. Sections: {names}")


def dump(obj) -> str:
    return json.dumps(obj, indent=2, default=str)
