"""
dipci.tr — Başlangıç Katalog, 90 Günlük Fiyat Hafızası, Barkod Havuzu ve Doğrulanmış Topluluk Verisi
Uygulama ilk kez ayağa kalktığında gerçekçi Türkiye piyasa verisiyle veritabanını hazırlar.
"""

from datetime import datetime, timedelta
from typing import Dict, List

from memory_db import PriceMemoryDB
from scanner import compute_ean13_with_check
from social_community import SocialCommunityService


VS_COMPARISONS: Dict[str, Dict] = {
    "fairy_platinum_plus": {
        "rival_name": "Finish Quantum Max Kapsül",
        "rows": [
            {"label": "1 Yıkama En Dip Maliyeti", "a": "5,19 TL (Kazanan)", "b": "6,40 TL", "win": "a"},
            {"label": "Kısa Programda Çözünme", "a": "Çok Hızlı (3 Sıvı Bölmeli)", "b": "Orta", "win": "a"},
            {"label": "Ön Yıkamasız Yağ Sökme", "a": "Yüksek", "b": "Yüksek", "win": "tie"},
        ],
    },
    "philips_aqua_8000": {
        "rival_name": "Dyson V12 Detect Slim Absolute",
        "rows": [
            {"label": "Taksitli Reel Maliyet", "a": "15.684 TL (9 Taksit)", "b": "24.999 TL (Tek Çekim)", "win": "a"},
            {"label": "Islak Silme (Mop) Başlığı", "a": "Var (Aqua Plus Başlık)", "b": "Yok", "win": "a"},
            {"label": "Gövde Ağırlığı", "a": "2,9 kg", "b": "2,2 kg (Daha Hafif)", "win": "b"},
            {"label": "Maksimum Pil Süresi", "a": "80 Dakika", "b": "60 Dakika", "win": "a"},
        ],
    },
    "sutas_kasar_peyniri": {
        "rival_name": "Geçen Ayki 1000g Eski Kalıp",
        "rows": [
            {"label": "Paket Net Gramajı", "a": "850 g (Yeni Kalıp)", "b": "1.000 g (Eski Kalıp)", "win": "b"},
            {"label": "1 Kg Gerçek Birim Fiyat", "a": "382,35 TL / kg", "b": "340,00 TL / kg", "win": "b"},
            {"label": "Süt Yağ Oranı", "a": "Tam Yağlı (%45)", "b": "Tam Yağlı (%45)", "win": "tie"},
        ],
    },
    "prima_premium_care_4": {
        "rival_name": "Molfix Pure & Soft 4 Numara",
        "rows": [
            {"label": "1 Adet Bez Birim Fiyatı", "a": "6,45 TL / adet", "b": "5,20 TL / adet", "win": "b"},
            {"label": "Gece Sızdırmazlık & Yumuşaklık", "a": "Çok Yüksek (12 Saat)", "b": "İyi", "win": "a"},
            {"label": "Islaklık Göstergesi", "a": "Var", "b": "Var", "win": "tie"},
        ],
    },
}


def ensure_seeded(db: PriceMemoryDB, social: SocialCommunityService) -> None:
    db.conn.execute(
        """
        CREATE TABLE IF NOT EXISTS partner_xml_feeds (
            feed_id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_name TEXT NOT NULL,
            brand_color TEXT DEFAULT '#2563EB',
            xml_url TEXT NOT NULL,
            contact_email TEXT DEFAULT '',
            status TEXT DEFAULT 'PENDING_VERIFICATION',
            created_at TEXT NOT NULL
        );
        """
    )
    db.conn.commit()

    existing = db.conn.execute("SELECT COUNT(*) AS cnt FROM canonical_products").fetchone()
    if existing and existing["cnt"] > 0:
        return

    now = datetime.now()

    # 1. Ana Ürün Aileleri ve Market Markalı Muadiller
    db.register_product(
        product_key="fairy_platinum_plus",
        brand="Fairy",
        family_name="Platinum Plus Bulaşık Makinesi Kapsülü",
        category="temizlik",
        base_unit="yıkama",
        reference_net_amount=50.0,
        equivalent_group="bulasik_kapsulu_premium",
    )
    db.register_product(
        product_key="bim_bind_activit",
        brand="Bind Activit",
        family_name="Premium Bulaşık Makinesi Kapsülü (BİM)",
        category="temizlik",
        base_unit="yıkama",
        reference_net_amount=50.0,
        is_private_label=True,
        equivalent_group="bulasik_kapsulu_premium",
    )
    db.register_product(
        product_key="philips_aqua_8000",
        brand="Philips",
        family_name="8000 Series Aqua Plus Kablosuz Dik Süpürge",
        category="teknoloji_beyaz_esya",
        base_unit="adet",
        reference_net_amount=1.0,
        official_model_code="XC8057/01",
    )
    db.register_product(
        product_key="sutas_kasar_peyniri",
        brand="Sütaş",
        family_name="Tam Yağlı Taze Kaşar Peyniri",
        category="gida",
        base_unit="kg",
        reference_net_amount=1.0,  # Eskiden 1.0 kg idi -> 0.85 kg'a düştü!
        equivalent_group="taze_kasar_tam_yagli",
    )
    db.register_product(
        product_key="a101_ahir_kasar",
        brand="Ahir (A101)",
        family_name="Tam Yağlı Taze Kaşar Peyniri 1 Kg",
        category="gida",
        base_unit="kg",
        reference_net_amount=1.0,
        is_private_label=True,
        equivalent_group="taze_kasar_tam_yagli",
    )
    db.register_product(
        product_key="prima_premium_care_4",
        brand="Prima",
        family_name="Premium Care 4 Numara Maxi Bebek Bezi",
        category="anne_bebek",
        base_unit="adet",
        reference_net_amount=108.0,
    )

    # 2. Resmi GS1 (869...) Barkod Havuzu
    ean_fairy_50 = compute_ean13_with_check("869050651412")
    ean_fairy_100 = compute_ean13_with_check("869050653910")
    ean_philips = compute_ean13_with_check("872038902914")
    ean_sutas_850 = compute_ean13_with_check("869052608501")
    ean_prima_108 = compute_ean13_with_check("869050681084")

    db.register_barcode(ean_fairy_50, "fairy_platinum_plus", "50 Yıkama Tekli Paket", 50.0, "SINGLE")
    db.register_barcode(ean_fairy_100, "fairy_platinum_plus", "100 Yıkama (2x50) Avantaj Paketi", 100.0, "MULTI_BULK")
    db.register_barcode(ean_philips, "philips_aqua_8000", "XC8057/01 Kutu", 1.0, "SINGLE")
    db.register_barcode(ean_sutas_850, "sutas_kasar_peyniri", "850g Yeni Kalıp", 0.85, "SINGLE")
    db.register_barcode(ean_prima_108, "prima_premium_care_4", "108 Adet Aylık Fırsat Paketi", 108.0, "MULTI_BULK")

    # 3. 90 Günlük Tarihsel Fiyat Hafızası Serileri
    fairy_hist = [7.45, 7.30, 7.20, 7.10, 7.25, 7.50, 7.15, 6.85]
    philips_hist = [20499, 19999, 19800, 20750, 19800, 19600, 19200, 18999]
    sutas_hist = [335.0, 340.0, 340.0, 340.0, 345.0, 345.0, 410.0, 395.0]
    prima_hist = [7.90, 7.80, 7.75, 7.85, 7.60, 7.70, 7.50, 7.40]

    for idx, days_ago in enumerate([84, 72, 60, 48, 36, 24, 12, 3]):
        ts = now - timedelta(days=days_ago)
        db.record_snapshot(
            "fairy_platinum_plus", "migros", "Migros Sanal Market", "Fairy Platinum Plus 50'li",
            "https://www.migros.com.tr/fairy-50", fairy_hist[idx] * 50, fairy_hist[idx] * 50, fairy_hist[idx], 50.0, captured_at=ts
        )
        db.record_snapshot(
            "philips_aqua_8000", "vatan", "Vatan Bilgisayar", "Philips XC8057/01",
            "https://www.vatanbilgisayar.com/philips-xc8057", philips_hist[idx], philips_hist[idx], philips_hist[idx], 1.0, captured_at=ts
        )
        db.record_snapshot(
            "sutas_kasar_peyniri", "carrefoursa", "CarrefourSA Online", "Sütaş Kaşar 1000g",
            "https://www.carrefoursa.com/sutas-kasar", sutas_hist[idx], sutas_hist[idx], sutas_hist[idx], 1.0 if days_ago > 15 else 0.85, captured_at=ts
        )
        db.record_snapshot(
            "prima_premium_care_4", "hepsiburada", "Hepsiburada Resmi", "Prima Premium Care 4 Beden 108'li",
            "https://www.hepsiburada.com/prima-4-108", prima_hist[idx] * 108, prima_hist[idx] * 108, prima_hist[idx], 108.0, captured_at=ts
        )

    # 4. Bugünkü Canlı Mağaza ve Paket Seçenekleri
    # Fairy — 4 farklı boyut + 1 şüpheli 3. parti satıcı + BİM muadil
    db.record_snapshot(
        "fairy_platinum_plus", "amazon", "Amazon.com.tr", "Fairy Platinum Plus 100 Yıkama (2x50) • Prime",
        "https://www.amazon.com.tr/s?k=Fairy+Platinum+Plus+Bula%C5%9F%C4%B1k+Makinesi+Kaps%C3%BCl%C3%BC", 519.0, 519.0, 5.19, 100.0, list_price=749.0, pack_tier="MULTI_BULK"
    )
    db.record_snapshot(
        "fairy_platinum_plus", "hepsiburada", "Hepsiburada Resmi", "Fairy Platinum Plus 75 Yıkama",
        "https://www.hepsiburada.com/ara?q=Fairy+Platinum+Plus+75", 465.0, 465.0, 6.20, 75.0, list_price=549.0, pack_tier="SINGLE"
    )
    db.record_snapshot(
        "fairy_platinum_plus", "migros", "Migros Hemen", "Fairy Platinum Plus 50 Yıkama • 45 Dk Teslimat",
        "https://www.migros.com.tr/fairy-platinum-50li-745-g-p-1d8b7dc", 399.0, 399.0, 7.98, 50.0, pack_tier="SINGLE"
    )
    db.record_snapshot(
        "fairy_platinum_plus", "hepsiburada", "Hepsiburada Resmi", "Fairy Platinum Plus 150 Yıkama Dev Koli",
        "https://www.hepsiburada.com/ara?q=Fairy+Platinum+Plus+150", 1095.0, 1095.0, 7.30, 150.0, pack_tier="MULTI_BULK"
    )
    db.record_snapshot(
        "fairy_platinum_plus", "trendyol", "KuzeyToptanTicaret", "Fairy 100'lü Fırsat",
        "https://www.trendyol.com/sr?q=Fairy+Platinum+Plus", 305.0, 305.0, 3.05, 100.0, seller_type="THIRD_PARTY", seller_rating=8.1, pack_tier="MULTI_BULK"
    )
    db.record_snapshot(
        "bim_bind_activit", "bim", "BİM Mağaza Rafı", "Bind Activit Premium 50 Kapsül",
        "https://www.google.com/maps/search/B%C4%B0M+Market", 155.0, 155.0, 3.10, 50.0
    )

    # Philips 8000 Aqua Plus — Taksit NPV Şampiyonu
    db.record_snapshot(
        "philips_aqua_8000", "amazon", "Amazon.com.tr", "Philips 8000 Aqua Plus XC8057/01 • Peşin Fiyatına 9 Taksit",
        "https://www.amazon.com.tr/s?k=Philips+XC8057%2F01+Aqua+Plus", 18299.0, 18299.0, 18299.0, 1.0,
        list_price=20499.0, zero_interest_months=9, warranty_type="TR_OFFICIAL", model_code_seen="XC8057/01"
    )
    db.record_snapshot(
        "philips_aqua_8000", "vatan", "Vatan Bilgisayar", "Philips XC8057/01 • Mağazadan 1 Saatte Gel-Al",
        "https://www.vatanbilgisayar.com/arama/XC8057/", 17950.0, 17950.0, 17950.0, 1.0,
        zero_interest_months=1, warranty_type="TR_OFFICIAL", model_code_seen="XC8057/01"
    )
    db.record_snapshot(
        "philips_aqua_8000", "mediamarkt", "MediaMarkt", "Philips XC8057/01 • Peşin Fiyatına 3 Taksit",
        "https://www.mediamarkt.com.tr/tr/search.html?query=Philips+XC8057", 18999.0, 18999.0, 18999.0, 1.0,
        zero_interest_months=3, warranty_type="TR_OFFICIAL", model_code_seen="XC8057/01"
    )

    # Sütaş Kaşar — Shrinkflation + Sahte İndirim Örneği
    db.record_snapshot(
        "sutas_kasar_peyniri", "carrefoursa", "CarrefourSA Online", "Sütaş Tam Yağlı Taze Kaşar 850 g (Yeni Paket)",
        "https://www.carrefoursa.com/search/?text=S%C3%BCta%C5%9F+Taze+Ka%C5%9Far", 325.0, 325.0, 382.35, 0.85, list_price=369.0
    )
    db.record_snapshot(
        "sutas_kasar_peyniri", "migros", "Migros Sanal Market", "Sütaş Taze Kaşar 700 g",
        "https://www.migros.com.tr/arama?q=S%C3%BCta%C5%9F+Taze+Ka%C5%9Far", 245.0, 245.0, 350.0, 0.70
    )
    db.record_snapshot(
        "a101_ahir_kasar", "a101", "A101 Kapıda", "Ahir Tam Yağlı Taze Kaşar 1000 g",
        "https://www.a101.com.tr/list/?search_text=Ahir+Taze+Ka%C5%9Far", 279.0, 279.0, 279.0, 1.0
    )

    # Prima Bebek Bezi
    db.record_snapshot(
        "prima_premium_care_4", "amazon", "Amazon.com.tr", "Prima Premium Care 4 Beden 108 Adet Aylık Paket",
        "https://www.amazon.com.tr/s?k=Prima+Premium+Care+4+Beden", 696.60, 696.60, 6.45, 108.0, list_price=849.0, pack_tier="MULTI_BULK"
    )
    db.record_snapshot(
        "prima_premium_care_4", "hepsiburada", "Hepsiburada Resmi", "Prima Premium Care 4 Beden 108 Adet",
        "https://www.hepsiburada.com/ara?q=Prima+Premium+Care+4+Beden+108", 769.0, 769.0, 7.12, 108.0, pack_tier="MULTI_BULK"
    )

    # 5. Örnek Doğrulanmış Üyeler, Mikro-Sosyal Yorumlar ve Topluluk Fırsat Akışı
    r1 = social.request_phone_otp("05321112233", "Ayşe Kaya", force_demo=True)
    v1 = social.verify_phone_otp("05321112233", r1["demo_sms_code"])
    tok_ayse = v1["session_token"]
    social.record_member_camera_scan(v1["member"]["member_id"], "fairy_platinum_plus")
    social.record_member_camera_scan(v1["member"]["member_id"], "sutas_kasar_peyniri")

    r2 = social.request_phone_otp("05442223344", "Burak Demir", force_demo=True)
    v2 = social.verify_phone_otp("05442223344", r2["demo_sms_code"])
    tok_burak = v2["session_token"]
    social.record_member_camera_scan(v2["member"]["member_id"], "philips_aqua_8000")

    social.add_product_post(
        tok_ayse,
        "fairy_platinum_plus",
        "COUPON_TIP",
        "Amazon mobil uygulamasında 'Abone Ol ve Tasarruf Et' kutucuğunu seçip ilk teslimattan sonra iptal ederseniz %10 ekstra düşüyor, yıkama başı 4,67 TL'ye indi!",
        coupon_code="ABONE10",
    )
    social.add_product_post(
        tok_burak,
        "philips_aqua_8000",
        "LONG_TERM_REVIEW",
        "8 aydır evde kedi tüyü ve mutfak zemini için kullanıyoruz. Islak silme başlığı günlük paspas ihtiyacını bitirdi; sadece filtreyi ayda bir yıkayıp tam kurutmadan takmayın.",
        usage_months=8,
    )
    social.add_product_post(
        tok_ayse,
        "sutas_kasar_peyniri",
        "FIRST_IMPRESSION",
        "Bugün market reyonunda kamerayla okutunca fark ettim, kalıp boyutu aynı duruyor ama köşesinde 850g yazıyor! Kilosu 382 TL'ye fırlamış.",
    )

    social.share_community_deal(
        tok_ayse,
        "Fairy Platinum Plus 100 Kapsül (2x50 Paket)",
        "https://www.amazon.com.tr/s?k=Fairy+Platinum+Plus+Bula%C5%9F%C4%B1k+Makinesi+Kaps%C3%BCl%C3%BC",
        519.0,
        coupon_code="ABONE10",
        note="Reyonda 50'lisi 439 TL iken Amazon'da 100'lüsü 519 TL!",
        product_key="fairy_platinum_plus",
    )
    social.share_community_deal(
        tok_burak,
        "Prima Premium Care 4 Beden 108 Adet Aylık Paket",
        "https://www.amazon.com.tr/s?k=Prima+Premium+Care+4+Beden",
        696.60,
        note="Tanesi 6,45 TL'ye geliyor, son 3 ayın en iyi birim fiyatı.",
        product_key="prima_premium_care_4",
    )
