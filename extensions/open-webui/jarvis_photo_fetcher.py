"""
title: Jarvis Photo Fetcher
description: Search Unsplash, Pexels and Pixabay (official free APIs) and save photos to a folder, with a credits list.
version: 1.1
requirements:
"""

import asyncio
import csv
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from pydantic import BaseModel, Field

USER_AGENT = "Jarvis-PhotoFetch/1.1 (personal use)"
# Inside ODS's chat container this folder is ODS's data/open-webui/photos on the host
SAVE_ROOT = Path("/app/backend/data/photos")
MAX_PER_SOURCE = 30        # these sites ban mass downloading
MAX_BYTES = 25 * 1024 * 1024  # skip anything bigger than 25 MB
MAX_FAILS_IN_A_ROW = 3     # give up on a site after this many failed downloads in a row
CREDIT_FIELDS = ["file", "source", "photographer", "page_url", "license", "query"]
EXT_BY_TYPE = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


# ---------- helpers ----------

def get_json(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def download(url, dest_stem, headers=None):
    """Stream url to dest_stem + the right extension. Returns the saved Path."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as resp:
        ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        ext = EXT_BY_TYPE.get(ctype)
        if not ext:
            raise ValueError(f"not a photo (Content-Type {ctype or 'missing'})")
        size = int(resp.headers.get("Content-Length") or 0)
        if size > MAX_BYTES:
            raise ValueError(f"too big ({size // 1048576} MB)")
        dest = dest_stem.with_name(dest_stem.name + ext)
        tmp = dest.with_name(dest.name + ".part")
        total = 0
        try:
            with tmp.open("wb") as f:
                while chunk := resp.read(65536):
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise ValueError("too big (over 25 MB)")
                    f.write(chunk)
            tmp.rename(dest)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    return dest


def slugify(text, limit=40):
    text = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").lower()).strip("-")
    return text[:limit].strip("-") or "photo"


def add_params(url, **params):
    """Add or replace query parameters on a URL (Unsplash raw URLs already have some)."""
    parts = urllib.parse.urlsplit(url)
    q = dict(urllib.parse.parse_qsl(parts.query))
    q.update({k: str(v) for k, v in params.items()})
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(q)))


def explain_http(name, code):
    if code == 401:
        return f"{name}: HTTP 401 (the API key is wrong)"
    if code in (403, 429):
        return f"{name}: HTTP {code} (rate limit reached or key not allowed; try again later)"
    return f"{name}: HTTP {code}"


# ---------- sources ----------
# Each returns a list of dicts: id, image_url, photographer, page_url, desc, license

def search_unsplash(query, count, key, orientation):
    params = {"query": query, "per_page": count, "content_filter": "high"}
    if orientation:
        params["orientation"] = "squarish" if orientation == "square" else orientation
    data = get_json("https://api.unsplash.com/search/photos?" + urllib.parse.urlencode(params),
                    {"Authorization": f"Client-ID {key}", "Accept-Version": "v1"})
    out = []
    for p in data.get("results", []):
        out.append({
            "id": p["id"],
            # 2400 px wide JPEG instead of the full-size original (often 20+ MB)
            "image_url": add_params(p["urls"]["raw"], w=2400, q=85, fm="jpg", fit="max"),
            "photographer": p["user"]["name"],
            "page_url": p["links"]["html"],
            "desc": p.get("alt_description") or p.get("description") or query,
            "license": "Unsplash License",
            # Unsplash's API rules require pinging this when a photo is downloaded
            "_track": p["links"].get("download_location"),
        })
    return out


def search_pexels(query, count, key, orientation):
    # Pexels has no safe-search setting in its API
    params = {"query": query, "per_page": count}
    if orientation:
        params["orientation"] = orientation
    data = get_json("https://api.pexels.com/v1/search?" + urllib.parse.urlencode(params),
                    {"Authorization": key})
    return [{
        "id": str(p["id"]),
        # large2x is about 1880 px; "original" can be tens of MB
        "image_url": p["src"].get("large2x") or p["src"]["original"],
        "photographer": p["photographer"],
        "page_url": p["url"],
        "desc": p.get("alt") or query,
        "license": "Pexels License",
    } for p in data.get("photos", [])]


def search_pixabay(query, count, key, orientation):
    params = {"key": key, "q": query, "image_type": "photo", "safesearch": "true",
              "per_page": max(3, count)}  # Pixabay minimum is 3
    if orientation in ("landscape", "portrait"):
        params["orientation"] = "horizontal" if orientation == "landscape" else "vertical"
    data = get_json("https://pixabay.com/api/?" + urllib.parse.urlencode(params))
    return [{
        "id": str(p["id"]),
        "image_url": p["largeImageURL"],
        "photographer": p["user"],
        "page_url": p["pageURL"],
        "desc": p.get("tags") or query,
        "license": "Pixabay Content License",
    } for p in data.get("hits", [])[:count]]


SOURCES = {
    "unsplash": ("UNSPLASH_ACCESS_KEY", search_unsplash),
    "pexels": ("PEXELS_API_KEY", search_pexels),
    "pixabay": ("PIXABAY_API_KEY", search_pixabay),
}


# ---------- credits ----------

def read_credits(path):
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return [r for r in csv.DictReader(f) if r.get("file")]


def write_credits(out_dir, rows):
    """Rewrite credits.csv and CREDITS.md from the full list of rows."""
    with (out_dir / "credits.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CREDIT_FIELDS, extrasaction="ignore", restval="")
        w.writeheader()
        w.writerows(rows)
    lines = ["# Photo credits", "", "Free photos; credit is appreciated and sometimes expected.", ""]
    for r in rows:
        source = (r.get("source") or "unknown site").title()
        line = f"- `{r['file']}`: photo by {r.get('photographer') or 'unknown'} on {source}"
        if r.get("page_url"):
            line += f" ({r['page_url']})"
        if r.get("license"):
            line += f", {r['license']}"
        lines.append(line)
    (out_dir / "CREDITS.md").write_text("\n".join(lines) + "\n")


# ---------- main job ----------

def _run(query, count, sources, orientation, keys, host_root):
    count = max(1, min(int(count or 10), MAX_PER_SOURCE))
    orientation = (orientation or "").strip().lower()
    if orientation not in ("", "landscape", "portrait", "square"):
        orientation = ""
    out_dir = SAVE_ROOT / slugify(query, 60)
    out_dir.mkdir(parents=True, exist_ok=True)
    credits_path = out_dir / "credits.csv"

    rows = read_credits(credits_path)
    seen = {Path(r["file"]).stem for r in rows}
    seen |= {p.stem for p in out_dir.iterdir() if p.is_file() and not p.name.endswith(".part")}
    secrets = [k for k in keys.values() if k]

    def clean(msg):  # never echo an API key back into the chat
        for s in secrets:
            msg = msg.replace(s, "***")
        return msg

    saved, skipped, errors, new_rows = [], [], [], []
    names = [s.strip().lower() for s in (sources or "unsplash,pexels,pixabay").split(",") if s.strip()]
    for name in dict.fromkeys(names):  # drop repeats, keep order
        if name not in SOURCES:
            errors.append(f"unknown source: {name}")
            continue
        key_name, search = SOURCES[name]
        key = keys.get(key_name)
        if not key:
            skipped.append(f"{name} (no key set)")
            continue
        try:
            results = search(query, count, key, orientation or None)
        except urllib.error.HTTPError as e:
            errors.append(explain_http(name, e.code))
            continue
        except Exception as e:
            errors.append(clean(f"{name}: {e}"))
            continue
        if not results:
            skipped.append(f"{name} (no results for “{query}”)")
            continue

        fails = 0
        for r in results:
            stem = f"{name}_{slugify(str(r['id']), 30)}_{slugify(r['desc'])}"
            if stem in seen:
                continue
            try:
                dest = download(r["image_url"], out_dir / stem)
            except urllib.error.HTTPError as e:
                errors.append(f"{name} {r['id']}: download failed, HTTP {e.code}")
                fails += 1
            except Exception as e:
                errors.append(clean(f"{name} {r['id']}: {e}"))
                fails += 1
            else:
                fails = 0
                if r.get("_track"):
                    try:
                        get_json(r["_track"], {"Authorization": f"Client-ID {key}"})
                    except Exception:
                        pass
                new_rows.append({"file": dest.name, "source": name, "photographer": r["photographer"],
                                 "page_url": r["page_url"], "license": r["license"], "query": query})
                seen.add(stem)
                saved.append(f"{dest.name} (photo by {r['photographer']} on {name.title()})")
                time.sleep(0.3)
            if fails >= MAX_FAILS_IN_A_ROW:
                errors.append(f"{name}: stopped after {fails} failed downloads in a row")
                break

    if new_rows:
        write_credits(out_dir, rows + new_rows)

    host_root = (host_root or "").rstrip("/")
    return {"folder_in_container": str(out_dir),
            "folder_on_computer": f"{host_root}/{out_dir.name}" if host_root
            else f"data/open-webui/photos/{out_dir.name} inside your ODS folder",
            "saved_count": len(saved), "saved": saved,
            "skipped_sources": skipped, "errors": errors}


class Tools:
    class Valves(BaseModel):
        UNSPLASH_ACCESS_KEY: str = Field(default="", description="Free key from unsplash.com/developers")
        PEXELS_API_KEY: str = Field(default="", description="Free key from pexels.com/api")
        PIXABAY_API_KEY: str = Field(default="", description="Free key from pixabay.com/api/docs")
        HOST_PHOTOS_FOLDER: str = Field(
            default="~/ods/data/open-webui/photos",
            description="Where the photos folder is on the computer, shown to you in replies")

    def __init__(self):
        self.valves = self.Valves()

    async def fetch_photos(self, query: str, count: int = 10, orientation: str = "",
                           sources: str = "unsplash,pexels,pixabay", __event_emitter__=None) -> str:
        """
        Search free stock photo sites and download the photos into a folder on the computer.
        Use when the user asks to find, grab, download or save photos or images of something.
        :param query: What to search for, e.g. "cozy cafe winter".
        :param count: Photos per site, 1 to 30. Default 10.
        :param orientation: Optional: "landscape", "portrait" or "square". Leave empty for any.
        :param sources: Comma-separated sites to use: unsplash, pexels, pixabay. Default all three.
        :return: JSON summary of the folder, files saved, photographer credits and any problems.
        """
        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": f"Fetching photos of {query}...", "done": False}})
        keys = {"UNSPLASH_ACCESS_KEY": self.valves.UNSPLASH_ACCESS_KEY.strip(),
                "PEXELS_API_KEY": self.valves.PEXELS_API_KEY.strip(),
                "PIXABAY_API_KEY": self.valves.PIXABAY_API_KEY.strip()}
        result = await asyncio.to_thread(_run, query, count, sources, orientation, keys,
                                         self.valves.HOST_PHOTOS_FOLDER)
        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": f"Saved {result['saved_count']} photos", "done": True}})
        return json.dumps(result, indent=2)
