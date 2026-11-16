"""
Market Dedektifi & dipci.tr — Mobil Kamera Okuyucu Motoru (Barkod + Yapay Zeka Görsel & Raf Etiketi)
Kullanıcı dışarıda (markette, AVM'de, teknoloji mağazasında) bir ürünün barkodunu veya
doğrudan ambalaj/raf etiketini telefon kamerasına tuttuğunda:
  1. GS1 barkod doğrulaması veya Çok Modlu Görsel Tanıma (AI Vision) ile ürünü bulur,
  2. Fiziksel raftaki fiyatı (varsa) eldeki gramaj üzerinden birim fiyata çevirir,
  3. Online marketlerdeki FARKLI SATIŞ SEÇENEKLERİNİ (farklı paket boyları, çoklu koliler,
     30 dk hızlı teslimat, ertesi gün kargo, mağazadan gel-al, taksitli reel fiyat ve muadil)
     çıkarıp "Raftan al" veya "Bırak, online söyle" kararını üretir.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from affiliate import AffiliateLinkBuilder
from audit_engine import MarketDedektifiAuditEngine
from config import AFFILIATE_CONFIG, AUDIT_THRESHOLDS, AffiliateConfig
from memory_db import LiveMarketQuote, PriceMemoryDB


VISION_SCANNER_SYSTEM_PROMPT = """
Sen dipci.tr ve Market Dedektifi'nin 'Mobil Reyon Gözü' görüntü çözümleyicisisin.
Kullanıcının telefon kamerasından gelen karede hem ÜRÜN AMBALAJINI / TEŞHİR KARTINI
hem de varsa altındaki RAF FİYAT ETİKETİNİ aynı anda oku ve SADECE şu JSON şemasını dön:
{
  "gtin_visible": "Görünüyorsa 8/12/13 haneli barkod, yoksa boş dizi",
  "brand": "Marka adı (örn: Fairy, Sütaş, Philips)",
  "product_name": "Tam ürün ve seri adı (örn: Platinum Plus Hepsi Bir Arada)",
  "net_amount_text": "Ambalaj üzerindeki gramaj/adet/hacim (örn: '50 Kapsül', '850 g')",
  "net_amount_value": 50.0,
  "model_code": "Elektronik/beyaz eşya ise tam model kodu (örn: XC8057/01)",
  "shelf_price_tl": 439.90,
  "shelf_store_hint": "Raf etiketinin rengi/logosundan anlaşılıyorsa mağaza adı, yoksa ''",
  "confidence": 0.96
}
Kurallar:
- Raf etiketinde 'Birim Fiyat: ... TL/kg' yazıyorsa bile `shelf_price_tl` alanına kasa satış fiyatını yaz.
- Ambalaj üzerindeki yeni küçültülmüş gramajları (örn: 1000g yerine 850g) dikkatle oku.
""".strip()


@dataclass
class VisualObservation:
    """Kameradan çekilen ürün/reyon fotoğrafından AI Vision'ın çıkardığı yapısal veri."""
    brand: str
    product_name: str
    net_amount_value: Optional[float] = None
    model_code: str = ""
    gtin_visible: str = ""
    shelf_price_tl: Optional[float] = None
    shelf_store_hint: str = ""
    confidence: float = 0.95


@dataclass
class PackSizeOption:
    """Aynı ürünün farklı gramaj/adet (tekli, ikili, dev ekonomi paketi) seçeneği."""
    net_amount: float
    base_unit: str
    best_platform: str
    best_seller: str
    sale_price: float
    effective_unit_price: float
    npv_real_price: Optional[float]
    zero_interest_months: int
    delivery_mode: str         # "FAST_30_45_MIN", "FREE_NEXT_DAY", "STORE_PICKUP", "STANDARD_CARGO"
    affiliate_url: str
    badge: str                 # "EN_DIP_BIRIM_FIYAT", "DEV_PAKET_TUZAGI", "AYNI_BOYUT", "TAKSIT_AVANTAJI"


@dataclass
class ScanComparisonResult:
    scan_mode: str                         # "BARCODE" veya "AI_VISION"
    gtin: Optional[str]
    gtin_origin: str
    product_key: str
    brand: str
    family_name: str
    category: str
    base_unit: str
    scanned_net_amount: float
    shrinkflation_warning: Optional[str]
    shelf_price_tl: Optional[float]
    shelf_unit_price: Optional[float]
    verdict_code: str                      # "BUY_ONLINE_SAVE", "BUY_ON_SHELF_NOW", "COMPARE_OPTIONS"
    verdict_headline: str
    savings_tl: float
    savings_pct: float
    same_size_quotes: List[Dict] = field(default_factory=list)
    pack_size_options: List[PackSizeOption] = field(default_factory=list)
    fulfillment_highlights: Dict[str, Dict] = field(default_factory=dict)
    private_label_alternative: Optional[Dict] = None


def validate_gtin(raw_code: str) -> Tuple[bool, str, str]:
    """
    GS1 Modulo-10 barkod kontrol basamağı doğrulaması (EAN-8, UPC-A, EAN-13, GTIN-14)
    ve ülke/bölge ön eki çözümü.
    """
    digits = "".join(ch for ch in raw_code if ch.isdigit())
    if len(digits) not in (8, 12, 13, 14):
        return False, digits, "Geçersiz barkod uzunluğu"

    body, check_digit = digits[:-1], int(digits[-1])
    total = 0
    for idx, ch in enumerate(reversed(body)):
        weight = 3 if idx % 2 == 0 else 1
        total += int(ch) * weight
    expected_check = (10 - (total % 10)) % 10
    if expected_check != check_digit:
        return False, digits, "Hatalı GS1 kontrol basamağı"

    prefix3 = int(digits[:3]) if len(digits) >= 13 else 0
    if prefix3 in (868, 869):
        origin = "Türkiye (GS1 TOBB 868/869)"
    elif 400 <= prefix3 <= 440:
        origin = "Almanya (GS1 400-440)"
    elif 300 <= prefix3 <= 379:
        origin = "Fransa (GS1 300-379)"
    elif 800 <= prefix3 <= 839:
        origin = "İtalya (GS1 800-839)"
    else:
        origin = "Uluslararası GS1 Kayıtlı Barkod"

    return True, digits, origin


def compute_ean13_with_check(twelve_digits: str) -> str:
    """Test ve kayıt kolaylığı için 12 haneden geçerli 13 haneli EAN-13 üretir."""
    d = "".join(ch for ch in twelve_digits if ch.isdigit())[:12]
    total = sum(int(ch) * (3 if idx % 2 == 0 else 1) for idx, ch in enumerate(reversed(d)))
    check = (10 - (total % 10)) % 10
    return f"{d}{check}"


class CameraScannerEngine:
    """Barkod veya Kamera Görselinden anlık çoklu mağaza ve farklı paket/satış seçeneği motoru."""

    FAST_GROCERY_PLATFORMS = {"migros", "getir", "carrefoursa", "iste_gelsin", "yemeksepeti_market"}
    PICKUP_TECH_PLATFORMS = {"mediamarkt", "vatan", "teknosa"}

    def __init__(
        self,
        db: PriceMemoryDB,
        audit_engine: Optional[MarketDedektifiAuditEngine] = None,
        affiliate_builder: Optional[AffiliateLinkBuilder] = None,
    ):
        self.db = db
        self.audit = audit_engine or MarketDedektifiAuditEngine(db)
        self.affiliate = affiliate_builder or AffiliateLinkBuilder(AFFILIATE_CONFIG)

    def _classify_delivery_mode(self, platform: str, effective_price: float) -> str:
        p = platform.lower()
        if p in self.FAST_GROCERY_PLATFORMS:
            return "FAST_30_45_MIN"
        if p in self.PICKUP_TECH_PLATFORMS:
            return "STORE_PICKUP"
        if p == "amazon" or effective_price >= 300.0:
            return "FREE_NEXT_DAY"
        return "STANDARD_CARGO"

    def scan_barcode(
        self,
        gtin: str,
        shelf_price_tl: Optional[float] = None,
        now: Optional[datetime] = None,
    ) -> Optional[ScanComparisonResult]:
        is_valid, clean_gtin, origin = validate_gtin(gtin)
        matched = self.db.lookup_by_barcode(clean_gtin)
        if not matched:
            return None

        return self._build_comparison(
            scan_mode="BARCODE",
            gtin=clean_gtin,
            gtin_origin=origin if is_valid else f"Doğrulanmamış ({origin})",
            prod_meta=matched,
            scanned_net_amount=float(matched["scanned_net_amount"]),
            shelf_price_tl=shelf_price_tl,
            now=now,
        )

    def scan_visual(
        self,
        obs: VisualObservation,
        now: Optional[datetime] = None,
    ) -> Optional[ScanComparisonResult]:
        # Fotoğrafta barkod da net okunduysa önce kesin barkod eşleşmesini dene
        if obs.gtin_visible:
            by_code = self.scan_barcode(obs.gtin_visible, shelf_price_tl=obs.shelf_price_tl, now=now)
            if by_code:
                by_code.scan_mode = "AI_VISION_PLUS_BARCODE"
                return by_code

        prod_meta = self.db.match_by_visual_clues(
            brand=obs.brand,
            product_text=obs.product_name,
            model_code=obs.model_code,
        )
        if not prod_meta:
            return None

        scanned_amt = float(obs.net_amount_value or prod_meta.get("reference_net_amount") or 1.0)
        return self._build_comparison(
            scan_mode="AI_VISION",
            gtin=None,
            gtin_origin="Kamera Görsel & Raf Etiketi Tanıma",
            prod_meta=prod_meta,
            scanned_net_amount=scanned_amt,
            shelf_price_tl=obs.shelf_price_tl,
            now=now,
        )

    def _build_comparison(
        self,
        scan_mode: str,
        gtin: Optional[str],
        gtin_origin: str,
        prod_meta: Dict,
        scanned_net_amount: float,
        shelf_price_tl: Optional[float],
        now: Optional[datetime],
    ) -> ScanComparisonResult:
        product_key = prod_meta["product_key"]
        base_unit = prod_meta["base_unit"]
        category = prod_meta["category"]
        ref_amount = float(prod_meta.get("reference_net_amount") or scanned_net_amount)

        # 1. Shrinkflation kontrolü (Eldeki ambalaj tarihsel standarttan küçülmüş mü?)
        shrink_warning = None
        if ref_amount > 0 and scanned_net_amount < ref_amount * 0.97:
            drop_pct = round((1.0 - (scanned_net_amount / ref_amount)) * 100, 1)
            shrink_warning = (
                f"Dikkat! Elinizdeki ambalaj {ref_amount:g} {base_unit} yerine "
                f"{scanned_net_amount:g} {base_unit}'a düşürülmüş (-%{drop_pct} gizli küçülme)."
            )

        # 2. Tüm canlı online teklifleri çek (Güvenilir satıcıları öne al)
        all_quotes: List[LiveMarketQuote] = self.db.get_latest_market_quotes(product_key, now=now)
        trusted_quotes = [
            q for q in all_quotes
            if q.seller_type in ("OFFICIAL", "AUTHORIZED") and q.seller_rating >= 8.8
        ]

        # 3. Farklı paket boyutlarına (net_amount) göre en iyi seçenekleri grupla
        by_size: Dict[float, List[LiveMarketQuote]] = {}
        for q in trusted_quotes:
            by_size.setdefault(round(q.net_amount, 3), []).append(q)

        # En düşük birim fiyatı bul (Dev paket tuzağını yakalamak için)
        global_min_unit = min((q.effective_unit_price for q in trusted_quotes), default=0.0)
        single_min_unit = min(
            (q.effective_unit_price for q in trusted_quotes if q.pack_tier == "SINGLE"),
            default=global_min_unit,
        )

        pack_options: List[PackSizeOption] = []
        for size_val in sorted(by_size.keys()):
            size_quotes = sorted(by_size[size_val], key=lambda x: x.effective_unit_price)
            best_q = size_quotes[0]
            npv_price = None
            if category == "teknoloji_beyaz_esya" and best_q.zero_interest_months >= 2:
                npv_price = self.audit.calculate_installment_npv(
                    best_q.effective_pack_price, best_q.zero_interest_months
                )

            if abs(best_q.effective_unit_price - global_min_unit) < 1e-4:
                badge = "EN_DIP_BIRIM_FIYAT"
            elif best_q.pack_tier == "MULTI_BULK" and best_q.effective_unit_price > single_min_unit * 1.02:
                badge = "DEV_PAKET_TUZAGI"
            elif npv_price and npv_price < best_q.effective_pack_price * 0.92:
                badge = "TAKSIT_AVANTAJI"
            elif abs(size_val - scanned_net_amount) < 0.05:
                badge = "AYNI_BOYUT"
            else:
                badge = "ALTERNATIF_PAKET"

            pack_options.append(
                PackSizeOption(
                    net_amount=size_val,
                    base_unit=base_unit,
                    best_platform=best_q.platform,
                    best_seller=best_q.seller_name,
                    sale_price=best_q.effective_pack_price,
                    effective_unit_price=best_q.effective_unit_price,
                    npv_real_price=npv_price,
                    zero_interest_months=best_q.zero_interest_months,
                    delivery_mode=self._classify_delivery_mode(best_q.platform, best_q.effective_pack_price),
                    affiliate_url=self.affiliate.build_tracking_url(best_q.url, sub_id="dipci_kamera"),
                    badge=badge,
                )
            )

        # Birim fiyata göre sırala (kullanıcı hangi paketin en kârlı olduğunu anında görsün)
        pack_options.sort(key=lambda p: p.npv_real_price if p.npv_real_price else p.effective_unit_price)

        # 4. Eldeki boyutun birebir mağaza listesi
        same_size_quotes = [
            {
                "platform": q.platform,
                "seller_name": q.seller_name,
                "seller_type": q.seller_type,
                "seller_rating": q.seller_rating,
                "sale_price": q.effective_pack_price,
                "unit_price": q.effective_unit_price,
                "zero_interest_months": q.zero_interest_months,
                "delivery_mode": self._classify_delivery_mode(q.platform, q.effective_pack_price),
                "url": self.affiliate.build_tracking_url(q.url, sub_id="dipci_kamera"),
            }
            for q in all_quotes
            if abs(q.net_amount - scanned_net_amount) < max(0.05, scanned_net_amount * 0.05)
        ]

        # 5. Teslimat & Ödeme Modu Öne Çıkanlar (Hızlı Market / Ertesi Gün / Mağazadan Gel-Al / Taksit)
        highlights: Dict[str, Dict] = {}
        for mode_key in ("FAST_30_45_MIN", "FREE_NEXT_DAY", "STORE_PICKUP"):
            candidates = [
                q for q in trusted_quotes
                if self._classify_delivery_mode(q.platform, q.effective_pack_price) == mode_key
            ]
            if candidates:
                win = min(candidates, key=lambda x: x.effective_unit_price)
                highlights[mode_key] = {
                    "platform": win.platform,
                    "title": win.title,
                    "price": win.effective_pack_price,
                    "unit_price": win.effective_unit_price,
                    "net_amount": win.net_amount,
                    "url": self.affiliate.build_tracking_url(win.url, sub_id="dipci_kamera"),
                }

        installment_cands = [q for q in trusted_quotes if q.zero_interest_months >= 3]
        if installment_cands:
            best_inst = min(
                installment_cands,
                key=lambda x: self.audit.calculate_installment_npv(x.effective_pack_price, x.zero_interest_months),
            )
            highlights["INSTALLMENT_CHAMPION"] = {
                "platform": best_inst.platform,
                "months": best_inst.zero_interest_months,
                "sticker_price": best_inst.effective_pack_price,
                "npv_real_price": self.audit.calculate_installment_npv(
                    best_inst.effective_pack_price, best_inst.zero_interest_months
                ),
                "url": self.affiliate.build_tracking_url(best_inst.url, sub_id="dipci_kamera"),
            }

        # 6. Market markalı muadil
        muadil = None
        if prod_meta.get("equivalent_group"):
            muadil = self.db.find_private_label_alternative(prod_meta["equivalent_group"], now=now)

        # 7. Reyon Etiketi vs Online Karar Motoru
        shelf_unit = round(shelf_price_tl / scanned_net_amount, 2) if (shelf_price_tl and scanned_net_amount > 0) else None
        verdict_code = "COMPARE_OPTIONS"
        verdict_headline = "Tüm online mağaza ve paket boyu seçenekleri birim fiyata göre sıralandı."
        savings_tl = 0.0
        savings_pct = 0.0

        if shelf_price_tl and shelf_unit and pack_options:
            best_opt = pack_options[0]
            # Teknoloji ise taksitli reel maliyet üzerinden, market ise birim fiyat üzerinden kıyasla
            if category == "teknoloji_beyaz_esya" and best_opt.npv_real_price:
                effective_online = best_opt.npv_real_price
                diff_tl = round(shelf_price_tl - effective_online, 2)
                diff_pct = round((diff_tl / shelf_price_tl) * 100, 1)
                if diff_pct >= 3.0:
                    verdict_code = "BUY_ONLINE_SAVE"
                    savings_tl = diff_tl
                    savings_pct = diff_pct
                    verdict_headline = (
                        f"Mağaza kasasında tek çekim ödemeyin! {best_opt.best_platform.upper()} üzerinde "
                        f"peşin fiyatına {best_opt.zero_interest_months} taksitle reel maliyet "
                        f"{diff_tl:,.0f} TL (-%{diff_pct}) daha avantajlı."
                    )
                else:
                    verdict_code = "BUY_ON_SHELF_NOW"
                    verdict_headline = "Mağazadaki etiket fiyatı online taksitli seçeneklerle başa baş; beklemeyip alabilirsiniz."
            else:
                diff_unit_pct = round(((shelf_unit - best_opt.effective_unit_price) / shelf_unit) * 100, 1)
                # Kullanıcının elindeki miktara eşdeğer veya önerilen paketteki toplam tasarruf
                equiv_saving = round((shelf_unit - best_opt.effective_unit_price) * best_opt.net_amount, 2)
                if diff_unit_pct >= 5.0:
                    verdict_code = "BUY_ONLINE_SAVE"
                    savings_tl = equiv_saving
                    savings_pct = diff_unit_pct
                    verdict_headline = (
                        f"Rafta bırakın! Elinizdeki ürün rafta {shelf_unit:.2f} TL/{base_unit}'a geliyor. "
                        f"{best_opt.best_platform.upper()} {best_opt.net_amount:g} {base_unit} seçeneğinde "
                        f"{best_opt.effective_unit_price:.2f} TL/{base_unit} (-%{diff_unit_pct} daha ucuz)!"
                    )
                else:
                    verdict_code = "BUY_ON_SHELF_NOW"
                    verdict_headline = (
                        f"Raftaki fiyat ({shelf_unit:.2f} TL/{base_unit}) gayet iyi! "
                        f"Online'daki en dip fiyattan farkı yok, kargo beklemeden sepete atabilirsiniz."
                    )

        return ScanComparisonResult(
            scan_mode=scan_mode,
            gtin=gtin,
            gtin_origin=gtin_origin,
            product_key=product_key,
            brand=prod_meta["brand"],
            family_name=prod_meta["family_name"],
            category=category,
            base_unit=base_unit,
            scanned_net_amount=scanned_net_amount,
            shrinkflation_warning=shrink_warning,
            shelf_price_tl=shelf_price_tl,
            shelf_unit_price=shelf_unit,
            verdict_code=verdict_code,
            verdict_headline=verdict_headline,
            savings_tl=savings_tl,
            savings_pct=savings_pct,
            same_size_quotes=same_size_quotes,
            pack_size_options=pack_options,
            fulfillment_highlights=highlights,
            private_label_alternative=muadil,
        )


@dataclass
class ShelfTagAuditInput:
    """Kameradan okunan raf etiketinin sahtecilik/sabotaj denetimi için ham sinyalleri."""
    product_gtin: str                      # Ürünün kendi barkodu
    label_printed_gtin: str                # Raf etiketinin köşesinde basılı barkod (Yönetmelik gereği zorunlu)
    label_sale_price_tl: float             # Etiketteki ana satış fiyatı
    label_printed_unit_price: Optional[float]  # Etikette basılı 'Birim Fiyat: X TL/kg' değeri
    net_amount: float                      # Ürünün net miktarı
    is_live_camera_frame: bool = True      # Galeriden yükleme mi, canlı WebRTC akışı mı?
    screen_moire_detected: bool = False    # AI Vision ekran pikseli / monitör yansıması gördü mü?
    in_plastic_shelf_rail: bool = True     # Etiket fiziksel market reyon rayının içinde mi?
    gps_store_distance_m: Optional[float] = 35.0  # Kullanıcının en yakın market şubesine mesafesi (metre)
    user_trust_score: float = 50.0         # Kullanıcının geçmiş doğruluk puanı (0-100)
    independent_confirmations: int = 1     # Aynı şubede kaç farklı cihaz/IP doğruladı?


@dataclass
class ShelfTagAuditVerdict:
    status: str            # "VERIFIED_PUBLIC", "PENDING_SECOND_WITNESS", "QUARANTINED_ANOMALY", "REJECTED_SABOTAGE"
    trust_confidence: float
    can_write_to_crowd_layer: bool
    can_affect_official_median: bool       # Altın kural: Topluluk verisi resmi medyanı kirletmez!
    reasons: List[str]


class ShelfTagAntiSabotageGuard:
    """
    Ticaret Bakanlığı Fiyat Etiketi Yönetmeliği kurallarını, fiziksel görüntü adli analizini (AI Forensics)
    ve istatistiksel sapma sınırlarını kullanarak sahte etiket / sabotaj girişimlerini yakalar.
    """

    @staticmethod
    def verify(
        tag: ShelfTagAuditInput,
        online_median_unit_price: Optional[float] = None,
        official_chain_floor_price: Optional[float] = None,
    ) -> ShelfTagAuditVerdict:
        reasons: List[str] = []

        # Katman 1: Galeri veya ekran görüntüsü ise ürün/fiyat sorgulama serbesttir;
        # yalnızca halka açık "şube raf etiketi veritabanına" otomatik yazılmaz.
        if not tag.is_live_camera_frame:
            return ShelfTagAuditVerdict(
                "PENDING_SECOND_WITNESS", 0.50, False, False,
                ["Fotoğraf galeriden yüklendiği için fiyat karşılaştırması gösterildi; şube raf kaydı yalnızca canlı kamerada işlenir."]
            )
        if tag.screen_moire_detected or not tag.in_plastic_shelf_rail:
            return ShelfTagAuditVerdict(
                "PENDING_SECOND_WITNESS", 0.45, False, False,
                ["Görsel reyon etiketi dışında çekildiği için yalnızca anlık fiyat karşılaştırması yapıldı."]
            )

        # Katman 2: Etiket Üzeri Barkod Eşleşmesi Kontrolü
        clean_prod = "".join(c for c in tag.product_gtin if c.isdigit())
        clean_lbl = "".join(c for c in tag.label_printed_gtin if c.isdigit())
        if clean_prod and clean_lbl and clean_prod != clean_lbl:
            return ShelfTagAuditVerdict(
                "QUARANTINED_ANOMALY", 0.20, False, False,
                [f"Ürün barkodu ({clean_prod}) ile raf etiketindeki barkod ({clean_lbl}) farklı olduğu için şube fiyatı kaydedilmedi."]
            )

        # Katman 3: Etiket İçi Matematiksel Sağlama (Satış Fiyatı / Gramaj == Basılı Birim Fiyat)
        if tag.label_printed_unit_price and tag.net_amount > 0:
            expected_unit = tag.label_sale_price_tl / tag.net_amount
            rel_err = abs(expected_unit - tag.label_printed_unit_price) / max(expected_unit, 0.01)
            if rel_err > 0.05:
                return ShelfTagAuditVerdict(
                    "QUARANTINED_ANOMALY", 0.25, False, False,
                    [
                        f"Etiketteki satış fiyatı ({tag.label_sale_price_tl:.2f} TL) ile birim fiyat ({tag.label_printed_unit_price:.2f}) "
                        f"farklı göründüğü için ek doğrulama bekleniyor."
                    ]
                )

        # Katman 4: Fiziksel Mağaza Konum Sağlaması
        if tag.gps_store_distance_m is not None and tag.gps_store_distance_m > 200.0:
            return ShelfTagAuditVerdict(
                "PENDING_SECOND_WITNESS", 0.40, False, False,
                ["Mağaza dışı konumdan okutulduğu için yalnızca anlık online fiyat karşılaştırması gösterildi."]
            )

        # Katman 5: Resmi Referans Tabanı ve İstatistiksel Z-Skoru / Sapma Karantinası
        calc_unit = tag.label_sale_price_tl / max(tag.net_amount, 0.001)
        if official_chain_floor_price and tag.label_sale_price_tl < official_chain_floor_price * 0.65:
            reasons.append(
                f"Zincir market resmi katalog fiyatının ({official_chain_floor_price:.0f} TL) %35'ten fazla altında; "
                "şube seri sonu mu yoksa hata mı anlaşılması için karantinaya alındı."
            )
            return ShelfTagAuditVerdict("QUARANTINED_ANOMALY", 0.35, False, False, reasons)

        if online_median_unit_price and calc_unit < online_median_unit_price * 0.55:
            reasons.append("Piyasa medyanından %45+ ucuz uç değer; 2. bağımsız cihaz onayı olmadan yayınlanmaz.")
            return ShelfTagAuditVerdict("QUARANTINED_ANOMALY", 0.40, False, False, reasons)

        # Katman 6: Bağımsız Tanık (Farklı Cihaz/IP) ve Kullanıcı Güven Puanı
        if tag.independent_confirmations >= 2 or tag.user_trust_score >= 85.0:
            reasons.append("Barkod, birim fiyat matematiği, reyon perspektifi ve konum doğrulandı.")
            return ShelfTagAuditVerdict("VERIFIED_PUBLIC", 0.96, True, False, reasons)

        reasons.append("İlk gözlem temiz; '1 Kullanıcı Gördü' etiketiyle izlemeye alındı, 2. okutmada kesinleşecek.")
        return ShelfTagAuditVerdict("PENDING_SECOND_WITNESS", 0.75, True, False, reasons)


if __name__ == "__main__":
    # Hızlı doğrulama testi: Markette elde 50'li Fairy tutarken barkod okutma simülasyonu
    db = PriceMemoryDB(":memory:")
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
        family_name="Premium Bulaşık Kapsülü (BİM)",
        category="temizlik",
        base_unit="yıkama",
        reference_net_amount=50.0,
        is_private_label=True,
        equivalent_group="bulasik_kapsulu_premium",
    )

    ean_50 = compute_ean13_with_check("869050651412")
    ean_100 = compute_ean13_with_check("869050653910")
    db.register_barcode(ean_50, "fairy_platinum_plus", "50 Yıkama Tekli Paket", 50.0, "SINGLE")
    db.register_barcode(ean_100, "fairy_platinum_plus", "100 Yıkama (2x50) Avantaj Paketi", 100.0, "MULTI_BULK")

    # Online marketlerdeki farklı boyut ve teslimat seçenekleri
    db.record_snapshot("fairy_platinum_plus", "migros", "Migros Hemen", "Fairy Platinum Plus 50'li", "https://www.migros.com.tr/fairy-50", 399.0, 399.0, 7.98, 50.0)
    db.record_snapshot("fairy_platinum_plus", "hepsiburada", "Hepsiburada Resmi", "Fairy Platinum Plus 75'li", "https://www.hepsiburada.com/fairy-75", 465.0, 465.0, 6.20, 75.0)
    db.record_snapshot("fairy_platinum_plus", "amazon", "Amazon.com.tr", "Fairy Platinum Plus 100'lü (2x50)", "https://www.amazon.com.tr/dp/B09FAIRY100", 519.0, 519.0, 5.19, 100.0, pack_tier="MULTI_BULK")
    db.record_snapshot("fairy_platinum_plus", "hepsiburada", "Hepsiburada Resmi", "Fairy Platinum Plus 150'li Dev Koli", "https://www.hepsiburada.com/fairy-150", 1095.0, 1095.0, 7.30, 150.0, pack_tier="MULTI_BULK")
    db.record_snapshot("bim_bind_activit", "bim", "BİM Mağaza", "Bind Activit 50'li", "https://www.bim.com.tr/", 155.0, 155.0, 3.10, 50.0)

    scanner = CameraScannerEngine(db)
    res = scanner.scan_barcode(ean_50, shelf_price_tl=439.0)
    assert res is not None
    print(f"[OK] Barkod: {res.gtin} ({res.gtin_origin})")
    print(f"[KARAR] {res.verdict_headline}")
    for opt in res.pack_size_options:
        print(f"  - {opt.net_amount:g} {opt.base_unit}: {opt.sale_price:.0f} TL ({opt.effective_unit_price:.2f} TL/{opt.base_unit}) [{opt.best_platform}] -> {opt.badge} | {opt.affiliate_url}")

    # Sabotaj Denetim Testleri
    sabotage_wrong_label = ShelfTagAntiSabotageGuard.verify(
        ShelfTagAuditInput(ean_50, "8690000000018", 29.90, 29.90, 50.0)
    )
    sabotage_fake_math = ShelfTagAntiSabotageGuard.verify(
        ShelfTagAuditInput(ean_50, ean_50, 199.00, 8.78, 50.0)  # 199/50 = 3.98 ama etikette 8.78 kalmış!
    )
    valid_shelf = ShelfTagAntiSabotageGuard.verify(
        ShelfTagAuditInput(ean_50, ean_50, 389.00, 7.78, 50.0, independent_confirmations=2),
        online_median_unit_price=6.50,
    )
    print(f"[SABOTAJ-1 Etiket Kaydırma] -> {sabotage_wrong_label.status}: {sabotage_wrong_label.reasons[0]}")
    print(f"[SABOTAJ-2 Oynanmış Fiyat]  -> {sabotage_fake_math.status}: {sabotage_fake_math.reasons[0]}")
    print(f"[GERÇEK RAF ETİKETİ]        -> {valid_shelf.status}: {valid_shelf.reasons[0]}")
