"""CSV and TXT exports of trace results."""

from __future__ import annotations

import csv
import io

from .model import TraceResult

CSV_COLUMNS = [
    "source_board", "source_ref", "source_part", "source_pin", "source_pin_name", "source_net",
    "fpga_board", "fpga_ref", "fpga_ball", "fpga_pin_name", "fpga_bank", "bank_voltage", "fpga_net",
    "hop_count", "path",
]


def _path_text(r: TraceResult) -> str:
    return " -> ".join(h.note for h in r.hops if h.note)


def to_csv(results: list[TraceResult], include_path: bool = True) -> str:
    buf = io.StringIO()
    cols = CSV_COLUMNS if include_path else CSV_COLUMNS[:-2]
    w = csv.writer(buf)
    w.writerow(cols)
    for r in results:
        s, e = r.start, r.end
        row = [s.board, s.refdes, s.part, s.pin, s.pin_name, s.net,
               e.board if e else "", e.refdes if e else "", e.pin if e else "",
               e.pin_name if e else "", r.bank, r.bank_voltage, e.net if e else ""]
        if include_path:
            row += [len(r.hops), _path_text(r)]
        w.writerow(row)
    return buf.getvalue()


def to_txt(results: list[TraceResult], include_path: bool = True) -> str:
    lines: list[str] = []
    last_comp = None
    for r in results:
        s, e = r.start, r.end
        comp = (s.board, s.refdes)
        if comp != last_comp:
            if lines:
                lines.append("")
            title = f"{s.board} / {s.refdes}" + (f" ({s.part})" if s.part else "")
            lines += [title, "=" * len(title)]
            last_comp = comp
        src = f"{s.pin_name or s.pin} (pin {s.pin}, net {s.net})"
        if e is None:
            lines.append(f"  {src}  →  FPGA'ya ulaşmıyor")
            continue
        bank = f", Bank {r.bank}" if r.bank else ""
        volt = f" ({r.bank_voltage})" if r.bank_voltage else ""
        dst = f"{e.board} / {e.refdes} ball {e.pin} [{e.pin_name}]{bank}{volt}"
        lines.append(f"  {src}  →  {dst}")
        if include_path:
            for h in r.hops:
                if h.note:
                    lines.append(f"      ↳ {h.note}  (net {h.net})")
    return "\n".join(lines) + "\n"
