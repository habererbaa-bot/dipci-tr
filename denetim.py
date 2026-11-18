"""
dipci denetim sistemi (Kemal, 18 Kas 2026: "bir de denetim sistemi kur. görselsiz ürünleri denetlesin, kampanyaları denetlesin,
yanlış bir karşılaştırma var mı ölçsün").

Saatte bir çalışır, raporu data/denetim_raporu.json'a yazar; /denetim sayfasında görülür.
1) Görselsiz ürünler: sayı + örnek; ürünün başka kaydında görsel varsa kendiliğinden doldurulur.
2) Kampanyalar (İndirimler şeridi): şu durumlar YANLIŞ sayılır ve şeritten kendiliğinden çıkarılır —
   - indirimli teklifin başlığı ürünle uyuşmuyor (başka gramaj/adet/koli),
   - mağazanın şu anki fiyatı kampanya fiyatından yüksek (kampanya bitmiş),
   - indirim %70'ten büyük ya da eski fiyat yeni fiyatın 4 katından fazla (veri hatası).
3) Karşılaştırmalar: aynı üründe en pahalı teklif en ucuzun 3 katından fazlaysa ya da teklif başlığı ürünle uyuşmuyorsa
   şüpheli sayılır, listelenir (silinmez; Kemal görür, karar verilir).
Kullanım: python denetim.py [--uygula]   (--uygula olmadan yalnız rapor)
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

KOK = Path(__file__).resolve().parent
sys.path.insert(0, str(KOK))
from memory_db import PriceMemoryDB  # noqa: E402
from teklif_denetim import uyumlu  # noqa: E402

RAPOR = KOK / "data" / "denetim_raporu.json"
OZEL_KATEGORI = ("teknoloji_beyaz_esya", "yapi_oto_bahce", "giyim_aksesuar", "kitap_hobi")


def gorselsiz(c, uygula: bool) -> dict:
    toplam = c.execute("SELECT COUNT(*) FROM product_catalog_index WHERE offer_count >= 1").fetchone()[0]
    rows = c.execute("SELECT product_key, brand, family_name, category FROM product_catalog_index "
                     "WHERE offer_count >= 1 AND COALESCE(image_url, '') = ''").fetchall()
    doldurulan = 0
    if uygula:
        for r in rows:
            img = c.execute("SELECT image_url FROM canonical_products WHERE product_key = ? AND COALESCE(image_url,'') <> ''",
                            (r["product_key"],)).fetchone()
            if img:
                c.execute("UPDATE product_catalog_index SET image_url = ? WHERE product_key = ?", (img[0], r["product_key"]))
                doldurulan += 1
    kategori = {}
    for r in rows:
        kategori[r["category"]] = kategori.get(r["category"], 0) + 1
    return {"toplam_urun": toplam, "gorselsiz": len(rows) - doldurulan, "kendiliginden_doldurulan": doldurulan,
            "oran_yuzde": round(100 * (len(rows) - doldurulan) / max(toplam, 1), 1), "kategoriye_gore": kategori,
            "ornek": [{"urun": f"{r['brand']} {r['family_name']}", "key": r["product_key"]} for r in rows[:30]]}


def kampanya_hatasi(c, product_key: str, platform: str, yeni: float, eski: float, pct: float, kategori: str, ana: str) -> str:
    """Kampanya yanlışsa nedenini, doğruysa "" döndürür (radar da bunu kullanır)."""
    if pct > 70 or (yeni > 0 and eski > 4 * yeni):
        return f"inanılmaz indirim (%{pct:.0f}): veri hatası olabilir"
    son = c.execute("SELECT title, effective_pack_price FROM price_snapshots WHERE product_key = ? AND platform = ? "
                    "ORDER BY captured_at DESC LIMIT 1", (product_key, platform)).fetchone()
    if not son:
        return ""
    if kategori not in OZEL_KATEGORI and not uyumlu(ana, son[0] or ""):
        return f"teklif başka ürün/paket: \"{son[0]}\""
    if son[1] > yeni * 1.03:
        return f"kampanya bitmiş: şimdi {son[1]:.2f} TL"
    return ""


def kampanyalar(c, uygula: bool) -> dict:
    rows = c.execute("SELECT d.*, p.brand AS pb, p.family_name AS pf FROM live_price_drops d "
                     "LEFT JOIN canonical_products p USING(product_key)").fetchall()
    yanlis = []
    for d in rows:
        ana = f"{d['pb'] or d['brand']} {d['pf'] or d['family_name']}"
        neden = kampanya_hatasi(c, d["product_key"], d["platform"], d["new_price"], d["old_ref_price"], d["drop_pct"],
                                d["category"], ana)
        if neden:
            yanlis.append({"urun": ana, "key": d["product_key"], "magaza": d["seller_name"] or d["platform"],
                           "eski": d["old_ref_price"], "yeni": d["new_price"], "indirim": d["drop_pct"], "neden": neden})
    if uygula and yanlis:
        c.executemany("DELETE FROM live_price_drops WHERE product_key = ?", [(y["key"],) for y in yanlis])
        c.executemany("UPDATE product_catalog_index SET is_real_drop = 0 WHERE product_key = ?", [(y["key"],) for y in yanlis])
    return {"toplam_kampanya": len(rows), "yanlis": len(yanlis), "seritten_cikarildi": bool(uygula),
            "liste": yanlis[:100]}


def karsilastirmalar(c) -> dict:
    rows = c.execute("SELECT product_key, brand, family_name, category, best_pack_price, worst_pack_price, best_platform, "
                     "worst_platform, offer_count FROM product_catalog_index WHERE offer_count >= 2").fetchall()
    supheli = []
    for r in rows:
        ana = f"{r['brand']} {r['family_name']}"
        neden = ""
        if r["best_pack_price"] > 0 and r["worst_pack_price"] > 3 * r["best_pack_price"]:
            neden = f"fiyat farkı {r['worst_pack_price'] / r['best_pack_price']:.1f} kat"
        if r["category"] not in OZEL_KATEGORI and not r["family_name"].startswith("Temel İhtiyaç"):
            for s in c.execute("SELECT platform, title FROM price_snapshots WHERE product_key = ? GROUP BY platform",
                               (r["product_key"],)).fetchall():
                if not uyumlu(ana, s["title"] or ""):
                    neden = (neden + "; " if neden else "") + f"{s['platform']} başka paket: \"{s['title']}\""
                    break
        if neden:
            supheli.append({"urun": ana, "key": r["product_key"], "en_ucuz": r["best_pack_price"],
                            "en_ucuz_magaza": r["best_platform"], "en_pahali": r["worst_pack_price"],
                            "en_pahali_magaza": r["worst_platform"], "neden": neden})
    return {"karsilastirilan_urun": len(rows), "supheli": len(supheli),
            "oran_yuzde": round(100 * len(supheli) / max(len(rows), 1), 1), "liste": supheli[:200]}


def calis(uygula: bool) -> dict:
    t0 = time.time()
    db = PriceMemoryDB(str(KOK / "data" / "dipci_platform.db"))
    c = db.conn
    rapor = {"zaman": datetime.now().strftime("%d.%m.%Y %H:%M"),
             "gorsel": gorselsiz(c, uygula),
             "kampanya": kampanyalar(c, uygula),
             "karsilastirma": karsilastirmalar(c)}
    c.commit()
    rapor["sure_sn"] = round(time.time() - t0)
    RAPOR.write_text(json.dumps(rapor, ensure_ascii=False, indent=1), encoding="utf-8")
    return rapor


def yanlis_teklifleri_sil() -> dict:
    """Şüpheli karşılaştırmalarda ürünle uyuşmayan (başka beden/paket) mağaza tekliflerini siler (Kemal, 18 Kas 2026: "sil")."""
    db = PriceMemoryDB(str(KOK / "data" / "dipci_platform.db"))
    c = db.conn
    sil, urun = [], set()
    for r in c.execute("SELECT product_key, brand, family_name, category FROM product_catalog_index WHERE offer_count >= 2").fetchall():
        if r["category"] in OZEL_KATEGORI or r["family_name"].startswith("Temel İhtiyaç"):
            continue
        ana = f"{r['brand']} {r['family_name']}"
        for s in c.execute("SELECT id, title FROM price_snapshots WHERE product_key = ?", (r["product_key"],)).fetchall():
            if not uyumlu(ana, s["title"] or ""):
                sil.append(s["id"])
                urun.add(r["product_key"])
    for i in range(0, len(sil), 500):
        parca = sil[i:i + 500]
        c.execute(f"DELETE FROM price_snapshots WHERE id IN ({','.join('?' * len(parca))})", parca)
    c.commit()
    db.rebuild_catalog_index()
    return {"silinen_teklif": len(sil), "duzelen_urun": len(urun)}


if __name__ == "__main__" and "--teklif-sil" in sys.argv:
    print(json.dumps(yanlis_teklifleri_sil(), ensure_ascii=False))
    sys.exit()
if __name__ == "__main__":
    r = calis("--uygula" in sys.argv)
    print(json.dumps({"gorselsiz": r["gorsel"]["gorselsiz"], "kampanya_yanlis": r["kampanya"]["yanlis"],
                      "kampanya_toplam": r["kampanya"]["toplam_kampanya"],
                      "karsilastirma_supheli": r["karsilastirma"]["supheli"],
                      "karsilastirilan": r["karsilastirma"]["karsilastirilan_urun"], "sure_sn": r["sure_sn"]},
                     ensure_ascii=False))
