"""Animated caption looks for explainers, written as ASS subtitles from word timings.

highlight  a short line at the bottom; each word lights up as it is spoken
pop        one to three big words at a time in the lower middle, popping in
box        sentence chunks on a dark rounded box at the bottom
subtle     small white text with a soft shadow at the bottom
"""

from pathlib import Path

from .common import FONT

Word = tuple[float, float, str]  # start, end, text (absolute seconds)

ACCENT = "&H0040C8F5"   # warm yellow (ASS is &HAABBGGRR)
WHITE = "&H00FFFFFF"
BLACK = "&H00000000"


def words_for(script: str, timings: list[dict] | None, start: float, dur: float) -> list[Word]:
    """Script words with times. Uses the voice's word timings when they line up with the
    script; otherwise spreads the words over the clip by length."""
    words = script.split()
    if timings:
        spoken = []
        for t in timings:
            w = str(t.get("word", "")).strip()
            if not w:
                continue
            if all(not c.isalnum() for c in w):  # punctuation token: stretch the previous word
                if spoken:
                    spoken[-1][1] = float(t["end_time"])
                continue
            spoken.append([float(t["start_time"]), float(t["end_time"]), w])
        if len(spoken) == len(words):
            return [(start + a, start + b, w) for (a, b, _), w in zip(spoken, words)]
        if spoken and abs(len(spoken) - len(words)) > 0:  # numbers read out etc.: show what was said
            return [(start + a, start + b, w) for a, b, w in spoken]
    total = sum(len(w) + 1 for w in words) or 1
    out, t = [], start
    for w in words:
        d = dur * (len(w) + 1) / total
        out.append((t, t + d, w))
        t += d
    return out


def _ts(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _esc(s: str) -> str:
    return s.replace("\\", "").replace("{", "(").replace("}", ")")


def _groups(words: list[Word], max_chars: int, max_words: int = 99) -> list[list[Word]]:
    groups, cur = [], []
    for w in words:
        line = " ".join(x[2] for x in cur + [w])
        gap = w[0] - cur[-1][1] if cur else 0
        if cur and (len(line) > max_chars or len(cur) >= max_words or gap > 0.6
                    or cur[-1][2].endswith((".", "?", "!"))):
            groups.append(cur)
            cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def write(words: list[Word], dest: Path, size: tuple[int, int], style: str) -> Path | None:
    if style == "none" or not words:
        return None
    w, h = size
    base = min(w, h)
    portrait = h > w
    if style == "highlight":
        fs, outline, shadow, margin = base * 0.062, base * 0.006, 0, h * (0.16 if portrait else 0.08)
        st = f"Style: Main,{FONT},{fs:.0f},{WHITE},{WHITE},{BLACK},&H80000000,-1,0,0,0,100,100,0,0,1,{outline:.1f},{shadow},2,60,60,{margin:.0f},1"
    elif style == "pop":
        fs, outline, margin = base * (0.105 if portrait else 0.085), base * 0.008, h * (0.30 if portrait else 0.18)
        st = f"Style: Main,{FONT},{fs:.0f},{WHITE},{ACCENT},{BLACK},&H80000000,-1,0,0,0,100,100,1,0,1,{outline:.1f},2,2,60,60,{margin:.0f},1"
    elif style == "box":
        fs, margin = base * 0.05, h * (0.14 if portrait else 0.07)
        st = f"Style: Main,{FONT},{fs:.0f},{WHITE},{WHITE},&HA0141414,&HA0141414,0,0,0,0,100,100,0,0,3,{base * 0.016:.1f},0,2,80,80,{margin:.0f},1"
    else:  # subtle
        fs, margin = base * 0.044, h * (0.12 if portrait else 0.06)
        st = f"Style: Main,{FONT},{fs:.0f},{WHITE},{WHITE},&H90000000,&H90000000,0,0,0,0,100,100,0,0,1,{base * 0.0025:.1f},{base * 0.003:.1f},2,80,80,{margin:.0f},1"

    head = ("[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\nScaledBorderAndShadow: yes\n"
            f"PlayResX: {w}\nPlayResY: {h}\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
            "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n" + st +
            "\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    lines = []
    chars = int(w / (fs * 0.55))
    if style == "highlight":
        # the whole line stays up; the word being spoken is coloured and slightly larger
        for g in _groups(words, min(chars, 26 if portrait else 34)):
            for i, (a, b, _) in enumerate(g):
                s = g[0][0] if i == 0 else a
                e = g[i + 1][0] if i + 1 < len(g) else b + 0.15
                text = " ".join(
                    (f"{{\\c&H40C8F5&\\fscx108\\fscy108}}{_esc(t)}{{\\c&HFFFFFF&\\fscx100\\fscy100}}" if j == i else _esc(t))
                    for j, (_, _, t) in enumerate(g))
                lines.append((s, e, text))
    elif style == "pop":
        for g in _groups(words, 16, 3):
            start, end = g[0][0], g[-1][1] + 0.05
            text = _esc(" ".join(x[2] for x in g)).upper()
            lines.append((start, end, "{\\fscx70\\fscy70\\t(0,90,\\fscx108\\fscy108)\\t(90,160,\\fscx100\\fscy100)}" + text))
    else:
        for g in _groups(words, min(chars, 40 if portrait else 52)):
            start, end = g[0][0], g[-1][1] + 0.2
            lines.append((start, end, "{\\fad(120,120)}" + _esc(" ".join(x[2] for x in g))))
    lines.sort(key=lambda x: x[0])
    out = []
    for i, (s, e, text) in enumerate(lines):  # never show two caption lines at once
        if i + 1 < len(lines):
            e = min(e, lines[i + 1][0])
        if e - s >= 0.02:
            out.append(f"Dialogue: 0,{_ts(s)},{_ts(e)},Main,,0,0,0,,{text}")
    dest.write_text(head + "\n".join(out) + "\n", encoding="utf-8")
    return dest
