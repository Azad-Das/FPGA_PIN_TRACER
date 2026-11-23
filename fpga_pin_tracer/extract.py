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
    for dr in page.get_drawings():
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
        diagonal = any(abs(a - c) > AXIS_TOL and abs(b - e) > AXIS_TOL for a, b, c, e in pts)
        first, last = pts[0][:2], pts[-1][2:]
        closed = bool(dr.get("closePath")) or (
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

    sizes = sorted(t.size for t in texts if not t.hidden)
    text_size = sizes[len(sizes) // 2] if sizes else 6.0
    for i, s in enumerate(segs):
        s.idx = i
    return PagePrims(page.number + 1, page.rect.width, page.rect.height, texts, segs, rects,
                     graphics, text_size)


def extract_pdf(path: str) -> list[PagePrims]:
    with pymupdf.open(path) as doc:
        return [extract_page(p) for p in doc]
