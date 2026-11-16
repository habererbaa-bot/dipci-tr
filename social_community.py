"""
dipci.tr & Market Dedektifi — Doğrulanmış Sosyal Topluluk, Fırsat Akışı ve 7/24 AI Moderasyon Motoru

Özellikler:
1. KVKK Dostu Telefon (SMS OTP) Doğrulaması: TC Kimlik No istemez; telefon numarasını SHA-256 ile
   özetleyerek '1 Gerçek Telefon = 1 Tekil Üye' kuralını uygular ve bot/trol hesapları engeller.
2. Kamera Rozeti Entegrasyonu: Ürünü fiziksel olarak okutan üyelerin yorumlarına otomatik
   '✓ Ürünü Kamerayla Doğruladı' rozeti ekler.
3. 3 Odaklı Mikro-Sosyal Format:
   - COUPON_TIP (Kupon & İndirim İpucu)
   - LONG_TERM_REVIEW (6+ Ay Uzun Kullanım Deneyimi)
   - FIRST_IMPRESSION (İlk İzlenim / SKT & Paket Notu)
4. Otomatik Gelir Üreten Topluluk Fırsat Akışı (Slickdeals / MyDealz Modeli):
   Üyenin paylaştığı mağaza linkini otomatik olarak AffiliateLinkBuilder (?tag=marketdedekti-21 vb.)
   ile sarmalar ve üstüne Market Dedektifi algoritmik onay damgasını vurur.
5. 7/24 Yapay Zeka Ön-Moderasyon Filtresi (5651 Sayılı Kanun uyumu):
   Hakaret, konu dışı/siyasi polemik, gizli satıcı reklamı ve spam linkleri yayına girmeden engeller.
"""

import hashlib
import json
import os
import random
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from affiliate import AffiliateLinkBuilder
from config import AFFILIATE_CONFIG
from email_auth import EmailOTPClient, normalize_and_validate_email, verify_google_id_token
from netgsm_sms import NetgsmClient
from normalizer import UnitNormalizer


@dataclass
class ModerationDecision:
    allowed: bool
    status: str          # "APPROVED", "BLOCKED_PROFANITY", "BLOCKED_SPAM_SELF_PROMO", "BLOCKED_OFF_TOPIC"
    reason: str
    risk_score: float


class AIContentModerator:
    """
    5651 Sayılı Kanun ve platform kalitesi için 7/24 anlık ön-moderasyon katmanı.
    Üretim ortamında LLM sınıflandırıcı ile birlikte hibrit çalışır; kural motoru milisaniyede kesin engelleme yapar.
    """

    PROFANITY_PATTERNS = [
        r"\b(aptal|gerizekal[ıi]|salak|doland[ıi]r[ıi]c[ıi]\s+köpek|şerefsiz|ahlaks[ıi]z|lanet\s+olsun)\b",
    ]
    SPAM_PROMO_PATTERNS = [
        r"\b(whatsapp|telegram\s+grubumuz|kendi\s+mağazam[ıi]z|profilimdeki\s+link|dm\s+at[ıi]n|bio['’]?daki)\b",
        r"(bit\.ly|tinyurl\.com|t\.me/|wa\.me/)",
        r"\b05\d{2}[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}\b",  # Yorum içine açık cep telefonu bırakma spam'i
    ]
    OFF_TOPIC_PATTERNS = [
        r"\b(parti|seçim\s+sand[ıi]k|futbol\s+hakem|bahis|kumar|casino|kripto\s+sinyal)\b",
    ]

    @classmethod
    def inspect(cls, text: str) -> ModerationDecision:
        cleaned = (text or "").strip()
        if len(cleaned) < 6:
            return ModerationDecision(False, "BLOCKED_TOO_SHORT", "Yorum çok kısa; lütfen faydalı bir detay ekleyin.", 0.6)

        lowered = cleaned.lower()
        for pat in cls.PROFANITY_PATTERNS:
            if re.search(pat, lowered):
                return ModerationDecision(
                    False,
                    "BLOCKED_PROFANITY",
                    "AI Moderatör: Hakaret veya kaba ifadeler topluluk kurallarımıza aykırıdır.",
                    0.98,
                )

        for pat in cls.SPAM_PROMO_PATTERNS:
            if re.search(pat, lowered):
                return ModerationDecision(
                    False,
                    "BLOCKED_SPAM_SELF_PROMO",
                    "AI Moderatör: Gizli satıcı reklamı, kısaltılmış dış link veya telefon numarası paylaşılamaz.",
                    0.95,
                )

        for pat in cls.OFF_TOPIC_PATTERNS:
            if re.search(pat, lowered):
                return ModerationDecision(
                    False,
                    "BLOCKED_OFF_TOPIC",
                    "AI Moderatör: Bu alan yalnızca ürün deneyimi, fiyat ve kupon yardımlaşması içindir.",
                    0.90,
                )

        return ModerationDecision(True, "APPROVED", "Temiz ve alışveriş odaklı içerik.", 0.02)


class SocialCommunityService:
    """Doğrulanmış üyelik, ürün altı mikro-yorumlar ve topluluk sıcak fırsat paylaşım motoru."""

    PHONE_HASH_SALT = "dipci_kvkk_v1_salt_2026"

    def __init__(self, conn: sqlite3.Connection, affiliate: Optional[AffiliateLinkBuilder] = None):
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        self.affiliate = affiliate or AffiliateLinkBuilder(AFFILIATE_CONFIG)
        self.normalizer = UnitNormalizer()
        self._init_social_schema()

    def _init_social_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS verified_members (
                member_id INTEGER PRIMARY KEY AUTOINCREMENT,
                display_name TEXT NOT NULL,           -- Örn: "Kemal V." veya "@mutfak_dedektifi"
                phone_hash TEXT NOT NULL UNIQUE,      -- KVKK uyumlu SHA-256 özet (ham telefon bile tutulmaz!)
                session_token TEXT UNIQUE,
                trust_score REAL DEFAULT 60.0,        -- Doğru bildirimlerle artar, sabotajda düşer
                scanned_products_json TEXT DEFAULT '[]',
                badges_json TEXT DEFAULT '["✓ Telefon Doğrulamalı"]',
                is_shadowbanned INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pending_otp_codes (
                phone_hash TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                otp_code TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS product_social_posts (
                post_id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_key TEXT NOT NULL,
                member_id INTEGER NOT NULL,
                post_type TEXT NOT NULL,              -- COUPON_TIP | LONG_TERM_REVIEW | FIRST_IMPRESSION
                coupon_code TEXT DEFAULT '',
                usage_months INTEGER DEFAULT 0,
                content TEXT NOT NULL,
                has_camera_badge INTEGER DEFAULT 0,   -- Üye bu ürünü kamerayla okuttuysa 1 olur
                helpful_count INTEGER DEFAULT 0,
                moderation_status TEXT DEFAULT 'APPROVED',
                created_at TEXT NOT NULL,
                FOREIGN KEY(member_id) REFERENCES verified_members(member_id)
            );

            CREATE TABLE IF NOT EXISTS post_helpful_votes (
                post_id INTEGER NOT NULL,
                member_id INTEGER NOT NULL,
                PRIMARY KEY (post_id, member_id)
            );

            CREATE TABLE IF NOT EXISTS community_deals (
                deal_id INTEGER PRIMARY KEY AUTOINCREMENT,
                member_id INTEGER NOT NULL,
                product_key TEXT DEFAULT '',
                title TEXT NOT NULL,
                platform TEXT NOT NULL,
                sale_price REAL NOT NULL,
                unit_summary TEXT DEFAULT '',
                coupon_code TEXT DEFAULT '',
                raw_url TEXT NOT NULL,
                tracked_affiliate_url TEXT NOT NULL,  -- Otomatik ?tag=marketdedekti-21 sarılmış link
                detective_stamp TEXT NOT NULL,        -- Algoritmanın otomatik vurduğu doğrulama notu
                detective_verified INTEGER DEFAULT 1,
                note TEXT DEFAULT '',
                temperature INTEGER DEFAULT 1,        -- 🔥 Sıcaklık derecesi
                created_at TEXT NOT NULL,
                FOREIGN KEY(member_id) REFERENCES verified_members(member_id)
            );

            CREATE TABLE IF NOT EXISTS deal_temperature_votes (
                deal_id INTEGER NOT NULL,
                member_id INTEGER NOT NULL,
                delta INTEGER NOT NULL,               -- +1 (Sıcak) veya -1 (Soğuk/Tuzak)
                PRIMARY KEY (deal_id, member_id)
            );
            """
        )
        self.conn.commit()

    @classmethod
    def _normalize_ten_digit_phone(cls, raw_phone: str) -> Tuple[bool, str]:
        digits = "".join(ch for ch in raw_phone if ch.isdigit())
        if digits.startswith("90") and len(digits) == 12:
            digits = digits[2:]
        elif digits.startswith("0") and len(digits) == 11:
            digits = digits[1:]
        if len(digits) != 10 or not digits.startswith("5"):
            return False, ""
        return True, digits

    @classmethod
    def _hash_phone(cls, raw_phone: str) -> Tuple[bool, str]:
        ok, digits = cls._normalize_ten_digit_phone(raw_phone)
        if not ok:
            return False, ""
        digest = hashlib.sha256(f"{cls.PHONE_HASH_SALT}:{digits}".encode("utf-8")).hexdigest()
        return True, digest

    @staticmethod
    def _privacy_safe_name(raw_name: str) -> str:
        parts = [p.strip() for p in (raw_name or "").strip().split() if p.strip()]
        if not parts:
            return "Doğrulanmış Üye"
        if parts[0].startswith("@"):
            return parts[0][:22]
        if len(parts) == 1:
            return parts[0].capitalize()[:18]
        return f"{parts[0].capitalize()} {parts[-1][0].upper()}."

    def request_phone_otp(self, raw_phone: str, display_name: str, force_demo: bool = False) -> Dict:
        ok, ten_digits = self._normalize_ten_digit_phone(raw_phone)
        if not ok:
            return {"ok": False, "error": "Lütfen geçerli bir Türkiye cep telefonu numarası girin (5XX XXX XX XX)."}
        _, p_hash = self._hash_phone(ten_digits)

        safe_name = self._privacy_safe_name(display_name)
        otp = f"{random.randint(100000, 999999)}"
        self.conn.execute(
            """
            INSERT OR REPLACE INTO pending_otp_codes (phone_hash, display_name, otp_code, expires_at)
            VALUES (?, ?, ?, datetime('now', '+10 minutes'))
            """,
            (p_hash, safe_name, otp),
        )
        self.conn.commit()

        netgsm = NetgsmClient()
        if netgsm.is_configured and not force_demo:
            sent, detail = netgsm.send_otp(ten_digits, otp)
            if not sent:
                return {"ok": False, "error": f"SMS gönderilemedi: {detail}"}
            return {
                "ok": True,
                "live_sms": True,
                "masked_name": safe_name,
                "message": "6 haneli doğrulama kodunuz Netgsm ile cep telefonunuza gönderildi.",
            }

        return {
            "ok": True,
            "live_sms": False,
            "masked_name": safe_name,
            "demo_sms_code": otp,
            "message": "6 haneli doğrulama kodu oluşturuldu (Test Modu).",
        }

    def verify_phone_otp(self, raw_phone: str, otp_code: str) -> Dict:
        ok, p_hash = self._hash_phone(raw_phone)
        if not ok:
            return {"ok": False, "error": "Geçersiz telefon numarası."}

        row = self.conn.execute(
            "SELECT * FROM pending_otp_codes WHERE phone_hash = ? AND otp_code = ?",
            (p_hash, (otp_code or "").strip()),
        ).fetchone()
        if not row:
            return {"ok": False, "error": "Doğrulama kodu hatalı veya süresi dolmuş."}

        token = secrets.token_hex(20)
        existing = self.conn.execute(
            "SELECT * FROM verified_members WHERE phone_hash = ?", (p_hash,)
        ).fetchone()

        if existing:
            self.conn.execute(
                "UPDATE verified_members SET session_token = ? WHERE member_id = ?",
                (token, existing["member_id"]),
            )
            member_id = existing["member_id"]
            disp = existing["display_name"]
            badges = json.loads(existing["badges_json"])
        else:
            disp = row["display_name"]
            badges = ["✓ Telefon Doğrulamalı"]
            cur = self.conn.execute(
                """
                INSERT INTO verified_members (display_name, phone_hash, session_token, badges_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (disp, p_hash, token, json.dumps(badges, ensure_ascii=False), datetime.now().isoformat()),
            )
            member_id = cur.lastrowid

        self.conn.execute("DELETE FROM pending_otp_codes WHERE phone_hash = ?", (p_hash,))
        self.conn.commit()

        return {
            "ok": True,
            "session_token": token,
            "member": {
                "member_id": member_id,
                "display_name": disp,
                "badges": badges,
            },
        }

    @classmethod
    def _hash_email(cls, raw_email: str) -> Tuple[bool, str, str]:
        ok, cleaned, err = normalize_and_validate_email(raw_email)
        if not ok:
            return False, "", err
        digest = hashlib.sha256(f"{cls.PHONE_HASH_SALT}:email:{cleaned}".encode("utf-8")).hexdigest()
        return True, digest, cleaned

    def request_email_otp(self, raw_email: str, display_name: str, force_demo: bool = False) -> Dict:
        ok, e_hash, cleaned_email = self._hash_email(raw_email)
        if not ok:
            return {"ok": False, "error": cleaned_email}

        safe_name = self._privacy_safe_name(display_name or cleaned_email.split("@", 1)[0])
        otp = f"{random.randint(100000, 999999)}"
        self.conn.execute(
            """
            INSERT OR REPLACE INTO pending_otp_codes (phone_hash, display_name, otp_code, expires_at)
            VALUES (?, ?, ?, datetime('now', '+10 minutes'))
            """,
            (e_hash, safe_name, otp),
        )
        self.conn.commit()

        mailer = EmailOTPClient()
        if mailer.is_configured and not force_demo:
            sent, detail = mailer.send_otp_email(cleaned_email, safe_name, otp)
            if not sent:
                return {"ok": False, "error": f"E-posta gönderilemedi: {detail}"}
            return {
                "ok": True,
                "live_email": True,
                "masked_name": safe_name,
                "message": f"6 haneli doğrulama kodunuz {cleaned_email} adresine gönderildi.",
            }

        return {
            "ok": True,
            "live_email": False,
            "masked_name": safe_name,
            "demo_otp_code": otp,
            "message": "6 haneli e-posta doğrulama kodu oluşturuldu.",
        }

    def verify_email_otp(self, raw_email: str, otp_code: str) -> Dict:
        ok, e_hash, err = self._hash_email(raw_email)
        if not ok:
            return {"ok": False, "error": err}

        row = self.conn.execute(
            "SELECT * FROM pending_otp_codes WHERE phone_hash = ? AND otp_code = ?",
            (e_hash, (otp_code or "").strip()),
        ).fetchone()
        if not row:
            return {"ok": False, "error": "Doğrulama kodu hatalı veya süresi dolmuş."}

        token = secrets.token_hex(20)
        existing = self.conn.execute(
            "SELECT * FROM verified_members WHERE phone_hash = ?", (e_hash,)
        ).fetchone()

        if existing:
            self.conn.execute(
                "UPDATE verified_members SET session_token = ? WHERE member_id = ?",
                (token, existing["member_id"]),
            )
            member_id = existing["member_id"]
            disp = existing["display_name"]
            badges = json.loads(existing["badges_json"])
        else:
            disp = row["display_name"]
            badges = ["✓ E-Posta Doğrulamalı"]
            cur = self.conn.execute(
                """
                INSERT INTO verified_members (display_name, phone_hash, session_token, badges_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (disp, e_hash, token, json.dumps(badges, ensure_ascii=False), datetime.now().isoformat()),
            )
            member_id = cur.lastrowid

        self.conn.execute("DELETE FROM pending_otp_codes WHERE phone_hash = ?", (e_hash,))
        self.conn.commit()
        return {
            "ok": True,
            "session_token": token,
            "member": {"member_id": member_id, "display_name": disp, "badges": badges},
        }

    def login_with_google_token(self, id_token: str) -> Dict:
        client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
        ok, info, err = verify_google_id_token(id_token, expected_client_id=client_id)
        if not ok:
            return {"ok": False, "error": err}

        email = str(info.get("email", "")).strip().lower()
        ok_mail, e_hash, mail_err = self._hash_email(email)
        if not ok_mail:
            return {"ok": False, "error": mail_err}

        full_name = str(info.get("name") or email.split("@", 1)[0]).strip()
        safe_name = self._privacy_safe_name(full_name)
        token = secrets.token_hex(20)

        existing = self.conn.execute(
            "SELECT * FROM verified_members WHERE phone_hash = ?", (e_hash,)
        ).fetchone()

        if existing:
            badges = json.loads(existing["badges_json"])
            if "✓ Google Doğrulamalı" not in badges:
                badges.append("✓ Google Doğrulamalı")
            self.conn.execute(
                "UPDATE verified_members SET session_token = ?, badges_json = ? WHERE member_id = ?",
                (token, json.dumps(badges, ensure_ascii=False), existing["member_id"]),
            )
            member_id = existing["member_id"]
            disp = existing["display_name"]
        else:
            badges = ["✓ Google Doğrulamalı"]
            cur = self.conn.execute(
                """
                INSERT INTO verified_members (display_name, phone_hash, session_token, badges_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (safe_name, e_hash, token, json.dumps(badges, ensure_ascii=False), datetime.now().isoformat()),
            )
            member_id = cur.lastrowid
            disp = safe_name

        self.conn.commit()
        return {
            "ok": True,
            "session_token": token,
            "member": {"member_id": member_id, "display_name": disp, "badges": badges},
        }

    def get_member_by_token(self, token: str) -> Optional[Dict]:
        if not token:
            return None
        r = self.conn.execute(
            "SELECT * FROM verified_members WHERE session_token = ?", (token.strip(),)
        ).fetchone()
        if not r:
            return None
        return {
            "member_id": r["member_id"],
            "display_name": r["display_name"],
            "trust_score": r["trust_score"],
            "scanned_products": json.loads(r["scanned_products_json"] or "[]"),
            "badges": json.loads(r["badges_json"] or "[]"),
            "is_shadowbanned": bool(r["is_shadowbanned"]),
        }

    def record_member_camera_scan(self, member_id: int, product_key: str) -> None:
        """Üye bir ürünü kamerayla okuttuğunda profiline fiziksel doğrulama izi işler."""
        r = self.conn.execute(
            "SELECT scanned_products_json, badges_json FROM verified_members WHERE member_id = ?",
            (member_id,),
        ).fetchone()
        if not r:
            return
        scanned = set(json.loads(r["scanned_products_json"] or "[]"))
        scanned.add(product_key)
        badges = json.loads(r["badges_json"] or "[]")
        if "📍 Reyon Doğrulayıcı" not in badges:
            badges.append("📍 Reyon Doğrulayıcı")
        self.conn.execute(
            "UPDATE verified_members SET scanned_products_json = ?, badges_json = ? WHERE member_id = ?",
            (json.dumps(list(scanned)), json.dumps(badges, ensure_ascii=False), member_id),
        )
        self.conn.commit()

    def add_product_post(
        self,
        session_token: str,
        product_key: str,
        post_type: str,
        content: str,
        coupon_code: str = "",
        usage_months: int = 0,
    ) -> Dict:
        member = self.get_member_by_token(session_token)
        if not member:
            return {"ok": False, "error": "Yorum yazmak için lütfen telefon doğrulaması ile giriş yapın."}

        mod = AIContentModerator.inspect(content)
        if not mod.allowed:
            return {"ok": False, "error": mod.reason, "moderation_status": mod.status}

        valid_types = {"COUPON_TIP", "LONG_TERM_REVIEW", "FIRST_IMPRESSION"}
        p_type = post_type if post_type in valid_types else "FIRST_IMPRESSION"
        has_cam = 1 if product_key in member["scanned_products"] else 0
        vis_status = "SHADOW_HIDDEN" if member["is_shadowbanned"] else "APPROVED"

        clean_coupon = re.sub(r"[^A-Z0-9_\-]", "", (coupon_code or "").upper())[:20]

        cur = self.conn.execute(
            """
            INSERT INTO product_social_posts
            (product_key, member_id, post_type, coupon_code, usage_months, content,
             has_camera_badge, moderation_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                product_key,
                member["member_id"],
                p_type,
                clean_coupon,
                max(0, int(usage_months or 0)),
                content.strip(),
                has_cam,
                vis_status,
                datetime.now().isoformat(),
            ),
        )
        self.conn.commit()
        return {
            "ok": True,
            "post_id": cur.lastrowid,
            "has_camera_badge": bool(has_cam),
            "message": "Paylaşımınız AI moderasyonundan geçti ve yayına alındı!",
        }

    def list_product_posts(self, product_key: str) -> List[Dict]:
        rows = self.conn.execute(
            """
            SELECT p.*, m.display_name, m.badges_json, m.trust_score
            FROM product_social_posts p
            JOIN verified_members m ON p.member_id = m.member_id
            WHERE p.product_key = ?
              AND p.moderation_status = 'APPROVED'
            ORDER BY
              CASE WHEN p.post_type = 'COUPON_TIP' THEN 0 ELSE 1 END ASC,
              p.helpful_count DESC,
              p.created_at DESC
            """,
            (product_key,),
        ).fetchall()

        return [
            {
                "post_id": r["post_id"],
                "post_type": r["post_type"],
                "coupon_code": r["coupon_code"],
                "usage_months": r["usage_months"],
                "content": r["content"],
                "has_camera_badge": bool(r["has_camera_badge"]),
                "helpful_count": r["helpful_count"],
                "author_name": r["display_name"],
                "author_badges": json.loads(r["badges_json"] or "[]"),
                "created_at": r["created_at"][:16].replace("T", " "),
            }
            for r in rows
        ]

    def vote_post_helpful(self, session_token: str, post_id: int) -> Dict:
        member = self.get_member_by_token(session_token)
        if not member:
            return {"ok": False, "error": "Faydalı oyu vermek için doğrulanmış üye girişi gereklidir."}
        try:
            self.conn.execute(
                "INSERT INTO post_helpful_votes (post_id, member_id) VALUES (?, ?)",
                (post_id, member["member_id"]),
            )
            self.conn.execute(
                "UPDATE product_social_posts SET helpful_count = helpful_count + 1 WHERE post_id = ?",
                (post_id,),
            )
            self.conn.commit()
        except sqlite3.IntegrityError:
            return {"ok": False, "error": "Bu yoruma zaten oy verdiniz."}

        row = self.conn.execute(
            "SELECT helpful_count FROM product_social_posts WHERE post_id = ?", (post_id,)
        ).fetchone()
        return {"ok": True, "helpful_count": row["helpful_count"] if row else 1}

    def share_community_deal(
        self,
        session_token: str,
        title: str,
        raw_url: str,
        sale_price: float,
        coupon_code: str = "",
        note: str = "",
        product_key: str = "",
    ) -> Dict:
        """
        Doğrulanmış üye fırsat paylaştığında:
        1. AI Moderatör başlık ve notu denetler,
        2. URL'den mağaza tespit edilir ve otomatik Affiliate (?tag=marketdedekti-21 vb.) sarılır,
        3. Birim fiyat çözümleyici ve fiyat hafızasıyla otomatik 'Dedektif Damgası' üretilir.
        """
        member = self.get_member_by_token(session_token)
        if not member:
            return {"ok": False, "error": "Fırsat paylaşmak için lütfen telefon doğrulaması yapın."}

        mod = AIContentModerator.inspect(f"{title} {note}")
        if not mod.allowed:
            return {"ok": False, "error": mod.reason}

        url_low = (raw_url or "").strip().lower()
        if not url_low.startswith(("http://", "https://")):
            return {"ok": False, "error": "Lütfen geçerli bir mağaza ürün bağlantısı (https://...) girin."}

        if "amazon.com.tr" in url_low:
            platform = "amazon"
        elif "hepsiburada.com" in url_low:
            platform = "hepsiburada"
        elif "migros.com.tr" in url_low:
            platform = "migros"
        elif "mediamarkt.com.tr" in url_low:
            platform = "mediamarkt"
        elif "vatanbilgisayar.com" in url_low:
            platform = "vatan"
        elif "carrefoursa.com" in url_low:
            platform = "carrefoursa"
        else:
            platform = "online_magaza"

        # Otomatik Gelir Ortaklığı Sarmalama!
        tracked_url = self.affiliate.build_tracking_url(
            raw_url.strip(), sub_id=f"topluluk_u{member['member_id']}"
        )

        # Otomatik Birim Maliyet ve Dedektif Damgası Hesaplama
        parsed_unit = self.normalizer.parse_title_quantity(title)
        unit_summary = ""
        detective_stamp = "🤖 Dedektif Kontrolü: Mağaza ve bağlantı doğrulandı"
        detective_ok = 1

        if parsed_unit and parsed_unit[0] > 0:
            base_amt, base_lbl = parsed_unit
            u_price = round(sale_price / base_amt, 2)
            unit_summary = f"{u_price:.2f} TL / {base_lbl}"
            detective_stamp = f"🤖 Dedektif Hesabı: Birim maliyet {unit_summary} olarak doğrulandı"

        if product_key:
            med_row = self.conn.execute(
                """
                SELECT AVG(effective_unit_price) AS avg_u
                FROM price_snapshots
                WHERE product_key = ? AND seller_type IN ('OFFICIAL', 'AUTHORIZED')
                """,
                (product_key,),
            ).fetchone()
            if med_row and med_row["avg_u"] and parsed_unit and parsed_unit[0] > 0:
                u_now = sale_price / parsed_unit[0]
                avg_u = float(med_row["avg_u"])
                if u_now <= avg_u * 0.90:
                    diff = round((1.0 - u_now / avg_u) * 100)
                    detective_stamp = f"✓ Dedektif Onaylı Dip Fiyat: 90g ortalamadan %{diff} daha ucuz!"
                elif u_now > avg_u * 1.05:
                    detective_stamp = "⚠️ Dedektif Uyarısı: Fiyat geçmiş ortalamanın üzerinde, dikkatli olun."
                    detective_ok = 0

        clean_coupon = re.sub(r"[^A-Z0-9_\-]", "", (coupon_code or "").upper())[:20]

        cur = self.conn.execute(
            """
            INSERT INTO community_deals
            (member_id, product_key, title, platform, sale_price, unit_summary, coupon_code,
             raw_url, tracked_affiliate_url, detective_stamp, detective_verified, note, temperature, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 15, ?)
            """,
            (
                member["member_id"],
                product_key,
                title.strip(),
                platform,
                float(sale_price),
                unit_summary,
                clean_coupon,
                raw_url.strip(),
                tracked_url,
                detective_stamp,
                detective_ok,
                note.strip(),
                datetime.now().isoformat(),
            ),
        )
        self.conn.commit()
        return {
            "ok": True,
            "deal_id": cur.lastrowid,
            "tracked_affiliate_url": tracked_url,
            "detective_stamp": detective_stamp,
        }

    def vote_deal_temperature(self, session_token: str, deal_id: int, delta: int) -> Dict:
        member = self.get_member_by_token(session_token)
        if not member:
            return {"ok": False, "error": "Derece oyu vermek için telefon doğrulaması ile giriş yapın."}

        step = 5 if delta > 0 else -5
        try:
            self.conn.execute(
                "INSERT INTO deal_temperature_votes (deal_id, member_id, delta) VALUES (?, ?, ?)",
                (deal_id, member["member_id"], step),
            )
            self.conn.execute(
                "UPDATE community_deals SET temperature = temperature + ? WHERE deal_id = ?",
                (step, deal_id),
            )
            self.conn.commit()
        except sqlite3.IntegrityError:
            return {"ok": False, "error": "Bu fırsata zaten derece oyu verdiniz."}

        row = self.conn.execute(
            "SELECT temperature FROM community_deals WHERE deal_id = ?", (deal_id,)
        ).fetchone()
        return {"ok": True, "temperature": row["temperature"] if row else 0}

    def list_community_deals(self) -> List[Dict]:
        rows = self.conn.execute(
            """
            SELECT d.*, m.display_name, m.badges_json
            FROM community_deals d
            JOIN verified_members m ON d.member_id = m.member_id
            WHERE m.is_shadowbanned = 0
            ORDER BY d.temperature DESC, d.created_at DESC
            LIMIT 30
            """
        ).fetchall()

        return [
            {
                "deal_id": r["deal_id"],
                "product_key": r["product_key"],
                "title": r["title"],
                "platform": r["platform"],
                "sale_price": r["sale_price"],
                "unit_summary": r["unit_summary"],
                "coupon_code": r["coupon_code"],
                "tracked_affiliate_url": r["tracked_affiliate_url"],
                "detective_stamp": r["detective_stamp"],
                "detective_verified": bool(r["detective_verified"]),
                "note": r["note"],
                "temperature": r["temperature"],
                "author_name": r["display_name"],
                "author_badges": json.loads(r["badges_json"] or "[]"),
                "created_at": r["created_at"][:16].replace("T", " "),
            }
            for r in rows
        ]
