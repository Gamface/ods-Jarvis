"""A small EPUB 3 writer (no extra libraries): cover, title page, contents, chapters, pictures."""

import datetime
import html
import uuid
import zipfile
from pathlib import Path

from .layout import text_html

e = html.escape
CSS = """body { font-family: serif; line-height: 1.5; margin: 0 4%; }
h1 { text-align: center; font-size: 1.5em; margin: 2em 0 1.2em; }
.label { display: block; text-align: center; font-size: .8em; letter-spacing: .2em; text-transform: uppercase; color: #666; margin-top: 2em; }
p { margin: 0; text-indent: 1.3em; }
h1 + p, h2 + p, h3 + p, .break + p, p.first { text-indent: 0; }
.break { text-align: center; text-indent: 0; margin: 1em 0; }
.title { text-align: center; margin-top: 30%; }
.copy { font-size: .8em; margin-top: 40%; }
.copy p { text-indent: 0; margin-bottom: .6em; }
.pic { text-align: center; margin: 1em 0; }
.pic img { max-width: 100%; max-height: 80vh; }
.page p { text-indent: 0; text-align: center; font-size: 1.2em; }
img.cover { width: 100%; }
"""


def _doc(title: str, body: str, nav: bool = False) -> str:
    return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en">'
            f'<head><meta charset="utf-8"/><title>{e(title)}</title><link rel="stylesheet" href="style.css"/></head>'
            f"<body>{body}</body></html>")


def _xhtml(s: str) -> str:
    return s.replace("<br>", "<br/>")


def write_epub(book: dict, dest: Path, cover_jpg: Path | None, pictures: dict[int, Path]) -> Path:
    from .project import chapter_rows
    uid = "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, "jarvis-book:" + book["title"]))
    author = book.get("author") or "Unknown"
    L = book.get("listing", {})
    files, manifest, spine, toc = [], [], [], []

    def add(name: str, content, mtype: str, props: str = "", in_spine: bool = True, label: str | None = None):
        files.append((name, content))
        mid = "i" + str(len(manifest))
        manifest.append(f'<item id="{mid}" href="{name}" media-type="{mtype}"{f" properties=\"{props}\"" if props else ""}/>')
        if in_spine:
            spine.append(f'<itemref idref="{mid}"/>')
        if label:
            toc.append((name, label))

    add("style.css", CSS, "text/css", in_spine=False)
    if cover_jpg and cover_jpg.exists():
        add("images/cover.jpg", cover_jpg.read_bytes(), "image/jpeg", "cover-image", in_spine=False)
        add("cover.xhtml", _doc("Cover", '<div class="pic"><img class="cover" src="images/cover.jpg" alt="Cover"/></div>'),
            "application/xhtml+xml")
    year = datetime.date.today().year
    add("title.xhtml", _doc(book["title"], f'<div class="title"><h1>{e(book["title"])}</h1>'
                                           + (f"<p><em>{e(L['subtitle'])}</em></p>" if L.get("subtitle") else "")
                                           + f"<p>{e(author)}</p></div>"
                                           f'<div class="copy"><p>Copyright © {year} {e(author)}. All rights reserved.</p></div>'),
        "application/xhtml+xml", label="Title page")
    if book.get("dedication"):
        add("dedication.xhtml", _doc("Dedication", f'<div class="title">{_xhtml(text_html(book["dedication"]))}</div>'),
            "application/xhtml+xml")
    if book["kind"] == "puzzle":
        raise ValueError("Puzzle books are made for print; build them as pdf/kdp.")
    for r in chapter_rows(book):
        name = f"ch{r['n']:03d}.xhtml"
        pic = pictures.get(r["n"])
        img = ""
        if pic and pic.exists():
            iname = f"images/p{r['n']:03d}.jpg"
            add(iname, pic.read_bytes(), "image/jpeg", in_spine=False)
            img = f'<div class="pic"><img src="{iname}" alt="{e(r["picture"][:120] or r["title"])}"/></div>'
        if book["kind"] == "picture":
            body = f'<section class="page">{img}{_xhtml(text_html(r["text"]))}</section>'
        else:
            body = (f'<section epub:type="chapter"><span class="label">Chapter {r["n"]}</span><h1>{e(r["title"])}</h1>'
                    f"{img}{_xhtml(text_html(r['text']))}</section>")
        add(name, _doc(r["title"], body), "application/xhtml+xml", label=r["title"] if book["kind"] != "picture" else None)
    nav_items = "".join(f'<li><a href="{n}">{e(t)}</a></li>' for n, t in toc)
    add("nav.xhtml", _doc("Contents", f'<nav epub:type="toc" id="toc"><h1>Contents</h1><ol>{nav_items}</ol></nav>'),
        "application/xhtml+xml", "nav", in_spine=book["kind"] == "ebook")
    modified = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    opf = ('<?xml version="1.0" encoding="utf-8"?>\n'
           '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="en">'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
           f'<dc:identifier id="bookid">{uid}</dc:identifier><dc:title>{e(book["title"])}</dc:title>'
           f'<dc:creator>{e(author)}</dc:creator><dc:language>en</dc:language>'
           f'<meta property="dcterms:modified">{modified}</meta>'
           + (f"<dc:description>{e(book.get('brief', '')[:500])}</dc:description>" if book.get("brief") else "")
           + "</metadata><manifest>" + "".join(manifest) + "</manifest><spine>" + "".join(spine) + "</spine></package>")
    container = ('<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                 '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                 "</rootfiles></container>")
    tmp = dest.with_suffix(".part")
    with zipfile.ZipFile(tmp, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", container, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        for name, content in files:
            z.writestr("OEBPS/" + name, content, compress_type=zipfile.ZIP_DEFLATED)
    tmp.replace(dest)
    return dest
