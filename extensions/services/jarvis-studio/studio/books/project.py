"""Book projects: plan -> outline (you approve it) -> chapters or pages -> build.

A project is BOOKS_OUT/<title>/project.json. Chapters are written one at a time by Jarvis;
book_context gives him just what he needs for the next one (plan, outline, continuity notes,
the end of the previous chapter), trimmed so a small context window is enough.
"""

import datetime
import json
import os
import random
import re
import shutil
from pathlib import Path

from ..config import safe_name

BOOKS_OUT = Path(os.environ.get("JARVIS_BOOKS_OUT", "~/Documents/Jarvis/books")).expanduser()

KINDS = {
    "novel": {"label": "Novel", "trim": "6x9", "paper": "cream", "target_words": 2500},
    "ebook": {"label": "Nonfiction ebook", "trim": "6x9", "paper": "white", "target_words": 1500},
    "picture": {"label": "Picture book", "trim": "8.5x8.5", "paper": "color", "target_words": 40},
    "puzzle": {"label": "Puzzle book", "trim": "8.5x11", "paper": "white", "target_words": 0},
}
KIND_ALIASES = {"fiction": "novel", "story": "novel", "nonfiction": "ebook", "guide": "ebook",
                "children": "picture", "kids": "picture", "picture-book": "picture",
                "puzzles": "puzzle", "activity": "puzzle", "puzzle-book": "puzzle"}
TRIMS = {"5x8": (5, 8), "5.5x8.5": (5.5, 8.5), "6x9": (6, 9), "7x10": (7, 10), "8x10": (8, 10),
         "8.5x8.5": (8.5, 8.5), "8.5x11": (8.5, 11), "8x8": (8, 8)}
PAPERS = ("white", "cream", "color")
MAX_CHAPTERS = 120
MAX_TEXT = 60_000          # characters per chapter save
STATUS_TEXT = {"planning": "planning: needs a plan and an outline",
               "review": "outline ready: waiting for your approval",
               "writing": "approved: writing", "ready": "ready to build"}


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def words(text: str) -> int:
    return len(re.findall(r"[\w'’-]+", text or ""))


def book_dir(title: str) -> Path:
    if not (title or "").strip():
        raise ValueError("Give the book a title.")
    return BOOKS_OUT / safe_name(title.strip())


def load(title: str) -> dict:
    p = book_dir(title) / "project.json"
    if not p.exists():
        near = [b["title"] for b in list_books() if title.lower() in b["title"].lower()]
        hint = f" Did you mean: {', '.join(near[:3])}?" if near else " Start one with book_new."
        raise FileNotFoundError(f"No book called '{title}'.{hint}")
    return json.loads(p.read_text())


def save(book: dict) -> None:
    d = book_dir(book["title"])
    d.mkdir(parents=True, exist_ok=True)
    book["updated"] = _now()
    book["status"] = status_of(book)
    tmp = d / "project.json.tmp"
    tmp.write_text(json.dumps(book, indent=1, ensure_ascii=False))
    os.replace(tmp, d / "project.json")


def status_of(b: dict) -> str:
    if b["kind"] == "puzzle":
        return "ready" if b.get("puzzles") else "planning"
    if not b.get("outline"):
        return "planning"
    if not b.get("approved"):
        return "review"
    done = [c for c in b.get("chapters", []) if c.get("text", "").strip()]
    return "ready" if len(done) >= len(b["outline"]) else "writing"


def _kind(kind: str | None) -> str:
    k = (kind or "novel").strip().lower()
    k = KIND_ALIASES.get(k, k)
    if k not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    return k


def create(title: str, kind: str, brief: str = "", audience: str = "", author: str = "",
           trim: str | None = None, paper: str | None = None, style: str | None = None,
           target_words: int | None = None, replace: bool = False) -> dict:
    kind = _kind(kind)
    title = (title or "").strip()[:120]
    d = book_dir(title)
    if (d / "project.json").exists() and not replace:
        raise FileExistsError(f"A book called '{title}' already exists. Use book_status to see it, "
                              "or replace=True to start it over.")
    if replace and d.exists():
        shutil.rmtree(d)
    k = KINDS[kind]
    trim = (trim or k["trim"]).replace(" ", "").replace("in", "").replace("×", "x")
    if trim not in TRIMS:
        raise ValueError(f"trim must be one of {', '.join(TRIMS)}")
    paper = (paper or k["paper"]).lower()
    if paper not in PAPERS:
        raise ValueError(f"paper must be one of {', '.join(PAPERS)}")
    book = {"title": title, "kind": kind, "brief": (brief or "").strip()[:4000],
            "audience": (audience or "").strip()[:300], "author": (author or "").strip()[:120],
            "trim": trim, "paper": paper, "style": (style or "").strip()[:400],
            "seed": random.randint(1, 2**31 - 1),
            "target_words": int(target_words or k["target_words"]), "plan": "", "characters": {},
            "outline": [], "approved": False, "approved_at": None, "chapters": [], "puzzles": [],
            "listing": {}, "cover": {}, "dedication": "", "created": _now()}
    save(book)
    return book


def set_plan(title: str, plan: str | None = None, outline=None, characters=None, style: str | None = None,
             cover: str | None = None, dedication: str | None = None, author: str | None = None,
             subtitle: str | None = None, target_words: int | None = None, approve: bool = False) -> dict:
    b = load(title)
    if plan is not None:
        b["plan"] = plan.strip()[:20_000]
    if characters is not None:
        if isinstance(characters, str):
            characters = _parse_pairs(characters)
        if not isinstance(characters, dict):
            raise ValueError('characters: {"Name": "one exact sentence describing how they look"}')
        b["characters"] = {str(k).strip()[:60]: str(v).strip()[:400] for k, v in characters.items() if str(k).strip()}
    if style is not None:
        b["style"] = style.strip()[:400]
    if cover is not None:
        b.setdefault("cover", {})["picture"] = cover.strip()[:600]
    if dedication is not None:
        b["dedication"] = dedication.strip()[:600]
    if author is not None:
        b["author"] = author.strip()[:120]
    if subtitle is not None:
        b.setdefault("listing", {})["subtitle"] = subtitle.strip()[:200]
    if target_words:
        b["target_words"] = max(20, min(int(target_words), 8000))
    if outline is not None:
        new = _clean_outline(outline)
        if new != b.get("outline"):
            b["outline"] = new
            b["approved"] = False  # a changed outline needs a fresh look
            chapters = b.get("chapters", [])
            b["chapters"] = (chapters + [{} for _ in new])[:len(new)]
            for c, o in zip(b["chapters"], new):
                c.setdefault("title", o["title"])
    if approve:
        if not b.get("outline"):
            raise ValueError("There is no outline to approve yet.")
        b["approved"], b["approved_at"] = True, _now()
    save(b)
    return b


def approve(title: str, ok: bool = True) -> dict:
    b = load(title)
    if ok and not b.get("outline"):
        raise ValueError("There is no outline to approve yet.")
    b["approved"], b["approved_at"] = bool(ok), _now() if ok else None
    save(b)
    return b


def _parse_pairs(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip(" -*")] = v.strip()
    return out


def _clean_outline(outline) -> list[dict]:
    if isinstance(outline, str):
        try:
            outline = json.loads(outline)
        except ValueError:
            items = []
            for line in outline.replace("\\n", "\n").splitlines():
                line = re.sub(r"^\s*(\d+[.)]|[-*])\s*", "", line).strip()
                if not line:
                    continue
                m = re.split(r"\s+[—–-]\s+|:\s+", line, maxsplit=1)
                items.append({"title": m[0].strip(), "summary": m[1].strip() if len(m) > 1 else ""})
            outline = items
    if not isinstance(outline, list) or not outline:
        raise ValueError('outline: a list of {"title": ..., "summary": what happens / what it covers}')
    out = []
    for i, o in enumerate(outline[:MAX_CHAPTERS]):
        if isinstance(o, str):
            o = {"title": o}
        if not isinstance(o, dict):
            raise ValueError(f"outline item {i + 1} must be an object with title and summary")
        t = str(o.get("title") or o.get("name") or f"Chapter {i + 1}").strip()[:160]
        s = str(o.get("summary") or o.get("beats") or o.get("text") or "").strip()[:2000]
        item = {"title": t, "summary": s}
        if o.get("picture"):
            item["picture"] = str(o["picture"]).strip()[:600]
        out.append(item)
    return out


def write(title: str, chapter: int, text: str, notes: str | None = None, append: bool = False,
          chapter_title: str | None = None, picture: str | None = None, image: str | None = None,
          new_picture: bool = False) -> dict:
    b = load(title)
    if b["kind"] == "puzzle":
        raise ValueError("Puzzle books use book_puzzles (and book_puzzles story= for case stories).")
    if not b.get("outline"):
        raise ValueError("Make the outline first with book_plan.")
    if not b.get("approved"):
        raise PermissionError("The outline isn't approved yet. Show it to the user and wait for their OK "
                              "(they can approve it on the dashboard's Books tab).")
    n = len(b["outline"])
    if not 1 <= int(chapter) <= n:
        raise ValueError(f"chapter must be 1 to {n}")
    text = (text or "").replace("\r", "")
    if "\n" not in text and "\\n" in text:
        text = text.replace("\\n", "\n")
    text = re.sub(r"^\s*#\s*(chapter\s+\d+[^\n]*|" + re.escape(b["outline"][chapter - 1]["title"]) + r")\s*\n",
                  "", text.strip(), flags=re.I)  # drop a repeated heading
    if len(text) > MAX_TEXT:
        raise ValueError(f"That's too long for one save ({len(text)} characters); save it in parts with append=True.")
    c = b["chapters"][chapter - 1]
    if append and c.get("text"):
        c["text"] = c["text"].rstrip() + "\n\n" + text
    elif text or not append:
        c["text"] = text
    if notes is not None:
        c["notes"] = notes.strip()[:1500]
    if chapter_title:
        c["title"] = b["outline"][chapter - 1]["title"] = chapter_title.strip()[:160]
    if picture is not None:
        c["picture"] = picture.strip()[:600]
    if image is not None:
        if image and not Path(image).expanduser().is_file():
            raise FileNotFoundError(f"No picture at {image}")
        c["image"] = str(Path(image).expanduser()) if image else ""
    if new_picture:
        c["pic_seed"] = random.randint(1, 2**31 - 1)
    c["words"] = words(c.get("text", ""))
    c["updated"] = _now()
    save(b)
    return b


def write_pages(title: str, pages: list) -> dict:
    """Picture books: save every page at once, a list of {text, picture}."""
    b = load(title)
    if isinstance(pages, str):
        pages = json.loads(pages)
    if len(pages) != len(b.get("outline", [])):
        raise ValueError(f"Give one entry per page in the outline ({len(b.get('outline', []))}).")
    for i, p in enumerate(pages, 1):
        if isinstance(p, str):
            p = {"text": p}
        write(title, i, p.get("text", ""), p.get("notes"), picture=p.get("picture"))
    return load(title)


def chapter_rows(b: dict) -> list[dict]:
    rows = []
    for i, o in enumerate(b.get("outline", [])):
        c = b["chapters"][i] if i < len(b.get("chapters", [])) else {}
        rows.append({"n": i + 1, "title": c.get("title") or o["title"], "summary": o.get("summary", ""),
                     "words": c.get("words", 0), "text": c.get("text", ""), "notes": c.get("notes", ""),
                     "picture": c.get("picture") or o.get("picture", ""), "image": c.get("image", "")})
    return rows


def _tail(text: str, n: int) -> str:
    w = (text or "").split()
    return ("…" + " ".join(w[-n:])) if len(w) > n else " ".join(w)


def context(title: str, chapter: int | None = None, budget: int = 22_000) -> str:
    """What Jarvis needs to write chapter N, kept under `budget` characters."""
    b = load(title)
    if b["kind"] == "puzzle":
        return describe(title)
    rows = chapter_rows(b)
    if chapter is None:
        chapter = next((r["n"] for r in rows if r["words"] < max(20, b["target_words"] * 0.7)), len(rows) or 1)
    if not rows:
        return "No outline yet: write the plan and outline with book_plan first."
    chapter = max(1, min(int(chapter), len(rows)))
    cur = rows[chapter - 1]
    kind = KINDS[b["kind"]]["label"]
    unit = "page" if b["kind"] == "picture" else "chapter"
    parts = [f"BOOK: {b['title']} ({kind}{', for ' + b['audience'] if b['audience'] else ''})"]
    if b.get("brief"):
        parts.append("BRIEF: " + b["brief"][:1500])
    if b.get("characters"):
        parts.append("CHARACTERS:\n" + "\n".join(f"- {k}: {v}" for k, v in b["characters"].items()))
    plan = b.get("plan", "")
    near = []
    for r in rows:
        if abs(r["n"] - chapter) <= 1:
            near.append(f"{r['n']}. {r['title']} — {r['summary']}" + ("  ← WRITE THIS" if r["n"] == chapter else ""))
        else:
            near.append(f"{r['n']}. {r['title']}")
    notes = [f"{r['n']}: {r['notes']}" for r in rows[:chapter - 1] if r["notes"]]
    prev = rows[chapter - 2]["text"] if chapter > 1 else ""
    # trim the biggest pieces until it fits
    plan_cap, notes_n, prev_n = 6000, 12, 350
    while True:
        body = list(parts)
        if plan:
            body.append("PLAN:\n" + (plan if len(plan) <= plan_cap else plan[:plan_cap] + " …"))
        body.append("OUTLINE:\n" + "\n".join(near))
        if notes:
            body.append("CONTINUITY NOTES SO FAR:\n" + "\n".join(notes[-notes_n:]))
        if prev:
            body.append(f"END OF {unit.upper()} {chapter - 1}:\n" + _tail(prev, prev_n))
        if cur["text"]:
            body.append(f"ALREADY WRITTEN IN {unit.upper()} {chapter} ({cur['words']} words), continue from:\n"
                        + _tail(cur["text"], 250))
        out = "\n\n".join(body)
        if len(out) <= budget or (plan_cap <= 1500 and notes_n <= 4 and prev_n <= 120):
            break
        plan_cap, notes_n, prev_n = max(1500, plan_cap - 1500), max(4, notes_n - 3), max(120, prev_n - 80)
    if not b.get("approved"):
        return out + ("\n\nWAIT: the outline isn't approved yet. Show it to the user and ask for their OK or "
                      "changes; book_write won't save until it's approved.")
    if b["kind"] == "picture":
        task = (f"TASK: write page {chapter}: up to {b['target_words']} words of read-aloud text, then save with "
                f"book_write(title, chapter={chapter}, text=..., picture=one sentence describing the picture "
                "using the characters' exact names; no words in the picture).")
    else:
        left = max(0, b["target_words"] - cur["words"])
        if cur["text"] and left < b["target_words"] * 0.1:
            return out + (f"\n\nNOTE: {unit} {chapter} already has {cur['words']} words (target {b['target_words']}). "
                          "Rewrite it only if the user asked; book_write without append replaces it.")
        task = (f"TASK: write {unit} {chapter} ({'about ' + str(left) + ' more words' if cur['text'] else 'about ' + str(b['target_words']) + ' words'}). "
                "Plain text, blank line between paragraphs, no chapter heading. Then save with "
                f"book_write(title, chapter={chapter}, text=..., notes=one or two lines of facts later chapters "
                "must keep: names, places, what changed, open threads). Long chapters: save in parts with append=True.")
    return out + "\n\n" + task


def describe(title: str) -> str:
    b = load(title)
    trim_w, trim_h = TRIMS[b["trim"]]
    lines = [f"'{b['title']}' — {KINDS[b['kind']]['label']}, {b['trim']} in, {b['paper']} paper. "
             f"Status: {STATUS_TEXT.get(b['status'], b['status'])}."]
    if b["kind"] == "puzzle":
        from collections import Counter
        cnt = Counter(p["type"] for p in b.get("puzzles", []))
        lines.append("Puzzles: " + (", ".join(f"{v} {k}" for k, v in cnt.items()) or "none yet"))
        for i, p in enumerate(b.get("puzzles", []), 1):
            if p["type"] in ("logic", "whodunit"):
                lines.append(f"  {i}. {p['title']} ({p['type']}, {p['difficulty']})"
                             + (" — has story" if p.get("story") else " — no story yet"))
    else:
        total = sum(c.get("words", 0) for c in b.get("chapters", []))
        lines.append(f"Plan: {'yes' if b.get('plan') else 'not yet'}. Outline: {len(b.get('outline', []))} "
                     f"{'pages' if b['kind'] == 'picture' else 'chapters'}"
                     f"{' (approved)' if b.get('approved') else ' (not approved)' if b.get('outline') else ''}. "
                     f"Written: {total} words.")
        for r in chapter_rows(b):
            extra = f", picture: {r['picture'][:60]}" if b["kind"] == "picture" and r["picture"] else ""
            lines.append(f"  {r['n']}. {r['title']} — {r['words']} words{extra}")
    outs = outputs(b)
    if outs:
        lines.append("Files: " + "; ".join(str(p) for p in outs))
    lines.append(f"Folder: {book_dir(b['title'])}")
    return "\n".join(lines)


def outputs(b: dict) -> list[Path]:
    d = book_dir(b["title"])
    found = []
    for pat in ("*.epub", "*.pdf", "*.md", "kdp/*.pdf", "kdp/*.md", "kdp/*.jpg"):
        found += sorted(d.glob(pat))
    return [p for p in found if not p.name.endswith(".part.pdf")]


def list_books() -> list[dict]:
    out = []
    if not BOOKS_OUT.exists():
        return out
    for p in BOOKS_OUT.glob("*/project.json"):
        try:
            b = json.loads(p.read_text())
        except ValueError:
            continue
        out.append({"title": b["title"], "kind": b["kind"], "label": KINDS[b["kind"]]["label"],
                    "status": b.get("status", "planning"), "status_text": STATUS_TEXT.get(b.get("status"), ""),
                    "trim": b["trim"], "updated": b.get("updated", ""),
                    "chapters": len(b.get("outline", [])), "puzzles": len(b.get("puzzles", [])),
                    "words": sum(c.get("words", 0) for c in b.get("chapters", [])),
                    "folder": str(p.parent)})
    out.sort(key=lambda x: x["updated"], reverse=True)
    return out


def set_listing(title: str, description: str | None = None, keywords=None, categories=None,
                subtitle: str | None = None, back_text: str | None = None) -> tuple[dict, list[str]]:
    from .kdp import clean_listing
    b = load(title)
    listing, notes = clean_listing(dict(b.get("listing", {})), description, keywords, categories, subtitle, back_text)
    b["listing"] = listing
    save(b)
    return b, notes


MAX_PUZZLES = 300


def add_puzzles(title: str, kind: str, count: int = 1, difficulty: str = "medium", words=None,
                names=None, categories=None, suspects=None, weapons=None, rooms=None, titles=None,
                story_for: int | None = None, story: str | None = None, remove: int | None = None,
                clear: bool = False) -> tuple[dict, list[dict]]:
    from . import puzzles as P
    b = load(title)
    if b["kind"] != "puzzle":
        raise ValueError("This isn't a puzzle book (book_new kind='puzzle').")
    if clear:
        b["puzzles"] = []
    if remove:
        if not 1 <= remove <= len(b["puzzles"]):
            raise ValueError(f"remove: 1 to {len(b['puzzles'])}")
        b["puzzles"].pop(remove - 1)
    if story_for:
        if not 1 <= story_for <= len(b["puzzles"]):
            raise ValueError(f"story_for: puzzle 1 to {len(b['puzzles'])}")
        b["puzzles"][story_for - 1]["story"] = (story or "").replace("\\n", "\n").strip()[:3000]
    made = []
    if kind and not story_for and not remove:
        count = max(1, min(int(count or 1), MAX_PUZZLES - len(b["puzzles"])))
        rng = random.Random(b["seed"] + len(b["puzzles"]) * 7919)
        if isinstance(words, str):
            words = [w for w in re.split(r"[,;\n]+" if re.search(r"[,;\n]", words) else r"\s+", words) if w.strip()]
        lists = None
        if words and isinstance(words[0], (list, tuple)):
            lists = [list(x) for x in words]
        if isinstance(titles, str):
            titles = [t.strip() for t in titles.split(",")]
        for i in range(count):
            kw = {"names": names, "categories": categories, "suspects": suspects, "weapons": weapons,
                  "rooms": rooms, "title": (titles[i] if titles and i < len(titles) else "")}
            if lists is not None:
                kw["words"] = lists[i % len(lists)]
            elif words:
                kw["words"] = words if len(words) <= 18 else rng.sample(words, rng.randint(12, 18))
            p = P.make(kind, difficulty, rng, **kw)
            n = len(b["puzzles"]) + 1
            if not kw["title"]:
                base = {"wordsearch": "Word search", "sudoku": "Sudoku", "logic": "Logic puzzle", "whodunit": "Case"}[p["type"]]
                p["title"] = f"{base} {sum(1 for q in b['puzzles'] if q['type'] == p['type']) + 1}"
            b["puzzles"].append(p)
            made.append({"n": n, **p})
    save(b)
    return b, made
