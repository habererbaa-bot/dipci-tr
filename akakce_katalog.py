"""
Akakçe kataloğunun tamamını dipci'ye alma (Kemal, 17 Kas 2026: "bilgisayarlar yok, beyaz eşyaları hiç görmedim... ne burası, mahalle bakkalı mı?").

- /tum-kategoriler/ sayfasından ~4.300 yaprak kategori ve üst grubu (Elektronik, Süpermarket...) okunur.
- Her kategorinin liste sayfalarındaki JSON-LD ItemList'ten ürün adı, marka, görsel ve piyasadaki en düşük fiyat alınır.
- Ürün dipci kataloğuna "piyasa en düşük" fiyatıyla girer; mağaza mağaza ayrıntı, ürüne bakıldığında (dipci-tazele) ve
  sıradaki online zenginleştirmede mağazaların kendi sayfalarından ve Akakçe ürün sayfasından doldurulur.
- İstekler Türkiye proxy'sinden (ev_baglanti) ve yavaş gider (varsayılan 7 sn arayla) ki adres engellenmesin.
Kullanım: python akakce_katalog.py --istek 150 --sayfa 2
"""
import argparse
import html as html_lib
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ev_baglanti  # noqa: E402

ev_baglanti.kur()
from auto_catalog import AutoCatalogEngine, classify_category  # noqa: E402
from memory_db import PriceMemoryDB  # noqa: E402

KOK = Path(__file__).resolve().parent
DURUM = KOK / "data" / "akakce_katalog_durum.json"
KATEGORI_DOSYA = KOK / "data" / "akakce_kategoriler.json"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36",
      "Accept-Language": "tr-TR,tr;q=0.9"}

UST_GRUPLAR = {
    "elektronik": "teknoloji_beyaz_esya",
    "ev-ofis-yasam": "ev_yasam_mutfak",
    "anne-bebek-oyuncak": "anne_bebek",
    "kozmetik-kisisel-bakim": "kisisel_bakim",
    "supermarket": "",  # süpermarket: ürün adına göre gıda / temizlik / evcil hayvan ...
    "spor-outdoor": "saglik_spor_outdoor",
    "yapi-market-oto-bahce": "yapi_oto_bahce",
    "saat-giyim-aksesuar": "giyim_aksesuar",
}


def getir(url: str, deneme: int = 2) -> str:
    for i in range(deneme):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return r.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return ""
            if e.code in (403, 429):
                raise
        except Exception:
            time.sleep(5)
    return ""


def kategoriler(yenile: bool = False) -> list:
    if KATEGORI_DOSYA.exists() and not yenile:
        return json.loads(KATEGORI_DOSYA.read_text(encoding="utf-8"))
    s = html_lib.unescape(getir("https://www.akakce.com/tum-kategoriler/"))
    ust = sorted((m.start(), slug) for slug in UST_GRUPLAR for m in re.finditer(r'href="/' + slug + r'\.html"', s))
    if not ust:
        raise RuntimeError("kategori sayfası okunamadı")
    bitis = s.find("Mağaza", ust[-1][0] + 2000)
    liste, gorulen = [], set()
    for m in re.finditer(r'href="/([a-z0-9-]+)\.html"', s):
        pos, slug = m.start(), m.group(1)
        if pos < ust[0][0] or (bitis > 0 and pos > bitis) or slug in UST_GRUPLAR or slug in gorulen:
            continue
        grup = [g for p, g in ust if p <= pos][-1]
        gorulen.add(slug)
        liste.append({"slug": slug, "grup": grup})
    KATEGORI_DOSYA.write_text(json.dumps(liste, ensure_ascii=False), encoding="utf-8")
    return liste


def urunler(sayfa_html: str) -> list:
    out = []
    for ld in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', sayfa_html, re.S):
        if "ItemList" not in ld:
            continue
        try:
            veri = json.loads(ld)
        except Exception:
            continue
        dugumler = veri.get("@graph", [veri]) if isinstance(veri, dict) else []
        for d in dugumler:
            if not isinstance(d, dict) or d.get("@type") != "ItemList":
                continue
            for el in d.get("itemListElement") or []:
                it = el.get("item") if isinstance(el, dict) else None
                if not isinstance(it, dict) or it.get("@type") != "Product":
                    continue
                try:
                    fiyat = float((it.get("offers") or {}).get("price") or 0)
                except Exception:
                    fiyat = 0
                marka = it.get("brand")
                marka = marka.get("name") if isinstance(marka, dict) else (marka or "")
                img = it.get("image")
                img = img[0] if isinstance(img, list) and img else (img if isinstance(img, str) else "")
                if fiyat > 1 and it.get("name"):
                    out.append({"ad": html_lib.unescape(it["name"]).strip(), "marka": html_lib.unescape(str(marka)).strip(),
                                "fiyat": fiyat, "img": img, "ak_url": it.get("url") or ""})
    return out


def calis(istek: int, sayfa_sinir: int, bekle: float) -> dict:
    t0 = time.time()
    liste = kategoriler()
    durum = json.loads(DURUM.read_text()) if DURUM.exists() else {"i": 0, "sayfa": 1}
    # Engellendiyse 6 saat hiç istek atma (18 Kas 2026: 3 dakikada bir denemek engeli 10 saat uzattı)
    if time.time() - float(durum.get("engel_zamani") or 0) < 6 * 3600:
        return {"ok": False, "bekliyor": "akakce engeli, 6 saat ara", "sira": durum}
    db = PriceMemoryDB(str(KOK / "data" / "dipci_platform.db"))
    motor = AutoCatalogEngine(db)
    db.conn.execute("CREATE TABLE IF NOT EXISTS akakce_baglanti (product_key TEXT PRIMARY KEY, url TEXT NOT NULL, kategori TEXT)")
    yapilan = eklenen = 0
    degisen = []
    engel = False
    while yapilan < istek:
        kat = liste[durum["i"] % len(liste)]
        adres = f"https://www.akakce.com/{kat['slug']}.html" if durum["sayfa"] == 1 else \
            f"https://www.akakce.com/{kat['slug']},{durum['sayfa']}.html"
        try:
            s = getir(adres)
        except urllib.error.HTTPError as e:
            print("akakce engelledi:", e.code, adres, flush=True)
            durum["engel_zamani"] = time.time()
            DURUM.write_text(json.dumps(durum))
            engel = True
            break
        yapilan += 1
        bulunan = urunler(s)
        dipci_kat = UST_GRUPLAR.get(kat["grup"], "")
        for u in bulunan:
            kategori = dipci_kat or classify_category(f"{u['marka']} {u['ad']}", hint="supermarket")
            teklif = [{"platform": "default", "seller_name": "Piyasadaki en düşük fiyat", "title": u["ad"], "price": u["fiyat"],
                       "unit_price_text": "", "url": "", "is_direct_product": False, "is_branch_only": False,
                       "source": "piyasa_ozeti"}]
            try:
                pk = motor.persist_offers_for_product(raw_brand=u["marka"], raw_title=u["ad"], offers=teklif,
                                                      category_hint=kategori, image_url=u["img"],
                                                      ping_search_engines=False, commit=False, update_index=False)
            except Exception:
                pk = None
            if pk:
                eklenen += 1
                degisen.append(pk)
                db.conn.execute("UPDATE canonical_products SET category = ? WHERE product_key = ?", (kategori, pk))
                if u["img"]:
                    db.conn.execute("UPDATE canonical_products SET image_url = ? WHERE product_key = ? AND (image_url IS NULL OR image_url = '')",
                                    (u["img"], pk))
                db.conn.execute("INSERT OR REPLACE INTO akakce_baglanti(product_key, url, kategori) VALUES (?,?,?)",
                                (pk, u["ak_url"], kat["slug"]))
        db.conn.commit()
        # Sonraki sayfa ya da sonraki kategori
        if bulunan and durum["sayfa"] < sayfa_sinir:
            durum["sayfa"] += 1
        else:
            durum = {"i": (durum["i"] + 1) % len(liste), "sayfa": 1, "engel_zamani": 0}
        DURUM.write_text(json.dumps(durum))
        time.sleep(bekle)
    indeks = db.rebuild_catalog_index(degisen) if degisen else 0  # yalnız değişen ürünler (tam yenileme 90 sn kilitliyordu)
    toplam = db.conn.execute("SELECT COUNT(*) FROM canonical_products").fetchone()[0]
    return {"ok": not engel, "sayfa_okunan": yapilan, "urun_yazilan": eklenen, "katalog_toplam": toplam,
            "indeks": indeks, "sira": durum, "kategori_sayisi": len(liste), "sure_sn": round(time.time() - t0)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--istek", type=int, default=150)
    ap.add_argument("--sayfa", type=int, default=2, help="kategori başına en çok kaç liste sayfası")
    ap.add_argument("--bekle", type=float, default=7.0)
    ap.add_argument("--kategori-yenile", action="store_true")
    a = ap.parse_args()
    if a.kategori_yenile:
        print(len(kategoriler(yenile=True)))
    print(json.dumps(calis(a.istek, a.sayfa, a.bekle), ensure_ascii=False))
