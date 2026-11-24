"""Sound effects for Jarvis Studio: text-to-audio with ThinkSound through Lemonade (Vulkan).

Finished clips go to ~/Music/Jarvis/sfx/<title>.wav with a matching .txt holding the prompt,
which the Unified Dashboard's Studio box lists under "Sounds".
"""

import datetime
import os

import httpx

from .config import out_dir, safe_name
from .images import _unique, heavy_job

LEMONADE = os.environ.get("JARVIS_LEMONADE_URL", "http://127.0.0.1:13305").rstrip("/")
MODEL = os.environ.get("JARVIS_SFX_MODEL", "ThinkSound-SFX")


def make_sfx(prompt: str, title: str | None = None, duration: float = 5,
             seed: int | None = None, steps: int | None = None) -> str:
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("Describe the sound (e.g. 'thunder rolling over hills, distant rain').")
    if len(prompt) > 500:
        raise ValueError("Keep the description under 500 characters.")
    duration = float(duration)
    if not 1 <= duration <= 30:
        raise ValueError("duration must be 1-30 seconds")
    title = (title or prompt.split(",")[0])[:60]

    body = {"model": MODEL, "prompt": prompt, "duration": duration, "response_format": "wav"}
    if seed is not None:
        body["seed"] = int(seed)
    if steps is not None:
        body["steps"] = int(steps)
    started = datetime.datetime.now()
    with heavy_job("sfx"):
        try:
            with httpx.Client(timeout=1200) as c:
                r = c.post(f"{LEMONADE}/api/v1/audio/generations", json=body)
                if r.status_code >= 400:
                    raise RuntimeError(f"Lemonade could not make the sound ({r.status_code}): {r.text[:300]}")
                if not r.content.startswith(b"RIFF"):
                    raise RuntimeError("Lemonade returned something that isn't a WAV file")
                wav = r.content
                try:  # free the GPU/RAM the model holds
                    c.post(f"{LEMONADE}/api/v1/unload", json={"model_name": MODEL}, timeout=30)
                except httpx.HTTPError:
                    pass
        except httpx.HTTPError as e:
            raise RuntimeError(f"Lemonade is not answering at {LEMONADE}: {e}") from e

    dest = _unique(out_dir("sfx") / f"{safe_name(title)}.wav")
    dest.write_bytes(wav)
    secs = (datetime.datetime.now() - started).total_seconds()
    dest.with_suffix(".txt").write_text(
        f"Title: {title}\nPrompt: {prompt}\nModel: {MODEL}  Length: {duration:.0f} s\n"
        f"Seed: {seed if seed is not None else 'random'}\nMade: {started:%Y-%m-%d %H:%M} ({secs:.0f} s)\n")
    return f"New sound effect '{title}' ({duration:.0f} s, made in {secs:.0f} s): {dest}. It's in the Studio box under Sounds."
