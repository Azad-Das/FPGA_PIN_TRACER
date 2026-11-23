"""Board-level netlist data model shared by the parser, tracer, exporters and GUI."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Pin:
    number: str
    name: str = ""
    net: str = ""
    page: int = 0
    bank: str = ""          # FPGA bank, when known (set by roles.annotate_fpga)


@dataclass
class Component:
    refdes: str
    part: str = ""
    value: str = ""
    role: str = "other"     # fpga | connector | passive | buffer | power | other
    pins: dict[str, Pin] = field(default_factory=dict)
    pages: set[int] = field(default_factory=set)

    def pin_by_name(self, name: str) -> list[Pin]:
        return [p for p in self.pins.values() if p.name == name]


@dataclass
class Board:
    name: str
    source: str = ""
    components: dict[str, Component] = field(default_factory=dict)
    nets: dict[str, list[tuple[str, str]]] = field(default_factory=dict)   # net -> [(refdes, pin)]
    bank_voltage: dict[tuple[str, str], str] = field(default_factory=dict)  # (fpga refdes, bank) -> net
    warnings: list[str] = field(default_factory=list)

    def rebuild_nets(self) -> None:
        nets: dict[str, list[tuple[str, str]]] = {}
        for comp in self.components.values():
            for pin in comp.pins.values():
                if pin.net:
                    nets.setdefault(pin.net, []).append((comp.refdes, pin.number))
        self.nets = nets

    def stats(self) -> str:
        n_pins = sum(len(c.pins) for c in self.components.values())
        return f"{len(self.components)} bileşen, {n_pins} pin, {len(self.nets)} net"


@dataclass(frozen=True)
class Mate:
    """Two connectors on different boards that plug into each other (pin N <-> pin N)."""

    board_a: str
    ref_a: str
    board_b: str
    ref_b: str


@dataclass
class Hop:
    board: str
    refdes: str
    pin: str
    pin_name: str
    net: str
    part: str = ""
    role: str = ""
    note: str = ""          # e.g. "seviye çevirici A1→B1", "konektör J1 ⇄ J17"


@dataclass
class TraceResult:
    start: Hop
    end: Hop | None                     # FPGA pin, or None when the trace dead-ends
    hops: list[Hop] = field(default_factory=list)   # intermediate hops, in order
    bank: str = ""
    bank_voltage: str = ""

    @property
    def via_summary(self) -> str:
        return " → ".join(h.note or f"{h.refdes}.{h.pin}" for h in self.hops if h.note)
