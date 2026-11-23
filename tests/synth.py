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

    def symbol(self, x: float, y: float, ref: str, part: str, left=(), right=(), bank: str | None = None,
               width: float = 90, wire: float = 70):
        """Body at (x, y). left/right: [(pin number, pin name, net label or None)].

        Pins with a net label get a wire ending in that label; returns {pin number: wire end point}.
        """
        n = max(len(left), len(right), 1)
        h = (n + 1) * PITCH
        self.page.draw_rect(pymupdf.Rect(x, y, x + width, y + h), width=0.8)
        self.text(x, y - 4, ref)
        self.text(x, y + h + 10, part)
        if bank:
            self.text(x + 20, y + 10, f"BANK {bank}")
        ends = {}
        for side, pins in (("L", left), ("R", right)):
            for i, (num, name, net) in enumerate(pins):
                py = y + (i + 1) * PITCH + 4
                if side == "L":
                    x_out = x - STUB
                    self.line(x_out, py, x, py)
                    self.text(x - STUB + 2, py - 1.5, num)
                    self.text(x + 3, py + 2.5, name)
                    if net:
                        self.line(x_out - wire, py, x_out, py)
                        self.text(x_out - wire + 3, py - 1.5, net)
                    ends[num] = (x_out, py)
                else:
                    x_out = x + width + STUB
                    self.line(x + width, py, x_out, py)
                    self.text(x + width + 2, py - 1.5, num)
                    self.text(x + width - 3 - 3.6 * len(name), py + 2.5, name)
                    if net:
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


def fpga_board(path: str):
    s = Sheet()
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


def sensor_board(path: str):
    s = Sheet()
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


def backplane(path: str):
    """Carries the sensor card (J10) to the FPGA board (J11), H-row signals straight through."""
    s = Sheet()
    s.symbol(120, 80, "J10", "ASP-134603-01", right=[("H7", "LA02_P", "BP_SDA"), ("H8", "LA02_N", "BP_SCL"),
                                                    ("H1", "GND", "GND")])
    s.symbol(520, 80, "J11", "ASP-134488-01", left=[("H7", "LA02_P", "BP_SDA"), ("H8", "LA02_N", "BP_SCL"),
                                                   ("H1", "GND", "GND")])
    s.save(path)
