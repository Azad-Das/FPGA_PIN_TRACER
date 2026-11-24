"""Suggest which connectors on different boards plug into each other.

Each connector pair (A on one board, B on another) is scored from four independent clues:

* part numbers  - known mating pairs (e.g. Samtec FMC carrier/mezzanine) or the same family
* pin numbers   - how many pin numbers both connectors use (FMC: A1..K40, VPX: rows/wafers)
* signal names  - on each shared pin number, do the net / pin names match once normalised
                  ("FMC-LA01_CC_P" on the carrier and "LA01_P_CC" on the card are the same)?
* power pattern - ground and supply pins must line up on both sides

The best-scoring pairs are offered to the user, who confirms or edits them. A pair is applied
automatically only when it scores high AND shares enough signal pins to prove it: two RF connectors
of the same type (one signal pin plus ground) score well on every clue yet take a cable, not the
other board, so small pairs are only suggested.
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
    ("Samtec ADF6/ADM6", ("ADF6",), ("ADM6",)),
    ("Samtec SSW/TSW", ("SSW", "SSQ", "SLW"), ("TSW", "TSM", "HTSW")),
]
AUTO_MATE_SCORE = 0.6       # auto-apply: minimum score ...
MIN_AUTO_SIGNALS = 8        # ... and minimum shared signal (non power/ground) pins
SHOW_OVERLAP = 0.8          # always offer pairs sharing this much of their pin numbers (and enough
                            # signal pins), however low the score: board-to-board connectors with
                            # in-house part numbers and different net names on each board look like this
DROP_TOKENS = {"FMC", "FMC1", "FMC2", "HPC", "LPC", "HSPC", "CC", "VPX", "P0", "P1", "J1", "J2", "CONN"}


@dataclass
class MateSuggestion:
    mate: Mate
    score: float
    reasons: list[str]
    signal_pins: int = 0    # shared pin numbers that carry a signal on both sides
    ambiguous: bool = False  # another pairing of one of these connectors scores (almost) the same
    overlap: float = 0.0     # shared pin numbers / all pin numbers (Jaccard)

    @property
    def auto(self) -> bool:
        """Strong enough, and unambiguous enough, to apply without asking."""
        return self.score >= AUTO_MATE_SCORE and self.signal_pins >= MIN_AUTO_SIGNALS and not self.ambiguous


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


def _gender_pair(a: str, b: str) -> bool:
    """Part numbers that differ only in one F/M (female/male) letter: ADF6-60-.. / ADM6-60-.."""
    a, b = (a or "").upper(), (b or "").upper()
    if len(a) != len(b) or len(a) < 4:
        return False
    diff = [(x, y) for x, y in zip(a, b) if x != y]
    return len(diff) == 1 and set(diff[0]) == {"F", "M"}


def _live(board: Board, net: str) -> bool:
    return bool(net) and (not net.startswith("N$") or len(board.nets.get(net, [])) >= 2)


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
    elif _gender_pair(ca.part, cb.part):
        score += 0.25
        reasons.append(f"dişi/erkek eş parça olabilir ({ca.part} ↔ {cb.part})")
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

    sig_total = sig_match = pwr_total = pwr_match = sig_pins = 0
    for n in common:
        a, b = ca.pins[n], cb.pins[n]
        a_pwr = is_power_net(ba, a.net) or bool(GROUND_PIN_RE.match(a.name or ""))
        b_pwr = is_power_net(bb, b.net) or bool(GROUND_PIN_RE.match(b.name or ""))
        if a_pwr or b_pwr:
            pwr_total += 1
            pwr_match += a_pwr == b_pwr
            continue
        # a signal pin counts only when it carries something on both sides: a named net, or a
        # net that reaches another pin (a dead-end stub is no evidence of a mate)
        if _live(ba, a.net) and _live(bb, b.net):
            sig_pins += 1
        names_a ={norm_signal(x) for x in (a.net, a.name) if x and not x.startswith("N$")}
        names_b = {norm_signal(x) for x in (b.net, b.name) if x and not x.startswith("N$")}
        # a pin "named" by its own number (A14 / A14 on generic connector symbols) proves nothing
        for names in (names_a, names_b):
            names.discard("")
            names.discard(norm_signal(n))
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
    if sig_pins < MIN_AUTO_SIGNALS:
        reasons.append(f"yalnızca {sig_pins} ortak sinyal pini: otomatik uygulanmaz (RF/kablo konnektörü olabilir)")
    return MateSuggestion(Mate(ba.name, ra, bb.name, rb), round(score, 3), reasons, sig_pins, overlap=jacc)


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
                    if s.score >= threshold or (s.overlap >= SHOW_OVERLAP and s.signal_pins >= MIN_AUTO_SIGNALS):
                        cands.append(s)
    cands.sort(key=lambda s: -s.score)
    # identical connectors (KN2/KN3/KN4, same part, same pins) with no signal-name evidence score the
    # same against every partner: picking one would be a coin toss, so flag it for the user
    for s in cands:
        ends = {(s.mate.board_a, s.mate.ref_a), (s.mate.board_b, s.mate.ref_b)}
        for o in cands:
            if o is s or abs(o.score - s.score) > 0.02:
                continue
            if ends & {(o.mate.board_a, o.mate.ref_a), (o.mate.board_b, o.mate.ref_b)}:
                if not s.ambiguous:
                    s.reasons.append("belirsiz: aynı puanı alan başka bir eşleşme de var, elle seçin")
                s.ambiguous = True
                break
    used: set[tuple[str, str]] = set()
    out = []
    for s in cands:
        ka, kb = (s.mate.board_a, s.mate.ref_a), (s.mate.board_b, s.mate.ref_b)
        if ka in used or kb in used:
            if s.ambiguous:
                out.append(s)       # keep every equally good option visible in the dialog
            continue
        used.update((ka, kb))
        out.append(s)
    return out
