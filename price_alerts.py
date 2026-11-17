"""
dipci.tr — Hedef Birim Fiyat Düşüş Alarmı Servisi
Kullanıcıların birim bazda (TL/kg, TL/litre, TL/yıkama, TL/adet) hedef fiyat belirlemesini
ve her otomatik market taramasından (live_sync.py) sonra hedef yakalandığında bildirim üretmesini sağlar.
"""

import json
import smtplib
import ssl
import urllib.request
from datetime import datetime
from email.message import EmailMessage
from typing import Dict, List, Optional

from email_auth import EmailOTPClient, normalize_and_validate_email
from memory_db import PriceMemoryDB
from seo_engine import CANONICAL_ORIGIN, key_to_slug


class PriceAlertService:
    def __init__(self, db: PriceMemoryDB) -> None:
        self.db = db
        self.email_client = EmailOTPClient()
        self._init_schema()

    def _init_schema(self) -> None:
        self.db.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS price_alerts (
                alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                member_id INTEGER,
                product_key TEXT NOT NULL,
                target_unit_price REAL NOT NULL,
                baseline_unit_price REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'ACTIVE',       -- ACTIVE, TRIGGERED
                triggered_platform TEXT DEFAULT '',
                triggered_seller TEXT DEFAULT '',
                triggered_unit_price REAL,
                triggered_pack_price REAL,
                triggered_url TEXT DEFAULT '',
                triggered_at TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_price_alerts_status
                ON price_alerts(status, product_key);
            CREATE INDEX IF NOT EXISTS idx_price_alerts_email
                ON price_alerts(email);
            """
        )
        cols = {
            r["name"]
            for r in self.db.conn.execute("PRAGMA table_info(price_alerts)").fetchall()
        }
        if "target_pack_price" not in cols:
            self.db.conn.execute("ALTER TABLE price_alerts ADD COLUMN target_pack_price REAL NOT NULL DEFAULT 0")
        if "baseline_pack_price" not in cols:
            self.db.conn.execute("ALTER TABLE price_alerts ADD COLUMN baseline_pack_price REAL NOT NULL DEFAULT 0")
        if "browser_notified" not in cols:
            self.db.conn.execute("ALTER TABLE price_alerts ADD COLUMN browser_notified INTEGER NOT NULL DEFAULT 0")
        self.db.conn.commit()

    def create_alert(
        self,
        raw_email: str,
        product_key: str,
        target_unit_price: float = 0.0,
        target_pack_price: float = 0.0,
        member_id: Optional[int] = None,
    ) -> Dict:
        valid, clean_email, err = normalize_and_validate_email(raw_email)
        if not valid:
            return {"ok": False, "error": err}

        if target_unit_price <= 0 and target_pack_price <= 0:
            return {"ok": False, "error": "Lütfen 0'dan büyük geçerli bir hedef fiyat girin."}

        prod = self.db.conn.execute(
            "SELECT product_key, brand, family_name, base_unit, reference_net_amount FROM canonical_products WHERE product_key = ?",
            (product_key,),
        ).fetchone()
        if not prod:
            return {"ok": False, "error": "Ürün bulunamadı."}

        quotes = self.db.get_latest_market_quotes(product_key, window_hours=168)
        trusted = [q for q in quotes if q.seller_type in ("OFFICIAL", "AUTHORIZED") and q.seller_rating >= 8.8]
        active_q = sorted(trusted if trusted else quotes, key=lambda q: (q.effective_pack_price, q.effective_unit_price))
        best_q = active_q[0] if active_q else None

        cur_pack = float(best_q.effective_pack_price) if best_q else 0.0
        cur_unit = float(best_q.effective_unit_price) if best_q else 0.0
        ref_amt = float(best_q.net_amount) if (best_q and best_q.net_amount > 0) else float(prod["reference_net_amount"] or 1.0)
        ref_amt = max(ref_amt, 0.01)

        if target_pack_price > 0 and target_unit_price <= 0:
            target_unit_price = round(target_pack_price / ref_amt, 2)
        elif target_unit_price > 0 and target_pack_price <= 0:
            target_pack_price = round(target_unit_price * ref_amt, 2)

        # Aynı ürün + e-posta için aktif alarm varsa hedefini güncelle
        existing = self.db.conn.execute(
            "SELECT alert_id FROM price_alerts WHERE email = ? AND product_key = ? AND status = 'ACTIVE'",
            (clean_email, product_key),
        ).fetchone()
        now_iso = datetime.now().isoformat()

        if existing:
            self.db.conn.execute(
                """
                UPDATE price_alerts
                SET target_unit_price = ?, target_pack_price = ?,
                    baseline_unit_price = ?, baseline_pack_price = ?,
                    member_id = COALESCE(?, member_id), browser_notified = 0, created_at = ?
                WHERE alert_id = ?
                """,
                (
                    round(target_unit_price, 2),
                    round(target_pack_price, 2),
                    round(cur_unit, 2),
                    round(cur_pack, 2),
                    member_id,
                    now_iso,
                    existing["alert_id"],
                ),
            )
            alert_id = existing["alert_id"]
        else:
            cur = self.db.conn.execute(
                """
                INSERT INTO price_alerts
                (email, member_id, product_key, target_unit_price, target_pack_price,
                 baseline_unit_price, baseline_pack_price, status, browser_notified, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE', 0, ?)
                """,
                (
                    clean_email,
                    member_id,
                    product_key,
                    round(target_unit_price, 2),
                    round(target_pack_price, 2),
                    round(cur_unit, 2),
                    round(cur_pack, 2),
                    now_iso,
                ),
            )
            alert_id = cur.lastrowid

        self.db.conn.commit()
        return {
            "ok": True,
            "alert_id": alert_id,
            "email": clean_email,
            "target_pack_price": round(target_pack_price, 2),
            "target_unit_price": round(target_unit_price, 2),
            "message": (
                f"{prod['brand']} {prod['family_name']} fiyatı {target_pack_price:.2f} TL "
                f"altına indiğinde 15 dakikalık hızlı radarımız size anında bildirim gönderecek."
            ),
        }

    def list_alerts(self, email: str = "", member_id: Optional[int] = None) -> List[Dict]:
        clean_email = (email or "").strip().lower()
        if not clean_email and not member_id:
            return []

        if member_id and clean_email:
            rows = self.db.conn.execute(
                """
                SELECT a.*, c.brand, c.family_name, c.base_unit, c.image_url
                FROM price_alerts a
                JOIN canonical_products c ON a.product_key = c.product_key
                WHERE a.member_id = ? OR a.email = ?
                ORDER BY a.created_at DESC
                LIMIT 30
                """,
                (member_id, clean_email),
            ).fetchall()
        elif member_id:
            rows = self.db.conn.execute(
                """
                SELECT a.*, c.brand, c.family_name, c.base_unit, c.image_url
                FROM price_alerts a
                JOIN canonical_products c ON a.product_key = c.product_key
                WHERE a.member_id = ?
                ORDER BY a.created_at DESC
                LIMIT 30
                """,
                (member_id,),
            ).fetchall()
        else:
            rows = self.db.conn.execute(
                """
                SELECT a.*, c.brand, c.family_name, c.base_unit, c.image_url
                FROM price_alerts a
                JOIN canonical_products c ON a.product_key = c.product_key
                WHERE a.email = ?
                ORDER BY a.created_at DESC
                LIMIT 30
                """,
                (clean_email,),
            ).fetchall()

        out = []
        for r in rows:
            quotes = self.db.get_latest_market_quotes(r["product_key"], window_hours=168)
            trusted = [q for q in quotes if q.seller_type in ("OFFICIAL", "AUTHORIZED") and q.seller_rating >= 8.8]
            active_q = sorted(trusted if trusted else quotes, key=lambda x: (x.effective_pack_price, x.effective_unit_price))
            best_q = active_q[0] if active_q else None
            d = dict(r)
            d["current_best_pack_price"] = best_q.effective_pack_price if best_q else 0.0
            d["current_best_unit_price"] = best_q.effective_unit_price if best_q else 0.0
            d["current_best_seller"] = best_q.seller_name if best_q else ""
            d["seo_url"] = f"/urun/{key_to_slug(r['product_key'])}"
            out.append(d)
        return out

    def pop_pending_browser_notifications(self, email: str = "", member_id: Optional[int] = None) -> List[Dict]:
        """Kullanıcı tarayıcıda siteyi açık tuttuğunda veya ziyaret ettiğinde tetiklenmiş alarmları döner."""
        clean_email = (email or "").strip().lower()
        if not clean_email and not member_id:
            return []
        if member_id and clean_email:
            rows = self.db.conn.execute(
                """
                SELECT a.*, c.brand, c.family_name, c.base_unit
                FROM price_alerts a
                JOIN canonical_products c ON a.product_key = c.product_key
                WHERE a.status = 'TRIGGERED' AND a.browser_notified = 0
                  AND (a.member_id = ? OR a.email = ?)
                """,
                (member_id, clean_email),
            ).fetchall()
        elif member_id:
            rows = self.db.conn.execute(
                """
                SELECT a.*, c.brand, c.family_name, c.base_unit
                FROM price_alerts a
                JOIN canonical_products c ON a.product_key = c.product_key
                WHERE a.status = 'TRIGGERED' AND a.browser_notified = 0 AND a.member_id = ?
                """,
                (member_id,),
            ).fetchall()
        else:
            rows = self.db.conn.execute(
                """
                SELECT a.*, c.brand, c.family_name, c.base_unit
                FROM price_alerts a
                JOIN canonical_products c ON a.product_key = c.product_key
                WHERE a.status = 'TRIGGERED' AND a.browser_notified = 0 AND a.email = ?
                """,
                (clean_email,),
            ).fetchall()

        if not rows:
            return []

        ids = [r["alert_id"] for r in rows]
        placeholders = ",".join("?" for _ in ids)
        self.db.conn.execute(
            f"UPDATE price_alerts SET browser_notified = 1 WHERE alert_id IN ({placeholders})",
            ids,
        )
        self.db.conn.commit()
        return [
            {
                "alert_id": r["alert_id"],
                "product_key": r["product_key"],
                "title": f"{r['brand']} {r['family_name']}",
                "seller_name": r["triggered_seller"],
                "pack_price": r["triggered_pack_price"],
                "unit_price": r["triggered_unit_price"],
                "seo_url": f"/urun/{key_to_slug(r['product_key'])}",
            }
            for r in rows
        ]

    def delete_alert(self, alert_id: int, email: str = "", member_id: Optional[int] = None) -> Dict:
        clean_email = (email or "").strip().lower()
        if member_id:
            self.db.conn.execute(
                "DELETE FROM price_alerts WHERE alert_id = ? AND (member_id = ? OR email = ?)",
                (alert_id, member_id, clean_email),
            )
        else:
            self.db.conn.execute(
                "DELETE FROM price_alerts WHERE alert_id = ? AND email = ?",
                (alert_id, clean_email),
            )
        self.db.conn.commit()
        return {"ok": True}

    def evaluate_active_alerts(self) -> int:
        active_rows = self.db.conn.execute(
            """
            SELECT a.*, c.brand, c.family_name, c.base_unit
            FROM price_alerts a
            JOIN canonical_products c ON a.product_key = c.product_key
            WHERE a.status = 'ACTIVE'
            """
        ).fetchall()
        triggered_count = 0
        now_iso = datetime.now().isoformat()

        for row in active_rows:
            quotes = self.db.get_latest_market_quotes(row["product_key"], window_hours=168)
            trusted = [q for q in quotes if q.seller_type in ("OFFICIAL", "AUTHORIZED") and q.seller_rating >= 8.8]
            active_q = sorted(trusted if trusted else quotes, key=lambda x: (x.effective_pack_price, x.effective_unit_price))
            if not active_q:
                continue
            best_q = active_q[0]
            t_pack = float(row["target_pack_price"] or 0.0)
            t_unit = float(row["target_unit_price"] or 0.0)
            hit_pack = t_pack > 0 and best_q.effective_pack_price <= t_pack
            hit_unit = t_unit > 0 and best_q.effective_unit_price <= t_unit
            if hit_pack or hit_unit:
                self.db.conn.execute(
                    """
                    UPDATE price_alerts
                    SET status = 'TRIGGERED',
                        triggered_platform = ?,
                        triggered_seller = ?,
                        triggered_unit_price = ?,
                        triggered_pack_price = ?,
                        triggered_url = ?,
                        triggered_at = ?,
                        browser_notified = 0
                    WHERE alert_id = ?
                    """,
                    (
                        best_q.platform,
                        best_q.seller_name,
                        best_q.effective_unit_price,
                        best_q.effective_pack_price,
                        best_q.url,
                        now_iso,
                        row["alert_id"],
                    ),
                )
                triggered_count += 1
                if self.email_client.is_configured:
                    self._send_alert_email(row, best_q)

        if triggered_count:
            self.db.conn.commit()
        return triggered_count

    def _send_alert_email(self, alert_row, best_q) -> None:
        prod_name = f"{alert_row['brand']} {alert_row['family_name']}"
        prod_url = f"{CANONICAL_ORIGIN}/urun/{key_to_slug(alert_row['product_key'])}"
        subject = f"Dip Alarmı: dibe indi → {prod_name} ({best_q.effective_unit_price:.2f} TL/{alert_row['base_unit']})"
        html_body = f"""
        <div style="font-family:Inter,Arial,sans-serif;max-width:520px;margin:0 auto;padding:24px;border:1px solid #e2e8f0;border-radius:16px;background:#fff">
          <div style="font-size:18px;font-weight:900;color:#0f172a">dipci<span style="color:#10b981">.tr</span> Dip Alarmı</div>
          <h2 style="font-size:16px;margin-top:14px;color:#065f46">{prod_name} dibe indi: hedef fiyatınıza düştü!</h2>
          <p style="font-size:14px;color:#334155">
            <strong>{best_q.seller_name}</strong> mağazasında güncel birim fiyat:
            <strong>{best_q.effective_unit_price:.2f} TL / {alert_row['base_unit']}</strong>
            (Paket: {best_q.effective_pack_price:.2f} TL)
          </p>
          <a href="{prod_url}" style="display:inline-block;margin-top:12px;padding:10px 18px;background:#059669;color:#fff;text-decoration:none;border-radius:10px;font-weight:700;font-size:13px">
            Fiyat Röntgenini Gör →
          </a>
        </div>
        """
        try:
            if self.email_client.resend_api_key:
                payload = json.dumps(
                    {
                        "from": f"dipci.tr Dip Alarmı <{self.email_client.smtp_from}>",
                        "to": [alert_row["email"]],
                        "subject": subject,
                        "html": html_body,
                    }
                ).encode("utf-8")
                req = urllib.request.Request(
                    "https://api.resend.com/emails",
                    data=payload,
                    headers={
                        "Authorization": f"Bearer {self.email_client.resend_api_key}",
                        "Content-Type": "application/json",
                        "User-Agent": "dipci-mailer/1.0",  # Resend, Python varsayılan kimliğini 403 ile engelliyor
                    },
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=8)
                return

            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = f"dipci.tr <{self.email_client.smtp_from}>"
            msg["To"] = alert_row["email"]
            msg.set_content(f"{prod_name} hedef fiyatınıza indi: {prod_url}")
            msg.add_alternative(html_body, subtype="html")
            if self.email_client.smtp_port == 465:
                with smtplib.SMTP_SSL(self.email_client.smtp_host, self.email_client.smtp_port, context=ssl.create_default_context(), timeout=8) as srv:
                    srv.login(self.email_client.smtp_user, self.email_client.smtp_pass)
                    srv.send_message(msg)
            else:
                with smtplib.SMTP(self.email_client.smtp_host, self.email_client.smtp_port, timeout=8) as srv:
                    srv.starttls(context=ssl.create_default_context())
                    srv.login(self.email_client.smtp_user, self.email_client.smtp_pass)
                    srv.send_message(msg)
        except Exception:
            pass
