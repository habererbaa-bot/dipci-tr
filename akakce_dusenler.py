"""
Akakçe "fiyatı düşen ürünler" → dipci kataloğu (Kemal, 17 Kas 2026: "teknoloji yok mu, beyaz eşyada kampanya olmuyor mu?").

dipci kataloğu çoğunlukla market verisinden geliyordu; teknoloji ve beyaz eşya azdı. Bu iş Akakçe'nin fiyatı düşen
ürünler listesini (robots izinli; MTR1 Market Dedektifi tarifindeki kaynak) yavaşça okur, en az 3 mağazada satılan
ürünleri dipci'nin kendi keşif akışıyla kataloğa ekler ve Akakçe'nin bildirdiği düşüşü indirim radarına yazar.
Böylece teknoloji, beyaz eşya, ev ve giyim ürünleri de dipci kartlarına ve Market Dedektifi yazısına girer.

Kurallar: yavaş tara (sayfa arası 4 sn, ürün arası 6 sn); tek mağazada satılan ürün eklenmez.
"""
import argparse
import html
import json
import re
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from auto_catalog import AutoCatalogEngine
from memory_db import PriceMemoryDB

AKAKCE = "https://www.akakce.com"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def _sayi(s: str) -> float:
    try:
        return float(s.replace(".", "").replace(",", "."))
    except ValueError:
        return 0.0


def dusenler(sayfa: int):
    out = []
    for p in range(1, sayfa + 1):
        url = AKAKCE + "/fiyati-dusen-urunler/" + (f"?p={p}" if p > 1 else "")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "tr-TR,tr;q=0.9"})
            with urllib.request.urlopen(req, timeout=25) as r:
                h = r.read().decode("utf-8", "ignore")
        except Exception:
            h = ""
        for li in h.split("<li data-pr=")[1:]:
            ad = re.search(r'<h3 class="pn_v8">([^<]+)<', li)
            f = re.search(r'class="pt_v8">\s*(?:<!-- -->)?([\d.]+)<i>,(?:<!-- -->)?(\d+)', li)
            href = re.search(r'href="(/[^"]+\.html)"', li)
            if not ad or not f or not href:
                continue
            out.append({
                "ad": html.unescape(ad.group(1)).strip(),
                "kategori": href.group(1).split("/")[1],
                "dusus": int((re.search(r"En Ucuz <i[^>]*>%(\d+)", li) or [0, 0])[1]),
                "fiyat": _sayi(f.group(1) + "," + f.group(2)),
                "magaza": 1 + int((re.search(r"\+(\d+) FİYAT", li) or [0, 0])[1]),
            })
        time.sleep(4)
    return out


def dipci_kategori(slug: str) -> str:
    """Akakçe kategori adresinden dipci kategorisi (dipci'nin sınıflayıcısı teknolojiyi bazen 'gıda' sanıyordu)."""
    s = slug.lower()
    kurallar = [
        ("kisisel_bakim", ("sac", "epilasyon", "tiras", "dis-fircasi", "parfum", "makyaj", "cilt", "deodorant")),
        ("saglik_spor_outdoor", ("ayakkabi", "spor", "bisiklet", "kosu", "outdoor", "kamp", "fitness", "tansiyon")),
        ("anne_bebek", ("bebek", "cocuk-arac", "mama-sandalye", "puset")),
        ("evcil_hayvan", ("kedi", "kopek", "evcil")),
        ("ev_yasam_mutfak", ("kahve-makinesi", "blender", "mutfak", "tencere", "fritoz", "airfryer", "tava", "nevresim",
                             "yatak", "mobilya", "aydinlatma", "soba", "isitici", "ütü", "utu")),
    ]
    for kat, anahtarlar in kurallar:
        if any(k in s for k in anahtarlar):
            return kat
    return "teknoloji_beyaz_esya"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sayfa", type=int, default=8)
    ap.add_argument("--en-fazla", type=int, default=60)
    a = ap.parse_args()

    db = PriceMemoryDB(str(Path(__file__).resolve().parent / "data" / "dipci_platform.db"))
    engine = AutoCatalogEngine(db)
    liste = [u for u in dusenler(a.sayfa) if u["magaza"] >= 3]
    # En çok düşenler önce; aynı kategoriden yığılma olmasın diye kategori başına en çok 8.
    liste.sort(key=lambda u: -u["dusus"])
    sayac, secilen = {}, []
    for u in liste:
        if sayac.get(u["kategori"], 0) >= 8:
            continue
        sayac[u["kategori"]] = sayac.get(u["kategori"], 0) + 1
        secilen.append(u)
    secilen = secilen[: a.en_fazla]

    eklenen, radar = 0, 0
    simdi = datetime.now().isoformat()
    for u in secilen:
        try:
            pk = engine.discover_and_persist_query(u["ad"])
        except Exception:
            pk = None
        time.sleep(6)
        if not pk:
            continue
        kat = dipci_kategori(u["kategori"])
        db.conn.execute("UPDATE canonical_products SET category = ? WHERE product_key = ?", (kat, pk))
        db.conn.execute("UPDATE product_catalog_index SET category = ? WHERE product_key = ?", (kat, pk))
        db.conn.commit()
        r = db.conn.execute("SELECT * FROM product_catalog_index WHERE product_key = ?", (pk,)).fetchone()
        if not r or int(r["offer_count"] or 0) < 2:
            continue
        eklenen += 1
        # Akakçe'nin bildirdiği düşüşü radara yaz (en az %8 ve dipci'deki en ucuz fiyat Akakçe'ninkine yakınsa).
        best = float(r["best_pack_price"] or 0)
        if u["dusus"] >= 8 and best > 0 and abs(best - u["fiyat"]) <= u["fiyat"] * 0.15:
            eski = round(u["fiyat"] / (1 - u["dusus"] / 100.0), 2)
            db.conn.execute(
                """INSERT OR REPLACE INTO live_price_drops
                   (product_key, brand, family_name, category, image_url, platform, seller_name, old_ref_price, new_price,
                    drop_pct, rival_next_price, unit_price, base_unit, product_url, seo_url, share_text, detected_at, notified_webhook)
                   VALUES (?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,?,?,1)""",
                (pk, r["brand"], r["family_name"], r["category"], r["image_url"] or "", r["best_platform"] or "default",
                 r["best_seller_name"] or "", eski, best, float(u["dusus"]), float(r["best_unit_price"] or 0),
                 r["base_unit"] or "adet", r["best_url"] or "", f"https://dipci.tr/urun/{pk.replace('_', '-')}", "", simdi),
            )
            db.conn.commit()
            radar += 1
    print(json.dumps({"okunan": len(liste), "denenen": len(secilen), "katalogda": eklenen, "radara_yazilan": radar}, ensure_ascii=False))


if __name__ == "__main__":
    main()
