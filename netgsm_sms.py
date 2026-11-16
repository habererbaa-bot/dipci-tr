"""
dipci.tr — Resmi Netgsm REST v2 SMS OTP Entegrasyonu
Ortam değişkenlerinde NETGSM_USERCODE ve NETGSM_PASSWORD tanımlıysa:
- NETGSM_MSGHEADER="AUTO" (veya boş) ise Netgsm /msgheader servisinden onaylı başlığı otomatik çeker.
- Henüz BTK onaylı başlık yoksa sistemi kırmaz, onaylandığı anda otomatik canlı SMS'e geçer.
"""

import base64
import json
import os
import time
import urllib.error
import urllib.request
from typing import Optional, Tuple


class NetgsmClient:
    OTP_URL = "https://api.netgsm.com.tr/sms/rest/v2/otp"
    SEND_URL = "https://api.netgsm.com.tr/sms/rest/v2/send"
    HEADER_URL = "https://api.netgsm.com.tr/sms/rest/v2/msgheader"

    _cached_header: Optional[str] = None
    _cache_ts: float = 0.0

    def __init__(self) -> None:
        self.usercode = os.getenv("NETGSM_USERCODE", "").strip()
        self.password = os.getenv("NETGSM_PASSWORD", "").strip()
        raw_hdr = os.getenv("NETGSM_MSGHEADER", "").strip()
        self.mode = os.getenv("NETGSM_MODE", "standard").strip().lower()  # "standard" veya "otp"
        if raw_hdr and raw_hdr.upper() != "AUTO":
            self.msgheader = raw_hdr
        else:
            self.msgheader = self._resolve_approved_header()

    def _basic_token(self) -> str:
        auth_raw = f"{self.usercode}:{self.password}".encode("utf-8")
        return base64.b64encode(auth_raw).decode("ascii")

    def _resolve_approved_header(self) -> str:
        if not (self.usercode and self.password):
            return ""
        now = time.time()
        if NetgsmClient._cached_header is not None and (now - NetgsmClient._cache_ts) < 300:
            return NetgsmClient._cached_header

        req = urllib.request.Request(
            self.HEADER_URL,
            headers={"Authorization": f"Basic {self._basic_token()}"},
            method="GET",
        )
        resolved = ""
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
                headers = data.get("msgheaders") or []
                if headers:
                    resolved = str(headers[0]).strip()
        except urllib.error.HTTPError as e:
            try:
                data = json.loads(e.read().decode("utf-8", errors="replace"))
                headers = data.get("msgheaders") or []
                if headers:
                    resolved = str(headers[0]).strip()
            except Exception:
                pass
        except Exception:
            pass

        NetgsmClient._cached_header = resolved
        NetgsmClient._cache_ts = now
        return resolved

    @property
    def is_configured(self) -> bool:
        return bool(self.usercode and self.password and self.msgheader)

    def send_otp(self, ten_digit_gsm: str, otp_code: str) -> Tuple[bool, str]:
        """Başında 0 olmadan 10 haneli numaraya (5XXXXXXXXX) Netgsm üzerinden doğrulama kodu gönderir."""
        msg_text = f"dipci.tr dogrulama kodunuz: {otp_code} (Kimseyle paylasmayiniz)"
        basic_token = self._basic_token()

        if self.mode == "standard":
            url = self.SEND_URL
            payload = {
                "msgheader": self.msgheader,
                "messages": [{"msg": msg_text, "no": ten_digit_gsm}],
                "encoding": "TR",
            }
        else:
            url = self.OTP_URL
            payload = {
                "msgheader": self.msgheader,
                "msg": msg_text,
                "no": ten_digit_gsm,
            }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Basic {basic_token}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                data = json.loads(body) if body.startswith("{") else {"raw": body}
                code = str(data.get("code", "00"))
                if code in ("00", "0", "200") or "jobid" in data:
                    return True, str(data.get("jobid", "ok"))
                return False, f"Netgsm yanıt kodu: {code} ({data.get('description', body)})"
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            return False, f"Netgsm HTTP {e.code}: {err_body[:160]}"
        except Exception as e:
            return False, f"Netgsm bağlantı hatası: {e}"
