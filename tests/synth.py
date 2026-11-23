"""Generate small synthetic schematic PDFs for tests (vendor schematics cannot live in the repo)."""

from __future__ import annotations

import pymupdf

FS = 7.0          # font size
STUB = 15.0       # pin stub length
PITCH = 14.0      # pin pitch


class Sheet:
    def __init__(self, width: float = 842, height: float = 595):
        self.doc = pymupdf.open()
        self.page = self.doc.new_page(width=width, height=height)
        self.page.draw_rect(pymupdf.Rect(10, 10, width - 10, height - 10), width=0.5)   # frame

    def text(self, x: float, y: float, s: str, size: float = FS):
        self.page.insert_text((x, y), s, fontsize=size, fontname="helv")

    def line(self, x0, y0, x1, y1):
        self.page.draw_line((x0, y0), (x1, y1), width=0.6)

    def rect(self, x0, y0, x1, y1):
        self.page.draw_rect(pymupdf.Rect(x0, y0, x1, y1), width=0.8)

    def flag(self, x: float, y: float, direction: int):
        """Off-page connector "<<" / ">>" starting at wire end (x, y), pointing away (+1 right)."""
        for k in (0, 4):
            x0 = x + direction * k
            self.page.draw_polyline([(x0, y - 3), (x0 + direction * 4, y), (x0, y + 3)], width=0.4)


    def symbol(self, x: float, y: float, ref: str, part: str, left=(), right=(), bank: str | None = None,
               width: float = 90, wire: float = 70, flags: bool = False,
               pitch: float = PITCH, mid_numbers: bool = False):
        """Body at (x, y). left/right: [(pin number, pin name, net label or None)].

        Pins with a net label get a wire ending in that label; returns {pin number: wire end point}.
        """
        n = max(len(left), len(right), 1)
        h = (n + 1) * pitch
        self.rect(x, y, x + width, y + h)
        self.text(x, y - 4, ref)
        self.text(x, y + h + 10, part)
        if bank:
            self.text(x + 20, y + 10, f"BANK {bank}")
        ends = {}
        for side, pins in (("L", left), ("R", right)):
            for i, (num, name, net) in enumerate(pins):
                py = y + (i + 1) * pitch + 4
                # number baseline: just above the line, or centred between this line and the one above
                ny = py - pitch / 2 + 0.35 * FS if mid_numbers else py - 1.5
                if side == "L":
                    x_out = x - STUB
                    self.line(x_out, py, x, py)
                    self.text(x - STUB + 2, ny, num)
                    self.text(x + 3, py + 2.5, name)
                    if net and flags:   # off-page flag "<<" at the wire end, name beyond it
                        self.line(x_out - wire, py, x_out, py)
                        self.flag(x_out - wire, py, -1)
                        self.text(x_out - wire - 12 - 3.9 * len(net), py + 2.5, net)
                    elif net:
                        self.line(x_out - wire, py, x_out, py)
                        self.text(x_out - wire + 3, py - 1.5, net)
                    ends[num] = (x_out, py)
                else:
                    x_out = x + width + STUB
                    self.line(x + width, py, x_out, py)
                    self.text(x + width + 2, ny, num)
                    self.text(x + width - 3 - 3.6 * len(name), py + 2.5, name)
                    if net and flags:
                        self.line(x_out, py, x_out + wire, py)
                        self.flag(x_out + wire, py, 1)
                        self.text(x_out + wire + 12, py + 2.5, net)
                    elif net:
                        self.line(x_out, py, x_out + wire, py)
                        self.text(x_out + 5, py - 1.5, net)
                    ends[num] = (x_out, py)
        return ends

    def resistor(self, x0: float, y: float, x1: float, ref: str, value: str):
        """Horizontal series resistor between wire ends (x0, y) and (x1, y), drawn as a zigzag."""
        mid0, mid1 = x0 + (x1 - x0) * 0.3, x0 + (x1 - x0) * 0.7
        self.line(x0, y, mid0, y)
        self.line(mid1, y, x1, y)
        pts = [(mid0, y)]
        steps = 6
        for k in range(1, steps):
            pts.append((mid0 + (mid1 - mid0) * k / steps, y + (2.5 if k % 2 else -2.5)))
        pts.append((mid1, y))
        self.page.draw_polyline(pts, width=0.6)
        self.text(mid0, y - 5, ref)
        self.text(mid0, y + 10, value)

    def save(self, path: str):
        self.doc.save(path)
        self.doc.close()


class CadenceSheet(Sheet):
    """Mimics Cadence/Allegro PDF output: every stroke is a closed 2-point path ("m l h S") drawn
    twice, symbol bodies are four loose strokes, connectors are stacks of rounded pin cells."""

    def line(self, x0, y0, x1, y1):
        for _ in range(2):
            sh = self.page.new_shape()
            sh.draw_line((x0, y0), (x1, y1))
            sh.finish(width=0.43, closePath=True)
            sh.commit()

    def rect(self, x0, y0, x1, y1):
        self.line(x0, y0, x1, y0)
        self.line(x1, y0, x1, y1)
        self.line(x1, y1, x0, y1)
        self.line(x0, y1, x0, y0)

    def pill_connector(self, x: float, y: float, ref: str, part: str, pins, wire: float = 80):
        """Column of pin cells; pins: [(number, net)], wire enters each cell from the left and
        the net name is a port-style label left of the wire with a gap."""
        w, h = 36.0, PITCH
        self.text(x + 12, y - 4, ref)
        for i, (num, net) in enumerate(pins):
            top, bot = y + i * h, y + (i + 1) * h
            mid = (top + bot) / 2
            self.line(x + 4, top, x + w - 4, top)
            self.line(x + 4, bot, x + w - 4, bot)
            for k in range(2):  # left and right arcs
                sh = self.page.new_shape()
                if k == 0:
                    sh.draw_bezier((x + 4, top), (x, top + 2), (x, bot - 2), (x + 4, bot))
                else:
                    sh.draw_bezier((x + w - 4, top), (x + w, top + 2), (x + w, bot - 2), (x + w - 4, bot))
                sh.finish(width=0.43)
                sh.commit()
            self.text(x + 11, mid + 2.5, num)
            if net:
                self.line(x - wire, mid, x, mid)
                self.text(x - wire - 12 - 3.9 * len(net), mid + 2.5, net)
        self.text(x, y + len(pins) * h + 10, part)


class GhostTextSheet(Sheet):
    """Mimics SCH2PDF-style output: letters are drawn as line strokes (a crude stroke font) and
    the real text is an invisible layer on top (text render mode 3)."""

    shift = 3.0     # the invisible layer does not sit exactly on the drawn letters

    def text(self, x: float, y: float, s: str, size: float = FS):
        self.page.insert_text((x, y - self.shift), s, fontsize=size, fontname="helv", render_mode=3)
        w, h = 0.55 * size, 0.7 * size
        cx = x
        for ch in s:
            if ch != " ":
                for (a, b, c, d) in ((0, 0, 0, -h), (0, -h, w * 0.8, -h), (0, -h / 2, w * 0.6, -h / 2),
                                     (0, 0, w * 0.8, 0)):
                    self.page.draw_line((cx + a, y + b), (cx + c, y + d), width=0.3)
            cx += w


def fpga_board(path: str, cls=None):
    s = (cls or Sheet)()
    s.symbol(500, 80, "U1", "XC7K325T-2FFG900C", bank="65", left=[
        ("AE12", "IO_L1P_T0_65", "SENS_SDA"),
        ("AF12", "IO_L1N_T0_65", "SENS_SCL"),
        ("AD10", "IO_L2P_T0_65", "LED0"),
        ("AC9", "VCCO_65", "VCC1V8"),
    ])
    s.symbol(150, 80, "J1", "ASP-134603-01", right=[
        ("H7", "LA02_P", "SENS_SDA"),
        ("H8", "LA02_N", "SENS_SCL"),
        ("H1", "GND", "GND"),
    ])
    s.save(path)


def sensor_board(path: str, cls=None):
    s = (cls or Sheet)()
    # TMP117 -> TXS0102 level shifter (A side 3V3, B side 1V8) -> FMC mezzanine connector
    sensor = s.symbol(110, 80, "U5", "TMP117AIDRVR", right=[("6", "SDA", None), ("1", "SCL", None)],
                      left=[("3", "GND", "GND")], wire=25)
    shifter = s.symbol(330, 80, "U7", "TXS0102DCUR", left=[("7", "A1", None), ("6", "A2", None),
                                                          ("5", "VCCA", "VCC3V3")],
                       right=[("2", "B1", "SDA_1V8"), ("3", "B2", None), ("1", "VCCB", "VCC1V8")])
    # SDA: direct wire; SCL: through a 33 ohm series resistor
    (sx, sy), (lx, ly) = sensor["6"], shifter["7"]
    s.line(sx, sy, lx, sy)
    (sx, sy), (lx, ly) = sensor["1"], shifter["6"]
    s.resistor(sx, sy, lx, "R12", "33")
    bx, by = shifter["3"]
    s.line(bx, by, bx + 70, by)
    s.text(bx + 5, by - 1.5, "SCL_1V8")
    s.symbol(650, 80, "J2", "ASP-134488-01", left=[("H7", "LA02_P", "SDA_1V8"), ("H8", "LA02_N", "SCL_1V8"),
                                                  ("H1", "GND", "GND")])
    s.save(path)


def cadence_fpga_board(path: str):
    """Cadence-style FPGA card: U1 body from loose strokes, FMC P1 as pin cells, port labels."""
    s = CadenceSheet()
    s.symbol(520, 80, "U1", "XCVU35P-3FSVH2892E", bank="70", left=[
        ("A9", "IO_L24P_T3U_N10_70", "FMC_LA11_P"),
        ("A8", "IO_L24N_T3U_N11_70", "FMC_LA11_N"),
        ("K15", "VCCO_70", "FMC_1P8V"),
    ])
    s.pill_connector(250, 90, "P1", "ASP-184329-01", [("H1", "GND"), ("H2", "FMC_LA11_P"),
                                                       ("H3", "FMC_LA11_N"), ("H4", None)])
    s.save(path)


def backplane(path: str):
    """Carries the sensor card (J10) to the FPGA board (J11), H-row signals straight through."""
    s = Sheet()
    s.symbol(120, 80, "J10", "ASP-134603-01", right=[("H7", "LA02_P", "BP_SDA"), ("H8", "LA02_N", "BP_SCL"),
                                                    ("H1", "GND", "GND")])
    s.symbol(520, 80, "J11", "ASP-134488-01", left=[("H7", "LA02_P", "BP_SDA"), ("H8", "LA02_N", "BP_SCL"),
                                                   ("H1", "GND", "GND")])
    s.save(path)


def ghost_fpga_board(path: str):
    """SCH2PDF-like FPGA card: stroke letters, invisible text, off-page flags, TD/KN refdes."""
    s = GhostTextSheet()
    # pin numbers centred between two pin lines, as in the real board this mimics
    s.symbol(520, 80, "TD10", "XCVP1502-1MLIVSVA2785", flags=True, pitch=20, mid_numbers=True, left=[
        ("N36", "IO_L6P_GC_XCC_N2P0_712", "FMCP1_LA02_P"),
        ("M37", "IO_L6N_GC_XCC_N2P1_712", "FMCP1_LA02_N"),
        ("N32", "IO_L7P_N2P2_712", "FMCP1_LA03_P"),
        ("M32", "IO_L7N_N2P3_712", "FMCP1_LA03_N"),
        ("AC9", "VCCO_712", "VCCO_1V5"),
    ])
    s.symbol(150, 80, "KN5", "ASP-184329-01", flags=True, right=[
        ("H7", "LA02_P", "FMCP1_LA02_P"),
        ("H8", "LA02_N", "FMCP1_LA02_N"),
        ("H1", "GND", "GND"),
    ])
    s.save(path)


def ghost_adc_board(path: str):
    s = GhostTextSheet()
    s.symbol(150, 80, "TD3", "AD9084BBPZ", flags=True, right=[
        ("C21", "GPIO_17", "APOLLO_GPIO17_FMC"), ("D21", "GPIO_18", "APOLLO_GPIO18_FMC")])
    s.symbol(520, 80, "KN1", "MJ-0000-39366", flags=True, left=[
        ("H7", "LA02_P", "APOLLO_GPIO17_FMC"), ("H8", "LA02_N", "APOLLO_GPIO18_FMC"),
        ("H1", "GND", "GND")])
    s.save(path)
