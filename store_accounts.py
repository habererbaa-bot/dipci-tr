"""
dipci.tr — Bağlı Market Hesapları, Teslimat Politikaları & Ortak Kasa (Unified Checkout) Zemini
- Müşterinin Migros Sanal Market, CarrefourSA Online, A101 Kapıda, Cepte ŞOK,
  Amazon.com.tr, Hepsiburada ve Trendyol hesap tanımlamalarını (şifre istemeden, güvenli şekilde),
  sadakat/kargo muafiyetlerini (Prime, Premium, Pass, Gel-Al) ve teslimat mahallesini saklar.
- Her mağaza için gerçekçi Türkiye online market minimum sepet tutarı ve getirme/kargo ücreti
  hesabını yürütür; ilerideki B2B API / OAuth Tek Ödeme entegrasyonunun veritabanı zeminini hazırlar.
"""

import json
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional


STORE_DELIVERY_POLICIES: Dict[str, Dict] = {
    "migros": {
        "platform": "migros",
        "name": "Migros Sanal Market",
        "short_name": "Migros",
        "has_online_delivery": True,
        "supports_click_collect": True,
        "channel_type": "ONLINE_SUPERMARKET",
        "channel_badge": "🛵 Online Eve Teslim / Gel-Al",
        "min_basket_tl": 400.0,
        "click_collect_min_tl": 100.0,
        "delivery_fee_tl": 44.90,
        "free_delivery_tl": 1000.0,
        "loyalty_label": "Migros Money Üyeliği",
        "pass_free_shipping": False,
        "login_url": "https://www.migros.com.tr/giris",
        "cart_url": "https://www.migros.com.tr/sepetim",
        "policy_note": "Adrese teslimde bölgeye göre ~400 TL alt sepet sınırı vardır; Mağazadan Tıkla Gel-Al (~100 TL+) ücretsizdir.",
    },
    "carrefoursa": {
        "platform": "carrefoursa",
        "name": "CarrefourSA Online",
        "short_name": "CarrefourSA",
        "has_online_delivery": True,
        "supports_click_collect": True,
        "channel_type": "ONLINE_SUPERMARKET",
        "channel_badge": "🛵 Online Eve Teslim",
        "min_basket_tl": 350.0,
        "click_collect_min_tl": 150.0,
        "delivery_fee_tl": 39.90,
        "free_delivery_tl": 1000.0,
        "loyalty_label": "CarrefourSA Kart",
        "pass_free_shipping": False,
        "login_url": "https://www.carrefoursa.com/login",
        "cart_url": "https://www.carrefoursa.com/cart",
        "policy_note": "Online sanal market siparişlerinde bölgeye göre ~350 TL alt limit ve getirme ücreti uygulanır.",
    },
    "a101": {
        "platform": "a101",
        "name": "A101 Kapıda",
        "short_name": "A101 Kapıda",
        "has_online_delivery": True,
        "supports_click_collect": False,
        "channel_type": "ONLINE_SUPERMARKET",
        "channel_badge": "🛵 Online Eve Teslim",
        "min_basket_tl": 400.0,
        "click_collect_min_tl": 400.0,
        "delivery_fee_tl": 39.90,
        "free_delivery_tl": 1250.0,
        "loyalty_label": "A101 Hadi Hesabı",
        "pass_free_shipping": False,
        "login_url": "https://www.a101.com.tr/kapida",
        "cart_url": "https://www.a101.com.tr/kapida/sepet",
        "policy_note": "A101 Kapıda'da ~400 TL alt limit vardır; 1.250 TL ve üzeri sepetlerde teslimat ücretsizdir.",
    },
    "sok": {
        "platform": "sok",
        "name": "Cepte ŞOK",
        "short_name": "Cepte ŞOK",
        "has_online_delivery": True,
        "supports_click_collect": True,
        "channel_type": "ONLINE_SUPERMARKET",
        "channel_badge": "🛵 Online Eve Teslim / Gel-Al",
        "min_basket_tl": 300.0,
        "click_collect_min_tl": 100.0,
        "delivery_fee_tl": 49.90,
        "free_delivery_tl": 1000.0,
        "loyalty_label": "Cepte ŞOK Yıldız",
        "pass_free_shipping": False,
        "login_url": "https://www.sokmarket.com.tr/giris",
        "cart_url": "https://www.sokmarket.com.tr/sepet",
        "policy_note": "Cepte ŞOK eve teslimatta ~50 TL getirme ücreti ve ~300 TL alt limit uygular; Gel-Al ücretsizdir.",
    },
    "bim": {
        "platform": "bim",
        "name": "BİM Mağaza",
        "short_name": "BİM",
        "has_online_delivery": False,
        "supports_click_collect": False,
        "channel_type": "PHYSICAL_ONLY",
        "channel_badge": "🏪 Sadece Şube (Online Yok)",
        "min_basket_tl": 0.0,
        "click_collect_min_tl": 0.0,
        "delivery_fee_tl": 0.0,
        "free_delivery_tl": 0.0,
        "loyalty_label": "BİM Para / Mağaza",
        "pass_free_shipping": False,
        "login_url": "https://www.bim.com.tr/",
        "cart_url": "https://www.google.com/maps/search/B%C4%B0M+Market",
        "policy_note": "BİM gıda ve market ürünlerinde eve online teslimat yapmaz; şube raf fiyatlarıdır.",
    },
    "amazon": {
        "platform": "amazon",
        "name": "Amazon.com.tr",
        "short_name": "Amazon",
        "has_online_delivery": True,
        "supports_click_collect": False,
        "channel_type": "ONLINE_MARKETPLACE",
        "channel_badge": "📦 Kargo ile Eve Teslim",
        "min_basket_tl": 0.0,
        "click_collect_min_tl": 0.0,
        "delivery_fee_tl": 49.90,
        "free_delivery_tl": 500.0,
        "loyalty_label": "Amazon Prime (Ücretsiz Kargo)",
        "pass_free_shipping": True,
        "login_url": "https://www.amazon.com.tr/",
        "cart_url": "https://www.amazon.com.tr/gp/cart/view.html",
        "policy_note": "500 TL üzeri siparişlerde veya Amazon Prime üyeliğinde kargo 0 TL'dir.",
    },
    "hepsiburada": {
        "platform": "hepsiburada",
        "name": "Hepsiburada",
        "short_name": "Hepsiburada",
        "has_online_delivery": True,
        "supports_click_collect": False,
        "channel_type": "ONLINE_MARKETPLACE",
        "channel_badge": "📦 Kargo ile Eve Teslim",
        "min_basket_tl": 0.0,
        "click_collect_min_tl": 0.0,
        "delivery_fee_tl": 49.90,
        "free_delivery_tl": 300.0,
        "loyalty_label": "Hepsiburada Premium (Ücretsiz Kargo)",
        "pass_free_shipping": True,
        "login_url": "https://www.hepsiburada.com/",
        "cart_url": "https://checkout.hepsiburada.com/sepetim",
        "policy_note": "300 TL üzeri veya Hepsiburada Premium üyeliğinde kargo ücretsizdir.",
    },
    "trendyol": {
        "platform": "trendyol",
        "name": "Trendyol",
        "short_name": "Trendyol",
        "has_online_delivery": True,
        "supports_click_collect": False,
        "channel_type": "ONLINE_MARKETPLACE",
        "channel_badge": "📦 Kargo ile Eve Teslim",
        "min_basket_tl": 0.0,
        "click_collect_min_tl": 0.0,
        "delivery_fee_tl": 44.90,
        "free_delivery_tl": 300.0,
        "loyalty_label": "Trendyol Pass (Ücretsiz Kargo)",
        "pass_free_shipping": True,
        "login_url": "https://www.trendyol.com/giris",
        "cart_url": "https://www.trendyol.com/sepet",
        "policy_note": "300 TL üzeri siparişlerde veya Trendyol Pass ile kargo ücretsizdir.",
    },
}


def compute_store_shipping_eval(
    platform: str,
    subtotal_tl: float,
    account_row: Optional[Dict] = None,
) -> Dict:
    """
    Bir marketteki alt sepet tutarına ve kullanıcının bağlı hesap/üyelik tercihine göre
    alt sepet uygunluğunu ve tahmini getirme/kargo ücretini hesaplar.
    """
    p_norm = "carrefoursa" if platform == "carrefour" else platform
    pol = STORE_DELIVERY_POLICIES.get(p_norm) or {
        "platform": platform,
        "name": platform.upper(),
        "short_name": platform.upper(),
        "has_online_delivery": True,
        "supports_click_collect": False,
        "channel_type": "ONLINE_MARKETPLACE",
        "channel_badge": "📦 Online Sipariş",
        "min_basket_tl": 0.0,
        "click_collect_min_tl": 0.0,
        "delivery_fee_tl": 39.90,
        "free_delivery_tl": 500.0,
        "loyalty_label": "",
        "pass_free_shipping": False,
        "policy_note": "",
    }

    acc = account_row or {}
    is_linked = bool(acc.get("account_identifier"))
    fulfillment = str(acc.get("preferred_fulfillment") or "HOME_DELIVERY")
    has_pass = bool(acc.get("loyalty_or_pass_active"))

    if not pol["has_online_delivery"]:
        return {
            "has_online_delivery": False,
            "channel_badge": pol["channel_badge"],
            "fulfillment_mode": "IN_STORE",
            "min_basket_tl": 0.0,
            "meets_min_basket": True,
            "min_shortfall_tl": 0.0,
            "delivery_fee_tl": 0.0,
            "free_delivery_tl": 0.0,
            "free_shortfall_tl": 0.0,
            "account_linked": is_linked,
            "delivery_summary": "🏪 Sadece mağaza şubesinden alınır (eve teslimat yok)",
        }

    if fulfillment == "CLICK_COLLECT" and pol.get("supports_click_collect"):
        min_req = float(pol.get("click_collect_min_tl") or 100.0)
        meets = subtotal_tl >= (min_req - 0.01)
        shortfall = round(max(0.0, min_req - subtotal_tl), 2)
        return {
            "has_online_delivery": True,
            "channel_badge": "🛍️ Mağazadan Tıkla Gel-Al",
            "fulfillment_mode": "CLICK_COLLECT",
            "min_basket_tl": min_req,
            "meets_min_basket": meets,
            "min_shortfall_tl": shortfall,
            "delivery_fee_tl": 0.0,
            "free_delivery_tl": 0.0,
            "free_shortfall_tl": 0.0,
            "account_linked": is_linked,
            "delivery_summary": (
                "🛍️ Tıkla Gel-Al: Teslimat 0 TL"
                if meets
                else f"⚠️ Gel-Al alt sınırı {min_req:g} TL ({shortfall:g} TL eksik)"
            ),
        }

    min_req = float(pol.get("min_basket_tl") or 0.0)
    meets = subtotal_tl >= (min_req - 0.01)
    shortfall = round(max(0.0, min_req - subtotal_tl), 2)

    free_thresh = float(pol.get("free_delivery_tl") or 0.0)
    base_fee = float(pol.get("delivery_fee_tl") or 0.0)

    if subtotal_tl <= 0:
        fee = 0.0
    elif has_pass and pol.get("pass_free_shipping"):
        fee = 0.0
    elif free_thresh > 0 and subtotal_tl >= (free_thresh - 0.01):
        fee = 0.0
    else:
        fee = base_fee

    free_shortfall = round(max(0.0, free_thresh - subtotal_tl), 2) if (fee > 0 and free_thresh > 0) else 0.0

    if not meets:
        summary = f"⚠️ Eve teslim alt sınırı ~{min_req:g} TL ({shortfall:,.2f} TL daha eklenmeli)".replace(",", "_").replace(".", ",").replace("_", ".")
    elif fee == 0.0:
        if has_pass and pol.get("pass_free_shipping"):
            summary = f"✓ {pol['loyalty_label'].split('(')[0].strip()} ile Kargo 0 TL"
        else:
            summary = "✓ Ücretsiz Teslimat / Kargo (0 TL)"
    else:
        fee_txt = f"{fee:.2f}".replace(".", ",")
        summary = f"+{fee_txt} TL getirme/kargo ücreti"
        if free_shortfall > 0:
            fs_txt = f"{free_shortfall:.0f}"
            summary += f" ({fs_txt} TL daha eklenirse ücretsiz)"

    return {
        "has_online_delivery": True,
        "channel_badge": pol["channel_badge"],
        "fulfillment_mode": "HOME_DELIVERY",
        "min_basket_tl": min_req,
        "meets_min_basket": meets,
        "min_shortfall_tl": shortfall,
        "delivery_fee_tl": round(fee, 2),
        "free_delivery_tl": free_thresh,
        "free_shortfall_tl": free_shortfall,
        "account_linked": is_linked,
        "delivery_summary": summary,
    }


class StoreAccountService:
    """
    Adım 3 (Ortak Kasa & Otomatik Sepet Doldurma) için müşteri market hesapları,
    teslimat mahallesi ve dağıtık sipariş taslağı (order intent) yönetimi.
    """

    LINKABLE_STORES = ["migros", "carrefoursa", "a101", "sok", "hepsiburada", "amazon", "trendyol"]

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._init_tables()

    def _init_tables(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS user_delivery_profiles (
                profile_key TEXT PRIMARY KEY,
                member_id INTEGER,
                city TEXT DEFAULT 'İstanbul',
                district TEXT DEFAULT '',
                neighborhood TEXT DEFAULT '',
                address_title TEXT DEFAULT 'Ev Adresim',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_store_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_key TEXT NOT NULL,
                platform TEXT NOT NULL,
                account_identifier TEXT NOT NULL,
                loyalty_or_pass_active INTEGER DEFAULT 0,
                preferred_fulfillment TEXT DEFAULT 'HOME_DELIVERY',
                connection_mode TEXT DEFAULT 'ACCOUNT_PRELINKED',
                oauth_token_encrypted TEXT DEFAULT '',
                partner_customer_ref TEXT DEFAULT '',
                consent_auto_cart INTEGER DEFAULT 1,
                linked_at TEXT NOT NULL,
                UNIQUE(profile_key, platform)
            );

            CREATE TABLE IF NOT EXISTS unified_order_intents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_key TEXT NOT NULL,
                basket_json TEXT NOT NULL,
                store_split_json TEXT NOT NULL,
                raw_items_total REAL NOT NULL,
                delivery_fees_total REAL NOT NULL,
                grand_total REAL NOT NULL,
                status TEXT DEFAULT 'CART_HANDOFF_READY',
                created_at TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def resolve_profile_key(self, member: Optional[Dict], client_profile_id: str = "") -> str:
        clean_cid = "".join(ch for ch in (client_profile_id or "") if ch.isalnum() or ch in ("-", "_"))[:64]
        if member and member.get("member_id"):
            mkey = f"member:{member['member_id']}"
            # Cihazda daha önce tanımlanmış market hesapları varsa üye profiline otomatik aktar
            if clean_cid:
                dkey = f"device:{clean_cid}"
                self._merge_device_into_member(dkey, mkey, int(member["member_id"]))
            return mkey
        if clean_cid:
            return f"device:{clean_cid}"
        return "device:anonymous"

    def _merge_device_into_member(self, device_key: str, member_key: str, member_id: int) -> None:
        dev_prof = self.conn.execute(
            "SELECT * FROM user_delivery_profiles WHERE profile_key = ?", (device_key,)
        ).fetchone()
        mem_prof = self.conn.execute(
            "SELECT * FROM user_delivery_profiles WHERE profile_key = ?", (member_key,)
        ).fetchone()
        if dev_prof and not mem_prof:
            self.conn.execute(
                """
                INSERT INTO user_delivery_profiles (profile_key, member_id, city, district, neighborhood, address_title, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    member_key,
                    member_id,
                    dev_prof["city"],
                    dev_prof["district"],
                    dev_prof["neighborhood"],
                    dev_prof["address_title"],
                    datetime.now().isoformat(),
                ),
            )
        dev_accs = self.conn.execute(
            "SELECT * FROM user_store_accounts WHERE profile_key = ?", (device_key,)
        ).fetchall()
        for da in dev_accs:
            self.conn.execute(
                """
                INSERT INTO user_store_accounts (
                    profile_key, platform, account_identifier, loyalty_or_pass_active,
                    preferred_fulfillment, connection_mode, consent_auto_cart, linked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_key, platform) DO NOTHING
                """,
                (
                    member_key,
                    da["platform"],
                    da["account_identifier"],
                    da["loyalty_or_pass_active"],
                    da["preferred_fulfillment"],
                    da["connection_mode"],
                    da["consent_auto_cart"],
                    da["linked_at"],
                ),
            )
        if dev_prof or dev_accs:
            self.conn.commit()

    def get_accounts_map(self, profile_key: str) -> Dict[str, Dict]:
        if not profile_key or profile_key == "device:anonymous":
            return {}
        rows = self.conn.execute(
            "SELECT * FROM user_store_accounts WHERE profile_key = ?", (profile_key,)
        ).fetchall()
        return {r["platform"]: dict(r) for r in rows}

    def get_full_bundle(self, profile_key: str) -> Dict:
        prof_row = self.conn.execute(
            "SELECT * FROM user_delivery_profiles WHERE profile_key = ?", (profile_key,)
        ).fetchone()
        delivery_profile = (
            {
                "city": prof_row["city"],
                "district": prof_row["district"],
                "neighborhood": prof_row["neighborhood"],
                "address_title": prof_row["address_title"],
            }
            if prof_row
            else {
                "city": "İstanbul",
                "district": "",
                "neighborhood": "",
                "address_title": "Ev Adresim",
            }
        )
        acc_map = self.get_accounts_map(profile_key)

        stores_out: List[Dict] = []
        for plat in self.LINKABLE_STORES:
            pol = STORE_DELIVERY_POLICIES[plat]
            linked = acc_map.get(plat)
            stores_out.append(
                {
                    "platform": plat,
                    "name": pol["name"],
                    "short_name": pol["short_name"],
                    "channel_badge": pol["channel_badge"],
                    "supports_click_collect": pol["supports_click_collect"],
                    "loyalty_label": pol["loyalty_label"],
                    "min_basket_tl": pol["min_basket_tl"],
                    "delivery_fee_tl": pol["delivery_fee_tl"],
                    "free_delivery_tl": pol["free_delivery_tl"],
                    "policy_note": pol["policy_note"],
                    "login_url": pol["login_url"],
                    "cart_url": pol["cart_url"],
                    "is_linked": bool(linked and linked.get("account_identifier")),
                    "account_identifier": linked["account_identifier"] if linked else "",
                    "loyalty_or_pass_active": bool(linked["loyalty_or_pass_active"]) if linked else False,
                    "preferred_fulfillment": linked["preferred_fulfillment"] if linked else "HOME_DELIVERY",
                    "connection_mode": linked["connection_mode"] if linked else "NOT_LINKED",
                }
            )

        linked_count = sum(1 for s in stores_out if s["is_linked"])
        return {
            "ok": True,
            "profile_key": profile_key,
            "delivery_profile": delivery_profile,
            "linked_count": linked_count,
            "stores": stores_out,
        }

    def save_delivery_profile(
        self,
        profile_key: str,
        city: str,
        district: str,
        neighborhood: str,
        address_title: str = "Ev Adresim",
        member_id: Optional[int] = None,
    ) -> Dict:
        now = datetime.now().isoformat()
        self.conn.execute(
            """
            INSERT INTO user_delivery_profiles (profile_key, member_id, city, district, neighborhood, address_title, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile_key) DO UPDATE SET
                member_id = COALESCE(excluded.member_id, user_delivery_profiles.member_id),
                city = excluded.city,
                district = excluded.district,
                neighborhood = excluded.neighborhood,
                address_title = excluded.address_title,
                updated_at = excluded.updated_at
            """,
            (
                profile_key,
                member_id,
                (city or "İstanbul").strip()[:60],
                (district or "").strip()[:60],
                (neighborhood or "").strip()[:80],
                (address_title or "Ev Adresim").strip()[:60],
                now,
            ),
        )
        self.conn.commit()
        return self.get_full_bundle(profile_key)

    def upsert_store_account(
        self,
        profile_key: str,
        platform: str,
        account_identifier: str,
        loyalty_or_pass_active: bool = False,
        preferred_fulfillment: str = "HOME_DELIVERY",
    ) -> Dict:
        plat = (platform or "").strip().lower()
        if plat == "carrefour":
            plat = "carrefoursa"
        if plat not in STORE_DELIVERY_POLICIES:
            return {"ok": False, "error": "Geçersiz market platformu."}

        clean_ident = (account_identifier or "").strip()[:120]
        if not clean_ident:
            self.conn.execute(
                "DELETE FROM user_store_accounts WHERE profile_key = ? AND platform = ?",
                (profile_key, plat),
            )
            self.conn.commit()
            return self.get_full_bundle(profile_key)

        fulf = "CLICK_COLLECT" if preferred_fulfillment == "CLICK_COLLECT" else "HOME_DELIVERY"
        now = datetime.now().isoformat()
        self.conn.execute(
            """
            INSERT INTO user_store_accounts (
                profile_key, platform, account_identifier, loyalty_or_pass_active,
                preferred_fulfillment, connection_mode, consent_auto_cart, linked_at
            ) VALUES (?, ?, ?, ?, ?, 'ACCOUNT_PRELINKED', 1, ?)
            ON CONFLICT(profile_key, platform) DO UPDATE SET
                account_identifier = excluded.account_identifier,
                loyalty_or_pass_active = excluded.loyalty_or_pass_active,
                preferred_fulfillment = excluded.preferred_fulfillment,
                linked_at = excluded.linked_at
            """,
            (
                profile_key,
                plat,
                clean_ident,
                1 if loyalty_or_pass_active else 0,
                fulf,
                now,
            ),
        )
        self.conn.commit()
        return self.get_full_bundle(profile_key)

    def record_order_intent(
        self,
        profile_key: str,
        basket_items: List[Dict],
        store_split: List[Dict],
        raw_items_total: float,
        delivery_fees_total: float,
    ) -> Dict:
        now = datetime.now().isoformat()
        grand = round(raw_items_total + delivery_fees_total, 2)
        cur = self.conn.execute(
            """
            INSERT INTO unified_order_intents (
                profile_key, basket_json, store_split_json,
                raw_items_total, delivery_fees_total, grand_total, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'CART_HANDOFF_READY', ?)
            """,
            (
                profile_key,
                json.dumps(basket_items, ensure_ascii=False),
                json.dumps(store_split, ensure_ascii=False),
                round(raw_items_total, 2),
                round(delivery_fees_total, 2),
                grand,
                now,
            ),
        )
        self.conn.commit()
        return {
            "ok": True,
            "intent_id": cur.lastrowid,
            "grand_total": grand,
            "status": "CART_HANDOFF_READY",
        }
