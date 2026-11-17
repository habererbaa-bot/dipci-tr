"""
Kırık / boş ürün görsellerini onarır (Kemal, 17 Kas 2026: "YUD yazısı ile zeytinyağı ne, neden görseli yok").

marketfiyati CDN'indeki bazı görseller (özellikle carrefourimages/*.png) 404 veriyor. Bu araç:
1) Her ürünün görselini yoklar (HEAD/GET, yavaş: aynı anda en çok 6 istek).
2) Kırık ya da boşsa marketfiyati aramasından aynı ürünün çalışan başka bir görselini bulur.
3) canonical_products, product_catalog_index ve live_price_drops tablolarında günceller.
Kullanım: python gorsel_onar.py [--hepsi]   (varsayılan: önce indirim şeridi + ana sayfa ürünleri)
"""
import json
import re
import sqlite3
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DB = Path(__file__).resolve().parent / "data" / "dipci_platform.db"
API = "https://api.marketfiyati.org.tr/api/v2/search"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _norm(t: str) -> str:
    t = (t or "").replace("İ", "i").replace("I", "ı").lower()
    for a, b in zip("ıüöçşğâ", "iuocsga"):
        t = t.replace(a, b)
    return re.sub(r"[^a-z0-9 ]+", " ", t)


def calisiyor(url: str) -> bool:
    if not url or not url.startswith("http"):
        return False
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": "bytes=0-1023"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status in (200, 206) and "image" in (r.headers.get("Content-Type") or "image")
    except Exception:
        return False


def yeni_gorsel(brand: str, family: str) -> str:
    sorgu = f"{brand} {family}".strip()
    hedef = [t for t in _norm(sorgu).split() if len(t) >= 3]
    for q in (sorgu, " ".join(sorgu.split()[:4])):
        try:
            req = urllib.request.Request(API, data=json.dumps({"keywords": q, "pages": 0, "size": 24}).encode(),
                                         headers={"Content-Type": "application/json", "User-Agent": UA}, method="POST")
            with urllib.request.urlopen(req, timeout=10) as r:
                items = json.loads(r.read().decode("utf-8", "replace")).get("content") or []
        except Exception:
            continue
        aday = []
        for it in items:
            tn = _norm(f"{it.get('brand') or ''} {it.get('title') or ''}")
            puan = sum(1 for t in hedef if t in tn)
            if hedef and puan >= max(2, int(len(hedef) * 0.7)):
                aday.append((puan, str(it.get("imageUrl") or "")))
        # Carrefour png'leri sık kırık: aynı puanda migros/a101/şok görseli öne
        aday.sort(key=lambda x: (-x[0], "carrefourimages" in x[1]))
        for _, u in aday[:6]:
            if calisiyor(u):
                return u
    return ""


def main():
    hepsi = "--hepsi" in sys.argv
    c = sqlite3.connect(str(DB), timeout=30)
    c.row_factory = sqlite3.Row
    if hepsi:
        rows = c.execute("SELECT product_key, brand, family_name, image_url FROM canonical_products").fetchall()
    else:
        rows = c.execute(
            """SELECT DISTINCT p.product_key, p.brand, p.family_name, p.image_url FROM canonical_products p
               WHERE p.product_key IN (SELECT product_key FROM live_price_drops)
                  OR p.product_key IN (SELECT product_key FROM product_catalog_index WHERE offer_count >= 2)"""
        ).fetchall()
    with ThreadPoolExecutor(max_workers=6) as ex:
        durum = list(ex.map(lambda r: calisiyor(r["image_url"]), rows))
    kirik = [r for r, ok in zip(rows, durum) if not ok]
    print(json.dumps({"yoklanan": len(rows), "kirik": len(kirik)}), flush=True)
    onarilan = 0
    for r in kirik:
        u = yeni_gorsel(r["brand"], r["family_name"])
        if not u:
            continue
        for tablo in ("canonical_products", "product_catalog_index", "live_price_drops"):
            try:
                c.execute(f"UPDATE {tablo} SET image_url = ? WHERE product_key = ?", (u, r["product_key"]))
            except sqlite3.OperationalError:
                pass
        c.commit()
        onarilan += 1
    print(json.dumps({"onarilan": onarilan, "bulunamayan": len(kirik) - onarilan}), flush=True)


if __name__ == "__main__":
    main()
