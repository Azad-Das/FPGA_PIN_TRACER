"""Explain why a schematic PDF yields few or no components, without revealing its content.

Prints only counts, font types and tool names (no net names, part numbers or text), so the output
can be shared even when the schematic itself cannot.

    python -m fpga_pin_tracer.diagnose kart.pdf [kart2.pdf ...]
"""

from __future__ import annotations

import sys
from collections import Counter

import pymupdf

from .extract import extract_page


def _page_report(page: pymupdf.Page) -> dict:
    r: dict = {}
    raw = page.get_text("rawdict", flags=pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES)
    chars = hidden = bad = 0
    for b in raw["blocks"]:
        for ln in b.get("lines", []):
            for sp in ln["spans"]:
                for c in sp["chars"]:
                    chars += 1
                    if sp.get("alpha", 255) == 0:
                        hidden += 1
                    ch = c["c"]
                    if ch == "�" or (not ch.isprintable() and not ch.isspace()):
                        bad += 1
    r["chars"], r["hidden"], r["bad"] = chars, hidden, bad
    r["words"] = len(page.get_text("words"))
    area = page.rect.width * page.rect.height or 1
    img_area = 0.0
    imgs = page.get_image_info()
    for im in imgs:
        bb = pymupdf.Rect(im["bbox"]) & page.rect
        img_area += bb.width * bb.height
    r["images"], r["img_cover"] = len(imgs), min(1.0, img_area / area)
    drs = page.get_drawings()
    kinds = Counter()
    for d in drs:
        for it in d["items"]:
            kinds[it[0]] += 1
    r["drawings"], r["items"] = len(drs), dict(kinds)
    pp = extract_page(page)
    r["cadence"], r["segs"], r["rects"], r["graphics"] = pp.cadence, len(pp.segs), len(pp.rects), len(pp.graphics)
    r["fonts"] = sorted({f"{f[2]}" for f in page.get_fonts()})
    return r


def diagnose(path: str, out=sys.stdout) -> None:
    w = lambda s="": print(s, file=out)  # noqa: E731
    try:
        doc = pymupdf.open(path)
    except Exception as e:  # noqa: BLE001
        w(f"{path}: açılamadı ({type(e).__name__}: {e})")
        return
    w(f"=== {path.replace(chr(92), '/').split('/')[-1]}")
    md = doc.metadata or {}
    w(f"sayfa: {doc.page_count}   oluşturan: {md.get('creator') or '-'}   üreten: {md.get('producer') or '-'}")
    w(f"şifreli: {'evet' if doc.is_encrypted else 'hayır'}   izin gerekli: {'evet' if doc.needs_pass else 'hayır'}")
    if doc.needs_pass:
        w("SONUÇ: PDF parola istiyor; parolasız bir kopya kaydedip tekrar deneyin.")
        return
    tot = Counter()
    fonts: set[str] = set()
    w("sayfa | karakter (gizli, bozuk) | kelime | çizim (çizgi/eğri/dikdörtgen) | resim (kaplama) | stil | tel/gövde/grafik")
    for i, page in enumerate(doc):
        r = _page_report(page)
        it = r["items"]
        w(f"{i + 1:>5} | {r['chars']:>6} ({r['hidden']}, {r['bad']}) | {r['words']:>5} | "
          f"{r['drawings']:>5} ({it.get('l', 0)}/{it.get('c', 0)}/{it.get('re', 0) + it.get('qu', 0)}) | "
          f"{r['images']} ({r['img_cover']:.0%}) | {'Cadence' if r['cadence'] else 'genel'} | "
          f"{r['segs']}/{r['rects']}/{r['graphics']}")
        for k in ("chars", "hidden", "bad", "words", "drawings", "images", "segs", "rects", "graphics"):
            tot[k] += r[k]
        tot["l"] += it.get("l", 0)
        tot["c"] += it.get("c", 0)
        tot["img_pages"] += r["img_cover"] > 0.5
        tot["cad_pages"] += r["cadence"]
        fonts.update(r["fonts"])
    w(f"font türleri: {', '.join(sorted(fonts)) or 'yok'}")
    n = doc.page_count or 1
    w("SONUÇ:")
    if tot["chars"] == 0 and tot["img_pages"] >= n / 2:
        w("  PDF'te metin yok ve sayfalar resimle kaplı: taranmış/resim PDF. Vektör PDF olarak yeniden")
        w("  dışa aktarılmalı (EDA aracından 'Export PDF') ya da OCR gerekir.")
    elif tot["chars"] == 0 and tot["c"] > 20 * n:
        w("  Metin yok ama çok sayıda eğri var: yazılar eğriye çevrilmiş (outline/'text as curves').")
        w("  EDA aracında PDF dışa aktarırken metni metin olarak bırakın (TrueType/gömülü font).")
    elif tot["chars"] == 0:
        w("  PDF'te okunabilir metin yok. Vektör, metinli bir PDF gerekir.")
    elif tot["hidden"] > 0.95 * tot["chars"]:
        w("  Metnin tamamı görünmez katmanda; harfler çizgiyle çizilmiş (ör. SCH2PDF). Uygulama bu")
        w("  durumda görünmez katmanı metin olarak kullanır ve harf çizgilerini atar.")
    elif tot["bad"] > 0.3 * tot["chars"]:
        w("  Metin var ama karakterlerin çoğu çözülemiyor (font Unicode eşlemesi yok, çoğunlukla Type3).")
        w("  Farklı font ayarıyla yeniden dışa aktarmak gerekir.")
    elif tot["segs"] == 0:
        w("  Metin var ama tel olarak kullanılabilecek yatay/dikey çizgi bulunamadı.")
    elif tot["rects"] == 0:
        w("  Metin ve teller var ama sembol gövdesi (dikdörtgen) bulunamadı; çizim tarzı tanınmıyor.")
    else:
        w("  Temel yapı okunuyor.")
    # parse summary: counts and roles only, no names
    try:
        from collections import Counter as _C

        from .parser import parse_pdf
        from .roles import annotate
        b = parse_pdf(path)
        annotate(b)
        roles = _C(c.role for c in b.components.values())
        pins = sum(len(c.pins) for c in b.components.values())
        named = sum(1 for c in b.components.values() for p in c.pins.values() if not p.net.startswith("N$"))
        big = sorted((len(c.pins) for c in b.components.values() if c.role == "fpga"), reverse=True)
        w(f"ÇÖZÜMLEME: {len(b.components)} bileşen, {pins} pin ({named} pin etiketli nette), {len(b.nets)} net")
        w("  roller: " + ", ".join(f"{k}={v}" for k, v in sorted(roles.items())) +
          (f"   FPGA pin sayısı: {big}" if big else "   FPGA bulunamadı"))
    except Exception as e:  # noqa: BLE001
        w(f"ÇÖZÜMLEME HATASI: {type(e).__name__}: {e}")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print(__doc__)
        return 1
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    for p in args:
        diagnose(p)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
