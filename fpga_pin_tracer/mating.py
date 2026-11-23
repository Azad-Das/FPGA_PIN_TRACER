"""Suggest which connectors on different boards plug into each other.

Each connector pair (A on one board, B on another) is scored from four independent clues:

* part numbers  - known mating pairs (e.g. Samtec FMC carrier/mezzanine) or the same family
* pin numbers   - how many pin numbers both connectors use (FMC: A1..K40, VPX: rows/wafers)
* signal names  - on each shared pin number, do the net / pin names match once normalised
                  ("FMC-LA01_CC_P" on the carrier and "LA01_P_CC" on the card are the same)?
* power pattern - ground and supply pins must line up on both sides

The best-scoring pairs are offered to the user, who confirms or edits them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .model import Board, Mate
from .roles import GROUND_PIN_RE, is_power_net

# Samtec FMC: carrier (on the FPGA board) <-> mezzanine (on the plug-in card)
FMC_CARRIER = ("ASP-134486", "ASP-134603", "ASP-186082", "ASP-184329")   # HPC, LPC, FMC+ HSPC
FMC_MEZZ = ("ASP-134488", "ASP-134602", "ASP-186083", "ASP-188588", "ASP-184330")
KNOWN_FAMILIES = [
    ("FMC", FMC_CARRIER, FMC_MEZZ),
    ("VPX", ("MULTIGIG", "1410187", "1410140", "VPX"), ("MULTIGIG", "1410186", "1410142", "VPX")),
    ("Samtec SEAF/SEAM", ("SEAF",), ("SEAM",)),
    ("Samtec QSH/QTH", ("QSH",), ("QTH",)),
    ("Samtec QSE/QTE", ("QSE",), ("QTE",)),
    ("Samtec ERF8/ERM8", ("ERF8",), ("ERM8",)),
    ("Samtec SSW/TSW", ("SSW", "SSQ", "SLW"), ("TSW", "TSM", "HTSW")),
]
DROP_TOKENS = {"FMC", "FMC1", "FMC2", "HPC", "LPC", "HSPC", "CC", "VPX", "P0", "P1", "J1", "J2", "CONN"}


@dataclass
class MateSuggestion:
    mate: Mate
    score: float
    reasons: list[str]


def norm_signal(name: str) -> str:
    toks = [t for t in re.split(r"[_\-./\s]+", (name or "").upper()) if t]
    toks = [t for t in toks if t not in DROP_TOKENS]
    return "_".join(toks)


def _family(part: str) -> tuple[str, str] | None:
    p = (part or "").upper()
    for fam, side_a, side_b in KNOWN_FAMILIES:
        if any(k in p for k in side_a):
            return fam, "a"
        if any(k in p for k in side_b):
            return fam, "b"
    return None


def score_pair(ba: Board, ra: str, bb: Board, rb: str) -> MateSuggestion:
    ca, cb = ba.components[ra], bb.components[rb]
    reasons: list[str] = []
    score = 0.0
    fa, fb = _family(ca.part), _family(cb.part)
    if fa and fb and fa[0] == fb[0]:
        if fa[1] != fb[1]:
            score += 0.35
            reasons.append(f"eş parça numaraları ({fa[0]}: {ca.part} ↔ {cb.part})")
        else:
            score += 0.15
            reasons.append(f"aynı konektör ailesi ({fa[0]})")
    elif ca.part and ca.part == cb.part:
        score += 0.1
        reasons.append(f"aynı parça numarası ({ca.part})")

    pa, pb = set(ca.pins), set(cb.pins)
    common = pa & pb
    if not common:
        return MateSuggestion(Mate(ba.name, ra, bb.name, rb), 0.0, ["ortak pin numarası yok"])
    jacc = len(common) / len(pa | pb)
    score += 0.2 * jacc
    reasons.append(f"ortak pin numarası: {len(common)} ({jacc:.0%})")

    sig_total = sig_match = pwr_total = pwr_match = 0
    for n in common:
        a, b = ca.pins[n], cb.pins[n]
        a_pwr = is_power_net(ba, a.net) or bool(GROUND_PIN_RE.match(a.name or ""))
        b_pwr = is_power_net(bb, b.net) or bool(GROUND_PIN_RE.match(b.name or ""))
        if a_pwr or b_pwr:
            pwr_total += 1
            pwr_match += a_pwr == b_pwr
            continue
        names_a = {norm_signal(x) for x in (a.net, a.name) if x and not x.startswith("N$")}
        names_b = {norm_signal(x) for x in (b.net, b.name) if x and not x.startswith("N$")}
        names_a.discard("")
        names_b.discard("")
        if not names_a or not names_b:
            continue
        sig_total += 1
        sig_match += bool(names_a & names_b)
    if sig_total:
        r = sig_match / sig_total
        score += 0.35 * r
        reasons.append(f"sinyal adı eşleşmesi: {sig_match}/{sig_total} ({r:.0%})")
    if pwr_total:
        r = pwr_match / pwr_total
        score += 0.1 * r
        reasons.append(f"güç/toprak deseni: {pwr_match}/{pwr_total}")
    return MateSuggestion(Mate(ba.name, ra, bb.name, rb), round(score, 3), reasons)


def suggest_mates(boards: dict[str, Board], threshold: float = 0.35) -> list[MateSuggestion]:
    names = list(boards)
    cands: list[MateSuggestion] = []
    for i, na in enumerate(names):
        for nb in names[i + 1:]:
            ba, bb = boards[na], boards[nb]
            ca = [r for r, c in ba.components.items() if c.role == "connector" and len(c.pins) >= 2]
            cb = [r for r, c in bb.components.items() if c.role == "connector" and len(c.pins) >= 2]
            for ra in ca:
                for rb in cb:
                    s = score_pair(ba, ra, bb, rb)
                    if s.score >= threshold:
                        cands.append(s)
    cands.sort(key=lambda s: -s.score)
    used: set[tuple[str, str]] = set()
    out = []
    for s in cands:
        ka, kb = (s.mate.board_a, s.mate.ref_a), (s.mate.board_b, s.mate.ref_b)
        if ka in used or kb in used:
            continue
        used.update((ka, kb))
        out.append(s)
    return out
