"""
dipci.tr — 15 Dakikalık Öncelikli Hızlı Fiyat Radarı, Anlık Fiyat Alarmı Tetikleyici
ve Market Dedektifi Canlı Gerçek İndirim Akışı Motoru.

Çalışma Mimarisi:
1. Kullanıcıların aktif fiyat alarmı kurduğu ürünler (`priority=100`) her 15 dakikada bir kesintisiz kontrol edilir.
2. Market Dedektifi'nin takip ettiği en popüler çok mağazalı ürünler (`priority=50`) dönüşümlü gruplar halinde
   her 15 dakikada bir canlı taranır.
3. Sahte "üstü çizili liste fiyatı" oyunlarını eleyen Shrinkflation Korumalı Gerçek İndirim Dedektörü,
   30 günlük medyan veya rakip mağaza ortalamasının gerçekten %10+ altına inen ürünleri `live_price_drops`
   tablosuna yazar; `MARKET_DEDEKTIFI_WEBHOOK_URL` veya `TELEGRAM_BOT_TOKEN` tanımlıysa saniyesinde bildirim atar.
"""

import argparse
import html
import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from affiliate import AffiliateLinkBuilder
from auto_catalog import AutoCatalogEngine, UNITLESS_ITEM_CATEGORIES
from config import AFFILIATE_CONFIG
from lens_engine import DipciLensEngine, _extract_pack_info_from_text
from live_sync import MARKET_API_URL, MARKET_META, _norm_tr, extract_direct_url_from_image
from variant_guard import is_same_variant
from memory_db import PriceMemoryDB
from price_alerts import PriceAlertService
from seo_engine import CANONICAL_ORIGIN, CATEGORY_META, key_to_slug


class FastPriceRadar:
    def __init__(
        self,
        db: PriceMemoryDB,
        alert_service: Optional[PriceAlertService] = None,
        auto_catalog: Optional[AutoCatalogEngine] = None,
        affiliate_builder: Optional[AffiliateLinkBuilder] = None,
    ) -> None:
        self.db = db
        self.alerts = alert_service or PriceAlertService(db)
        self.lens = DipciLensEngine(db)
        self.catalog = auto_catalog or AutoCatalogEngine(db, self.lens)
        self.affiliate = affiliate_builder or AffiliateLinkBuilder(AFFILIATE_CONFIG)
        self._init_schema()

    def _init_schema(self) -> None:
        self.db.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS watched_radar_products (
                product_key TEXT PRIMARY KEY,
                priority INTEGER NOT NULL DEFAULT 50,     -- 100: Kullanıcı Alarmı, 50: Market Dedektifi VIP
                watch_reason TEXT NOT NULL DEFAULT 'VIP',
                last_checked_at TEXT DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_watched_priority
                ON watched_radar_products(priority DESC, last_checked_at ASC);

            CREATE TABLE IF NOT EXISTS live_price_drops (
                product_key TEXT PRIMARY KEY,
                brand TEXT NOT NULL,
                family_name TEXT NOT NULL,
                category TEXT NOT NULL,
                image_url TEXT DEFAULT '',
                platform TEXT NOT NULL,
                seller_name TEXT NOT NULL,
                old_ref_price REAL NOT NULL,
                new_price REAL NOT NULL,
                drop_pct REAL NOT NULL,
                rival_next_price REAL NOT NULL DEFAULT 0,
                unit_price REAL NOT NULL DEFAULT 0,
                base_unit TEXT NOT NULL DEFAULT 'adet',
                product_url TEXT NOT NULL DEFAULT '',
                seo_url TEXT NOT NULL DEFAULT '',
                share_text TEXT NOT NULL DEFAULT '',
                detected_at TEXT NOT NULL,
                notified_webhook INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_live_drops_pct
                ON live_price_drops(drop_pct DESC, detected_at DESC);
            CREATE INDEX IF NOT EXISTS idx_live_drops_cat
                ON live_price_drops(category, drop_pct DESC);
            """
        )
        self.db.conn.commit()

    def sync_watchlist(self, vip_limit: int = 350) -> Dict[str, int]:
        """
        Aktif kullanıcı alarmlarını `priority=100` olarak işaretler; ayrıca çok mağazada satılan
        en popüler ürünleri `priority=50` olarak hızlı radar listesine dahil eder.
        """
        # Artık aktif alarmı kalmayan 100 öncelikli kayıtları VIP seviyesine düşür
        self.db.conn.execute(
            """
            UPDATE watched_radar_products
            SET priority = 50, watch_reason = 'VIP_TAKIP'
            WHERE priority = 100
              AND product_key NOT IN (SELECT DISTINCT product_key FROM price_alerts WHERE status = 'ACTIVE')
            """
        )

        alert_keys = [
            r["product_key"]
            for r in self.db.conn.execute(
                "SELECT DISTINCT product_key FROM price_alerts WHERE status = 'ACTIVE'"
            ).fetchall()
        ]
        for pk in alert_keys:
            self.db.conn.execute(
                """
                INSERT INTO watched_radar_products (product_key, priority, watch_reason, last_checked_at)
                VALUES (?, 100, 'KULLANICI_ALARMI', '')
                ON CONFLICT(product_key) DO UPDATE SET
                    priority = 100,
                    watch_reason = 'KULLANICI_ALARMI'
                """,
                (pk,),
            )

        vip_rows = self.db.conn.execute(
            """
            SELECT product_key
            FROM product_catalog_index
            WHERE offer_count >= 2
            ORDER BY is_real_drop DESC, offer_count DESC, savings_pct DESC
            LIMIT ?
            """,
            (vip_limit,),
        ).fetchall()
        for vr in vip_rows:
            self.db.conn.execute(
                """
                INSERT OR IGNORE INTO watched_radar_products (product_key, priority, watch_reason, last_checked_at)
                VALUES (?, 50, 'MARKET_DEDEKTIFI_RADAR', '')
                """,
                (vr["product_key"],),
            )

        self.db.conn.commit()
        total_watched = self.db.conn.execute("SELECT COUNT(*) AS c FROM watched_radar_products").fetchone()["c"]
        return {"active_alert_products": len(alert_keys), "total_watched_products": total_watched}

    def _refresh_single_supermarket_product(self, prod_row) -> bool:
        pkey = prod_row["product_key"]
        brand = prod_row["brand"]
        fam = prod_row["family_name"]
        base_unit = prod_row["base_unit"]
        ref_amt = float(prod_row["reference_net_amount"] or 1.0)
        q_str = f"{brand} {re.sub(r'\\([^)]*\\)', '', fam)}".strip()

        payload = json.dumps({"keywords": q_str, "pages": 0, "size": 24}).encode("utf-8")
        req = urllib.request.Request(
            MARKET_API_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=7) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            items = data.get("content") or []
        except Exception:
            return False

        b_norm = _norm_tr(brand)
        fam_tokens = {
            w for w in re.findall(r"[a-z0-9]+", _norm_tr(re.sub(r"\([^)]*\)", "", fam)))
            if len(w) >= 3
        }
        per_store: Dict[str, Dict] = {}

        for it in items:
            raw_title = str(it.get("title") or "").strip()
            raw_b = str(it.get("brand") or "").strip()
            ref_w = str(it.get("refinedVolumeOrWeight") or "").strip()
            img_u = str(it.get("imageUrl") or "").strip()
            depots = it.get("productDepotInfoList") or []
            if not raw_title or not depots:
                continue

            t_norm = _norm_tr(f"{raw_b} {raw_title}")
            if b_norm and b_norm not in t_norm:
                continue
            if fam_tokens:
                overlap = sum(1 for tok in fam_tokens if tok in t_norm)
                if overlap < max(1, min(2, len(fam_tokens))):
                    continue

            # Farklı varyantı (yağ oranı, laktozsuz, aroma...) aynı ürün sayma
            if not is_same_variant(f"{brand} {fam}", f"{raw_b} {raw_title}"):
                continue

            p_info = _extract_pack_info_from_text(f"{ref_w} {raw_title}")
            if p_info and p_info[1] == base_unit and ref_amt > 0:
                ratio = p_info[0] / ref_amt
                if not (0.82 <= ratio <= 1.20):
                    continue
            item_amt = p_info[0] if (p_info and p_info[1] == base_unit and p_info[0] > 0) else ref_amt

            for dep in depots:
                raw_m = str(dep.get("marketAdi") or "").strip().lower()
                if raw_m not in MARKET_META:
                    continue
                try:
                    price = float(dep.get("price") or 0)
                except Exception:
                    price = 0.0
                if price <= 1.0:
                    continue
                plat_key, seller_label, base_search_url = MARKET_META[raw_m]
                direct_u = extract_direct_url_from_image(raw_m, raw_title, img_u)
                is_branch = plat_key in ("bim", "tarimkredi", "hakmar")
                final_url = direct_u or (
                    base_search_url if is_branch else f"{base_search_url}{urllib.parse.quote_plus(raw_title)}"
                )
                cand = {
                    "platform": plat_key,
                    "seller_name": seller_label,
                    "title": raw_title,
                    "price": round(price, 2),
                    "url": final_url,
                    "amount": item_amt,
                }
                prev = per_store.get(plat_key)
                if prev is None or cand["price"] / cand["amount"] < prev["price"] / prev["amount"]:
                    per_store[plat_key] = cand

        if not per_store:
            return False

        now = datetime.now()
        today_prefix = now.strftime("%Y-%m-%d")
        for off in per_store.values():
            u_price = round(off["price"] / max(off["amount"], 0.01), 2)
            self.db.conn.execute(
                """
                DELETE FROM price_snapshots
                WHERE product_key = ? AND platform = ? AND seller_name = ?
                  AND substr(captured_at, 1, 10) = ?
                """,
                (pkey, off["platform"], off["seller_name"], today_prefix),
            )
            self.db.conn.execute(
                """
                INSERT INTO price_snapshots
                (product_key, platform, seller_name, seller_type, seller_rating, title, url,
                 captured_at, list_price, sale_price, effective_pack_price, effective_unit_price,
                 net_amount, pack_tier, warranty_type, zero_interest_months, energy_class, model_code_seen)
                VALUES (?, ?, ?, 'OFFICIAL', 9.5, ?, ?, ?, NULL, ?, ?, ?, ?, 'SINGLE', 'TR_OFFICIAL', 0, '', '')
                """,
                (
                    pkey,
                    off["platform"],
                    off["seller_name"],
                    off["title"],
                    off["url"],
                    now.isoformat(),
                    off["price"],
                    off["price"],
                    u_price,
                    max(off["amount"], 0.01),
                ),
            )
        self.db.conn.commit()
        self.db.rebuild_catalog_index([pkey])
        return True

    def _refresh_single_online_product(self, prod_row) -> bool:
        pkey = prod_row["product_key"]
        q_str = f"{prod_row['brand']} {prod_row['family_name']}".strip()
        try:
            time.sleep(0.3)
            res = self.lens.analyze_and_compare(query_override=q_str)
            offers = res.get("offers") or []
            if not res.get("ok") or not offers:
                return False
            now = datetime.now()
            today_prefix = now.strftime("%Y-%m-%d")
            ref_amt = max(float(prod_row["reference_net_amount"] or 1.0), 0.01)
            is_unitless = prod_row["category"] in UNITLESS_ITEM_CATEGORIES
            for off in offers:
                price = round(float(off.get("price") or 0.0), 2)
                if price <= 1.0:
                    continue
                item_amt = 1.0 if is_unitless else ref_amt
                u_price = round(price / item_amt, 2)
                plat = str(off.get("platform") or "default").strip().lower()
                seller = str(off.get("seller_name") or plat.upper()).strip()
                url = str(off.get("url") or "").strip()
                title = str(off.get("title") or q_str).strip()
                self.db.conn.execute(
                    """
                    DELETE FROM price_snapshots
                    WHERE product_key = ? AND platform = ? AND seller_name = ?
                      AND substr(captured_at, 1, 10) = ?
                    """,
                    (pkey, plat, seller, today_prefix),
                )
                self.db.conn.execute(
                    """
                    INSERT INTO price_snapshots
                    (product_key, platform, seller_name, seller_type, seller_rating, title, url,
                     captured_at, list_price, sale_price, effective_pack_price, effective_unit_price,
                     net_amount, pack_tier, warranty_type, zero_interest_months, energy_class, model_code_seen)
                    VALUES (?, ?, ?, 'OFFICIAL', 9.5, ?, ?, ?, ?, ?, ?, ?, ?, 'SINGLE', 'TR_OFFICIAL', 0, '', '')
                    """,
                    (
                        pkey,
                        plat,
                        seller,
                        title,
                        url,
                        now.isoformat(),
                        off.get("list_price"),
                        price,
                        price,
                        u_price,
                        item_amt,
                    ),
                )
            self.db.conn.commit()
            self.db.rebuild_catalog_index([pkey])
            return True
        except Exception:
            return False

    def refresh_watched_batch(self, max_vip_items: int = 40, max_online_vip: int = 6) -> Dict[str, int]:
        """
        1) Tüm aktif kullanıcı alarmlı ürünleri (priority=100) eksiksiz kontrol eder.
        2) VIP takip listesinden en uzun süredir kontrol edilmemiş `max_vip_items` ürünü canlı günceller.
        """
        alert_rows = self.db.conn.execute(
            """
            SELECT w.product_key, w.priority, c.brand, c.family_name, c.category, c.base_unit, c.reference_net_amount
            FROM watched_radar_products w
            JOIN canonical_products c ON w.product_key = c.product_key
            WHERE w.priority >= 100
            ORDER BY w.last_checked_at ASC
            """
        ).fetchall()

        vip_rows = self.db.conn.execute(
            """
            SELECT w.product_key, w.priority, c.brand, c.family_name, c.category, c.base_unit, c.reference_net_amount
            FROM watched_radar_products w
            JOIN canonical_products c ON w.product_key = c.product_key
            WHERE w.priority < 100
            ORDER BY w.last_checked_at ASC
            LIMIT ?
            """,
            (max_vip_items,),
        ).fetchall()

        checked = 0
        updated = 0
        online_used = 0
        now_iso = datetime.now().isoformat()

        for row in list(alert_rows) + list(vip_rows):
            pk = row["product_key"]
            is_alert = int(row["priority"]) >= 100
            is_online_cat = row["category"] in UNITLESS_ITEM_CATEGORIES
            ok = False

            if is_online_cat:
                if is_alert or online_used < max_online_vip:
                    online_used += 1
                    ok = self._refresh_single_online_product(row)
                else:
                    continue
            else:
                ok = self._refresh_single_supermarket_product(row)
                if not ok and is_alert and online_used < max_online_vip + 4:
                    online_used += 1
                    ok = self._refresh_single_online_product(row)

            checked += 1
            if ok:
                updated += 1
            self.db.conn.execute(
                "UPDATE watched_radar_products SET last_checked_at = ? WHERE product_key = ?",
                (now_iso, pk),
            )

        self.db.conn.commit()
        return {"checked_count": checked, "updated_count": updated}

    def detect_and_publish_drops(self) -> Dict[str, int]:
        """
        `product_catalog_index` içindeki gerçek indirimleri (`is_real_drop = 1`) tarar,
        `live_price_drops` tablosunu günceller ve yeni yakalanan büyük düşüşler için
        Market Dedektifi Webhook / Telegram kanallarına anında bildirim gönderir.
        """
        # Artık indirimde olmayan eski kayıtları temizle
        self.db.conn.execute(
            """
            DELETE FROM live_price_drops
            WHERE product_key NOT IN (
                SELECT product_key FROM product_catalog_index WHERE is_real_drop = 1 AND offer_count >= 2
            )
            """
        )

        drop_rows = self.db.conn.execute(
            """
            SELECT *
            FROM product_catalog_index
            WHERE is_real_drop = 1 AND offer_count >= 2 AND best_pack_price > 5.0
            ORDER BY drop_vs_median_pct DESC, savings_pct DESC
            LIMIT 250
            """
        ).fetchall()

        existing_notified = {
            r["product_key"]: (float(r["new_price"]), int(r["notified_webhook"]))
            for r in self.db.conn.execute("SELECT product_key, new_price, notified_webhook FROM live_price_drops").fetchall()
        }

        now_iso = datetime.now().isoformat()
        newly_notified = 0

        for r in drop_rows:
            pk = r["product_key"]
            top_quotes = json.loads(r["top_quotes_json"] or "[]")
            rival_next = float(top_quotes[1]["pack_price"]) if len(top_quotes) >= 2 else float(r["worst_pack_price"] or 0.0)
            seo_url = f"{CANONICAL_ORIGIN}/urun/{key_to_slug(pk)}"
            tracked_store_url = self.affiliate.build_tracking_url(r["best_url"] or seo_url, sub_id="dedektif_radar")

            old_ref = round(float(r["median_30d_pack_price"] or rival_next), 2)
            new_p = round(float(r["best_pack_price"]), 2)
            drop_pct = round(float(r["drop_vs_median_pct"]), 1)
            # Denetim (Kemal, 18 Kas 2026): başka paket, bitmiş kampanya ya da veri hatası şeride girmez
            try:
                from denetim import kampanya_hatasi
                if kampanya_hatasi(self.db.conn, pk, r["best_platform"] or "", new_p, old_ref, drop_pct, r["category"],
                                   f"{r['brand']} {r['family_name']}"):
                    self.db.conn.execute("DELETE FROM live_price_drops WHERE product_key = ?", (pk,))
                    continue
            except Exception:
                pass

            new_p_fmt = f"{new_p:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
            old_ref_fmt = f"{old_ref:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
            share_text = (
                f"Gerçek İndirim Yakalandı: {r['brand']} {r['family_name']} şu an {r['best_seller_name']} "
                f"mağazasında {new_p_fmt} TL! (Referans piyasa ortalaması: {old_ref_fmt} TL • %{drop_pct:.0f} avantaj). "
                f"Tüm mağaza karşılaştırması: {seo_url}"
            )

            prev_info = existing_notified.get(pk)
            already_pushed = 1 if (prev_info and prev_info[1] == 1 and abs(prev_info[0] - new_p) < 0.5) else 0

            if not already_pushed and drop_pct >= 15.0:
                if self._push_outbound_deal_signal(
                    product_key=pk,
                    brand=r["brand"],
                    family_name=r["family_name"],
                    category=r["category"],
                    seller_name=r["best_seller_name"],
                    old_price=old_ref,
                    new_price=new_p,
                    drop_pct=drop_pct,
                    seo_url=seo_url,
                    store_url=tracked_store_url,
                    share_text=share_text,
                ):
                    already_pushed = 1
                    newly_notified += 1

            self.db.conn.execute(
                """
                INSERT OR REPLACE INTO live_price_drops
                (product_key, brand, family_name, category, image_url, platform, seller_name,
                 old_ref_price, new_price, drop_pct, rival_next_price, unit_price, base_unit,
                 product_url, seo_url, share_text, detected_at, notified_webhook)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pk,
                    r["brand"],
                    r["family_name"],
                    r["category"],
                    r["image_url"] or "",
                    r["best_platform"] or "",
                    r["best_seller_name"] or "",
                    old_ref,
                    new_p,
                    drop_pct,
                    round(rival_next, 2),
                    round(float(r["best_unit_price"] or 0.0), 2),
                    r["base_unit"] or "adet",
                    tracked_store_url,
                    seo_url,
                    share_text,
                    now_iso,
                    already_pushed,
                ),
            )

        self.db.conn.commit()
        return {"active_real_drops": len(drop_rows), "webhooks_dispatched": newly_notified}

    @staticmethod
    def _push_outbound_deal_signal(
        product_key: str,
        brand: str,
        family_name: str,
        category: str,
        seller_name: str,
        old_price: float,
        new_price: float,
        drop_pct: float,
        seo_url: str,
        store_url: str,
        share_text: str,
    ) -> bool:
        sent_any = False
        webhook_url = os.getenv("MARKET_DEDEKTIFI_WEBHOOK_URL", "").strip()
        if webhook_url.startswith(("http://", "https://")):
            try:
                body = json.dumps(
                    {
                        "event": "REAL_PRICE_DROP",
                        "product_key": product_key,
                        "brand": brand,
                        "family_name": family_name,
                        "category": category,
                        "seller_name": seller_name,
                        "reference_median_price_tl": old_price,
                        "current_best_price_tl": new_price,
                        "drop_pct": drop_pct,
                        "dipci_url": seo_url,
                        "store_url": store_url,
                        "share_text": share_text,
                        "detected_at": datetime.now().isoformat(),
                    },
                    ensure_ascii=False,
                ).encode("utf-8")
                req = urllib.request.Request(
                    webhook_url,
                    data=body,
                    headers={"Content-Type": "application/json", "User-Agent": "dipci-fast-radar/1.0"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=6) as resp:
                    if 200 <= resp.status < 300:
                        sent_any = True
            except Exception:
                pass

        tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        # Kemal, 18 Kas 2026: "bu bana gerçek indirim yakaladım diye neden bildirim gönderiyor" — radar Kemal'in kişisel
        # sohbetine (TELEGRAM_CHAT_ID) yazmaz; yalnız ayrıca bir yayın kanalı (RADAR_TELEGRAM_CHAT_ID) tanımlanırsa oraya yazar.
        tg_chat = os.getenv("RADAR_TELEGRAM_CHAT_ID", "").strip()
        if tg_token and tg_chat:
            try:
                tg_payload = json.dumps(
                    {
                        "chat_id": tg_chat,
                        "text": share_text,
                        "disable_web_page_preview": False,
                    },
                    ensure_ascii=False,
                ).encode("utf-8")
                tg_req = urllib.request.Request(
                    f"https://api.telegram.org/bot{tg_token}/sendMessage",
                    data=tg_payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(tg_req, timeout=6) as resp:
                    if 200 <= resp.status < 300:
                        sent_any = True
            except Exception:
                pass

        return sent_any

    def list_live_drops(
        self,
        category: str = "",
        limit: int = 48,
        min_drop_pct: float = 10.0,
    ) -> List[Dict]:
        safe_lim = max(1, min(int(limit or 48), 200))
        cat_clause = "AND l.category = ?" if category else ""
        params: list = [float(min_drop_pct)]
        if category:
            params.append(category)
        params.append(safe_lim)

        rows = self.db.conn.execute(
            f"""
            SELECT l.*, COALESCE(p.offer_count, 2) AS offer_count
            FROM live_price_drops l
            LEFT JOIN product_catalog_index p ON l.product_key = p.product_key
            WHERE l.drop_pct >= ?
              {cat_clause}
            ORDER BY l.drop_pct DESC, l.detected_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            cat_meta = CATEGORY_META.get(r["category"], {})
            d["category_title"] = cat_meta.get("title", r["category"])
            d["old_ref_pack_price"] = d["old_ref_price"]
            d["new_pack_price"] = d["new_price"]
            d["new_unit_price"] = d["unit_price"]
            d["tracked_url"] = d["product_url"]
            out.append(d)
        return out

    def build_rss_xml(self, limit: int = 40) -> str:
        drops = self.list_live_drops(limit=limit, min_drop_pct=10.0)
        items_xml = []
        for d in drops:
            title = f"%{d['drop_pct']:.0f} Gerçek İndirim: {d['brand']} {d['family_name']} — {d['new_price']:.2f} TL ({d['seller_name']})"
            items_xml.append(
                f"""    <item>
      <title>{html.escape(title)}</title>
      <link>{html.escape(d['seo_url'])}</link>
      <guid isPermaLink="false">{html.escape(d['product_key'])}-{int(d['new_price'])}</guid>
      <description>{html.escape(d['share_text'])}</description>
      <pubDate>{html.escape(d['detected_at'])}</pubDate>
    </item>"""
            )
        joined = "\n".join(items_xml)
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>dipci.tr &amp; Market Dedektifi — Canlı Gerçek İndirim Radarı</title>
    <link>{CANONICAL_ORIGIN}/firsatlar</link>
    <description>30 günlük medyan ve çoklu mağaza denetiminden geçen gerçek fiyat düşüşleri.</description>
    <language>tr-TR</language>
{joined}
  </channel>
</rss>"""

    def run_cycle(self, refresh_live: bool = True, max_vip_items: int = 40) -> Dict:
        t0 = time.time()
        watch_stats = self.sync_watchlist()
        ref_stats = (
            self.refresh_watched_batch(max_vip_items=max_vip_items)
            if refresh_live
            else {"checked_count": 0, "updated_count": 0}
        )
        alerts_triggered = self.alerts.evaluate_active_alerts()
        drop_stats = self.detect_and_publish_drops()
        return {
            "ok": True,
            "elapsed_sec": round(time.time() - t0, 2),
            **watch_stats,
            **ref_stats,
            "alerts_triggered": alerts_triggered,
            **drop_stats,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="dipci.tr 15 Dakikalık Hızlı Fiyat Radarı")
    parser.add_argument("--run", action="store_true", help="Tam 15dk radar döngüsünü çalıştır")
    parser.add_argument("--detect-only", action="store_true", help="Dış sorgu yapmadan mevcut katalogdan indirimleri hesapla")
    parser.add_argument("--vip-batch", type=int, default=40, help="Her döngüde kontrol edilecek VIP ürün adedi")
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent
    db_path = Path(os.getenv("DIPCI_DB_PATH", str(base_dir / "data" / "dipci_platform.db")))
    db = PriceMemoryDB(db_path)
    radar = FastPriceRadar(db)
    res = radar.run_cycle(refresh_live=not args.detect_only, max_vip_items=max(5, args.vip_batch))
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
