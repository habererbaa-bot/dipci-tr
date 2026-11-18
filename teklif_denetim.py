"""
Teklif denetimi (Kemal, 18 Kas 2026: "doğuş çay 400 küsür.. hepsiburada 6500.. 12 tanesi 6500 bunları düzelt... bunlar rezalet").

Bir mağaza teklifinin ürüne yazılabilmesi için:
- Koli/çoklu paket (12 Paket, 8 Adet, 4'lü, 2x) ürün adında yoksa teklifte de olmamalı.
- Gramaj/hacim (500 g, 1 kg, 2 lt) ürünün gramajıyla %10 içinde aynı olmalı.

Kullanım (mevcut yanlış teklifleri temizlemek):  python teklif_denetim.py --temizle   (önce --dene ile sayıya bakılır)
"""
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Optional, Tuple


def _norm(t: str) -> str:
    t = (t or "").replace("İ", "i").replace("I", "ı").lower()
    for a, b in zip("ıüöçşğâ", "iuocsga"):
        t = t.replace(a, b)
    return t.replace("×", "x").replace("’", "'")


_COKLU = re.compile(r"(\d+)\s*'?\s*(?:li|lu)\b|(\d+)\s*(?:adet|paket|koli)\b|(\d+)\s*x(?![a-z])|\bx\s*(\d+)\b")
_PAKET = re.compile(r"(\d+(?:[.,]\d+)?)\s*(kg|gr|g|lt|l|ml|cl)\b")


def coklu(text: str) -> set:
    return {next(g for g in m.groups() if g) for m in _COKLU.finditer(_norm(text))} - {"1"}


def paket(text: str) -> Optional[Tuple[float, str]]:
    m = _PAKET.search(_norm(text))
    if not m:
        return None
    v, u = float(m.group(1).replace(",", ".")), m.group(2)
    return {"gr": (v / 1000, "kg"), "g": (v / 1000, "kg"), "kg": (v, "kg"), "ml": (v / 1000, "l"),
            "cl": (v / 100, "l")}.get(u, (v, "l"))


def ayni_paket(a: Optional[Tuple[float, str]], b: Optional[Tuple[float, str]]) -> bool:
    if not a or not b:
        return True
    return a[1] == b[1] and 0.9 <= b[0] / max(a[0], 1e-6) <= 1.1


def fazla_coklu(ana: str, teklif: str) -> bool:
    """Teklifte ürün adında olmayan bir koli/adet sayısı var mı? "20 adet 29 g" gibi iç adet (10 ve üstü) gramaj
    tutuyorsa koli sayılmaz (çay poşeti, kahve paketi)."""
    fazla = coklu(teklif) - coklu(ana)
    if not fazla:
        return False
    pa, pt = paket(ana), paket(teklif)
    return not (pa and pt and ayni_paket(pa, pt) and all(int(x) >= 10 for x in fazla))


def uyumlu(ana: str, teklif: str) -> bool:
    if fazla_coklu(ana, teklif):
        return False
    return ayni_paket(paket(ana), paket(teklif))


def temizle(uygula: bool) -> dict:
    kok = Path(__file__).resolve().parent
    sys.path.insert(0, str(kok))
    from memory_db import PriceMemoryDB
    db = PriceMemoryDB(str(kok / "data" / "dipci_platform.db"))
    c = db.conn
    urunler = c.execute("SELECT product_key, brand, family_name, category FROM canonical_products").fetchall()
    silinecek, etkilenen = [], set()
    for u in urunler:
        if u["category"] in ("teknoloji_beyaz_esya", "yapi_oto_bahce", "giyim_aksesuar"):
            continue
        ana = f"{u['brand']} {u['family_name']}"
        satirlar = c.execute("SELECT id, title FROM price_snapshots WHERE product_key = ?", (u["product_key"],)).fetchall()
        if not satirlar:
            continue
        ref = paket(ana)
        if not ref:  # ürün adında gramaj yok: tekliflerde en sık görülen tekli gramaj esas alınır
            say = Counter(p for p in (paket(s["title"] or "") for s in satirlar if not fazla_coklu(ana, s["title"] or "")) if p)
            ref = say.most_common(1)[0][0] if say else None
        for s in satirlar:
            t = s["title"] or ""
            if fazla_coklu(ana, t) or not ayni_paket(ref, paket(t)):
                silinecek.append(s["id"])
                etkilenen.add(u["product_key"])
    if uygula and silinecek:
        for i in range(0, len(silinecek), 500):
            parca = silinecek[i:i + 500]
            c.execute(f"DELETE FROM price_snapshots WHERE id IN ({','.join('?' * len(parca))})", parca)
        c.execute("DELETE FROM live_price_drops")
        c.commit()
        db.rebuild_catalog_index()
    return {"yanlis_teklif": len(silinecek), "etkilenen_urun": len(etkilenen), "uygulandi": uygula}


if __name__ == "__main__":
    import json
    print(json.dumps(temizle("--temizle" in sys.argv), ensure_ascii=False))
