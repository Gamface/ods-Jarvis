"""~/Studio: the studio's system of record (pipeline, research, community, reports).

Hermes runs in Docker and can't see the host's files, so it reaches the workspace
through these functions. Paths are confined to the workspace, and nothing is ever
deleted: rejected items move to pipeline/rejected.
"""

import datetime
import os
import re
import shutil
from pathlib import Path

ROOT = Path(os.environ.get("JARVIS_STUDIO_HOME", "~/Studio")).expanduser()
STAGES = ["1-ideas", "2-scripts", "3-production", "4-review", "5-published", "rejected"]
TEXT_EXT = {".md", ".yaml", ".yml", ".txt", ".csv", ".json", ".srt", ".lrc"}
MAX_READ = 200_000


def _safe(rel: str) -> Path:
    p = (ROOT / (rel or "").lstrip("/")).resolve()
    if not p.is_relative_to(ROOT.resolve()):
        raise PermissionError("Path is outside ~/Studio")
    return p


def tree(rel: str = "", depth: int = 3) -> str:
    base = _safe(rel)
    if not base.exists():
        raise FileNotFoundError(rel)
    lines = [f"~/Studio/{rel}".rstrip("/") + "/"]

    def walk(d: Path, level: int):
        if level > depth:
            return
        for c in sorted(d.iterdir(), key=lambda x: (x.is_file(), x.name)):
            if c.name.startswith("."):
                continue
            lines.append("  " * level + c.name + ("/" if c.is_dir() else ""))
            if c.is_dir():
                walk(c, level + 1)
    walk(base, 1)
    return "\n".join(lines[:400])


def status() -> str:
    """Counts per pipeline stage plus what's waiting on the user."""
    rows = []
    for st in STAGES:
        d = ROOT / "pipeline" / st
        items = sorted(x.name for x in d.iterdir() if x.is_dir()) if d.exists() else []
        rows.append(f"{st:13} {len(items):3}  {', '.join(items[:8])}")
    drafts = ROOT / "community" / "drafts"
    n_drafts = len(list(drafts.glob("*"))) if drafts.exists() else 0
    waiting = (len(list((ROOT / "pipeline" / "2-scripts").glob("*/")))
               + len(list((ROOT / "pipeline" / "4-review").glob("*/"))) + n_drafts)
    return "\n".join(rows + [f"drafts        {n_drafts:3}", f"waiting on you {waiting}"])


def read(rel: str) -> str:
    p = _safe(rel)
    if p.is_dir():
        return tree(rel, 1)
    if p.suffix.lower() not in TEXT_EXT:
        return f"{rel}: binary file, {p.stat().st_size} bytes"
    return p.read_text(errors="replace")[:MAX_READ]


def write(rel: str, content: str, append: bool = False) -> str:
    p = _safe(rel)
    if p.suffix.lower() not in TEXT_EXT:
        raise ValueError(f"Only text files ({', '.join(sorted(TEXT_EXT))}) can be written")
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() and not append:  # keep the previous version
        bak = ROOT / "logs" / "versions" / f"{p.relative_to(ROOT.resolve())}.{datetime.datetime.now():%Y%m%d-%H%M%S}"
        bak.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, bak)
    with p.open("a" if append else "w") as f:
        f.write(content)
    return f"{'Appended to' if append else 'Wrote'} ~/Studio/{p.relative_to(ROOT.resolve())}"


def new_item(title: str, stage: str = "1-ideas") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "item"
    d = ROOT / "pipeline" / stage / slug
    d.mkdir(parents=True, exist_ok=False)
    (d / "brief.yaml").write_text(
        f'slug: {slug}\ntitle: "{title}"\ncreated: {datetime.date.today()}\nstatus: idea\n'
        f"format: null\nsong: null\nevidence: null\nhook: null\nplatforms: []\nstyle: {{}}\n"
        f"renders: []\nreview: null\nlinks: {{}}\nhistory:\n"
        f"  - {{at: {datetime.datetime.now():%Y-%m-%dT%H:%M}, note: created}}\n")
    return f"Created pipeline/{stage}/{slug}/brief.yaml"


def move(slug: str, stage: str, note: str = "") -> str:
    """Move a pipeline item to another stage (never deletes)."""
    if stage not in STAGES:
        raise ValueError(f"stage must be one of {', '.join(STAGES)}")
    hits = [d for st in STAGES for d in [ROOT / "pipeline" / st / slug] if d.is_dir()]
    if not hits:
        raise FileNotFoundError(f"No pipeline item '{slug}'")
    dest = ROOT / "pipeline" / stage / slug
    if hits[0] != dest:
        shutil.move(str(hits[0]), dest)
    brief = dest / "brief.yaml"
    if brief.exists():
        with brief.open("a") as f:
            f.write(f"  - {{at: {datetime.datetime.now():%Y-%m-%dT%H:%M}, moved_to: {stage}"
                    + (f", note: \"{note}\"" if note else "") + "}\n")
    return f"Moved {slug} to {stage}"
