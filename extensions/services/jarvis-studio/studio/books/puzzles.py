"""Puzzles made in code, each with exactly one answer.

wordsearch  words hidden in a letter grid (the words come from Jarvis or the user)
sudoku      9x9, cells removed only while the puzzle still has one solution
logic       logic grid: a solver keeps adding true clues until one answer is left, then
            drops every clue that isn't needed
whodunit    a logic grid framed as a case: suspects, weapons, rooms (and motives), plus the
            crime scene facts; solve the grid to name the culprit

The answer is kept in the puzzle's data for the answer key; the tool replies never show it.
"""

import itertools
import random
import string

TYPES = ("wordsearch", "sudoku", "logic", "whodunit")
LEVELS = ("easy", "medium", "hard")

# --- word search ---------------------------------------------------------------
WS_SIZE = {"easy": 12, "medium": 15, "hard": 17}
WS_DIRS = {"easy": [(0, 1), (1, 0)],
           "medium": [(0, 1), (1, 0), (1, 1), (-1, 1)],
           "hard": [(0, 1), (1, 0), (1, 1), (-1, 1), (0, -1), (-1, 0), (-1, -1), (1, -1)]}


def wordsearch(words: list[str], level: str = "medium", rng: random.Random | None = None,
               title: str = "") -> dict:
    rng = rng or random.Random()
    n = WS_SIZE[level]
    clean = []
    for w in words:
        k = "".join(c for c in str(w).upper() if c in string.ascii_uppercase)
        if 3 <= len(k) <= n and k not in clean:
            clean.append(k)
    if len(clean) < 4:
        raise ValueError("A word search needs at least 4 words of 3+ letters (letters A-Z).")
    clean = sorted(clean, key=len, reverse=True)[:24]
    for _attempt in range(40):
        grid = [[""] * n for _ in range(n)]
        placed = []
        for w in clean:
            spots = [(r, c, dr, dc) for dr, dc in WS_DIRS[level] for r in range(n) for c in range(n)
                     if 0 <= r + dr * (len(w) - 1) < n and 0 <= c + dc * (len(w) - 1) < n]
            rng.shuffle(spots)
            for r, c, dr, dc in spots:
                cells = [(r + dr * i, c + dc * i) for i in range(len(w))]
                if all(grid[y][x] in ("", w[i]) for i, (y, x) in enumerate(cells)):
                    for i, (y, x) in enumerate(cells):
                        grid[y][x] = w[i]
                    placed.append({"word": w, "cells": cells})
                    break
        if len(placed) == len(clean):
            break
    else:
        clean = [p["word"] for p in placed]
    for row in grid:
        for x in range(n):
            if not row[x]:
                row[x] = rng.choice(string.ascii_uppercase)
    return {"type": "wordsearch", "difficulty": level, "title": title or "Word search",
            "grid": ["".join(r) for r in grid], "words": sorted(p["word"] for p in placed),
            "solution": placed}


# --- sudoku --------------------------------------------------------------------
SUDOKU_CLUES = {"easy": 40, "medium": 32, "hard": 26}
_PEERS = []
for _i in range(81):
    r, c = divmod(_i, 9)
    br, bc = r // 3 * 3, c // 3 * 3
    _PEERS.append(sorted({r * 9 + k for k in range(9)} | {k * 9 + c for k in range(9)}
                         | {(br + a) * 9 + bc + b for a in range(3) for b in range(3)} - {_i}))


def _solve(cells: list[int], limit: int, rng: random.Random | None = None) -> list[list[int]]:
    """Up to `limit` solutions (0 = empty cell)."""
    sols = []
    cells = list(cells)

    def rec():
        best, best_opts = -1, None
        for i in range(81):
            if cells[i] == 0:
                used = {cells[p] for p in _PEERS[i]}
                opts = [v for v in range(1, 10) if v not in used]
                if not opts:
                    return
                if best_opts is None or len(opts) < len(best_opts):
                    best, best_opts = i, opts
                    if len(opts) == 1:
                        break
        if best < 0:
            sols.append(list(cells))
            return
        if rng:
            rng.shuffle(best_opts)
        for v in best_opts:
            cells[best] = v
            rec()
            if len(sols) >= limit:
                return
        cells[best] = 0

    rec()
    return sols


def sudoku(level: str = "medium", rng: random.Random | None = None, title: str = "") -> dict:
    rng = rng or random.Random()
    full = _solve([0] * 81, 1, rng)[0]
    puzzle = list(full)
    order = list(range(81))
    rng.shuffle(order)
    target = SUDOKU_CLUES[level]
    filled = 81
    for i in order:
        if filled <= target:
            break
        keep = puzzle[i]
        puzzle[i] = 0
        if len(_solve(puzzle, 2)) != 1:
            puzzle[i] = keep
        else:
            filled -= 1
    return {"type": "sudoku", "difficulty": level, "title": title or "Sudoku",
            "grid": "".join(str(v) for v in puzzle), "solution": "".join(str(v) for v in full),
            "clues": filled}


# --- logic grids ---------------------------------------------------------------
FIRST_NAMES = ["Ava", "Ben", "Cora", "Dev", "Ella", "Finn", "Grace", "Hugo", "Iris", "Jack", "Kira",
               "Leo", "Maya", "Noah", "Olive", "Paul", "Quinn", "Rosa", "Sam", "Tara", "Uma", "Vic"]
LOGIC_THEMES = [
    {"name": "Pet", "values": ["cat", "dog", "parrot", "rabbit", "turtle", "hamster"], "verb": "owns the {}"},
    {"name": "Drink", "values": ["tea", "coffee", "cocoa", "lemonade", "juice", "milk"], "verb": "ordered the {}"},
    {"name": "Hobby", "values": ["painting", "chess", "gardening", "baking", "hiking", "fishing"],
     "verb": "loves {}"},
    {"name": "Colour", "values": ["red", "blue", "green", "yellow", "purple", "orange"], "verb": "wore {}"},
    {"name": "City", "values": ["Paris", "Tokyo", "Lima", "Cairo", "Oslo", "Sydney"], "verb": "visited {}"},
    {"name": "Age", "values": [7, 8, 9, 10, 11], "verb": "is {} years old", "more": "older", "less": "younger"},
    {"name": "Time", "values": ["1 pm", "2 pm", "3 pm", "4 pm", "5 pm"], "verb": "arrived at {}",
     "more": "later", "less": "earlier", "ordered": True},
]
SUSPECTS = ["Colonel Ash", "Lady Briar", "Doctor Crane", "Miss Dove", "Professor Elm", "Captain Frost",
            "Madame Gray", "Mr Hart"]
WEAPONS = ["candlestick", "rope", "wrench", "dagger", "poison bottle", "iron poker"]
ROOMS = ["library", "kitchen", "conservatory", "ballroom", "study", "cellar"]
MOTIVES = ["revenge", "money", "jealousy", "a secret", "power", "a stolen letter"]
SIZES = {"easy": (4, 2), "medium": (4, 3), "hard": (5, 2)}  # items, categories besides the first


class _Grid:
    """All candidate answers; each clue is a bitset of the candidates it allows."""

    def __init__(self, n: int, k: int):
        self.n, self.k = n, k
        perms = list(itertools.permutations(range(n)))
        self.cands = [p for p in itertools.product(perms, repeat=k)]

    def value(self, cand, cat: int, item: int) -> int:
        return item if cat == 0 else cand[cat - 1][item]

    def item_with(self, cand, cat: int, val: int) -> int:
        return val if cat == 0 else cand[cat - 1].index(val)

    def mask(self, pred) -> int:
        m = 0
        for i, c in enumerate(self.cands):
            if pred(c):
                m |= 1 << i
        return m


def _clue_makers(g: _Grid, cats: list[dict], sol, rng: random.Random, level: str):
    """Yield (text, predicate) clues that are true for `sol`."""
    n, k = g.n, g.k

    def who(cat, v):
        return cats[0]["values"][v] if cat == 0 else "the one who " + cats[cat]["verb"].format(cats[cat]["values"][v])

    def does(cat, v):
        return "is " + cats[0]["values"][v] if cat == 0 else cats[cat]["verb"].format(cats[cat]["values"][v])

    def cap(s):
        return s[0].upper() + s[1:]

    weights = {"easy": [4, 3, 2, 2], "medium": [2, 3, 3, 3], "hard": [1, 3, 3, 4]}[level]
    while True:
        kind = rng.choices(["pos", "neg", "either", "order"], weights)[0]
        c1, c2 = rng.sample(range(k + 1), 2)
        item = rng.randrange(n)
        v1, v2 = g.value(sol, c1, item), g.value(sol, c2, item)
        if kind == "pos":
            yield (f"{cap(who(c1, v1))} {does(c2, v2)}.",
                   lambda c, c1=c1, v1=v1, c2=c2, v2=v2: g.value(c, c2, g.item_with(c, c1, v1)) == v2)
        elif kind == "neg":
            other = rng.choice([x for x in range(n) if x != item])
            w2 = g.value(sol, c2, other)
            yield (f"{cap(who(c1, v1))} is not {who(c2, w2)}.",
                   lambda c, c1=c1, v1=v1, c2=c2, w2=w2: g.value(c, c2, g.item_with(c, c1, v1)) != w2)
        elif kind == "either":
            other = rng.choice([x for x in range(n) if x != item])
            w2 = g.value(sol, c2, other)
            a, b = rng.sample([v2, w2], 2)
            yield (f"{cap(who(c1, v1))} is either {who(c2, a)} or {who(c2, b)}.",
                   lambda c, c1=c1, v1=v1, c2=c2, a=a, b=b: g.value(c, c2, g.item_with(c, c1, v1)) in (a, b))
        else:
            oc = [i for i in range(1, k + 1) if cats[i].get("more")]
            if not oc:
                continue
            oc = rng.choice(oc)
            c1 = rng.choice([i for i in range(k + 1) if i != oc])
            a_item, b_item = rng.sample(range(n), 2)
            va, vb = g.value(sol, c1, a_item), g.value(sol, c1, b_item)
            if g.value(sol, oc, a_item) < g.value(sol, oc, b_item):
                va, vb = vb, va  # va is the higher one
            yield (f"{cap(who(c1, va))} is {cats[oc]['more']} than {who(c1, vb)}.",
                   lambda c, c1=c1, va=va, vb=vb, oc=oc: g.value(c, oc, g.item_with(c, c1, va))
                   > g.value(c, oc, g.item_with(c, c1, vb)))


def _build_logic(cats: list[dict], level: str, rng: random.Random, fixed: list | None = None) -> dict:
    n, k = len(cats[0]["values"]), len(cats) - 1
    g = _Grid(n, k)
    sol = rng.choice(g.cands)
    full = (1 << len(g.cands)) - 1
    clues, masks = [], []
    cur = full
    for text, pred in fixed or []:
        clues.append(text)
        masks.append(g.mask(lambda c, pred=pred: pred(g, c, sol)))
        cur &= masks[-1]
    maker = _clue_makers(g, cats, sol, rng, level)
    tries = 0
    while cur.bit_count() > 1 and tries < 600:
        tries += 1
        text, pred = next(maker)
        if text in clues:
            continue
        m = g.mask(pred)
        if (cur & m) != cur:  # it rules something out
            clues.append(text)
            masks.append(m)
            cur &= m
    if cur.bit_count() != 1:
        raise RuntimeError("Could not make a puzzle with one answer; try again.")
    # drop clues that aren't needed (never the fixed ones)
    nfixed = len(fixed or [])
    keep = list(range(len(clues)))
    for i in sorted(range(nfixed, len(clues)), key=lambda _: rng.random()):
        rest = full
        for j in keep:
            if j != i:
                rest &= masks[j]
        if rest.bit_count() == 1:
            keep.remove(i)
    fixed_part = [clues[i] for i in keep if i < nfixed]
    free = [clues[i] for i in keep if i >= nfixed]
    rng.shuffle(free)
    table = [[str(cats[c]["values"][g.value(sol, c, i)]) for c in range(k + 1)] for i in range(n)]
    return {"categories": [{"name": c["name"], "values": [str(v) for v in c["values"]]} for c in cats],
            "clues": fixed_part + free, "solution": table}


def _pick_values(cat: dict, n: int, rng: random.Random) -> dict:
    vals = list(cat["values"])
    if cat.get("more"):
        start = rng.randrange(0, len(vals) - n + 1)
        vals = vals[start:start + n]  # keep numbers in order
    else:
        vals = rng.sample(vals, n)
    return dict(cat, values=vals)


def logic(level: str = "medium", rng: random.Random | None = None, title: str = "",
          names: list[str] | None = None, categories: list[dict] | None = None) -> dict:
    rng = rng or random.Random()
    n, k = SIZES[level]
    people = [str(x) for x in (names or [])][:n]
    pool = [x for x in FIRST_NAMES if x not in people]
    people += rng.sample(pool, n - len(people))
    cats = [{"name": "Name", "values": people}]
    chosen = []
    for c in categories or []:
        if isinstance(c, dict) and c.get("values") and c.get("verb") and "{}" in c["verb"] and len(c["values"]) >= n:
            chosen.append(c)
    pool = [t for t in LOGIC_THEMES if t["name"] not in {c["name"] for c in chosen}]
    if not any(c.get("more") for c in chosen) and level != "easy":
        chosen.append(rng.choice([t for t in pool if t.get("more")]))
    pool = [t for t in pool if t["name"] not in {c["name"] for c in chosen} and not t.get("more")]
    chosen += rng.sample(pool, max(0, k - len(chosen)))
    cats += [_pick_values(c, n, rng) for c in chosen[:k]]
    data = _build_logic(cats, level, rng)
    return {"type": "logic", "difficulty": level, "title": title or "Logic puzzle", **data}


def whodunit(level: str = "medium", rng: random.Random | None = None, title: str = "",
             suspects: list[str] | None = None, weapons: list[str] | None = None,
             rooms: list[str] | None = None) -> dict:
    rng = rng or random.Random()
    n = 4 if level != "hard" else 5
    def pick(given, pool):
        given = [str(x) for x in (given or [])][:n]
        return given + rng.sample([x for x in pool if x not in given], n - len(given))
    cats = [{"name": "Suspect", "values": pick(suspects, SUSPECTS)},
            {"name": "Weapon", "values": pick(weapons, WEAPONS), "verb": "had the {}"},
            {"name": "Room", "values": pick(rooms, ROOMS), "verb": "was in the {}"}]
    if level == "medium":
        cats.append({"name": "Motive", "values": rng.sample(MOTIVES, n), "verb": "had {} as a motive"})
    # the crime scene: the culprit's room and weapon (a fixed clue)
    culprit = None

    def scene(g, c, sol):
        nonlocal culprit
        if culprit is None:
            culprit = rng.randrange(n)
        room, weapon = g.value(sol, 2, culprit), g.value(sol, 1, culprit)
        return g.item_with(c, 2, room) == g.item_with(c, 1, weapon)

    data = _build_logic(cats, level, rng, fixed=[("SCENE", scene)])
    sol_rows = data["solution"]
    who = sol_rows[culprit]
    scene_text = f"The crime happened in the {who[2]}, and the weapon was the {who[1]}."
    data["clues"] = [c for c in data["clues"] if c != "SCENE"]
    return {"type": "whodunit", "difficulty": level, "title": title or "The case", **data,
            "scene": scene_text, "culprit": who[0],
            "question": "Who did it? Work out where everyone was and what they had."}


def make(kind: str, level: str = "medium", rng: random.Random | None = None, **kw) -> dict:
    kind = (kind or "").lower().replace(" ", "").replace("-", "")
    kind = {"wordsearches": "wordsearch", "words": "wordsearch", "logicgrid": "logic",
            "mystery": "whodunit", "murdermystery": "whodunit"}.get(kind, kind)
    if kind not in TYPES:
        raise ValueError(f"type must be one of {', '.join(TYPES)}")
    level = (level or "medium").lower()
    if level not in LEVELS:
        raise ValueError("difficulty must be easy, medium or hard")
    if kind == "wordsearch":
        return wordsearch(kw.get("words") or [], level, rng, kw.get("title", ""))
    if kind == "sudoku":
        return sudoku(level, rng, kw.get("title", ""))
    if kind == "logic":
        return logic(level, rng, kw.get("title", ""), kw.get("names"), kw.get("categories"))
    return whodunit(level, rng, kw.get("title", ""), kw.get("suspects"), kw.get("weapons"), kw.get("rooms"))


def public_view(p: dict) -> str:
    """What the tool reply may show (never the answer)."""
    if p["type"] == "wordsearch":
        return f"{p['title']}: {len(p['grid'])}x{len(p['grid'])}, words: {', '.join(p['words'])}"
    if p["type"] == "sudoku":
        return f"{p['title']}: {p['clues']} given numbers"
    cats = "; ".join(f"{c['name']}: {', '.join(c['values'])}" for c in p["categories"])
    extra = f" Scene: {p['scene']}" if p["type"] == "whodunit" else ""
    return f"{p['title']} — {cats}. {len(p['clues'])} clues.{extra}"
