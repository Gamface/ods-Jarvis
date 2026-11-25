"""Photos for Jarvis Studio: text-to-image through Lemonade's stable-diffusion.cpp (CPU).

Finished images go to ~/Music/Jarvis/images/<title>.png with a matching .txt holding the
prompt, which is where the Unified Dashboard's Studio box looks for photos.
"""

import base64
import datetime
import fcntl
import os
from contextlib import contextmanager

import httpx

from .config import STATE_DIR, out_dir, safe_name

LEMONADE = os.environ.get("JARVIS_LEMONADE_URL", "http://127.0.0.1:13305").rstrip("/")
DEFAULT_MODEL = os.environ.get("JARVIS_IMAGE_MODEL", "SD-Turbo")
MODELS = {"SD-Turbo", "SDXL-Turbo", "SD-1.5", "SDXL-Base-1.0"}
SIZES = {"512x512", "768x512", "512x768", "768x768"}


@contextmanager
def heavy_job(name: str):
    """One heavy AI job at a time (image or song), so they can't run out of memory together."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_DIR / "heavy-job.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.write(f"{name} {datetime.datetime.now().isoformat(timespec='seconds')}\n")
        fh.flush()
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _unique(path):
    n = 2
    stem = path.stem
    while path.exists():
        path = path.with_name(f"{stem} ({n}){path.suffix}")
        n += 1
    return path


def unload(model: str | None = None) -> None:
    """Free the memory the image model holds in Lemonade."""
    try:
        httpx.post(f"{LEMONADE}/api/v1/unload", json={"model_name": model or DEFAULT_MODEL}, timeout=30)
    except httpx.HTTPError:
        pass


def generate_png(prompt: str, size: str = "512x512", model: str | None = None,
                 seed: int | None = None, keep_loaded: bool = False) -> bytes:
    """One picture from Lemonade as PNG bytes (no files written)."""
    model = model or DEFAULT_MODEL
    body = {"model": model, "prompt": prompt, "size": size, "n": 1, "response_format": "b64_json"}
    if seed is not None:
        body["seed"] = int(seed)
    with heavy_job("image"):
        try:
            with httpx.Client(timeout=900) as c:
                r = c.post(f"{LEMONADE}/api/v1/images/generations", json=body)
                if r.status_code >= 400:
                    raise RuntimeError(f"Lemonade could not make the image ({r.status_code}): {r.text[:300]}")
                data = r.json().get("data") or []
                if not data or not data[0].get("b64_json"):
                    raise RuntimeError("Lemonade returned no image")
                png = base64.b64decode(data[0]["b64_json"])
                if not keep_loaded:
                    # Free the ~5 GB the image model holds; reloading costs little on CPU.
                    try:
                        c.post(f"{LEMONADE}/api/v1/unload", json={"model_name": model}, timeout=30)
                    except httpx.HTTPError:
                        pass
        except httpx.HTTPError as e:
            raise RuntimeError(f"Lemonade is not answering at {LEMONADE}: {e}") from e

    return png


def make_image(prompt: str, title: str | None = None, size: str = "512x512",
               model: str | None = None, seed: int | None = None, keep_loaded: bool = False) -> str:
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("Describe the picture (subject, style, mood, colours).")
    if len(prompt) > 1000:
        raise ValueError("Keep the prompt under 1000 characters.")
    if size not in SIZES:
        raise ValueError(f"size must be one of {', '.join(sorted(SIZES))}")
    model = model or DEFAULT_MODEL
    if model not in MODELS:
        raise ValueError(f"model must be one of {', '.join(sorted(MODELS))}")
    title = (title or prompt.split(",")[0])[:60]

    started = datetime.datetime.now()
    png = generate_png(prompt, size, model, seed, keep_loaded)

    dest = _unique(out_dir("images") / f"{safe_name(title)}.png")
    dest.write_bytes(png)
    secs = (datetime.datetime.now() - started).total_seconds()
    dest.with_suffix(".txt").write_text(
        f"Title: {title}\nPrompt: {prompt}\nModel: {model}  Size: {size}\n"
        f"Seed: {seed if seed is not None else 'random'}\nMade: {started:%Y-%m-%d %H:%M} ({secs:.0f} s)\n")
    return f"New picture '{title}' ({size}, {model}, {secs:.0f} s): {dest}. It's in the Studio box on the dashboard."
