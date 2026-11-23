"""Turn PDF primitives into a board netlist (components, pins, nets).

The approach is purely geometric so it works for any EDA tool that exports vector PDFs:

1. Symbol bodies are rectangles. A pin is a short stroke perpendicular to a body edge with one
   end on the edge; its number is the small text next to the stroke and its name is the text
   inside the body on the same line.
2. Wires are axis-aligned strokes. Strokes that share an end point, or where one ends on the
   other (a T junction), are the same net. Plain crossings are not connected.
3. A net label is text sitting on a wire. Equal labels anywhere in the PDF are the same net.
4. Two-pin parts drawn without a body rectangle (resistors, ferrites, capacitors) are found
   from their reference designator and the wire ends that touch the symbol drawing next to it.

Altium PDFs additionally carry invisible marker text ("PI<ref>0<pin>" at every pin and
"CO<ref>" at every component); when present those markers fix the pin assignment exactly.
"""

from __future__ import annotations

import re
from collections import defaultdict

from .extract import Box, PagePrims, Seg, Text, extract_pdf
from .model import Board, Component, Pin

REFDES_RE = re.compile(r"^(?:[A-Z]{1,4})\d{1,4}[A-Z]?$")
COMMON_PREFIX_RE = re.compile(r"^(U|IC|J|P|CN|CON|X|XU|Q|R|RN|C|L|D|FB|SW|S|T|K|Y|JP|TP|LD|LED|BT|F|"
                              r"BTN|M|MOD|PS|VR|DS|H|XP|XS|JA|JB|JC|JD|JE)\d{1,4}[A-Z]?$")
PASSIVE_RE = re.compile(r"^(R|RN|C|L|FB|D|F|JP|TP|Y|X)\d{1,4}[A-Z]?$")
PIN_NUM_RE = re.compile(r"^(?:[A-Z]{0,3}\d{1,3}[A-Z]?|[A-Z]{1,2})$")
NET_RE = re.compile(r"^[A-Za-z0-9_+\-./\\#~\[\]!()]{2,64}$")
NC_RE = re.compile(r"^(NC|N\.C\.?|N/C|NO_?CONNECT|DNC|DNP|OPEN)$", re.I)
PART_LIKE_RE = re.compile(r"^[A-Z]{2,6}-?\d{3,}[A-Z0-9#\-/.,()+_]*$")
BANK_RE = re.compile(r"\bBANK\s*_?(\d{1,3}[A-Z]?)\b", re.I)
# component values that must never be read as net labels: 150uF, 0.1uF, 10K, 4K7, 100, 1nF, 600R
ANON = "⁣"   # invisible separator: prefix of page-local wire-group keys (never in a label)
VALUE_RE = re.compile(r"^\d+([.,]\d+)?\s*([pnuµmkKMG]|[RKkM]\d+)?\s*(F|H|Ω|R|ohm|OHM)?$|"
                      r"^\d+([.,]\d+)?\s*[pnuµm]?[FH]\b", re.I)


class Grid:
    """Bucket grid over segments for fast proximity queries."""

    def __init__(self, segs: list[Seg], cell: float = 12.0):
        self.cell = cell
        self.buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
        self.segs = segs
        for s in segs:
            for key in self._keys(s.x0 - 1, s.y0 - 1, s.x1 + 1, s.y1 + 1):
                self.buckets[key].append(s.idx)

    def _keys(self, x0, y0, x1, y1):
        c = self.cell
        for gx in range(int(x0 // c), int(x1 // c) + 1):
            for gy in range(int(y0 // c), int(y1 // c) + 1):
                yield gx, gy

    def near(self, x0, y0, x1, y1) -> set[int]:
        out: set[int] = set()
        for key in self._keys(x0, y0, x1, y1):
            out.update(self.buckets.get(key, ()))
        return out


class UF:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, a: int) -> int:
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a: int, b: int) -> None:
        a, b = self.find(a), self.find(b)
        if a != b:
            self.p[b] = a


def _on_segment(x: float, y: float, s: Seg, tol: float) -> bool:
    if s.horizontal:
        return abs(y - s.y0) <= tol and s.x0 - tol <= x <= s.x1 + tol
    return abs(x - s.x0) <= tol and s.y0 - tol <= y <= s.y1 + tol


class _PinRec:
    __slots__ = ("seg", "body", "number", "name", "outer", "ref")

    def __init__(self, seg: Seg, body: int, outer: tuple[float, float]):
        self.seg = seg
        self.body = body
        self.outer = outer
        self.number = ""
        self.name = ""
        self.ref = ""


class PageParser:
    def __init__(self, pp: PagePrims):
        self.pp = pp
        self.ts = pp.text_size
        self.tol = max(0.5, 0.12 * self.ts)
        self.visible = [t for t in pp.texts if not t.hidden]
        self.hidden = [t for t in pp.texts if t.hidden]
        self.values: dict[int, str] = {}

    # ---------------------------------------------------------------- bodies
    def find_bodies(self) -> list[Box]:
        pp, ts = self.pp, self.ts
        page_area = pp.width * pp.height
        bodies = []
        for r in pp.rects:
            if r.w < 1.5 * ts or r.h < 1.5 * ts:
                continue
            if r.w * r.h > 0.25 * page_area:
                continue
            bodies.append(r)
        if pp.cadence:
            bodies.extend(self._pill_columns())
        # drop exact duplicates (fill + stroke drawn separately)
        uniq: list[Box] = []
        for b in bodies:
            if not any(abs(b.x0 - u.x0) < 0.5 and abs(b.y0 - u.y0) < 0.5 and abs(b.x1 - u.x1) < 0.5
                       and abs(b.y1 - u.y1) < 0.5 for u in uniq):
                uniq.append(b)
        return uniq

    def _pill_columns(self) -> list[Box]:
        """Connector symbols drawn as a stack of rounded pin cells with the number inside.

        Cadence connector symbols (FMC, VPX) have no body rectangle: every pin is a small "pill"
        made of a left arc, a right arc and two short horizontal lines. A vertical run of left
        arcs paired with a run of right arcs at the same height, with pin numbers between them,
        is treated as one body so the usual edge-pin logic applies.
        """
        ts, tol = self.ts, self.tol
        arcs = [g for g in self.pp.graphics if g.w <= 1.0 * ts and 0.6 * ts <= g.h <= 2.5 * ts]
        cols: dict[tuple[int, int], list[Box]] = defaultdict(list)
        for g in arcs:
            cols[(round(g.x0), round(g.x1))].append(g)
        runs: list[tuple[float, float, float, float, list[Box]]] = []   # x0, x1, ytop, ybot, cells
        for gs in cols.values():
            gs.sort(key=lambda g: g.y0)
            cur = [gs[0]]
            for g in gs[1:]:
                if g.y0 - cur[-1].y1 <= tol:
                    if g.y0 >= cur[-1].y1 - tol:
                        cur.append(g)
                    continue    # overlapping duplicate
                runs.append((min(c.x0 for c in cur), max(c.x1 for c in cur), cur[0].y0, cur[-1].y1, cur))
                cur = [g]
            runs.append((min(c.x0 for c in cur), max(c.x1 for c in cur), cur[0].y0, cur[-1].y1, cur))
        out: list[Box] = []
        used: set[int] = set()
        for i, (lx0, lx1, ly0, ly1, cells) in enumerate(runs):
            if i in used:
                continue
            best = None
            for j, (rx0, rx1, ry0, ry1, _) in enumerate(runs):
                if j == i or j in used or rx0 <= lx1:
                    continue
                if abs(ry0 - ly0) > 2 * tol or abs(ry1 - ly1) > 2 * tol:
                    continue
                w = rx1 - lx0
                if not (1.5 * ts <= w <= 8 * ts):
                    continue
                if best is None or rx0 < runs[best][0]:
                    best = j
            if best is None:
                continue
            rx1 = runs[best][1]
            box = Box(lx0, min(ly0, runs[best][2]), rx1, max(ly1, runs[best][3]))
            numbered = 0
            for c in cells:
                if any(lx1 <= t.cx <= runs[best][0] and c.y0 <= t.cy <= c.y1 and PIN_NUM_RE.match(t.text)
                       for t in self.visible):
                    numbered += 1
            if numbered < max(1, len(cells) // 2):
                continue
            used.update((i, best))
            out.append(box)
        return out

    # ---------------------------------------------------------------- pins
    def find_pins(self, bodies: list[Box], grid: Grid) -> list[_PinRec]:
        tol, ts = self.tol, self.ts
        pins: list[_PinRec] = []
        reach = 1.0 * ts    # a wire may run a little into the body (connector pin graphics)
        for bi, b in enumerate(bodies):
            for si in grid.near(b.x0 - 1, b.y0 - 1, b.x1 + 1, b.y1 + 1):
                s = grid.segs[si]
                outer = None
                if s.horizontal and b.y0 + tol < s.y0 < b.y1 - tol:
                    if b.x0 - tol <= s.x1 <= b.x0 + reach and s.x0 < b.x0 - tol:
                        outer = (s.x0, s.y0)
                    elif b.x1 - reach <= s.x0 <= b.x1 + tol and s.x1 > b.x1 + tol:
                        outer = (s.x1, s.y1)
                elif not s.horizontal and b.x0 + tol < s.x0 < b.x1 - tol:
                    if b.y0 - tol <= s.y1 <= b.y0 + reach and s.y0 < b.y0 - tol:
                        outer = (s.x0, s.y0)
                    elif b.y1 - reach <= s.y0 <= b.y1 + tol and s.y1 > b.y1 + tol:
                        outer = (s.x1, s.y1)
                if outer is None:
                    continue
                pins.append(_PinRec(s, bi, outer))
        self._label_pins(pins, bodies)
        return pins

    def _label_pins(self, pins: list[_PinRec], bodies: list[Box]) -> None:
        ts = self.ts
        # candidate (score, pin index, text) pairs, then assign greedily so each text is used once
        num_cands = []
        name_cands = []
        for pi, p in enumerate(pins):
            s, b = p.seg, bodies[p.body]
            for t in self.visible:
                inside = b.contains(t.cx, t.cy, -0.2)
                if s.horizontal:
                    if not t.horizontal:
                        continue
                    if inside:
                        if abs(t.cy - s.y0) <= 0.6 * ts:
                            edge = b.x0 if abs(s.x1 - b.x0) < abs(s.x0 - b.x1) else b.x1
                            dist = min(abs(t.x0 - edge), abs(t.x1 - edge))
                            if dist <= 4 * ts:
                                name_cands.append((dist, pi, t))
                        continue
                    if not (s.x0 - 0.5 * ts <= t.cx <= s.x1 + 0.5 * ts):
                        continue
                    above = s.y0 - 1.1 * ts <= t.oy <= s.y0 + 0.35 * ts
                    below = s.y0 - 0.3 * ts <= t.y0 <= s.y0 + 0.6 * ts
                    if not (above or below) or not PIN_NUM_RE.match(t.text):
                        continue
                    # Measure from the glyphs, not the bbox (which includes ascender/descender
                    # space): baseline-to-line when above, cap-top-to-line when below. Numbers
                    # printed above the pin line are the common convention, so "below" pays a
                    # small penalty - with tight pin pitch a number sits between two lines.
                    d_above = abs(s.y0 - t.oy) if above else 1e9
                    d_below = abs((t.oy - 0.7 * t.size) - s.y0) + 0.3 * ts if below else 1e9
                    num_cands.append((min(d_above, d_below) + 0.2 * abs(t.cx - (s.x0 + s.x1) / 2),
                                      pi, t))
                else:
                    if t.horizontal and not inside:
                        continue
                    if inside:
                        if abs(t.cx - s.x0) <= 0.6 * ts:
                            edge = b.y0 if abs(s.y1 - b.y0) < abs(s.y0 - b.y1) else b.y1
                            dist = min(abs(t.y0 - edge), abs(t.y1 - edge))
                            if dist <= 4 * ts:
                                name_cands.append((dist, pi, t))
                        continue
                    if not (s.y0 - 0.5 * ts <= t.cy <= s.y1 + 0.5 * ts):
                        continue
                    if abs(t.cx - s.x0) > 1.2 * ts or not PIN_NUM_RE.match(t.text):
                        continue
                    num_cands.append((abs(t.cx - s.x0) + 0.2 * abs(t.cy - (s.y0 + s.y1) / 2), pi, t))
        for cands, attr in ((num_cands, "number"), (name_cands, "name")):
            cands.sort(key=lambda c: c[0])
            for _, pi, t in cands:
                if t.used or getattr(pins[pi], attr):
                    continue
                setattr(pins[pi], attr, t.text)
                t.used = True
        for p in pins:   # connectors often print only the number, inside the body
            if not p.number and re.fullmatch(r"\d{1,3}|[A-Z]{1,2}\d{1,3}", p.name):
                p.number, p.name = p.name, ""

    # ---------------------------------------------------------------- wires
    def group_wires(self, grid: Grid, exclude: set[int]) -> UF:
        segs, tol = self.pp.segs, self.tol
        uf = UF(len(segs))
        for s in segs:
            if s.idx in exclude:
                continue
            for (x, y) in s.ends():
                for oi in grid.near(x - tol, y - tol, x + tol, y + tol):
                    if oi == s.idx or oi in exclude:
                        continue
                    if _on_segment(x, y, segs[oi], tol):
                        uf.union(s.idx, oi)
        return uf

    def find_labels(self, grid: Grid, exclude: set[int]) -> dict[int, list[str]]:
        """Map segment index -> net labels sitting on it."""
        ts, segs = self.ts, self.pp.segs
        out: dict[int, list[str]] = defaultdict(list)
        graphics = self.pp.graphics
        for t in self.visible:
            if t.used or not NET_RE.match(t.text) or not re.search(r"[A-Za-z]", t.text) \
                    or VALUE_RE.match(t.text):
                continue
            if PASSIVE_RE.match(t.text) and any(
                    g.x1 >= t.x0 - ts and g.x0 <= t.x1 + ts and g.y1 >= t.y0 - 1.5 * ts and g.y0 <= t.y1 + 1.5 * ts
                    for g in graphics):
                continue    # "R175" printed over a zigzag on a wire is the resistor's refdes
            best = None
            for si in grid.near(t.x0 - ts, t.y0 - ts, t.x1 + ts, t.y1 + ts):
                if si in exclude:
                    continue
                s = segs[si]
                if t.horizontal and s.horizontal:
                    on = s.x0 - 0.3 * ts <= t.cx <= s.x1 + 0.3 * ts
                    if not on and self.pp.cadence:
                        # Cadence starts the label at the wire end, so it may overhang it
                        ov = min(t.x1, s.x1) - max(t.x0, s.x0)
                        on = ov >= 0.25 * (t.x1 - t.x0)
                    if not on:
                        continue
                    d = s.y0 - t.oy
                    if -0.3 * ts <= d <= 0.7 * ts:
                        score = abs(d)
                    else:
                        continue
                elif not t.horizontal and not s.horizontal:
                    if not (s.y0 - 0.3 * ts <= t.cy <= s.y1 + 0.3 * ts):
                        continue
                    d = (s.x0 - t.ox) * (1 if t.dy < 0 else -1)
                    if -0.3 * ts <= d <= 0.7 * ts:
                        score = abs(d)
                    else:
                        continue
                else:
                    continue
                if best is None or score < best[0]:
                    best = (score, si)
            if best is None and self.pp.cadence and t.horizontal:
                # port-style label: text beside a free wire end, on the wire's line, with a gap
                for si in grid.near(t.x0 - 3 * ts, t.y0 - ts, t.x1 + 3 * ts, t.y1 + ts):
                    if si in exclude:
                        continue
                    s = segs[si]
                    if not s.horizontal or abs(t.cy - s.y0) > 0.6 * ts:
                        continue
                    if t.x1 - 0.3 * ts <= s.x0 <= t.x1 + 3 * ts:
                        gap, end = s.x0 - t.x1, (s.x0, s.y0)
                    elif t.x0 - 3 * ts <= s.x1 <= t.x0 + 0.3 * ts:
                        gap, end = t.x0 - s.x1, (s.x1, s.y1)
                    else:
                        continue
                    if any(oi != si and _on_segment(end[0], end[1], segs[oi], self.tol)
                           for oi in grid.near(end[0] - 1, end[1] - 1, end[0] + 1, end[1] + 1)):
                        continue    # not a free end
                    score = abs(gap) + ts
                    if best is None or score < best[0]:
                        best = (score, si)
            if best is not None:
                out[best[1]].append(t.text)
                t.used = True
        return out

    # ---------------------------------------------------------------- refdes / part
    def assign_refdes(self, bodies: list[Box]) -> tuple[dict[int, str], dict[int, str], dict[int, str]]:
        ts = self.ts
        ref_c, part_c = [], []
        for bi, b in enumerate(bodies):
            for t in self.visible:
                if t.used:
                    continue
                d = b.dist(t.cx, t.cy)
                if d > 5 * ts:
                    continue
                inside = b.contains(t.cx, t.cy)
                if REFDES_RE.match(t.text):
                    common = bool(COMMON_PREFIX_RE.match(t.text))
                    ref_c.append((d + (2 * ts if inside else 0) - (4 * ts if common else 0), bi, t))
                elif re.search(r"\d", t.text) and re.search(r"[A-Za-z]", t.text) and len(t.text) >= 4 \
                        and not BANK_RE.search(t.text) and not re.search(r"\s|:", t.text) \
                        and not VALUE_RE.match(t.text):
                    score = d - 0.05 * len(t.text)
                    if PART_LIKE_RE.match(t.text):
                        score -= 1.5 * ts       # LTM4702EY#PBF, AD9084BBPZ, XCVU35P-..., ASP-184329-01
                    if t.cy > b.y1:
                        score -= 0.5 * ts       # part numbers are usually printed under the body
                    part_c.append((score, bi, t))
        refs: dict[int, str] = {}
        parts: dict[int, str] = {}
        for cands, out in ((ref_c, refs), (part_c, parts)):
            cands.sort(key=lambda c: c[0])
            for _, bi, t in cands:
                if t.used or bi in out:
                    continue
                out[bi] = t.text
                t.used = True
        banks: dict[int, str] = {}
        for bi, b in enumerate(bodies):
            for t in self.visible:
                if b.contains(t.cx, t.cy, 0.5 * ts):
                    m = BANK_RE.search(t.text)
                    if m:
                        banks[bi] = m.group(1)
                        t.used = True
                        break
        return refs, parts, banks


def _split_marker(body: str, refs: set[str]) -> tuple[str, str] | None:
    """'IC160Y11' -> ('IC16', 'Y11') using the known refdes set (Altium replaces separators by 0)."""
    best = None
    for i in range(1, len(body)):
        ref = body[:i]
        rest = body[i:]
        if ref in refs and rest.startswith("0") and len(rest) > 1:
            if best is None or len(ref) > len(best[0]):
                best = (ref, rest[1:])
    return best


def parse_pdf(path: str, name: str | None = None, progress=None, use_markers: bool = True) -> Board:
    pages = extract_pdf(path)
    board = Board(name=name or re.sub(r"\.pdf$", "", path.replace("\\", "/").split("/")[-1], flags=re.I),
                  source=path)
    # net name -> list of (page, group root) for global merging
    comp_pins: dict[str, dict[str, Pin]] = defaultdict(dict)
    comp_part: dict[str, str] = {}
    comp_pages: dict[str, set[int]] = defaultdict(set)
    comp_bank_by_pin: dict[tuple[str, str], str] = {}
    anon = 0

    # global net merge: union of "net keys" (page-local group ids and label names)
    net_uf: dict[str, str] = {}

    def nfind(k: str) -> str:
        net_uf.setdefault(k, k)
        while net_uf[k] != k:
            net_uf[k] = net_uf[net_uf[k]]
            k = net_uf[k]
        return k

    def nunion(a: str, b: str) -> None:
        ra, rb = nfind(a), nfind(b)
        if ra != rb:
            # the root names the net: labels beat anonymous group ids, net-like names beat part numbers
            if _name_rank(rb) > _name_rank(ra):
                ra, rb = rb, ra
            net_uf[rb] = ra

    pending: list[tuple[str, str, str, str, int]] = []   # (ref, pin no, pin name, group key, page)

    for n, pp in enumerate(pages):
        if progress:
            progress(n, len(pages))
        P = PageParser(pp)
        grid = Grid(pp.segs, cell=max(8.0, 2 * pp.text_size))
        bodies = P.find_bodies()
        pins = P.find_pins(bodies, grid)

        # capacitor plates: short parallel strokes close together next to a C refdes -> graphic
        plate_ex = _capacitor_plates(P, grid)
        # strokes lying wholly inside a symbol body (pin-cell dividers, internal graphics) are
        # not wires; text above them must not pass for a net label
        # (Cadence pages only: elsewhere a large group frame can enclose whole sub-circuits)
        pin_segs = {p.seg.idx for p in pins}
        for b in (bodies if pp.cadence else []):
            for si in grid.near(b.x0, b.y0, b.x1, b.y1):
                s = pp.segs[si]
                if si not in pin_segs and b.contains(s.x0, s.y0, P.tol) and b.contains(s.x1, s.y1, P.tol):
                    plate_ex.add(si)
        uf = P.group_wires(grid, plate_ex)
        P.values = _passive_values(P)      # consume values before they can pass for net labels
        labels = P.find_labels(grid, plate_ex)
        refs, parts, banks = P.assign_refdes(bodies)

        def gkey(si: int) -> str:
            return f"{ANON}p{pp.number}g{uf.find(si)}"

        for si, names in labels.items():
            for nm in names:
                if NC_RE.match(nm):     # "NC" marks an unconnected pin, it is not a shared net
                    continue
                nunion(nm, gkey(si))

        # Altium markers
        co_refs = ({t.text[2:] for t in P.hidden if t.text.startswith("CO") and len(t.text) > 2}
                   if use_markers else set())
        marker_pins = []
        for t in P.hidden:
            if t.text.startswith("PI") and co_refs:
                sp = _split_marker(t.text[2:], co_refs)
                if sp:
                    marker_pins.append((sp[0], sp[1], t))

        # body pins
        body_pin_seg: dict[int, _PinRec] = {}
        for p in pins:
            body_pin_seg[p.seg.idx] = p
            ref = refs.get(p.body)
            if not ref:
                continue
            p.ref = ref
        # use markers to correct refdes/pin numbers of body pins
        for ref, num, t in marker_pins:
            hits = grid.near(t.x0, t.y0, t.x1, t.y1)
            best = None
            for si in hits:
                if si in plate_ex:
                    continue
                s = pp.segs[si]
                ov = _overlap(s, Box(t.x0, t.y0, t.x1, t.y1))
                if ov > 0 and (best is None or ov > best[0]):
                    best = (ov, si)
            if best is None:
                continue
            si = best[1]
            if si in body_pin_seg:
                p = body_pin_seg[si]
                p.ref, p.number = ref, num
            else:
                pending.append((ref, num, "", gkey(si), pp.number))
                comp_pages[ref].add(pp.number)

        for p in pins:
            if not p.ref:
                continue
            number = p.number or p.name or f"?{p.seg.idx}"
            pending.append((p.ref, number, p.name, gkey(p.seg.idx), pp.number))
            comp_pages[p.ref].add(pp.number)
            if p.body in parts and p.ref not in comp_part:
                comp_part[p.ref] = parts[p.body]
            if p.body in banks:
                comp_bank_by_pin[(p.ref, number)] = banks[p.body]

        # two-pin parts without a body rectangle
        if not marker_pins:
            for ref, value, ends in _two_pin_parts(P, grid, plate_ex, bodies):
                for i, si in enumerate(ends, start=1):
                    pending.append((ref, str(i), "", gkey(si), pp.number))
                comp_pages[ref].add(pp.number)
                if value:
                    comp_part.setdefault(ref, value)
        else:
            for t in P.visible:
                if not t.used and PASSIVE_RE.match(t.text):
                    t.used = True
                    if P.values.get(id(t)):
                        comp_part.setdefault(t.text, P.values[id(t)])

    # merge multi-part symbols (IC16A..IC16E -> IC16)
    sections: dict[str, set[str]] = defaultdict(set)
    for ref, *_ in pending:
        m = re.match(r"^([A-Z]+\d+)([A-Z])$", ref)
        if m:
            sections[m.group(1)].add(ref)
    all_refs = {r for r, *_ in pending}

    def _conflict(base: str) -> bool:
        # a plain "U1" that is a different part than the "U1A" sections is its own component
        if base not in all_refs or not comp_part.get(base):
            return False
        return any(comp_part.get(s) and comp_part[s] != comp_part[base] for s in sections[base])

    merge_ok = {b for b, secs in sections.items()
                if (len(secs) >= 2 or b in all_refs) and not _conflict(b)}

    def canon(ref: str) -> str:
        m = re.match(r"^([A-Z]+\d+)([A-Z])$", ref)
        if m and m.group(1) in merge_ok:
            return m.group(1)
        return ref

    for ref, num, pname, key, page in pending:
        root = nfind(key)
        net = root if not root.startswith(ANON) else ""
        if not net:
            anon += 1
            net = "N$" + root[len(ANON):]
        c = canon(ref)
        pin = comp_pins[c].get(num)
        if pin is None:
            comp_pins[c][num] = Pin(num, pname, net, page)
        elif pname and not pin.name:
            pin.name = pname
        bank = comp_bank_by_pin.get((ref, num))
        if bank:
            comp_pins[c][num].bank = bank
        comp_pages[c].add(page)
        if ref in comp_part and c not in comp_part:
            comp_part[c] = comp_part[ref]

    for ref, pins_ in comp_pins.items():
        board.components[ref] = Component(ref, comp_part.get(ref, ""), pins=pins_, pages=comp_pages[ref])
    board.rebuild_nets()
    if progress:
        progress(len(pages), len(pages))
    return board


def _name_rank(name: str) -> int:
    if name.startswith(ANON):
        return -10
    rank = 0
    if re.fullmatch(r"[A-Z]{2,6}\d{3,}[A-Z0-9\-]*", name) and len(name) >= 8:
        rank -= 5        # looks like a part number (NRS5020T1R0NMGJ, SRN6045-1R0Y)
    if "_" in name or "-" in name:
        rank += 1
    if re.match(r"^(VCC|VDD|VADJ|GND|\d+V\d)", name):
        rank += 2
    return rank


def _overlap(s: Seg, b: Box) -> float:
    if s.horizontal:
        if not (b.y0 - 0.5 <= s.y0 <= b.y1 + 0.5):
            return 0.0
        return max(0.0, min(s.x1, b.x1) - max(s.x0, b.x0))
    if not (b.x0 - 0.5 <= s.x0 <= b.x1 + 0.5):
        return 0.0
    return max(0.0, min(s.y1, b.y1) - max(s.y0, b.y0))


def _capacitor_plates(P: PageParser, grid: Grid) -> set[int]:
    ts, segs = P.ts, P.pp.segs
    crefs = [t for t in P.visible if re.match(r"^C\d+[A-Z]?$", t.text)]
    out: set[int] = set()
    if not crefs:
        return out
    for t in crefs:
        near = [segs[i] for i in grid.near(t.cx - 4 * ts, t.cy - 4 * ts, t.cx + 4 * ts, t.cy + 4 * ts)]
        short = [s for s in near if 0.8 * ts <= s.length <= 3 * ts]
        for i, a in enumerate(short):
            for b in short[i + 1:]:
                if a.horizontal != b.horizontal:
                    continue
                if a.horizontal:
                    gap = abs(a.y0 - b.y0)
                    ov = min(a.x1, b.x1) - max(a.x0, b.x0)
                else:
                    gap = abs(a.x0 - b.x0)
                    ov = min(a.y1, b.y1) - max(a.y0, b.y0)
                if 0.1 * ts < gap <= 0.9 * ts and ov >= 0.6 * min(a.length, b.length):
                    out.update((a.idx, b.idx))
    return out


def _passive_values(P: PageParser) -> dict[int, str]:
    """Value text of each passive refdes: aligned directly above/below it, or a plain value nearby."""
    ts = P.ts
    out: dict[int, str] = {}
    cands = []
    for t in P.visible:
        if t.used or not PASSIVE_RE.match(t.text):
            continue
        for v in P.visible:
            if v is t or v.used or REFDES_RE.match(v.text) or t.horizontal != v.horizontal:
                continue
            if t.horizontal:
                aligned = abs(v.x0 - t.x0) <= 0.5 * ts or abs(v.cx - t.cx) <= 0.5 * ts
                gap = max(v.y0 - t.y1, t.y0 - v.y1)
            else:
                aligned = abs(v.y0 - t.y0) <= 0.5 * ts or abs(v.cy - t.cy) <= 0.5 * ts
                gap = max(v.x0 - t.x1, t.x0 - v.x1)
            near = abs(v.cx - t.cx) <= 4 * ts and abs(v.cy - t.cy) <= 2.5 * ts
            if aligned and -0.3 * ts <= gap <= 1.5 * ts:
                cands.append((gap, t, v))
            elif near and VALUE_RE.match(v.text):
                cands.append((ts + abs(v.cx - t.cx) + abs(v.cy - t.cy), t, v))
    cands.sort(key=lambda c: c[0])
    for _, t, v in cands:
        if id(t) in out or v.used:
            continue
        out[id(t)] = v.text
        v.used = True
    return out


def _two_pin_parts(P: PageParser, grid: Grid, plate_ex: set[int], bodies: list[Box]):
    """Yield (refdes, value, [segment index of pin 1, pin 2]) for passives drawn without a body."""
    ts, segs, tol = P.ts, P.pp.segs, P.tol
    graphics = list(P.pp.graphics) + [r for r in P.pp.rects if r.w < 1.5 * ts or r.h < 1.5 * ts]
    plates = [Box(segs[i].x0, segs[i].y0, segs[i].x1, segs[i].y1) for i in plate_ex]
    plate_ids = {id(b) for b in plates}
    graphics += plates
    cadence = P.pp.cadence

    def gscore(t, g):
        d = g.dist(t.cx, t.cy)
        if cadence and (g.y0 + g.y1) / 2 < t.cy:
            d += 1.0 * ts       # Cadence prints the refdes above its symbol
        return d

    # visit refdes texts closest-first so a crowded label cannot steal a neighbour's symbol
    todo = []
    for t in P.visible:
        if t.used or not PASSIVE_RE.match(t.text):
            continue
        cand = [g for g in graphics if g.dist(t.cx, t.cy) <= 2.5 * ts and g.w < 6 * ts and g.h < 6 * ts]
        if cand:
            todo.append((min(gscore(t, g) for g in cand), t, cand))
    if cadence:
        todo.sort(key=lambda x: x[0])
    claimed: set[int] = set()
    for _, t, cand in todo:
        if t.used:
            continue
        cand = [g for g in cand if id(g) not in claimed]
        if not cand:
            continue
        g0 = min(cand, key=lambda g: gscore(t, g))
        sym = Box(g0.x0, g0.y0, g0.x1, g0.y1)
        if cadence:
            # Cadence splits a zigzag into touching arcs; neighbours in a dense column of
            # resistors sit only a few points away, so merge strictly touching pieces, transitively
            members = {id(g0)}
            grew = True
            while grew:
                grew = False
                for g in cand:
                    if id(g) in members:
                        continue
                    # capacitor plates face each other across a gap; arcs of a zigzag touch
                    gap = 1.0 * ts if (id(g) in plate_ids and id(g0) in plate_ids) else tol
                    if any(g.x0 <= m.x1 + gap and g.x1 >= m.x0 - gap and g.y0 <= m.y1 + gap
                           and g.y1 >= m.y0 - gap for m in cand if id(m) in members):
                        members.add(id(g))
                        sym = Box(min(sym.x0, g.x0), min(sym.y0, g.y0), max(sym.x1, g.x1), max(sym.y1, g.y1))
                        grew = True
        else:
            for g in cand:   # merge graphics touching the nearest one (e.g. two capacitor plates)
                if g is not g0 and g.dist((g.x0 + g.x1) / 2, (g.y0 + g.y1) / 2) < 3 * ts and \
                        sym.dist((g.x0 + g.x1) / 2, (g.y0 + g.y1) / 2) < 1.0 * ts:
                    sym = Box(min(sym.x0, g.x0), min(sym.y0, g.y0), max(sym.x1, g.x1), max(sym.y1, g.y1))
        ends = []
        for si in grid.near(sym.x0 - tol, sym.y0 - tol, sym.x1 + tol, sym.y1 + tol):
            if si in plate_ex:
                continue
            s = segs[si]
            for (x, y) in s.ends():
                if sym.contains(x, y, tol) and not sym.contains((s.x0 + s.x1) / 2, (s.y0 + s.y1) / 2, -tol):
                    ends.append((x, y, si))
        # collapse to distinct sides of the symbol
        uniq = []
        for e in ends:
            if not any(abs(e[0] - u[0]) < tol and abs(e[1] - u[1]) < tol for u in uniq):
                uniq.append(e)
        if len(uniq) != 2:
            continue
        uniq.sort(key=lambda e: (e[0], e[1]))
        value = P.values.get(id(t), "")
        t.used = True
        if cadence:
            claimed.update(id(g) for g in cand if sym.contains((g.x0 + g.x1) / 2, (g.y0 + g.y1) / 2, tol))
        yield t.text, value, [uniq[0][2], uniq[1][2]]
