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
    if not (pa and pt and ayni_paket(pa, pt)):
        return True
    # İç adet sayılması için sayı gramajdan ÖNCE gelmeli ("20 Adet 29 Gr", "8 Adet 100 gr", "3'lü 465 Gr"); sonra geliyorsa kolidir
    # ("1 Kg 12 Paket", "200 ml 27 Adet"). "paket/koli" her zaman kolidir.
    n = _norm(teklif)
    gram_yer = _PAKET.search(n).start()
    for m in _COKLU.finditer(n):
        sayi = next(g for g in m.groups() if g)
        if sayi in fazla and (m.start() > gram_yer or re.search(r"paket|koli", m.group(0))):
            return True
    return False


def uyumlu(ana: str, teklif: str) -> bool:
    if fazla_coklu(ana, teklif):
        return False
    return ayni_paket(paket(ana), paket(teklif))


def koli_adedi(ana: str, teklif: str) -> int:
    """Teklif bu ürünün kolisi mi (ör. "1 Kg 12 Paket")? Öyleyse içindeki adet, değilse 0.
    Kemal, 18 Kas 2026: "12 satan 10 satan belki de daha hesaplı birim maliyet açısından" — koli silinmez, ayrı gösterilir."""
    if not fazla_coklu(ana, teklif):
        return 0
    pa = paket(ana)
    # Koli başlığındaki tek ürün gramajı ana ürünle aynı olmalı ("6x330 ml" 1,98 Lt'nin kolisi değildir)
    if pa and paket(teklif) and not ayni_paket(pa, paket(teklif)):
        return 0
    adet = min(int(x) for x in coklu(teklif) - coklu(ana))
    # Koli en çok 24'lüdür; üründe zaten adet yazıyorsa (bebek bezi "74 Adet") başka büyük sayı paket boyudur, koli değil
    if adet > 24 or (coklu(ana) and adet >= 10):
        return 0
    return adet if adet >= 2 else 0


def koli_tablosunu_denetle() -> dict:
    import sqlite3
    c = sqlite3.connect(str(Path(__file__).resolve().parent / "data" / "dipci_platform.db"))
    c.row_factory = sqlite3.Row
    sil = [(r["product_key"], r["platform"], r["adet"]) for r in c.execute(
        "SELECT k.product_key, k.platform, k.adet, k.title, p.brand, p.family_name FROM koli_teklifleri k "
        "JOIN canonical_products p USING(product_key)").fetchall()
        if koli_adedi(f"{r['brand']} {r['family_name']}", r["title"] or "") != r["adet"]]
    c.executemany("DELETE FROM koli_teklifleri WHERE product_key=? AND platform=? AND adet=?", sil)
    c.commit()
    return {"silinen_hatali_koli": len(sil), "kalan": c.execute("SELECT COUNT(*) FROM koli_teklifleri").fetchone()[0]}


KOLI_TABLO = """CREATE TABLE IF NOT EXISTS koli_teklifleri (
    product_key TEXT NOT NULL, platform TEXT NOT NULL, seller_name TEXT, title TEXT, price REAL NOT NULL,
    adet INTEGER NOT NULL, birim_fiyat REAL NOT NULL, url TEXT, captured_at TEXT NOT NULL,
    PRIMARY KEY (product_key, platform, adet))"""


def koli_kaydet(conn, product_key: str, platform: str, seller: str, title: str, price: float, adet: int, url: str,
                zaman: str = "") -> None:
    from datetime import datetime
    conn.execute(KOLI_TABLO)
    conn.execute("INSERT OR REPLACE INTO koli_teklifleri VALUES (?,?,?,?,?,?,?,?,?)",
                 (product_key, platform, seller, title, round(price, 2), adet, round(price / adet, 2), url,
                  zaman or datetime.now().isoformat()))


def yedekten_koliler(yedek: str) -> dict:
    """Daha önce silinen koli tekliflerini yedek veritabanından koli tablosuna geri alır."""
    import sqlite3
    kok = Path(__file__).resolve().parent
    c = sqlite3.connect(str(kok / "data" / "dipci_platform.db"))
    c.row_factory = sqlite3.Row
    c.execute(f"ATTACH DATABASE ? AS y", (yedek,))
    c.execute(KOLI_TABLO)
    rows = c.execute("""SELECT s.*, p.brand, p.family_name FROM y.price_snapshots s JOIN canonical_products p USING(product_key)
                        WHERE s.id NOT IN (SELECT id FROM main.price_snapshots) ORDER BY s.captured_at""").fetchall()
    n = 0
    for r in rows:
        adet = koli_adedi(f"{r['brand']} {r['family_name']}", r["title"] or "")
        if adet:
            koli_kaydet(c, r["product_key"], r["platform"], r["seller_name"], r["title"], float(r["effective_pack_price"]),
                        adet, r["url"] or "", r["captured_at"])
            n += 1
    c.commit()
    return {"yedekte_silinen": len(rows), "koliye_alinan": n,
            "koli_toplam": c.execute("SELECT COUNT(*) FROM koli_teklifleri").fetchone()[0]}


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
    if "--koli-denetle" in sys.argv:
        print(json.dumps(koli_tablosunu_denetle(), ensure_ascii=False))
    elif "--yedekten" in sys.argv:
        print(json.dumps(yedekten_koliler(sys.argv[sys.argv.index("--yedekten") + 1]), ensure_ascii=False))
    else:
        print(json.dumps(temizle("--temizle" in sys.argv), ensure_ascii=False))
