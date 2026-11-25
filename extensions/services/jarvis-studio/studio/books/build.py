"""Build a book's files in the background queue: pictures, print PDF (KDP interior), KDP
wraparound cover and listing kit, ebook cover, EPUB and a Markdown manuscript."""

import hashlib
import shutil
import subprocess
from pathlib import Path

from .. import images, jobqueue
from ..video.styles import NO_TEXT
from . import kdp, layout
from .epub import write_epub
from .project import TRIMS, book_dir, chapter_rows, load, save

DEFAULT_FORMATS = {"novel": ["epub", "pdf", "kdp"], "ebook": ["epub", "pdf", "kdp"],
                   "picture": ["pdf", "kdp", "epub"], "puzzle": ["pdf", "kdp"]}
UPSCALE_W = 2400


def formats_for(book: dict, formats) -> list[str]:
    if not formats:
        return DEFAULT_FORMATS[book["kind"]]
    if isinstance(formats, str):
        formats = [f.strip().lower() for f in formats.replace(";", ",").split(",") if f.strip()]
    bad = [f for f in formats if f not in ("epub", "pdf", "kdp", "md")]
    if bad:
        raise ValueError("formats: epub, pdf, kdp, md")
    if book["kind"] == "puzzle":
        formats = [f for f in formats if f != "epub"]
    return formats


def _pic_size(trim: tuple[float, float]) -> str:
    w, h = trim
    return "512x512" if abs(w - h) < 0.6 else ("512x768" if h > w else "768x512")


def picture_prompt(book: dict, picture: str) -> str:
    chars = [f"{name}: {look}" for name, look in book.get("characters", {}).items()
             if name.lower() in picture.lower()]
    style = book.get("style") or ("children's picture book illustration, soft watercolor, warm colours"
                                  if book["kind"] == "picture" else "book cover art, dramatic, cinematic lighting")
    parts = [picture.strip().rstrip(".")] + chars + [style, NO_TEXT]
    return ", ".join(p for p in parts if p)[:1000]


def _make_pic(book: dict, prompt: str, seed: int, size: str, cache: Path) -> Path:
    key = hashlib.sha1(f"{prompt}|{seed}|{size}|{UPSCALE_W}".encode()).hexdigest()[:16]
    out = cache / f"pic-{key}.jpg"
    if out.exists():
        return out
    png = images.generate_png(prompt, size, seed=seed, keep_loaded=True)
    raw = cache / f"pic-{key}.png"
    raw.write_bytes(png)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-vf",
                    f"scale={UPSCALE_W}:-2:flags=lanczos,unsharp=5:5:0.5", "-q:v", "2", str(out)], check=True)
    raw.unlink(missing_ok=True)
    return out


def _to_jpg(src: Path, cache: Path) -> Path:
    if src.suffix.lower() in (".jpg", ".jpeg"):
        return src
    key = hashlib.sha1(f"{src}|{src.stat().st_mtime}".encode()).hexdigest()[:16]
    out = cache / f"img-{key}.jpg"
    if not out.exists():
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-q:v", "2", str(out)], check=True)
    return out


def make_pictures(book: dict, cache: Path, progress, want_cover: bool) -> tuple[dict[int, Path], Path | None]:
    rows = chapter_rows(book)
    trim = TRIMS[book["trim"]]
    todo = []
    pics: dict[int, Path] = {}
    for r in rows:
        if r["image"]:
            pics[r["n"]] = _to_jpg(Path(r["image"]), cache)
        elif book["kind"] == "picture" and r["picture"] and not book.get("no_pictures"):
            c = book["chapters"][r["n"] - 1]
            todo.append((r["n"], picture_prompt(book, r["picture"]), c.get("pic_seed") or book["seed"]))
    cover_prompt = (book.get("cover") or {}).get("picture")
    cover_img = (book.get("cover") or {}).get("image")
    if want_cover and not cover_img and not cover_prompt and book["kind"] == "picture" and rows and rows[0]["picture"]:
        cover_prompt = rows[0]["picture"]
    try:
        for i, (n, prompt, seed) in enumerate(todo):
            progress(f"drawing page {n} picture ({i + 1}/{len(todo)})", 0.05 + 0.55 * i / max(1, len(todo)))
            pics[n] = _make_pic(book, prompt, seed, _pic_size(trim), cache)
        cover = None
        if want_cover:
            if cover_img and Path(cover_img).exists():
                cover = _to_jpg(Path(cover_img), cache)
            elif cover_prompt:
                progress("drawing the cover picture", 0.6)
                cover = _make_pic(book, picture_prompt(book, cover_prompt), book["seed"],
                                  "512x768" if trim[1] > trim[0] + 0.5 else "512x512", cache)
    finally:
        if todo or cover_prompt:
            images.unload()
    return pics, cover


def _interior(book: dict, pics: dict, d: Path, cache: Path, progress) -> tuple[Path, int, list[str]]:
    notes = []
    dest = d / f"{book['title'][:80].replace('/', '-')}.pdf"
    if book["kind"] == "picture":
        count = layout.picture_page_count(book)
        pad = max(0, kdp.MIN_PAGES - count)
        layout.print_pdf(layout.picture_html(book, pics, pad), dest, cache)
        if pad:
            notes.append(f"Added {pad} blank pages at the end to reach KDP's 24-page minimum.")
        return dest, layout.page_count(dest), notes
    make = (lambda m, toc=None, pad=0: layout.puzzle_html(book, m, pad)) if book["kind"] == "puzzle" \
        else (lambda m, toc=None, pad=0: layout.prose_html(book, m, toc, pad))
    pages = 100
    for _ in range(4):  # the gutter depends on the page count, which depends on the gutter
        m = kdp.margins(pages)
        progress("laying out pages", 0.7)
        layout.print_pdf(make(m), dest, cache)
        new = layout.page_count(dest)
        if kdp.inside_margin(new) == kdp.inside_margin(pages):
            pages = new
            break
        pages = new
    toc = None
    if book["kind"] != "puzzle" and "Contents" in subprocess.run(["pdftotext", "-l", "6", str(dest), "-"],
                                                                 capture_output=True, text=True).stdout:
        toc = layout.chapter_pages(dest, len(book.get("outline", [])))
    pad = max(0, kdp.MIN_PAGES - pages)
    if toc or pad:
        layout.print_pdf(make(m, toc, pad), dest, cache)
        pages = layout.page_count(dest)
    if pad:
        notes.append(f"Added {pad} blank pages at the end to reach KDP's 24-page minimum.")
    notes += kdp.check_pages(pages, book["paper"])
    return dest, pages, notes


def _manuscript(book: dict, dest: Path) -> Path:
    lines = [f"# {book['title']}", ""]
    if book.get("author"):
        lines += [f"by {book['author']}", ""]
    for r in chapter_rows(book):
        lines += [f"## {r['title']}", "", r["text"].strip(), ""]
        if book["kind"] == "picture" and r["picture"]:
            lines += [f"*Picture: {r['picture']}*", ""]
    dest.write_text("\n".join(lines), encoding="utf-8")
    return dest


def build(title: str, progress=lambda step, frac=None: None, formats=None) -> str:
    book = load(title)
    formats = formats_for(book, formats)
    d = book_dir(book["title"])
    cache = d / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    rows = chapter_rows(book)
    notes = []
    if book["kind"] == "puzzle":
        if not book.get("puzzles"):
            raise ValueError("No puzzles yet: add some with book_puzzles.")
    else:
        if not rows:
            raise ValueError("No outline yet.")
        missing = [r["n"] for r in rows if not r["text"].strip()]
        if len(missing) == len(rows):
            raise ValueError("Nothing is written yet.")
        if missing:
            notes.append(f"Draft build: {len(missing)} {'pages' if book['kind'] == 'picture' else 'chapters'} "
                         f"not written yet ({', '.join(map(str, missing[:12]))}).")
    want_cover = "kdp" in formats or "epub" in formats
    progress("pictures", 0.03)
    pics, cover_pic = make_pictures(book, cache, progress, want_cover)
    made = []
    pages = None
    if "pdf" in formats or "kdp" in formats:
        progress("laying out pages", 0.65)
        pdf, pages, n2 = _interior(book, pics, d, cache, progress)
        notes += n2
        made.append(f"Print PDF ({pages} pages, {book['trim']} in{', with bleed' if book['kind'] == 'picture' else ''}): {pdf}")
    ebook_cover = None
    if want_cover:
        progress("making the cover", 0.85)
        kd = d / "kdp"
        kd.mkdir(exist_ok=True)
        png = cache / "ebook-cover.png"
        layout.screenshot(layout.front_only_html(book, cover_pic), png, cache)
        ebook_cover = kd / "ebook cover.jpg"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(png), "-q:v", "2", str(ebook_cover)], check=True)
    if "kdp" in formats and pages:
        size = kdp.cover_size(TRIMS[book["trim"]], pages, book["paper"])
        cpdf = layout.print_pdf(layout.cover_html(book, size, cover_pic), d / "kdp" / "paperback cover.pdf", cache)
        (d / "kdp" / "listing.md").write_text(kdp.listing_markdown(book, pages, size), encoding="utf-8")
        made.append(f"KDP cover ({size['width']:.3f} x {size['height']:.3f} in, spine {size['spine']:.3f} in): {cpdf}")
        made.append(f"KDP listing kit: {d / 'kdp' / 'listing.md'}")
        if not book.get("listing", {}).get("description"):
            notes.append("No listing yet: ask Jarvis to write it with book_listing, then build again.")
    if ebook_cover:
        made.append(f"Ebook cover (1600x2560): {ebook_cover}")
    if "epub" in formats and book["kind"] != "puzzle":
        progress("writing the EPUB", 0.93)
        ep = write_epub(book, d / f"{book['title'][:80].replace('/', '-')}.epub", ebook_cover, pics)
        made.append(f"EPUB: {ep}")
    if book["kind"] != "puzzle":
        made.append(f"Manuscript: {_manuscript(book, d / 'manuscript.md')}")
    # tidy cache: keep pictures still in use and the latest html
    used = {p.name for p in pics.values()} | ({cover_pic.name} if cover_pic else set())
    for p in cache.glob("pic-*.jpg"):
        if p.name not in used:
            p.unlink()
    for p in cache.iterdir():
        if p.is_dir() and p.name.startswith("chrome-"):
            shutil.rmtree(p, ignore_errors=True)
    book = load(title)
    book["built"] = {"pages": pages, "formats": formats}
    save(book)
    return "\n".join([f"Built '{book['title']}':"] + made + notes)


@jobqueue.handler("book", "Book")
def _job(args: dict, progress) -> str:
    return build(args["title"], progress, args.get("formats"))


def existing_pictures(book: dict) -> dict[int, Path]:
    """Pictures already drawn or chosen for each page (nothing is generated)."""
    cache = book_dir(book["title"]) / "cache"
    size = _pic_size(TRIMS[book["trim"]])
    out = {}
    for r in chapter_rows(book):
        if r["image"] and Path(r["image"]).exists():
            out[r["n"]] = Path(r["image"])
        elif r["picture"]:
            c = book["chapters"][r["n"] - 1] if r["n"] - 1 < len(book["chapters"]) else {}
            prompt = picture_prompt(book, r["picture"])
            key = hashlib.sha1(f"{prompt}|{c.get('pic_seed') or book['seed']}|{size}|{UPSCALE_W}".encode()).hexdigest()[:16]
            p = cache / f"pic-{key}.jpg"
            if p.exists():
                out[r["n"]] = p
    return out
