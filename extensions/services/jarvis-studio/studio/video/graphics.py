"""Title cards, overlays, slides and thumbnails: SVG drawn here, rendered by Inkscape."""

import shutil
import textwrap
from pathlib import Path
from xml.sax.saxutils import escape

from ..audio import run
from .common import FONT

ACCENT = "#f4b740"
PALETTE = {"vocals": "#f4b740", "drums": "#ef6f5e", "bass": "#7c9cff", "guitar": "#5cc27e",
           "piano": "#c58cf2", "other": "#4fd1c5"}


def _lines(text: str, width: int) -> list[str]:
    return textwrap.wrap(text, width=max(8, width), break_long_words=False) or [text]


def _text(lines: list[str], x: float, y: float, size: float, weight: int = 800,
          fill: str = "#ffffff", anchor: str = "middle", lh: float = 1.12, opacity: float = 1) -> str:
    spans = "".join(f'<tspan x="{x}" dy="{0 if i == 0 else size * lh}">{escape(l)}</tspan>'
                    for i, l in enumerate(lines))
    return (f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" font-weight="{weight}" '
            f'fill="{fill}" fill-opacity="{opacity}" text-anchor="{anchor}">{spans}</text>')


def _render(svg: str, dest: Path, w: int, h: int) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = dest.with_suffix(".svg")
    src.write_text(svg)
    if shutil.which("inkscape"):
        run(["inkscape", str(src), "--export-type=png", f"--export-filename={dest}",
             f"--export-width={w}", f"--export-height={h}"], timeout=120)
    else:  # ImageMagick fallback
        run(["magick", "-background", "none", str(src), str(dest)], timeout=120)
    return dest


def _svg(w: int, h: int, body: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'width="{w}" height="{h}" viewBox="0 0 {w} {h}">{body}</svg>')


def title_card(title: str, subtitle: str | None, size: tuple[int, int], dest: Path,
               meta: str | None = None) -> Path:
    """Transparent PNG: big title, accent rule, subtitle. Overlaid on video."""
    w, h = size
    base = min(w, h)
    ts = base * (0.11 if w >= h else 0.10)
    lines = _lines(title, int(w / ts * 1.7))
    block = ts * 1.12 * len(lines)
    y0 = h * 0.5 - block / 2 + ts * 0.8
    body = (f'<defs><radialGradient id="v" cx="50%" cy="50%" r="60%">'
            f'<stop offset="0" stop-color="#000" stop-opacity=".55"/>'
            f'<stop offset="1" stop-color="#000" stop-opacity="0"/></radialGradient></defs>'
            f'<rect width="{w}" height="{h}" fill="url(#v)"/>'
            + _text(lines, w / 2, y0, ts))
    ry = y0 + block - ts * 0.55
    body += f'<rect x="{w / 2 - base * 0.06}" y="{ry}" width="{base * 0.12}" height="{base * 0.008}" rx="{base * 0.004}" fill="{ACCENT}"/>'
    if subtitle:
        body += _text(_lines(subtitle, 40), w / 2, ry + base * 0.075, base * 0.045, 500, "#f2ece2")
    if meta:
        body += _text([meta], w / 2, ry + base * 0.075 + base * 0.07, base * 0.034, 600, ACCENT,
                      opacity=0.9)
    return _render(_svg(w, h, body), dest, w, h)


def stems_overlay(title: str, meta: str, stems: list[str], size: tuple[int, int], lanes: tuple[int, int],
                  dest: Path) -> Path:
    """Labels for the per-stem waveform lanes plus a corner title."""
    w, h = size
    top, lane_h = lanes
    base = min(w, h)
    pad = w * 0.06
    body = _text([title], pad, top - base * 0.07, base * 0.045, 800, anchor="start")
    body += _text([meta], pad, top - base * 0.03, base * 0.024, 600, ACCENT, anchor="start", opacity=0.9)
    for i, s in enumerate(stems):
        y = top + i * lane_h + lane_h * 0.5 + base * 0.012
        body += f'<rect x="{pad}" y="{y - base * 0.022}" width="{base * 0.012}" height="{base * 0.012}" rx="2" fill="{PALETTE.get(s, ACCENT)}"/>'
        body += _text([s.upper()], pad + base * 0.024, y - base * 0.011, base * 0.02, 700, "#e9e2d6",
                      anchor="start", opacity=0.85)
    return _render(_svg(w, h, body), dest, w, h)


def slide(text: str, size: tuple[int, int], dest: Path, kicker: str | None = None,
          index: int = 0) -> Path:
    """Opaque text slide for narrated videos (no photos given)."""
    w, h = size
    base = min(w, h)
    hues = [("#1d1530", "#0c2b3c"), ("#2a1a12", "#14283a"), ("#10261d", "#1b1838"), ("#2b1430", "#0f2633")]
    c0, c1 = hues[index % len(hues)]
    ts = base * (0.065 if len(text) < 90 else 0.05)
    lines = _lines(text, int(w * 0.84 / (ts * 0.52)))
    block = ts * 1.18 * len(lines)
    y0 = h / 2 - block / 2 + ts * 0.8
    body = (f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{c0}"/>'
            f'<stop offset="1" stop-color="{c1}"/></linearGradient></defs>'
            f'<rect width="{w}" height="{h}" fill="url(#g)"/>'
            f'<circle cx="{w * 0.88}" cy="{h * 0.12}" r="{base * 0.35}" fill="{ACCENT}" fill-opacity=".06"/>')
    if kicker:
        body += _text([kicker.upper()], w * 0.08, y0 - ts * 1.4, base * 0.026, 700, ACCENT, "start")
    body += _text(lines, w * 0.08, y0, ts, 750, "#f5efe6", "start", 1.18)
    return _render(_svg(w, h, body), dest, w, h)


def thumbnail(frame: Path | None, title: str, size: tuple[int, int], dest: Path,
              subtitle: str | None = None) -> Path:
    """Thumbnail: a video frame (or gradient), dark gradient, bold title bottom-left."""
    w, h = size
    base = min(w, h)
    bg = (f'<image xlink:href="{escape(str(frame))}" href="{escape(str(frame))}" x="0" y="0" '
          f'width="{w}" height="{h}" preserveAspectRatio="xMidYMid slice"/>' if frame else
          f'<rect width="{w}" height="{h}" fill="#1a1430"/>')
    ts = base * (0.12 if len(title) < 24 else 0.09)
    lines = _lines(title, int(w * 0.86 / (ts * 0.55)))[:3]
    y0 = h * 0.92 - ts * 1.05 * (len(lines) - 1) - (base * 0.07 if subtitle else 0)
    body = (bg + '<defs><linearGradient id="s" x1="0" y1="0" x2="0" y2="1">'
            '<stop offset=".35" stop-color="#000" stop-opacity="0"/>'
            '<stop offset="1" stop-color="#000" stop-opacity=".85"/></linearGradient></defs>'
            f'<rect width="{w}" height="{h}" fill="url(#s)"/>'
            f'<rect x="{w * 0.06}" y="{y0 - ts * 1.15}" width="{base * 0.1}" height="{base * 0.012}" fill="{ACCENT}"/>'
            + _text(lines, w * 0.06, y0, ts, 900, "#ffffff", "start", 1.05))
    if subtitle:
        body += _text([subtitle], w * 0.06, y0 + ts * 1.05 * (len(lines) - 1) + base * 0.07,
                      base * 0.04, 600, "#f2ece2", "start")
    return _render(_svg(w, h, body), dest, w, h)


def grab_frame(video: Path, t: float, dest: Path) -> Path:
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{max(0, t):.2f}", "-i", str(video),
         "-frames:v", "1", "-q:v", "2", str(dest)])
    return dest
