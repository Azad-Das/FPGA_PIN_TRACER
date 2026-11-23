"""Command line use without the GUI.

Examples:
  python -m fpga_pin_tracer.cli kart.pdf --list
  python -m fpga_pin_tracer.cli kart.pdf --component U12 --txt
  python -m fpga_pin_tracer.cli fpga.pdf sensor.pdf --auto-mate --component sensor:J1 --csv out.csv
  python -m fpga_pin_tracer.cli fpga.pdf sensor.pdf --mate fpga:J1=sensor:J17 --all --csv out.csv
"""

from __future__ import annotations

import argparse
import os
import sys

from .export import to_csv, to_txt
from .mating import suggest_mates
from .model import Mate
from .parser import parse_pdf
from .trace import Design


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fpga_pin_tracer.cli", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdfs", nargs="+", help="şematik PDF'leri (kart adı = dosya adı)")
    ap.add_argument("--list", action="store_true", help="FPGA'ya bağlı bileşenleri listele")
    ap.add_argument("--component", action="append", default=[], help="[kart:]refdes, birden çok verilebilir")
    ap.add_argument("--all", action="store_true", help="FPGA'ya bağlı tüm bileşenler")
    ap.add_argument("--mate", action="append", default=[], help="kartA:J1=kartB:J17")
    ap.add_argument("--auto-mate", action="store_true", help="konektör eşleşmelerini otomatik öner ve uygula")
    ap.add_argument("--no-path", action="store_true", help="ara katmanları yazma")
    ap.add_argument("--csv", metavar="DOSYA", help="CSV olarak kaydet ('-' = ekrana)")
    ap.add_argument("--txt", nargs="?", const="-", metavar="DOSYA", help="TXT olarak kaydet (varsayılan ekrana)")
    a = ap.parse_args(argv)

    d = Design()
    for p in a.pdfs:
        d.add_board(parse_pdf(p, name=os.path.splitext(os.path.basename(p))[0]))
    for b in d.boards.values():
        print(f"# {b.name}: {b.stats()}", file=sys.stderr)
    if a.auto_mate:
        for s in suggest_mates(d.boards):
            tag = "eşleşme" if s.auto else "öneri (uygulanmadı, --mate ile ekleyin)"
            if s.auto:
                d.mates.append(s.mate)
            print(f"# {tag}: {s.mate.board_a}/{s.mate.ref_a} <-> {s.mate.board_b}/{s.mate.ref_b} "
                  f"(puan {s.score:.2f}, {s.signal_pins} ortak sinyal pini)", file=sys.stderr)
    for m in a.mate:
        left, right = m.split("=")
        ba, ra = left.split(":")
        bb, rb = right.split(":")
        d.mates.append(Mate(ba, ra, bb, rb))

    if a.list:
        for bname, ref, n in d.peripherals():
            c = d.boards[bname].components[ref]
            print(f"{bname}\t{ref}\t{c.part}\t{c.role}\t{n}")
        return 0

    targets: list[tuple[str, str]] = []
    if a.all:
        targets = [(b, r) for b, r, _ in d.peripherals()]
    for spec in a.component:
        if ":" in spec:
            b, r = spec.split(":", 1)
            targets.append((b, r))
        else:
            hits = [(b, spec) for b, brd in d.boards.items() if spec in brd.components]
            if not hits:
                print(f"bileşen bulunamadı: {spec}", file=sys.stderr)
                return 2
            targets += hits
    if not targets:
        ap.error("--list, --all veya --component verin")
    for b, r in targets:
        if b not in d.boards or r not in d.boards[b].components:
            print(f"bulunamadı: {b}:{r} (kartlar: {', '.join(d.boards)})", file=sys.stderr)
            return 2
    results = [r for b, ref in targets for r in d.trace_component(b, ref)]
    path = not a.no_path
    if a.csv:
        text = to_csv(results, include_path=path)
        if a.csv == "-":
            sys.stdout.write(text)
        else:
            with open(a.csv, "w", encoding="utf-8-sig", newline="") as f:
                f.write(text)
    if a.txt or not a.csv:
        text = to_txt(results, include_path=path)
        if not a.txt or a.txt == "-":
            sys.stdout.write(text)
        else:
            with open(a.txt, "w", encoding="utf-8") as f:
                f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
