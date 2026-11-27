"""Design programs for Jarvis Studio: Inkscape (SVG, text on artwork), GIMP (finishing filters,
cut-outs) and Krita (layered hand-off files and export).

Everything runs headless and writes NEW files: PNGs to ~/Music/Jarvis/images (the dashboard's
Studio box) and editable sources (.svg, .ora) to ~/Music/Jarvis/designs. Originals are never
overwritten. Only `open_in_app` shows a window, on the operator's desktop, for hand finishing.
"""

import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from .config import STATE_DIR, out_dir, safe_name
from .images import _boxes, _unique, image_size, resolve_image

INKSCAPE = shutil.which("inkscape") or "inkscape"
GIMP = shutil.which("gimp-console-3") or shutil.which("gimp-console") or "gimp-console"
KRITA = shutil.which("krita") or "krita"
FONTS = {"serif": "Noto Serif Display", "sans": "Inter", "mono": "DejaVu Sans Mono"}
MAX_SVG = 400_000


def _run(cmd: list[str], timeout: int = 180, env: dict | None = None) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    if r.returncode:
        raise RuntimeError(f"{Path(cmd[0]).name} failed: {(r.stderr or r.stdout)[-400:]}")


def _headless_env() -> dict:
    """Krita and GIMP try to talk to the desktop unless the display is hidden from them."""
    env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
    env["QT_QPA_PLATFORM"] = "offscreen"
    return env


# --- Inkscape ----------------------------------------------------------------------------------

def _check_svg(svg: str) -> str:
    svg = (svg or "").strip()
    if not svg or len(svg) > MAX_SVG:
        raise ValueError("Give SVG markup under 400 KB.")
    if not re.match(r"^(<\?xml[^>]*>\s*)?(<!--.*?-->\s*)*<svg[\s>]", svg, re.S):
        raise ValueError("The markup must be an <svg> document.")
    if re.search(r"""(?:href|src)\s*=\s*["']\s*(?:https?:|ftp:|//)""", svg, re.I):
        raise ValueError("Link only local files (file:///home/...) in the SVG, not web addresses.")
    for ref in re.findall(r"""href\s*=\s*["']file://([^"']+)["']""", svg):
        resolve_image(ref)          # same folders as every other Studio image tool
    return svg


def render_svg(svg: str | None = None, svg_path: str | None = None, title: str | None = None,
               width: int | None = None, fmt: str = "png") -> str:
    """Render SVG markup (or an existing .svg) with Inkscape. The .svg source is kept for edits."""
    fmt = (fmt or "png").lower()
    if fmt not in ("png", "pdf"):
        raise ValueError("fmt is png or pdf")
    if svg_path:
        src = Path(svg_path).expanduser().resolve()
        if src.suffix.lower() != ".svg" or not src.is_file():
            raise ValueError(f"No .svg file at {src}")
        markup = _check_svg(src.read_text(errors="replace"))
        title = (title or src.stem)[:60]
    else:
        markup = _check_svg(svg or "")
        title = (title or "design")[:60]
    designs = out_dir("designs")
    if svg_path and src.parent == designs:
        source = src
    else:
        source = _unique(designs / f"{safe_name(title)}.svg")
        source.write_text(markup)
    dest = _unique(out_dir("images" if fmt == "png" else "designs") / f"{safe_name(title)}.{fmt}")
    cmd = [INKSCAPE, str(source), f"--export-type={fmt}", f"--export-filename={dest}"]
    if width and fmt == "png":
        cmd.append(f"--export-width={max(16, min(8000, int(width)))}")
    _run(cmd)
    return (f"Rendered '{title}' to {dest} (editable source: {source}). "
            f"Look at it: studio_image_preview(['{dest}'], grid=False)." if fmt == "png" else
            f"Rendered '{title}' to {dest} (editable source: {source}).")


def image_text(image: str, title: str, subtitle: str | None = None, position: str = "bottom-left",
               title_font: str = "serif", subtitle_font: str = "sans", title_color: str = "#ffffff",
               subtitle_color: str = "#f3d9b1", title_size: int | None = None, name: str | None = None) -> str:
    """Set real, sharp text (title + optional subtitle) on a picture with Inkscape."""
    src = resolve_image(image)
    w, h = image_size(src)
    title = (title or "").strip()
    if not title or len(title) > 120:
        raise ValueError("Give a title of 1-120 characters.")
    subtitle = (subtitle or "").strip()[:160]
    pos = position if position in ("bottom-left", "bottom-center", "top-left", "top-center", "center") else "bottom-left"
    for c in (title_color, subtitle_color):
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", c or ""):
            raise ValueError("Colours are #rrggbb")
    tf, sf = FONTS.get(title_font, title_font), FONTS.get(subtitle_font, subtitle_font)
    if not re.fullmatch(r"[A-Za-z0-9 ,\-]{1,60}", tf) or not re.fullmatch(r"[A-Za-z0-9 ,\-]{1,60}", sf):
        raise ValueError("Font is serif, sans, mono or a font family name")
    margin = round(w * 0.055)
    ts = int(title_size or min(h * 0.125, (w - 2 * margin) * 1.75 / max(6, len(title))))
    ss = max(12, round(ts * 0.33))
    anchor, x = ("middle", w / 2) if pos.endswith("center") else ("start", margin)
    if pos.startswith("top"):
        ty = margin + ts
    elif pos == "center":
        ty = h / 2
    else:
        ty = h - margin - (ss * 1.9 if subtitle else 0)
    sy = ty + ss * 1.8
    shadow = ('<filter id="sh" x="-10%" y="-30%" width="120%" height="160%"><feGaussianBlur in="SourceAlpha" '
              'stdDeviation="4"/><feOffset dy="2"/><feComponentTransfer><feFuncA type="linear" slope="0.8"/>'
              '</feComponentTransfer><feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge></filter>')
    esc = lambda t: html.escape(t, quote=True)
    sub = (f'<text x="{x:.0f}" y="{sy:.0f}" text-anchor="{anchor}" font-family="{esc(sf)}" font-size="{ss}" '
           f'letter-spacing="{ss * 0.08:.1f}" fill="{subtitle_color}" filter="url(#sh)">{esc(subtitle.upper())}</text>'
           if subtitle else "")
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
           f'width="{w}" height="{h}" viewBox="0 0 {w} {h}"><defs>{shadow}</defs>'
           f'<image xlink:href="file://{esc(str(src))}" x="0" y="0" width="{w}" height="{h}"/>'
           f'<text x="{x:.0f}" y="{ty:.0f}" text-anchor="{anchor}" font-family="{esc(tf)}" font-size="{ts}" '
           f'font-weight="700" fill="{title_color}" filter="url(#sh)">{esc(title)}</text>{sub}</svg>')
    return render_svg(svg=svg, title=(name or f"{src.stem} titled")[:60], width=w)


# --- GIMP --------------------------------------------------------------------------------------

GIMP_OPS = {
    # name: (gegl op, {property: value or callable(amount)}, default amount)
    "auto_contrast": ("gegl:stretch-contrast", {"keep-colors": True}, None),
    "enhance": ("gegl:color-enhance", {}, None),
    "warmer": ("gegl:color-temperature", {"original-temperature": 6500.0,
                                          "intended-temperature": lambda a: 6500.0 - 1500.0 * a}, 1.0),
    "cooler": ("gegl:color-temperature", {"original-temperature": 6500.0,
                                          "intended-temperature": lambda a: 6500.0 + 2000.0 * a}, 1.0),
    "exposure": ("gegl:exposure", {"exposure": lambda a: a}, 0.3),
    "saturation": ("gegl:saturation", {"scale": lambda a: 1.0 + a}, 0.2),
    "shadows_highlights": ("gegl:shadows-highlights", {"shadows": lambda a: 40.0 * a,
                                                       "highlights": lambda a: -30.0 * a}, 1.0),
    "denoise": ("gegl:noise-reduction", {"iterations": lambda a: int(max(1, min(10, a)))}, 3),
    "sharpen": ("gegl:unsharp-mask", {"std-dev": 1.5, "scale": lambda a: a}, 0.6),
    "vignette": ("gegl:vignette", {"radius": lambda a: 2.0 - 0.6 * a, "softness": 0.9}, 0.5),
    "bloom": ("gegl:bloom", {"strength": lambda a: 60.0 * a}, 0.6),
}

_GIMP_SCRIPT = r'''
import json
from gi.repository import Gimp, Gio, Gegl
job = json.load(open(JOB))
img = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE, Gio.File.new_for_path(job["in"]))
layer = img.get_layers()[0]
if job.get("cutout"):
    layer.add_alpha()
if job.get("box"):
    x0, y0, x1, y1 = job["box"]
    img.select_rectangle(Gimp.ChannelOps.REPLACE, x0, y0, x1 - x0, y1 - y0)
    Gimp.Selection.feather(img, max(4, min(x1 - x0, y1 - y0) // 6))
for op, props in job["ops"]:
    f = Gimp.DrawableFilter.new(layer, op, "")
    cfg = f.get_config()
    for k, v in props.items():
        if k == "color":
            c = Gegl.Color.new(v)
            cfg.set_property(k, c)
        else:
            cfg.set_property(k, v)
    f.update()
    layer.append_filter(f)
layer.merge_filters()
Gimp.Selection.none(img)
Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, img, Gio.File.new_for_path(job["out"]), None)
img.delete()
'''


def gimp_finish(image: str, ops: list, box=None, cutout_color: str | None = None, title: str | None = None) -> str:
    """Apply GIMP finishing filters (whole picture or one box), or cut out a flat background colour."""
    src = resolve_image(image)
    w, h = image_size(src)
    plan = []
    for item in ops or []:
        name, amount = (item.get("op"), item.get("amount")) if isinstance(item, dict) else (str(item), None)
        if name not in GIMP_OPS:
            raise ValueError(f"Unknown op '{name}'. Use: {', '.join(GIMP_OPS)}")
        gegl, props, default = GIMP_OPS[name]
        a = float(amount) if amount is not None else (default if default is not None else 1.0)
        a = max(-3.0, min(10.0, a))
        plan.append([gegl, {k: (v(a) if callable(v) else v) for k, v in props.items()}])
    if cutout_color:
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", cutout_color):
            raise ValueError("cutout_color is #rrggbb (the flat background to make transparent)")
        plan.append(["gegl:color-to-alpha", {"color": cutout_color, "transparency-threshold": 0.12,
                                             "opacity-threshold": 0.45}])
    if not plan:
        raise ValueError("Give at least one op (or a cutout_color).")
    bx = _boxes([box], w, h, "box")[0] if box else None
    title = (title or f"{src.stem} finished")[:60]
    dest = _unique(out_dir("images") / f"{safe_name(title)}.png")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=STATE_DIR) as tmp:
        job = Path(tmp) / "job.json"
        script = Path(tmp) / "run.py"
        job.write_text(json.dumps({"in": str(src), "out": str(dest), "ops": plan, "box": bx,
                                   "cutout": bool(cutout_color)}))
        script.write_text(_GIMP_SCRIPT)
        _run([GIMP, "-i", "--quit", "--batch-interpreter", "python-fu-eval", "-b",
              f"JOB = {json.dumps(str(job))}; exec(open({json.dumps(str(script))}).read())"],
             timeout=300, env=_headless_env())
    if not dest.exists():
        raise RuntimeError("GIMP finished without writing the picture")
    names = [p[0].split(":")[1] for p in plan]
    return (f"GIMP applied {', '.join(names)}{' inside ' + str(list(bx)) if bx else ''}: {dest}. "
            f"Look at it: studio_image_preview(['{dest}'], grid=False).")


# --- Krita (and anything that reads OpenRaster) --------------------------------------------------

def layered_file(base: str, layers: list | None = None, title: str | None = None, guide_boxes=None) -> str:
    """Build an OpenRaster (.ora) file: the base picture plus pasted layers at their positions,
    each on its own layer so the operator can move, mask and paint them by hand in Krita or GIMP."""
    b = resolve_image(base)
    w, h = image_size(b)
    title = (title or f"{b.stem} layers")[:60]
    dest = _unique(out_dir("designs") / f"{safe_name(title)}.ora")
    entries = []
    for i, L in enumerate(layers or [], 1):
        if not isinstance(L, dict) or "path" not in L:
            raise ValueError("Each layer is {path, at:[x,y], name?, opacity?}")
        p = resolve_image(L["path"])
        at = L.get("at") or [0, 0]
        entries.append((p, int(at[0]), int(at[1]), str(L.get("name") or p.stem)[:60],
                        max(0.0, min(1.0, float(L.get("opacity", 1.0))))))
    guides = _boxes(guide_boxes, w, h, "guide_boxes") if guide_boxes else []
    with tempfile.TemporaryDirectory(dir=STATE_DIR) as tmp:
        t = Path(tmp)
        stack = []
        if guides:   # top layer: outlines showing where to paint (hide or delete it when done)
            draw = []
            for i, (x0, y0, x1, y1) in enumerate(guides, 1):
                draw += ["-stroke", "#00e5ff", "-strokewidth", "3", "-fill", "none",
                         "-draw", f"rectangle {x0},{y0} {x1},{y1}",
                         "-stroke", "none", "-fill", "#00e5ff", "-pointsize", "16",
                         "-annotate", f"+{x0 + 3}+{max(16, y0 - 5)}", f"paint {i}"]
            _run(["magick", "-size", f"{w}x{h}", "xc:none", *draw, f"PNG32:{t / 'guide.png'}"], timeout=60)
            stack.append('<layer name="PAINT HERE (hide me)" src="data/guide.png" x="0" y="0" opacity="1.00"/>')
        for i, (p, x, y, name, op) in reversed(list(enumerate(entries, 1))):
            png = t / f"layer{i}.png"
            _run(["magick", str(p), f"PNG32:{png}"], timeout=60)
            stack.append(f'<layer name="{html.escape(name, quote=True)}" src="data/layer{i}.png" x="{x}" y="{y}" opacity="{op:.2f}"/>')
        _run(["magick", str(b), f"PNG32:{t / 'base.png'}"], timeout=60)
        stack.append('<layer name="base" src="data/base.png" x="0" y="0" opacity="1.00"/>')
        _run(["magick", str(b), "-thumbnail", "256x256", f"PNG32:{t / 'thumb.png'}"], timeout=60)
        xml = (f'<?xml version="1.0" encoding="UTF-8"?>\n<image version="0.0.3" w="{w}" h="{h}">'
               f'<stack>{"".join(stack)}</stack></image>')
        with zipfile.ZipFile(dest, "w") as z:
            z.writestr(zipfile.ZipInfo("mimetype"), "image/openraster", compress_type=zipfile.ZIP_STORED)
            z.writestr("stack.xml", xml, compress_type=zipfile.ZIP_DEFLATED)
            z.write(t / "thumb.png", "Thumbnails/thumbnail.png")
            z.write(t / "base.png", "data/base.png")
            for i in range(1, len(entries) + 1):
                z.write(t / f"layer{i}.png", f"data/layer{i}.png")
            if guides:
                z.write(t / "guide.png", "data/guide.png")
    return (f"Layered file '{title}' ({len(entries) + 1 + bool(guides)} layers"
            f"{', guide boxes ' + str([list(g) for g in guides]) if guides else ''}): {dest}. The operator can open it with "
            f"studio_open_in_app('krita', '{dest}'); export it back with studio_design_export.")


def export_design(path: str, title: str | None = None) -> str:
    """Flatten a design file to PNG: .kra/.ora with Krita, .xcf with GIMP, .svg with Inkscape."""
    src = Path(path).expanduser().resolve()
    allowed = [Path.home() / d for d in ("Music/Jarvis", "Studio", "Pictures", "Documents/Jarvis")]
    if not any(src.is_relative_to(r) for r in allowed) or not src.is_file():
        raise ValueError("Give a design file under ~/Music/Jarvis, ~/Studio, ~/Pictures or ~/Documents/Jarvis")
    ext = src.suffix.lower()
    title = (title or src.stem)[:60]
    dest = _unique(out_dir("images") / f"{safe_name(title)}.png")
    if ext in (".kra", ".ora", ".psd"):
        _run([KRITA, "--nosplash", "--export", "--export-filename", str(dest), str(src)], timeout=300, env=_headless_env())
    elif ext == ".xcf":
        code = (f"from gi.repository import Gimp, Gio\nimg = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE, "
                f"Gio.File.new_for_path({json.dumps(str(src))}))\nimg.flatten()\nGimp.file_save(Gimp.RunMode.NONINTERACTIVE, img, "
                f"Gio.File.new_for_path({json.dumps(str(dest))}), None)\nimg.delete()\n")
        _run([GIMP, "-i", "--quit", "--batch-interpreter", "python-fu-eval", "-b", code], timeout=300, env=_headless_env())
    elif ext == ".svg":
        _run([INKSCAPE, str(src), "--export-type=png", f"--export-filename={dest}"])
    else:
        raise ValueError("Export .kra, .ora, .psd, .xcf or .svg files")
    if not dest.exists():
        raise RuntimeError("The export finished without writing a PNG")
    return f"Exported {src.name} to {dest}. Look at it: studio_image_preview(['{dest}'], grid=False)."


def open_in_app(app: str, path: str) -> str:
    """Open a file in GIMP, Krita or Inkscape on the operator's desktop for hand finishing."""
    exe = {"gimp": shutil.which("gimp") or "gimp", "krita": KRITA, "inkscape": INKSCAPE}.get((app or "").lower())
    if not exe:
        raise ValueError("app is gimp, krita or inkscape")
    p = Path(path).expanduser().resolve()
    allowed = [Path.home() / d for d in ("Music/Jarvis", "Studio", "Pictures", "Documents/Jarvis", "Videos/Jarvis")]
    if not any(p.is_relative_to(r) for r in allowed) or not p.is_file():
        raise ValueError("Open files under ~/Music/Jarvis, ~/Studio, ~/Pictures, ~/Documents/Jarvis or ~/Videos/Jarvis")
    env = dict(os.environ)
    try:   # the Studio runs as a service: borrow the desktop session's display settings
        out = subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True, text=True, timeout=10).stdout
        for line in out.splitlines():
            k, _, v = line.partition("=")
            if k in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP", "DBUS_SESSION_BUS_ADDRESS"):
                env[k] = v
    except (OSError, subprocess.SubprocessError):
        pass
    env.pop("QT_QPA_PLATFORM", None)
    subprocess.Popen([exe, str(p)], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    return f"Opened {p.name} in {app.capitalize()} on the desktop. When the operator saves, export it with studio_design_export."


# --- Import and convert ---------------------------------------------------------------------------

from .images import IMAGE_ROOTS, UPLOADS_CONTAINER, UPLOADS_HOST

RASTER = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".gif"}
DESIGN = {".svg", ".pdf", ".ora", ".kra", ".psd", ".xcf"}
AUDIO = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac"}
VIDEO = {".mp4", ".mov", ".mkv", ".webm"}
IMPORT_FROM = [Path.home() / d for d in ("Downloads", "Desktop", "Pictures", "Documents")] + [UPLOADS_HOST]
MAX_IMPORT = 500 * 1024 * 1024


def _host_path(path: str) -> Path:
    p = str(path or "").strip()
    if p.startswith(UPLOADS_CONTAINER + "/"):
        p = str(UPLOADS_HOST / p[len(UPLOADS_CONTAINER) + 1:])
    return Path(p).expanduser().resolve()


def import_file(path: str, name: str | None = None) -> str:
    """Copy a file the operator downloaded or uploaded into the Studio so every tool can use it."""
    src = _host_path(path)
    if not any(src.is_relative_to(r.resolve()) for r in IMPORT_FROM):
        raise ValueError("Import from ~/Downloads, ~/Desktop, ~/Pictures, ~/Documents or the dashboard uploads.")
    if not src.is_file():
        raise ValueError(f"No file at {src}")
    ext = src.suffix.lower()
    if ext in RASTER:
        folder = out_dir("images")
    elif ext in DESIGN:
        folder = out_dir("designs")
    elif ext in AUDIO:
        folder = out_dir("imports", "audio")
    elif ext in VIDEO:
        folder = out_dir("imports", "video")
    else:
        raise ValueError(f"{ext or 'That file type'} isn't a picture, design, audio or video file the Studio uses.")
    if src.stat().st_size > MAX_IMPORT:
        raise ValueError("That file is over 500 MB.")
    if src.is_relative_to(folder.resolve()):
        return f"{src} is already in the Studio."
    dest = _unique(folder / f"{safe_name((name or src.stem)[:80])}{ext}")
    shutil.copy2(src, dest)
    return f"Imported {src.name} as {dest} (the original stays where it was)."


def _design_source(path: str) -> Path:
    src = _host_path(path)
    roots = [r.resolve() for r in IMAGE_ROOTS] + [(Path.home() / "Music/Jarvis/designs").resolve()]
    if not any(src.is_relative_to(r) for r in roots):
        raise ValueError("Convert files in the Studio folders; import other files first with studio_import_file.")
    if not src.is_file() or src.suffix.lower() not in RASTER | DESIGN:
        raise ValueError(f"No picture or design file at {src}")
    return src


def _to_png(src: Path, tmp: Path) -> Path:
    """Flatten any supported file to one PNG (first page for PDFs)."""
    ext = src.suffix.lower()
    out = tmp / "flat.png"
    if ext == ".png":
        return src
    if ext in RASTER:
        _run(["magick", f"{src}[0]", f"PNG32:{out}"], timeout=120)
    elif ext in (".svg", ".pdf"):
        _run([INKSCAPE, str(src), "--export-type=png", f"--export-filename={out}"]
             + (["--pages=1"] if ext == ".pdf" else []))
    elif ext in (".kra", ".ora", ".psd"):
        _run([KRITA, "--nosplash", "--export", "--export-filename", str(out), str(src)], timeout=300, env=_headless_env())
    elif ext == ".xcf":
        code = (f"from gi.repository import Gimp, Gio\nimg = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE, "
                f"Gio.File.new_for_path({json.dumps(str(src))}))\nimg.flatten()\nGimp.file_save(Gimp.RunMode.NONINTERACTIVE, img, "
                f"Gio.File.new_for_path({json.dumps(str(out))}), None)\nimg.delete()\n")
        _run([GIMP, "-i", "--quit", "--batch-interpreter", "python-fu-eval", "-b", code], timeout=300, env=_headless_env())
    if not out.exists():
        raise RuntimeError(f"Couldn't read {src.name}")
    return out


def convert_file(path: str, to: str, title: str | None = None) -> str:
    """Convert between picture and design formats with the right program for each pair."""
    src = _design_source(path)
    to = (to or "").lower().lstrip(".")
    to = {"jpeg": "jpg", "tif": "tiff"}.get(to, to)
    if to not in ("png", "jpg", "webp", "tiff", "pdf", "svg", "ora", "kra", "psd", "xcf"):
        raise ValueError("to is one of png, jpg, webp, tiff, pdf, svg, ora, kra, psd, xcf")
    ext = src.suffix.lower()
    title = (title or src.stem)[:60]
    folder = out_dir("images") if to in ("png", "jpg", "webp", "tiff") else out_dir("designs")
    dest = _unique(folder / f"{safe_name(title)}.{to}")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=STATE_DIR) as tmp:
        tmp = Path(tmp)
        if to in ("png", "jpg", "webp", "tiff"):
            flat = _to_png(src, tmp)
            _run(["magick", str(flat), *(["-quality", "92"] if to in ("jpg", "webp") else []), str(dest)], timeout=120)
            how = "flattened"
        elif to == "pdf":
            if ext == ".svg":
                _run([INKSCAPE, str(src), "--export-type=pdf", f"--export-filename={dest}"])
                how = "vector PDF (Inkscape)"
            else:
                _run(["magick", str(_to_png(src, tmp)), str(dest)], timeout=120)
                how = "picture PDF"
        elif to == "svg":
            if ext in (".pdf", ".svg"):
                _run([INKSCAPE, str(src), "--export-type=svg", f"--export-filename={dest}"]
                     + (["--pages=1"] if ext == ".pdf" else []))
                how = "editable SVG (Inkscape)"
            else:
                flat = _to_png(src, tmp)
                w, h = image_size(flat)
                import base64
                data = base64.b64encode(flat.read_bytes()).decode("ascii")
                dest.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
                                f'width="{w}" height="{h}" viewBox="0 0 {w} {h}"><image width="{w}" height="{h}" '
                                f'xlink:href="data:image/png;base64,{data}"/></svg>')
                how = "SVG with the picture embedded, ready for text and shapes in Inkscape"
        elif to in ("kra", "psd", "ora"):
            feed = src if ext in RASTER | {".kra", ".ora", ".psd"} else _to_png(src, tmp)
            _run([KRITA, "--nosplash", "--export", "--export-filename", str(dest), str(feed)], timeout=300, env=_headless_env())
            how = "Krita (layers kept where the source has them)"
        else:  # xcf
            feed = src if ext in RASTER | {".psd", ".ora", ".xcf"} else _to_png(src, tmp)
            code = (f"from gi.repository import Gimp, Gio\nimg = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE, "
                    f"Gio.File.new_for_path({json.dumps(str(feed))}))\nGimp.file_save(Gimp.RunMode.NONINTERACTIVE, img, "
                    f"Gio.File.new_for_path({json.dumps(str(dest))}), None)\nimg.delete()\n")
            _run([GIMP, "-i", "--quit", "--batch-interpreter", "python-fu-eval", "-b", code], timeout=300, env=_headless_env())
            how = "GIMP project"
    if not dest.exists():
        raise RuntimeError("The conversion finished without writing a file")
    return f"Converted {src.name} to {dest} ({how})."


FIND_ROOTS = {
    "studio": [Path.home() / "Music/Jarvis", Path.home() / "Videos/Jarvis", Path.home() / "Studio",
               Path.home() / "Documents/Jarvis"],
    "downloads": [Path.home() / "Downloads"],
    "desktop": [Path.home() / "Desktop"],
    "pictures": [Path.home() / "Pictures"],
    "uploads": [UPLOADS_HOST],
}
SKIP_DIRS = {"cache", ".git", "__pycache__", "node_modules", "previews", ".venv"}


def find_files(query: str = "", where: str = "studio", kind: str = "any", limit: int = 30) -> str:
    """List files on the host by name (newest first). Jarvis's own file tools can't see these folders."""
    places = list(FIND_ROOTS) if where == "all" else [where]
    if any(p not in FIND_ROOTS for p in places):
        raise ValueError(f"where is one of: {', '.join(FIND_ROOTS)} or all")
    exts = {"image": RASTER, "design": DESIGN, "audio": AUDIO, "video": VIDEO,
            "book": {".pdf", ".epub", ".md", ".docx"}}.get(kind)
    if kind != "any" and exts is None:
        raise ValueError("kind is any, image, design, audio, video or book")
    words = [w for w in (query or "").lower().split() if w]
    hits = []
    for place in places:
        for root in FIND_ROOTS[place]:
            if not root.exists():
                continue
            base_depth = len(root.parts)
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
                if len(Path(dirpath).parts) - base_depth >= 5:
                    dirnames[:] = []
                for fn in filenames:
                    if fn.startswith("."):
                        continue
                    p = Path(dirpath) / fn
                    if exts and p.suffix.lower() not in exts:
                        continue
                    if words and not all(w in str(p.relative_to(root)).lower() for w in words):
                        continue
                    try:
                        st = p.stat()
                    except OSError:
                        continue
                    hits.append((st.st_mtime, st.st_size, p))
    hits.sort(reverse=True)
    limit = max(1, min(200, int(limit)))
    if not hits:
        return f"No files matching '{query}' in {where}."
    import datetime as _dt
    lines = [f"{_dt.datetime.fromtimestamp(m):%Y-%m-%d %H:%M}  {s / 1e6:7.1f} MB  {p}" for m, s, p in hits[:limit]]
    more = f"\n(+{len(hits) - limit} more; narrow the query)" if len(hits) > limit else ""
    note = ("\nFiles outside the Studio folders: bring them in with studio_import_file before using other tools."
            if where not in ("studio", "uploads") else "")
    return f"{len(hits)} file(s), newest first:\n" + "\n".join(lines) + more + note
