"""
dipci.tr — Site haritasını her gün Google Search Console'a yeniden gönderir (Kemal, 17 Kas 2026).

Google ürün sayfaları için "anında dizine ekle" kapısı vermez; izinli yol site haritasını
Search Console API ile göndermektir. Hizmet hesabı: dipci-seo@mtr1-vision.iam.gserviceaccount.com
(Search Console'da dipci.tr mülküne "Tam" kullanıcı olarak eklenmeli).
Anahtar dosyası yalnız sunucuda: GSC_KEY_PATH (varsayılan /opt/dipci/secrets/gsc-dipci-seo.json).
"""
import json
import os
import sys
import urllib.parse

from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession

SITE = os.getenv("GSC_SITE", "sc-domain:dipci.tr")
SITEMAP = os.getenv("GSC_SITEMAP", "https://dipci.tr/sitemap.xml")
KEY_PATH = os.getenv("GSC_KEY_PATH", "/opt/dipci/secrets/gsc-dipci-seo.json")
SCOPES = ["https://www.googleapis.com/auth/webmasters"]


def main() -> int:
    creds = service_account.Credentials.from_service_account_file(KEY_PATH, scopes=SCOPES)
    s = AuthorizedSession(creds)
    base = "https://www.googleapis.com/webmasters/v3/sites/" + urllib.parse.quote(SITE, safe="")
    put = s.put(f"{base}/sitemaps/" + urllib.parse.quote(SITEMAP, safe=""), timeout=30)
    if put.status_code not in (200, 204):
        print(json.dumps({"ok": False, "status": put.status_code, "body": put.text[:300]}, ensure_ascii=False))
        return 1
    info = s.get(f"{base}/sitemaps/" + urllib.parse.quote(SITEMAP, safe=""), timeout=30).json()
    print(json.dumps({
        "ok": True,
        "lastSubmitted": info.get("lastSubmitted"),
        "lastDownloaded": info.get("lastDownloaded"),
        "contents": info.get("contents"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
