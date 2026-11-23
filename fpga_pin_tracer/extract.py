"""Low-level PDF primitives: visible/hidden text, line segments, rectangles, symbol graphics."""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf

AXIS_TOL = 0.3      # a segment is axis-aligned when |dx| or |dy| is below this (pt)
MIN_SEG = 0.8       # ignore segments shorter than this (pt)


@dataclass
class Text:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    ox: float           # baseline origin
    oy: float
    dx: float           # writing direction (1,0) horizontal, (0,-1)/(0,1) vertical
    dy: float
    size: float
    hidden: bool
    used: bool = False

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def horizontal(self) -> bool:
        return abs(self.dx) >= abs(self.dy)


@dataclass
class Seg:
    x0: float
    y0: float
    x1: float
    y1: float
    width: float
    idx: int = -1

    @property
    def horizontal(self) -> bool:
        return abs(self.y1 - self.y0) <= AXIS_TOL

    @property
    def length(self) -> float:
        return abs(self.x1 - self.x0) + abs(self.y1 - self.y0)

    def ends(self) -> tuple[tuple[float, float], tuple[float, float]]:
        return (self.x0, self.y0), (self.x1, self.y1)


@dataclass
class Box:
    x0: float
    y0: float
    x1: float
    y1: float
    filled: bool = False

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    def contains(self, x: float, y: float, tol: float = 0.0) -> bool:
        return self.x0 - tol <= x <= self.x1 + tol and self.y0 - tol <= y <= self.y1 + tol

    def dist(self, x: float, y: float) -> float:
        dx = max(self.x0 - x, 0.0, x - self.x1)
        dy = max(self.y0 - y, 0.0, y - self.y1)
        return (dx * dx + dy * dy) ** 0.5


@dataclass
class PagePrims:
    number: int                 # 1-based
    width: float
    height: float
    texts: list[Text]
    segs: list[Seg]             # wire-like strokes (axis aligned, from open paths)
    rects: list[Box]            # rectangles (symbol bodies, frames, off-page markers)
    graphics: list[Box]         # bboxes of symbol drawings (zigzags, arrows, curves)
    text_size: float            # median visible text size, drives all tolerances
    cadence: bool = False       # Cadence/Allegro drawing style detected on this page
    ghost_text: bool = False    # letters drawn as strokes, real text in an invisible layer


def _norm(x0, y0, x1, y1):
    if (x1, y1) < (x0, y0):
        x0, y0, x1, y1 = x1, y1, x0, y0
    return x0, y0, x1, y1


def _split_span(chars: list[dict], ddx: float, ddy: float, size: float) -> list[list[dict]]:
    """Split a span where the baseline jumps or a gap wider than a space opens.

    PDF text extraction happily glues a pin number to the pin name next to it ("AE12IO_L1P_T0");
    geometrically they are separate labels.
    """
    out: list[list[dict]] = []
    cur: list[dict] = []
    horizontal = abs(ddx) >= abs(ddy)
    for c in chars:
        if cur:
            p = cur[-1]
            if horizontal:
                jump = abs(c["origin"][1] - p["origin"][1]) > 0.25 * size
                gap = c["bbox"][0] - p["bbox"][2]
            else:
                jump = abs(c["origin"][0] - p["origin"][0]) > 0.25 * size
                gap = (c["bbox"][1] - p["bbox"][3]) if ddy > 0 else (p["bbox"][1] - c["bbox"][3])
            if jump or gap > 0.6 * size:
                out.append(cur)
                cur = []
        cur.append(c)
    if cur:
        out.append(cur)
    return out


def _rects_from_segs(segs: list[Seg]) -> tuple[list[Box], set[int]]:
    """Find rectangles drawn as four separate strokes (Cadence/Allegro symbol bodies).

    Collinear touching strokes are merged first (an edge may be broken where pins meet it), then
    a rectangle is a top edge whose two ends start vertical edges of equal length that are closed
    by a bottom edge. Returns the boxes and the indices of the strokes that form their edges.
    """
    tol = 0.3

    def merge(items, horizontal):
        # items: (fixed, a, b, idx) with a <= b; merge touching/overlapping runs on one line
        items.sort()
        out = []    # (fixed, a, b, [idx])
        for f, a, b, i in items:
            if out and abs(out[-1][0] - f) <= tol and a <= out[-1][2] + tol:
                o = out[-1]
                out[-1] = (o[0], o[1], max(o[2], b), o[3] + [i])
            else:
                out.append((f, a, b, [i]))
        return out

    hs = merge([(s.y0, s.x0, s.x1, i) for i, s in enumerate(segs) if s.horizontal], True)
    vs = merge([(s.x0, s.y0, s.y1, i) for i, s in enumerate(segs) if not s.horizontal], False)

    def key(x, y):
        return (round(x), round(y))

    v_by_top: dict[tuple[int, int], list[int]] = {}
    for k, (x, a, b, _) in enumerate(vs):
        v_by_top.setdefault(key(x, a), []).append(k)
    h_by_left: dict[tuple[int, int], list[int]] = {}
    for k, (y, a, b, _) in enumerate(hs):
        h_by_left.setdefault(key(a, y), []).append(k)

    def find(index, x, y):
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for k in index.get((round(x) + dx, round(y) + dy), ()):
                    yield k

    boxes: list[Box] = []
    used: set[int] = set()
    for y, x0, x1, hidx in hs:
        if x1 - x0 < 3:
            continue
        lefts = [k for k in find(v_by_top, x0, y) if abs(vs[k][0] - x0) <= tol and abs(vs[k][1] - y) <= tol]
        rights = [k for k in find(v_by_top, x1, y) if abs(vs[k][0] - x1) <= tol and abs(vs[k][1] - y) <= tol]
        for lk in lefts:
            for rk in rights:
                yb = vs[lk][2]
                if abs(vs[rk][2] - yb) > tol or yb - y < 3:
                    continue
                bottoms = [k for k in find(h_by_left, x0, yb)
                           if abs(hs[k][0] - yb) <= tol and abs(hs[k][1] - x0) <= tol
                           and abs(hs[k][2] - x1) <= tol]
                if not bottoms:
                    continue
                boxes.append(Box(x0, y, x1, yb))
                used.update(hidx, vs[lk][3], vs[rk][3], hs[bottoms[0]][3])
    return boxes, used


def extract_page(page: pymupdf.Page) -> PagePrims:
    texts: list[Text] = []
    d = page.get_text("rawdict", flags=pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES)
    for block in d["blocks"]:
        for line in block.get("lines", []):
            ddx, ddy = line["dir"]
            for span in line["spans"]:
                hidden = span.get("alpha", 255) == 0
                for chars in _split_span(span["chars"], ddx, ddy, span["size"]):
                    t = "".join(c["c"] for c in chars).strip()
                    if not t:
                        continue
                    x0 = min(c["bbox"][0] for c in chars)
                    y0 = min(c["bbox"][1] for c in chars)
                    x1 = max(c["bbox"][2] for c in chars)
                    y1 = max(c["bbox"][3] for c in chars)
                    o = chars[0]["origin"]
                    texts.append(Text(t, x0, y0, x1, y1, o[0], o[1], ddx, ddy, span["size"], hidden))

    segs: list[Seg] = []
    rects: list[Box] = []
    graphics: list[Box] = []
    drawings = page.get_drawings()
    # Cadence/Allegro style: every wire is a closed 2-point path ("m l h S") and symbol bodies are
    # four loose strokes. Other exporters use closed paths only for real shapes (capacitor plates,
    # pin graphics), so the Cadence handling is switched on per page only when it is the norm.
    line_paths = [dr for dr in drawings if dr["items"] and all(it[0] == "l" for it in dr["items"])]

    def _retraced(dr) -> bool:     # "m A l B h": reported as A->B plus B->A (or closePath set)
        its = dr["items"]
        if len(its) == 1:
            return bool(dr.get("closePath"))
        return (len(its) == 2 and abs(its[0][1].x - its[1][2].x) < 0.01 and abs(its[0][1].y - its[1][2].y) < 0.01
                and abs(its[0][2].x - its[1][1].x) < 0.01 and abs(its[0][2].y - its[1][1].y) < 0.01)

    degenerate = sum(1 for dr in line_paths if _retraced(dr))
    cadence = bool(line_paths) and degenerate > 0.5 * len(line_paths)
    for dr in drawings:
        items = dr["items"]
        stroked = "s" in (dr.get("type") or "")
        filled = "f" in (dr.get("type") or "")
        width = dr.get("width") or 0.0
        lines = [it for it in items if it[0] == "l"]
        others = [it for it in items if it[0] != "l"]
        for it in others:
            if it[0] == "re":
                r = it[1]
                rects.append(Box(r.x0, r.y0, r.x1, r.y1, filled))
            elif it[0] == "qu":
                q = it[1].rect
                rects.append(Box(q.x0, q.y0, q.x1, q.y1, filled))
        if not lines:
            if others and not any(it[0] in ("re", "qu") for it in others):
                r = dr["rect"]
                graphics.append(Box(r.x0, r.y0, r.x1, r.y1, filled))
            continue
        pts = [(it[1].x, it[1].y, it[2].x, it[2].y) for it in lines]
        close_path = bool(dr.get("closePath"))
        if cadence and (close_path or _retraced(dr)):
            # Cadence/Allegro writes every wire as "m l h S": a closed path whose closing
            # segment just retraces the line. Drop retraced/zero-length segments; if that
            # removed anything the path encloses no area and is really an open stroke.
            uniq: list[tuple[float, float, float, float]] = []
            seen: set[tuple[float, float, float, float]] = set()
            for p in pts:
                key = tuple(round(v, 2) for v in _norm(*p))
                if key in seen or (abs(p[0] - p[2]) + abs(p[1] - p[3])) < 1e-3:
                    continue
                seen.add(key)
                uniq.append(p)
            if len(uniq) < len(pts):
                pts = uniq
                close_path = False
            if not pts:
                continue
        diagonal = any(abs(a - c) > AXIS_TOL and abs(b - e) > AXIS_TOL for a, b, c, e in pts)
        first, last = pts[0][:2], pts[-1][2:]
        closed = close_path or (
            len(pts) >= 3 and abs(first[0] - last[0]) < 0.05 and abs(first[1] - last[1]) < 0.05)
        if diagonal or closed or others or (filled and not stroked):
            r = dr["rect"]
            if closed and not diagonal and len(pts) == 4 and not others:
                rects.append(Box(r.x0, r.y0, r.x1, r.y1, filled))
            else:
                graphics.append(Box(r.x0, r.y0, r.x1, r.y1, filled))
            continue
        for a, b, c, e in pts:
            x0, y0, x1, y1 = _norm(a, b, c, e)
            if abs(x1 - x0) <= AXIS_TOL:
                x1 = x0 = (x0 + x1) / 2
            elif abs(y1 - y0) <= AXIS_TOL:
                y1 = y0 = (y0 + y1) / 2
            if abs(x1 - x0) + abs(y1 - y0) < MIN_SEG:
                continue
            segs.append(Seg(x0, y0, x1, y1, width))

    # some exporters (Cadence) stroke every line twice; identical strokes would become twin pins
    if cadence:
        seen_segs: set[tuple[float, float, float, float]] = set()
        uniq_segs: list[Seg] = []
        for s in segs:
            k = (round(s.x0, 1), round(s.y0, 1), round(s.x1, 1), round(s.y1, 1))
            if k not in seen_segs:
                seen_segs.add(k)
                uniq_segs.append(s)
        segs = uniq_segs

    # "Ghost text" (e.g. SCH2PDF): every letter is drawn with vector strokes and the real text sits
    # on top as an invisible search layer. Use that layer as the visible text and drop the glyph
    # strokes, which would otherwise pass for wires and symbol graphics.
    visible_n = sum(len(t.text) for t in texts if not t.hidden)
    hidden_n = sum(len(t.text) for t in texts if t.hidden)
    ghost = hidden_n > 50 and visible_n < 0.05 * hidden_n
    if ghost:
        for t in texts:
            t.hidden = False
        cell = 20.0
        boxes: dict[tuple[int, int], list[Text]] = {}
        for t in texts:
            for gx in range(int(t.x0 // cell), int(t.x1 // cell) + 1):
                for gy in range(int(t.y0 // cell), int(t.y1 // cell) + 1):
                    boxes.setdefault((gx, gy), []).append(t)

        def in_text(x0, y0, x1, y1) -> bool:
            pad = 0.6
            for t in boxes.get((int(x0 // cell), int(y0 // cell)), ()):
                if t.x0 - pad <= x0 and x1 <= t.x1 + pad and t.y0 - pad <= y0 and y1 <= t.y1 + pad:
                    return True
            return False

        segs = [s for s in segs if not in_text(s.x0, s.y0, s.x1, s.y1)]
        graphics = [g for g in graphics if not in_text(g.x0, g.y0, g.x1, g.y1)]
        rects = [r for r in rects if not in_text(r.x0, r.y0, r.x1, r.y1)]

    seg_boxes, edge_idx = _rects_from_segs(segs) if cadence else ([], set())
    if seg_boxes:
        rects.extend(seg_boxes)
        segs = [s for i, s in enumerate(segs) if i not in edge_idx]

    sizes = sorted(t.size for t in texts if not t.hidden)
    text_size = sizes[len(sizes) // 2] if sizes else 6.0
    for i, s in enumerate(segs):
        s.idx = i
    return PagePrims(page.number + 1, page.rect.width, page.rect.height, texts, segs, rects,
                     graphics, text_size, cadence, ghost)


def extract_pdf(path: str) -> list[PagePrims]:
    with pymupdf.open(path) as doc:
        return [extract_page(p) for p in doc]
