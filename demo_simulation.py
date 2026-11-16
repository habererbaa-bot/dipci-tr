"""
Market Dedektifi — Uçtan Uca Algoritmik Doğrulama Simülasyonu
60 günlük geçmiş fiyat/gramaj hafızasını kurar, bugünün kampanyalarını 7 çapraz denetimden geçirir
ve basinca.com yayınına hazır örnek köşe yazısını üretir.
"""

from datetime import datetime, timedelta
from pathlib import Path

from audit_engine import MarketDedektifiAuditEngine
from columnist import MarketDedektifiColumnist
from memory_db import PriceMemoryDB
from normalizer import CampaignRule, UnitNormalizer


def run_simulation() -> None:
    base_dir = Path(__file__).parent
    db_file = base_dir / "fiyat_hafizasi.db"
    if db_file.exists():
        db_file.unlink()

    db = PriceMemoryDB(db_file)
    now = datetime(2026, 11, 15, 2, 35, 0)

    # ------------------------------------------------------------------
    # 1. KANONİK ÜRÜN KAYITLARI
    # ------------------------------------------------------------------
    db.register_product(
        "fairy_plat_plus", "Fairy", "Platinum Plus Bulaşık Makinesi Kapsülü",
        "temizlik", "yıkama", 75.0, is_private_label=False,
        equivalent_group="bulasik_kapsulu_premium",
    )
    db.register_product(
        "bind_aktivit_kapsul", "Bind Activit", "Premium Hepsi Bir Arada Bulaşık Kapsülü (BİM)",
        "temizlik", "yıkama", 50.0, is_private_label=True,
        equivalent_group="bulasik_kapsulu_premium",
    )
    db.register_product(
        "elseve_sampuan", "L'Oréal Elseve", "Dream Long Onarıcı Şampuan",
        "kisisel_bakim", "litre", 0.45,
    )
    db.register_product(
        "sutas_kasar", "Sütaş", "Tam Yağlı Taze Kaşar Peyniri",
        "gida", "kg", 1.0,  # Tarihsel standart paket 1.0 kg idi!
    )
    db.register_product(
        "papia_tuvalet_kagidi", "Papia", "Bambu Özlü 3 Katlı Tuvalet Kağıdı",
        "temizlik", "100 std yaprak", 24.0,
    )
    db.register_product(
        "ariel_toz_deterjan", "Ariel", "Dağ Esintisi Toz Çamaşır Deterjanı 9 kg",
        "temizlik", "kg", 9.0,
    )
    db.register_product(
        "philips_xc8057", "Philips", "8000 Series Aqua Plus Kablosuz Dikey Süpürge",
        "teknoloji_beyaz_esya", "adet", 1.0, official_model_code="XC8057/01",
    )

    # ------------------------------------------------------------------
    # 2. SON 60 GÜNLÜK FİYAT HAFIZASI TOHUMLAMA (Tarihsel Baz)
    # ------------------------------------------------------------------
    for day_ago in range(60, 2, -5):
        ts = now - timedelta(days=day_ago)
        # Fairy normalde 75 kapsül 540 TL (7.20 TL / yıkama)
        db.record_snapshot(
            "fairy_plat_plus", "migros", "Migros", "Fairy Platinum Plus 75 Kapsül",
            "https://www.migros.com.tr/fairy-75", 540.0, 540.0, 7.20, 75.0, captured_at=ts,
        )
        # Elseve 450 ml normalde 135 TL (300 TL / litre)
        db.record_snapshot(
            "elseve_sampuan", "amazon", "Amazon.com.tr", "Elseve Dream Long 450 ml",
            "https://www.amazon.com.tr/dp/B08ELSEVE", 135.0, 135.0, 300.0, 0.45, captured_at=ts,
        )
        # Sütaş Kaşar eskiden 1000g = 340 TL (340 TL / kg)
        db.record_snapshot(
            "sutas_kasar", "migros", "Migros", "Sütaş Kaşar Peyniri 1000 g",
            "https://www.migros.com.tr/sutas-1kg", 340.0, 340.0, 340.0, 1.0, captured_at=ts,
        )
        # Ariel 9 kg normalde 495 TL (55 TL / kg)
        db.record_snapshot(
            "ariel_toz_deterjan", "amazon", "Amazon.com.tr", "Ariel Dağ Esintisi 9 kg",
            "https://www.amazon.com.tr/dp/B09ARIEL9KG", 495.0, 495.0, 55.0, 9.0, captured_at=ts,
        )
        # Philips XC8057 normalde 19.800 TL
        db.record_snapshot(
            "philips_xc8057", "mediamarkt", "MediaMarkt", "Philips XC8057/01 Aqua Plus",
            "https://www.mediamarkt.com.tr/philips-xc8057", 19800.0, 19800.0, 19800.0, 1.0,
            captured_at=ts, model_code_seen="XC8057/01",
        )

    # ------------------------------------------------------------------
    # 3. BUGÜNÜN CANLI PİYASA KAMPANYALARI (Çapraz Denetime Girecek Adaylar)
    # ------------------------------------------------------------------

    # SENARYO 1: GERÇEK DİP FİYAT (Amazon Çok Al Az Öde: 2 Adet 100'lü alımda %10 ek indirim!)
    amt, unit = UnitNormalizer.parse_title_quantity("Fairy Platinum Plus 100 Kapsül")
    fairy_offer = UnitNormalizer.calculate_normalized_offer(
        sale_price=520.0, net_amount=amt, base_unit=unit,
        campaign=CampaignRule(campaign_type="MULTI_BUY_PCT", buy_qty=2, extra_discount_pct=10.0),
    )
    db.record_snapshot(
        "fairy_plat_plus", "amazon", "Amazon.com.tr",
        "Fairy Platinum Plus 100 Kapsül (2 Al %10 Az Öde)",
        "https://www.amazon.com.tr/dp/B09FAIRY100",
        sale_price=520.0, effective_pack_price=fairy_offer.effective_pack_price,
        effective_unit_price=fairy_offer.effective_unit_price,
        net_amount=amt, captured_at=now, list_price=649.0,
    )
    # Rakip Migros güncel fiyat (75 kapsül = 499 TL -> 6.65 TL/yıkama)
    db.record_snapshot(
        "fairy_plat_plus", "migros", "Migros", "Fairy Platinum Plus 75 Kapsül",
        "https://www.migros.com.tr/fairy-75", 499.0, 499.0, round(499.0 / 75.0, 4), 75.0, captured_at=now,
    )
    # BİM Muadil (Bind Activit 50 kapsül = 135 TL -> 2.70 TL/yıkama!)
    db.record_snapshot(
        "bind_aktivit_kapsul", "bim", "BİM", "Bind Activit Premium 50 Kapsül",
        "https://www.bim.com.tr/", 135.0, 135.0, 2.70, 50.0, captured_at=now,
    )

    # SENARYO 2: SAHTE İNDİRİM ("Gratis'te %60 İndirim" diye 390 -> 156 TL yazılmış ama Amazon'da düz fiyat 129 TL!)
    db.record_snapshot(
        "elseve_sampuan", "gratis", "Gratis", "Elseve Dream Long Şampuan 450 ml",
        "https://www.gratis.com/elseve-450ml",
        sale_price=156.0, effective_pack_price=156.0, effective_unit_price=round(156.0 / 0.45, 4),
        net_amount=0.45, captured_at=now, list_price=390.0,
    )
    db.record_snapshot(
        "elseve_sampuan", "amazon", "Amazon.com.tr", "Elseve Dream Long Şampuan 450 ml",
        "https://www.amazon.com.tr/dp/B08ELSEVE",
        sale_price=129.0, effective_pack_price=129.0, effective_unit_price=round(129.0 / 0.45, 4),
        net_amount=0.45, captured_at=now,
    )

    # SENARYO 3: GİZLİ GRAMAJ KÜÇÜLTME (Shrinkflation: 1000g -> 850g düşmüş, paket fiyatı 340 -> 325 TL olmuş!)
    amt_k, unit_k = UnitNormalizer.parse_title_quantity("Sütaş Tam Yağlı Taze Kaşar Peyniri 850 g")
    db.record_snapshot(
        "sutas_kasar", "carrefoursa", "CarrefourSA", "Sütaş Tam Yağlı Taze Kaşar Peyniri 850 g",
        "https://www.carrefoursa.com/sutas-850g",
        sale_price=325.0, effective_pack_price=325.0, effective_unit_price=round(325.0 / amt_k, 4),
        net_amount=amt_k, captured_at=now, list_price=349.0,
    )

    # SENARYO 4: ÇOKLU PAKET / YAPRAK SAYISI TUZAĞI (32 Rulo ama 120 yaprak vs 16 Rulo 160 yaprak!)
    amt_16, u_tp = UnitNormalizer.parse_title_quantity("Papia Tuvalet Kağıdı 16 Rulo 3 Katlı 160 Yaprak")
    amt_32, _ = UnitNormalizer.parse_title_quantity("Papia Tuvalet Kağıdı 32 Rulo 3 Katlı 120 Yaprak")
    db.record_snapshot(
        "papia_tuvalet_kagidi", "migros", "Migros", "Papia 16 Rulo (160 Yaprak/Rulo)",
        "https://www.migros.com.tr/papia-16", 185.0, 185.0, round(185.0 / amt_16, 4),
        amt_16, captured_at=now, pack_tier="SINGLE",
    )
    db.record_snapshot(
        "papia_tuvalet_kagidi", "trendyol", "Trendyol Süpermarket", "Papia Dev Ekonomi 32 Rulo (120 Yaprak/Rulo)",
        "https://www.trendyol.com/papia-32", 319.0, 319.0, round(319.0 / amt_32, 4),
        amt_32, captured_at=now, pack_tier="MULTI_BULK",
    )

    # SENARYO 5: SAHTE ÜRÜN / ŞÜPHELİ 3. PARTİ SATICI RİSKİ (Piyasası 495 TL olan deterjan 265 TL!)
    db.record_snapshot(
        "ariel_toz_deterjan", "trendyol", "KuzeyToptanTicaret", "Ariel Dağ Esintisi 9 kg",
        "https://www.trendyol.com/ariel-9kg-x", 265.0, 265.0, round(265.0 / 9.0, 4),
        9.0, captured_at=now, seller_type="THIRD_PARTY", seller_rating=8.1,
    )
    db.record_snapshot(
        "ariel_toz_deterjan", "amazon", "Amazon.com.tr", "Ariel Dağ Esintisi 9 kg (Resmi Satıcı)",
        "https://www.amazon.com.tr/dp/B09ARIEL9KG", 469.0, 469.0, round(469.0 / 9.0, 4),
        9.0, captured_at=now, seller_type="OFFICIAL", seller_rating=10.0,
    )

    # SENARYO 6: TEKNOLOJİ & BEYAZ EŞYA — PEŞİN FİYATINA 9 TAKSİT NPV KAZANANI
    db.record_snapshot(
        "philips_xc8057", "amazon", "Amazon.com.tr", "Philips 8000 Series Aqua Plus XC8057/01",
        "https://www.amazon.com.tr/dp/B0C8PHILIPS",
        sale_price=18299.0, effective_pack_price=18299.0, effective_unit_price=18299.0,
        net_amount=1.0, captured_at=now, list_price=20999.0,
        warranty_type="TR_OFFICIAL", zero_interest_months=9, model_code_seen="XC8057/01",
    )
    db.record_snapshot(
        "philips_xc8057", "vatan", "Vatan Bilgisayar", "Philips 8000 Series Aqua Plus XC8057/01",
        "https://www.vatanbilgisayar.com/philips-xc8057",
        sale_price=17950.0, effective_pack_price=17950.0, effective_unit_price=17950.0,
        net_amount=1.0, captured_at=now,
        warranty_type="TR_OFFICIAL", zero_interest_months=1, model_code_seen="XC8057/01",
    )

    # ------------------------------------------------------------------
    # 4. ÇAPRAZ DENETİM MOTORUNU ÇALIŞTIR
    # ------------------------------------------------------------------
    engine = MarketDedektifiAuditEngine(db)
    targets = [
        ("fairy_plat_plus", "amazon", "SINGLE"),
        ("elseve_sampuan", "gratis", "SINGLE"),
        ("sutas_kasar", "carrefoursa", "SINGLE"),
        ("papia_tuvalet_kagidi", "trendyol", "MULTI_BULK"),
        ("ariel_toz_deterjan", "trendyol", "SINGLE"),
        ("philips_xc8057", "amazon", "SINGLE"),
    ]

    findings = []
    for pkey, plat, tier in targets:
        quotes = [
            q for q in db.get_latest_market_quotes(pkey, now=now)
            if q.platform == plat and q.pack_tier == tier
        ]
        if quotes:
            findings.append(engine.audit_quote(pkey, quotes[0], now=now))

    print("=" * 88)
    print("MARKET DEDEKTİFİ — 7 KATMANLI ÇAPRAZ DENETİM SONUÇ TABLOSU")
    print("=" * 88)
    for f in findings:
        print(f"[{f.verdict}] Puan: {f.detective_score}/100 | {f.product_name} ({f.platform_display})")
        print(f"   Birim Maliyet: {f.effective_unit_price:.2f} TL/{f.base_unit} | Bayraklar: {f.flags}")
        for n in f.evidence_notes:
            print(f"   -> {n}")
        if f.private_label_tip:
            print(f"   -> {f.private_label_tip}")
        print("-" * 88)

    columnist = MarketDedektifiColumnist()
    sample_md = columnist.render_sample_column(findings)
    sample_path = base_dir / "ornek_kose_yazisi.md"
    sample_path.write_text(sample_md, encoding="utf-8")
    print(f"\nÖrnek köşe yazısı üretildi: {sample_path}")


if __name__ == "__main__":
    run_simulation()
