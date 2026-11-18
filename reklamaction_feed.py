"""
ReklamAction ürün feed'lerini (Google Merchant XML) dipci kataloğuna alma (Kemal, 18 Kas 2026: "bu mail geldi" —
Güçkan Can: "XML sistemimiz aktif olmuştur, feed.reklamaction.com").

- Feed adresi kişiye özel token içerir: .env -> REKLAMACTION_FEED_TOKEN="<aff_id>/<token>" (git'e girmez).
- Feed'ler günde 2 kez (00:00, 12:00) yenilenir; kural: feed başına günde en çok 100 istek, en çok 10 farklı IP.
  Bu yüzden yalnız sunucudan, günde 2 kez çekilir.
- Ürün linkleri zaten takip kodlu (ad.reklm.com/aff_c...): tıklama ve satış Kemal'in hesabına yazılır, link değiştirilmez.
- Aynı ürünün beden/renk kopyaları tek ürüne indirilir (en düşük fiyat kalır). Stokta olmayan alınmaz.
Kullanım: python reklamaction_feed.py [--magaza koton,gap] [--sinir 5000]
"""
import argparse
import gzip
import json
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

KOK = Path(__file__).resolve().parent
sys.path.insert(0, str(KOK))
from auto_catalog import AutoCatalogEngine, classify_category  # noqa: E402
from memory_db import PriceMemoryDB  # noqa: E402

G = "{http://base.google.com/ns/1.0}"
KLASOR = KOK / "data" / "reklamaction"

# feed dosyası -> (platform anahtarı, mağaza adı, dipci kategorisi; "" = ürüne göre)
MAGAZALAR = {
    "colins": ("colins", "Colin's", "giyim_aksesuar"),
    "columbia": ("columbia", "Columbia", "giyim_aksesuar"),
    "dr": ("dr", "D&R", ""),
    "gap": ("gap", "Gap", "giyim_aksesuar"),
    "hatemoglu": ("hatemoglu", "Hatemoğlu", "giyim_aksesuar"),
    "hotic": ("hotic", "Hotiç", "giyim_aksesuar"),
    "koctas": ("koctas", "Koçtaş", ""),
    "konyali-saat": ("konyalisaat", "Konyalı Saat", "giyim_aksesuar"),
    "koton": ("koton", "Koton", "giyim_aksesuar"),
    "ms": ("ms", "M&S", "giyim_aksesuar"),
    "puma": ("puma", "Puma", "saglik_spor_outdoor"),
    "saatsaat": ("saatsaat", "Saat&Saat", "giyim_aksesuar"),
    "sporthink": ("sporthink", "Sporthink", "saglik_spor_outdoor"),
    "sportive": ("sportive", "Sportive", "saglik_spor_outdoor"),
    # touristica: tur/tatil paketi, ürün değil — alınmaz
}


def _kategori(urun_tipi: str, metin: str) -> str:
    t = urun_tipi.lower()
    if t.startswith("kitap") or "> kitap" in t:
        return "kitap_hobi"
    if any(k in t for k in ("müzik", "film", "hobi", "oyuncak", "kırtasiye", "parti", "sanat")):
        return "anne_bebek" if "oyuncak" in t else "kitap_hobi"
    if any(k in t for k in ("elektronik", "bilgisayar", "telefon", "beyaz eşya", "küçük ev aletleri", "televizyon")):
        return "teknoloji_beyaz_esya"
    if any(k in t for k in ("bahçe", "yapı", "hırdavat", "elektrik", "boya", "nalbur", "oto", "banyo", "tesisat",
                            "aydınlatma", "zemin", "kapı", "pencere", "el aletleri")):
        return "yapi_oto_bahce"
    if any(k in t for k in ("mutfak", "ev ", "ev>", "mobilya", "dekorasyon", "ev tekstili", "depolama", "düzenleme")):
        return "ev_yasam_mutfak"
    return classify_category(metin, hint="")


def _fiyat(s: str) -> float:
    m = re.search(r"(\d+(?:[.,]\d+)?)", s or "")
    return float(m.group(1).replace(",", ".")) if m else 0.0


# Beden/numara kopyalarını birleştirmek için başlıktan beden ekini at
_BEDEN = re.compile(r"\s*[-–,/]?\s*\b(?:beden\s*)?(?:xxs|xs|s|m|l|xl|xxl|xxxl|[2-5]xl|\d{2}(?:[.,]5)?|\d{2}/\d{2})\s*$", re.I)


def _anahtar(marka: str, baslik: str) -> str:
    b = baslik.strip()
    for _ in range(2):
        b = _BEDEN.sub("", b)
    return re.sub(r"\s+", " ", f"{marka} {b}".lower()).strip()


def indir(dosya: str, token: str) -> Path:
    KLASOR.mkdir(parents=True, exist_ok=True)
    hedef = KLASOR / f"{dosya}.xml.gz"
    # Feed günde 2 kez yenilenir: 6 saatten taze kopya varsa yeniden çekme (istek sınırı)
    if hedef.exists() and time.time() - hedef.stat().st_mtime < 6 * 3600:
        return hedef
    url = f"https://feed.reklamaction.com/{token}/{dosya}.xml"
    req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip", "User-Agent": "dipci-feed/1.0"})
    gecici = hedef.with_suffix(".tmp")
    with urllib.request.urlopen(req, timeout=600) as r, open(gecici, "wb") as f:
        sikistirilmis = r.headers.get("Content-Encoding", "") == "gzip"
        if sikistirilmis:
            while True:
                parca = r.read(1 << 20)
                if not parca:
                    break
                f.write(parca)
        else:
            with gzip.GzipFile(fileobj=f, mode="wb") as gz:
                while True:
                    parca = r.read(1 << 20)
                    if not parca:
                        break
                    gz.write(parca)
    gecici.replace(hedef)
    return hedef


def oku(yol: Path):
    with gzip.open(yol, "rb") as f:
        for _, el in ET.iterparse(f, events=("end",)):
            if el.tag != "item":
                continue
            d = {
                "ad": (el.findtext("title") or "").strip(),
                "link": (el.findtext("link") or "").strip(),
                "img": (el.findtext(f"{G}image_link") or "").strip(),
                "fiyat": _fiyat(el.findtext(f"{G}price") or ""),
                "indirimli": _fiyat(el.findtext(f"{G}sale_price") or ""),
                "stok": (el.findtext(f"{G}availability") or "in stock").strip().lower(),
                "marka": (el.findtext(f"{G}brand") or "").strip(),
                "gtin": (el.findtext(f"{G}gtin") or "").strip(),
                "tip": (el.findtext(f"{G}product_type") or "").strip(),
            }
            el.clear()
            yield d


def aktar(dosya: str, token: str, sinir: int = 0) -> dict:
    platform, magaza_adi, sabit_kat = MAGAZALAR[dosya]
    t0 = time.time()
    yol = indir(dosya, token)
    # 1) Beden/renk kopyalarını birleştir: her ürün için en düşük fiyat
    urunler = {}
    okunan = 0
    for u in oku(yol):
        okunan += 1
        if u["stok"] not in ("in stock", "in_stock", "preorder"):
            continue
        fiyat = u["indirimli"] if 1 < u["indirimli"] < u["fiyat"] else u["fiyat"]
        if fiyat <= 1 or not u["ad"] or not u["link"]:
            continue
        marka = u["marka"] or magaza_adi
        k = _anahtar(marka, u["ad"])
        onceki = urunler.get(k)
        if not onceki or fiyat < onceki["fiyat_son"]:
            u["fiyat_son"], u["marka"] = fiyat, marka
            urunler[k] = u
        if sinir and len(urunler) >= sinir:
            break
    # 2) Kataloğa yaz
    db = PriceMemoryDB(str(KOK / "data" / "dipci_platform.db"))
    motor = AutoCatalogEngine(db)
    yazilan = 0
    for i, u in enumerate(urunler.values(), 1):
        kategori = sabit_kat or _kategori(u["tip"], f"{u['marka']} {u['ad']}")
        teklif = [{"platform": platform, "seller_name": magaza_adi, "title": u["ad"], "price": u["fiyat_son"],
                   "unit_price_text": "", "url": u["link"], "is_direct_product": True, "is_branch_only": False,
                   "source": "reklamaction_feed"}]
        try:
            pk = motor.persist_offers_for_product(raw_brand=u["marka"], raw_title=u["ad"], offers=teklif,
                                                  category_hint=kategori, gtin=u["gtin"], image_url=u["img"],
                                                  ping_search_engines=False, commit=False, update_index=False)
        except Exception:
            pk = None
        if pk:
            yazilan += 1
            db.conn.execute("UPDATE canonical_products SET category = ? WHERE product_key = ? AND "
                            "(category IS NULL OR category = '' OR category = 'gida')", (kategori, pk))
            if u["img"]:
                db.conn.execute("UPDATE canonical_products SET image_url = ? WHERE product_key = ? AND "
                                "(image_url IS NULL OR image_url = '')", (u["img"], pk))
        if i % 1000 == 0:
            db.conn.commit()
    db.conn.commit()
    return {"magaza": magaza_adi, "feed_satir": okunan, "tekil_urun": len(urunler), "yazilan": yazilan,
            "sure_sn": round(time.time() - t0)}


def _token() -> str:
    tok = os.getenv("REKLAMACTION_FEED_TOKEN", "")
    if not tok:
        env = KOK / ".env"
        if env.exists():
            for satir in env.read_text(encoding="utf-8").splitlines():
                if satir.startswith("REKLAMACTION_FEED_TOKEN="):
                    tok = satir.split("=", 1)[1].strip().strip('"')
    if not tok:
        raise SystemExit("REKLAMACTION_FEED_TOKEN .env'de yok")
    return tok


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--magaza", default="", help="virgülle: koton,gap (boş = hepsi)")
    ap.add_argument("--sinir", type=int, default=0, help="mağaza başına en çok kaç tekil ürün (0 = hepsi)")
    a = ap.parse_args()
    tok = _token()
    secilen = [m for m in (a.magaza.split(",") if a.magaza else MAGAZALAR) if m in MAGAZALAR]
    sonuc = []
    for m in secilen:
        try:
            r = aktar(m, tok, a.sinir)
        except Exception as e:
            r = {"magaza": m, "hata": str(e)[:200]}
        print(json.dumps(r, ensure_ascii=False), flush=True)
        sonuc.append(r)
    db = PriceMemoryDB(str(KOK / "data" / "dipci_platform.db"))
    print(json.dumps({"indeks": db.rebuild_catalog_index(),
                      "katalog_toplam": db.conn.execute("SELECT COUNT(*) FROM canonical_products").fetchone()[0]},
                     ensure_ascii=False))
