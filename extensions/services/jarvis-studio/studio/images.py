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
SIZES = {"512x512", "768x512", "512x768", "768x768",
         "1024x576", "576x1024", "768x1152", "1024x1024"}   # the larger ones are for SDXL-Turbo


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


# --- Editing and looking: masked repaint + previews Jarvis can inspect with his vision tool ----
# Masks and previews are drawn with ffmpeg (no imaging library in this venv).
import json
import shlex
import subprocess
from pathlib import Path

HOME = Path.home()
UPLOADS_HOST = HOME / "ods/data/hermes/uploads"
UPLOADS_CONTAINER = "/opt/data/uploads"
IMAGE_ROOTS = [HOME / "Music/Jarvis", HOME / "Studio", HOME / "Videos/Jarvis", HOME / "Pictures",
               HOME / "Documents/Jarvis", UPLOADS_HOST]
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def resolve_image(path: str) -> Path:
    """Accept host paths under the Studio folders, or Hermes' /opt/data/uploads paths."""
    p = str(path or "").strip()
    if p.startswith(UPLOADS_CONTAINER + "/"):
        p = str(UPLOADS_HOST / p[len(UPLOADS_CONTAINER) + 1:])
    full = Path(p).expanduser().resolve()
    if not any(full.is_relative_to(r.resolve()) for r in IMAGE_ROOTS):
        raise ValueError("Use an image under ~/Music/Jarvis, ~/Studio, ~/Videos/Jarvis or the dashboard uploads.")
    if not full.is_file() or full.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
        raise ValueError(f"No PNG/JPEG image at {full}")
    return full


def image_size(path: Path) -> tuple[int, int]:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "json", str(path)],
                         capture_output=True, text=True, timeout=30, check=True).stdout
    s = json.loads(out)["streams"][0]
    return int(s["width"]), int(s["height"])


def _boxes(raw, w: int, h: int, name: str) -> list[tuple[int, int, int, int]]:
    out = []
    for b in raw or []:
        if not isinstance(b, (list, tuple)) or len(b) != 4:
            raise ValueError(f"{name}: each box is [x0, y0, x1, y1] in pixels")
        x0, y0, x1, y1 = (int(round(float(v))) for v in b)
        x0, x1 = sorted((max(0, min(w, x0)), max(0, min(w, x1))))
        y0, y1 = sorted((max(0, min(h, y0)), max(0, min(h, y1))))
        if x1 - x0 < 4 or y1 - y0 < 4:
            raise ValueError(f"{name}: box {b} is too small or outside the {w}x{h} picture")
        out.append((x0, y0, x1, y1))
    return out


def _ffmpeg(args: list[str]) -> None:
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", *args], capture_output=True, text=True, timeout=120)
    if r.returncode:
        raise RuntimeError("ffmpeg failed: " + r.stderr[-400:])


def make_mask(w: int, h: int, edit, keep, feather: float, dest: Path) -> None:
    """White = repaint, black = keep. Edit boxes are feathered; keep boxes stay hard on top."""
    vf = [f"drawbox=x={x0}:y={y0}:w={x1 - x0}:h={y1 - y0}:color=white:t=fill" for x0, y0, x1, y1 in edit]
    if feather > 0:
        vf.append(f"gblur=sigma={feather}")
    vf += [f"drawbox=x={x0}:y={y0}:w={x1 - x0}:h={y1 - y0}:color=black:t=fill" for x0, y0, x1, y1 in keep]
    vf.append("format=gray")
    _ffmpeg(["-f", "lavfi", "-i", f"color=black:s={w}x{h}", "-frames:v", "1", "-vf", ",".join(vf), str(dest)])


def edit_image(image: str, prompt: str, edit_boxes, keep_boxes=None, feather: float = 6,
               model: str = "SDXL-Turbo", seed: int | None = None, title: str | None = None) -> str:
    src = resolve_image(image)
    prompt = (prompt or "").strip()
    if not prompt or len(prompt) > 1000:
        raise ValueError("Describe what the edited areas should show (under 1000 characters).")
    if model not in MODELS:
        raise ValueError(f"model must be one of {', '.join(sorted(MODELS))}")
    w, h = image_size(src)
    edit = _boxes(edit_boxes, w, h, "edit_boxes")
    if not edit:
        raise ValueError("Give at least one edit box: the areas to repaint.")
    keep = _boxes(keep_boxes, w, h, "keep_boxes")
    feather = max(0.0, min(30.0, float(feather)))
    title = (title or f"{src.stem} edit")[:60]
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    mask = STATE_DIR / "edit-mask.png"
    make_mask(w, h, edit, keep, feather, mask)
    started = datetime.datetime.now()
    data = {"model": model, "prompt": prompt, "size": f"{w}x{h}", "response_format": "b64_json"}
    if seed is not None:
        data["seed"] = str(int(seed))
    with heavy_job("image"):
        try:
            with httpx.Client(timeout=900) as c, open(src, "rb") as fi, open(mask, "rb") as fm:
                r = c.post(f"{LEMONADE}/api/v1/images/edits", data=data,
                           files={"image": (src.name, fi, "image/png"), "mask": ("mask.png", fm, "image/png")})
                if r.status_code >= 400:
                    raise RuntimeError(f"Lemonade could not edit the image ({r.status_code}): {r.text[:300]}")
                items = r.json().get("data") or []
                if not items or not items[0].get("b64_json"):
                    raise RuntimeError("Lemonade returned no image")
                png = base64.b64decode(items[0]["b64_json"])
                try:
                    c.post(f"{LEMONADE}/api/v1/unload", json={"model_name": model}, timeout=30)
                except httpx.HTTPError:
                    pass
        except httpx.HTTPError as e:
            raise RuntimeError(f"Lemonade is not answering at {LEMONADE}: {e}") from e
    dest = _unique(out_dir("images") / f"{safe_name(title)}.png")
    dest.write_bytes(png)
    secs = (datetime.datetime.now() - started).total_seconds()
    dest.with_suffix(".txt").write_text(
        f"Title: {title}\nEdited from: {src}\nPrompt: {prompt}\nEdit boxes: {edit}\nKeep boxes: {keep}\n"
        f"Feather: {feather}  Model: {model}  Size: {w}x{h}\nSeed: {seed if seed is not None else 'random'}\n"
        f"Made: {started:%Y-%m-%d %H:%M} ({secs:.0f} s)\n")
    return (f"Edited picture '{title}' ({w}x{h}, {model}, {secs:.0f} s): {dest}. "
            f"Look at it before trusting it: studio_image_preview(['{dest}']).")


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace("%", "\\%")


def preview_images(paths, grid: bool = True, edit_boxes=None, keep_boxes=None, zoom_box=None) -> str:
    """One image: a copy with a labelled pixel grid (and boxes drawn). Several: a labelled contact sheet.
    Written where Hermes can read it, so Jarvis can look with his vision tool."""
    if isinstance(paths, str):
        paths = [paths]
    files = [resolve_image(p) for p in (paths or [])][:12]
    if not files:
        raise ValueError("Give one or more image paths.")
    out_dir_ = UPLOADS_HOST / "previews"
    out_dir_.mkdir(parents=True, exist_ok=True)
    dest = out_dir_ / f"preview-{datetime.datetime.now():%Y%m%d-%H%M%S}-{os.urandom(2).hex()}.jpg"
    if len(files) == 1 and zoom_box:
        w, h = image_size(files[0])
        (zx0, zy0, zx1, zy1), = _boxes([zoom_box], w, h, "zoom_box")
        zw, zh = zx1 - zx0, zy1 - zy0
        k = max(1.0, min(8.0, 1000.0 / max(zw, zh)))          # enlarge to about 1000 px
        step = next(s for s in (8, 16, 32, 64, 128) if s * k >= 48)
        vf = [f"crop={zw}:{zh}:{zx0}:{zy0}", f"scale={round(zw * k)}:{round(zh * k)}:flags=neighbor"]
        gx0 = (zx0 + step - 1) // step * step
        gy0 = (zy0 + step - 1) // step * step
        for x in range(gx0, zx1, step):
            X = round((x - zx0) * k)
            vf.append(f"drawbox=x={X}:y=0:w=1:h=ih:color=white@0.5:t=fill")
            vf.append(f"drawtext=fontfile={FONT}:text='{x}':x={X + 2}:y=2:fontsize=13:fontcolor=yellow:box=1:boxcolor=black@0.6")
        for y in range(gy0, zy1, step):
            Y = round((y - zy0) * k)
            vf.append(f"drawbox=x=0:y={Y}:w=iw:h=1:color=white@0.5:t=fill")
            vf.append(f"drawtext=fontfile={FONT}:text='{y}':x=2:y={Y + 2}:fontsize=13:fontcolor=yellow:box=1:boxcolor=black@0.6")
        for colour, raw, name in (("red", edit_boxes, "edit"), ("lime", keep_boxes, "keep")):
            for i, (x0, y0, x1, y1) in enumerate(_boxes(raw, w, h, name + "_boxes"), 1):
                X0, Y0 = round((x0 - zx0) * k), round((y0 - zy0) * k)
                vf.append(f"drawbox=x={X0}:y={Y0}:w={round((x1 - x0) * k)}:h={round((y1 - y0) * k)}:color={colour}:t=3")
        out_dir_ = UPLOADS_HOST / "previews"
        out_dir_.mkdir(parents=True, exist_ok=True)
        dest = out_dir_ / f"zoom-{datetime.datetime.now():%Y%m%d-%H%M%S}-{os.urandom(2).hex()}.jpg"
        _ffmpeg(["-i", str(files[0]), "-frames:v", "1", "-vf", ",".join(vf), "-q:v", "3", str(dest)])
        return (f"Zoomed preview of {files[0].name} box {[zx0, zy0, zx1, zy1]} at {k:.1f}x; grid lines every {step} px, "
                f"labelled in the ORIGINAL picture's coordinates. Look at it with your vision tool at "
                f"{UPLOADS_CONTAINER}/previews/{dest.name} (host copy {dest}).")
    if len(files) == 1:
        w, h = image_size(files[0])
        step = 64 if max(w, h) <= 1100 else 128
        vf = []
        if grid:
            vf.append(f"drawgrid=w={step}:h={step}:t=1:color=white@0.35")
            for x in range(0, w, step):
                vf.append(f"drawtext=fontfile={FONT}:text='{x}':x={x + 3}:y=3:fontsize=13:fontcolor=yellow:box=1:boxcolor=black@0.6")
            for y in range(step, h, step):
                vf.append(f"drawtext=fontfile={FONT}:text='{y}':x=3:y={y + 3}:fontsize=14:fontcolor=yellow:box=1:boxcolor=black@0.6")
        for colour, raw, name in (("red", edit_boxes, "edit"), ("lime", keep_boxes, "keep")):
            for i, (x0, y0, x1, y1) in enumerate(_boxes(raw, w, h, name + "_boxes"), 1):
                vf.append(f"drawbox=x={x0}:y={y0}:w={x1 - x0}:h={y1 - y0}:color={colour}:t=3")
                vf.append(f"drawtext=fontfile={FONT}:text='{name} {i}':x={x0 + 4}:y={y0 + 4}:fontsize=16:fontcolor={colour}:box=1:boxcolor=black@0.6")
        _ffmpeg(["-i", str(files[0]), "-frames:v", "1", "-vf", ",".join(vf) or "null", "-q:v", "3", str(dest)])
        what = (f"{files[0].name} ({w}x{h}) with a labelled pixel grid every {step} px; for exact coordinates "
                f"preview again with zoom_box around the detail") if grid else files[0].name
    else:
        cols = 3 if len(files) > 4 else 2
        cell_w, cell_h = 512, 288
        args, chains, labels = [], [], []
        for i, f in enumerate(files):
            args += ["-i", str(f)]
            chains.append(f"[{i}:v]scale={cell_w}:{cell_h}:force_original_aspect_ratio=decrease,"
                          f"pad={cell_w}:{cell_h}:(ow-iw)/2:(oh-ih)/2,"
                          f"drawtext=fontfile={FONT}:text='{i + 1}. {_esc(f.stem[:40])}':x=6:y=6:fontsize=16:"
                          f"fontcolor=white:box=1:boxcolor=black@0.65[c{i}]")
            labels.append(f"[c{i}]")
        layout = "|".join(f"{(i % cols) * cell_w}_{(i // cols) * cell_h}" for i in range(len(files)))
        fc = ";".join(chains) + ";" + "".join(labels) + f"xstack=inputs={len(files)}:layout={layout}:fill=black[out]"
        _ffmpeg([*args, "-filter_complex", fc, "-map", "[out]", "-frames:v", "1", "-q:v", "3", str(dest)])
        what = "contact sheet: " + "; ".join(f"{i + 1}. {f.name}" for i, f in enumerate(files))
    container = f"{UPLOADS_CONTAINER}/previews/{dest.name}"
    return (f"Preview ready ({what}). Look at it with your vision tool at {container} "
            f"(host copy {dest}).")


def _means(path_spec: list[str]) -> tuple[float, float, float]:
    out = subprocess.run(["magick", *path_spec, "-format", "%[fx:mean.r] %[fx:mean.g] %[fx:mean.b]", "info:"],
                         capture_output=True, text=True, timeout=60, check=True).stdout.split()
    return tuple(float(v) for v in out[:3])


def composite_image(base: str, source: str, source_box, at, scale: float = 1.0, feather: float = 8,
                    key_fuzz: float = 0, match_colour: bool = True, title: str | None = None) -> str:
    """Paste a region of one picture into another with ImageMagick: crop, scale, optionally key out the
    background colour around the object, match its colour to the target spot, feather the edges."""
    b, s = resolve_image(base), resolve_image(source)
    sw, sh = image_size(s)
    bw, bh = image_size(b)
    (x0, y0, x1, y1), = _boxes([source_box], sw, sh, "source_box")
    scale = max(0.1, min(4.0, float(scale)))
    cw, ch = max(1, round((x1 - x0) * scale)), max(1, round((y1 - y0) * scale))
    if not isinstance(at, (list, tuple)) or len(at) != 2:
        raise ValueError("at is [x, y]: where the top-left of the pasted region goes in the base picture")
    px, py = int(at[0]), int(at[1])
    if px < 0 or py < 0 or px + cw > bw or py + ch > bh:
        raise ValueError(f"The pasted region ({cw}x{ch} at {px},{py}) must fit inside the {bw}x{bh} base")
    feather = max(0.0, min(40.0, float(feather)))
    crop = ["(", str(s), "-crop", f"{x1 - x0}x{y1 - y0}+{x0}+{y0}", "+repage", "-resize", f"{cw}x{ch}!"]
    adjust = []
    if match_colour:
        src_m = _means([str(s), "-crop", f"{x1 - x0}x{y1 - y0}+{x0}+{y0}", "+repage"])
        dst_m = _means([str(b), "-crop", f"{cw}x{ch}+{px}+{py}", "+repage"])
        for ch_name, sm, dm in zip("RGB", src_m, dst_m):
            adjust += ["-channel", ch_name, "-evaluate", "add", f"{(dm - sm) * 100:.2f}%"]
        adjust += ["+channel"]
    key = []
    if key_fuzz and float(key_fuzz) > 0:
        key = ["-alpha", "set", "-fuzz", f"{max(1.0, min(60.0, float(key_fuzz))):.0f}%",
               "-fill", "none", "-draw", "color 0,0 floodfill",
               "-draw", f"color {cw - 1},0 floodfill", "-draw", f"color 0,{ch - 1} floodfill",
               "-draw", f"color {cw - 1},{ch - 1} floodfill"]
    f = int(round(feather))
    title = (title or f"{b.stem} composite")[:60]
    dest = _unique(out_dir("images") / f"{safe_name(title)}.png")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    cut, alpha, final = (STATE_DIR / n for n in ("comp-cut.png", "comp-alpha.png", "comp-final.png"))
    f = int(round(feather))
    edge = ["-size", f"{cw}x{ch}", "xc:black", "-fill", "white",
            "-draw", f"rectangle {f},{f} {max(f, cw - 1 - f)},{max(f, ch - 1 - f)}",
            *(["-blur", f"0x{max(0.5, feather / 2):.1f}"] if f else [])]
    steps = [
        ["magick", *crop[1:], *adjust, "-alpha", "set", *key, str(cut)],
        ["magick", str(cut), "-alpha", "extract", "(", *edge, ")", "-compose", "multiply", "-composite", str(alpha)],
        ["magick", str(cut), str(alpha), "-alpha", "off", "-compose", "copyopacity", "-composite", str(final)],
        ["magick", str(b), str(final), "-geometry", f"+{px}+{py}", "-compose", "over", "-composite", str(dest)],
    ]
    for cmd in steps:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode:
            raise RuntimeError("ImageMagick failed: " + r.stderr[-400:])
    dest.with_suffix(".txt").write_text(
        f"Title: {title}\nComposite: base {b}\nSource: {s} box {[x0, y0, x1, y1]} scale {scale}\n"
        f"Placed at: {px},{py} ({cw}x{ch})  Feather: {feather}  Key fuzz: {key_fuzz}  Colour match: {match_colour}\n")
    return (f"Composite '{title}' saved: {dest}. Look at it before trusting it: "
            f"studio_image_preview(['{dest}'], grid=False).")


UPSCALERS = {"RealESRGAN-x4plus", "RealESRGAN-x4plus-anime"}


def upscale_image(image: str, model: str = "RealESRGAN-x4plus", title: str | None = None) -> str:
    """4x upscale with Real-ESRGAN through Lemonade (first use downloads the 64 MB model)."""
    src = resolve_image(image)
    if model not in UPSCALERS:
        raise ValueError(f"model is one of {', '.join(sorted(UPSCALERS))} (the anime one is for drawn/cartoon art)")
    w, h = image_size(src)
    if w * h > 2_400_000:
        raise ValueError(f"{w}x{h} is already large; upscale pictures under about 2.4 megapixels.")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    png_in = src
    if src.suffix.lower() != ".png":
        png_in = STATE_DIR / "upscale-in.png"
        _ffmpeg(["-i", str(src), "-frames:v", "1", str(png_in)])
    title = (title or f"{src.stem} 4x")[:60]
    started = datetime.datetime.now()
    with heavy_job("image"):
        try:
            with httpx.Client(timeout=1800) as c:
                r = c.post(f"{LEMONADE}/api/v1/images/upscale",
                           json={"image": base64.b64encode(png_in.read_bytes()).decode("ascii"), "model": model})
                if r.status_code >= 400:
                    raise RuntimeError(f"Lemonade could not upscale ({r.status_code}): {r.text[:300]}")
                items = r.json().get("data") or []
                if not items or not items[0].get("b64_json"):
                    raise RuntimeError("Lemonade returned no image")
                png = base64.b64decode(items[0]["b64_json"])
                try:
                    c.post(f"{LEMONADE}/api/v1/unload", json={"model_name": model}, timeout=30)
                except httpx.HTTPError:
                    pass
        except httpx.HTTPError as e:
            raise RuntimeError(f"Lemonade is not answering at {LEMONADE}: {e}") from e
    dest = _unique(out_dir("images") / f"{safe_name(title)}.png")
    dest.write_bytes(png)
    nw, nh = image_size(dest)
    secs = (datetime.datetime.now() - started).total_seconds()
    dest.with_suffix(".txt").write_text(f"Title: {title}\nUpscaled from: {src} ({w}x{h})\nModel: {model}  Size: {nw}x{nh}\n"
                                        f"Made: {started:%Y-%m-%d %H:%M} ({secs:.0f} s)\n")
    return (f"Upscaled {src.name} {w}x{h} -> {nw}x{nh} ({model}, {secs:.0f} s): {dest}. "
            f"Look at it (zoom into fine detail and lettering) before using it.")
