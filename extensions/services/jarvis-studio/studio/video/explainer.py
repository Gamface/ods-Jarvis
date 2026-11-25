"""Long explainer videos built scene by scene, with every scene's pieces kept.

A project lives in ~/Videos/Jarvis/explainers/<title>/:
  project.json   the plan: settings plus scenes [{say, picture, image, text, chapter, seed}]
  cache/         voice, picture and clip files named by a fingerprint of what made them
  <title>.mp4    the finished video, with <title>.srt, chapters.txt and thumbnail.png

Rebuilding only makes what changed: edit scene 4's words and only its voice and clip are
redone, then the video is put back together. Runs as a background job (see jobqueue).
"""

import base64
import hashlib
import json
import os
import re
import time
from pathlib import Path

import httpx

from .. import images
from .. import stemdeck as sd
from ..audio import run
from ..config import safe_name
from ..jobqueue import handler
from . import caption_styles as cst
from . import captions as cap
from . import styles as stl
from . import graphics as gfx
from .common import AUDIO_EXT, FPS, IMAGE_EXT, VIDEO_EXT, probe, render, size_of, vout
from .workflows import SAME, _is_media

KOKORO_URL = os.environ.get("JARVIS_KOKORO_URL", "http://127.0.0.1:8880").rstrip("/")
DEFAULT_VOICE = os.environ.get("JARVIS_EXPLAINER_VOICE", "bm_george")
SR = 48000
GAP = 0.45            # pause after each scene's voice
INTRO = 3.0           # title card
CHAPTER = 2.5         # chapter card
OUTRO = 1.2           # hold after the last scene
MAX_WORDS = 28        # about 10 s of speech per scene when splitting a script
MAX_SCENES = 240
PIC_SIZE = {"16:9": "768x512", "9:16": "512x768", "1:1": "512x512"}


def _key(*parts) -> str:
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:16]


def project_dir(title: str) -> Path:
    return vout("explainers", title)


def _load(title: str) -> dict:
    p = project_dir(title) / "project.json"
    if not p.exists():
        raise FileNotFoundError(f"No explainer called '{title}'. Use video_explainer to start one.")
    return json.loads(p.read_text())


def _save(proj: dict) -> None:
    d = project_dir(proj["title"])
    tmp = d / "project.json.tmp"
    tmp.write_text(json.dumps(proj, indent=1))
    os.replace(tmp, d / "project.json")


# --- planning ---------------------------------------------------------------
def split_script(script: str) -> list[dict]:
    """Script text -> scenes. '# Heading' lines start a chapter; blank lines separate
    paragraphs; long paragraphs are split into ~10 s scenes on sentence boundaries."""
    scenes, chapter = [], None
    text = script.replace("\r", "")
    if "\n" not in text and "\\n" in text:
        text = text.replace("\\n", "\n")
    for block in re.split(r"\n\s*\n", text):
        lines = [ln.strip() for ln in block.strip().splitlines() if ln.strip()]
        body = []
        for ln in lines:
            if ln.startswith("#"):
                if body:
                    scenes += _chunks(" ".join(body), chapter)
                    chapter, body = None, []
                chapter = ln.lstrip("#").strip() or None
            else:
                body.append(ln)
        if body:
            scenes += _chunks(" ".join(body), chapter)
            chapter = None
    return scenes


def _chunks(para: str, chapter: str | None) -> list[dict]:
    sents = re.split(r"(?<=[.!?])\s+", " ".join(para.split()))
    out, cur = [], []
    for s in sents:
        if cur and len(" ".join(cur + [s]).split()) > MAX_WORDS:
            out.append(" ".join(cur))
            cur = []
        cur.append(s)
    if cur:
        out.append(" ".join(cur))
    scenes = [{"say": c} for c in out if c.strip()]
    if scenes and chapter:
        scenes[0]["chapter"] = chapter
    return scenes


def _clean_scene(s, i: int) -> dict:
    if isinstance(s, str):
        s = {"say": s}
    if not isinstance(s, dict):
        raise ValueError(f"Scene {i + 1} must be an object with at least 'say'")
    say = " ".join(str(s.get("say") or "").split())
    if not say:
        raise ValueError(f"Scene {i + 1} has nothing to say")
    out = {"say": say}
    for k in ("picture", "text", "chapter"):
        if s.get(k):
            out[k] = " ".join(str(s[k]).split())[:1000 if k == "picture" else 200]
    if s.get("image"):
        p = Path(str(s["image"])).expanduser()
        if not p.is_file() or p.suffix.lower() not in IMAGE_EXT | VIDEO_EXT:
            raise ValueError(f"Scene {i + 1}: image not found or not a picture/clip: {p}")
        out["image"] = str(p)
    if s.get("seed") is not None:
        out["seed"] = int(s["seed"])
    if s.get("motion"):
        m = str(s["motion"]).strip().lower()
        if m not in stl.MOTIONS:
            raise ValueError(f"Scene {i + 1}: motion must be one of {', '.join(stl.MOTIONS)}")
        out["motion"] = m
    if s.get("voice"):
        v = str(s["voice"]).strip()
        if v.lower().endswith(".wav") and not Path(v).expanduser().is_file():
            raise ValueError(f"Scene {i + 1}: voice file not found: {v}")
        out["voice"] = v
    if s.get("pause"):
        out["pause"] = max(0.0, min(5.0, float(s["pause"])))
    return out


def create(title: str, script: str | None = None, scenes=None, aspect: str = "16:9",
           voice: str | None = None, voice_speed: float = 1.0, music: str | None = None,
           style: str | None = None, seed: int | None = None, intro: bool = True,
           captions: str | None = None, transition: str | None = None, motion: str | None = None,
           sfx: bool = True) -> dict:
    """Check and save the plan. Scenes already made for this title are reused when unchanged."""
    title = " ".join((title or "").split())[:80]
    if not title:
        raise ValueError("Give the explainer a title")
    size_of(aspect)
    voice_speed = float(voice_speed or 1.0)
    if not 0.5 <= voice_speed <= 2.0:
        raise ValueError("voice_speed must be between 0.5 and 2.0")
    if isinstance(scenes, str) and scenes.strip():
        try:
            scenes = json.loads(scenes)
        except ValueError as e:
            raise ValueError(f"scenes must be a JSON list: {e}") from e
    if scenes:
        plan = [_clean_scene(s, i) for i, s in enumerate(scenes)]
    elif script and script.strip():
        p = Path(script.strip()).expanduser()
        text = p.read_text() if ("\n" not in script and len(script) < 255 and p.is_file()) else script
        plan = split_script(text)
    else:
        raise ValueError("Give a script (text) or scenes (a list of {say, picture, ...})")
    if not plan:
        raise ValueError("The script is empty")
    if len(plan) > MAX_SCENES:
        raise ValueError(f"Too many scenes ({len(plan)}); keep it under {MAX_SCENES}")
    voice = (voice or DEFAULT_VOICE).strip()
    if voice.lower().endswith(".wav") and not Path(voice).expanduser().is_file():
        raise ValueError(f"Voice file not found: {voice}")
    for name, val, allowed in (("captions", captions, stl.CAPTIONS), ("transition", transition, stl.TRANSITIONS),
                               ("motion", motion, tuple(stl.MOTION_SETS))):
        if val and val not in allowed:
            raise ValueError(f"{name} must be one of {', '.join(allowed)}")
    proj = {"title": title, "aspect": aspect, "voice": voice, "voice_speed": voice_speed,
            "music": music or None, "style": (style or "").strip() or None,
            "seed": int(seed) if seed is not None else 1234, "intro": bool(intro),
            "captions": captions or None, "transition": transition or None, "motion": motion or None,
            "sfx": bool(sfx), "scenes": plan}
    _save(proj)
    return proj


def update_scene(title: str, scene: int, say: str | None = None, picture: str | None = None,
                 image: str | None = None, text: str | None = None, chapter: str | None = None,
                 new_picture: bool = False, remove: bool = False, motion: str | None = None,
                 voice: str | None = None, pause: float | None = None) -> dict:
    proj = _load(title)
    n = len(proj["scenes"])
    if not 1 <= int(scene) <= n:
        raise ValueError(f"Scene must be 1 to {n}")
    i = int(scene) - 1
    if remove:
        if n == 1:
            raise ValueError("Can't remove the only scene")
        proj["scenes"].pop(i)
    else:
        s = dict(proj["scenes"][i])
        for k, v in (("say", say), ("picture", picture), ("image", image), ("text", text),
                     ("chapter", chapter), ("motion", motion), ("voice", voice), ("pause", pause)):
            if v is not None:
                if v == "":
                    s.pop(k, None)
                else:
                    s[k] = v
        if new_picture:
            s["seed"] = int(time.time()) % 100000
        proj["scenes"][i] = _clean_scene(s, i)
    _save(proj)
    return proj


def describe(title: str) -> str:
    proj = _load(title)
    d = project_dir(title)
    preset, look = stl.resolve(proj.get("style"))
    lines = [f"Explainer '{proj['title']}' ({proj['aspect']}, voice {proj['voice']} ×{proj['voice_speed']}, "
             f"{len(proj['scenes'])} scenes, style {preset or ('custom' if look['prompt'] else 'text slides')}, "
             f"captions {proj.get('captions') or look['captions']}, transitions {proj.get('transition') or look['transition']}). "
             f"Folder: {d}"]
    for i, s in enumerate(proj["scenes"], 1):
        vis = f"image {Path(s['image']).name}" if s.get("image") else (
            f"picture: {s['picture'][:70]}" if s.get("picture") else
            ("auto picture" if look["prompt"] else "text slide"))
        ch = f"[{s['chapter']}] " if s.get("chapter") else ""
        extra = "".join(f", {k} {s[k]}" for k in ("motion", "voice", "pause") if s.get(k))
        lines.append(f"{i}. {ch}{s['say'][:90]}{'…' if len(s['say']) > 90 else ''} ({vis}{extra})")
    mp4 = d / f"{safe_name(proj['title'])}.mp4"
    if mp4.exists():
        lines.append(f"Finished video: {mp4}")
    return "\n".join(lines)


def list_projects() -> list[dict]:
    root = vout("explainers")
    out = []
    for p in sorted(root.glob("*/project.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            proj = json.loads(p.read_text())
        except ValueError:
            continue
        mp4 = p.parent / f"{safe_name(proj['title'])}.mp4"
        out.append({"title": proj["title"], "scenes": len(proj["scenes"]), "aspect": proj["aspect"],
                    "video": str(mp4) if mp4.exists() else None})
    return out


# --- making the pieces ---------------------------------------------------------
def _to_track(src: Path, dest: Path, speed: float = 1.0) -> Path:
    af = [] if abs(speed - 1.0) < 1e-3 else ["-af", f"atempo={speed:.3f}"]
    run(["ffmpeg", "-y", "-v", "error", "-i", str(src), *af, "-ar", str(SR), "-ac", "1",
         "-c:a", "pcm_s16le", str(dest)])
    return dest


def _make_voice(text: str, dest: Path, voice: str, speed: float) -> list | None:
    """Speak `text` into dest (48 kHz mono). Returns Kokoro's word timings, or None for MOSS."""
    raw = dest.with_name(dest.stem + ".raw.wav")
    timings = None
    if voice.lower() == "moss" or voice.lower().endswith(".wav"):
        from ..workflows import speak
        speak(text, raw, None if voice.lower() == "moss" else str(Path(voice).expanduser()))
        _to_track(raw, dest, speed)
    else:
        body = {"model": "kokoro", "input": text, "voice": voice, "response_format": "wav",
                "speed": speed, "stream": False}
        try:
            r = httpx.post(f"{KOKORO_URL}/dev/captioned_speech", timeout=300, json=body)
            if r.status_code == 404:  # older Kokoro: plain speech, no word timings
                r = httpx.post(f"{KOKORO_URL}/v1/audio/speech", timeout=300, json=body)
        except httpx.HTTPError as e:
            raise RuntimeError(f"Kokoro voice service is not answering at {KOKORO_URL}: {e}") from e
        if r.status_code >= 400:
            raise RuntimeError(f"Kokoro could not speak with voice '{voice}' ({r.status_code}): {r.text[:200]}")
        if r.headers.get("content-type", "").startswith("application/json"):
            data = r.json()
            raw.write_bytes(base64.b64decode(data["audio"]))
            timings = data.get("timestamps") or None
        else:
            raw.write_bytes(r.content)
        _to_track(raw, dest)
    raw.unlink(missing_ok=True)
    if timings:
        dest.with_suffix(".json").write_text(json.dumps(timings))
    return timings


def _clip(src: Path, dest: Path, size: tuple[int, int], frames: int, motion: str = "still",
          transition: str = "cut") -> Path:
    """A picture, slide or video clip -> an exact-length silent clip with camera motion and
    the scene transition baked in (fade from/to black for dip, white for flash)."""
    w, h = size
    if src.suffix.lower() in VIDEO_EXT:
        vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={FPS},setsar=1")
        cmd = ["ffmpeg", "-y", "-v", "error", "-stream_loop", "-1", "-i", str(src)]
    else:
        f = max(1, frames - 1)
        z, x, y = "1.0", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
        if motion == "zoom-in":
            z = f"1+0.12*on/{f}"
        elif motion == "zoom-out":
            z = f"1.12-0.12*on/{f}"
        elif motion in ("pan-left", "pan-right", "pan-up", "pan-down"):
            z = "1.14"
            prog = f"(on/{f})" if motion in ("pan-right", "pan-down") else f"(1-on/{f})"
            if motion in ("pan-left", "pan-right"):
                x = f"(iw-iw/zoom)*{prog}"
            else:
                y = f"(ih-ih/zoom)*{prog}"
        vf = (f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,crop={w * 2}:{h * 2},"
              f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={FPS},setsar=1")
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src)]
    dur = frames / FPS
    if transition in ("dip", "flash") and dur > 0.8:
        fd, col = (0.22, "black") if transition == "dip" else (0.12, "white")
        vf += f",fade=t=in:st=0:d={fd}:color={col},fade=t=out:st={dur - fd:.3f}:d={fd}:color={col}"
    vf += f",format=yuv420p,{SAME}"
    cmd += ["-vf", vf, "-frames:v", str(frames), "-an", "-c:v", "libx264", "-preset", "ultrafast",
            "-crf", "16", "-r", str(FPS), str(dest)]
    run(cmd, timeout=900)
    return dest


def _whoosh(dest: Path) -> Path:
    """A soft synthetic whoosh (filtered pink noise), made once."""
    if not dest.exists():
        run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"anoisesrc=d=0.55:c=pink:a=0.6:r={SR}",
             "-af", "highpass=f=400,lowpass=f=4500,afade=t=in:d=0.32:curve=exp,"
                    "afade=t=out:st=0.32:d=0.23,volume=0.5", "-ac", "1", "-c:a", "pcm_s16le", str(dest)])
    return dest


def _silence(dest: Path, frames: int) -> Path:
    if not dest.exists():
        run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"anullsrc=r={SR}:cl=mono",
             "-af", f"atrim=end_sample={frames * SR // FPS}", "-c:a", "pcm_s16le", str(dest)])
    return dest


def _scene_audio(voice: Path, dest: Path, frames: int) -> Path:
    """Voice padded with silence to exactly `frames` video frames (1600 samples each)."""
    if not dest.exists():
        run(["ffmpeg", "-y", "-v", "error", "-i", str(voice), "-af",
             f"apad,atrim=end_sample={frames * SR // FPS}", "-ar", str(SR), "-ac", "1",
             "-c:a", "pcm_s16le", str(dest)])
    return dest


def _music_bed(music: str, d: Path, mood: str | None = None, seconds: float = 120) -> Path | None:
    if not music:
        return None
    if music == "auto":  # write an instrumental to match the style (ACE-Step, a few minutes)
        caption = f"instrumental background music, {mood or 'calm modern underscore'}, no vocals"
        dur = round(min(max(seconds, 30), 180))
        dest = d / "cache" / f"music-{_key(caption, dur)}.wav"
        if not dest.exists():
            from ..workflows import generate_music
            made = generate_music(caption, d / "cache" / "acestep", duration=dur)
            os.replace(made, dest)
        return dest
    if _is_media(music, AUDIO_EXT):
        return Path(music).expanduser()
    dest = d / "cache" / f"music-{_key(music)}.wav"
    if not dest.exists():
        job = sd.ensure_song(music)
        sd.render_mix(job, dest, {n: (0.0 if n == "vocals" else 1.0) for n in sd.stem_names(job)})
    return dest


def _fmt(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def build(title: str, progress=lambda step, frac=None: None) -> str:
    proj = _load(title)
    d = project_dir(proj["title"])
    cache = d / "cache"
    cache.mkdir(exist_ok=True)
    aspect, scenes = proj["aspect"], proj["scenes"]
    w, h = size_of(aspect)
    n = len(scenes)
    preset, look = stl.resolve(proj.get("style"))
    captions = proj.get("captions") or look["captions"]
    transition = proj.get("transition") or look["transition"]
    motion_set = proj.get("motion") or look["motion"]
    used: set[str] = set()
    started = time.time()

    # 1. voices (with word timings from Kokoro for the animated captions)
    voices = []
    for i, s in enumerate(scenes):
        progress(f"voice {i + 1} of {n}", 0.2 * i / n)
        voice = s.get("voice") or proj["voice"]
        k = _key("voice", s["say"], voice, proj["voice_speed"])
        p = cache / f"voice-{k}.wav"
        words = p.with_suffix(".json")
        if not p.exists():
            _make_voice(s["say"], p, voice, proj["voice_speed"])
        timings = json.loads(words.read_text()) if words.exists() else None
        used |= {p.name, words.name}
        voices.append((p, probe(p)["duration"], timings))

    # 2. pictures (keep the image model loaded for the whole batch, then free it)
    psize = PIC_SIZE[aspect]
    visuals, todo = [], []
    for i, s in enumerate(scenes):
        if s.get("image"):
            src = Path(s["image"])
            visuals.append(("file", src, _key("file", str(src), src.stat().st_mtime)))
            continue
        subject = s.get("picture") or (s["say"][:160] if look["prompt"] else None)
        if not subject:
            visuals.append(("slide", None, _key("slide", s.get("text") or s["say"], aspect, i % 4)))
            continue
        prompt = stl.picture_prompt(subject, look["prompt"])
        seed = s.get("seed", proj["seed"] + i)
        k = _key("pic", prompt, psize, seed)
        p = cache / f"pic-{k}.png"
        visuals.append(("pic", p, k))
        if not p.exists():
            todo.append((i, prompt, seed, p))
    try:
        for j, (i, prompt, seed, p) in enumerate(todo):
            progress(f"picture {j + 1} of {len(todo)} (scene {i + 1})", 0.2 + 0.45 * j / max(1, len(todo)))
            p.write_bytes(images.generate_png(prompt, psize, None, seed, keep_loaded=True))
    finally:
        if todo:
            images.unload()

    # 3. clips: each exactly as long as its voice plus a pause; motion and transition baked in
    timeline = []  # (clip, audio, frames, scene index or None, chapter, whoosh at start)
    def card(text: str, kicker: str, idx: int, secs: float, label: str) -> None:
        f = round(secs * FPS)
        k = _key("card", text, kicker, aspect, transition, idx % 4)
        clip, slide = cache / f"clip-{k}.mp4", cache / f"slide-{k}.png"
        if not clip.exists():
            gfx.slide(text, (w, h), slide, kicker, idx)
            _clip(slide, clip, (w, h), f, "zoom-in" if motion_set != "still" else "still", transition)
        sil = _silence(cache / f"silence-{f}.wav", f)
        used.update({clip.name, slide.name, sil.name})
        timeline.append((clip, sil, f, None, label, True))

    if proj.get("intro", True):
        card(proj["title"], (look.get("label") or "Explainer") if preset else "Explainer", 0, INTRO, "Intro")
    part = 0
    for i, (s, (vp, vlen, _), (kind, src, vk)) in enumerate(zip(scenes, voices, visuals)):
        progress(f"clip {i + 1} of {n}", 0.65 + 0.25 * i / n)
        if s.get("chapter"):
            part += 1
            card(s["chapter"], f"Part {part}", part, CHAPTER, s["chapter"])
        hold = GAP + s.get("pause", 0) + (OUTRO if i == n - 1 else 0)
        f = max(1, -(-int((vlen + hold) * 1000) * FPS // 1000))  # round up to whole frames
        if kind == "slide":
            img = cache / f"slide-{vk}.png"
            if not img.exists():
                gfx.slide(s.get("text") or s["say"], (w, h), img, None, i % 4)
            used.add(img.name)
            src = img
        motion = stl.scene_motion(motion_set, i, s.get("motion"))
        ck = _key("clip", vk, f, aspect, motion, transition)
        clip = cache / f"clip-{ck}.mp4"
        if not clip.exists():
            _clip(src, clip, (w, h), f, motion, transition)
        audio = _scene_audio(vp, cache / f"track-{_key(vp.name, f)}.wav", f)
        used.update({clip.name, audio.name, *([src.name] if kind == "pic" else [])})
        timeline.append((clip, audio, f, i, None, transition == "whoosh" and i > 0 and not s.get("chapter")))

    # 4. put it together
    progress("putting the video together", 0.9)
    vlist, alist = d / "clips.txt", d / "audio.txt"
    vlist.write_text("".join(f"file '{c}'\n" for c, *_ in timeline))
    alist.write_text("".join(f"file '{a}'\n" for _, a, *_ in timeline))
    narration = d / "narration.wav"
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(alist),
         "-c:a", "pcm_s16le", str(narration)])
    total = sum(item[2] for item in timeline) / FPS

    cues, words, chapters, whooshes, t = [], [], [], [], 0.0
    for clip, audio, f, si, chap, wh in timeline:
        if chap:
            chapters.append((t, chap))
        if wh and proj.get("sfx", True) and t > 0.3:
            whooshes.append(t - 0.3)
        if si is not None:
            vlen, timings = voices[si][1], voices[si][2]
            cues += cap.cues_from_text(scenes[si]["say"], t + 0.05, max(0.3, vlen - 0.1))
            words += cst.words_for(scenes[si]["say"], timings, t, vlen)
        t += f / FPS
    name = safe_name(proj["title"])
    srt = cap.write_srt(cues, d / f"{name}.srt")
    ass = cst.write(words, d / "captions.ass", (w, h), captions)
    from .common import ass_path

    inputs = ["-f", "concat", "-safe", "0", "-i", str(vlist), "-i", str(narration)]
    vchain = ["setsar=1"]
    if look.get("grade"):
        vchain.append(look["grade"])
    if ass:
        vchain.append(f"ass={ass_path(ass)}")
    vchain += ["fade=t=in:st=0:d=0.6", f"fade=t=out:st={max(0, total - 1):.2f}:d=1"]
    graph = [f"[0:v]{','.join(vchain)}[vf]"]
    voice_lbl = "[1:a]"
    nxt = 2
    if whooshes:
        inputs += ["-i", str(_whoosh(cache / "whoosh.wav"))]
        used.add("whoosh.wav")
        m = len(whooshes)
        graph.append(f"[{nxt}:a]asplit={m}" + "".join(f"[w{i}]" for i in range(m)) if m > 1 else f"[{nxt}:a]anull[w0]")
        graph += [f"[w{i}]adelay={int(ts * 1000)}:all=1[wd{i}]" for i, ts in enumerate(whooshes)]
        graph.append("[1:a]" + "".join(f"[wd{i}]" for i in range(m)) +
                     f"amix=inputs={m + 1}:normalize=0:duration=first[vx]")
        voice_lbl = "[vx]"
        nxt += 1
    bed = _music_bed(proj.get("music"), d, look.get("music"), total)
    if bed:
        used.add(bed.name) if bed.parent == cache else None
        inputs += ["-stream_loop", "-1", "-i", str(bed)]
        graph += [f"{voice_lbl}aformat=channel_layouts=stereo,asplit[nv][sc]",
                  f"[{nxt}:a]aformat=channel_layouts=stereo,volume=0.3[mb]",
                  "[mb][sc]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=450[duck]",
                  f"[nv][duck]amix=inputs=2:normalize=0:duration=first,"
                  f"afade=t=out:st={max(0, total - 1.5):.2f}:d=1.5,loudnorm=I=-16:TP=-1.5:LRA=11[a]"]
    else:
        graph += [f"{voice_lbl}aformat=channel_layouts=stereo,loudnorm=I=-16:TP=-1.5:LRA=11[a]"]
    tmp = d / f".{name}.part.mp4"
    render(inputs, ";".join(graph), "vf", tmp, "a", extra=["-t", f"{total:.3f}"])
    dest = d / f"{name}.mp4"
    os.replace(tmp, dest)

    first = next((tt for tt, item in zip(_starts(timeline), timeline) if item[3] is not None), 0)
    thumb = gfx.thumbnail(gfx.grab_frame(dest, first + 1.5, d / "frame.jpg"), proj["title"], (w, h),
                          d / "thumbnail.png")
    # YouTube wants the first chapter at 0:00 and none shorter than 10 s: fold the short
    # intro into the first real chapter.
    if len(chapters) > 1 and chapters[0][1] == "Intro" and chapters[1][0] < 10:
        chapters = [(0.0, chapters[1][1])] + chapters[2:]
    chap_txt = "\n".join(f"{_fmt(tt)} {c}" for tt, c in chapters) if len(chapters) >= 3 else ""
    (d / "chapters.txt").write_text(chap_txt + "\n" if chap_txt else "")

    for f in cache.iterdir():  # drop pieces no scene uses any more
        if f.is_file() and f.name not in used and not f.name.startswith("music-"):
            f.unlink(missing_ok=True)
    mins = (time.time() - started) / 60
    look_txt = f"style {preset}" if preset else ("custom picture style" if look["prompt"] else "text slides")
    return "\n".join([
        f"Explainer video ready ({n} scenes, {_fmt(total)} long, {look_txt}, {captions} captions, "
        f"{transition} transitions, made in {mins:.0f} min): {dest}",
        f"Subtitles: {srt}", f"Thumbnail: {thumb}",
        f"YouTube chapters ({len(chapters)}): {d / 'chapters.txt'}" if chap_txt
        else "No YouTube chapters (YouTube needs at least 3; add '# Heading' lines or chapter fields).",
        f"Pictures made this run: {len(todo)}. Redo one scene with video_explainer_scene."])


def _starts(timeline):
    t = 0.0
    for _, _, f, *_ in timeline:
        yield t
        t += f / FPS


@handler("explainer", "Explainer video")
def _job(args: dict, progress) -> str:
    return build(args["title"], progress)
