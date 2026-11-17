"""
Market Dedektifi — Çoklu Gelir Ortaklığı (Affiliate) Bağlantı Dönüştürücü
Etik Duvar İlkesi:
  Fiyat karşılaştırması ve ürün seçimi tamamen bağımsız yapılır.
  Bu modül YALNIZCA yazı içine konulacak mağaza bağlantılarını (Amazon, Admitad,
  ReklamAction, Awin, Hepsiburada vb.) komisyonlu takip linkine çevirir.
  Komisyon anlaşması olmayan bir zincir market (BİM, A101, ŞOK vb.) en ucuzsa
  bağlantıya müdahale edilmez ve okuyucuya dürüstçe o market önerilir.
"""

from typing import Dict
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


class AffiliateMotoru:
    def __init__(self, aff_cfg: Dict | None = None):
        cfg = aff_cfg or {}
        self.amazon_tag = str(cfg.get("amazon_tag") or "marketdedekti-21").strip()
        self.reklamaction_pub_id = str(cfg.get("reklamaction_pub_id") or "").strip()
        self.admitad_campaign_code = str(cfg.get("admitad_campaign_code") or "").strip()
        self.awin_aff_id = str(cfg.get("awin_aff_id") or "").strip()
        self.awin_merchant_map = cfg.get("awin_merchant_map") or {}
        self.utm_source = str(cfg.get("utm_source") or "basinca_market_dedektifi").strip()

    def linki_donustur(self, ham_url: str, alt_etiket: str = "kose_yazisi") -> str:
        if not ham_url or not ham_url.startswith(("http://", "https://")):
            return ham_url or ""

        parsed = urlparse(ham_url)
        host = parsed.netloc.lower()

        # 1) Amazon.com.tr Resmi Gelir Ortaklığı
        if "amazon.com.tr" in host and self.amazon_tag:
            qs = parse_qs(parsed.query, keep_blank_values=True)
            qs["tag"] = [self.amazon_tag]
            qs["linkCode"] = ["ll1"]
            return urlunparse(parsed._replace(query=urlencode(qs, doseq=True)))

        # 2) Awin Üzerindeki Global Markalar (Samsung, Philips, Dyson, Decathlon vb.)
        if self.awin_aff_id:
            for domain_key, mid in self.awin_merchant_map.items():
                if domain_key.lower() in host and mid:
                    params = urlencode(
                        {
                            "awinmid": str(mid),
                            "awinaffid": self.awin_aff_id,
                            "clickref": alt_etiket,
                            "ued": ham_url,
                        }
                    )
                    return f"https://www.awin1.com/cread.php?{params}"

        # 3) ReklamAction / ReklamStore Web Yayıncı Ağı
        if self.reklamaction_pub_id and any(
            d in host
            for d in (
                "hepsiburada.com",
                "koctas.com.tr",
                "ebebek.com",
                "watsons.com.tr",
                "carrefoursa.com",
                "pazarama.com",
            )
        ):
            params = urlencode({"pub_id": self.reklamaction_pub_id, "subid": alt_etiket, "url": ham_url})
            return f"https://track.reklamaction.com/click?{params}"

        # 4) Admitad Türkiye Deeplink / Moneylink
        if self.admitad_campaign_code and any(
            d in host
            for d in (
                "defacto.com.tr",
                "mediamarkt.com.tr",
                "teknosa.com",
                "aliexpress.com",
                "boyner.com.tr",
            )
        ):
            params = urlencode({"subid": alt_etiket, "ulp": ham_url})
            return f"https://ad.admitad.com/g/{self.admitad_campaign_code}/?{params}"

        # 5) Doğrudan Mağaza Linklerine Kaynak İmzası (UTM) Ekle (dipci.tr ve Harita linkleri hariç)
        if "google.com/maps" not in host and "dipci.tr" not in host and self.utm_source:
            qs = parse_qs(parsed.query, keep_blank_values=True)
            qs.setdefault("utm_source", [self.utm_source])
            qs.setdefault("utm_medium", ["editorial"])
            return urlunparse(parsed._replace(query=urlencode(qs, doseq=True)))

        return ham_url

    @staticmethod
    def yasal_seffaflik_notu() -> str:
        return (
            "Şeffaflık Notu (#işbirliği): Bu köşedeki tüm fiyat, gramaj ve birim maliyet "
            "hesaplamaları dipci.tr canlı veri tabanı üzerinden bağımsız olarak yapılır; "
            "en ucuz ürün komisyon anlaşması olmayan bir zincir marketteyse açıkça o market "
            "önerilir. Bazı e-ticaret bağlantıları gelir ortaklığı içerebilir; bu durum "
            "ödeyeceğiniz fiyatı kesinlikle değiştirmez."
        )
