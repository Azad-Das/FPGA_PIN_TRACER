"""Test report for multi-board designs: what reaches the FPGA, and where the rest stops.

    python tools/trace_report.py fpga.pdf anakart.pdf apollo.pdf --component apollo:TD3
    python tools/trace_report.py a.pdf b.pdf c.pdf --component c:TD3 --mate a:KN5=b:KN2 --out rapor.txt

Board names are the file names without .pdf. Connectors are auto-mated with the same rule as the app
(score >= 0.6 and >= 8 shared signal pins); --mate adds pairs by hand.

For every signal pin of each --component the report says:
  * OK      - FPGA pin, bank and the path (one line per hop)
  * LIMIT   - reached only when the 12-hop limit is lifted (the app currently drops these silently)
  * STOP    - not reached; lists where the search ended: connectors without a mate, mated connectors
              missing that pin, buffers/passives it could not pass, other ICs on the way
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fpga_pin_tracer import trace as trace_mod  # noqa: E402
from fpga_pin_tracer.mating import suggest_mates  # noqa: E402
from fpga_pin_tracer.model import Mate  # noqa: E402
from fpga_pin_tracer.parser import parse_pdf  # noqa: E402
from fpga_pin_tracer.roles import is_power_net, is_signal_pin, pass_through  # noqa: E402
from fpga_pin_tracer.trace import Design  # noqa: E402


def explore(d: Design, bname: str, ref: str, pin_no: str) -> dict:
    """Unlimited search from one pin; records why each branch ended (no FPGA logic, no hop limit)."""
    mates = d.mate_index()
    b0 = d.boards[bname]
    start_net = b0.components[ref].pins[pin_no].net
    seen = {(bname, start_net)}
    q = deque([(bname, start_net, 0, (ref, pin_no))])
    ev = Counter()
    deepest = (0, bname, start_net)
    while q:
        bn, net, depth, came = q.popleft()
        if depth > deepest[0]:
            deepest = (depth, bn, net)
        b = d.boards[bn]
        for r, p in b.nets.get(net, []):
            if (r, p) == came:
                continue
            c = b.components[r]
            if c.role == "fpga":
                ev[f"FPGA'ya ulaştı: {bn}/{r}.{p}"] += 1
                continue
            if c.role in ("passive", "buffer"):
                outs = pass_through(b, c, p)
                if not outs and not (c.refdes.startswith("C") or c.role == "passive" and len(c.pins) != 2):
                    if c.role == "buffer":
                        ev[f"buffer geçilemedi (pin eşi bulunamadı): {bn}/{r}.{p} [{c.pins[p].name}] {c.part}"] += 1
                for op, _ in outs:
                    on = c.pins[op].net
                    if (bn, on) not in seen and not is_power_net(b, on):
                        seen.add((bn, on))
                        q.append((bn, on, depth + 1, (r, op)))
            elif c.role == "connector":
                if (bn, r) not in mates:
                    ev[f"eşleşmesi olmayan konnektör: {bn}/{r}.{p} ({c.part or '-'})"] += 1
                    continue
                ob, oref = mates[(bn, r)]
                oc = d.boards[ob].components.get(oref)
                if not oc or p not in oc.pins:
                    ev[f"eşleşmiş konnektörde pin yok: {bn}/{r}.{p} -> {ob}/{oref}.{p}"] += 1
                    continue
                on = oc.pins[p].net
                if (ob, on) not in seen and not is_power_net(d.boards[ob], on):
                    seen.add((ob, on))
                    q.append((ob, on, depth + 1, (oref, p)))
            else:
                ev[f"başka entegre: {bn}/{r}.{p} ({c.part or '-'})"] += 1
    return {"events": ev, "deepest": deepest, "visited": len(seen)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdfs", nargs="+")
    ap.add_argument("--component", action="append", default=[], help="kart:refdes (birden çok verilebilir)")
    ap.add_argument("--mate", action="append", default=[], help="kartA:J1=kartB:J2 (elle eşleşme)")
    ap.add_argument("--no-auto-mate", action="store_true")
    ap.add_argument("--out", help="raporu dosyaya da yaz")
    a = ap.parse_args(argv)

    lines: list[str] = []
    w = lines.append
    d = Design()
    for p in a.pdfs:
        b = parse_pdf(p, name=os.path.splitext(os.path.basename(p))[0])
        d.add_board(b)
        roles = Counter(c.role for c in b.components.values())
        fpgas = [f"{c.refdes}({len(c.pins)} pin)" for c in b.components.values() if c.role == "fpga"]
        w(f"KART {b.name}: {b.stats()} | " + ", ".join(f"{k}={v}" for k, v in sorted(roles.items()))
          + (f" | FPGA: {', '.join(fpgas)}" if fpgas else ""))
    w("")
    w("KONNEKTÖR ÖNERİLERİ (puan, ortak sinyal pini, otomatik mi):")
    for s in suggest_mates(d.boards, threshold=0.3):
        m = s.mate
        w(f"  {m.board_a}/{m.ref_a} <-> {m.board_b}/{m.ref_b}  puan {s.score:.2f}  "
          f"{s.signal_pins} sinyal pini  {'OTOMATİK' if s.auto else 'öneri'}")
        if s.auto and not a.no_auto_mate:
            d.mates.append(m)
    for spec in a.mate:
        l, r = spec.split("=")
        ba, ra = l.split(":")
        bb, rb = r.split(":")
        d.mates.append(Mate(ba, ra, bb, rb))
    w("UYGULANAN EŞLEŞMELER: " + ("; ".join(f"{m.board_a}/{m.ref_a}<->{m.board_b}/{m.ref_b}" for m in d.mates)
                                  or "yok"))
    w("")

    for spec in a.component:
        bname, ref = spec.split(":", 1)
        if bname not in d.boards or ref not in d.boards[bname].components:
            w(f"BULUNAMADI: {spec}")
            continue
        b = d.boards[bname]
        c = b.components[ref]
        sig = [n for n in trace_mod._sorted_pins(c) if is_signal_pin(b, c, n)]
        trace_mod.MAX_HOPS = 12
        normal = {n: d.trace_pin(bname, ref, n) for n in sig}
        trace_mod.MAX_HOPS = 60
        lifted = {n: d.trace_pin(bname, ref, n) for n in sig}
        trace_mod.MAX_HOPS = 12
        ok = sum(1 for n in sig if normal[n])
        lim = sum(1 for n in sig if not normal[n] and lifted[n])
        w(f"BİLEŞEN {bname}/{ref} ({c.part}): {len(sig)} sinyal pini, FPGA'ya ulaşan {ok}, "
          f"yalnızca adım sınırı kalkınca ulaşan {lim}, ulaşmayan {len(sig) - ok - lim}")
        for n in sig:
            p = c.pins[n]
            head = f"  {n:<6} {p.name or '-':<20} net {p.net}"
            res = normal[n] or lifted[n]
            if res:
                tag = "OK   " if normal[n] else "LIMIT"
                for r in res:
                    e = r.end
                    w(f"{tag} {head} -> {e.board}/{e.refdes} pin {e.pin} [{e.pin_name}] bank {r.bank or '-'}"
                      f" ({len(r.hops)} adım)")
                    for h in r.hops:
                        w(f"          ↳ {h.note or f'{h.board}/{h.refdes}.{h.pin}'} (net {h.net})")
            else:
                info = explore(d, bname, ref, n)
                dep, db, dn = info["deepest"]
                w(f"STOP  {head}  | en uzak: {db} net {dn} ({dep} adım, {info['visited']} net gezildi)")
                for msg, k in info["events"].most_common(6):
                    w(f"          · {msg}" + (f" x{k}" if k > 1 else ""))
        w("")

    text = "\n".join(lines)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    print(text)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
