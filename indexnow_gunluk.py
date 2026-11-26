"""
Yeni ve fiyatı değişen ürünleri her gün IndexNow ile Bing ve Yandex'e bildirir (Kemal, 26 Kas 2026: "ürünlerin Google'da
daha hızlı çıkması için çalış"). Google IndexNow'u kullanmaz; Google'a site haritası (dipci-gsc) ve sayfa içi bağlantılar gider.
Önce karşılaştırmalı (2+ mağaza) ürünler, istek başına en çok 10.000 adres, günde en çok 5 istek.
Kullanım: python indexnow_gunluk.py [--gun 1]
"""
import json
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

KOK = Path(__file__).resolve().parent
sys.path.insert(0, str(KOK))
from memory_db import PriceMemoryDB  # noqa: E402
from seo_engine import CANONICAL_ORIGIN, INDEXNOW_KEY, key_to_slug  # noqa: E402


def gonder(adresler):
    govde = json.dumps({"host": "dipci.tr", "key": INDEXNOW_KEY, "keyLocation": f"{CANONICAL_ORIGIN}/{INDEXNOW_KEY}.txt",
                        "urlList": adresler}).encode("utf-8")
    req = urllib.request.Request("https://api.indexnow.org/indexnow", data=govde, method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": "dipci-indexnow/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:
        return str(e)[:80]


def main():
    gun = int(sys.argv[sys.argv.index("--gun") + 1]) if "--gun" in sys.argv else 1
    esik = (datetime.now() - timedelta(days=gun)).isoformat()
    db = PriceMemoryDB(str(KOK / "data" / "dipci_platform.db"))
    rows = db.conn.execute("SELECT product_key FROM product_catalog_index WHERE offer_count >= 1 AND updated_at >= ? "
                           "ORDER BY offer_count >= 2 DESC, offer_count DESC LIMIT 50000", (esik,)).fetchall()
    adresler = [f"{CANONICAL_ORIGIN}/urun/{key_to_slug(r[0])}" for r in rows]
    sonuc = []
    for i in range(0, len(adresler), 10000):
        sonuc.append(gonder(adresler[i:i + 10000]))
    print(json.dumps({"bildirilen": len(adresler), "yanitlar": sonuc}, ensure_ascii=False))


if __name__ == "__main__":
    main()
