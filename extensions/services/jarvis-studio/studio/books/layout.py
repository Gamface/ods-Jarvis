"""Book pages as HTML, printed to PDF by headless Chrome (exact trim size, mirrored margins,
page numbers, bleed for picture books)."""

import hashlib
import html
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import kdp
from .project import KINDS, TRIMS

CHROME = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
FONTS = {
    "novel": ("'Gentium Book Basic', 'Gentium Basic', C059, 'DejaVu Serif', serif", "Lato, Inter, sans-serif", "11pt", 1.42),
    "ebook": ("'Gentium Book Basic', C059, 'DejaVu Serif', serif", "Lato, Inter, sans-serif", "10.8pt", 1.45),
    "picture": ("Andika, Lato, sans-serif", "Andika, Lato, sans-serif", "19pt", 1.35),
    "puzzle": ("Lato, Inter, 'DejaVu Sans', sans-serif", "Lato, Inter, sans-serif", "11pt", 1.4),
}
e = html.escape


# --- text ----------------------------------------------------------------------
def _inline(s: str) -> str:
    s = e(s, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", s)
    s = re.sub(r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])", r"<em>\1</em>", s)
    return s


def text_html(text: str) -> str:
    """Plain text with light Markdown (## headings, lists, > quotes, *italic*, **bold**,
    * * * scene breaks) -> HTML paragraphs."""
    out = []
    for block in re.split(r"\n\s*\n", (text or "").strip()):
        lines = [ln.rstrip() for ln in block.strip().splitlines() if ln.strip()]
        if not lines:
            continue
        first = lines[0].strip()
        if re.fullmatch(r"(\*\s*){3,}|(-\s*){3,}|#{1}\s*$|~+", first) and len(lines) == 1:
            out.append('<p class="break">* * *</p>')
        elif first.startswith("### "):
            out.append(f"<h3>{_inline(first[4:])}</h3>" + (text_html("\n".join(lines[1:])) if lines[1:] else ""))
        elif first.startswith("## ") or first.startswith("# "):
            out.append(f"<h2>{_inline(first.lstrip('#').strip())}</h2>" + (text_html("\n".join(lines[1:])) if lines[1:] else ""))
        elif all(re.match(r"\s*[-*•]\s+", ln) for ln in lines):
            out.append("<ul>" + "".join(f"<li>{_inline(re.sub(r'^\s*[-*•]\s+', '', ln))}</li>" for ln in lines) + "</ul>")
        elif all(re.match(r"\s*\d+[.)]\s+", ln) for ln in lines):
            out.append("<ol>" + "".join(f"<li>{_inline(re.sub(r'^\s*\d+[.)]\s+', '', ln))}</li>" for ln in lines) + "</ol>")
        elif all(ln.lstrip().startswith(">") for ln in lines):
            out.append("<blockquote>" + _inline(" ".join(ln.lstrip()[1:].strip() for ln in lines)) + "</blockquote>")
        else:
            out.append("<p>" + "<br>".join(_inline(ln.strip()) for ln in lines) + "</p>")
    return "\n".join(out)


def palette(title: str) -> tuple[str, str, str]:
    """Background, accent and text colours picked from the title (stable per book)."""
    sets = [("#1d2b3a", "#e9b44c", "#f6f1e7"), ("#2f1b2c", "#e07a5f", "#f8efe6"), ("#16302b", "#9bc53d", "#eef5e9"),
            ("#262a53", "#ffa0a0", "#f2f2f7"), ("#3b2416", "#d9a441", "#fbf3e4"), ("#0f3d3e", "#e2dcc8", "#f1f1f1"),
            ("#40263f", "#7fc8f8", "#f4f0f6"), ("#1f1f1f", "#ff6b35", "#fafafa")]
    return sets[int(hashlib.sha1(title.encode()).hexdigest(), 16) % len(sets)]


def book_colors(book: dict) -> tuple[str, str, str]:
    """The book's cover colours: cover.colors [background, accent, text] if set, else palette()."""
    c = (book.get("cover") or {}).get("colors")
    if isinstance(c, (list, tuple)) and len(c) == 3 and all(re.fullmatch(r"#[0-9a-fA-F]{6}", str(x)) for x in c):
        return tuple(c)
    return palette(book["title"])


# --- interiors -------------------------------------------------------------------
def _base_css(book: dict, m: dict | None, bleed: bool) -> str:
    w, h = TRIMS[book["trim"]]
    body, head, size, lh = FONTS[book["kind"]]
    if bleed:
        page = f"@page {{ size: {w + kdp.BLEED}in {h + 2 * kdp.BLEED}in; margin: 0; }}"
    else:
        page = (f"@page {{ size: {w}in {h}in; margin: {m['top']}in {m['outside']}in {m['bottom']}in {m['inside']}in;"
                f" @bottom-center {{ content: counter(page); font: 9pt {head}; color: #555; }} }}\n"
                f"@page :right {{ margin-left: {m['inside']}in; margin-right: {m['outside']}in; }}\n"
                f"@page :left {{ margin-left: {m['outside']}in; margin-right: {m['inside']}in; }}\n"
                "@page front { @bottom-center { content: none; } }\n"
                "@page blank { @bottom-center { content: none; } }")
    return page + f"""
html {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
body {{ margin: 0; font-family: {body}; font-size: {size}; line-height: {lh}; color: #111; }}
h1, h2, h3, .label {{ font-family: {head}; }}
.front {{ page: front; break-after: page; }}
.blank {{ page: blank; break-after: page; height: 1px; }}
.titlepage {{ text-align: center; padding-top: 28%; }}
.titlepage h1 {{ font-size: 26pt; line-height: 1.15; margin: 0 0 .4em; }}
.titlepage .sub {{ font-size: 13pt; font-style: italic; margin-bottom: 3em; }}
.titlepage .by {{ font-size: 13pt; letter-spacing: .08em; text-transform: uppercase; }}
.copyright {{ font-size: 8.5pt; line-height: 1.5; padding-top: 70%; color: #333; }}
.dedication {{ text-align: center; font-style: italic; padding-top: 35%; }}
.toc h2 {{ text-align: center; margin: 1.5em 0 1.2em; }}
.toc ol {{ list-style: none; padding: 0; }}
.toc li {{ display: flex; gap: .4em; margin: .35em 0; }}
.toc li .t {{ flex: 1; border-bottom: 1px dotted #aaa; }}
.chapter {{ break-before: page; }}
.chapter .label {{ display: block; text-align: center; font-size: 9.5pt; letter-spacing: .25em;
  text-transform: uppercase; color: #666; margin-top: 18%; }}
.chapter h1 {{ text-align: center; font-size: 19pt; font-weight: 600; margin: .35em 0 1.8em; line-height: 1.2; }}
.text p {{ margin: 0; text-indent: 1.4em; text-align: justify; hyphens: auto; orphans: 2; widows: 2; }}
.text h1 + p, .text h2 + p, .text h3 + p, .text p:first-child, .text .break + p, .text blockquote + p,
.text ul + p, .text ol + p {{ text-indent: 0; }}
.text h2 {{ font-size: 13.5pt; margin: 1.3em 0 .5em; break-after: avoid; }}
.text h3 {{ font-size: 11.5pt; margin: 1.1em 0 .4em; break-after: avoid; }}
.text ul, .text ol {{ margin: .5em 0 .5em 1.2em; padding-left: 1em; }}
.text li {{ margin: .2em 0; }}
.text blockquote {{ margin: .8em 1.5em; font-style: italic; }}
.text .break {{ text-align: center; text-indent: 0; margin: .9em 0; letter-spacing: .4em; }}
"""


def _front_matter(book: dict, toc: list | None) -> str:
    L = book.get("listing", {})
    author = book.get("author") or ""
    import datetime
    year = datetime.date.today().year
    parts = [f'<section class="front titlepage"><h1>{e(book["title"])}</h1>'
             + (f'<div class="sub">{e(L["subtitle"])}</div>' if L.get("subtitle") else "")
             + (f'<div class="by">{e(author)}</div>' if author else "") + "</section>"]
    parts.append(f'<section class="front copyright"><p>{e(book["title"])}</p><p>Copyright © {year} '
                 f'{e(author or "the author")}. All rights reserved.</p><p>No part of this book may be reproduced '
                 "without written permission from the author, except for brief quotations in reviews.</p>"
                 + ("<p>This is a work of fiction. Names, characters, places and events are products of the "
                    "author's imagination; any resemblance to real people or events is coincidental.</p>"
                    if book["kind"] == "novel" else "")
                 + "</section>")
    if book.get("dedication"):
        parts.append(f'<section class="front dedication">{text_html(book["dedication"])}</section>')
    if toc is not None:
        items = "".join(f'<li><span class="t">{e(t)}</span><span>{p or ""}</span></li>' for t, p in toc)
        parts.append(f'<section class="front toc"><h2>Contents</h2><ol>{items}</ol></section>')
    return "\n".join(parts)


def prose_html(book: dict, m: dict, toc_pages: dict | None = None, pad_to: int = 0) -> str:
    from .project import chapter_rows
    rows = chapter_rows(book)
    want_toc = book["kind"] == "ebook" or len(rows) > 1 and book.get("toc", book["kind"] == "ebook")
    toc = [(r["title"], (toc_pages or {}).get(r["n"])) for r in rows] if want_toc else None
    body = [_front_matter(book, toc)]
    for r in rows:
        body.append(f'<section class="chapter"><span class="label">Chapter {r["n"]}</span>'
                    f'<h1>{e(r["title"])}</h1><div class="text">{text_html(r["text"])}</div></section>')
    body.append('<div class="blank"></div>' * pad_to)
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><style>{_base_css(book, m, False)}'
            f'</style></head><body>{"".join(body)}</body></html>')


# --- picture book -----------------------------------------------------------------
def picture_html(book: dict, pictures: dict[int, Path], pad_to: int = 0, cover_pic: Path | None = None) -> str:
    from .project import chapter_rows
    w, h = TRIMS[book["trim"]]
    pw, ph = w + kdp.BLEED, h + 2 * kdp.BLEED
    bg, accent, fg = book_colors(book)
    css = _base_css(book, None, True) + f"""
.pg {{ width: {pw}in; height: {ph}in; position: relative; overflow: hidden; break-after: page; }}
.pg img.full {{ position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }}
.pg .words {{ position: absolute; left: .75in; right: .75in; bottom: .8in; background: rgba(255,255,255,.88);
  border-radius: .18in; padding: .18in .28in; text-align: center; font-size: 19pt; line-height: 1.35; }}
.pg.nopic .words {{ background: none; top: 30%; bottom: auto; }}
.pg.title {{ background: {bg}; color: {fg}; text-align: center; }}
.pg.title h1 {{ position: absolute; left: .8in; right: .8in; top: 32%; font-size: 34pt; line-height: 1.15; margin: 0; }}
.pg.title .by {{ position: absolute; left: 0; right: 0; bottom: 1.2in; font-size: 15pt; color: {accent}; }}
.pg.info {{ font-size: 9pt; }}
.pg.info .copy {{ position: absolute; left: .75in; right: .75in; bottom: 1in; line-height: 1.5; }}
.pg.end h2 {{ position: absolute; left: 0; right: 0; top: 42%; text-align: center; font-size: 30pt; }}
"""
    import datetime
    pages = [f'<div class="pg title"><h1>{e(book["title"])}</h1>'
             + (f'<div class="by">{e(book["author"])}</div>' if book.get("author") else "") + "</div>",
             f'<div class="pg info"><div class="copy">{e(book["title"])}<br>Copyright © {datetime.date.today().year} '
             f'{e(book.get("author") or "the author")}. All rights reserved.'
             + (f"<br><br>{e(book['dedication'])}" if book.get("dedication") else "") + "</div></div>"]
    for r in chapter_rows(book):
        pic = pictures.get(r["n"])
        img = f'<img class="full" src="{pic.as_uri()}">' if pic else ""
        words = f'<div class="words">{text_html(r["text"])}</div>' if r["text"].strip() else ""
        pages.append(f'<div class="pg{"" if pic else " nopic"}">{img}{words}</div>')
    pages.append('<div class="pg end"><h2>The End</h2></div>')
    pages += ['<div class="pg"></div>'] * pad_to
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><style>{css}</style></head>'
            f'<body>{"".join(pages)}</body></html>')


def picture_page_count(book: dict) -> int:
    return 2 + len(book.get("outline", [])) + 1


# --- puzzle book --------------------------------------------------------------------
def _ws_table(p: dict, answer: bool = False) -> str:
    hit = {tuple(c) for s in p.get("solution", []) for c in s["cells"]} if answer else set()
    rows = "".join("<tr>" + "".join(f'<td class="{"hit" if (y, x) in hit else ""}">{ch}</td>'
                                    for x, ch in enumerate(row)) + "</tr>" for y, row in enumerate(p["grid"]))
    return f'<table class="ws">{rows}</table>'


def _sudoku_table(grid: str, given: str | None = None) -> str:
    rows = []
    for r in range(9):
        cells = []
        for c in range(9):
            v = grid[r * 9 + c]
            cls = "g" if given is None or given[r * 9 + c] != "0" else "a"
            cells.append(f'<td class="{cls}">{v if v != "0" else ""}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return f'<table class="su">{"".join(rows)}</table>'


def _logic_grids(p: dict) -> str:
    cats = p["categories"]
    out = []
    for a in range(len(cats)):
        for b in range(a + 1, len(cats)):
            head = "".join(f'<th class="v"><span>{e(v)}</span></th>' for v in cats[b]["values"])
            body = "".join(f"<tr><th>{e(v)}</th>" + "<td></td>" * len(cats[b]["values"]) + "</tr>"
                           for v in cats[a]["values"])
            out.append(f'<table class="lg"><caption>{e(cats[a]["name"])} × {e(cats[b]["name"])}</caption>'
                       f"<tr><th></th>{head}</tr>{body}</table>")
    return '<div class="lgs">' + "".join(out) + "</div>"


def _answer_table(p: dict) -> str:
    head = "".join(f"<th>{e(c['name'])}</th>" for c in p["categories"])
    rows = "".join("<tr>" + "".join(f"<td>{e(v)}</td>" for v in row) + "</tr>" for row in p["solution"])
    return f'<table class="ans"><tr>{head}</tr>{rows}</table>'


def puzzle_html(book: dict, m: dict, pad_to: int = 0) -> str:
    w, h = TRIMS[book["trim"]]
    small = w < 7
    css = _base_css(book, m, False) + f"""
.pz {{ break-before: page; }}
.pz h2 {{ text-align: center; font-size: {14 if small else 17}pt; margin: .2em 0 .2em; }}
.pz .meta {{ text-align: center; font-size: 9pt; color: #666; letter-spacing: .12em; text-transform: uppercase; margin-bottom: 1em; }}
.pz .story {{ font-size: 10.5pt; margin-bottom: .8em; }}
.pz .story p {{ margin: 0 0 .5em; }}
table {{ border-collapse: collapse; margin: 0 auto; }}
.ws td {{ width: {0.27 if small else 0.36}in; height: {0.27 if small else 0.36}in; text-align: center;
  font: 600 {10 if small else 13}pt 'DejaVu Sans Mono', monospace; }}
.ws {{ border: 1.5px solid #222; }}
.ws td.hit {{ background: #ffe08a; }}
.words {{ columns: 3; margin: 1.2em auto 0; max-width: 90%; font-size: 10pt; text-transform: uppercase; letter-spacing: .05em; }}
.su td {{ width: {0.42 if small else 0.62}in; height: {0.42 if small else 0.62}in; border: 1px solid #888;
  text-align: center; font-size: {15 if small else 22}pt; }}
.su {{ border: 2.5px solid #111; margin-top: .4in; }}
.su tr:nth-child(3n) td {{ border-bottom: 2.5px solid #111; }}
.su td:nth-child(3n) {{ border-right: 2.5px solid #111; }}
.su td.a {{ color: #2a6fdb; }}
.clues {{ font-size: 10pt; margin: 0 0 .9em 1.2em; padding: 0; }}
.clues li {{ margin: .2em 0; }}
.lgs {{ display: flex; flex-wrap: wrap; gap: .18in; justify-content: center; }}
.lg {{ margin: 0; font-size: 7.5pt; }}
.lg caption {{ font-size: 8pt; font-weight: 600; padding-bottom: 2px; }}
.lg td {{ width: .24in; height: .24in; border: 1px solid #777; }}
.lg th {{ text-align: right; padding-right: 3px; font-weight: 500; white-space: nowrap; }}
.lg th.v {{ height: .9in; vertical-align: bottom; padding: 0; }}
.lg th.v span {{ writing-mode: vertical-rl; transform: rotate(180deg); white-space: nowrap; }}
.ans {{ font-size: 9pt; margin: .3em auto .6em; }}
.ans td, .ans th {{ border: 1px solid #999; padding: 2px 6px; }}
.scene {{ font-weight: 600; margin: .3em 0 .6em; }}
.answers {{ break-before: page; }}
.answers h1 {{ text-align: center; }}
.akey {{ display: grid; grid-template-columns: 1fr 1fr; gap: .25in .3in; }}
.akey .one {{ break-inside: avoid; text-align: center; font-size: 8pt; }}
.akey .one h4 {{ margin: 0 0 .08in; font-size: 9pt; }}
.akey .ws td {{ width: {0.13 if small else 0.17}in; height: {0.13 if small else 0.17}in; font-size: {5.5 if small else 7}pt; }}
.akey .su {{ margin-top: 0; border-width: 1.5px; }}
.akey .su td {{ width: {0.17 if small else 0.24}in; height: {0.17 if small else 0.24}in; font-size: {7 if small else 9}pt; }}
.howto p {{ margin: 0 0 .6em; }}
"""
    pz = book.get("puzzles", [])
    types = {p["type"] for p in pz}
    howto = ['<section class="front howto"><h2 style="text-align:center">How to play</h2>']
    if "wordsearch" in types:
        howto.append("<p><b>Word search:</b> find every word in the list. Words run across, down"
                     + (", diagonally and backwards" if any(p["difficulty"] == "hard" for p in pz if p["type"] == "wordsearch")
                        else " and diagonally" if any(p["difficulty"] == "medium" for p in pz if p["type"] == "wordsearch") else "")
                     + ".</p>")
    if "sudoku" in types:
        howto.append("<p><b>Sudoku:</b> fill the grid so every row, column and 3×3 box has the numbers 1 to 9 once each.</p>")
    if "logic" in types:
        howto.append("<p><b>Logic puzzles:</b> each person matches exactly one item in every group. Use the clues and "
                     "mark ✓ or ✗ in the grids until only one answer is left.</p>")
    if "whodunit" in types:
        howto.append("<p><b>Mysteries:</b> read the case, then use the clues and the grids to work out where everyone was "
                     "and what they had. The crime scene facts point to the culprit.</p>")
    howto.append("<p>Answers are at the back of the book.</p></section>")
    body = [_front_matter(book, None), "".join(howto)]
    for i, p in enumerate(pz, 1):
        meta = f'<div class="meta">Puzzle {i} · {p["difficulty"]}</div>'
        story = f'<div class="story">{text_html(p["story"])}</div>' if p.get("story") else ""
        if p["type"] == "wordsearch":
            inner = _ws_table(p) + '<div class="words">' + "<br>".join(e(x) for x in p["words"]) + "</div>"
        elif p["type"] == "sudoku":
            inner = _sudoku_table(p["grid"])
        else:
            scene = f'<p class="scene">{e(p["scene"])}</p>' if p["type"] == "whodunit" else ""
            cats = "".join(f"<li><b>{e(c['name'])}:</b> {e(', '.join(c['values']))}</li>" for c in p["categories"])
            clues = "".join(f"<li>{e(c)}</li>" for c in p["clues"])
            q = f'<p><b>{e(p["question"])}</b></p>' if p.get("question") else ""
            inner = (f'<ul class="clues" style="list-style:none;margin-left:0">{cats}</ul>{scene}'
                     f'<ol class="clues">{clues}</ol>{q}{_logic_grids(p)}')
        body.append(f'<section class="pz"><h2>{e(p["title"])}</h2>{meta}{story}{inner}</section>')
    keys = []
    for i, p in enumerate(pz, 1):
        if p["type"] == "wordsearch":
            k = _ws_table(p, True)
        elif p["type"] == "sudoku":
            k = _sudoku_table(p["solution"], p["grid"])
        else:
            k = _answer_table(p) + (f"<div><b>Culprit: {e(p['culprit'])}</b></div>" if p["type"] == "whodunit" else "")
        keys.append(f'<div class="one"><h4>{i}. {e(p["title"])}</h4>{k}</div>')
    if keys:
        body.append(f'<section class="answers"><h1>Answers</h1><div class="akey">{"".join(keys)}</div></section>')
    body.append('<div class="blank"></div>' * pad_to)
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><style>{css}</style></head>'
            f'<body>{"".join(body)}</body></html>')


# --- cover --------------------------------------------------------------------------
def cover_html(book: dict, size: dict, front_pic: Path | None) -> str:
    L = book.get("listing", {})
    bg, accent, fg = book_colors(book)
    W, H, S = size["width"], size["height"], size["spine"]
    b = kdp.BLEED
    tw = size["trim_w"]
    back_x, spine_x, front_x = 0, b + tw, b + tw + S
    head = FONTS[book["kind"]][1]
    title_font = FONTS["novel"][0] if book["kind"] in ("novel", "ebook") else head
    pic = (f'<img src="{front_pic.as_uri()}" style="position:absolute;left:0;top:0;width:100%;height:100%;object-fit:cover">'
           f'<div style="position:absolute;inset:0;background:linear-gradient(180deg,rgba(0,0,0,.55) 0%,rgba(0,0,0,0) 38%,rgba(0,0,0,0) 62%,rgba(0,0,0,.6) 100%)"></div>'
           if front_pic else
           f'<div style="position:absolute;left:12%;right:12%;top:46%;height:3px;background:{accent}"></div>')
    back_text = text_html(L.get("back_text") or "")
    spine = ""
    if size["spine_text"]:
        spine = (f'<div style="position:absolute;left:{spine_x}in;top:0;width:{S}in;height:{H}in;display:flex;'
                 f'align-items:center;justify-content:center"><div style="writing-mode:vertical-rl;font:600 '
                 f'{min(14, S * 72 * 0.45):.1f}pt {head};color:{fg};white-space:nowrap;letter-spacing:.06em">'
                 f'{e(book["title"])}{"  ·  " + e(book["author"]) if book.get("author") else ""}</div></div>')
    bw, bh = kdp.BARCODE
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
@page {{ size: {W}in {H}in; margin: 0; }}
html {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
body {{ margin: 0; width: {W}in; height: {H}in; position: relative; background: {bg}; overflow: hidden; font-family: {head}; }}
.back {{ position: absolute; left: {back_x + b + 0.5}in; top: {b + 0.6}in; width: {tw - 1.0}in; color: {fg};
  font-size: 10.5pt; line-height: 1.5; font-family: {FONTS['novel'][0]}; }}
.back p {{ margin: 0 0 .7em; }}
.front {{ position: absolute; left: {front_x}in; top: 0; width: {tw + b}in; height: {H}in; overflow: hidden; }}
.ttl {{ position: absolute; left: .55in; right: {0.55 + b}in; top: {b + 0.7}in; text-align: center; color: #fff;
  font: 700 {30 if tw >= 7 else 26}pt/1.12 {title_font}; text-shadow: 0 2px 10px rgba(0,0,0,.45); }}
.sub {{ display: block; font: italic 400 {13 if tw >= 7 else 12}pt/1.3 {title_font}; margin-top: .35em; }}
.au {{ position: absolute; left: .55in; right: {0.55 + b}in; bottom: {b + 0.6}in; text-align: center; color: #fff;
  font: 600 14pt {head}; letter-spacing: .14em; text-transform: uppercase; text-shadow: 0 1px 8px rgba(0,0,0,.5); }}
</style></head><body>
<div class="back">{back_text}</div>
<div class="front" style="background:{bg}">{pic}
<div class="ttl">{e(book["title"])}{f'<span class="sub">{e(L["subtitle"])}</span>' if L.get("subtitle") else ""}</div>
{f'<div class="au">{e(book["author"])}</div>' if book.get("author") else ""}</div>
{spine}
<!-- barcode area (kept clear): {bw} x {bh} in at the back cover's lower right -->
</body></html>"""


def front_only_html(book: dict, front_pic: Path | None) -> str:
    """Ebook cover page (1600 x 2560 px screenshot)."""
    size = {"width": 6.25 + 6.25, "height": 10.0, "spine": 0, "trim_w": 6.25 - kdp.BLEED, "spine_text": False}
    page = cover_html(book, size, front_pic)
    shift = f"<style>body{{width:6.25in;height:10in}} .front{{left:0!important;width:6.25in!important}} .back{{display:none}}</style>"
    return page.replace("</head>", shift + "</head>")


# --- Chrome -------------------------------------------------------------------------
def print_pdf(html_text: str, dest: Path, work: Path) -> Path:
    if not CHROME:
        raise RuntimeError("Google Chrome or Chromium is needed to print PDFs.")
    work.mkdir(parents=True, exist_ok=True)
    src = work / (dest.stem + ".html")
    src.write_text(html_text, encoding="utf-8")
    tmp = dest.with_name(dest.stem + ".part.pdf")
    with tempfile.TemporaryDirectory(prefix="chrome-", dir=work) as prof:
        r = subprocess.run([CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer", "--no-first-run",
                            "--allow-file-access-from-files", f"--user-data-dir={prof}",
                            "--virtual-time-budget=20000", f"--print-to-pdf={tmp}", src.as_uri()],
                           capture_output=True, text=True, timeout=600)
    if r.returncode != 0 or not tmp.exists():
        raise RuntimeError(f"Chrome could not print the PDF: {(r.stderr or '')[-400:]}")
    tmp.replace(dest)
    return dest


def screenshot(html_text: str, dest_png: Path, work: Path, size=(1600, 2560)) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    src = work / (dest_png.stem + ".html")
    src.write_text(html_text, encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="chrome-", dir=work) as prof:
        # 6.25in at 96 px/in = 600 px wide; zoom so it fills the requested size
        scale = size[0] / 600
        subprocess.run([CHROME, "--headless", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                        "--allow-file-access-from-files", f"--user-data-dir={prof}",
                        f"--force-device-scale-factor={scale}", f"--window-size=600,{round(size[1] / scale)}",
                        "--virtual-time-budget=20000", f"--screenshot={dest_png}", src.as_uri()],
                       capture_output=True, text=True, timeout=300)
    if not dest_png.exists():
        raise RuntimeError("Chrome could not make the ebook cover picture.")
    return dest_png


def page_count(pdf: Path) -> int:
    r = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True)
    m = re.search(r"^Pages:\s+(\d+)", r.stdout, re.M)
    if m:
        return int(m.group(1))
    return len(re.findall(rb"/Type\s*/Page\b", pdf.read_bytes()))


def chapter_pages(pdf: Path, n: int) -> dict[int, int]:
    """Page number where each 'Chapter N' label starts."""
    r = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True)
    found = {}
    for i, page in enumerate(r.stdout.split("\f"), 1):
        first = next((ln.strip() for ln in page.splitlines() if ln.strip()), "")
        m = re.fullmatch(r"C\s*H\s*A\s*P\s*T\s*E\s*R\s+(\d+)|Chapter\s+(\d+)", first, re.I)
        if m:
            k = int(m.group(1) or m.group(2))
            found.setdefault(k, i)
    return {k: v for k, v in found.items() if k <= n}
