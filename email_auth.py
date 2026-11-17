"""
dipci.tr — KVKK Uyumlu E-Posta OTP & Google ile Tek Tık Giriş (GIS) Servisi
- Tek kullanımlık / sahte (disposable) e-posta sağlayıcılarını otomatik engeller.
- SMTP_HOST/SMTP_USER/SMTP_PASS veya RESEND_API_KEY tanımlıysa gerçek e-posta gönderir;
  tanımlı değilse geliştirme/önizleme modunda kodu güvenli şekilde döndürür.
- Google Identity Services (GIS) id_token doğrulamasını Google resmi tokeninfo uç noktasından yapar.
"""

import json
import os
import re
import smtplib
import ssl
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from typing import Dict, Tuple

DISPOSABLE_EMAIL_DOMAINS = {
    "mailinator.com",
    "tempmail.com",
    "temp-mail.org",
    "10minutemail.com",
    "guerrillamail.com",
    "yopmail.com",
    "sharklasers.com",
    "trashmail.com",
    "dispostable.com",
    "getnada.com",
    "maildrop.cc",
    "fakeinbox.com",
}

EMAIL_REGEX = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,20}$")


def normalize_and_validate_email(raw_email: str) -> Tuple[bool, str, str]:
    """(gecerli_mi, temiz_email, hata_mesaji) döndürür."""
    cleaned = (raw_email or "").strip().lower()
    if not cleaned or not EMAIL_REGEX.match(cleaned):
        return False, "", "Lütfen geçerli bir e-posta adresi girin (örn: adiniz@gmail.com)."
    domain = cleaned.split("@", 1)[1]
    if domain in DISPOSABLE_EMAIL_DOMAINS:
        return False, "", "Sahte yorumları önlemek için geçici (tek kullanımlık) e-posta adresleri kabul edilmez."
    return True, cleaned, ""


class EmailOTPClient:
    def __init__(self) -> None:
        self.smtp_host = os.getenv("SMTP_HOST", "").strip()
        self.smtp_port = int(os.getenv("SMTP_PORT", "587").strip() or "587")
        self.smtp_user = os.getenv("SMTP_USER", "").strip()
        self.smtp_pass = os.getenv("SMTP_PASS", "").strip()
        self.smtp_from = os.getenv("SMTP_FROM", "").strip() or self.smtp_user or "dogrulama@dipci.tr"
        self.resend_api_key = os.getenv("RESEND_API_KEY", "").strip()

    @property
    def is_configured(self) -> bool:
        return bool(self.resend_api_key or (self.smtp_host and self.smtp_user and self.smtp_pass))

    def send_otp_email(self, to_email: str, display_name: str, otp_code: str) -> Tuple[bool, str]:
        subject = f"dipci.tr giriş kodunuz: {otp_code}"
        html_body = f"""
        <div style="font-family:Inter,Arial,sans-serif;max-width:480px;margin:0 auto;padding:24px;border:1px solid #e2e8f0;border-radius:16px;background:#ffffff;color:#0f172a">
          <div style="font-size:18px;font-weight:800;color:#0b132b">dipci<span style="color:#10b981">.tr</span></div>
          <p style="font-size:14px;color:#334155;margin-top:12px">Merhaba <strong>{display_name}</strong>,</p>
          <p style="font-size:13px;color:#475569">Doğrulanmış alışveriş topluluğuna giriş için tek kullanımlık 6 haneli kodunuz:</p>
          <div style="margin:18px 0;padding:16px;background:#ecfdf5;border:1px solid #a7f3d0;border-radius:12px;text-align:center;font-family:monospace;font-size:28px;font-weight:900;letter-spacing:6px;color:#065f46">
            {otp_code}
          </div>
          <p style="font-size:12px;color:#64748b">Bu kod 10 dakika geçerlidir. Kimseyle paylaşmayınız.</p>
          <p style="font-size:11px;color:#94a3b8;margin-top:18px;border-top:1px solid #e2e8f0;padding-top:12px">Bu e-postayı dipci.tr'de e-posta ile giriş istediğiniz için aldınız. Siz istemediyseniz görmezden gelebilirsiniz. dipci.tr · <a href="https://dipci.tr" style="color:#047857">dipci.tr</a></p>
        </div>
        """
        # Düz metin sürümü: Hotmail/Outlook yalnız HTML içeren postaları daha sık gereksize atıyor (17 Kas 2026).
        text_body = (
            f"Merhaba {display_name},\n\n"
            f"dipci.tr giriş kodunuz: {otp_code}\n\n"
            "Bu kod 10 dakika geçerlidir. Kimseyle paylaşmayınız.\n\n"
            "Bu e-postayı dipci.tr'de e-posta ile giriş istediğiniz için aldınız. Siz istemediyseniz görmezden gelebilirsiniz.\n"
            "https://dipci.tr\n"
        )

        if self.resend_api_key:
            payload = json.dumps(
                {
                    "from": f"dipci.tr <{self.smtp_from}>",
                    "to": [to_email],
                    "subject": subject,
                    "html": html_body,
                    "text": text_body,
                    "reply_to": "iletisim@dipci.tr",
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                "https://api.resend.com/emails",
                data=payload,
                headers={
                    "Authorization": f"Bearer {self.resend_api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "dipci-mailer/1.0",  # Resend, Python varsayılan kimliğini 403 ile engelliyor
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=8) as resp:
                    return True, resp.read().decode("utf-8", errors="replace")
            except Exception as e:
                return False, f"Resend hatası: {e}"

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = f"dipci.tr <{self.smtp_from}>"
        msg["To"] = to_email
        msg.set_content(f"dipci.tr doğrulama kodunuz: {otp_code} (10 dakika geçerlidir)")
        msg.add_alternative(html_body, subtype="html")

        try:
            if self.smtp_port == 465:
                ctx = ssl.create_default_context()
                with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, context=ctx, timeout=8) as server:
                    server.login(self.smtp_user, self.smtp_pass)
                    server.send_message(msg)
            else:
                with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=8) as server:
                    server.starttls(context=ssl.create_default_context())
                    server.login(self.smtp_user, self.smtp_pass)
                    server.send_message(msg)
            return True, "ok"
        except Exception as e:
            return False, f"SMTP hatası: {e}"


def verify_google_id_token(id_token: str, expected_client_id: str = "") -> Tuple[bool, Dict, str]:
    """Google Identity Services id_token (JWT) bilgisini Google resmi tokeninfo servisiyle doğrular."""
    token = (id_token or "").strip()
    if not token:
        return False, {}, "Google oturum anahtarı boş."
    url = "https://oauth2.googleapis.com/tokeninfo?" + urllib.parse.urlencode({"id_token": token})
    try:
        with urllib.request.urlopen(url, timeout=6) as resp:
            info = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError:
        return False, {}, "Google oturum anahtarı geçersiz veya süresi dolmuş."
    except Exception as e:
        return False, {}, f"Google doğrulama bağlantı hatası: {e}"

    if not expected_client_id or info.get("aud") != expected_client_id:
        return False, {}, "Google uygulama kimliği (aud) eşleşmedi."
    if str(info.get("email_verified", "")).lower() != "true":
        return False, {}, "Google e-posta adresi doğrulanmamış."
    return True, info, ""
