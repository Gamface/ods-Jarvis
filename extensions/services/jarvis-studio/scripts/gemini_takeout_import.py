#!/usr/bin/env python3
"""Turn a Google Takeout Gemini export into one Markdown file per chat, for Jarvis.

Input: a Takeout zip (or its "My Activity/Gemini Apps/MyActivity.json").
Output (default ~/ods/data/hermes/gemini-history, which Hermes sees as /opt/data/gemini-history):
  chats/<date>_<title>_<id>.md   one file per Gemini conversation, oldest turn first
  INDEX.md                       newest chats first: date, title, turns, file
Re-running replaces chats/ and INDEX.md with the new export (the export holds all history).

Usage: gemini_takeout_import.py <Takeout.zip | MyActivity.json> [output_dir]
Standard library only. Attachments (images, audio) are not copied, only their names.
"""

import html
import json
import re
import shutil
import sys
import zipfile
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path

MEMBER = "Takeout/My Activity/Gemini Apps/MyActivity.json"


class _Text(HTMLParser):
    """Gemini's reply HTML -> readable plain text with light Markdown."""
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "hr", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []

    def handle_starttag(self, tag, attrs):
        if tag in ("h1", "h2", "h3", "h4"):
            self.out.append("\n\n" + "#" * (int(tag[1]) + 2) + " ")
        elif tag == "li":
            self.out.append("\n- ")
        elif tag in ("strong", "b"):
            self.out.append("**")
        elif tag == "code":
            self.out.append("`")
        elif tag == "hr":
            self.out.append("\n\n---\n")
        elif tag in self.BLOCK:
            self.out.append("\n")
        elif tag in ("td", "th"):
            self.out.append(" | ")

    def handle_endtag(self, tag):
        if tag in ("strong", "b"):
            self.out.append("**")
        elif tag == "code":
            self.out.append("`")
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        self.out.append(data)

    def text(self):
        t = "".join(self.out)
        t = re.sub(r"[ \t]+\n", "\n", t)
        return re.sub(r"\n{3,}", "\n\n", t).strip()


def html_to_text(s: str) -> str:
    p = _Text()
    try:
        p.feed(s)
        p.close()
        return p.text()
    except Exception:  # malformed HTML: fall back to stripping tags
        return html.unescape(re.sub(r"<[^>]+>", " ", s)).strip()


def load(src: Path) -> list:
    if src.suffix.lower() == ".zip":
        with zipfile.ZipFile(src) as z:
            with z.open(MEMBER) as fh:
                return json.load(fh)
    return json.loads(src.read_text(encoding="utf-8"))


def slug(text: str, n: int = 50) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")
    return (s[:n].rstrip("-") or "chat")


def prompt_of(entry: dict) -> tuple[str, str]:
    title = entry.get("title") or ""
    m = re.match(r"^(Prompted|Branched|Used|Created|Asked|Searched)\s+(.*)$", title, re.S)
    return (m.group(1), m.group(2).strip()) if m else ("", title.strip())


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    src = Path(sys.argv[1]).expanduser()
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "~/ods/data/hermes/gemini-history").expanduser()
    entries = load(src)

    chats = defaultdict(list)
    for e in entries:
        url = next((d.get("url", "") for d in e.get("details") or [] if "gemini.google.com/app/" in d.get("url", "")), "")
        cid = url.rsplit("/", 1)[-1] if url else "no-chat-" + (e.get("time") or "")[:7]
        chats[cid].append((e, url))

    chats_dir = out / "chats"
    if chats_dir.exists():
        shutil.rmtree(chats_dir)
    chats_dir.mkdir(parents=True)
    out.chmod(0o700)

    index = []
    for cid, items in chats.items():
        items.sort(key=lambda x: x[0].get("time") or "")
        first = prompt_of(items[0][0])[1] or "Untitled"
        start, end = (items[0][0].get("time") or "")[:10], (items[-1][0].get("time") or "")[:10]
        url = next((u for _, u in items if u), "")
        lines = [f"# {first[:120]}", "",
                 f"- Gemini chat: {url or '(no link)'}",
                 f"- Dates: {start}" + (f" to {end}" if end != start else ""),
                 f"- Turns: {len(items)}", ""]
        for e, _ in items:
            verb, prompt = prompt_of(e)
            when = (e.get("time") or "")[:16].replace("T", " ")
            lines.append(f"## {when}" + (f" ({verb.lower()})" if verb and verb != "Prompted" else ""))
            lines.append("")
            lines.append("**You:** " + (prompt or "(no prompt text)"))
            for s in e.get("subtitles") or []:
                if s.get("name"):
                    lines.append(f"_{s['name']}_")
            files = e.get("attachedFiles") or []
            if files:
                lines.append("_Attachments: " + ", ".join(files) + "_")
            reply = "\n\n".join(html_to_text(h.get("html", "")) for h in e.get("safeHtmlItem") or [] if h.get("html"))
            lines += ["", "**Gemini:**", "", reply or "(no reply text in the export)", ""]
        name = f"{start or 'undated'}_{slug(first)}_{cid[-8:]}.md"
        (chats_dir / name).write_text("\n".join(lines) + "\n", encoding="utf-8")
        index.append((end or start, start, first, len(items), name))

    index.sort(reverse=True)
    rows = ["# Gemini chat history (from Google Takeout)", "",
            f"{len(index)} chats, {len(entries)} turns. Newest first. Files are in chats/.", "",
            "| Last active | Started | Title | Turns | File |", "|---|---|---|---|---|"]
    for last, start, title, n, name in index:
        t = title.replace("|", "/").replace("\n", " ")[:90]
        rows.append(f"| {last} | {start} | {t} | {n} | {name} |")
    (out / "INDEX.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(f"{len(index)} chats, {len(entries)} turns -> {out}")


if __name__ == "__main__":
    main()
