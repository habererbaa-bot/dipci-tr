"""
Market Dedektifi — dipci.tr Canlı Fiyat ve İndirim Veri İstemcisi
Köşe yazarının hiçbir rakamı uydurmaması (Sıfır Halüsinasyon) için
tüm fiyatları, birim maliyetleri, 30 günlük medyan kanıtlarını ve market sepeti
hesaplamalarını doğrudan canlı https://dipci.tr API'lerinden çeker.
"""

import json
import urllib.parse
import urllib.request
from typing import Dict, List, Optional


KATEGORI_BASLIKLARI: Dict[str, str] = {
    "": "Tüm Kategoriler",
    "gida": "Gıda & Temel Market",
    "temizlik": "Ev Temizlik & Deterjan",
    "kisisel_bakim": "Kişisel Bakım & Kozmetik",
    "anne_bebek": "Anne & Bebek İhtiyaçları",
    "ev_yasam_mutfak": "Ev, Mutfak & Küçük Ev Aletleri",
    "teknoloji_beyaz_esya": "Teknoloji & Beyaz Eşya",
    "saglik_spor_outdoor": "Sağlık, Vitamin & Termos/Outdoor",
    "evcil_hayvan": "Evcil Hayvan Maması & Bakım",
}

MAGAZA_ISIMLERI: Dict[str, str] = {
    "bim": "BİM",
    "a101": "A101",
    "sok": "ŞOK",
    "migros": "Migros",
    "carrefoursa": "CarrefourSA",
    "tarimkredi": "Tarım Kredi",
    "hakmar": "Hakmar Express",
    "amazon": "Amazon.com.tr",
    "hepsiburada": "Hepsiburada",
    "trendyol": "Trendyol",
    "mediamarkt": "MediaMarkt",
    "teknosa": "Teknosa",
    "vatan": "Vatan Bilgisayar",
    "n11": "n11",
    "pazarama": "Pazarama",
    "pttavm": "PttAVM",
    "idefix": "İdefix",
}


class DipciCanliIstemci:
    def __init__(self, origin: str = "https://dipci.tr"):
        self.origin = origin.rstrip("/")

    def _get_json(self, path: str, params: Optional[Dict] = None) -> Dict:
        url = f"{self.origin}{path}"
        if params:
            clean = {k: v for k, v in params.items() if v is not None and v != ""}
            if clean:
                url = f"{url}?{urllib.parse.urlencode(clean)}"
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "MarketDedektifi-Columnist/2.0 (+https://dipci.tr)",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))

    def _post_json(self, path: str, payload: Dict) -> Dict:
        url = f"{self.origin}{path}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "MarketDedektifi-Columnist/2.0 (+https://dipci.tr)",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=18) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))

    def gercek_indirimleri_getir(
        self, kategori: str = "", limit: int = 20, min_indirim: float = 12.0
    ) -> List[Dict]:
        """15 dakikalık canlı indirim radarından 30g medyan altı gerçek fırsatları çeker."""
        data = self._get_json(
            "/api/dedektif/firsatlar",
            {"category": kategori, "limit": limit, "min_drop_pct": min_indirim},
        )
        items = data.get("items") or []
        # Aşırı uç paket uyuşmazlığı şüphesi taşımayan %12-%46 bandındaki en temiz fırsatları öne al
        temiz = [x for x in items if 10.0 <= float(x.get("drop_pct") or 0) <= 46.0]
        return temiz if len(temiz) >= 3 else items

    def kategori_analizi_getir(
        self, kategori: str = "", arama: str = "", limit: int = 48
    ) -> Dict:
        """
        Belirli bir kategorideki veya arama terimindeki:
        1) Gerçek dip fiyat fırsatlarını (yildizlar),
        2) Mağazalar arası en yüksek fiyat uçurumu olan ürünleri (fiyat_ucurumlari — 'Pahalı Mağaza Uyarısı'),
        3) Genel katalog özet istatistiklerini döner.
        """
        vitrin = self._get_json(
            "/api/products",
            {
                "category": kategori,
                "q": arama,
                "sort": "savings",
                "min_stores": 2 if not arama else 0,
                "limit": limit,
            },
        )
        urunler = vitrin.get("items") or []
        ozet = vitrin.get("summary") or {}

        # Gerçek indirim yıldızları
        indirim_ham = self.gercek_indirimleri_getir(kategori=kategori, limit=12)
        if arama:
            q_low = arama.lower()
            indirim_ham = [
                d for d in indirim_ham
                if q_low in f"{d.get('brand', '')} {d.get('family_name', '')}".lower()
            ]

        # İki mağaza arasında ciddi fiyat farkı olan (tüketicinin kazıklanma riski yüksek) ürünler
        ucurumlar = []
        for p in urunler:
            fark = float(p.get("savings_pct") or 0.0)
            teklifler = p.get("top_quotes") or []
            if len(teklifler) >= 2 and 18.0 <= fark <= 62.0:
                en_ucuz = teklifler[0]
                en_pahali = p.get("worst_pack_price") or teklifler[-1].get("pack_price")
                pahali_magaza = MAGAZA_ISIMLERI.get(
                    str(p.get("worst_platform") or ""), str(p.get("worst_platform") or "Rakip Mağaza").upper()
                )
                ucuz_magaza = MAGAZA_ISIMLERI.get(
                    str(en_ucuz.get("platform") or ""), str(en_ucuz.get("seller_name") or "")
                )
                ucurumlar.append(
                    {
                        "product_key": p["product_key"],
                        "urun_adi": f"{p['brand']} {p['family_name']}",
                        "kategori": p["category"],
                        "birim": p["base_unit"],
                        "magaza_sayisi": p["offer_count"],
                        "ucuz_magaza": ucuz_magaza,
                        "ucuz_fiyat": float(en_ucuz.get("pack_price") or 0),
                        "ucuz_birim_fiyat": float(en_ucuz.get("unit_price") or 0),
                        "ucuz_url": en_ucuz.get("tracked_url") or "",
                        "pahali_magaza": pahali_magaza,
                        "pahali_fiyat": float(en_pahali or 0),
                        "fark_tl": round(float(en_pahali or 0) - float(en_ucuz.get("pack_price") or 0), 2),
                        "fark_yuzde": fark,
                        "dipci_url": f"{self.origin}/urun/{p['product_key'].replace('_', '-')}",
                    }
                )

        return {
            "ozet": ozet,
            "yildiz_indirimler": indirim_ham[:8],
            "fiyat_ucurumlari": ucurumlar[:8],
            "tum_urunler": urunler[:15],
        }

    def sepet_savasi_getir(self, hazir_paket_anahtari: str = "") -> Dict:
        """
        Temel mutfak/ev ihtiyaçları sepetini canlı olarak BİM, A101, ŞOK, Migros ve CarrefourSA'da
        karşılaştırır; tek market şampiyonunu ve 2 marketli bölünmüş dip rotayı döner.
        """
        paketler_yanit = self._get_json("/api/basket/presets")
        paketler = paketler_yanit.get("presets") or []
        secilen = None
        if hazir_paket_anahtari:
            secilen = next((p for p in paketler if p.get("key") == hazir_paket_anahtari), None)
        if not secilen and paketler:
            secilen = paketler[0]

        kalemler = (
            [{"product_key": i["product_key"], "qty": i.get("qty", 1)} for i in (secilen.get("items") or [])]
            if secilen
            else [
                {"product_key": "temel_sut_1lt", "qty": 2},
                {"product_key": "temel_yumurta", "qty": 1},
                {"product_key": "temel_beyaz_peynir", "qty": 1},
                {"product_key": "temel_tereyagi", "qty": 1},
                {"product_key": "temel_aycicek_yagi", "qty": 1},
                {"product_key": "temel_cay", "qty": 1},
                {"product_key": "temel_toz_seker", "qty": 1},
                {"product_key": "temel_makarna", "qty": 2},
            ]
        )

        sonuc = self._post_json("/api/basket/optimize", {"items": kalemler})
        return {
            "sepet_basligi": secilen.get("title") if secilen else "Temel Mutfak Sepeti",
            "sepet_aciklamasi": secilen.get("subtitle") if secilen else "",
            "optimizasyon": sonuc,
        }

    def tekil_urun_rontgeni_getir(self, sorgu_veya_key: str) -> Optional[Dict]:
        """Tek bir ürünün tüm mağaza tekliflerini, birim maliyetini ve 90g geçmişini çeker."""
        temiz = (sorgu_veya_key or "").strip()
        if not temiz:
            return None
        # Önce doğrudan key dene, olmazsa arama yapıp ilk ürünü al
        aday_key = temiz.lower().replace("-", "_")
        try:
            detay = self._get_json(f"/api/products/{urllib.parse.quote(aday_key)}")
            if detay and detay.get("product"):
                return detay
        except Exception:
            pass

        arama = self._get_json("/api/products", {"q": temiz, "live": 1, "limit": 5})
        items = arama.get("items") or []
        if not items:
            return None
        ilk_key = items[0]["product_key"]
        return self._get_json(f"/api/products/{urllib.parse.quote(ilk_key)}")
