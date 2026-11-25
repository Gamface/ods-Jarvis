"""Amazon KDP paperback rules: margins, spine, cover size, listing kit checks.

Numbers from KDP's paperback guides (print options, margins, cover calculator):
- inside margin grows with page count; outside, top and bottom at least 0.25 in (0.375 with bleed)
- bleed adds 0.125 in to the outside edge and 0.125 in to top and bottom of each page
- spine width = pages x paper thickness; spine text only from 80 pages
- page count 24 to 828 (white/cream), fewer for colour
"""

import html
import re

MIN_PAGES = 24
MAX_PAGES = {"white": 828, "cream": 776, "color": 480}
PAPER_IN = {"white": 0.002252, "cream": 0.0025, "color": 0.002347}  # inches per page (premium colour)
BLEED = 0.125
SPINE_TEXT_MIN = 80
BARCODE = (2.0, 1.2)       # width x height of the clear area, inches
ALLOWED_TAGS = {"b", "i", "u", "em", "strong", "br", "p", "h4", "h5", "h6", "ul", "ol", "li"}


def inside_margin(pages: int) -> float:
    """KDP's minimum inside (gutter) margin for this many pages."""
    for limit, m in ((150, 0.375), (300, 0.5), (500, 0.625), (700, 0.75)):
        if pages <= limit:
            return m
    return 0.875


def margins(pages: int, bleed: bool = False) -> dict:
    """Comfortable margins that stay above KDP's minimums (inches)."""
    return {"inside": inside_margin(pages) + 0.25, "outside": 0.5 if not bleed else 0.5,
            "top": 0.7, "bottom": 0.75}


def spine_width(pages: int, paper: str) -> float:
    return pages * PAPER_IN.get(paper, PAPER_IN["white"])


def cover_size(trim: tuple[float, float], pages: int, paper: str) -> dict:
    w, h = trim
    spine = spine_width(pages, paper)
    return {"width": BLEED + w + spine + w + BLEED, "height": BLEED + h + BLEED, "spine": spine,
            "trim_w": w, "trim_h": h, "spine_text": pages >= SPINE_TEXT_MIN}


def check_pages(pages: int, paper: str) -> list[str]:
    notes = []
    if pages > MAX_PAGES.get(paper, 828):
        notes.append(f"{pages} pages is over KDP's limit of {MAX_PAGES[paper]} for {paper} paper; "
                     "use a bigger trim size or split the book.")
    return notes


def _clean_html(text: str) -> str:
    """Keep only the tags KDP allows in a description; escape everything else."""
    out = []
    for part in re.split(r"(<[^>]*>)", text):
        m = re.fullmatch(r"<\s*(/?)\s*([a-zA-Z0-9]+)[^>]*?(/?)\s*>", part)
        if m and m.group(2).lower() in ALLOWED_TAGS:
            tag = m.group(2).lower()
            out.append(f"<{m.group(1)}{tag}{' /' if tag == 'br' else ''}>")
        elif part.startswith("<"):
            continue  # drop other tags
        else:
            out.append(html.escape(html.unescape(part), quote=False))
    return "".join(out).strip()


def clean_listing(listing: dict, description=None, keywords=None, categories=None, subtitle=None,
                  back_text=None) -> tuple[dict, list[str]]:
    notes = []
    if description is not None:
        d = description.replace("\\n", "\n").strip()
        if "<" not in d:  # plain text: paragraphs -> <p>
            d = "".join(f"<p>{html.escape(p.strip())}</p>" for p in re.split(r"\n\s*\n", d) if p.strip())
        d = _clean_html(d)
        if len(d) > 4000:
            notes.append(f"Description was {len(d)} characters; KDP allows 4000, so it was cut.")
            d = d[:4000].rsplit(" ", 1)[0]
        listing["description"] = d
    if keywords is not None:
        if isinstance(keywords, str):
            keywords = re.split(r"[;\n]|,(?![^()]*\))", keywords)
        kw = []
        for k in keywords:
            k = re.sub(r"\s+", " ", str(k)).strip(" ,.\"'")
            low = k.lower()
            if not k or low in {x.lower() for x in kw}:
                continue
            if re.search(r"\b(best ?seller|free|kindle unlimited|new release|amazon|#1)\b", low):
                notes.append(f"Dropped keyword '{k}' (KDP doesn't allow claims, prices or 'Amazon' terms).")
                continue
            if len(k) > 50:
                notes.append(f"Shortened keyword '{k}' to 50 characters.")
                k = k[:50].rsplit(" ", 1)[0]
            kw.append(k)
        if len(kw) > 7:
            notes.append(f"KDP takes 7 keywords; kept the first 7 of {len(kw)}.")
        listing["keywords"] = kw[:7]
    if categories is not None:
        if isinstance(categories, str):
            categories = [c for c in re.split(r"[;\n]", categories) if c.strip()]
        listing["categories"] = [str(c).strip()[:200] for c in categories][:3]
        if len(categories) > 3:
            notes.append("KDP takes 3 categories; kept the first 3.")
    if subtitle is not None:
        listing["subtitle"] = subtitle.strip()[:200]
    if back_text is not None:
        bt = back_text.replace("\\n", "\n").strip()
        if len(bt.split()) > 170:
            notes.append(f"Back cover text is {len(bt.split())} words; about 150 fits a 6x9 back cover.")
        listing["back_text"] = bt[:2000]
    return listing, notes


def listing_markdown(book: dict, pages: int | None, cover: dict | None) -> str:
    L = book.get("listing", {})
    lines = [f"# KDP listing kit: {book['title']}", ""]
    lines += [f"**Title:** {book['title']}", f"**Subtitle:** {L.get('subtitle', '(none)')}",
              f"**Author:** {book.get('author') or '(add your pen name)'}", ""]
    lines += ["## Description (paste into KDP's description box)", "", L.get("description") or "(not written yet: ask Jarvis for book_listing)", ""]
    lines += ["## Keywords (7 boxes)", ""] + [f"{i}. {k}" for i, k in enumerate(L.get("keywords", []), 1)] + [""]
    lines += ["## Categories", ""] + [f"- {c}" for c in L.get("categories", [])] + [""]
    if L.get("back_text"):
        lines += ["## Back cover text", "", L["back_text"], ""]
    lines += ["## Print settings", ""]
    if pages:
        lines.append(f"- Trim size: {book['trim']} in; paper: {book['paper']}; bleed: "
                     f"{'yes' if book['kind'] == 'picture' else 'no'}; interior: "
                     f"{'premium colour' if book['paper'] == 'color' else 'black and white'}")
        lines.append(f"- Page count: {pages}")
    if cover:
        lines.append(f"- Cover PDF: {cover['width']:.3f} x {cover['height']:.3f} in, spine {cover['spine']:.3f} in"
                     f"{'' if cover['spine_text'] else ' (no spine text under 80 pages)'}")
        lines.append("- Leave the barcode area on the back cover clear; KDP prints the barcode there.")
    lines += ["", "Check everything in KDP's previewer before publishing. AI-made text or pictures must be "
              "declared as AI-generated in KDP's content questions."]
    return "\n".join(lines) + "\n"
