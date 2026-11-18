"""
Mağazaların kendi sitelerinden fiyat okuma (Kemal, 17 Kas 2026: "Akakçe'deki fiyatların sağlıklı olduğunu nereden biliyoruz?
Başka yerlerde daha uygun fiyatlar çıkabiliyor").

Gerçek bir tarayıcıyla (Playwright/Chromium), Türkiye ISP proxy'si (.env TR_PROXY_URL) üzerinden mağazanın kendi arama
sayfasını açar, ürün kartlarından ad/fiyat/link/görsel okur ve aranan ürünle aynı olanı seçer.
Açık mağazalar (17 Kas 2026 denemesi): Trendyol, n11, Pazarama, Amazon, Teknosa, Vatan.
Kapalı (proxy'yle de engelliyor): Cimri, Hepsiburada, MediaMarkt, Epey.

Yavaş çalışır (mağaza başına birkaç saniye); yalnız arka plan işlerinde kullanılır, ziyaretçi isteğinde kullanılmaz.
"""
import math
import os
import re
import time
import urllib.parse
from typing import Dict, List, Optional, Tuple

MAGAZALAR = {
    # anahtar: (görünen ad, arama adresi, ürün linki deseni, hangi ürünler için)
    "trendyol": ("Trendyol", "https://www.trendyol.com/sr?q={q}", r"-p-\d+", "hepsi"),
    "n11": ("n11", "https://www.n11.com/arama?q={q}", r"/urun/", "hepsi"),
    "pazarama": ("Pazarama", "https://www.pazarama.com/arama?q={q}", r"-p-\d+", "hepsi"),
    "amazon": ("Amazon.com.tr", "https://www.amazon.com.tr/s?k={q}", r"/dp/[A-Z0-9]{10}", "hepsi"),
    "teknosa": ("Teknosa", "https://www.teknosa.com/arama/?s={q}", r"-p-\d+", "teknoloji"),
    "vatan": ("Vatan Bilgisayar", "https://www.vatanbilgisayar.com/arama/{q}/", r"vatanbilgisayar\.com/[a-z0-9-]+\.html", "teknoloji"),
}
TEKNO_KATEGORI = {"teknoloji_beyaz_esya", "ev_yasam_mutfak"}

_JS_KARTLAR = r"""
(desen) => {
  const re = new RegExp(desen);
  const out = []; const seen = new Set();
  const fiyatRe = /(\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:,\d{1,2})?)\s*(?:TL|₺)/g;
  for (const a of document.querySelectorAll('a[href]')) {
    const href = a.href.split('?')[0].split('#')[0];
    if (!re.test(href) || seen.has(href)) continue;
    let kart = a, metin = '';
    for (let i = 0; i < 7 && kart; i++) {
      metin = kart.innerText || '';
      if (/\d\s*(TL|₺)/.test(metin)) break;
      kart = kart.parentElement;
    }
    if (!kart || !/\d\s*(TL|₺)/.test(metin) || metin.length > 2500) continue;
    // Taksit ("3 x 120 TL"), birim fiyat ("12,50 TL/adet", "(400 TL/kg)"), kupon/puan satırlarını fiyat sanma
    const temiz = metin.replace(/\d+\s*[xX×]\s*[\d.,]+\s*(TL|₺)/g, ' ')
      .replace(/\([^)]*\/[^)]*\)/g, ' ')
      .replace(/[\d.,]+\s*(TL|₺)\s*\/\s*[a-zA-ZçğıöşüÇĞİÖŞÜ]+/g, ' ')
      .replace(/^.*(taksit|aylık|ayda|kupon|puan|kazan|cashback|indirim kodu).*$/gim, ' ');
    let fiyatlar = [...temiz.matchAll(fiyatRe)].map(m => parseFloat(m[1].replace(/\./g, '').replace(',', '.'))).filter(x => x > 1);
    if (!fiyatlar.length) continue;
    const tepe = Math.max(...fiyatlar);
    fiyatlar = fiyatlar.filter(x => x >= tepe * 0.5);  // eski/indirimli fiyat kalır, kırıntılar elenir
    const img = kart.querySelector('img');
    let ad = (a.getAttribute('title') || (img && img.getAttribute('alt')) || '').trim();
    if (ad.length < 8) ad = (a.innerText || metin).split('\n').map(s => s.trim()).filter(s => s.length > 8 && !/(TL|₺)/.test(s)).slice(0, 2).join(' ');
    seen.add(href);
    out.push({ ad, fiyat: Math.min(...fiyatlar), enYuksek: Math.max(...fiyatlar), url: href,
               img: img ? (img.currentSrc || img.src || img.getAttribute('data-src') || '') : '' });
    if (out.length >= 40) break;
  }
  return out;
}
"""


def _norm(t: str) -> str:
    t = (t or "").replace("İ", "i").replace("I", "ı").lower()
    for a, b in zip("ıüöçşğâéè", "iuocsgaee"):
        t = t.replace(a, b)
    return re.sub(r"[^a-z0-9.,% ]+", " ", t)


def _paket(text: str) -> Optional[Tuple[float, str]]:
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(kg|gr|g|lt|l|ml|cl)\b", _norm(text))
    if not m:
        return None
    v = float(m.group(1).replace(",", "."))
    u = m.group(2)
    if u in ("gr", "g"):
        return v / 1000, "kg"
    if u == "kg":
        return v, "kg"
    if u == "ml":
        return v / 1000, "l"
    if u == "cl":
        return v / 100, "l"
    return v, "l"


_COKLU = re.compile(r"\d+\s*['’]?\s*(?:li|lı|lu|lü)\b|\b\d+\s*(?:adet|x)\b|\bx\s*\d+\b|\d+\s*paket|koli", re.I)


def esles(sorgu: str, ad: str) -> float:
    """0 = başka ürün; büyük = daha iyi eşleşme."""
    q = [t for t in _norm(sorgu).split() if len(t) >= 2 and not re.fullmatch(r"\d+(?:[.,]\d+)?(?:kg|gr|g|lt|l|ml)?|kg|gr|g|lt|l|ml|adet", t)]
    a = _norm(ad)
    if not q or not a:
        return 0.0
    tut = sum(1 for t in q if t in a)
    if tut < max(2, math.ceil(len(q) * 0.7)):
        return 0.0
    kelime = [t for t in q if not re.search(r"\d", t)]
    if kelime and not re.search(r"(?<![a-z])" + re.escape(kelime[0][:5]), a):  # marka (ilk kelime) şart
        return 0.0
    if kelime and kelime[-1][:4] not in a:  # ürünün ne olduğunu söyleyen son kelime
        return 0.0
    if _COKLU.search(ad) and not _COKLU.search(sorgu):
        return 0.0
    ps, pa = _paket(sorgu), _paket(ad)
    if ps and pa:
        if ps[1] != pa[1]:
            return 0.0
        oran = pa[0] / max(ps[0], 1e-6)
        if not 0.9 <= oran <= 1.1:
            return 0.0
    if any(k in a for k in ("kilif", "ekran koruyucu", "yedek parca", "uyumlu", "aparat")) and not any(
            k in _norm(sorgu) for k in ("kilif", "yedek", "aparat")):
        return 0.0
    return tut / len(q) + (0.3 if ps and pa else 0)


def _ortaklik(url: str) -> str:
    try:
        from lens_engine import _apply_affiliate_url
        return _apply_affiliate_url(url)
    except Exception:
        return url


class MagazaTarayici:
    def __init__(self, bekleme: float = 2.5):
        self.bekleme = bekleme
        self._pw = None
        self._tarayici = None
        self._sayfa = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        tr = os.getenv("TR_PROXY_URL", "").strip()
        proxy = None
        try:
            from ev_baglanti import tunel_acik, EV_PORT
            ev = tunel_acik()
        except Exception:
            ev = False
        if ev:  # önce Kemal'in ev interneti (18 Kas 2026), yoksa ISP proxy'si
            proxy = {"server": f"http://127.0.0.1:{EV_PORT}"}
        elif tr:
            u = urllib.parse.urlsplit(tr)
            proxy = {"server": f"http://{u.hostname}:{u.port}",
                     "username": urllib.parse.unquote(u.username or ""), "password": urllib.parse.unquote(u.password or "")}
        self._pw = sync_playwright().start()
        self._tarayici = self._pw.chromium.launch(headless=True, proxy=proxy,
                                                 args=["--disable-blink-features=AutomationControlled"])
        ctx = self._tarayici.new_context(
            locale="tr-TR", viewport={"width": 1366, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )
        # Görsel, yazı tipi ve video indirme: proxy kotası ve hız için (görsel adresleri yine sayfada okunur)
        ctx.route("**/*", lambda r: r.abort() if r.request.resource_type in ("image", "font", "media") else r.continue_())
        self._sayfa = ctx.new_page()
        return self

    def __exit__(self, *a):
        try:
            self._tarayici.close()
        finally:
            self._pw.stop()

    def kartlar(self, magaza: str, sorgu: str) -> List[Dict]:
        ad, adres, desen, _ = MAGAZALAR[magaza]
        q = urllib.parse.quote(sorgu) if magaza == "vatan" else urllib.parse.quote_plus(sorgu)
        try:
            self._sayfa.goto(adres.format(q=q), timeout=40000, wait_until="domcontentloaded")
            time.sleep(self.bekleme)
            return self._sayfa.evaluate(_JS_KARTLAR, desen) or []
        except Exception:
            return []

    def ara(self, sorgu: str, kategori: str = "") -> List[Dict]:
        """Her açık mağazadan aranan ürünle aynı olan en ucuz teklifi döner (lens_engine teklif biçiminde)."""
        teklifler = []
        for anahtar, (ad, _, _, kapsam) in MAGAZALAR.items():
            if kapsam == "teknoloji" and kategori not in TEKNO_KATEGORI:
                continue
            adaylar = [(esles(sorgu, k["ad"]), k) for k in self.kartlar(anahtar, sorgu)]
            adaylar = [x for x in adaylar if x[0] > 0]
            if not adaylar:
                continue
            en_iyi = max(s for s, _ in adaylar)
            secilen = min((k for s, k in adaylar if s >= en_iyi - 0.05), key=lambda k: k["fiyat"])
            teklifler.append({
                "platform": anahtar, "seller_name": ad, "title": secilen["ad"][:200],
                "price": round(float(secilen["fiyat"]), 2), "unit_price_text": "",
                "url": _ortaklik(secilen["url"]), "is_direct_product": True, "is_branch_only": False,
                "source": "magaza_sitesi", "image_url": secilen.get("img") or "",
            })
        return teklifler


if __name__ == "__main__":
    import json
    import sys
    with MagazaTarayici() as t:
        for s in sys.argv[1:] or ["Sek Tam Yağlı Süt 1 L"]:
            t0 = time.time()
            print(s, json.dumps([(x["seller_name"], x["price"], x["title"][:60]) for x in t.ara(s, "teknoloji_beyaz_esya" if "airfryer" in s.lower() else "gida")], ensure_ascii=False), round(time.time() - t0, 1), "sn")
