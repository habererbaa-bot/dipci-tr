"""
Market Dedektifi (basinca.com) — Merkezi Yapılandırma ve Parametreler
"""

from dataclasses import dataclass, field
from typing import Dict

@dataclass(frozen=True)
class AffiliateConfig:
    # Resmi aktif Amazon Türkiye Ortak Kimliğimiz
    amazon_tag: str = "marketdedekti-21"
    # ReklamAction ve Admitad onaylandığında buraya eklenecek yayıncı kodları
    reklamaction_pub_id: str = ""
    admitad_campaign_id: str = ""
    include_legal_disclosure: bool = True


@dataclass(frozen=True)
class AuditThresholds:
    # Denetim 1: Sahte indirim / Önce bindir sonra indir eşikleri
    min_genuine_discount_pct: float = 10.0      # 30g medyana göre en az %10 indirim olmalı
    stock_up_discount_pct: float = 20.0         # %20 ve üzeri gerçek indirim -> "Stokla!"
    anchor_inflation_ratio: float = 1.18        # Üstü çizili fiyat, 30g medyandan %18+ yüksekse şüpheli

    # Denetim 2: Çapraz piyasa (Cross-Market) kıyas eşikleri
    cross_market_edge_pct: float = 8.0          # En yakın güvenilir rakipten en az %8 ucuzsa gerçek fırsat
    overpriced_vs_rival_pct: float = 5.0        # "İndirimli" hali bile rakibin düz fiyatından %5+ pahalıysa tuzak

    # Denetim 3: Shrinkflation (Gizli gramaj/yaprak düşüşü)
    shrinkflation_tolerance_pct: float = 2.0    # %2'den fazla gramaj/adet düşüşü varsa alarm ver

    # Denetim 5: Satıcı güvenilirliği ve sahte ürün/SKT riski
    min_third_party_rating: float = 9.3         # Pazaryeri 3. parti satıcı minimum puanı (10 üzerinden)
    suspicious_cheap_anomaly_pct: float = 35.0  # 3. parti satıcı piyasanın %35+ altındaysa sahte/yakın SKT riski

    # Denetim 7: Teknoloji & Beyaz Eşya taksit fırsat maliyeti (aylık reel iskonto oranı)
    monthly_opportunity_rate: float = 0.032     # Aylık %3.2 vade farksız taksit avantaj katsayısı


TRUSTED_PLATFORMS: Dict[str, str] = {
    "amazon": "Amazon.com.tr",
    "migros": "Migros Sanal Market",
    "carrefoursa": "CarrefourSA Online",
    "a101": "A101 (Kapıda / Ekstra)",
    "sok": "Cepte Şok",
    "bim": "BİM (Aktüel / Raf)",
    "file": "FİLE Market",
    "hepsiburada": "Hepsiburada",
    "trendyol": "Trendyol",
    "ebebek": "Ebebek",
    "gratis": "Gratis",
    "watsons": "Watsons",
    "rossmann": "Rossmann",
    "vatan": "Vatan Bilgisayar",
    "mediamarkt": "MediaMarkt",
    "teknosa": "Teknosa",
    "tarimkredi": "Tarım Kredi Kooperatif",
    "hakmar": "Hakmar Express",
    "marketfiyati": "MarketFiyati.org.tr (Resmi Referans)",
}

AFFILIATE_CONFIG = AffiliateConfig()
AUDIT_THRESHOLDS = AuditThresholds()
