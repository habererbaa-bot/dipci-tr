"""
Market Dedektifi — Gelir Ortaklığı (Affiliate) Bağlantı Motoru
Etik Duvar İlkesi: Algoritma puanlama yaparken komisyon oranını ASLA görmez.
Sadece denetimden geçip okuyucuya gerçekten önerilen ürünlerin linkleri
bu modülden geçerek gelir ortaklığı etiketine (?tag=marketdedekti-21 vb.) dönüştürülür.
"""

from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from config import AFFILIATE_CONFIG, AffiliateConfig


class AffiliateLinkBuilder:
    def __init__(self, cfg: AffiliateConfig = AFFILIATE_CONFIG):
        self.cfg = cfg

    def build_tracking_url(self, raw_url: str, sub_id: str = "basinca_md") -> str:
        """
        Desteklenen mağaza linklerini otomatik olarak komisyonlu takip linkine çevirir.
        Komisyon anlaşması olmayan sitelerin (örn. BİM, A101) linkine dokunmaz.
        """
        if not raw_url:
            return raw_url

        parsed = urlparse(raw_url)
        host = parsed.netloc.lower()

        # 1) Amazon Türkiye Resmi Ortak Kimliği (marketdedekti-21)
        if "amazon.com.tr" in host and self.cfg.amazon_tag:
            qs = parse_qs(parsed.query, keep_blank_values=True)
            qs["tag"] = [self.cfg.amazon_tag]
            qs["linkCode"] = ["ll1"]
            new_query = urlencode(qs, doseq=True)
            return urlunparse(parsed._replace(query=new_query))

        # 2) ReklamAction onaylandığında (Hepsiburada, Ebebek, Watsons, Getir vb.)
        if self.cfg.reklamaction_pub_id and any(
            d in host for d in ("hepsiburada.com", "ebebek.com", "watsons.com.tr", "koctas.com.tr")
        ):
            encoded_dest = urlencode({"url": raw_url, "subid": sub_id})
            return f"https://track.reklamaction.com/click?pub_id={self.cfg.reklamaction_pub_id}&{encoded_dest}"

        # 3) Admitad onaylandığında
        if self.cfg.admitad_campaign_id and any(d in host for d in ("mediamarkt.com.tr", "teknosa.com")):
            encoded_dest = urlencode({"ulp": raw_url, "subid": sub_id})
            return f"https://ad.admitad.com/g/{self.cfg.admitad_campaign_id}/?{encoded_dest}"

        return raw_url

    @staticmethod
    def legal_disclosure_footer() -> str:
        return (
            "_Şeffaflık Notu (#işbirliği): Köşemizdeki tüm fiyat ve birim maliyet analizleri tamamen "
            "bağımsız algoritmamız tarafından yapılır; en ucuz ürün komisyon vermeyen bir marketteyse "
            "açıkça orası önerilir. Bazı mağaza bağlantıları gelir ortaklığı içerebilir; bu durum "
            "ödeyeceğiniz fiyatı kesinlikle değiştirmez._"
        )
