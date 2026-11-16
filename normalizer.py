"""
Market Dedektifi — Birim Fiyat, Gramaj ve Kampanya Normalizasyon Motoru
Farklı marketlerde farklı paket boyutları ve kampanya kurgularıyla satılan ürünleri
tek bir ortak "Baz Birim" (1 kg, 1 litre, 1 yıkama/kapsül, 100 yaprak, 1 adet) maliyetine indirger.
"""

import re
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class CampaignRule:
    """Kampanya mekanizması tanımı."""
    campaign_type: str = "NONE"
    # Türler:
    # - "NONE": Düz etiket fiyatı
    # - "BUY_X_PAY_Y": Örn. "3 Al 2 Öde" (buy_qty=3, pay_qty=2)
    # - "SECOND_HALF_PRICE": "2.si %50 İndirimli" (2 alımda %25 efektif indirim)
    # - "MULTI_BUY_PCT": Örn. Amazon "4 Adet Al %10 Az Öde" (buy_qty=4, extra_discount_pct=10)
    # - "CART_COUPON_TL": Sepette anında TL kupon (coupon_tl=100, min_spend_tl=500)
    buy_qty: int = 1
    pay_qty: int = 1
    extra_discount_pct: float = 0.0
    coupon_tl: float = 0.0
    min_spend_tl: float = 0.0


@dataclass
class NormalizedOffer:
    effective_pack_price: float          # Kupon/çoklu alım sonrası 1 paketin efektif fiyatı (kargo hariç)
    effective_pack_price_with_ship: float # Tek siparişte kargo dahil 1 paketin efektif maliyeti
    required_buy_qty: int                # Bu birim fiyata ulaşmak için alınması gereken minimum paket sayısı
    out_of_pocket_total: float           # Kasada anlık çıkacak toplam nakit (ör. 3 al 2 öde için 2 paket parası)
    normalized_amount_per_pack: float    # 1 paketteki baz birim miktarı (örn. 0.85 kg, 75 yıkama, 48.0 yüz-yaprak)
    base_unit_label: str                 # "kg", "litre", "yıkama", "100 yaprak", "adet"
    effective_unit_price: float          # Kargo hariç net birim fiyat (TL / baz birim)
    effective_unit_price_with_ship: float # Kargo dahil net birim fiyat


class UnitNormalizer:
    """Türkçe ürün isimlerinden gramaj, yıkama, rulo/yaprak/kat ve çoklu paket çarpanını çözer."""

    @staticmethod
    def parse_title_quantity(title: str) -> Optional[Tuple[float, str]]:
        """
        Ürün başlığından miktar ve baz birimi otomatik çıkarır.
        Örnekler:
        - "Fairy Platinum Plus 75 Kapsül x 2 Paket" -> (150.0, "yıkama")
        - "Sütaş Tam Yağlı Taze Kaşar Peyniri 850 g" -> (0.85, "kg")
        - "Papia Tuvalet Kağıdı 32 Rulo 3 Katlı 150 Yaprak" -> (48.0, "100 yaprak (3 kat eşdeğeri)")
        """
        t = title.lower()

        # Çoklu paket çarpanı (örn: "2 x", "x 3 paket", "3'lü", "4 adet paket")
        pack_mult = 1
        m_mult = re.search(r"(\d+)\s*[xX]\s*\d+", t)
        if not m_mult:
            m_mult_after = re.search(r"[xX]\s*(\d+)\s*(?:paket|adet|kutu)?", t)
            if m_mult_after:
                pack_mult = int(m_mult_after.group(1))
            else:
                m_lu = re.search(r"(\d+)\s*['’]?\s*(?:lü|lu|li|lı)\s*(?:paket|avantaj|set)", t)
                if m_lu:
                    pack_mult = int(m_lu.group(1))

        # 1) Kağıt ürünleri: Rulo x Yaprak x Kat (En çok hile yapılan kategori!)
        m_roll = re.search(r"(\d+)\s*(?:rulo|롤|lu|lü)", t)
        m_sheet = re.search(r"(\d+)\s*yaprak", t)
        if m_roll and m_sheet and ("tuvalet" in t or "havlu" in t):
            rolls = int(m_roll.group(1))
            sheets = int(m_sheet.group(1))
            m_ply = re.search(r"(\d+)\s*kat", t)
            ply = int(m_ply.group(1)) if m_ply else 2
            # 3 katlı standardına göre normalize edilmiş "100 yaprak" birimi
            std_hundred_sheets = (rolls * sheets * (ply / 3.0) * pack_mult) / 100.0
            return round(std_hundred_sheets, 3), "100 std yaprak"

        # 2) Yıkama / Kapsül / Tablet
        m_wash = re.search(r"(\d+)\s*(?:yıkama|yikama|kapsül|kapsul|tablet)", t)
        if m_wash:
            val = float(m_wash.group(1)) * pack_mult
            return val, "yıkama"

        # 3) Bebek bezi / Islak mendil adet / yaprak
        if "bez" in t or "külot" in t or "mendil" in t:
            m_cnt = re.search(r"(\d+)\s*(?:adet|yaprak)", t)
            if m_cnt:
                val = float(m_cnt.group(1)) * pack_mult
                unit = "yaprak" if "mendil" in t else "adet"
                return val, unit

        # 4) Ağırlık: kg / gr / g
        m_kg_multi = re.search(r"(\d+)\s*[xX]\s*(\d+(?:[.,]\d+)?)\s*(kg|gr|g)\b", t)
        if m_kg_multi:
            c = int(m_kg_multi.group(1))
            amt = float(m_kg_multi.group(2).replace(",", "."))
            u = m_kg_multi.group(3)
            kg_val = c * (amt if u == "kg" else amt / 1000.0)
            return round(kg_val, 4), "kg"

        m_wt = re.search(r"(\d+(?:[.,]\d+)?)\s*(kg|gr|g)\b", t)
        if m_wt:
            amt = float(m_wt.group(1).replace(",", "."))
            u = m_wt.group(2)
            kg_val = (amt if u == "kg" else amt / 1000.0) * pack_mult
            return round(kg_val, 4), "kg"

        # 5) Hacim: lt / l / ml
        m_vol_multi = re.search(r"(\d+)\s*[xX]\s*(\d+(?:[.,]\d+)?)\s*(lt|litre|l|ml)\b", t)
        if m_vol_multi:
            c = int(m_vol_multi.group(1))
            amt = float(m_vol_multi.group(2).replace(",", "."))
            u = m_vol_multi.group(3)
            lt_val = c * (amt / 1000.0 if u == "ml" else amt)
            return round(lt_val, 4), "litre"

        m_vol = re.search(r"(\d+(?:[.,]\d+)?)\s*(lt|litre|l|ml)\b", t)
        if m_vol:
            amt = float(m_vol.group(1).replace(",", "."))
            u = m_vol.group(2)
            lt_val = (amt / 1000.0 if u == "ml" else amt) * pack_mult
            return round(lt_val, 4), "litre"

        return None

    @staticmethod
    def calculate_normalized_offer(
        sale_price: float,
        net_amount: float,
        base_unit: str,
        campaign: Optional[CampaignRule] = None,
        shipping_fee: float = 0.0,
        free_shipping_threshold: float = 0.0,
    ) -> NormalizedOffer:
        """Kampanya kurgusunu ve kargo barajını hesaba katarak gerçek birim maliyeti hesaplar."""
        rule = campaign or CampaignRule()
        req_qty = 1
        subtotal = sale_price

        if rule.campaign_type == "BUY_X_PAY_Y" and rule.buy_qty > 0:
            req_qty = rule.buy_qty
            subtotal = sale_price * rule.pay_qty
        elif rule.campaign_type == "SECOND_HALF_PRICE":
            req_qty = 2
            subtotal = sale_price * 1.5
        elif rule.campaign_type == "MULTI_BUY_PCT" and rule.buy_qty > 0:
            req_qty = rule.buy_qty
            subtotal = (sale_price * req_qty) * (1.0 - rule.extra_discount_pct / 100.0)
        elif rule.campaign_type == "CART_COUPON_TL":
            # Kuponun aktifleşmesi için gereken minimum adet
            if rule.min_spend_tl > sale_price:
                import math
                req_qty = math.ceil(rule.min_spend_tl / sale_price)
            subtotal = max(0.0, (sale_price * req_qty) - rule.coupon_tl)
        else:
            req_qty = 1
            subtotal = sale_price

        # Kargo barajı kontrolü
        actual_shipping = 0.0
        if shipping_fee > 0:
            if free_shipping_threshold <= 0 or subtotal < free_shipping_threshold:
                actual_shipping = shipping_fee

        eff_pack_price = subtotal / req_qty
        eff_pack_with_ship = (subtotal + actual_shipping) / req_qty
        eff_unit_price = eff_pack_price / net_amount
        eff_unit_price_ship = eff_pack_with_ship / net_amount

        return NormalizedOffer(
            effective_pack_price=round(eff_pack_price, 2),
            effective_pack_price_with_ship=round(eff_pack_with_ship, 2),
            required_buy_qty=req_qty,
            out_of_pocket_total=round(subtotal + actual_shipping, 2),
            normalized_amount_per_pack=net_amount,
            base_unit_label=base_unit,
            effective_unit_price=round(eff_unit_price, 4),
            effective_unit_price_with_ship=round(eff_unit_price_ship, 4),
        )
