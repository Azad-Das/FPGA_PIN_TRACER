"""Follow signals from a component pin to FPGA pins, across passives, buffers and board connectors."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .model import Board, Component, Hop, Mate, TraceResult
from .roles import SUPPLY_PIN_RE, annotate, is_power_net, is_signal_pin, pass_through

MAX_HOPS = 12


@dataclass
class Design:
    boards: dict[str, Board] = field(default_factory=dict)
    mates: list[Mate] = field(default_factory=list)

    def add_board(self, board: Board) -> None:
        annotate(board)
        self.boards[board.name] = board

    def mate_index(self) -> dict[tuple[str, str], tuple[str, str]]:
        idx: dict[tuple[str, str], tuple[str, str]] = {}
        for m in self.mates:
            idx[(m.board_a, m.ref_a)] = (m.board_b, m.ref_b)
            idx[(m.board_b, m.ref_b)] = (m.board_a, m.ref_a)
        return idx

    # ------------------------------------------------------------------ tracing
    def trace_pin(self, board_name: str, refdes: str, pin_no: str) -> list[TraceResult]:
        board = self.boards[board_name]
        comp = board.components[refdes]
        pin = comp.pins[pin_no]
        start = _hop(board, comp, pin_no)
        if comp.role == "fpga" or is_power_net(board, pin.net):
            return []
        mates = self.mate_index()
        results: list[TraceResult] = []
        seen: set[tuple[str, str]] = {(board_name, pin.net)}
        # queue items: (board, net, hops so far, (refdes, pin) we arrived through)
        queue = deque([(board_name, pin.net, [], (refdes, pin_no))])
        if comp.role == "connector" and (board_name, refdes) in mates:
            # a connector start also continues through its mate
            ob, oref = mates[(board_name, refdes)]
            ocomp = self.boards[ob].components.get(oref)
            if ocomp and pin_no in ocomp.pins:
                onet = ocomp.pins[pin_no].net
                if (ob, onet) not in seen and not is_power_net(self.boards[ob], onet):
                    seen.add((ob, onet))
                    hop = _hop(self.boards[ob], ocomp, pin_no,
                               f"{board_name}/{refdes}.{pin_no} ⇄ {ob}/{oref}.{pin_no}")
                    queue.append((ob, onet, [hop], (oref, pin_no)))
        while queue:
            bname, net, hops, came = queue.popleft()
            b = self.boards[bname]
            for ref, pno in b.nets.get(net, []):
                if (ref, pno) == came:
                    continue
                c = b.components[ref]
                if c.role == "fpga":
                    p = c.pins[pno]
                    if SUPPLY_PIN_RE.match(p.name or ""):
                        continue
                    end = _hop(b, c, pno)
                    results.append(TraceResult(start, end, list(hops), p.bank,
                                               b.bank_voltage.get((ref, p.bank), "")))
                    continue
                if len(hops) >= MAX_HOPS:
                    continue
                if c.role in ("passive", "buffer"):
                    for out_pin, note in pass_through(b, c, pno):
                        onet = c.pins[out_pin].net
                        if (bname, onet) in seen or is_power_net(b, onet):
                            continue
                        seen.add((bname, onet))
                        queue.append((bname, onet, hops + [_hop(b, c, pno, note)], (ref, out_pin)))
                elif c.role == "connector" and (bname, ref) in mates:
                    ob, oref = mates[(bname, ref)]
                    ocomp = self.boards[ob].components.get(oref)
                    if not ocomp or pno not in ocomp.pins:
                        continue
                    onet = ocomp.pins[pno].net
                    if (ob, onet) in seen or is_power_net(self.boards[ob], onet):
                        continue
                    seen.add((ob, onet))
                    note = f"{bname}/{ref}.{pno} ⇄ {ob}/{oref}.{pno}"
                    queue.append((ob, onet, hops + [_hop(b, c, pno, note)], (oref, pno)))
        return results

    def trace_component(self, board_name: str, refdes: str,
                        include_unconnected: bool = False) -> list[TraceResult]:
        board = self.boards[board_name]
        comp = board.components[refdes]
        out: list[TraceResult] = []
        for pin_no in _sorted_pins(comp):
            if not is_signal_pin(board, comp, pin_no):
                continue
            res = self.trace_pin(board_name, refdes, pin_no)
            if res:
                out.extend(res)
            elif include_unconnected:
                out.append(TraceResult(_hop(board, comp, pin_no), None))
        return out

    def peripherals(self) -> list[tuple[str, str, int]]:
        """(board, refdes, number of FPGA connections) for every non-FPGA part that reaches an FPGA."""
        out = []
        for bname, board in self.boards.items():
            for ref, comp in board.components.items():
                if comp.role in ("fpga", "passive"):
                    continue
                n = len(self.trace_component(bname, ref))
                if n:
                    out.append((bname, ref, n))
        out.sort(key=lambda x: (x[0], _natural(x[1])))
        return out


def _hop(board: Board, comp: Component, pin_no: str, note: str = "") -> Hop:
    p = comp.pins[pin_no]
    return Hop(board.name, comp.refdes, pin_no, p.name, p.net, comp.part, comp.role, note)


def _natural(s: str):
    import re
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def _sorted_pins(comp: Component) -> list[str]:
    return sorted(comp.pins, key=_natural)
