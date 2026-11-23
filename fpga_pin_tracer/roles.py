"""Component roles, power nets, FPGA banks and pass-through pin mapping."""

from __future__ import annotations

import re

from .model import Board, Component

FPGA_PART_RE = re.compile(
    r"^(XC|XQ|XA)[A-Z]{0,3}\d|"     # AMD/Xilinx: XC7K, XCZU, XCKU, XCVU, XCVP (Versal Premium), XQ...
    r"^(10M|10CL|10AX|10AS|5CE|5CG|5CS|5SG|EP4C|EP3C|EP2C|EP4S|AGF|AGI|1S[GXM]|A5E)|"   # Intel/Altera
    r"^(LFE|LCMXO|LFCPNX|LIFCL|ICE40|ICE5)|"                   # Lattice
    r"^(M2S|M2GL|MPF|A3P|AGL|RT4G)|^GW\dA",                     # Microchip, Gowin
    re.I)
BUFFER_PART_RE = re.compile(
    r"TXS0\d|TXB0\d|TXU0\d|TXV0\d|TXS02612|SN74(LVC|AVC|AUP|ALVC|LV|AHC|HC|CBT|CB3|LVT|TXB|TXS|GTL)|"
    r"^74(LVC|AVC|ALVC|AUP|LVT|HC|HCT|AHC|AHCT|ACT|LV|CBT)|NLSX|NTB0|NTS0|PCA9306|PCA951[2-7]|"
    r"PCA954\d|TCA9406|TCA951[2-7]|GTL2|FXMA|FXLA|FXL[0-9]|LSF0\d|NVT20|ADG3\d{3}|MAX395|"
    r"MAX1484[0-9]|ADUM|ISO7[0-9]|SN65LVDS|DS90LV|DS90C|MAX3232|SN75|LTC28|ADM3|MAX48|SP33|"
    r"PI3|TS3A|TS5A|FSA|NX3L", re.I)
CONNECTOR_PART_RE = re.compile(
    r"ASP-1344|ASP-1346|ASP-1348|SEAF|SEAM|FMC|VPX|VITA|HEADER|HDR|CONN|SAMTEC|MOLEX|TSW-|SSW-|"
    r"QSH|QTH|QSE|QTE|ERM|ERF|HSEC|PMOD|H-\d+X\d+|\d+X\d+|DF\d+|FX\d+|BM\d+|USB|RJ45|SFP|QSFP|HDMI",
    re.I)
# Supply nets: GND*, VCC*, DVDD, VADJ, 1V8, P1V8, +3V3, 3V3_AUX, DDR1V5, 12P0V, 3P3V ...
# but not signals that merely carry a voltage domain suffix (I2C_SDA_1V8, SCL_3V3).
POWER_NET_RE = re.compile(
    r"^[+-]?((A|D|P|S|C)?GND\w*|EARTH|CHASSIS|[A-Z]{0,3}V(CC|DD|SS|EE)\w*|"
    r"V(ADJ|IN|BAT|BUS|USB|CCO|CCINT|CCAUX|CCBRAM|MGT\w*|REF\w*|TT|PP|IO)\w*|"
    r"(P|V|DDR|VIO)?_?\d+V\d*(_\w+)?|\d+P\d+V?\w*|P\d+V\d+\w*)$",
    re.I)
GROUND_PIN_RE = re.compile(r"^(A|D|P)?GND\w*$|^VSS\w*$", re.I)
# pin names of supply pins (FPGA VCCAUX/VCCO_13/VCCPINT..., chip AVDD/DVDD/VIO...)
SUPPLY_PIN_RE = re.compile(r"^[A-Z]{0,3}V(CC|DD|SS|EE)\w*$|^(A|D|P)?GND\w*$|^V(REF|IO|BAT|IN|BUS)\w*$|"
                           r"^(VCCO|VCCAUX|VCCINT|VCCBRAM|VCCPINT|VCCPAUX|VCCPLL|VCCADC|VREFP|VREFN|"
                           r"GNDADC|MGTAVCC|MGTAVTT|MGTVCCAUX)\w*$", re.I)
PASSIVE_REF_RE = re.compile(r"^(R|RN|FB|L|C|D|F|Y|X|TP|JP)\d+[A-Z]?$")
CONNECTOR_REF_RE = re.compile(r"^(J|P|CN|CON|KN|XS|XP|X|JA|JB|JC|JD|JE)\d+[A-Z]?$")   # KN: konnektör

XILINX_BANK_FROM_NAME = re.compile(r"_(\d{2,3})$")
VCCO_RE = re.compile(r"^VCCO_?(\d{1,3}[A-Z]?)$", re.I)


def is_power_net(board: Board, net: str) -> bool:
    if not net:
        return True
    if POWER_NET_RE.match(net):
        return True
    if net.startswith("N$"):
        # anonymous: power when every connected pin is a ground/supply pin
        members = board.nets.get(net, [])
        names = [board.components[r].pins[p].name for r, p in members if p in board.components[r].pins]
        return bool(names) and all(GROUND_PIN_RE.match(n or "") or POWER_NET_RE.match(n or "") for n in names)
    return False


def classify(comp: Component) -> str:
    part = comp.part or ""
    ref = comp.refdes
    names = [p.name for p in comp.pins.values()]
    io_like = sum(1 for n in names if re.match(r"^(IO|PS_MIO|MIO|PL_|HP_|HD_|IO_L)", n or ""))
    if FPGA_PART_RE.search(part) or (len(comp.pins) >= 100 and io_like >= 40):
        return "fpga"
    if BUFFER_PART_RE.search(part):
        return "buffer"
    if PASSIVE_REF_RE.match(ref) and len(comp.pins) <= 2:
        return "passive"
    if CONNECTOR_REF_RE.match(ref) or CONNECTOR_PART_RE.search(part):
        return "connector"
    return "other"


def annotate(board: Board) -> None:
    """Assign roles, FPGA banks and bank voltages in place."""
    for comp in board.components.values():
        comp.role = classify(comp)
    for comp in board.components.values():
        if comp.role != "fpga":
            continue
        for pin in comp.pins.values():
            if not pin.bank:
                m = XILINX_BANK_FROM_NAME.search(pin.name or "")
                if m and re.match(r"^(IO|MGT)", pin.name or ""):
                    pin.bank = m.group(1)
        # bank voltage = net of that bank's VCCO pins
        for pin in comp.pins.values():
            m = VCCO_RE.match(pin.name or "")
            if m and pin.net and not pin.net.startswith("N$"):
                board.bank_voltage.setdefault((comp.refdes, m.group(1)), pin.net)


def is_signal_pin(board: Board, comp: Component, pin_no: str) -> bool:
    pin = comp.pins.get(pin_no)
    if pin is None or is_power_net(board, pin.net):
        return False
    return not SUPPLY_PIN_RE.match(pin.name or "")


# ----------------------------------------------------------------- pass-through mapping
def _buffer_partner_names(name: str) -> list[str]:
    """Names of the pin on the other side of a level shifter / buffer."""
    n = name.upper()
    out = []
    m = re.fullmatch(r"([A-Z]*?)A(\d+)", n)          # A1 <-> B1, 1A? handled below
    if m:
        out += [f"{m.group(1)}B{m.group(2)}", f"{m.group(1)}Y{m.group(2)}"]
    m = re.fullmatch(r"([A-Z]*?)B(\d+)", n)
    if m:
        out += [f"{m.group(1)}A{m.group(2)}"]
    m = re.fullmatch(r"([A-Z]*?)Y(\d+)", n)
    if m:
        out += [f"{m.group(1)}A{m.group(2)}"]
    m = re.fullmatch(r"(\d+)A(\d*)", n)              # 1A <-> 1Y / 1B
    if m:
        out += [f"{m.group(1)}Y{m.group(2)}", f"{m.group(1)}B{m.group(2)}"]
    m = re.fullmatch(r"(\d+)([YB])(\d*)", n)
    if m:
        out += [f"{m.group(1)}A{m.group(3)}"]
    m = re.fullmatch(r"(.*?)(1|2)", n)               # SCL1 <-> SCL2 (PCA9306), SDA_1 <-> SDA_2
    if m and m.group(1) and not m.group(1).endswith(("A", "B", "Y")) and not m.group(1)[-1].isdigit():
        out += [f"{m.group(1)}{3 - int(m.group(2))}"]
    m = re.fullmatch(r"(.*?)_?(A|B)", n)             # SDA_A <-> SDA_B
    if m and m.group(1):
        out += [f"{m.group(1)}_{'B' if m.group(2) == 'A' else 'A'}", f"{m.group(1)}{'B' if m.group(2) == 'A' else 'A'}"]
    if n in ("A", "IN"):
        out += ["Y", "B", "OUT"]
    if n in ("Y", "B", "OUT"):
        out += ["A", "IN"]
    return out


def _diff_pair(a: str, b: str) -> bool:
    """HSCI_CKIN_P / HSCI_CKIN_N, LA01_P_CC / LA01_N_CC, CLK+ / CLK-: same name except polarity."""
    if not a or not b or a == b:
        return False
    for p, n in (("P", "N"), ("POS", "NEG"), ("PLUS", "MINUS"), ("+", "-")):
        for x, y in ((a, b), (b, a)):
            for i in range(len(x) - len(p) + 1):
                if x[i:i + len(p)].upper() != p:
                    continue
                before = x[i - 1] if i else ""
                after = x[i + len(p)] if i + len(p) < len(x) else ""
                if (before.isalpha() and p.isalpha()) or (after.isalpha() and p.isalpha()):
                    continue
                if (x[:i] + n + x[i + len(p):]).upper() == y.upper():
                    return True
    return False


def pass_through(board: Board, comp: Component, pin_no: str) -> list[tuple[str, str]]:
    """Pins of `comp` a signal entering at `pin_no` continues to, with a short note for each."""
    pin = comp.pins.get(pin_no)
    if pin is None:
        return []
    if comp.role == "passive" and len(comp.pins) == 2:
        if comp.refdes.startswith(("C", "D", "TP", "Y", "X")):
            # capacitors only pass AC-coupled signals, i.e. when neither side is power/ground
            if not comp.refdes.startswith("C"):
                return []
        other = next(p for n, p in comp.pins.items() if n != pin_no)
        if is_power_net(board, other.net) or is_power_net(board, pin.net):
            return []
        if _diff_pair(pin.net, other.net):
            return []       # termination resistor across a P/N pair, not in series
        kind ={"R": "seri direnç", "F": "ferrit", "L": "bobin", "C": "kuplaj kondansatörü"}.get(comp.refdes[0], "pasif")
        val = f" {comp.part}" if comp.part else ""
        return [(other.number, f"{comp.refdes} ({kind}{val})")]
    if comp.role == "buffer":
        out = []
        for partner in _buffer_partner_names(pin.name or ""):
            for p in comp.pin_by_name(partner):
                if p.number != pin_no and not is_power_net(board, p.net):
                    label = comp.part or "buffer"
                    out.append((p.number, f"{comp.refdes} ({label}) {pin.name}→{p.name}"))
            if out:
                break
        return out
    return []
