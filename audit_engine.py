"""
Market Dedektifi — 7 Katmanlı Çapraz Denetim ve Algoritmik Doğrulama Motoru
Bir kampanyanın gerçekten ucuz olup olmadığını 7 bağımsız filtreyle denetler.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from config import AUDIT_THRESHOLDS, TRUSTED_PLATFORMS
from memory_db import LiveMarketQuote, PriceMemoryDB


@dataclass
class AuditFinding:
    product_key: str
    product_name: str
    category: str
    platform: str
    platform_display: str
    seller_name: str
    url: str
    verdict: str
    # Karar kodları:
    # - "STOKLA_GERCEK_DIP_FIYAT"
    # - "AVANTAJLI_FIRSAT"
    # - "SAHTE_INDIRIM_TUZAGI"
    # - "GRAMAJ_OYUNU_SHRINKFLATION"
    # - "COKLU_ALIM_TUZAGI"
    # - "SATICI_VE_SAHTE_URUN_RISKI"
    # - "TAKSIT_AVANTAJLI_TEKNOLOJI"
    detective_score: int                        # 0 - 100 arası güven ve avantaj puanı
    effective_pack_price: float
    effective_unit_price: float
    base_unit: str
    claimed_discount_pct: Optional[float]       # Satıcının iddia ettiği sözde indirim %
    real_discount_vs_30d_pct: Optional[float]   # Bizim 30g hafızamıza göre gerçek indirim %
    edge_vs_next_rival_pct: Optional[float]     # En yakın güvenilir rakibe göre ucuzluk %
    next_rival_summary: str
    flags: List[str] = field(default_factory=list)
    evidence_notes: List[str] = field(default_factory=list)
    private_label_tip: Optional[str] = None
    real_npv_price: Optional[float] = None      # Taksitli teknoloji ürünlerinde enflasyon arındırılmış reel maliyet


class MarketDedektifiAuditEngine:
    def __init__(self, db: PriceMemoryDB):
        self.db = db

    @staticmethod
    def calculate_installment_npv(price: float, zero_interest_months: int) -> float:
        """
        Peşin fiyatına N taksit yapılan bir teknoloji/beyaz eşya ürününün
        aylık fırsat maliyeti (mevduat/enflasyon) düşülmüş 'Bugünkü Reel Peşin Eşdeğerini' hesaplar.
        """
        if zero_interest_months <= 1:
            return round(price, 2)
        r = AUDIT_THRESHOLDS.monthly_opportunity_rate
        monthly_payment = price / zero_interest_months
        # İlk taksit ekstresi ~1 ay sonra başlar varsayımıyla bugünkü değer toplamı
        npv = sum(monthly_payment / ((1.0 + r) ** m) for m in range(1, zero_interest_months + 1))
        return round(npv, 2)

    def audit_quote(
        self,
        product_key: str,
        target_quote: LiveMarketQuote,
        now: Optional[datetime] = None,
    ) -> AuditFinding:
        ref_now = now or datetime.now()
        prod_row = self.db.conn.execute(
            "SELECT * FROM canonical_products WHERE product_key = ?", (product_key,)
        ).fetchone()

        product_name = f"{prod_row['brand']} {prod_row['family_name']}" if prod_row else target_quote.title
        category = prod_row["category"] if prod_row else "genel"
        equiv_group = prod_row["equivalent_group"] if prod_row else None
        official_model = prod_row["official_model_code"] if prod_row else ""

        # Geçmiş istatistikler (hem genel piyasa hafızası hem mağaza bazlı hafıza)
        global_hist = self.db.get_historical_stats(product_key, platform=None, now=ref_now)
        all_quotes = self.db.get_latest_market_quotes(product_key, now=ref_now)

        flags: List[str] = []
        notes: List[str] = []
        score = 50

        # Satıcının iddia ettiği sözde indirim oranı
        claimed_disc = None
        if target_quote.list_price and target_quote.list_price > target_quote.sale_price:
            claimed_disc = round(
                ((target_quote.list_price - target_quote.effective_pack_price) / target_quote.list_price) * 100.0, 1
            )

        # ------------------------------------------------------------------
        # DENETİM 1: Kendi Fiyat Hafızamız & "Önce Bindir Sonra İndir" Kontrolü
        # ------------------------------------------------------------------
        real_disc_30d = None
        if global_hist.median_30d_unit_price:
            med30 = global_hist.median_30d_unit_price
            real_disc_30d = round(((med30 - target_quote.effective_unit_price) / med30) * 100.0, 1)

            if target_quote.list_price:
                claimed_list_unit = target_quote.list_price / target_quote.net_amount
                if claimed_list_unit >= med30 * AUDIT_THRESHOLDS.anchor_inflation_ratio and real_disc_30d < 5.0:
                    flags.append("FAKE_DISCOUNT_ANCHOR")
                    notes.append(
                        f"Üstü çizili fiyat şişirilmiş! Satıcı %{claimed_disc} indirim iddia ediyor ama "
                        f"son 30 günlük gerçek piyasa medyanı zaten {med30:.2f} TL/{target_quote.base_unit} "
                        f"(gerçek fark sadece %{real_disc_30d})."
                    )
                    score -= 35

            is_trusted_seller = target_quote.seller_type in ("OFFICIAL", "AUTHORIZED")
            if is_trusted_seller and global_hist.min_90d_unit_price and target_quote.effective_unit_price <= global_hist.min_90d_unit_price * 1.01:
                flags.append("90_DAY_HISTORICAL_LOW")
                notes.append(
                    f"Son 90 günün en düşük birim fiyatı yakalandı! "
                    f"(30g medyan: {med30:.2f} TL/{target_quote.base_unit} -> Şimdi: {target_quote.effective_unit_price:.2f} TL/{target_quote.base_unit}, gerçek indirim: %{real_disc_30d})"
                )
                score += 25
            elif is_trusted_seller and real_disc_30d >= AUDIT_THRESHOLDS.stock_up_discount_pct:
                score += 20
                notes.append(f"30 günlük fiyat hafızamıza göre net %{real_disc_30d} gerçek birim fiyat düşüşü var.")
            elif is_trusted_seller and real_disc_30d >= AUDIT_THRESHOLDS.min_genuine_discount_pct:
                score += 10

        # ------------------------------------------------------------------
        # DENETİM 2: Çapraz Market Kıyaslaması (Cross-Market Consensus)
        # ------------------------------------------------------------------
        trusted_rivals = [
            q for q in all_quotes
            if q.platform != target_quote.platform
            and q.seller_type in ("OFFICIAL", "AUTHORIZED")
            and q.warranty_type == "TR_OFFICIAL"
        ]
        edge_vs_rival = None
        rival_summary = "Karşılaştırılacak güncel rakip kaydı yok"

        if trusted_rivals:
            best_rival = min(trusted_rivals, key=lambda x: x.effective_unit_price)
            rival_name = TRUSTED_PLATFORMS.get(best_rival.platform, best_rival.platform)
            edge_vs_rival = round(
                ((best_rival.effective_unit_price - target_quote.effective_unit_price) / best_rival.effective_unit_price) * 100.0,
                1,
            )
            rival_summary = (
                f"En yakın güvenilir rakip {rival_name}: "
                f"{best_rival.effective_pack_price:.2f} TL ({best_rival.effective_unit_price:.2f} TL/{best_rival.base_unit})"
            )

            if edge_vs_rival <= -AUDIT_THRESHOLDS.overpriced_vs_rival_pct:
                flags.append("CROSS_MARKET_OVERPRICED")
                notes.append(
                    f"Sözde indirimli hali bile {rival_name} fiyatından %{abs(edge_vs_rival):.1f} daha pahalı! "
                    f"({rival_name}'da birim fiyat {best_rival.effective_unit_price:.2f} TL/{best_rival.base_unit})."
                )
                score -= 30
            elif edge_vs_rival >= AUDIT_THRESHOLDS.cross_market_edge_pct and target_quote.seller_type in ("OFFICIAL", "AUTHORIZED"):
                flags.append("CROSS_MARKET_LEADER")
                notes.append(
                    f"En yakın rakibi {rival_name}'dan bile birim bazda net %{edge_vs_rival:.1f} daha ucuz."
                )
                score += 18

        # ------------------------------------------------------------------
        # DENETİM 3: Gizli Gramaj / Yaprak Küçültme (Shrinkflation) Dedektörü
        # ------------------------------------------------------------------
        if (
            target_quote.pack_tier == "SINGLE"
            and global_hist.baseline_net_amount
            and target_quote.net_amount < global_hist.baseline_net_amount * (1.0 - AUDIT_THRESHOLDS.shrinkflation_tolerance_pct / 100.0)
        ):
            shrink_pct = round(
                ((global_hist.baseline_net_amount - target_quote.net_amount) / global_hist.baseline_net_amount) * 100.0, 1
            )
            flags.append("SHRINKFLATION_DETECTED")
            notes.append(
                f"Gizli gramaj küçültme (Shrinkflation) yakalandı! Paket boyutu "
                f"{global_hist.baseline_net_amount:g} {target_quote.base_unit} seviyesinden "
                f"{target_quote.net_amount:g} {target_quote.base_unit} seviyesine (%{shrink_pct} küçülme) düşürülmüş."
            )
            score -= 25

        # ------------------------------------------------------------------
        # DENETİM 4: Çoklu Alım / Ekonomik Boy Tuzağı (Bulk Trap) Kontrolü
        # ------------------------------------------------------------------
        if target_quote.pack_tier == "MULTI_BULK":
            single_quotes = [
                q for q in all_quotes
                if q.pack_tier == "SINGLE" and q.seller_type in ("OFFICIAL", "AUTHORIZED")
            ]
            if single_quotes:
                best_single = min(single_quotes, key=lambda x: x.effective_unit_price)
                if target_quote.effective_unit_price > best_single.effective_unit_price * 1.02:
                    bulk_overpay_pct = round(
                        ((target_quote.effective_unit_price - best_single.effective_unit_price) / best_single.effective_unit_price) * 100.0,
                        1,
                    )
                    flags.append("BULK_TRAP")
                    best_s_plat = TRUSTED_PLATFORMS.get(best_single.platform, best_single.platform)
                    notes.append(
                        f"Çoklu paket tuzağı! 'Avantajlı büyük paket' diye satılıyor ama birim fiyatı "
                        f"({target_quote.effective_unit_price:.2f} TL/{target_quote.base_unit}), "
                        f"{best_s_plat}'daki tekli paketten (%{bulk_overpay_pct}) daha pahalıya geliyor!"
                    )
                    score -= 30

        # ------------------------------------------------------------------
        # DENETİM 5: Satıcı Güvenilirliği, Sahte Ürün & Yakın SKT Filtresi
        # ------------------------------------------------------------------
        if target_quote.seller_type == "THIRD_PARTY":
            ref_med = global_hist.median_30d_unit_price
            is_too_cheap = (
                ref_med is not None
                and target_quote.effective_unit_price < ref_med * (1.0 - AUDIT_THRESHOLDS.suspicious_cheap_anomaly_pct / 100.0)
            )
            if target_quote.seller_rating < AUDIT_THRESHOLDS.min_third_party_rating or is_too_cheap:
                flags.append("SELLER_AUTHENTICITY_OR_SKT_RISK")
                notes.append(
                    f"Dikkat: Fiyat çok düşük görünüyor ancak satıcı yetkili/resmi mağaza değil "
                    f"(Satıcı: {target_quote.seller_name}, Puan: {target_quote.seller_rating}/10). "
                    f"Bu kategoride aşırı ucuz 3. parti ilanlarda sahte ürün veya yakın SKT riski yüksektir!"
                )
                score -= 45

        # ------------------------------------------------------------------
        # DENETİM 6: Market Markalı (Private Label / Muadil) Çapraz Kontrolü
        # ------------------------------------------------------------------
        pl_tip = None
        if equiv_group and prod_row and not prod_row["is_private_label"]:
            pl = self.db.find_private_label_alternative(equiv_group, now=ref_now)
            if pl and pl["effective_unit_price"] < target_quote.effective_unit_price * 0.68:
                save_pct = round(
                    ((target_quote.effective_unit_price - pl["effective_unit_price"]) / target_quote.effective_unit_price) * 100.0,
                    0,
                )
                pl_plat = TRUSTED_PLATFORMS.get(pl["platform"], pl["platform"])
                pl_tip = (
                    f"Muadil Notu: Marka takıntınız yoksa {pl_plat}'daki '{pl['title']}' "
                    f"birim fiyatta ({pl['effective_unit_price']:.2f} TL/{pl['base_unit']}) "
                    f"bu kampanyalı fiyattan bile %{int(save_pct)} daha ekonomik."
                )

        # ------------------------------------------------------------------
        # DENETİM 7: Teknoloji & Beyaz Eşya Özel Denetimi (Taksit NPV, Garanti, Model)
        # ------------------------------------------------------------------
        real_npv = None
        if category == "teknoloji_beyaz_esya":
            real_npv = self.calculate_installment_npv(
                target_quote.effective_pack_price, target_quote.zero_interest_months
            )
            if target_quote.warranty_type != "TR_OFFICIAL":
                flags.append("WARRANTY_WARNING")
                notes.append(
                    f"Garanti Uyarısı: Bu ilan Resmi Türkiye Garantili değil ({target_quote.warranty_type})!"
                )
                score -= 30

            # Orijinal model kodu doğrulaması (satırda kırpılmış model kodu varsa)
            snap_row = self.db.conn.execute(
                """
                SELECT model_code_seen FROM price_snapshots
                WHERE product_key = ? AND platform = ? AND seller_name = ?
                ORDER BY captured_at DESC LIMIT 1
                """,
                (product_key, target_quote.platform, target_quote.seller_name),
            ).fetchone()
            seen_code = snap_row["model_code_seen"] if snap_row else ""
            if official_model and seen_code and seen_code != official_model:
                flags.append("DOWNGRADED_MODEL_CODE")
                notes.append(
                    f"Model Kodu Uyarısı: İlandaki kod ({seen_code}), ana serinin orijinal koduyla ({official_model}) aynı değil (kırpılmış alt varyant olabilir)."
                )
                score -= 20

            # Rakiplerle Taksit NPV karşılaştırması
            if trusted_rivals and target_quote.zero_interest_months >= 6:
                best_cash_rival = min(trusted_rivals, key=lambda x: x.effective_pack_price)
                rival_npv = self.calculate_installment_npv(
                    best_cash_rival.effective_pack_price, best_cash_rival.zero_interest_months
                )
                if real_npv < rival_npv:
                    r_name = TRUSTED_PLATFORMS.get(best_cash_rival.platform, best_cash_rival.platform)
                    npv_gain = round(rival_npv - real_npv, 0)
                    flags.append("INSTALLMENT_NPV_WINNER")
                    notes.append(
                        f"Peşin fiyatına {target_quote.zero_interest_months} taksit avantajıyla "
                        f"enflasyon/mevduat etkisi düşüldüğünde reel maliyeti {real_npv:,.0f} TL'ye geliyor! "
                        f"{r_name}'daki tek çekim {best_cash_rival.effective_pack_price:,.0f} TL fiyattan gerçekte ~{npv_gain:,.0f} TL daha kârlı."
                    )
                    score += 22

        # Puan sınırlandırma ve nihai karar (Verdict) ataması
        score = max(0, min(100, score))

        if "SELLER_AUTHENTICITY_OR_SKT_RISK" in flags:
            verdict = "SATICI_VE_SAHTE_URUN_RISKI"
        elif "BULK_TRAP" in flags:
            verdict = "COKLU_ALIM_TUZAGI"
        elif "SHRINKFLATION_DETECTED" in flags and (real_disc_30d is None or real_disc_30d < 10):
            verdict = "GRAMAJ_OYUNU_SHRINKFLATION"
        elif "FAKE_DISCOUNT_ANCHOR" in flags or "CROSS_MARKET_OVERPRICED" in flags:
            verdict = "SAHTE_INDIRIM_TUZAGI"
        elif category == "teknoloji_beyaz_esya" and "INSTALLMENT_NPV_WINNER" in flags and score >= 70:
            verdict = "TAKSIT_AVANTAJLI_TEKNOLOJI"
        elif score >= 80:
            verdict = "STOKLA_GERCEK_DIP_FIYAT"
        elif score >= 65:
            verdict = "AVANTAJLI_FIRSAT"
        else:
            verdict = "SIRADAN_FIYAT"

        return AuditFinding(
            product_key=product_key,
            product_name=product_name,
            category=category,
            platform=target_quote.platform,
            platform_display=TRUSTED_PLATFORMS.get(target_quote.platform, target_quote.platform),
            seller_name=target_quote.seller_name,
            url=target_quote.url,
            verdict=verdict,
            detective_score=score,
            effective_pack_price=target_quote.effective_pack_price,
            effective_unit_price=target_quote.effective_unit_price,
            base_unit=target_quote.base_unit,
            claimed_discount_pct=claimed_disc,
            real_discount_vs_30d_pct=real_disc_30d,
            edge_vs_next_rival_pct=edge_vs_rival,
            next_rival_summary=rival_summary,
            flags=flags,
            evidence_notes=notes,
            private_label_tip=pl_tip,
            real_npv_price=real_npv,
        )
