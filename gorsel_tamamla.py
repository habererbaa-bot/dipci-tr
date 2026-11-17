"""
Görseli olmayan ürünlere marketfiyati.org.tr'den görsel bulur (Kemal, 17 Kas 2026: "zeytinyağı ne yahu neden görseli yok").
Yavaş tarar (istekler arası 1,5 sn). Yalnız ad kelimelerinin çoğu tutan ürünün görseli alınır.
Kullanım: python gorsel_tamamla.py [en_fazla]
"""
import json
import math
import re
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

DB = Path(__file__).resolve().parent / "data" / "dipci_platform.db"
API = "https://api.marketfiyati.org.tr/api/v2/search"


def norm(t: str) -> str:
    t = (t or "").replace("İ", "i").replace("I", "ı").lower()
    for a, b in (("ı", "i"), ("ş", "s"), ("ğ", "g"), ("ü", "u"), ("ö", "o"), ("ç", "c"), ("é", "e")):
        t = t.replace(a, b)
    return re.sub(r"[^a-z0-9 ]+", " ", t)


def ara(q: str):
    req = urllib.request.Request(API, data=json.dumps({"keywords": q, "pages": 0, "size": 24}).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (compatible; DipciGorsel/1.0; +https://dipci.tr)"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.loads(r.read().decode("utf-8", "replace")).get("content") or []


def main(limit: int = 400):
    c = sqlite3.connect(str(DB), timeout=30)
    c.row_factory = sqlite3.Row
    rows = c.execute("""SELECT product_key, brand, family_name FROM product_catalog_index
                        WHERE COALESCE(image_url,'') = '' ORDER BY offer_count DESC LIMIT ?""", (limit,)).fetchall()
    bulunan = 0
    for r in rows:
        ad = f"{r['brand']} {r['family_name']}".strip()
        toks = [t for t in norm(ad).split() if len(t) >= 3 and not re.search(r"\d", t)]
        if not toks:
            continue
        need = max(2, math.ceil(len(toks) * 0.6)) if len(toks) >= 2 else 1
        img = ""
        for q in (ad, " ".join(ad.split()[:4])):
            try:
                items = ara(q)
            except Exception:
                items = []
            time.sleep(1.5)
            best = (0, "")
            for it in items:
                u = str(it.get("imageUrl") or "").strip()
                if not u.startswith("http"):
                    continue
                tn = norm(f"{it.get('brand') or ''} {it.get('title') or ''}")
                m = sum(1 for t in toks if t in tn)
                if m >= need and m > best[0]:
                    best = (m, u)
            if best[1]:
                img = best[1]
                break
        if img:
            c.execute("UPDATE canonical_products SET image_url = ? WHERE product_key = ? AND COALESCE(image_url,'') = ''", (img, r["product_key"]))
            c.execute("UPDATE product_catalog_index SET image_url = ? WHERE product_key = ?", (img, r["product_key"]))
            c.commit()
            bulunan += 1
    print(json.dumps({"denenen": len(rows), "gorsel_bulunan": bulunan}))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 400)
