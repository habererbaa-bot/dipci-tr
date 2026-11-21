"""
dipci.tr — Katalog Birleştirme, Ölü Kayıt Temizliği ve FTS5 Hızlandırma Bakım Aracı
1. `product_catalog_index` içindeki 0 teklifli (fiyatsız) ölü kayıtları temizler.
2. Farklı mağazalardan ayrı `product_key` ile girmiş birebir aynı ürünleri
   (aynı marka + temizlenmiş aynı ad + aynı fiyat bandı, farklı platformlar)
   tek kanonik üründe birleştirir -> tek mağazalı ürünleri gerçek çok mağazalı
   karşılaştırmaya dönüştürür.
3. FTS5 tam metin arama indeksini (`product_search_fts`) ve SQLite istatistiklerini
   (`PRAGMA optimize`) güncelleyerek arama ve ana sayfa hızını milisaniye seviyesine indirir.
"""

import argparse
import json
import re
import time
from collections import defaultdict
from pathlib import Path

from memory_db import PriceMemoryDB

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "dipci_platform.db"


def _norm_sig(brand: str, family_name: str) -> str:
    s = (
        f"{brand} {family_name}"
        .replace("İ", "i")
        .replace("I", "ı")
        .lower()
        .replace("ı", "i")
        .replace("ş", "s")
        .replace("ğ", "g")
        .replace("ü", "u")
        .replace("ö", "o")
        .replace("ç", "c")
    )
    # Gürültülü noktalama ve tekrarlı boşlukları temizle
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    tokens = [t for t in s.split() if len(t) >= 2]
    if len(tokens) < 3:
        return ""
    return " ".join(tokens)


def temizle_ve_birlestir(db: PriceMemoryDB, max_merges: int = 15000) -> dict:
    t0 = time.time()

    # 1) 0 teklifli fiyatsız ölü kayıtları dizinden temizle
    dead_cnt = db.conn.execute(
        "SELECT COUNT(*) FROM product_catalog_index WHERE offer_count <= 0"
    ).fetchone()[0]
    if dead_cnt:
        db.conn.execute("DELETE FROM product_catalog_index WHERE offer_count <= 0")
        db.conn.commit()

    # 2) Farklı platformlarda aynı (brand, family_name) imzasına sahip tekil/çift kayıtları bul ve birleştir
    rows = db.conn.execute(
        """
        SELECT product_key, brand, family_name, offer_count, best_platform, best_pack_price
        FROM product_catalog_index
        WHERE offer_count >= 1 AND best_pack_price > 1.0
        """
    ).fetchall()

    groups = defaultdict(list)
    for r in rows:
        sig = _norm_sig(r["brand"], r["family_name"])
        if sig:
            groups[sig].append(dict(r))

    merged_pairs = 0
    touched_keys = set()
    for sig, items in groups.items():
        if len(items) < 2:
            continue
        # Farklı mağazalara yayılmışsa birleştir
        plats = {it["best_platform"] for it in items if it["best_platform"]}
        if len(plats) < 2:
            continue

        # En çok teklifi olanı ana ürün seç
        items.sort(key=lambda x: (-int(x["offer_count"]), x["product_key"]))
        primary = items[0]
        p_key = primary["product_key"]
        p_price = float(primary["best_pack_price"])

        for dup in items[1:]:
            if merged_pairs >= max_merges:
                break
            d_key = dup["product_key"]
            d_price = float(dup["best_pack_price"])
            # Aşırı fiyat uçurumu varsa (farklı paket/varyant riski) birleştirme!
            if p_price <= 0 or not (0.52 * p_price <= d_price <= 1.90 * p_price):
                continue
            try:
                db.conn.execute(
                    "UPDATE price_snapshots SET product_key = ? WHERE product_key = ?",
                    (p_key, d_key),
                )
                db.conn.execute(
                    "UPDATE OR IGNORE product_barcodes SET product_key = ? WHERE product_key = ?",
                    (p_key, d_key),
                )
                db.conn.execute("DELETE FROM product_barcodes WHERE product_key = ?", (d_key,))
                db.conn.execute("DELETE FROM product_catalog_index WHERE product_key = ?", (d_key,))
                db.conn.execute("DELETE FROM canonical_products WHERE product_key = ?", (d_key,))
                touched_keys.add(p_key)
                merged_pairs += 1
                if merged_pairs % 200 == 0:
                    db.conn.commit()
            except Exception:
                continue

    db.conn.commit()
    if touched_keys:
        db.rebuild_catalog_index(list(touched_keys))

    # 3) FTS5 tam metin arama indeksini doldur ve sorgu planlayıcısını optimize et
    fts_rows = db.rebuild_fts_index()
    db.conn.execute("PRAGMA optimize")
    db.conn.commit()

    multi_cnt = db.conn.execute(
        "SELECT COUNT(*) FROM product_catalog_index WHERE offer_count >= 2"
    ).fetchone()[0]
    total_cnt = db.conn.execute(
        "SELECT COUNT(*) FROM product_catalog_index WHERE offer_count >= 1"
    ).fetchone()[0]

    return {
        "ok": True,
        "elapsed_sec": round(time.time() - t0, 2),
        "dead_zero_offer_deleted": int(dead_cnt),
        "duplicate_products_merged": merged_pairs,
        "primary_products_updated": len(touched_keys),
        "fts5_indexed_products": fts_rows,
        "total_active_products": int(total_cnt),
        "multi_store_products": int(multi_cnt),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--max-merges", type=int, default=15000)
    args = ap.parse_args()
    db = PriceMemoryDB(args.db)
    res = temizle_ve_birlestir(db, max_merges=args.max_merges)
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
