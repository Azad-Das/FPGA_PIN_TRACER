"""Measure the generic geometric parser against Altium's hidden pin markers.

Usage: python tools/compare_markers.py <altium-exported schematic.pdf>

Altium PDFs carry invisible "PI<ref>0<pin>" text at every pin, which gives an exact pin -> wire
assignment. Parsing the same PDF with markers disabled and comparing the two shows how well the
tool-independent parser does. Two-pin passives are compared as unordered pairs.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fpga_pin_tracer.parser import parse_pdf  # noqa: E402


def norm(net: str) -> str:
    return "" if net.startswith("N$") else net


def main(path: str) -> None:
    truth = parse_pdf(path, use_markers=True)
    gen = parse_pdf(path, use_markers=False)
    print("markers:", truth.stats())
    print("generic:", gen.stats())
    total = found = agree = 0
    missing = Counter()
    diffs = []
    for ref, comp in truth.components.items():
        g = gen.components.get(ref)
        if len(comp.pins) == 2 and g and len(g.pins) == 2:
            t_nets = sorted(norm(p.net) for p in comp.pins.values())
            g_nets = sorted(norm(p.net) for p in g.pins.values())
            total += 2
            found += 2
            agree += sum(1 for a, b in zip(t_nets, g_nets) if a == b or not a)
            if t_nets != g_nets and all(t_nets):
                diffs.append((ref, "pair", t_nets, g_nets))
            continue
        for num, pin in comp.pins.items():
            total += 1
            gp = g.pins.get(num) if g else None
            if gp is None:
                missing[ref] += 1
                continue
            found += 1
            if norm(pin.net) == norm(gp.net) or not norm(pin.net):
                agree += 1
            else:
                diffs.append((ref, num, pin.net, gp.net))
    print(f"pins: {total}, found by generic: {found} ({100 * found / total:.1f}%), "
          f"same net: {agree} ({100 * agree / max(found, 1):.1f}% of found)")
    print("most missing:", missing.most_common(12))
    for d in diffs[:30]:
        print("  diff", d)


if __name__ == "__main__":
    main(sys.argv[1])
