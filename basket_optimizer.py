"""
dipci.tr — Sade ve Anlaşılır Market Alışveriş Listesi & Yan Yana Fiş Karşılaştırma Motoru
Her evin gerçek temel ihtiyaçlarını BİM, A101, ŞOK, Migros ve CarrefourSA'da yan yana tablo (fiş)
halinde karşılaştırır; tıklanan her market hücresi doğrudan ilgili ürüne gider.
"""

from typing import Dict, List

from affiliate import AffiliateLinkBuilder
from memory_db import PriceMemoryDB

QUICK_STAPLES: List[Dict[str, str]] = [
    {"product_key": "temel_yumurta", "icon": "🥚", "short": "Yumurta"},
    {"product_key": "temel_sut_1lt", "icon": "🥛", "short": "Süt (1 L)"},
    {"product_key": "temel_yogurt", "icon": "🥣", "short": "Yoğurt"},
    {"product_key": "temel_beyaz_peynir", "icon": "🧀", "short": "Beyaz Peynir"},
    {"product_key": "temel_kasar_peyniri", "icon": "🧀", "short": "Kaşar Peyniri"},
    {"product_key": "temel_tereyagi", "icon": "🧈", "short": "Tereyağı"},
    {"product_key": "temel_dana_kiyma", "icon": "🥩", "short": "Dana Kıyma"},
    {"product_key": "temel_siyah_zeytin", "icon": "🥗", "short": "Siyah Zeytin"},
    {"product_key": "temel_aycicek_yagi", "icon": "🌻", "short": "Ayçiçek Yağı"},
    {"product_key": "temel_bugday_unu", "icon": "🍞", "short": "Un (2 Kg)"},
    {"product_key": "temel_toz_seker", "icon": "🍬", "short": "Toz Şeker"},
    {"product_key": "temel_kirmizi_mercimek", "icon": "🍲", "short": "Kırmızı Mercimek"},
    {"product_key": "temel_nohut", "icon": "🥘", "short": "Nohut"},
    {"product_key": "temel_pirinc", "icon": "🍚", "short": "Pirinç (1 Kg)"},
    {"product_key": "temel_makarna", "icon": "🍝", "short": "Makarna"},
    {"product_key": "temel_salca", "icon": "🍅", "short": "Salça"},
    {"product_key": "temel_siyah_cay", "icon": "☕", "short": "Çay (1 Kg)"},
    {"product_key": "temel_tuvalet_kagidi", "icon": "🧻", "short": "Tuvalet Kağıdı"},
    {"product_key": "temel_bulasik_deterjani", "icon": "🧽", "short": "Bulaşık Deterjanı"},
]

PRESET_BASKETS: Dict[str, Dict] = {
    "haftalik_temel": {
        "key": "haftalik_temel",
        "title": "🥚 Haftalık Temel Ev Alışverişi",
        "subtitle": "Yumurta, Süt, Yoğurt, Beyaz Peynir, Ayçiçek Yağı, Kıyma, Makarna, Çay",
        "items": [
            {"product_key": "temel_yumurta", "qty": 1},
            {"product_key": "temel_sut_1lt", "qty": 2},
            {"product_key": "temel_yogurt", "qty": 1},
            {"product_key": "temel_beyaz_peynir", "qty": 1},
            {"product_key": "temel_aycicek_yagi", "qty": 1},
            {"product_key": "temel_dana_kiyma", "qty": 1},
            {"product_key": "temel_makarna", "qty": 2},
            {"product_key": "temel_siyah_cay", "qty": 1},
        ],
    },
    "aylik_kiler": {
        "key": "aylik_kiler",
        "title": "🍲 Aylık Bakliyat & Kiler Listesi",
        "subtitle": "Mercimek, Nohut, Pirinç, Un, Toz Şeker, Ayçiçek Yağı, Salça, Makarna",
        "items": [
            {"product_key": "temel_kirmizi_mercimek", "qty": 1},
            {"product_key": "temel_nohut", "qty": 1},
            {"product_key": "temel_pirinc", "qty": 1},
            {"product_key": "temel_bugday_unu", "qty": 1},
            {"product_key": "temel_toz_seker", "qty": 1},
            {"product_key": "temel_aycicek_yagi", "qty": 1},
            {"product_key": "temel_salca", "qty": 1},
            {"product_key": "temel_makarna", "qty": 2},
        ],
    },
    "temizlik_kagit": {
        "key": "temizlik_kagit",
        "title": "🧼 Ev Temizlik & Kağıt İhtiyaçları",
        "subtitle": "Tuvalet Kağıdı, Bulaşık Deterjanı, Makine Kapsülü, Çamaşır Deterjanı, Çamaşır Suyu",
        "items": [
            {"product_key": "temel_tuvalet_kagidi", "qty": 1},
            {"product_key": "temel_bulasik_deterjani", "qty": 1},
            {"product_key": "fairy_platinum_plus", "qty": 1},
            {"product_key": "omo_sivi_deterjan", "qty": 1},
            {"product_key": "domestos_camasir_suyu", "qty": 1},
        ],
    },
}

CORE_SUPERMARKET_ORDER = ["bim", "a101", "sok", "migros", "carrefoursa"]


class BasketOptimizer:
    def __init__(self, db: PriceMemoryDB, affiliate_builder: AffiliateLinkBuilder) -> None:
        self.db = db
        self.affiliate_builder = affiliate_builder

    def optimize(self, raw_items: List[Dict]) -> Dict:
        merged_qty: Dict[str, int] = {}
        for entry in raw_items or []:
            pk = str(entry.get("product_key") or "").strip()
            qty = max(1, min(20, int(entry.get("qty") or 1)))
            if pk:
                merged_qty[pk] = merged_qty.get(pk, 0) + qty

        if not merged_qty:
            return {
                "ok": True,
                "item_count": 0,
                "columns": CORE_SUPERMARKET_ORDER,
                "items": [],
                "single_store_ranking": [],
                "split_optimum": None,
            }

        basket_rows = []
        extra_platforms_seen = []
        split_checkout_total = 0.0
        worst_checkout_total = 0.0
        stops_map: Dict[str, Dict] = {}

        for pk, qty in merged_qty.items():
            prod = self.db.conn.execute(
                "SELECT * FROM canonical_products WHERE product_key = ?", (pk,)
            ).fetchone()
            if not prod:
                continue

            quotes = self.db.get_latest_market_quotes(pk, window_hours=72)
            trusted = [
                q for q in quotes if q.seller_type in ("OFFICIAL", "AUTHORIZED") and q.seller_rating >= 8.8
            ]
            if not trusted:
                continue

            # Her market için kasa paket fiyatı en uygun olan güncel ürünü seç
            best_by_plat: Dict[str, object] = {}
            for q in trusted:
                prev = best_by_plat.get(q.platform)
                if prev is None or q.effective_pack_price < prev.effective_pack_price:
                    best_by_plat[q.platform] = q

            for plat in best_by_plat:
                if plat not in CORE_SUPERMARKET_ORDER and plat not in ("hakmar", "tarimkredi", "trendyol"):
                    if plat not in extra_platforms_seen:
                        extra_platforms_seen.append(plat)

            # Tablo hücrelerini hazırla (öncelikle 5 büyük zincir + varsa online pazaryerleri)
            candidate_plats = [
                p for p in (CORE_SUPERMARKET_ORDER + extra_platforms_seen) if p in best_by_plat
            ] or list(best_by_plat.keys())

            ordered_for_row = sorted(
                [best_by_plat[p] for p in candidate_plats],
                key=lambda x: x.effective_pack_price,
            )
            cheapest_q = ordered_for_row[0]
            priciest_q = ordered_for_row[-1]

            min_line_total = round(cheapest_q.effective_pack_price * qty, 2)
            max_line_total = round(priciest_q.effective_pack_price * qty, 2)
            split_checkout_total = round(split_checkout_total + min_line_total, 2)
            worst_checkout_total = round(worst_checkout_total + max_line_total, 2)

            cells: Dict[str, Dict] = {}
            for plat, q in best_by_plat.items():
                line_total = round(q.effective_pack_price * qty, 2)
                is_branch_only = plat in ("bim", "tarimkredi", "hakmar")
                cells[plat] = {
                    "platform": plat,
                    "seller_name": q.seller_name,
                    "title": q.title,
                    "pack_price": round(q.effective_pack_price, 2),
                    "line_total": line_total,
                    "net_amount": q.net_amount,
                    "base_unit": prod["base_unit"],
                    "unit_price": round(q.effective_unit_price, 2),
                    "is_cheapest": abs(line_total - min_line_total) < 0.05,
                    "is_branch_only": is_branch_only,
                    "tracked_url": self.affiliate_builder.build_tracking_url(q.url, sub_id="dipci_sepet"),
                }

            img_url = prod["image_url"] if ("image_url" in prod.keys() and prod["image_url"]) else ""
            display_name = (
                prod["family_name"]
                if prod["brand"] == "Temel İhtiyaç"
                else f"{prod['brand']} {prod['family_name']}"
            )

            basket_rows.append(
                {
                    "product_key": pk,
                    "slug": pk.replace("_", "-"),
                    "brand": prod["brand"],
                    "family_name": prod["family_name"],
                    "display_name": display_name,
                    "base_unit": prod["base_unit"],
                    "image_url": img_url,
                    "qty": qty,
                    "best_platform": cheapest_q.platform,
                    "best_line_total": min_line_total,
                    "worst_line_total": max_line_total,
                    "cells": cells,
                }
            )

            st = stops_map.setdefault(
                cheapest_q.platform,
                {
                    "platform": cheapest_q.platform,
                    "seller_name": cheapest_q.seller_name,
                    "checkout_subtotal": 0.0,
                    "items": [],
                },
            )
            st["checkout_subtotal"] = round(st["checkout_subtotal"] + min_line_total, 2)
            st["items"].append(
                {
                    "product_key": pk,
                    "display_name": display_name,
                    "title": cheapest_q.title,
                    "qty": qty,
                    "pack_price": round(cheapest_q.effective_pack_price, 2),
                    "checkout_line_total": min_line_total,
                    "tracked_url": self.affiliate_builder.build_tracking_url(cheapest_q.url, sub_id="dipci_sepet"),
                }
            )

        total_requested = len(basket_rows)
        # Online pazaryerlerini (Amazon/HB) yalnızca listenin en az yarısında ürün varsa tabloya ek sütun yap
        qualified_extras = [
            ep
            for ep in extra_platforms_seen
            if sum(1 for r in basket_rows if ep in r["cells"]) >= max(1, (total_requested + 1) // 2)
        ]
        active_columns = list(CORE_SUPERMARKET_ORDER) + qualified_extras[:2]

        # Her sütun (mağaza) için toplam kasa fişi hesabı
        ranking = []
        for plat in active_columns:
            matched = 0
            checkout_sum = 0.0
            missing_names = []
            missing_fallback_sum = 0.0
            lines = []

            for row in basket_rows:
                cell = row["cells"].get(plat)
                if cell:
                    matched += 1
                    checkout_sum = round(checkout_sum + cell["line_total"], 2)
                    lines.append(
                        {
                            "product_key": row["product_key"],
                            "display_name": row["display_name"],
                            "title": cell["title"],
                            "qty": row["qty"],
                            "checkout_line_total": cell["line_total"],
                            "tracked_url": cell["tracked_url"],
                        }
                    )
                else:
                    missing_names.append(row["display_name"])
                    missing_fallback_sum = round(missing_fallback_sum + row["best_line_total"], 2)

            if matched == 0:
                continue

            ranking.append(
                {
                    "platform": plat,
                    "matched_count": matched,
                    "total_requested": total_requested,
                    "is_full_match": matched == total_requested,
                    "checkout_total": checkout_sum,
                    "completed_total": round(checkout_sum + missing_fallback_sum, 2),
                    "missing_products": missing_names,
                    "lines": lines,
                }
            )

        ranking.sort(key=lambda x: (-x["matched_count"], x["completed_total"]))

        full_matches = [r for r in ranking if r["is_full_match"]]
        best_single = full_matches[0] if full_matches else (ranking[0] if ranking else None)
        worst_single = full_matches[-1] if len(full_matches) >= 2 else None

        single_vs_worst_savings = 0.0
        if best_single and worst_single and worst_single["checkout_total"] > best_single["checkout_total"]:
            single_vs_worst_savings = round(worst_single["checkout_total"] - best_single["checkout_total"], 2)

        extra_split_savings = 0.0
        if best_single and best_single["is_full_match"]:
            extra_split_savings = max(0.0, round(best_single["checkout_total"] - split_checkout_total, 2))

        stops_list = sorted(stops_map.values(), key=lambda x: -x["checkout_subtotal"])

        return {
            "ok": True,
            "item_count": total_requested,
            "columns": [r["platform"] for r in ranking] or active_columns,
            "quick_staples": QUICK_STAPLES,
            "items": basket_rows,
            "single_store_ranking": ranking,
            "single_vs_worst_savings_tl": single_vs_worst_savings,
            "split_optimum": {
                "checkout_total": round(split_checkout_total, 2),
                "worst_checkout_total": round(worst_checkout_total, 2),
                "total_savings_vs_worst_tl": max(0.0, round(worst_checkout_total - split_checkout_total, 2)),
                "extra_savings_vs_single_winner_tl": extra_split_savings,
                "store_count": len(stops_list),
                "stops": stops_list,
            },
        }
