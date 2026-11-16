"""
Market Dedektifi — Zaman Serisi Fiyat ve Gramaj Hafızası (SQLite WAL)
Her ürünün tüm mağazalardaki geçmiş etiket fiyatlarını, gerçek birim fiyatlarını,
paket gramajlarını ve taksit/garanti koşullarını tarih bazlı saklar.
"""

import json
import sqlite3
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional


@dataclass
class HistoricalStats:
    sample_count: int
    median_30d_unit_price: Optional[float]
    median_90d_unit_price: Optional[float]
    min_90d_unit_price: Optional[float]
    max_7d_unit_price: Optional[float]
    baseline_net_amount: Optional[float]  # İlk/standart kaydedilen gramaj (Shrinkflation kıyası için)


@dataclass
class LiveMarketQuote:
    platform: str
    seller_name: str
    seller_type: str          # "OFFICIAL", "AUTHORIZED", "THIRD_PARTY"
    seller_rating: float      # 0.0 - 10.0
    title: str
    url: str
    list_price: Optional[float]
    sale_price: float
    effective_pack_price: float
    effective_unit_price: float
    net_amount: float
    base_unit: str
    pack_tier: str            # "SINGLE", "MULTI_BULK"
    warranty_type: str        # "TR_OFFICIAL", "IMPORTER", "REFURB_DISPLAY"
    zero_interest_months: int
    energy_class: str


class PriceMemoryDB:
    def __init__(self, db_path: str | Path = ":memory:"):
        self.db_path = str(db_path)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            PRAGMA journal_mode = WAL;

            CREATE TABLE IF NOT EXISTS canonical_products (
                product_key TEXT PRIMARY KEY,
                brand TEXT NOT NULL,
                family_name TEXT NOT NULL,
                category TEXT NOT NULL,           -- gida, temizlik, anne_bebek, kisisel_bakim, teknoloji_beyaz_esya
                base_unit TEXT NOT NULL,          -- kg, litre, yıkama, 100 std yaprak, adet
                reference_net_amount REAL,        -- Standart/tarihsel paket miktarı
                is_private_label INTEGER DEFAULT 0,
                equivalent_group TEXT,            -- Muadil eşleştirme grubu (örn: "bulasik_kapsulu_premium")
                official_model_code TEXT,         -- Elektronik/beyaz eşyada orijinal model kodu
                image_url TEXT DEFAULT ''         -- Resmi ürün görsel adresi
            );

            CREATE TABLE IF NOT EXISTS price_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_key TEXT NOT NULL,
                platform TEXT NOT NULL,
                seller_name TEXT NOT NULL,
                seller_type TEXT NOT NULL DEFAULT 'OFFICIAL',
                seller_rating REAL DEFAULT 10.0,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                captured_at TEXT NOT NULL,        -- ISO-8601 UTC/Local
                list_price REAL,                  -- Üstü çizili sözde fiyat
                sale_price REAL NOT NULL,         -- Etiket satış fiyatı
                effective_pack_price REAL NOT NULL,
                effective_unit_price REAL NOT NULL,
                net_amount REAL NOT NULL,
                pack_tier TEXT DEFAULT 'SINGLE',  -- SINGLE veya MULTI_BULK
                warranty_type TEXT DEFAULT 'TR_OFFICIAL',
                zero_interest_months INTEGER DEFAULT 0,
                energy_class TEXT DEFAULT '',
                model_code_seen TEXT DEFAULT '',
                FOREIGN KEY(product_key) REFERENCES canonical_products(product_key)
            );

            CREATE INDEX IF NOT EXISTS idx_snapshots_prod_time
                ON price_snapshots(product_key, captured_at);
            CREATE INDEX IF NOT EXISTS idx_snapshots_prod_plat
                ON price_snapshots(product_key, platform, captured_at);
            CREATE INDEX IF NOT EXISTS idx_snapshots_captured_prod
                ON price_snapshots(captured_at, product_key);
            CREATE INDEX IF NOT EXISTS idx_products_cat_pl
                ON canonical_products(is_private_label, category);
            CREATE INDEX IF NOT EXISTS idx_products_brand
                ON canonical_products(brand COLLATE NOCASE);

            CREATE TABLE IF NOT EXISTS product_barcodes (
                gtin TEXT PRIMARY KEY,            -- EAN-13 / EAN-8 / UPC barkodu (örn: 8690506514129)
                product_key TEXT NOT NULL,        -- Bağlı olduğu ana ürün ailesi (örn: fairy_platinum_plus)
                variant_label TEXT NOT NULL,      -- Paket varyant adı (örn: "50 Yıkama Tekli Paket")
                net_amount REAL NOT NULL,         -- Bu barkodun net miktarı (örn: 50.0)
                pack_tier TEXT DEFAULT 'SINGLE',
                FOREIGN KEY(product_key) REFERENCES canonical_products(product_key)
            );

            CREATE INDEX IF NOT EXISTS idx_barcodes_product
                ON product_barcodes(product_key);

            CREATE TABLE IF NOT EXISTS product_catalog_index (
                product_key TEXT PRIMARY KEY,
                brand TEXT NOT NULL,
                family_name TEXT NOT NULL,
                category TEXT NOT NULL,
                base_unit TEXT NOT NULL,
                image_url TEXT DEFAULT '',
                search_norm TEXT NOT NULL DEFAULT '',
                barcodes_json TEXT NOT NULL DEFAULT '[]',
                offer_count INTEGER NOT NULL DEFAULT 0,
                best_platform TEXT DEFAULT '',
                best_seller_name TEXT DEFAULT '',
                best_pack_price REAL NOT NULL DEFAULT 0,
                best_unit_price REAL NOT NULL DEFAULT 0,
                best_net_amount REAL NOT NULL DEFAULT 1,
                best_url TEXT DEFAULT '',
                worst_platform TEXT DEFAULT '',
                worst_pack_price REAL NOT NULL DEFAULT 0,
                worst_unit_price REAL NOT NULL DEFAULT 0,
                savings_pct REAL NOT NULL DEFAULT 0,
                median_30d_pack_price REAL NOT NULL DEFAULT 0,
                drop_vs_median_pct REAL NOT NULL DEFAULT 0,
                is_real_drop INTEGER NOT NULL DEFAULT 0,
                platforms_csv TEXT NOT NULL DEFAULT '',
                top_quotes_json TEXT NOT NULL DEFAULT '[]',
                verdict TEXT NOT NULL DEFAULT 'NORMAL_PIYASA_FIYATI',
                detective_score INTEGER NOT NULL DEFAULT 75,
                updated_at TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_catidx_cat_rank
                ON product_catalog_index(category, offer_count DESC, savings_pct DESC);
            CREATE INDEX IF NOT EXISTS idx_catidx_drops
                ON product_catalog_index(is_real_drop DESC, drop_vs_median_pct DESC);
            CREATE INDEX IF NOT EXISTS idx_catidx_brand
                ON product_catalog_index(brand COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS idx_catidx_price
                ON product_catalog_index(best_pack_price);
            """
        )
        cols = {
            r["name"]
            for r in self.conn.execute("PRAGMA table_info(canonical_products)").fetchall()
        }
        if "image_url" not in cols:
            self.conn.execute("ALTER TABLE canonical_products ADD COLUMN image_url TEXT DEFAULT ''")
        self.conn.commit()

    def set_product_image(self, product_key: str, image_url: str, commit: bool = True) -> None:
        if not image_url:
            return
        self.conn.execute(
            "UPDATE canonical_products SET image_url = ? WHERE product_key = ? AND (image_url IS NULL OR image_url = '')",
            (image_url.strip(), product_key),
        )
        if commit:
            self.conn.commit()

    def register_product(
        self,
        product_key: str,
        brand: str,
        family_name: str,
        category: str,
        base_unit: str,
        reference_net_amount: float,
        is_private_label: bool = False,
        equivalent_group: Optional[str] = None,
        official_model_code: str = "",
        image_url: str = "",
        commit: bool = True,
    ) -> None:
        existing = self.conn.execute(
            "SELECT image_url FROM canonical_products WHERE product_key = ?", (product_key,)
        ).fetchone()
        final_img = image_url.strip() or (existing["image_url"] if existing and existing["image_url"] else "")
        self.conn.execute(
            """
            INSERT OR REPLACE INTO canonical_products
            (product_key, brand, family_name, category, base_unit, reference_net_amount,
             is_private_label, equivalent_group, official_model_code, image_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                product_key,
                brand,
                family_name,
                category,
                base_unit,
                reference_net_amount,
                1 if is_private_label else 0,
                equivalent_group,
                official_model_code,
                final_img,
            ),
        )
        if commit:
            self.conn.commit()

    def record_snapshot(
        self,
        product_key: str,
        platform: str,
        seller_name: str,
        title: str,
        url: str,
        sale_price: float,
        effective_pack_price: float,
        effective_unit_price: float,
        net_amount: float,
        captured_at: Optional[datetime] = None,
        list_price: Optional[float] = None,
        seller_type: str = "OFFICIAL",
        seller_rating: float = 10.0,
        pack_tier: str = "SINGLE",
        warranty_type: str = "TR_OFFICIAL",
        zero_interest_months: int = 0,
        energy_class: str = "",
        model_code_seen: str = "",
    ) -> None:
        ts = (captured_at or datetime.now()).isoformat()
        self.conn.execute(
            """
            INSERT INTO price_snapshots
            (product_key, platform, seller_name, seller_type, seller_rating, title, url,
             captured_at, list_price, sale_price, effective_pack_price, effective_unit_price,
             net_amount, pack_tier, warranty_type, zero_interest_months, energy_class, model_code_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                product_key,
                platform,
                seller_name,
                seller_type,
                seller_rating,
                title,
                url,
                ts,
                list_price,
                sale_price,
                effective_pack_price,
                effective_unit_price,
                net_amount,
                pack_tier,
                warranty_type,
                zero_interest_months,
                energy_class,
                model_code_seen,
            ),
        )
        self.conn.commit()

    def get_historical_stats(
        self,
        product_key: str,
        platform: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> HistoricalStats:
        """Son 7, 30 ve 90 günlük kendi fiyat hafızamızdan medyan ve dip/tepe istatistiklerini üretir."""
        ref_now = now or datetime.now()
        t_7d = (ref_now - timedelta(days=7)).isoformat()
        t_30d = (ref_now - timedelta(days=30)).isoformat()
        t_90d = (ref_now - timedelta(days=90)).isoformat()
        # Bugünkü anlık kaydı geçmiş medyana karıştırmamak için 12 saat öncesine kadar olan geçmişi baz alırız
        cutoff_recent = (ref_now - timedelta(hours=12)).isoformat()

        params: list = [product_key, t_90d, cutoff_recent]
        plat_filter = ""
        if platform:
            plat_filter = " AND platform = ?"
            params.append(platform)

        rows = self.conn.execute(
            f"""
            SELECT captured_at, effective_unit_price, net_amount
            FROM price_snapshots
            WHERE product_key = ?
              AND captured_at >= ?
              AND captured_at <= ?
              AND seller_type IN ('OFFICIAL', 'AUTHORIZED')
              {plat_filter}
            ORDER BY captured_at ASC
            """,
            params,
        ).fetchall()

        prod_row = self.conn.execute(
            "SELECT reference_net_amount FROM canonical_products WHERE product_key = ?",
            (product_key,),
        ).fetchone()
        baseline_amt = float(prod_row["reference_net_amount"]) if prod_row and prod_row["reference_net_amount"] else None

        if not rows:
            return HistoricalStats(0, None, None, None, None, baseline_amt)

        prices_90d = [float(r["effective_unit_price"]) for r in rows]
        prices_30d = [float(r["effective_unit_price"]) for r in rows if r["captured_at"] >= t_30d]
        prices_7d = [float(r["effective_unit_price"]) for r in rows if r["captured_at"] >= t_7d]

        if baseline_amt is None:
            baseline_amt = float(rows[0]["net_amount"])

        return HistoricalStats(
            sample_count=len(rows),
            median_30d_unit_price=round(statistics.median(prices_30d), 4) if prices_30d else round(statistics.median(prices_90d), 4),
            median_90d_unit_price=round(statistics.median(prices_90d), 4),
            min_90d_unit_price=round(min(prices_90d), 4),
            max_7d_unit_price=round(max(prices_7d), 4) if prices_7d else None,
            baseline_net_amount=baseline_amt,
        )

    def get_latest_market_quotes(
        self,
        product_key: str,
        now: Optional[datetime] = None,
        window_hours: int = 168,
    ) -> List[LiveMarketQuote]:
        """Son pencere içinde tüm market ve sitelerden toplanan en güncel fiyatları döner."""
        ref_now = now or datetime.now()
        since = (ref_now - timedelta(hours=window_hours)).isoformat()

        prod = self.conn.execute(
            "SELECT base_unit FROM canonical_products WHERE product_key = ?",
            (product_key,),
        ).fetchone()
        base_unit = prod["base_unit"] if prod else "birim"

        rows = self.conn.execute(
            """
            SELECT s.*
            FROM price_snapshots s
            INNER JOIN (
                SELECT platform, seller_name, pack_tier, MAX(captured_at) AS max_ts
                FROM price_snapshots
                WHERE product_key = ?
                  AND captured_at >= ?
                  AND seller_name NOT LIKE '%Referans%'
                  AND url NOT LIKE 'https://dipci.tr%'
                GROUP BY platform, seller_name, pack_tier
            ) latest
            ON s.platform = latest.platform
               AND s.seller_name = latest.seller_name
               AND s.pack_tier = latest.pack_tier
               AND s.captured_at = latest.max_ts
            WHERE s.product_key = ?
              AND s.seller_name NOT LIKE '%Referans%'
              AND s.url NOT LIKE 'https://dipci.tr%'
            ORDER BY s.effective_unit_price ASC
            """,
            (product_key, since, product_key),
        ).fetchall()

        if not rows:
            rows = self.conn.execute(
                """
                SELECT s.*
                FROM price_snapshots s
                INNER JOIN (
                    SELECT platform, seller_name, pack_tier, MAX(captured_at) AS max_ts
                    FROM price_snapshots
                    WHERE product_key = ?
                      AND seller_name NOT LIKE '%Referans%'
                      AND url NOT LIKE 'https://dipci.tr%'
                    GROUP BY platform, seller_name, pack_tier
                ) latest
                ON s.platform = latest.platform
                   AND s.seller_name = latest.seller_name
                   AND s.pack_tier = latest.pack_tier
                   AND s.captured_at = latest.max_ts
                WHERE s.product_key = ?
                  AND s.seller_name NOT LIKE '%Referans%'
                  AND s.url NOT LIKE 'https://dipci.tr%'
                ORDER BY s.effective_unit_price ASC
                """,
                (product_key, product_key),
            ).fetchall()

        return [
            LiveMarketQuote(
                platform=r["platform"],
                seller_name=r["seller_name"],
                seller_type=r["seller_type"],
                seller_rating=float(r["seller_rating"]),
                title=r["title"],
                url=r["url"],
                list_price=float(r["list_price"]) if r["list_price"] is not None else None,
                sale_price=float(r["sale_price"]),
                effective_pack_price=float(r["effective_pack_price"]),
                effective_unit_price=float(r["effective_unit_price"]),
                net_amount=float(r["net_amount"]),
                base_unit=base_unit,
                pack_tier=r["pack_tier"],
                warranty_type=r["warranty_type"],
                zero_interest_months=int(r["zero_interest_months"]),
                energy_class=r["energy_class"],
            )
            for r in rows
        ]

    def find_private_label_alternative(
        self,
        equivalent_group: str,
        now: Optional[datetime] = None,
    ) -> Optional[Dict]:
        """Aynı eşdeğer ürün grubundaki en uygun market markalı (muadil) ürünü bulur."""
        ref_now = now or datetime.now()
        since = (ref_now - timedelta(hours=48)).isoformat()
        row = self.conn.execute(
            """
            SELECT c.brand, c.family_name, c.base_unit, s.platform, s.sale_price,
                   s.effective_unit_price, s.title
            FROM canonical_products c
            JOIN price_snapshots s ON c.product_key = s.product_key
            WHERE c.equivalent_group = ?
              AND c.is_private_label = 1
              AND s.captured_at >= ?
            ORDER BY s.effective_unit_price ASC
            LIMIT 1
            """,
            (equivalent_group, since),
        ).fetchone()
        return dict(row) if row else None

    def register_barcode(
        self,
        gtin: str,
        product_key: str,
        variant_label: str,
        net_amount: float,
        pack_tier: str = "SINGLE",
        commit: bool = True,
    ) -> None:
        """Bir EAN-13 / EAN-8 / UPC barkodunu ana ürün ailesine ve kendi gramaj/paket boyutuna bağlar."""
        clean_gtin = "".join(ch for ch in gtin if ch.isdigit())
        self.conn.execute(
            """
            INSERT OR REPLACE INTO product_barcodes
            (gtin, product_key, variant_label, net_amount, pack_tier)
            VALUES (?, ?, ?, ?, ?)
            """,
            (clean_gtin, product_key, variant_label, net_amount, pack_tier),
        )
        if commit:
            self.conn.commit()

    def lookup_by_barcode(self, gtin: str) -> Optional[Dict]:
        """Kameradan okutulan barkoddan ana ürün ailesini ve eldeki paketin tam boyutunu döner."""
        clean_gtin = "".join(ch for ch in gtin if ch.isdigit())
        row = self.conn.execute(
            """
            SELECT b.gtin, b.product_key, b.variant_label, b.net_amount AS scanned_net_amount,
                   b.pack_tier AS scanned_pack_tier,
                   c.brand, c.family_name, c.category, c.base_unit,
                   c.reference_net_amount, c.equivalent_group, c.official_model_code
            FROM product_barcodes b
            JOIN canonical_products c ON b.product_key = c.product_key
            WHERE b.gtin = ?
            """,
            (clean_gtin,),
        ).fetchone()
        return dict(row) if row else None

    def match_by_visual_clues(
        self,
        brand: str = "",
        product_text: str = "",
        model_code: str = "",
    ) -> Optional[Dict]:
        """
        Barkod görünmediğinde (ön ambalaj veya mağaza teşhir ürünü fotoğrafı çekildiğinde)
        önce model koduyla, yoksa marka + ürün adı anahtar kelimeleriyle ana ürünü bulur.
        """
        if model_code:
            norm_code = model_code.strip().upper().replace(" ", "")
            row = self.conn.execute(
                """
                SELECT * FROM canonical_products
                WHERE REPLACE(UPPER(official_model_code), ' ', '') = ?
                """,
                (norm_code,),
            ).fetchone()
            if row:
                return dict(row)

        all_products = self.conn.execute("SELECT * FROM canonical_products").fetchall()
        haystack_tokens = set(
            f"{brand} {product_text}".lower()
            .replace("ı", "i").replace("ş", "s").replace("ğ", "g")
            .replace("ü", "u").replace("ö", "o").replace("ç", "c")
            .split()
        )
        best_row = None
        best_score = 0
        for r in all_products:
            cand_str = f"{r['brand']} {r['family_name']} {r['official_model_code']}"
            cand_tokens = set(
                cand_str.lower()
                .replace("ı", "i").replace("ş", "s").replace("ğ", "g")
                .replace("ü", "u").replace("ö", "o").replace("ç", "c")
                .split()
            )
            overlap = len(haystack_tokens & cand_tokens)
            if r["brand"].lower() in f"{brand} {product_text}".lower():
                overlap += 2
            if overlap > best_score:
                best_score = overlap
                best_row = r

        return dict(best_row) if best_row and best_score >= 2 else None

    @staticmethod
    def _norm_tr_idx(text: str) -> str:
        return (
            (text or "")
            .replace("İ", "i")
            .replace("I", "ı")
            .lower()
            .replace("ı", "i")
            .replace("ş", "s")
            .replace("ğ", "g")
            .replace("ü", "u")
            .replace("ö", "o")
            .replace("ç", "c")
            .replace("é", "e")
            .strip()
        )

    def rebuild_catalog_index(self, product_keys: Optional[Iterable[str]] = None) -> int:
        """
        On binlerce ürünü < 5 ms'de filtrelemek, sıralamak ve gerçek indirimleri anında sunmak için
        `product_catalog_index` özet tablosunu toplu SQL sorgularıyla günceller.
        """
        now_dt = datetime.now()
        now_iso = now_dt.isoformat()
        cutoff_hist = (now_dt - timedelta(hours=12)).isoformat()
        t_30d = (now_dt - timedelta(days=35)).isoformat()

        keys_list = [k for k in (product_keys or []) if k]
        if keys_list:
            placeholders = ",".join("?" for _ in keys_list)
            prod_rows = self.conn.execute(
                f"SELECT * FROM canonical_products WHERE is_private_label = 0 AND product_key IN ({placeholders})",
                keys_list,
            ).fetchall()
            bc_rows = self.conn.execute(
                f"SELECT product_key, gtin FROM product_barcodes WHERE product_key IN ({placeholders})",
                keys_list,
            ).fetchall()
            latest_rows = self.conn.execute(
                f"""
                SELECT s.*
                FROM price_snapshots s
                INNER JOIN (
                    SELECT product_key, platform, seller_name, MAX(captured_at) AS max_ts
                    FROM price_snapshots
                    WHERE product_key IN ({placeholders})
                      AND seller_name NOT LIKE '%Referans%'
                      AND url NOT LIKE 'https://dipci.tr%'
                    GROUP BY product_key, platform, seller_name
                ) m ON s.product_key = m.product_key
                   AND s.platform = m.platform
                   AND s.seller_name = m.seller_name
                   AND s.captured_at = m.max_ts
                WHERE s.product_key IN ({placeholders})
                  AND s.seller_name NOT LIKE '%Referans%'
                  AND s.url NOT LIKE 'https://dipci.tr%'
                """,
                keys_list + keys_list,
            ).fetchall()
            hist_rows = self.conn.execute(
                f"""
                SELECT product_key, effective_pack_price, effective_unit_price
                FROM price_snapshots
                WHERE product_key IN ({placeholders})
                  AND captured_at >= ?
                  AND captured_at <= ?
                  AND seller_type IN ('OFFICIAL', 'AUTHORIZED')
                """,
                keys_list + [t_30d, cutoff_hist],
            ).fetchall()
        else:
            prod_rows = self.conn.execute(
                "SELECT * FROM canonical_products WHERE is_private_label = 0"
            ).fetchall()
            bc_rows = self.conn.execute("SELECT product_key, gtin FROM product_barcodes").fetchall()
            latest_rows = self.conn.execute(
                """
                SELECT s.*
                FROM price_snapshots s
                INNER JOIN (
                    SELECT product_key, platform, seller_name, MAX(captured_at) AS max_ts
                    FROM price_snapshots
                    WHERE seller_name NOT LIKE '%Referans%'
                      AND url NOT LIKE 'https://dipci.tr%'
                    GROUP BY product_key, platform, seller_name
                ) m ON s.product_key = m.product_key
                   AND s.platform = m.platform
                   AND s.seller_name = m.seller_name
                   AND s.captured_at = m.max_ts
                WHERE s.seller_name NOT LIKE '%Referans%'
                  AND s.url NOT LIKE 'https://dipci.tr%'
                """
            ).fetchall()
            hist_rows = self.conn.execute(
                """
                SELECT product_key, effective_pack_price, effective_unit_price
                FROM price_snapshots
                WHERE captured_at >= ?
                  AND captured_at <= ?
                  AND seller_type IN ('OFFICIAL', 'AUTHORIZED')
                """,
                (t_30d, cutoff_hist),
            ).fetchall()

        bc_map: Dict[str, List[str]] = {}
        for b in bc_rows:
            bc_map.setdefault(b["product_key"], []).append(b["gtin"])

        quotes_map: Dict[str, List[sqlite3.Row]] = {}
        for lr in latest_rows:
            quotes_map.setdefault(lr["product_key"], []).append(lr)

        hist_pack_map: Dict[str, List[float]] = {}
        hist_unit_map: Dict[str, List[float]] = {}
        for hr in hist_rows:
            pk = hr["product_key"]
            hist_pack_map.setdefault(pk, []).append(float(hr["effective_pack_price"] or 0.0))
            hist_unit_map.setdefault(pk, []).append(float(hr["effective_unit_price"] or 0.0))

        upsert_params = []
        for r in prod_rows:
            pk = r["product_key"]
            bcs = bc_map.get(pk, [])
            q_list = quotes_map.get(pk, [])
            trusted = [
                q for q in q_list
                if q["seller_type"] in ("OFFICIAL", "AUTHORIZED") and float(q["seller_rating"] or 0.0) >= 8.8
            ]
            active_q = trusted if trusted else q_list
            ref_amt = max(float(r["reference_net_amount"] or 1.0), 0.01)

            # 1) Gramaj/adet uyuşmazlığı filtresi (ör. 180g ile 500g veya 6'lı paket ile tekli şişe karışmasın)
            size_matched_q = [
                q for q in active_q
                if float(q["effective_pack_price"] or 0.0) > 1.0
                and (0.75 * ref_amt <= float(q["net_amount"] or ref_amt) <= 1.28 * ref_amt)
            ]
            base_q = size_matched_q if size_matched_q else [q for q in active_q if float(q["effective_pack_price"] or 0.0) > 1.0]

            # 2) Stok dışı fahiş fiyat (ör. 105.555 TL) ve çoklu koli aykırı değer filtresi
            if len(base_q) >= 2:
                raw_prices = sorted(float(q["effective_pack_price"]) for q in base_q)
                anchor_p = statistics.median(raw_prices) if len(raw_prices) >= 3 else raw_prices[0]
                if len(raw_prices) == 2 and raw_prices[1] > raw_prices[0] * 1.95:
                    # İki teklif arasında >%95 uçurum varsa yüksek olan stok dışı/çoklu paket aykırı değerdir
                    cleaned_q = [q for q in base_q if float(q["effective_pack_price"]) <= raw_prices[0] * 1.95]
                else:
                    cleaned_q = [
                        q for q in base_q
                        if (0.48 * anchor_p <= float(q["effective_pack_price"]) <= 1.85 * anchor_p)
                    ]
                if cleaned_q:
                    base_q = cleaned_q

            ordered = sorted(
                base_q,
                key=lambda q: (float(q["effective_pack_price"] or 0.0), float(q["effective_unit_price"] or 0.0)),
            )

            lead = ordered[0] if ordered else None
            worst = ordered[-1] if len(ordered) >= 2 else None

            best_pack = round(float(lead["effective_pack_price"]), 2) if lead else 0.0
            best_unit = round(float(lead["effective_unit_price"]), 2) if lead else 0.0
            best_net = float(lead["net_amount"]) if lead else ref_amt
            worst_pack = round(float(worst["effective_pack_price"]), 2) if worst else 0.0
            worst_unit = round(float(worst["effective_unit_price"]), 2) if worst else 0.0

            savings_pct = 0.0
            if lead and worst and worst_pack > best_pack > 0:
                savings_pct = round(((worst_pack - best_pack) / worst_pack) * 100.0, 1)

            # 30 günlük gerçek referans medyan hesabı (aykırı değerlerden arındırılmış)
            h_packs = [
                v for v in hist_pack_map.get(pk, [])
                if v > 0 and (best_pack <= 0 or (0.55 * best_pack <= v <= 1.85 * best_pack))
            ]
            h_units = [
                v for v in hist_unit_map.get(pk, [])
                if v > 0 and (best_unit <= 0 or (0.55 * best_unit <= v <= 1.85 * best_unit))
            ]
            rival_packs = [float(q["effective_pack_price"]) for q in ordered[1:] if float(q["effective_pack_price"]) > 0]
            rival_units = [float(q["effective_unit_price"]) for q in ordered[1:] if float(q["effective_unit_price"]) > 0]

            ref_med_pack = 0.0
            ref_med_unit = 0.0
            if len(h_packs) >= 2:
                ref_med_pack = round(statistics.median(h_packs), 2)
                ref_med_unit = round(statistics.median(h_units), 4) if h_units else 0.0
            elif len(ordered) >= 3 and rival_packs:
                ref_med_pack = round(statistics.median([float(q["effective_pack_price"]) for q in ordered]), 2)
                ref_med_unit = round(statistics.median([float(q["effective_unit_price"]) for q in ordered]), 4)
            elif len(ordered) == 2 and rival_packs:
                ref_med_pack = round(rival_packs[0], 2)
                ref_med_unit = round(rival_units[0], 4) if rival_units else 0.0

            drop_pct = 0.0
            is_real_drop = 0
            if best_pack > 5.0 and ref_med_pack > best_pack:
                cand_drop = round(((ref_med_pack - best_pack) / ref_med_pack) * 100.0, 1)
                unit_drop = (
                    ((ref_med_unit - best_unit) / ref_med_unit) * 100.0
                    if (ref_med_unit > best_unit > 0)
                    else cand_drop
                )
                # Shrinkflation ve paket-farkı kalkanı: gerçek perakende indirimi %10–%45 bandında olmalı
                if 10.0 <= cand_drop <= 45.0 and unit_drop >= 9.5 and len(ordered) >= 2:
                    drop_pct = cand_drop
                    is_real_drop = 1

            verdict = "GERCEK_DIP_FIYAT" if (is_real_drop or savings_pct >= 12.0) else "NORMAL_PIYASA_FIYATI"
            detective_score = min(98, int(74 + min(20.0, max(drop_pct, savings_pct * 0.6)))) if ordered else 50

            seen_plats = []
            top_quotes = []
            for tq in ordered:
                pl = str(tq["platform"] or "").strip().lower()
                if pl and pl not in seen_plats:
                    seen_plats.append(pl)
                    if len(top_quotes) < 4:
                        top_quotes.append(
                            {
                                "platform": pl,
                                "seller_name": tq["seller_name"],
                                "pack_price": round(float(tq["effective_pack_price"]), 2),
                                "unit_price": round(float(tq["effective_unit_price"]), 2),
                                "net_amount": float(tq["net_amount"]),
                                "url": tq["url"],
                            }
                        )

            platforms_csv = ("," + ",".join(seen_plats) + ",") if seen_plats else ""
            search_norm = self._norm_tr_idx(
                f"{r['brand']} {r['family_name']} {r['official_model_code'] or ''} {' '.join(bcs)}"
            )
            img_url = r["image_url"] if ("image_url" in r.keys() and r["image_url"]) else ""

            upsert_params.append(
                (
                    pk,
                    r["brand"],
                    r["family_name"],
                    r["category"],
                    r["base_unit"],
                    img_url,
                    search_norm,
                    json.dumps(bcs, ensure_ascii=False),
                    len(seen_plats) if seen_plats else len(q_list),
                    lead["platform"] if lead else "",
                    lead["seller_name"] if lead else "",
                    best_pack,
                    best_unit,
                    best_net,
                    lead["url"] if lead else "",
                    worst["platform"] if worst else "",
                    worst_pack,
                    worst_unit,
                    savings_pct,
                    ref_med_pack,
                    drop_pct,
                    is_real_drop,
                    platforms_csv,
                    json.dumps(top_quotes, ensure_ascii=False),
                    verdict,
                    detective_score,
                    now_iso,
                )
            )

        if not keys_list:
            self.conn.execute(
                """
                DELETE FROM product_catalog_index
                WHERE product_key NOT IN (SELECT product_key FROM canonical_products WHERE is_private_label = 0)
                """
            )

        if upsert_params:
            self.conn.executemany(
                """
                INSERT OR REPLACE INTO product_catalog_index
                (product_key, brand, family_name, category, base_unit, image_url, search_norm,
                 barcodes_json, offer_count, best_platform, best_seller_name, best_pack_price,
                 best_unit_price, best_net_amount, best_url, worst_platform, worst_pack_price,
                 worst_unit_price, savings_pct, median_30d_pack_price, drop_vs_median_pct,
                 is_real_drop, platforms_csv, top_quotes_json, verdict, detective_score, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                upsert_params,
            )
        self.conn.commit()
        return len(upsert_params)


