"""
dipci.tr — Kendi Markalı Sepetini Oluştur (/kendi-sepetim) Motoru
1. Bulunurluk Sıralaması: Peynir, süt, şeker, çay vb. ihtiyaçlarda en çok zincir markette ortak bulunan
   markaları en üstte listeler.
2. Şeffaf Muadil Tamamlama: Seçilen marka bir markette yoksa aynı alt tür ve gramajdaki en yakın muadil
   ürünü otomatik tamamlar; her market için "X Birebir Marka + Y Muadil" dağılımını şeffafça hesaplar.
3. Soğuk Zincir Kuralı: Sepette süt, peynir, yoğurt, tereyağı, kıyma, yumurta gibi soğuk ürünler varsa
   yalnızca 5 zincir marketi (BİM, A101, ŞOK, Migros, CarrefourSA) kıyaslar; yalnızca kuru gıda ve
   temizlik ürünleri varsa Hepsiburada ve Amazon.com.tr gibi online siteleri de dahil eder.
"""

import html as html_lib
import itertools
import json
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

from affiliate import AffiliateLinkBuilder
from live_sync import (
    MARKET_API_URL,
    MARKET_META,
    _norm_tr,
    extract_direct_url_from_image,
)
from memory_db import PriceMemoryDB
from store_accounts import STORE_DELIVERY_POLICIES, compute_store_shipping_eval

CORE_CHAINS = ["bim", "a101", "sok", "migros", "carrefoursa"]
ONLINE_PLATFORMS = ["hepsiburada", "amazon", "n11", "pazarama"]

NEED_CATEGORIES: List[Dict] = [
    {"key": "peynir", "icon": "🧀", "label": "Peynir", "is_cold": True},
    {"key": "sut", "icon": "🥛", "label": "Süt", "is_cold": True},
    {"key": "yogurt_tereyagi", "icon": "🥣", "label": "Yoğurt & Tereyağı", "is_cold": True},
    {"key": "seker_un", "icon": "🍬", "label": "Şeker & Un", "is_cold": False},
    {"key": "cay_kahve", "icon": "☕", "label": "Çay & Kahve", "is_cold": False},
    {"key": "sivi_yag", "icon": "🌻", "label": "Sıvı Yağ & Zeytinyağı", "is_cold": False},
    {"key": "bakliyat_makarna", "icon": "🍝", "label": "Makarna, Bakliyat & Salça", "is_cold": False},
    {"key": "zeytin_kahvalti", "icon": "🍫", "label": "Zeytin & Kahvaltılık", "is_cold": False},
    {"key": "temizlik", "icon": "🧼", "label": "Deterjan & Temizlik", "is_cold": False},
    {"key": "kagit", "icon": "🧻", "label": "Tuvalet Kağıdı & Havlu", "is_cold": False},
    {"key": "bebek_bakim", "icon": "👶", "label": "Bebek & Kişisel Bakım", "is_cold": False},
    {"key": "tumu", "icon": "✨", "label": "Tüm Markalar", "is_cold": False},
]

# Alt ürün tipi -> (need_category, temel_muadil_key, is_cold_chain, okunabilir_etiket)
SUBTYPE_META: Dict[str, Tuple[str, str, bool, str]] = {
    "beyaz_peynir": ("peynir", "temel_beyaz_peynir", True, "Beyaz Peynir"),
    "kasar_peyniri": ("peynir", "temel_kasar_peyniri", True, "Kaşar Peyniri"),
    "suzme_peynir": ("peynir", "temel_beyaz_peynir", True, "Süzme Peynir"),
    "labne_peyniri": ("peynir", "", True, "Labne Peyniri"),
    "sut_1lt": ("sut", "temel_sut_1lt", True, "Süt (1 L)"),
    "yogurt": ("yogurt_tereyagi", "temel_yogurt", True, "Yoğurt"),
    "tereyagi": ("yogurt_tereyagi", "temel_tereyagi", True, "Tereyağı"),
    "yumurta": ("zeytin_kahvalti", "temel_yumurta", True, "Yumurta"),
    "dana_kiyma": ("zeytin_kahvalti", "temel_dana_kiyma", True, "Dana Kıyma"),
    "toz_seker": ("seker_un", "temel_toz_seker", False, "Toz Şeker"),
    "bugday_unu": ("seker_un", "temel_bugday_unu", False, "Buğday Unu"),
    "siyah_cay": ("cay_kahve", "temel_siyah_cay", False, "Siyah Çay"),
    "turk_kahvesi": ("cay_kahve", "", False, "Türk Kahvesi"),
    "granul_kahve": ("cay_kahve", "", False, "Çözünebilir Kahve"),
    "aycicek_yagi": ("sivi_yag", "temel_aycicek_yagi", False, "Ayçiçek Yağı"),
    "zeytinyagi": ("sivi_yag", "", False, "Zeytinyağı"),
    "makarna": ("bakliyat_makarna", "temel_makarna", False, "Makarna"),
    "pirinc": ("bakliyat_makarna", "temel_pirinc", False, "Pirinç"),
    "mercimek": ("bakliyat_makarna", "temel_kirmizi_mercimek", False, "Kırmızı Mercimek"),
    "nohut": ("bakliyat_makarna", "temel_nohut", False, "Nohut"),
    "salca": ("bakliyat_makarna", "temel_salca", False, "Salça"),
    "siyah_zeytin": ("zeytin_kahvalti", "temel_siyah_zeytin", False, "Siyah Zeytin"),
    "findik_kremasi": ("zeytin_kahvalti", "", False, "Kakaolu Fındık Kreması"),
    "bulasik_kapsulu": ("temizlik", "", False, "Bulaşık Makinesi Kapsülü"),
    "sivi_bulasik": ("temizlik", "temel_bulasik_deterjani", False, "Sıvı Bulaşık Deterjanı"),
    "camasir_deterjani": ("temizlik", "", False, "Çamaşır Deterjanı"),
    "yumusatici": ("temizlik", "", False, "Çamaşır Yumuşatıcısı"),
    "yuzey_temizlik": ("temizlik", "", False, "Çamaşır Suyu & Temizleyici"),
    "tuvalet_kagidi": ("kagit", "temel_tuvalet_kagidi", False, "Tuvalet Kağıdı"),
    "kagit_havlu": ("kagit", "", False, "Kağıt Havlu"),
    "bebek_bezi": ("bebek_bakim", "", False, "Bebek Bezi"),
    "islak_mendil": ("bebek_bakim", "", False, "Islak Mendil"),
    "devam_sutu": ("bebek_bakim", "", False, "Bebek Devam Sütü"),
    "sampuan": ("bebek_bakim", "", False, "Şampuan"),
    "dus_jeli_sabun": ("bebek_bakim", "", False, "Duş Jeli & Sıvı Sabun"),
    "dis_macunu": ("bebek_bakim", "", False, "Diş Macunu"),
    "hijyenik_ped": ("bebek_bakim", "", False, "Hijyenik Ped"),
    "deodorant": ("bebek_bakim", "", False, "Deodorant"),
}


def classify_product_subtype(product_key: str, brand: str, family_name: str, category: str) -> str:
    txt = _norm_tr(f"{product_key} {brand} {family_name}")
    if "labne" in txt:
        return "labne_peyniri"
    if "kasar" in txt:
        return "kasar_peyniri"
    if "suzme" in txt and "peynir" in txt:
        return "suzme_peynir"
    if "peynir" in txt:
        return "beyaz_peynir"
    if "devam sutu" in txt or "aptamil" in txt or "bebelac" in txt:
        return "devam_sutu"
    if "yogurt" in txt:
        return "yogurt"
    if "tereyag" in txt:
        return "tereyagi"
    if "sut" in txt:
        return "sut_1lt"
    if "yumurta" in txt:
        return "yumurta"
    if "kiyma" in txt:
        return "dana_kiyma"
    if "seker" in txt:
        return "toz_seker"
    if re.search(r"\bun\b|\bunu\b|soke|sinangil", txt):
        return "bugday_unu"
    if "turk kahve" in txt or "mehmet efendi" in txt:
        return "turk_kahvesi"
    if "kahve" in txt or "nescafe" in txt or "jacobs" in txt:
        return "granul_kahve"
    if "cay" in txt and "camasir" not in txt:
        return "siyah_cay"
    if "zeytinyag" in txt:
        return "zeytinyagi"
    if "aycicek" in txt or "biryag" in txt:
        return "aycicek_yagi"
    if "makarna" in txt or "barilla" in txt or "filiz" in txt or "pastavilla" in txt:
        return "makarna"
    if "pirinc" in txt:
        return "pirinc"
    if "mercimek" in txt:
        return "mercimek"
    if "nohut" in txt:
        return "nohut"
    if "salca" in txt:
        return "salca"
    if "zeytin" in txt:
        return "siyah_zeytin"
    if "nutella" in txt or "banada" in txt or "findik kremasi" in txt:
        return "findik_kremasi"
    if "kapsul" in txt or "quantum" in txt or "platinum plus" in txt:
        return "bulasik_kapsulu"
    if "bulasik" in txt or "pril" in txt:
        return "sivi_bulasik"
    if "yumusatici" in txt or "yumos" in txt or "vernel" in txt:
        return "yumusatici"
    if "domestos" in txt or "cif" in txt or "asperox" in txt:
        return "yuzey_temizlik"
    if "deterjan" in txt or "ariel" in txt or "omo" in txt or "persil" in txt or "perwoll" in txt:
        return "camasir_deterjani"
    if "tuvalet" in txt:
        return "tuvalet_kagidi"
    if "havlu" in txt and "islak" not in txt:
        return "kagit_havlu"
    if "islak mendil" in txt or "mendil" in txt:
        return "islak_mendil"
    if "bez" in txt or "prima" in txt or "molfix" in txt or "canbebe" in txt:
        return "bebek_bezi"
    if "dis macunu" in txt or "sensodyne" in txt or "colgate" in txt or "parodontax" in txt:
        return "dis_macunu"
    if "ped" in txt or "orkid" in txt:
        return "hijyenik_ped"
    if "deodorant" in txt:
        return "deodorant"
    if "dus jeli" in txt or "sabun" in txt:
        return "dus_jeli_sabun"
    if "sampuan" in txt:
        return "sampuan"
    return ""


_ONLINE_CACHE_LOCK = threading.Lock()
_ONLINE_OFFER_CACHE: Dict[str, Tuple[float, Dict[str, Dict]]] = {}
_LIVE_DISCOVER_CACHE: Dict[str, Tuple[float, List[Dict]]] = {}


class CustomBasketEngine:
    def __init__(self, db: PriceMemoryDB, affiliate_builder: AffiliateLinkBuilder) -> None:
        self.db = db
        self.affiliate_builder = affiliate_builder

    def _get_product_best_by_plat(self, product_key: str) -> Dict[str, object]:
        quotes = self.db.get_latest_market_quotes(product_key, window_hours=96)
        trusted = [
            q for q in quotes if q.seller_type in ("OFFICIAL", "AUTHORIZED") and q.seller_rating >= 8.5
        ]
        best_by_plat: Dict[str, object] = {}
        for q in trusted:
            prev = best_by_plat.get(q.platform)
            if prev is None or q.effective_pack_price < prev.effective_pack_price:
                best_by_plat[q.platform] = q
        return best_by_plat

    def _fetch_online_dry_offers_cached(
        self, query: str, target_amt: float, base_unit: str, ref_median_price: float = 0.0
    ) -> Dict[str, Dict]:
        """
        Kuru gıda ve temizlik ürünlerinde Hepsiburada / Amazon.com.tr canlı fiyatlarını
        bellek önbellekli (30 dk TTL) ve gramaj/koli doğrulamalı olarak çeker.
        """
        if base_unit == "kg" and target_amt < 1.0:
            pack_q = f"{int(round(target_amt * 1000))} gr"
        elif base_unit == "litre" and target_amt < 1.0:
            pack_q = f"{int(round(target_amt * 1000))} ml"
        elif base_unit in ("adet", "yıkama"):
            pack_q = f"{int(round(target_amt))}"
        else:
            pack_q = f"{target_amt:g} {base_unit}"

        full_q = f"{query} {pack_q}".strip()
        q_key = f"{_norm_tr(full_q)}|{round(ref_median_price)}"
        now = time.time()
        with _ONLINE_CACHE_LOCK:
            cached = _ONLINE_OFFER_CACHE.get(q_key)
            if cached and (now - cached[0]) < 1800:
                return cached[1]

        result_by_plat: Dict[str, Dict] = {}
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "tr-TR,tr;q=0.9",
        }
        try:
            s_url = f"https://www.akakce.com/arama/?q={urllib.parse.quote_plus(full_q)}"
            with urllib.request.urlopen(urllib.request.Request(s_url, headers=headers), timeout=3.2) as r:
                h = r.read().decode("utf-8", errors="ignore")
            cards = re.findall(r'<li[^>]+data-pr="\d+"[^>]*>(.*?)</li>', h, re.S)
            best_href = ""
            q_toks = [t for t in _norm_tr(query).split() if len(t) >= 3][:2]
            for inner in cards[:8]:
                t_m = re.search(r'<h3[^>]*class="pn_v8"[^>]*>(.*?)</h3>', inner, re.S)
                h_m = re.search(r'href="(/[^",]+,\d+\.html)"', inner)
                if not t_m or not h_m:
                    continue
                t_clean = html_lib.unescape(re.sub(r"<[^>]+>", "", t_m.group(1)).strip())
                t_n = _norm_tr(t_clean)
                # Çoklu koli (örn: 6'lı, 12'li, x4, koli) ilanlarını tekli sepet kıyasından hariç tut
                if re.search(r"\b(\d+)\s*[x\*]\s*\d+|\bkoli\b|\b([2-9]|[12]\d)\s*(?:li|lu|lü|lı|adet)\b", t_n) and base_unit not in ("adet", "yıkama"):
                    continue
                if sum(1 for tok in q_toks if tok in t_n) >= max(1, len(q_toks)):
                    best_href = f"https://www.akakce.com{h_m.group(1)}"
                    break

            if best_href:
                with urllib.request.urlopen(urllib.request.Request(best_href, headers=headers), timeout=3.2) as r2:
                    dh = r2.read().decode("utf-8", errors="ignore")
                for ld_raw in re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', dh, re.S):
                    if '"Product"' not in ld_raw or '"offers"' not in ld_raw:
                        continue
                    prod_ld = json.loads(ld_raw)
                    if prod_ld.get("@type") != "Product":
                        continue
                    p_name = html_lib.unescape(str(prod_ld.get("name") or "").strip())
                    raw_offers = (prod_ld.get("offers") or {}).get("offers") or []
                    if isinstance(raw_offers, list):
                        for off in raw_offers[:18]:
                            price = float(off.get("price") or 0)
                            u = str(off.get("url") or "").strip()
                            seller = str((off.get("seller") or {}).get("name") or "").lower()
                            if price <= 5 or not u:
                                continue
                            # Market medyanına göre aşırı sapmalı (18L teneke / toptan koli) fiyatları ele
                            if ref_median_price > 0 and (price > ref_median_price * 2.25 or price < ref_median_price * 0.35):
                                continue
                            for plat_cand, seller_label in [
                                ("hepsiburada", "Hepsiburada"),
                                ("amazon", "Amazon.com.tr"),
                                ("trendyol", "Trendyol"),
                            ]:
                                if plat_cand in seller or plat_cand in u.lower():
                                    prev = result_by_plat.get(plat_cand)
                                    if not prev or price < prev["pack_price"]:
                                        result_by_plat[plat_cand] = {
                                            "platform": plat_cand,
                                            "seller_name": seller_label,
                                            "title": p_name,
                                            "pack_price": round(price, 2),
                                            "url": self.affiliate_builder.build_tracking_url(u, sub_id="dipci_kendi_sepet"),
                                        }
                    break
        except Exception:
            pass

        with _ONLINE_CACHE_LOCK:
            _ONLINE_OFFER_CACHE[q_key] = (now, result_by_plat)
        return result_by_plat

    # ----------------- 1. BULUNURLUK SIRALAMALI MARKA KEŞFİ -----------------

    def discover_brands(self, need_key: str = "peynir", q: str = "") -> Dict:
        rows = self.db.conn.execute(
            """
            SELECT * FROM canonical_products
            WHERE is_private_label = 0
              AND product_key NOT LIKE 'temel_%'
              AND category != 'teknoloji_beyaz_esya'
            ORDER BY brand ASC
            """
        ).fetchall()

        q_norm = _norm_tr(q).strip()
        items_out: List[Dict] = []

        for r in rows:
            pk = r["product_key"]
            brand = r["brand"]
            fam = r["family_name"]
            cat = r["category"]
            subtype = classify_product_subtype(pk, brand, fam, cat)
            if not subtype or subtype not in SUBTYPE_META:
                continue

            need_cat, _, is_cold, subtype_label = SUBTYPE_META[subtype]
            if not q_norm and need_key and need_key != "tumu" and need_cat != need_key:
                continue

            if q_norm:
                searchable = _norm_tr(f"{brand} {fam} {subtype_label} {need_cat}")
                if not all(tok in searchable for tok in q_norm.split() if len(tok) >= 2):
                    continue

            best_by_plat = self._get_product_best_by_plat(pk)
            if not best_by_plat:
                continue

            chain_hits = [p for p in CORE_CHAINS if p in best_by_plat]
            all_plats = list(best_by_plat.keys())
            prices = [best_by_plat[p].effective_pack_price for p in (chain_hits or all_plats)]
            min_p = round(min(prices), 2)
            max_p = round(max(prices), 2)

            ref_amt = float(r["reference_net_amount"] or 1.0)
            base_u = r["base_unit"]
            if base_u == "kg" and ref_amt < 1.0:
                pack_disp = f"{int(round(ref_amt * 1000))} g"
            elif base_u == "litre" and ref_amt < 1.0:
                pack_disp = f"{int(round(ref_amt * 1000))} ml"
            elif base_u in ("adet", "yıkama"):
                pack_disp = f"{int(round(ref_amt))} {base_u}"
            else:
                pack_disp = f"{ref_amt:g} {base_u}"

            items_out.append(
                {
                    "item_id": pk,
                    "brand": brand,
                    "family_name": fam,
                    "display_name": f"{brand} {fam}",
                    "pack_label": pack_disp,
                    "subtype": subtype,
                    "subtype_label": subtype_label,
                    "need_category": need_cat,
                    "is_cold_chain": is_cold,
                    "image_url": r["image_url"] or "",
                    "chain_availability_count": len(chain_hits),
                    "total_store_count": len(all_plats),
                    "available_chains": chain_hits,
                    "min_price": min_p,
                    "max_price": max_p,
                }
            )

        # Kullanıcı katalog dışı özel bir ürün aradıysa (örn: "ton balığı", "bisküvi", "kola") canlı marketfiyati'ndan tamamla
        if q_norm and len(items_out) < 4:
            live_extras = self._discover_live_marketfiyati_brands(q_norm)
            seen_names = {_norm_tr(x["display_name"]) for x in items_out}
            for lx in live_extras:
                if _norm_tr(lx["display_name"]) not in seen_names:
                    items_out.append(lx)

        # Önce kaç zincir markette ortak bulunduğuna göre (5 -> 4 -> 3 -> 2 -> 1), sonra en uygun fiyata göre sırala
        items_out.sort(
            key=lambda x: (-x["chain_availability_count"], -x["total_store_count"], x["min_price"])
        )

        return {
            "ok": True,
            "categories": NEED_CATEGORIES,
            "active_category": need_key,
            "query": q,
            "count": len(items_out),
            "brands": items_out,
        }

    def _discover_live_marketfiyati_brands(self, query_norm: str) -> List[Dict]:
        now = time.time()
        with _ONLINE_CACHE_LOCK:
            cached = _LIVE_DISCOVER_CACHE.get(query_norm)
            if cached and (now - cached[0]) < 900:
                return cached[1]

        grouped: Dict[str, Dict] = {}
        try:
            req = urllib.request.Request(
                MARKET_API_URL,
                data=json.dumps({"keywords": query_norm, "pages": 0, "size": 36}).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5.5) as resp:
                raw_items = json.loads(resp.read().decode("utf-8", errors="replace")).get("content") or []

            for it in raw_items:
                brand = str(it.get("brand") or "").strip()
                title = str(it.get("title") or "").strip()
                pack_w = str(it.get("refinedVolumeOrWeight") or "").strip()
                img_u = str(it.get("imageUrl") or "").strip()
                if not brand or brand.lower() == "markasız" or not title:
                    continue
                g_key = f"{_norm_tr(brand)}|{_norm_tr(pack_w)}"
                entry = grouped.setdefault(
                    g_key,
                    {
                        "brand": brand,
                        "title": title,
                        "pack_label": pack_w,
                        "image_url": img_u,
                        "chains": {},
                    },
                )
                if img_u and not entry["image_url"]:
                    entry["image_url"] = img_u
                for dep in it.get("productDepotInfoList") or []:
                    raw_m = str(dep.get("marketAdi") or "").lower().strip()
                    if raw_m not in MARKET_META:
                        continue
                    plat_key, seller_lbl, base_s = MARKET_META[raw_m]
                    if plat_key not in CORE_CHAINS:
                        continue
                    p_val = float(dep.get("price") or 0)
                    if p_val <= 1.0:
                        continue
                    d_url = extract_direct_url_from_image(raw_m, title, img_u)
                    f_url = d_url or (base_s if plat_key == "bim" else f"{base_s}{urllib.parse.quote_plus(title)}")
                    prev = entry["chains"].get(plat_key)
                    if not prev or p_val < prev["price"]:
                        entry["chains"][plat_key] = {
                            "title": title,
                            "price": round(p_val, 2),
                            "url": f_url,
                            "seller_name": seller_lbl,
                        }
        except Exception:
            pass

        out: List[Dict] = []
        for g_key, ent in grouped.items():
            if not ent["chains"]:
                continue
            prices = [v["price"] for v in ent["chains"].values()]
            dyn_id = "live::" + urllib.parse.quote(f"{ent['brand']} {ent['pack_label']} {query_norm}".strip())
            is_cold = any(cw in query_norm for cw in ("peynir", "sut", "yogurt", "tereyag", "et", "tavuk", "kiyma", "sucuk", "salam"))
            out.append(
                {
                    "item_id": dyn_id,
                    "brand": ent["brand"],
                    "family_name": ent["title"].replace(ent["brand"], "").strip() or ent["title"],
                    "display_name": ent["title"],
                    "pack_label": ent["pack_label"],
                    "subtype": "canli_arama",
                    "subtype_label": query_norm.title(),
                    "need_category": "tumu",
                    "is_cold_chain": is_cold,
                    "image_url": ent["image_url"],
                    "chain_availability_count": len(ent["chains"]),
                    "total_store_count": len(ent["chains"]),
                    "available_chains": sorted(ent["chains"].keys()),
                    "min_price": min(prices),
                    "max_price": max(prices),
                }
            )

        out.sort(key=lambda x: (-x["chain_availability_count"], x["min_price"]))
        with _ONLINE_CACHE_LOCK:
            _LIVE_DISCOVER_CACHE[query_norm] = (now, out[:18])
        return out[:18]

    # ----------------- 2. MUADİL HAVUZU VE MARKET DAĞILIMI HESAPLAMA -----------------

    def _build_subtype_substitute_pools(self) -> Dict[str, Dict[str, List[Tuple[str, object, float]]]]:
        """
        Her alt ürün tipi (örn: beyaz_peynir, sut_1lt, toz_seker) ve her market için
        mevcut tüm ürünleri (temel_* öz markalar dahil) muadil havuzunda toplar.
        Dönüş: pools[subtype][platform] = [(brand_or_title, LiveMarketQuote, ref_amt), ...]
        """
        pools: Dict[str, Dict[str, List[Tuple[str, object, float]]]] = {}
        all_rows = self.db.conn.execute(
            "SELECT * FROM canonical_products WHERE is_private_label = 0 AND category != 'teknoloji_beyaz_esya'"
        ).fetchall()

        for r in all_rows:
            pk = r["product_key"]
            st = classify_product_subtype(pk, r["brand"], r["family_name"], r["category"])
            if not st:
                continue
            ref_amt = float(r["reference_net_amount"] or 1.0)
            best_by_plat = self._get_product_best_by_plat(pk)
            for plat, q in best_by_plat.items():
                pools.setdefault(st, {}).setdefault(plat, []).append((pk, q, ref_amt))

        return pools

    def _build_split_plan_for_subset(
        self,
        basket_rows: List[Dict],
        allowed_plats: List[str],
        acc_map: Dict[str, Dict],
    ) -> Optional[Dict]:
        """
        Verilen market kümesi içinde her ürünü en ucuz markete (önce birebir marka varsa onu,
        yoksa muadili seçerek) dağıtır; her market durağının alt sepet limiti ve getirme/kargo ücretini hesaplar.
        """
        if not basket_rows or not allowed_plats:
            return None

        stops_by_plat: Dict[str, Dict] = {}
        raw_items_total = 0.0
        exact_picks = 0
        sub_picks = 0

        for row in basket_rows:
            cells = row["cells"]
            exact_cands = [cells[p] for p in allowed_plats if p in cells and not cells[p]["is_substitute"]]
            sub_cands = [cells[p] for p in allowed_plats if p in cells and cells[p]["is_substitute"]]
            chosen = None
            if exact_cands:
                chosen = min(exact_cands, key=lambda c: (c["line_total"], 0 if not c["is_branch_only"] else 1))
                exact_picks += 1
            elif sub_cands:
                chosen = min(sub_cands, key=lambda c: c["line_total"])
                sub_picks += 1
            if not chosen:
                return None

            plat = chosen["platform"]
            pol = STORE_DELIVERY_POLICIES.get(plat) or {}
            st = stops_by_plat.setdefault(
                plat,
                {
                    "platform": plat,
                    "store_name": pol.get("name") or chosen["seller_name"],
                    "short_name": pol.get("short_name") or plat.upper(),
                    "has_online_delivery": bool(pol.get("has_online_delivery", plat != "bim")),
                    "channel_badge": pol.get("channel_badge") or ("🏪 Şube Rafı" if plat == "bim" else "🛵 Online"),
                    "cart_url": pol.get("cart_url") or "",
                    "login_url": pol.get("login_url") or "",
                    "items_subtotal": 0.0,
                    "items": [],
                },
            )
            st["items_subtotal"] = round(st["items_subtotal"] + chosen["line_total"], 2)
            st["items"].append(
                {
                    "item_id": row["item_id"],
                    "display_name": row["display_name"],
                    "store_title": chosen["title"],
                    "image_url": row["image_url"],
                    "qty": row["qty"],
                    "pack_price": chosen["pack_price"],
                    "line_total": chosen["line_total"],
                    "is_substitute": chosen["is_substitute"],
                    "is_branch_only": chosen["is_branch_only"],
                    "tracked_url": chosen["tracked_url"],
                }
            )
            raw_items_total = round(raw_items_total + chosen["line_total"], 2)

        stops_list = sorted(stops_by_plat.values(), key=lambda x: -x["items_subtotal"])
        delivery_fees_total = 0.0
        below_min_count = 0
        physical_stops_count = 0

        for st in stops_list:
            ev = compute_store_shipping_eval(
                st["platform"],
                st["items_subtotal"],
                acc_map.get(st["platform"]),
            )
            st["delivery_eval"] = ev
            st["account_linked"] = ev["account_linked"]
            st["delivery_fee_tl"] = ev["delivery_fee_tl"]
            st["stop_grand_total"] = round(st["items_subtotal"] + ev["delivery_fee_tl"], 2)
            delivery_fees_total = round(delivery_fees_total + ev["delivery_fee_tl"], 2)
            if not st["has_online_delivery"]:
                physical_stops_count += 1
            elif not ev["meets_min_basket"]:
                below_min_count += 1

        return {
            "store_count": len(stops_list),
            "exact_count": exact_picks,
            "substitute_count": sub_picks,
            "raw_items_total": round(raw_items_total, 2),
            "delivery_fees_total": round(delivery_fees_total, 2),
            "grand_total_with_delivery": round(raw_items_total + delivery_fees_total, 2),
            "below_min_count": below_min_count,
            "all_meet_min_basket": below_min_count == 0,
            "physical_stops_count": physical_stops_count,
            "stops": stops_list,
        }

    def compare_custom_basket(
        self,
        raw_items: List[Dict],
        delivery_filter: str = "all",
        user_accounts: Optional[Dict[str, Dict]] = None,
    ) -> Dict:
        acc_map = user_accounts or {}
        online_only = (delivery_filter or "all").strip().lower() == "online_only"
        merged_qty: Dict[str, int] = {}
        for entry in raw_items or []:
            iid = str(entry.get("item_id") or entry.get("product_key") or "").strip()
            qty = max(1, min(20, int(entry.get("qty") or 1)))
            if iid:
                merged_qty[iid] = merged_qty.get(iid, 0) + qty

        default_cols = [c for c in CORE_CHAINS if (c != "bim" or not online_only)]
        if not merged_qty:
            return {
                "ok": True,
                "item_count": 0,
                "delivery_filter": "online_only" if online_only else "all",
                "has_cold_chain": False,
                "cold_chain_note": "",
                "columns": default_cols,
                "store_cards": [],
                "items": [],
                "pure_cheapest_split": None,
                "smart_online_split": None,
                "split_optimum": None,
            }

        pools = self._build_subtype_substitute_pools()
        parsed_requests: List[Dict] = []
        has_cold_chain = False

        for iid, qty in merged_qty.items():
            if iid.startswith("live::"):
                q_str = urllib.parse.unquote(iid[6:])
                is_cold = any(cw in _norm_tr(q_str) for cw in ("peynir", "sut", "yogurt", "tereyag", "kiyma", "tavuk", "sucuk"))
                if is_cold:
                    has_cold_chain = True
                parsed_requests.append({"type": "live", "item_id": iid, "query": q_str, "qty": qty, "is_cold": is_cold})
                continue

            prod = self.db.conn.execute(
                "SELECT * FROM canonical_products WHERE product_key = ?", (iid,)
            ).fetchone()
            if not prod:
                continue
            subtype = classify_product_subtype(iid, prod["brand"], prod["family_name"], prod["category"])
            is_cold = SUBTYPE_META.get(subtype, ("", "", False, ""))[2]
            if is_cold:
                has_cold_chain = True
            parsed_requests.append(
                {
                    "type": "db",
                    "item_id": iid,
                    "prod": prod,
                    "subtype": subtype,
                    "qty": qty,
                    "is_cold": is_cold,
                }
            )

        if not parsed_requests:
            return {
                "ok": True,
                "item_count": 0,
                "has_cold_chain": False,
                "cold_chain_note": "",
                "columns": CORE_CHAINS,
                "store_cards": [],
                "items": [],
                "split_optimum": None,
            }

        # Soğuk zincir kuralı: Sepette soğuk ürün yoksa Hepsiburada ve Amazon'u da paralel çek
        target_platforms = list(CORE_CHAINS)
        online_lookup_by_iid: Dict[str, Dict[str, Dict]] = {}
        if not has_cold_chain and len(parsed_requests) <= 12:
            target_platforms = list(CORE_CHAINS) + ["hepsiburada", "amazon", "trendyol"]
            ref_medians: Dict[str, float] = {}
            for pr in parsed_requests:
                if pr["type"] == "db":
                    eqs = self._get_product_best_by_plat(pr["item_id"])
                    ps = sorted(q.effective_pack_price for q in eqs.values())
                    ref_medians[pr["item_id"]] = ps[len(ps) // 2] if ps else 0.0

            with ThreadPoolExecutor(max_workers=4) as ex:
                futs = {}
                for pr in parsed_requests:
                    if pr["type"] == "db":
                        p = pr["prod"]
                        q_txt = f"{p['brand']} {p['family_name']}"
                        futs[pr["item_id"]] = ex.submit(
                            self._fetch_online_dry_offers_cached,
                            q_txt,
                            float(p["reference_net_amount"] or 1.0),
                            p["base_unit"],
                            ref_medians.get(pr["item_id"], 0.0),
                        )
                for iid_k, fut in futs.items():
                    try:
                        online_lookup_by_iid[iid_k] = fut.result()
                    except Exception:
                        online_lookup_by_iid[iid_k] = {}

        basket_rows: List[Dict] = []
        split_total = 0.0
        worst_total = 0.0

        for pr in parsed_requests:
            iid = pr["item_id"]
            qty = pr["qty"]

            if pr["type"] == "live":
                # Canlı arama kalemi
                q_str = pr["query"]
                live_cands = self._discover_live_marketfiyati_brands(_norm_tr(q_str))
                cells: Dict[str, Dict] = {}
                disp_name = q_str
                img_url = ""
                if live_cands:
                    c0 = live_cands[0]
                    disp_name = c0["display_name"]
                    img_url = c0["image_url"]
                    # Tekrar grup detayından fiyatları çöz
                    req = urllib.request.Request(
                        MARKET_API_URL,
                        data=json.dumps({"keywords": q_str, "pages": 0, "size": 24}).encode("utf-8"),
                        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
                        method="POST",
                    )
                    try:
                        with urllib.request.urlopen(req, timeout=5) as r:
                            raw_its = json.loads(r.read().decode("utf-8", errors="replace")).get("content") or []
                    except Exception:
                        raw_its = []

                    target_brand_n = _norm_tr(c0["brand"])
                    exact_by_plat: Dict[str, Dict] = {}
                    sub_by_plat: Dict[str, Dict] = {}
                    for it in raw_its:
                        b_n = _norm_tr(str(it.get("brand") or ""))
                        t_str = str(it.get("title") or "")
                        im_u = str(it.get("imageUrl") or "")
                        for dep in it.get("productDepotInfoList") or []:
                            rm = str(dep.get("marketAdi") or "").lower().strip()
                            if rm not in MARKET_META:
                                continue
                            pk_m, sl_m, bs_m = MARKET_META[rm]
                            if pk_m not in CORE_CHAINS:
                                continue
                            pv = float(dep.get("price") or 0)
                            if pv <= 1.0:
                                continue
                            du = extract_direct_url_from_image(rm, t_str, im_u) or (
                                bs_m if pk_m == "bim" else f"{bs_m}{urllib.parse.quote_plus(t_str)}"
                            )
                            bucket = exact_by_plat if b_n == target_brand_n else sub_by_plat
                            if pk_m not in bucket or pv < bucket[pk_m]["pack_price"]:
                                bucket[pk_m] = {
                                    "platform": pk_m,
                                    "seller_name": sl_m,
                                    "title": t_str,
                                    "pack_price": round(pv, 2),
                                    "line_total": round(pv * qty, 2),
                                    "is_substitute": b_n != target_brand_n,
                                    "substitute_note": "" if b_n == target_brand_n else "Aynı marka yok → Muadil",
                                    "is_branch_only": pk_m == "bim",
                                    "tracked_url": du,
                                }
                    for plat in CORE_CHAINS:
                        if plat in exact_by_plat:
                            cells[plat] = exact_by_plat[plat]
                        elif plat in sub_by_plat:
                            cells[plat] = sub_by_plat[plat]

                if not cells:
                    continue

                line_vals = [c["line_total"] for c in cells.values()]
                min_l, max_l = min(line_vals), max(line_vals)
                for c in cells.values():
                    c["is_cheapest"] = abs(c["line_total"] - min_l) < 0.05
                split_total = round(split_total + min_l, 2)
                worst_total = round(worst_total + max_l, 2)
                basket_rows.append(
                    {
                        "item_id": iid,
                        "product_key": "",
                        "slug": "",
                        "display_name": disp_name,
                        "subtype_label": "Market Ürünü",
                        "image_url": img_url,
                        "qty": qty,
                        "best_line_total": min_l,
                        "cells": cells,
                    }
                )
                continue

            # DB tabanlı markalı ürün kalemi
            prod = pr["prod"]
            subtype = pr["subtype"]
            subtype_lbl = SUBTYPE_META.get(subtype, ("", "", False, "Ürün"))[3]
            target_amt = float(prod["reference_net_amount"] or 1.0)
            base_u = prod["base_unit"]
            disp_name = f"{prod['brand']} {prod['family_name']}"

            exact_quotes = self._get_product_best_by_plat(iid)
            online_extra = online_lookup_by_iid.get(iid, {})
            cells = {}

            for plat in target_platforms:
                # 1. Birebir aynı marka kontrolü
                if plat in exact_quotes:
                    q = exact_quotes[plat]
                    pack_p = round(q.effective_pack_price, 2)
                    cells[plat] = {
                        "platform": plat,
                        "seller_name": q.seller_name,
                        "title": q.title,
                        "pack_price": pack_p,
                        "line_total": round(pack_p * qty, 2),
                        "is_substitute": False,
                        "substitute_note": "",
                        "is_branch_only": plat in ("bim", "tarimkredi", "hakmar"),
                        "tracked_url": self.affiliate_builder.build_tracking_url(q.url, sub_id="dipci_kendi_sepet"),
                    }
                    continue

                if plat in online_extra:
                    ox = online_extra[plat]
                    pack_p = round(ox["pack_price"], 2)
                    cells[plat] = {
                        "platform": plat,
                        "seller_name": ox["seller_name"],
                        "title": ox["title"],
                        "pack_price": pack_p,
                        "line_total": round(pack_p * qty, 2),
                        "is_substitute": False,
                        "substitute_note": "",
                        "is_branch_only": False,
                        "tracked_url": ox["url"],
                    }
                    continue

                # 2. Aynı marka o markette yoksa -> Aynı alt tür ve gramajdaki en yakın MUADİLİ bul
                sub_candidates = (pools.get(subtype) or {}).get(plat) or []
                if sub_candidates:
                    # Gramajı en yakın ve birim fiyatı en uygun muadili seç
                    def _sub_rank(tup: Tuple[str, object, float]) -> Tuple[float, float]:
                        _, q_cand, _ = tup
                        q_amt = max(float(q_cand.net_amount or target_amt), 0.01)
                        ratio_diff = abs(q_amt - target_amt) / max(target_amt, 0.01)
                        # Yakın gramaj öncelikli (<= %35 fark), sonra uygun paket fiyatı
                        penalty = 0.0 if ratio_diff <= 0.35 else ratio_diff * 50.0
                        return (penalty, q_cand.effective_pack_price)

                    _, best_sub_q, _ = min(sub_candidates, key=_sub_rank)
                    sub_amt = max(float(best_sub_q.net_amount or target_amt), 0.01)
                    ratio = target_amt / sub_amt

                    # Paket gramajı çok farklıysa (örn: seçilen 3 kg şeker ama marketteki muadil 5 kg şeker)
                    # yanıltıcı kasa şişmesini önlemek için gramaj-eşitlenmiş fiyatı baz al ve açıkça belirt
                    if ratio < 0.68 or ratio > 1.45:
                        adj_pack_price = round(best_sub_q.effective_pack_price * ratio, 2)
                        note_txt = f"Muadil ({target_amt:g} {base_u} eşdeğeri)"
                    else:
                        adj_pack_price = round(best_sub_q.effective_pack_price, 2)
                        note_txt = f"Bu markette {prod['brand']} yok → Muadil"

                    cells[plat] = {
                        "platform": plat,
                        "seller_name": best_sub_q.seller_name,
                        "title": best_sub_q.title,
                        "pack_price": adj_pack_price,
                        "line_total": round(adj_pack_price * qty, 2),
                        "is_substitute": True,
                        "substitute_note": note_txt,
                        "is_branch_only": plat in ("bim", "tarimkredi", "hakmar"),
                        "tracked_url": self.affiliate_builder.build_tracking_url(
                            best_sub_q.url, sub_id="dipci_kendi_sepet"
                        ),
                    }

            if not cells:
                continue

            line_vals = [c["line_total"] for c in cells.values()]
            min_l, max_l = min(line_vals), max(line_vals)
            for c in cells.values():
                c["is_cheapest"] = abs(c["line_total"] - min_l) < 0.05

            split_total = round(split_total + min_l, 2)
            worst_total = round(worst_total + max_l, 2)

            basket_rows.append(
                {
                    "item_id": iid,
                    "product_key": iid,
                    "slug": iid.replace("_", "-"),
                    "display_name": disp_name,
                    "brand": prod["brand"],
                    "subtype_label": subtype_lbl,
                    "image_url": prod["image_url"] or "",
                    "qty": qty,
                    "best_line_total": min_l,
                    "cells": cells,
                }
            )

        total_items = len(basket_rows)
        store_cards: List[Dict] = []

        filtered_platforms = [
            p for p in target_platforms
            if (not online_only) or bool((STORE_DELIVERY_POLICIES.get(p) or {}).get("has_online_delivery", p != "bim"))
        ]

        for plat in filtered_platforms:
            exact_cnt = 0
            sub_cnt = 0
            missing_cnt = 0
            checkout_sum = 0.0
            missing_est = 0.0
            substituted_items: List[str] = []

            for row in basket_rows:
                cell = row["cells"].get(plat)
                if cell:
                    checkout_sum = round(checkout_sum + cell["line_total"], 2)
                    if cell["is_substitute"]:
                        sub_cnt += 1
                        substituted_items.append(f"{row['display_name']} ⇄ {cell['title']}")
                    else:
                        exact_cnt += 1
                else:
                    missing_cnt += 1
                    missing_est = round(missing_est + row["best_line_total"], 2)

            covered = exact_cnt + sub_cnt
            # Online pazaryerlerini yalnızca sepetteki ürünlerin en az yarısını karşılıyorsa göster
            if plat not in CORE_CHAINS and covered < max(1, (total_items + 1) // 2):
                continue
            if covered == 0:
                continue

            if missing_cnt == 0 and sub_cnt == 0:
                status_badge = f"✓ {total_items}/{total_items} Tam Birebir Marka"
            elif missing_cnt == 0:
                status_badge = f"✓ {exact_cnt} Birebir Marka + {sub_cnt} Muadil"
            else:
                status_badge = f"{exact_cnt} Birebir + {sub_cnt} Muadil ({missing_cnt} Eksik)"

            pol = STORE_DELIVERY_POLICIES.get(plat) or {}
            deliv_eval = compute_store_shipping_eval(plat, round(checkout_sum, 2), acc_map.get(plat))

            store_cards.append(
                {
                    "platform": plat,
                    "store_name": pol.get("name") or plat.upper(),
                    "short_name": pol.get("short_name") or plat.upper(),
                    "has_online_delivery": deliv_eval["has_online_delivery"],
                    "channel_badge": deliv_eval["channel_badge"],
                    "cart_url": pol.get("cart_url") or "",
                    "login_url": pol.get("login_url") or "",
                    "account_linked": deliv_eval["account_linked"],
                    "exact_count": exact_cnt,
                    "substitute_count": sub_cnt,
                    "covered_count": covered,
                    "missing_count": missing_cnt,
                    "total_requested": total_items,
                    "is_complete_with_subs": missing_cnt == 0,
                    "is_100pct_exact": (missing_cnt == 0 and sub_cnt == 0),
                    "checkout_total": round(checkout_sum, 2),
                    "delivery_fee_tl": deliv_eval["delivery_fee_tl"],
                    "grand_total_with_delivery": round(checkout_sum + deliv_eval["delivery_fee_tl"], 2),
                    "delivery_eval": deliv_eval,
                    "completed_total": round(checkout_sum + missing_est, 2),
                    "status_badge": status_badge,
                    "substituted_items": substituted_items,
                }
            )

        # Sıralama: Önce eksiksiz (birebir + muadil tam) sepetler içinde EN UCUZDAN PAHALIYA
        store_cards.sort(key=lambda x: (-x["covered_count"], x["completed_total"]))
        for idx, sc in enumerate(store_cards):
            sc["rank"] = idx + 1

        active_cols = [sc["platform"] for sc in store_cards]

        # Aktif sütunlara göre tablodaki "EN UCUZ" işaretini güncelle
        for row in basket_rows:
            vis_totals = [row["cells"][p]["line_total"] for p in active_cols if p in row["cells"]]
            if vis_totals:
                v_min = min(vis_totals)
                for p, c in row["cells"].items():
                    c["is_cheapest"] = (p in active_cols) and (abs(c["line_total"] - v_min) < 0.05)

        # 1. Market Gözetmeksizin Saf En Ucuz Karma Sepet (Önce birebir marka en ucuzu, yoksa muadil)
        pure_cheapest_split = self._build_split_plan_for_subset(basket_rows, active_cols, acc_map)
        best_single_total = store_cards[0]["checkout_total"] if store_cards else 0.0
        worst_single_total = max((sc["checkout_total"] for sc in store_cards), default=0.0)
        if pure_cheapest_split:
            pure_cheapest_split["vs_best_single_savings_tl"] = round(
                max(0.0, best_single_total - pure_cheapest_split["raw_items_total"]), 2
            )
            pure_cheapest_split["vs_worst_single_savings_tl"] = round(
                max(0.0, worst_single_total - pure_cheapest_split["raw_items_total"]), 2
            )
            pure_cheapest_split["best_single_store_name"] = store_cards[0]["store_name"] if store_cards else ""
            pure_cheapest_split["best_single_total"] = best_single_total

        # 2. Kargo / Getirme Ücreti ve Minimum Sepet Alt Limiti Dahil En Akıllı Online Dağılım (En fazla 2 online market)
        online_cols = [
            p for p in active_cols
            if bool((STORE_DELIVERY_POLICIES.get(p) or {}).get("has_online_delivery", p != "bim"))
        ]
        # Eğer kullanıcı "Tüm Marketler" modundaysa ama online marketler mevcutsa online adayları da hesapla
        if not online_cols:
            online_cols = [
                p for p in target_platforms
                if bool((STORE_DELIVERY_POLICIES.get(p) or {}).get("has_online_delivery", p != "bim"))
            ]

        smart_candidates: List[Dict] = []
        for k in (1, 2):
            if len(online_cols) < k:
                continue
            for combo in itertools.combinations(online_cols, k):
                plan = self._build_split_plan_for_subset(basket_rows, list(combo), acc_map)
                if plan:
                    smart_candidates.append(plan)

        smart_online_split = None
        if smart_candidates:
            smart_candidates.sort(
                key=lambda pl: (
                    0 if pl["all_meet_min_basket"] else 1,
                    pl["grand_total_with_delivery"],
                    pl["store_count"],
                )
            )
            smart_online_split = smart_candidates[0]

        if online_only:
            cold_note = (
                "🛵 Yalnızca eve online teslimat yapan marketler gösteriliyor (fiziksel şube BİM gizlendi)."
                + (" Soğuk gıda bulunduğu için kargo pazaryerleri hariç tutuldu." if has_cold_chain else "")
            )
        else:
            cold_note = (
                "🧊 Sepetinizde soğuk zincir ürünler (süt, peynir, yoğurt, tereyağı vb.) bulunduğu için kargo siteleri hariç tutuldu; 4 online market + BİM şube kıyaslandı."
                if has_cold_chain
                else "✅ Sepetiniz yalnızca kuru gıda / temizlik ürünlerinden oluştuğu için zincir marketlerin yanında kargo pazaryerleri de kıyaslamaya dahil edildi."
            )

        return {
            "ok": True,
            "item_count": total_items,
            "delivery_filter": "online_only" if online_only else "all",
            "has_cold_chain": has_cold_chain,
            "cold_chain_note": cold_note,
            "columns": active_cols,
            "store_cards": store_cards,
            "items": basket_rows,
            "pure_cheapest_split": pure_cheapest_split,
            "smart_online_split": smart_online_split,
            "split_optimum": {
                "checkout_total": pure_cheapest_split["raw_items_total"] if pure_cheapest_split else round(split_total, 2),
                "worst_checkout_total": round(worst_single_total or worst_total, 2),
                "savings_tl": pure_cheapest_split["vs_worst_single_savings_tl"] if pure_cheapest_split else round(max(0.0, worst_total - split_total), 2),
            },
        }
