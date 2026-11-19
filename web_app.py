"""
dipci.tr — Yeni Nesil Fiyat, Birim Maliyet, Kamera Okuyucu ve Doğrulanmış Sosyal Alışveriş Platformu
Tam Kapsamlı Web Sunucusu (FastAPI + SQLite WAL + 7 Katmanlı Market Dedektifi Denetim Motoru)
"""

import ipaddress
import json
import os
import socket
import threading
import time
from collections import defaultdict, deque
from dataclasses import asdict
from datetime import datetime
import sqlite3
from pathlib import Path
from typing import Optional

from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from affiliate import AffiliateLinkBuilder
from audit_engine import MarketDedektifiAuditEngine
from config import AFFILIATE_CONFIG
from memory_db import PriceMemoryDB
from scanner import (
    CameraScannerEngine,
    ShelfTagAntiSabotageGuard,
    ShelfTagAuditInput,
    VisualObservation,
)
from seed_platform import VS_COMPARISONS, ensure_seeded
from seo_engine import (
    CANONICAL_ORIGIN,
    CATEGORY_META,
    INDEXNOW_KEY,
    key_to_slug,
    build_barcodes_sitemap_xml,
    build_categories_sitemap_xml,
    build_corporate_identity_html,
    build_llms_txt,
    build_organization_and_website_schema,
    build_pages_sitemap_xml,
    build_product_seo_bundle,
    build_products_sitemap_xml,
    build_robots_txt,
    build_sitemap_index_xml,
    cat_slug_to_db,
    render_seo_hydrated_spa,
    slug_to_key,
)
from photo_vision import analyze_shelf_photo_base64
from lens_engine import DipciLensEngine
from auto_catalog import AutoCatalogEngine, UNITLESS_ITEM_CATEGORIES
from fast_radar import FastPriceRadar
from price_alerts import PriceAlertService
from basket_optimizer import BasketOptimizer, PRESET_BASKETS, QUICK_STAPLES
from custom_basket_engine import CustomBasketEngine
from social_community import SocialCommunityService
from store_accounts import StoreAccountService

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = Path(os.getenv("DIPCI_DB_PATH", str(DATA_DIR / "dipci_platform.db")))
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)

db = PriceMemoryDB(DB_PATH)
import talep as _talep
_talep.kaydet(db.conn)  # ana sayfa ihtiyaç sıralaması (Kemal, 17 Kas 2026)
affiliate_builder = AffiliateLinkBuilder(AFFILIATE_CONFIG)
audit_engine = MarketDedektifiAuditEngine(db)
scanner_engine = CameraScannerEngine(db, audit_engine, affiliate_builder)
social_service = SocialCommunityService(db.conn, affiliate_builder)
alert_service = PriceAlertService(db)
basket_optimizer = BasketOptimizer(db, affiliate_builder)
custom_basket_engine = CustomBasketEngine(db, affiliate_builder)
store_account_service = StoreAccountService(db.conn)
lens_engine = DipciLensEngine(db)
auto_catalog = AutoCatalogEngine(db, lens_engine)
fast_radar = FastPriceRadar(db, alert_service, auto_catalog, affiliate_builder)

ensure_seeded(db, social_service)

# Başlangıçta hızlı arama ve filtreleme indeksi boşsa anında oluştur
_idx_count = db.conn.execute("SELECT COUNT(*) AS c FROM product_catalog_index").fetchone()["c"]
_prod_count = db.conn.execute("SELECT COUNT(*) AS c FROM canonical_products WHERE is_private_label = 0").fetchone()["c"]
def _baslangic_indeksi():
    try:
        auto_catalog.reclassify_existing_catalog()
        db.rebuild_catalog_index()
        fast_radar.sync_watchlist()
        fast_radar.detect_and_publish_drops()
    except Exception as _e:
        print("baslangic indeksi:", _e)


# 400 bin üründe bu iş dakikalar sürüyor: site açılışını bekletmesin, arka planda yapılsın (19 Kas 2026)
if _idx_count < _prod_count and _idx_count > 1000:
    threading.Thread(target=_baslangic_indeksi, daemon=True).start()
elif _idx_count < _prod_count:
    # Arka plandaki tarama veritabanını o an kilitliyorsa site açılışı çökmesin (17 Kas 2026).
    try:
        auto_catalog.reclassify_existing_catalog()
        db.rebuild_catalog_index()
        fast_radar.sync_watchlist()
        fast_radar.detect_and_publish_drops()
    except sqlite3.OperationalError as _e:
        db.conn.rollback()
        print(f"[dipci] Açılış indeks yenilemesi atlandı (veritabanı meşgul): {_e}")

# basınca reklam kodlarının ürün havuzlarını arka planda hazırla (ilk okur beklemesin).
try:
    import basinca_ads as _basinca_ads

    _basinca_ads.warm_pools(db)
except Exception as _e:
    print(f"[dipci] basinca reklam havuzu hazırlanamadı: {_e}")

_DOCS_ENABLED = os.getenv("DIPCI_ENABLE_DOCS", "0").strip() == "1"

app = FastAPI(
    title="dipci.tr — Fiyat Röntgeni, Reyon Kamerası & Doğrulanmış Alışveriş Ağı",
    version="1.0.0",
    docs_url="/docs" if _DOCS_ENABLED else None,
    redoc_url="/redoc" if _DOCS_ENABLED else None,
    openapi_url="/openapi.json" if _DOCS_ENABLED else None,
)


# ----------------- Basit IP Bazlı Hız Sınırlayıcı -----------------
# Not: Her uvicorn işçisi kendi sayacını tutar (--workers 2 => etkin sınır en fazla 2 katı).
# Daha katı sınır gerekirse Caddy rate_limit modülü veya Redis tabanlı sayaç kullanılabilir.

class _SlidingWindowLimiter:
    def __init__(self) -> None:
        self._hits: dict = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window_seconds:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            if len(self._hits) > 50_000:  # bellek koruması
                for k in [k for k, v in self._hits.items() if not v][:10_000]:
                    self._hits.pop(k, None)
            return True


_limiter = _SlidingWindowLimiter()


def _client_ip(request: Request) -> str:
    # uvicorn --proxy-headers, Caddy'den (127.0.0.1) gelen X-Forwarded-For'u request.client'a yansıtır
    return (request.client.host if request.client else "") or "unknown"


def _enforce_rate_limit(request: Request, bucket: str, limit: int, window_seconds: int) -> None:
    if not _limiter.allow(f"{bucket}:{_client_ip(request)}", limit, window_seconds):
        raise HTTPException(status_code=429, detail="Çok fazla istek gönderdiniz; lütfen biraz sonra tekrar deneyin.")


def _discovery_allowed(request: Request) -> bool:
    """Katalogda olmayan ürün için dış mağazalarda canlı tarama + kalıcı sayfa açmaya izin var mı?"""
    return _limiter.allow(f"discover:{_client_ip(request)}", 6, 60) and _limiter.allow("discover:global", 120, 60)


class SecurityHeadersMiddleware:
    """Temel güvenlik başlıkları (CSP, Tailwind CDN derlenmiş CSS'e taşınınca eklenmeli)."""

    HEADERS = [
        (b"strict-transport-security", b"max-age=31536000; includeSubDomains"),
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"SAMEORIGIN"),
        (b"referrer-policy", b"strict-origin-when-cross-origin"),
        (b"permissions-policy", b"camera=(self), microphone=(), geolocation=()"),
    ]

    def __init__(self, asgi_app):
        self.asgi_app = asgi_app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.asgi_app(scope, receive, send)
            return

        gomulu = scope.get("path", "").startswith("/reklam/kutu/")

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"server"]
                existing = {k.lower() for k, _ in headers}
                if gomulu:
                    # dipci reklam kutusu basınca'ya gömülür (Kemal, 17 Kas 2026)
                    headers.append((b"content-security-policy", b"frame-ancestors 'self' https://basinca.com https://*.basinca.com"))
                    headers.extend((k, v) for k, v in self.HEADERS if k not in existing and k != b"x-frame-options")
                else:
                    headers.extend((k, v) for k, v in self.HEADERS if k not in existing)
                message = dict(message, headers=headers)
            await send(message)

        await self.asgi_app(scope, receive, send_with_headers)


class HeadSupportMiddleware:
    """Googlebot ve Search Console HEAD yoklamalarının 200 OK dönmesini sağlar."""

    def __init__(self, asgi_app):
        self.asgi_app = asgi_app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] == "HEAD":
            scope = dict(scope, method="GET")

            async def send_no_body(message):
                if message["type"] == "http.response.body":
                    message = dict(message, body=b"")
                await send(message)

            await self.asgi_app(scope, receive, send_no_body)
            return
        await self.asgi_app(scope, receive, send)


app.add_middleware(HeadSupportMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
class CachedStaticFiles(StaticFiles):
    """Statik dosyalar tarayıcıda önbelleğe alınır; ?v= sürümlü dosyalar 1 yıl (değişince URL değişir)."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            if b"v=" in scope.get("query_string", b""):
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            else:
                response.headers["Cache-Control"] = "public, max-age=86400"
        return response


app.mount("/static", CachedStaticFiles(directory=str(STATIC_DIR)), name="static")


# ----------------- İstek Modelleri -----------------

class OTPRequestPayload(BaseModel):
    phone: str
    display_name: str


class OTPVerifyPayload(BaseModel):
    phone: str
    otp_code: str


class EmailOTPRequestPayload(BaseModel):
    email: str
    display_name: str = ""


class EmailOTPVerifyPayload(BaseModel):
    email: str
    otp_code: str


class GoogleAuthPayload(BaseModel):
    credential: str


class BarcodeScanPayload(BaseModel):
    gtin: str
    shelf_price_tl: Optional[float] = None
    save_crowd_shelf_tag: bool = False
    label_printed_gtin: Optional[str] = None
    label_printed_unit_price: Optional[float] = None


class VisionScanPayload(BaseModel):
    brand: str
    product_name: str
    net_amount_value: Optional[float] = None
    model_code: str = ""
    gtin_visible: str = ""
    shelf_price_tl: Optional[float] = None


class SocialPostPayload(BaseModel):
    product_key: str
    post_type: str = "FIRST_IMPRESSION"
    content: str
    coupon_code: str = ""
    usage_months: int = 0
    seen_store: str = ""
    seen_price: float = 0.0


class ShareDealPayload(BaseModel):
    title: str
    raw_url: str
    sale_price: float
    coupon_code: str = ""
    note: str = ""
    product_key: str = ""


class VotePayload(BaseModel):
    delta: int = 1


class PartnerXMLPayload(BaseModel):
    store_name: str
    xml_url: str
    contact_email: str = ""
    brand_color: str = "#2563EB"


class PhotoScanPayload(BaseModel):
    image_base64: str = ""
    mime_type: str = "image/jpeg"
    detected_gtin: str = ""
    hint_text: str = ""
    shelf_price_tl: Optional[float] = None


class LensScanPayload(BaseModel):
    image_base64: str = ""
    mime_type: str = "image/jpeg"
    detected_gtin: str = ""
    query_override: str = ""


class CreatePriceAlertPayload(BaseModel):
    email: str
    product_key: str
    target_unit_price: float = 0.0
    target_pack_price: float = 0.0


class BasketItemInput(BaseModel):
    product_key: str
    qty: int = 1


class BasketOptimizePayload(BaseModel):
    items: list[BasketItemInput]


class CustomBasketItemInput(BaseModel):
    item_id: str
    qty: int = 1


class CustomBasketComparePayload(BaseModel):
    items: list[CustomBasketItemInput]
    delivery_filter: str = "all"
    client_profile_id: str = ""


class SaveDeliveryProfilePayload(BaseModel):
    client_profile_id: str = ""
    city: str = "İstanbul"
    district: str = ""
    neighborhood: str = ""
    address_title: str = "Ev Adresim"


class LinkStoreAccountPayload(BaseModel):
    client_profile_id: str = ""
    platform: str
    account_identifier: str = ""
    loyalty_or_pass_active: bool = False
    preferred_fulfillment: str = "HOME_DELIVERY"


class OrderIntentPayload(BaseModel):
    client_profile_id: str = ""
    items: list[CustomBasketItemInput]
    delivery_filter: str = "all"


# ----------------- SEO, Google İndeksleme & SSR Ön Yüz Rotaları -----------------

NO_CACHE_HTML_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


_TEMPLATE_CACHE: dict = {"mtime": None, "html": ""}


def _asset_version(rel_path: str) -> str:
    try:
        return str(int((STATIC_DIR / rel_path).stat().st_mtime))
    except OSError:
        return "0"


def _read_index_template() -> str:
    """index.html'i bellekte tutar; dosya değişince (deploy) otomatik yeniler.
    CSS bağlantısına sürüm ekler ki tarayıcı önbelleği deploy sonrası eski stili göstermesin."""
    path = STATIC_DIR / "index.html"
    mtime = path.stat().st_mtime
    if _TEMPLATE_CACHE["mtime"] != mtime:
        html_src = path.read_text(encoding="utf-8")
        html_src = html_src.replace('href="/static/app.css"', f'href="/static/app.css?v={_asset_version("app.css")}"')
        _TEMPLATE_CACHE.update(mtime=mtime, html=html_src)
    return _TEMPLATE_CACHE["html"]


@app.get("/", response_class=HTMLResponse)
def serve_spa_index(q: str = ""):
    raw_html = _read_index_template()
    title = (
        f"'{q}' En Ucuz Birim Fiyatları & Karşılaştırma | dipci.tr"
        if q.strip()
        else "dipci.tr — Gerçek Dip Fiyat Radarı, Reyon Kamerası & Doğrulanmış Alışveriş Ağı"
    )
    desc = (
        "Türkiye'nin 90 günlük fiyat hafızası, birim maliyet (TL/kg, TL/yıkama) normalizasyonu, "
        "sahte indirim ve gizli gramaj küçültme (shrinkflation) radarı, reyon barkod kamerası ve doğrulanmış tüketici ağı."
    )
    canon = f"{CANONICAL_ORIGIN}/"
    return HTMLResponse(
        render_seo_hydrated_spa(
            raw_html,
            title=title,
            description=desc,
            canonical_url=canon,
            json_ld_objects=build_organization_and_website_schema(),
            initial_view="vitrin",
        ),
        headers=NO_CACHE_HTML_HEADERS,
    )


@app.get("/urun/{slug}", response_class=HTMLResponse)
def serve_product_seo_page(slug: str, request: Request):
    product_key = slug_to_key(slug)
    bundle = build_product_seo_bundle(db, audit_engine, social_service, product_key)
    if not bundle:
        human_query = slug.replace("-", " ").replace("_", " ").strip()
        if 3 <= len(human_query) <= 120 and _discovery_allowed(request):
            discovered_key = auto_catalog.discover_and_persist_query(query=human_query)
            if discovered_key:
                product_key = discovered_key
                bundle = build_product_seo_bundle(db, audit_engine, social_service, product_key)
    if not bundle:
        raise HTTPException(status_code=404, detail="Ürün sayfası bulunamadı.")
    return HTMLResponse(
        render_seo_hydrated_spa(
            _read_index_template(),
            title=bundle["title"],
            description=bundle["description"],
            canonical_url=bundle["canonical"],
            json_ld_objects=bundle["json_ld_list"],
            ssr_html=bundle["ssr_html"],
            initial_product_key=product_key,
            initial_view="xray",
            price_amount=bundle["price_amount"],
        ),
        headers=NO_CACHE_HTML_HEADERS,
    )


@app.get("/barkod/{gtin}", response_class=HTMLResponse)
def serve_barcode_seo_page(gtin: str, request: Request):
    matched = db.lookup_by_barcode(gtin)
    target_key = matched["product_key"] if matched else ""
    target_gtin = matched["gtin"] if matched else "".join(ch for ch in gtin if ch.isdigit())

    if not target_key and 8 <= len(target_gtin) <= 14 and _discovery_allowed(request):
        discovered_key = auto_catalog.discover_and_persist_query(detected_gtin=target_gtin)
        if discovered_key:
            target_key = discovered_key

    if not target_key:
        raise HTTPException(status_code=404, detail="Barkod kaydı bulunamadı.")

    bundle = build_product_seo_bundle(
        db, audit_engine, social_service, target_key, focused_gtin=target_gtin
    )
    if not bundle:
        raise HTTPException(status_code=404, detail="Ürün verisi bulunamadı.")
    return HTMLResponse(
        render_seo_hydrated_spa(
            _read_index_template(),
            title=bundle["title"],
            description=bundle["description"],
            canonical_url=bundle["canonical"],
            json_ld_objects=bundle["json_ld_list"],
            ssr_html=bundle["ssr_html"],
            initial_product_key=target_key,
            initial_view="xray",
            price_amount=bundle["price_amount"],
        ),
        headers=NO_CACHE_HTML_HEADERS,
    )


@app.get("/kategori/{cat_slug}", response_class=HTMLResponse)
def serve_category_seo_page(cat_slug: str):
    db_cat = cat_slug_to_db(cat_slug)
    if not db_cat:
        raise HTTPException(status_code=404, detail="Kategori bulunamadı.")
    meta = CATEGORY_META[db_cat]
    first_prod = db.conn.execute(
        "SELECT product_key FROM canonical_products WHERE category = ? AND is_private_label = 0 LIMIT 1",
        (db_cat,),
    ).fetchone()
    return HTMLResponse(
        render_seo_hydrated_spa(
            _read_index_template(),
            title=f"{meta['title']} | dipci.tr",
            description=meta["desc"],
            canonical_url=f"{CANONICAL_ORIGIN}/kategori/{meta['slug']}",
            json_ld_objects=build_organization_and_website_schema(),
            initial_product_key=first_prod["product_key"] if first_prod else None,
            initial_view="vitrin",
        ),
        headers=NO_CACHE_HTML_HEADERS,
    )


@app.get("/firsatlar", response_class=HTMLResponse)
def serve_deals_seo_page():
    return HTMLResponse(
        render_seo_hydrated_spa(
            _read_index_template(),
            title="Sıcak Fırsatlar & Doğrulanmış İndirim Kodu Akışı | dipci.tr",
            description="SMS doğrulamalı üyelerin yakaladığı ve Market Dedektifi algoritmasının birim fiyat kontrolünden geçen gerçek indirimler.",
            canonical_url=f"{CANONICAL_ORIGIN}/firsatlar",
            json_ld_objects=build_organization_and_website_schema(),
            initial_view="deals",
        ),
        headers=NO_CACHE_HTML_HEADERS,
    )


@app.get("/denetim", response_class=HTMLResponse)
def serve_denetim_page():
    """Denetim raporu (Kemal, 18 Kas 2026): görselsiz ürün, yanlış kampanya, şüpheli karşılaştırma. Arama motoruna kapalı."""
    import html as _h
    yol = Path(__file__).resolve().parent / "data" / "denetim_raporu.json"
    if not yol.exists():
        return HTMLResponse("<p>Denetim henüz çalışmadı.</p>")
    r = json.loads(yol.read_text(encoding="utf-8"))
    e = _h.escape

    def link(key, ad):
        return f'<a href="/urun/{e(key_to_slug(key))}" target="_blank">{e(ad)}</a>'

    g, k, c = r["gorsel"], r["kampanya"], r["karsilastirma"]
    kart = lambda baslik, sayi, alt, renk: (
        f'<div class="kart"><div class="b">{baslik}</div><div class="s" style="color:{renk}">{sayi}</div><div class="a">{alt}</div></div>')
    satir_k = "".join(f"<tr><td>{link(x['key'], x['urun'])}</td><td>{e(x['magaza'])}</td><td>{x['eski']:.2f} → {x['yeni']:.2f}</td>"
                      f"<td>{e(x['neden'])}</td></tr>" for x in k["liste"])
    satir_c = "".join(f"<tr><td>{link(x['key'], x['urun'])}</td><td>{x['en_ucuz']:.2f} ({e(x['en_ucuz_magaza'])})</td>"
                      f"<td>{x['en_pahali']:.2f} ({e(x['en_pahali_magaza'])})</td><td>{e(x['neden'])}</td></tr>" for x in c["liste"])
    satir_g = "".join(f"<li>{link(x['key'], x['urun'])}</li>" for x in g["ornek"])
    sayfa = f"""<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>Denetim · dipci.tr</title>
<style>body{{font-family:Inter,system-ui,sans-serif;background:#f8fafc;color:#0f172a;margin:0;padding:16px}}main{{max-width:1100px;margin:auto}}
h1{{font-size:22px}}h2{{font-size:17px;margin-top:28px}}.kartlar{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}}
.kart{{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:14px}}.b{{font-size:13px;color:#475569}}.s{{font-size:28px;font-weight:700}}
.a{{font-size:12px;color:#64748b}}table{{width:100%;border-collapse:collapse;background:#fff;font-size:13px}}td,th{{border-bottom:1px solid #e2e8f0;padding:7px;text-align:left;vertical-align:top}}
.kap{{overflow-x:auto;border:1px solid #e2e8f0;border-radius:12px}}a{{color:#1d4ed8}}</style></head><body><main>
<h1>dipci denetim raporu</h1><p class="a">Son denetim: {e(r['zaman'])} · saatte bir kendiliğinden çalışır</p>
<div class="kartlar">
{kart("Görselsiz ürün", g["gorselsiz"], f"{g['toplam_urun']:,} üründe %{g['oran_yuzde']}".replace(",", "."), "#b45309" if g["gorselsiz"] else "#047857")}
{kart("Yanlış kampanya", k["yanlis"], f"{k['toplam_kampanya']} kampanyadan; şeritten çıkarıldı", "#b91c1c" if k["yanlis"] else "#047857")}
{kart("Şüpheli karşılaştırma", c["supheli"], f"{c['karsilastirilan_urun']} karşılaştırmada %{c['oran_yuzde']}", "#b91c1c" if c["supheli"] else "#047857")}
</div>
<h2>Yanlış kampanyalar</h2><div class="kap"><table><tr><th>Ürün</th><th>Mağaza</th><th>Eski → yeni</th><th>Neden</th></tr>{satir_k or '<tr><td colspan=4>Yok</td></tr>'}</table></div>
<h2>Şüpheli karşılaştırmalar</h2><div class="kap"><table><tr><th>Ürün</th><th>En ucuz</th><th>En pahalı</th><th>Neden</th></tr>{satir_c or '<tr><td colspan=4>Yok</td></tr>'}</table></div>
<h2>Görselsiz ürünler (ilk 30)</h2><ul>{satir_g or '<li>Yok</li>'}</ul>
</main></body></html>"""
    return HTMLResponse(sayfa, headers=NO_CACHE_HTML_HEADERS)


@app.get("/koli", response_class=HTMLResponse)
def serve_koli_page():
    return HTMLResponse(
        render_seo_hydrated_spa(
            _read_index_template(),
            title="Koli ve Toplu Alım Fiyatları — Tanesi Kaça Geliyor? | dipci.tr",
            description="Koli ve çoklu paket satışlarının tane fiyatı, aynı ürünün en ucuz tekli fiyatıyla karşılaştırılır: avantajlı koliler yeşil, pahalı olanlar kırmızı.",
            canonical_url=f"{CANONICAL_ORIGIN}/koli",
            json_ld_objects=build_organization_and_website_schema(),
            initial_view="koli",
        ),
        headers=NO_CACHE_HTML_HEADERS,
    )


@app.get("/api/koli")
def api_koli(filtre: str = "hepsi", category: str = "", limit: int = 300):
    """Koli/çoklu satış incelemesi (Kemal, 18 Kas 2026): her kolinin tane fiyatı, aynı ürünün en ucuz tekli
    satışıyla karşılaştırılır. Yeşil = koli avantajlı, kırmızı = koli pahalı."""
    try:
        rows = db.conn.execute(
            """SELECT k.product_key, k.platform, k.seller_name, k.title, k.price, k.adet, k.birim_fiyat, k.url,
                      i.brand, i.family_name, i.image_url, i.category, i.best_pack_price, i.best_seller_name
               FROM koli_teklifleri k JOIN product_catalog_index i ON i.product_key = k.product_key
               WHERE k.captured_at >= date('now', '-14 days') AND i.best_pack_price > 0"""
            + (" AND i.category = ?" if category else ""),
            ((category,) if category else ()),
        ).fetchall()
    except Exception:
        rows = []
    out = []
    for r in rows:
        tekli = float(r["best_pack_price"])
        if not 0.4 <= float(r["birim_fiyat"]) / tekli <= 2.0:  # bu kadar uç fark koli değil yanlış eşleşmedir
            continue
        fark = round(tekli - float(r["birim_fiyat"]), 2)  # + : koli tanesi daha ucuz
        oran = round(fark / tekli * 100, 1) if tekli else 0.0
        durum = "ucuz" if fark > 0.01 else "pahali"
        if filtre in ("ucuz", "pahali") and durum != filtre:
            continue
        out.append({
            "product_key": r["product_key"], "brand": r["brand"], "family_name": r["family_name"],
            "image_url": r["image_url"] or "", "category": r["category"], "seller_name": r["seller_name"] or r["platform"],
            "title": r["title"], "koli_fiyat": r["price"], "adet": r["adet"], "tane_fiyat": r["birim_fiyat"],
            "tekli_en_ucuz": tekli, "tekli_satici": r["best_seller_name"] or "", "tane_fark": fark, "fark_yuzde": oran,
            "durum": durum, "seo_url": f"/urun/{key_to_slug(r['product_key'])}",
            "tracked_url": affiliate_builder.build_tracking_url(r["url"], sub_id="dipci_koli") if r["url"] else "",
        })
    out.sort(key=lambda x: (0 if x["durum"] == "ucuz" else 1, -abs(x["fark_yuzde"])))
    return {"ok": True, "toplam": len(out), "ucuz": sum(1 for x in out if x["durum"] == "ucuz"),
            "pahali": sum(1 for x in out if x["durum"] == "pahali"), "koliler": out[: max(1, min(int(limit), 600))]}


@app.get("/topluluk", response_class=HTMLResponse)
def serve_topluluk_page():
    return HTMLResponse(
        render_seo_hydrated_spa(
            _read_index_template(),
            title="Topluluk — Gördüğün Fiyatı Paylaş, Fırsatı Duyur | dipci.tr",
            description="Onaylı dipci üyeleri markette ve mağazada gördükleri fiyatları paylaşıyor, fırsatları duyuruyor, ürünlere yorum yazıyor.",
            canonical_url=f"{CANONICAL_ORIGIN}/topluluk",
            json_ld_objects=build_organization_and_website_schema(),
            initial_view="topluluk",
        ),
        headers=NO_CACHE_HTML_HEADERS,
    )


@app.get("/sepet", response_class=HTMLResponse)
def serve_basket_seo_page():
    return HTMLResponse(
        render_seo_hydrated_spa(
            _read_index_template(),
            title="Akıllı Market Sepeti Karşılaştırma — Hangi Market Daha Ucuz? | dipci.tr",
            description="Alışveriş listenizi BİM, A101, ŞOK, Migros, CarrefourSA, Tarım Kredi, Amazon ve Hepsiburada'da tek tıkla karşılaştırın; tek market şampiyonunu ve bölünmüş dip rotayı görün.",
            canonical_url=f"{CANONICAL_ORIGIN}/sepet",
            json_ld_objects=build_organization_and_website_schema(),
            initial_view="basket",
        ),
        headers=NO_CACHE_HTML_HEADERS,
    )


@app.get("/kendi-sepetim", response_class=HTMLResponse)
def serve_custom_basket_seo_page():
    return HTMLResponse(
        render_seo_hydrated_spa(
            _read_index_template(),
            title="Kendi Markalı Sepetini Oluştur — Bulunurluk Sıralaması & Muadil Hesaplama | dipci.tr",
            description="Peynir, süt, şeker, çay... Marketlerde en çok bulunan markaları seçerek sepetinizi kurun; aynı marka olmayan marketlerde şeffaf muadil tamamlamayla tam kasa tutarlarını kıyaslayın.",
            canonical_url=f"{CANONICAL_ORIGIN}/kendi-sepetim",
            json_ld_objects=build_organization_and_website_schema(),
            initial_view="custom_basket",
        ),
        headers=NO_CACHE_HTML_HEADERS,
    )


@app.get("/lens", response_class=HTMLResponse)
def serve_lens_seo_page():
    return HTMLResponse(
        render_seo_hydrated_spa(
            _read_index_template(),
            title="Dipçi Lens — Kamerayla veya Fotoğraftan Ürün Tanı & En Ucuz Fiyatı Bul | dipci.tr",
            description="Elinizdeki ürünün fotoğrafını çekin, görsel yükleyin veya barkodunu okutun; ürün bilgilerini ve satıldığı tüm mağazaları en düşük fiyata göre anında görün.",
            canonical_url=f"{CANONICAL_ORIGIN}/lens",
            json_ld_objects=build_organization_and_website_schema(),
            initial_view="scanner",
        ),
        headers=NO_CACHE_HTML_HEADERS,
    )


@app.get("/api/basinca/reklamlar")
def api_basinca_reklamlar(tur: str = "karisik", limit: int = 12):
    """basinca.com akışı için dipci.tr reklamları (Kemal, 17 Kas 2026).
    tur: 8 reklam kodundan biri (bkz. /api/basinca/reklam-kodlari) ya da karisik (hepsi sırayla)."""
    import basinca_ads
    limit = max(1, min(int(limit or 12), 40))
    # Her istekte farklı ürünler, karışık sırayla (dönüşümlü gösterim).
    items = basinca_ads.rotating_ads(db, fast_radar, tur, limit)
    return JSONResponse(
        {"ok": True, "uretildi": datetime.now().isoformat(timespec="seconds"), "gorsel_olcu": "900x1200 (3:4 dikey)", "items": items},
        headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"},
    )


DEDEKTIF_DIR = BASE_DIR / "data" / "dedektif"


@app.get("/api/dedektif/kart/{product_key}")
def api_dedektif_urun_karti(product_key: str):
    """Market Dedektifi yazısındaki ürün kartı: görsel + 3-5 mağaza fiyat sıralaması (Kemal, 17 Kas 2026)."""
    import basinca_ads
    r = db.conn.execute("SELECT * FROM product_catalog_index WHERE product_key = ?", (product_key[:160],)).fetchone()
    if not r:
        return JSONResponse({"ok": False}, status_code=404)
    quotes = basinca_ads._clean_quotes(json.loads(r["top_quotes_json"] or "[]"))
    magazalar = []
    for q in quotes:
        ad, renk, yazi = basinca_ads.BRANDS.get(q["platform"], basinca_ads.DEFAULT_BRAND)
        magazalar.append({"magaza": ad, "fiyat": q["pack_price"], "fiyat_yazi": basinca_ads._tl(q["pack_price"]), "renk": renk, "yazi_rengi": yazi})
    return JSONResponse(
        {
            "ok": bool(magazalar),
            "baslik": f"{r['brand']} {r['family_name']}".strip(),
            "gorsel": r["image_url"] or "",
            "link": f"https://dipci.tr/urun/{product_key.replace('_', '-')}?utm_source=basinca&utm_medium=market_dedektifi",
            "magazalar": magazalar,
        },
        headers={"Access-Control-Allow-Origin": "*"},
    )


def _son_dedektif_yazisi() -> Optional[dict]:
    try:
        dosyalar = sorted(DEDEKTIF_DIR.glob("*.json"))
    except Exception:
        return None
    if not dosyalar:
        return None
    return json.loads(dosyalar[-1].read_text(encoding="utf-8"))


@app.get("/api/dedektif/gunun-yazisi")
def api_dedektif_gunun_yazisi():
    """basinca.com Market Dedektifi köşesi her gün bunu çeker: başlık, spot, hazır HTML gövde (Kemal, 17 Kas 2026)."""
    y = _son_dedektif_yazisi()
    if not y:
        return JSONResponse({"ok": False, "hata": "Henüz yazı üretilmedi"}, status_code=404)
    if y.get("basinca_id"):
        # dipci yazıyı basınca köşesine kendisi ekledi; basınca'nın çekme işi ikinci kez basmasın.
        return JSONResponse({"ok": False, "not": "dipci kendisi yayınladı", "basinca_id": y["basinca_id"]},
                            headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"})
    return JSONResponse(
        {
            "ok": True,
            "yazar": y.get("yazar") or "Market Dedektifi",
            "kose": y.get("kose") or "Market Dedektifi",
            "tarih": y.get("uretim_zamani"),
            "baslik": y.get("baslik"),
            "spot": y.get("ozet_spot"),
            "html": y.get("html"),
            "sosyal_medya": y.get("sosyal_medya"),
        },
        headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"},
    )


@app.get("/dedektif/gunun-yazisi", response_class=HTMLResponse)
def serve_dedektif_onizleme():
    y = _son_dedektif_yazisi()
    if not y:
        return HTMLResponse("<p>Henüz yazı üretilmedi.</p>", status_code=404)
    sayfa = (
        '<!DOCTYPE html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Market Dedektifi önizleme</title></head><body style=\"margin:0;padding:20px;background:#F8FAFC\">"
        f"<p style=\"font-family:Arial;color:#64748B;font-size:13px\">basinca.com Market Dedektifi köşesine gidecek günün yazısı (önizleme) — {y.get('uretim_zamani', '')[:16]}</p>"
        f"{y.get('html', '')}</body></html>"
    )
    return HTMLResponse(sayfa, headers=NO_CACHE_HTML_HEADERS)


# ——— Ziyaretçi ölçümü ve yönetim paneli (Kemal, 17 Kas 2026) ———
class ZiyaretPayload(BaseModel):
    p: str = "/"
    r: str = ""
    u: str = ""


@app.post("/api/z")
def api_ziyaret(payload: ZiyaretPayload, request: Request):
    import ziyaret
    ip = _client_ip(request)
    if _limiter.allow(f"z:{ip}", 120, 60):
        try:
            ziyaret.kaydet(ip, request.headers.get("user-agent", ""), payload.p, payload.r[:300], payload.u)
        except Exception:
            pass
    return Response(status_code=204)


def _yonetici_mi(token: str) -> bool:
    uye = social_service.get_member_by_token(token or "")
    izinli = {x.strip() for x in os.getenv("ADMIN_MEMBER_IDS", "").split(",") if x.strip()}
    return bool(uye and str(uye.get("member_id")) in izinli)


@app.get("/api/yonetim/trafik")
def api_yonetim_trafik(aralik: str = "gun", x_session_token: Optional[str] = Header(default=None)):
    import ziyaret
    if not _yonetici_mi(x_session_token or ""):
        return JSONResponse({"ok": False, "hata": "yetki yok"}, status_code=403)
    return JSONResponse(ziyaret.rapor(aralik if aralik in ("gun", "hafta", "ay") else "gun"), headers={"Cache-Control": "no-store"})


@app.get("/yonetim", response_class=HTMLResponse)
def serve_yonetim():
    return HTMLResponse((STATIC_DIR / "yonetim.html").read_text(encoding="utf-8"), headers=NO_CACHE_HTML_HEADERS)


@app.get("/reklam/kutu/{tur}", response_class=HTMLResponse)
def serve_reklam_kutusu(tur: str, sure: int = 7):
    """basınca'ya bir kez gömülen dipci reklam kutusu (Kemal, 17 Kas 2026).
    Gösterilecek ürünü dipci seçer: ürünleri sırayla döndürür, 1 dakikada bir yeniler; olağanüstü fiyat düşüşü
    ya da yeni kampanya çıkınca hemen ona geçer. basınca tarafında kod değişmez."""
    import basinca_ads
    if tur not in basinca_ads.KINDS and tur != "karisik":
        raise HTTPException(status_code=404, detail="Bulunamadı")
    sure = max(4, min(int(sure or 7), 60))
    sayfa = """<!DOCTYPE html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>dipci.tr</title><style>
html,body{margin:0;height:100%;background:#fff;overflow:hidden}
a{display:block;width:100%;height:100%}
img{display:block;width:100%;height:100%;object-fit:contain;transition:opacity .3s ease}
img.g{opacity:0}
</style></head><body><a id="a" href="https://dipci.tr" target="_blank" rel="noopener"><img id="i" alt="dipci.tr"></a>
<script>
(function(){
var TUR=__TUR__, SURE=__SURE__*1000, liste=[], i=0, gorulen={}, zaman=null;
var a=document.getElementById("a"), im=document.getElementById("i");
function goster(it){ if(!it) return; im.classList.add("g");
  var y=new Image(); y.onload=function(){ im.src=it.gorsel; im.alt=it.baslik; a.href=it.link; im.classList.remove("g"); };
  y.src=it.gorsel; }
function sonraki(){ if(!liste.length) return; i=(i+1)%liste.length; goster(liste[i]); }
function yukle(ilk){
  fetch("/api/basinca/reklamlar?tur="+encodeURIComponent(TUR)+"&limit=12",{cache:"no-store"}).then(function(r){return r.json();}).then(function(d){
    var yeni=(d&&d.items)||[]; if(!yeni.length) return;
    var acil=null;
    yeni.forEach(function(it){ if(it.acil && !gorulen[it.id]) acil=acil||it; });
    yeni.forEach(function(it){ gorulen[it.id]=1; });
    liste=yeni;
    if(ilk||acil){ i=acil?liste.indexOf(acil):0; goster(liste[i]); clearInterval(zaman); zaman=setInterval(sonraki,SURE); }
  }).catch(function(){});
}
yukle(true); setInterval(function(){yukle(false);},60000);
})();
</script></body></html>""".replace("__TUR__", json.dumps(tur)).replace("__SURE__", str(sure))
    return HTMLResponse(sayfa, headers={"Cache-Control": "no-store"})


@app.get("/reklam/kart/{tur}.png")
def serve_reklam_simdiki_kart(tur: str):
    """Sabit görsel adresi: her istekte dipci'nin o an seçtiği ürünün kartı (20 sn'lik dilimle döner)."""
    import basinca_ads
    if tur not in basinca_ads.KINDS:
        raise HTTPException(status_code=404, detail="Bulunamadı")
    it = basinca_ads.current_slot(db, tur)
    if not it:
        raise HTTPException(status_code=404, detail="Bulunamadı")
    png = basinca_ads.render_card_png(db, fast_radar, tur, it["id"][len(f"dipci-{tur}-"):])
    if not png:
        raise HTTPException(status_code=404, detail="Bulunamadı")
    return Response(png, media_type="image/png", headers={"Cache-Control": "no-store", "Access-Control-Allow-Origin": "*"})


@app.get("/reklam/git/{tur}")
def serve_reklam_simdiki_link(tur: str):
    """Sabit tıklama adresi: o an gösterilen ürünün dipci sayfasına yönlendirir."""
    import basinca_ads
    from fastapi.responses import RedirectResponse
    it = basinca_ads.current_slot(db, tur) if tur in basinca_ads.KINDS else None
    return RedirectResponse(it["link"] if it else "https://dipci.tr/?utm_source=basinca", status_code=302, headers={"Cache-Control": "no-store"})


@app.get("/reklam/basinca.js")
def serve_basinca_reklam_js():
    """basinca.com'a yapıştırılan canlı reklam kodu: ürünü ve fiyatları her açılışta dipci.tr'den çeker."""
    return FileResponse(
        STATIC_DIR / "basinca-reklam.js",
        media_type="application/javascript; charset=utf-8",
        headers={"Cache-Control": "public, max-age=300", "Access-Control-Allow-Origin": "*"},
    )


@app.get("/reklam/basinca-ornek", response_class=HTMLResponse)
def serve_basinca_reklam_ornek():
    """8 reklam kodunun canlı önizlemesi (basınca kart ölçüsü, 3:4 dikey)."""
    import basinca_ads
    kartlar = "".join(
        f'<figure><div class="dipci-reklam" data-tur="{k}"></div><figcaption><b>{v[0]}</b><br>'
        f'<code>&lt;div class="dipci-reklam" data-tur="{k}"&gt;&lt;/div&gt;</code></figcaption></figure>'
        for k, v in basinca_ads.KINDS.items()
    )
    html = (
        '<!DOCTYPE html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>dipci.tr reklam kodları | basinca.com</title><style>"
        "body{margin:0;background:#0A0F1A;color:#E2E8F0;font-family:Arial,sans-serif;padding:24px}"
        "h1{font-size:20px}p{color:#94A3B8;font-size:14px}"
        ".izgara{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:20px;margin-top:20px}"
        "figure{margin:0}figcaption{font-size:12px;color:#94A3B8;margin-top:8px;line-height:1.6}figcaption b{color:#F8FAFC}"
        "code{color:#FACC15;font-size:11px}pre{background:#111827;padding:12px;border-radius:8px;color:#FACC15;overflow:auto}"
        "</style></head><body><h1>dipci.tr → basinca.com canlı reklam kodları (8 kod)</h1>"
        "<p>Her kart açılışta dipci.tr'den güncel ürünü ve fiyatları çeker; sayfayı yenileyince farklı ürün gelir.</p>"
        '<pre>&lt;div class="dipci-reklam" data-tur="sok"&gt;&lt;/div&gt;<br>&lt;script src="https://dipci.tr/reklam/basinca.js" async&gt;&lt;/script&gt;</pre>'
        f'<div class="izgara">{kartlar}</div><script src="/reklam/basinca.js" async></script></body></html>'
    )
    return HTMLResponse(html, headers=NO_CACHE_HTML_HEADERS)


@app.get("/api/basinca/reklam-kodlari")
def api_basinca_reklam_kodlari():
    import basinca_ads
    return JSONResponse(
        {
            "ok": True,
            "gorsel_olcu": "900x1200 (3:4 dikey)",
            "kodlar": [
                {"kod": k, "etiket": v[0], "aciklama": v[1], "akis": f"https://dipci.tr/api/basinca/reklamlar?tur={k}&limit=12"}
                for k, v in basinca_ads.KINDS.items()
            ],
        },
        headers={"Access-Control-Allow-Origin": "*"},
    )


@app.get("/reklam/basinca/{tur}/{product_key}.png")
def serve_basinca_reklam_gorseli(tur: str, product_key: str):
    import basinca_ads
    if tur not in basinca_ads.KINDS or len(product_key) > 160:
        raise HTTPException(status_code=404, detail="Bulunamadı")
    png = basinca_ads.render_card_png(db, fast_radar, tur, product_key)
    if not png:
        raise HTTPException(status_code=404, detail="Bulunamadı")
    return Response(png, media_type="image/png", headers={"Cache-Control": "public, max-age=1800", "Access-Control-Allow-Origin": "*"})


@app.get("/gizlilik", response_class=HTMLResponse)
def serve_privacy_page():
    from seo_engine import build_legal_page_html
    return HTMLResponse(build_legal_page_html("gizlilik"), headers=NO_CACHE_HTML_HEADERS)


@app.get("/kullanim-kosullari", response_class=HTMLResponse)
def serve_terms_page():
    from seo_engine import build_legal_page_html
    return HTMLResponse(build_legal_page_html("kullanim-kosullari"), headers=NO_CACHE_HTML_HEADERS)


@app.get("/kurumsal", response_class=HTMLResponse)
def serve_corporate_page():
    page = build_corporate_identity_html().replace(
        'href="/static/app.css"', f'href="/static/app.css?v={_asset_version("app.css")}"'
    )
    return HTMLResponse(page, headers=NO_CACHE_HTML_HEADERS)


@app.get("/favicon.ico")
def serve_favicon():
    return FileResponse(str(STATIC_DIR / "brand" / "favicon.ico"), media_type="image/x-icon")


@app.get("/robots.txt", response_class=PlainTextResponse)
def serve_robots():
    return PlainTextResponse(build_robots_txt())


@app.get("/llms.txt", response_class=PlainTextResponse)
def serve_llms_txt():
    return PlainTextResponse(build_llms_txt(db))


@app.get(f"/{INDEXNOW_KEY}.txt", response_class=PlainTextResponse)
def serve_indexnow_key():
    return PlainTextResponse(INDEXNOW_KEY)


@app.get("/sitemap.xml")
def serve_sitemap_index():
    return Response(content=build_sitemap_index_xml(db), media_type="application/xml")


@app.get("/sitemap-pages.xml")
def serve_sitemap_pages():
    return Response(content=build_pages_sitemap_xml(), media_type="application/xml")


@app.get("/sitemap-products.xml")
def serve_sitemap_products():
    return Response(content=build_products_sitemap_xml(db, page=1), media_type="application/xml")


@app.get("/sitemap-products-{page}.xml")
def serve_sitemap_products_paged(page: int):
    return Response(content=build_products_sitemap_xml(db, page=max(1, page)), media_type="application/xml")


@app.get("/sitemap-barcodes.xml")
def serve_sitemap_barcodes():
    return Response(content=build_barcodes_sitemap_xml(db, page=1), media_type="application/xml")


@app.get("/sitemap-barcodes-{page}.xml")
def serve_sitemap_barcodes_paged(page: int):
    return Response(content=build_barcodes_sitemap_xml(db, page=max(1, page)), media_type="application/xml")


@app.get("/sitemap-categories.xml")
def serve_sitemap_categories():
    return Response(content=build_categories_sitemap_xml(), media_type="application/xml")


# ----------------- Katalog & 7 Katmanlı Ürün Röntgeni API -----------------

def _norm_search_tr(text: str) -> str:
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


_LISTE_ONBELLEK: dict = {}
_FACET_ONBELLEK: dict = {}


def _onbellek_al(depo: dict, anahtar, sure: float):
    kayit = depo.get(anahtar)
    if kayit and time.time() - kayit[0] < sure:
        return kayit[1]
    return None


def _onbellek_koy(depo: dict, anahtar, deger):
    if len(depo) > 500:
        depo.clear()
    depo[anahtar] = (time.time(), deger)
    return deger


@app.get("/api/products")
def list_products(
    request: Request,
    q: str = "",
    category: str = "",
    brand: str = "",
    store: str = "",
    min_price: float = 0.0,
    max_price: float = 0.0,
    only_drops: int = 0,
    min_stores: int = 0,
    sort: str = "ihtiyac",
    page: int = 1,
    limit: int = 72,
    live: int = 0,
):
    # Arama yoksa yanıt 2 dk önbellekte (Kemal, 18 Kas 2026: "site çok yavaş" — katalog 350 bin ürüne çıktı)
    onbellek_anahtar = None
    if not (q or "").strip() and not live:
        onbellek_anahtar = (category, brand, store, min_price, max_price, only_drops, min_stores, sort, page, limit)
        hazir = _onbellek_al(_LISTE_ONBELLEK, onbellek_anahtar, 120)
        if hazir is not None:
            return hazir
    safe_limit = max(12, min(int(limit or 72), 240))
    safe_page = max(1, int(page or 1))
    offset = (safe_page - 1) * safe_limit

    q_raw = (q or "").strip()
    q_norm = _norm_search_tr(q_raw)
    q_tokens = [t for t in q_norm.split() if t]
    brand_clean = (brand or "").strip()
    store_clean = (store or "").strip().lower()

    def _build_where(include_brand_and_store: bool = True):
        clauses = ["offer_count >= 1"]
        params: list = []
        if category:
            clauses.append("category = ?")
            params.append(category)
        if q_tokens:
            for tok in q_tokens:
                clauses.append("search_norm LIKE ?")
                params.append(f"%{tok}%")
        if only_drops == 1:
            clauses.append("is_real_drop = 1")
        if min_stores >= 2:
            clauses.append("offer_count >= ?")
            params.append(int(min_stores))
        if min_price > 0:
            clauses.append("best_pack_price >= ?")
            params.append(float(min_price))
        if max_price > 0:
            clauses.append("best_pack_price <= ?")
            params.append(float(max_price))
        if include_brand_and_store:
            if brand_clean:
                clauses.append("brand = ? COLLATE NOCASE")
                params.append(brand_clean)
            if store_clean:
                clauses.append("platforms_csv LIKE ?")
                params.append(f"%,{store_clean},%")
        return " AND ".join(clauses), params

    where_sql, where_params = _build_where(True)
    total_matching = db.conn.execute(
        f"SELECT COUNT(*) AS c FROM product_catalog_index WHERE {where_sql}",
        where_params,
    ).fetchone()["c"]

    # Katalogda henüz olmayan bir ürün arandıysa canlı mağazalardan keşfet ve kalıcı sayfa aç
    if (
        q_raw
        and 3 <= len(q_raw) <= 120
        # Canlı mağaza taraması yalnız katalogda hiç sonuç yoksa (her Enter'da taramak aramayı yavaşlatıyordu, 19 Kas 2026)
        and (total_matching == 0 and not brand_clean and not store_clean)
        and _discovery_allowed(request)
    ):
        gtin_arg = q_raw if q_raw.isdigit() and len(q_raw) >= 8 else ""
        new_pkey = auto_catalog.discover_and_persist_query(query=q_raw, detected_gtin=gtin_arg)
        if new_pkey:
            fast_radar.detect_and_publish_drops()
            where_sql, where_params = _build_where(True)
            total_matching = db.conn.execute(
                f"SELECT COUNT(*) AS c FROM product_catalog_index WHERE {where_sql}",
                where_params,
            ).fetchone()["c"]

    order_map = {
        # Ana sayfa: çok alınan/aranan temel ihtiyaçlar önde, aynı gruptakiler 3 saatte bir yer değiştirir (Kemal, 17 Kas 2026)
        # Talep/karışım hesabı yalnız çok mağazalı ürünlerde yapılır (tüm katalogda yapmak sayfayı 15 sn'ye çıkarıyordu)
        "ihtiyac": "CASE WHEN offer_count >= 2 THEN 1 ELSE 0 END DESC, CASE WHEN COALESCE(image_url, '') <> '' THEN 1 ELSE 0 END DESC, CASE WHEN offer_count >= 2 THEN dipci_talep(search_norm, category) / 10 END DESC, CASE WHEN offer_count >= 2 THEN dipci_karisim(product_key) END, savings_pct DESC",
        "savings": "CASE WHEN offer_count >= 2 THEN 1 ELSE 0 END DESC, savings_pct DESC, offer_count DESC",
        "drops": "is_real_drop DESC, drop_vs_median_pct DESC, savings_pct DESC",
        "price_asc": "best_pack_price ASC, offer_count DESC",
        "price_desc": "best_pack_price DESC, offer_count DESC",
        "offers": "offer_count DESC, savings_pct DESC",
        "score": "detective_score DESC, savings_pct DESC",
    }
    order_sql = order_map.get(sort, order_map["ihtiyac"])

    page_rows = db.conn.execute(
        f"""
        SELECT *
        FROM product_catalog_index
        WHERE {where_sql}
        ORDER BY {order_sql}
        LIMIT ? OFFSET ?
        """,
        where_params + [safe_limit, offset],
    ).fetchall()

    # Dinamik marka, kategori ve gerçek indirim sayıları (hızlı tekil indeks üzerinden)
    facet_where, facet_params = _build_where(False)
    facet_anahtar = (facet_where, tuple(facet_params))
    facet_hazir = _onbellek_al(_FACET_ONBELLEK, facet_anahtar, 600)
    brand_facets = facet_hazir[0] if facet_hazir else [
        {"brand": br["brand"], "count": int(br["c"])}
        for br in db.conn.execute(
            f"""
            SELECT brand, COUNT(*) AS c
            FROM product_catalog_index
            WHERE {facet_where}
            GROUP BY brand
            ORDER BY c DESC, brand ASC
            LIMIT 40
            """,
            facet_params,
        ).fetchall()
    ]
    if facet_hazir:
        cat_counts, global_stats = facet_hazir[1], facet_hazir[2]
    else:
        cat_counts, global_stats = _genel_sayilar()
        _onbellek_koy(_FACET_ONBELLEK, facet_anahtar, (brand_facets, cat_counts, global_stats))

    items = []
    for r in page_rows:
        items.append(_liste_ogesi(r))
    sonuc = {
        "items": items,
        "pagination": {
            "page": safe_page,
            "limit": safe_limit,
            "total_matching": int(total_matching),
            "has_more": (offset + len(items)) < int(total_matching),
        },
        "facets": {
            "brands": brand_facets,
            "category_counts": cat_counts,
        },
        "summary": {
            "total_products": int(total_matching),
            "catalog_total_products": int(global_stats["prod_cnt"] or len(items)),
            "total_live_quotes": int(global_stats["quote_cnt"] or 0),
            "real_drops_count": int(global_stats["drop_cnt"] or 0),
            "active_stores_count": 19,
            "max_savings_pct": float(global_stats["max_sav"] or 0.0),
        },
    }
    if onbellek_anahtar is not None:
        _onbellek_koy(_LISTE_ONBELLEK, onbellek_anahtar, sonuc)
    return sonuc


def _genel_sayilar():
    hazir = _onbellek_al(_FACET_ONBELLEK, "genel", 300)
    if hazir:
        return hazir
    cat_counts = {
        cr["category"]: int(cr["c"])
        for cr in db.conn.execute(
            "SELECT category, COUNT(*) AS c FROM product_catalog_index WHERE offer_count >= 1 GROUP BY category"
        ).fetchall()
    }
    g = db.conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM product_catalog_index WHERE offer_count >= 1) AS prod_cnt,
            (SELECT COALESCE(SUM(offer_count), 0) FROM product_catalog_index WHERE offer_count >= 1) AS quote_cnt,
            (SELECT COUNT(*) FROM product_catalog_index WHERE is_real_drop = 1 AND offer_count >= 2) AS drop_cnt,
            (SELECT COALESCE(MAX(savings_pct), 0) FROM product_catalog_index WHERE offer_count >= 2) AS max_sav
        """
    ).fetchone()
    return _onbellek_koy(_FACET_ONBELLEK, "genel", (cat_counts, dict(g)))


def _liste_ogesi(r) -> dict:
    raw_top = json.loads(r["top_quotes_json"] or "[]")
    top_quotes = [
        {
            "platform": tq["platform"],
            "seller_name": tq["seller_name"],
            "pack_price": tq["pack_price"],
            "unit_price": tq["unit_price"],
            "net_amount": tq["net_amount"],
            "tracked_url": affiliate_builder.build_tracking_url(tq.get("url") or "", sub_id="dipci_vitrin"),
        }
        for tq in raw_top
    ]
    return {
            "product_key": r["product_key"],
            "brand": r["brand"],
            "family_name": r["family_name"],
            "category": r["category"],
            "base_unit": r["base_unit"],
            "image_url": r["image_url"] or "",
            "barcodes": json.loads(r["barcodes_json"] or "[]"),
            "offer_count": int(r["offer_count"] or 0),
            "social_post_count": 0,
            "best_platform": r["best_platform"] or "",
            "best_seller_name": r["best_seller_name"] or "",
            "best_pack_price": float(r["best_pack_price"] or 0.0),
            "best_unit_price": float(r["best_unit_price"] or 0.0),
            "best_net_amount": float(r["best_net_amount"] or 1.0),
            "best_tracked_url": affiliate_builder.build_tracking_url(r["best_url"], sub_id="dipci_vitrin") if r["best_url"] else "",
            "worst_platform": r["worst_platform"] or "",
            "worst_pack_price": float(r["worst_pack_price"] or 0.0),
            "worst_unit_price": float(r["worst_unit_price"] or 0.0),
            "savings_pct": float(r["savings_pct"] or 0.0),
            "median_30d_pack_price": float(r["median_30d_pack_price"] or 0.0),
            "drop_vs_median_pct": float(r["drop_vs_median_pct"] or 0.0),
            "is_real_drop": bool(r["is_real_drop"]),
            "top_quotes": top_quotes,
            "verdict": r["verdict"] or "NORMAL_PIYASA_FIYATI",
            "detective_score": int(r["detective_score"] or 75),
        }


def _tazelik_iste(product_key: str) -> None:
    """Ziyaretçi bir ürüne baktı: online fiyatı 6 saatten eskiyse yenileme kuyruğuna al (dipci-tazele 5 dk'da bir işler)."""
    try:
        r = db.conn.execute(
            "SELECT 1 FROM online_tazelik WHERE product_key = ? AND son >= datetime('now', '-6 hours')", (product_key,)
        ).fetchone()
        if not r:
            db.conn.execute("INSERT OR IGNORE INTO yenile_kuyrugu(product_key, istendi) VALUES (?, datetime('now'))", (product_key,))
            db.conn.commit()
    except sqlite3.Error:
        pass  # tablo henüz yok ya da veritabanı meşgul: ziyaretçiyi bekletme


@app.get("/api/products/{product_key}")
def get_product_xray(product_key: str, request: Request):
    # Kuyruğa yazma arka planda: yazma kilidi varken ziyaretçi 15 sn beklemesin (19 Kas 2026)
    threading.Thread(target=_tazelik_iste, args=(product_key,), daemon=True).start()
    prod = db.conn.execute(
        "SELECT * FROM canonical_products WHERE product_key = ?", (product_key,)
    ).fetchone()
    if not prod and len(product_key) <= 120 and _discovery_allowed(request):
        discovered = auto_catalog.discover_and_persist_query(query=product_key.replace("_", " "))
        if discovered:
            product_key = discovered
            prod = db.conn.execute(
                "SELECT * FROM canonical_products WHERE product_key = ?", (product_key,)
            ).fetchone()
    if not prod:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı.")

    quotes = db.get_latest_market_quotes(product_key)
    trusted = [q for q in quotes if q.seller_type in ("OFFICIAL", "AUTHORIZED") and q.seller_rating >= 8.8]
    ordered_trusted = sorted(trusted if trusted else quotes, key=lambda q: (q.effective_pack_price, q.effective_unit_price))
    target_q = ordered_trusted[0] if ordered_trusted else None
    finding = audit_engine.audit_quote(product_key, target_q) if target_q else None

    # 90 Günlük Tarihsel Noktalar (Grafik için)
    hist_rows = db.conn.execute(
        """
        SELECT day, unit_p FROM (
            SELECT substr(captured_at, 1, 10) AS day, MIN(effective_unit_price) AS unit_p
            FROM price_snapshots
            WHERE product_key = ? AND seller_type IN ('OFFICIAL', 'AUTHORIZED')
              AND seller_name NOT LIKE '%Referans%'
              AND captured_at >= date('now', '-90 days')
            GROUP BY substr(captured_at, 1, 10)
            ORDER BY day DESC
            LIMIT 15
        )
        ORDER BY day ASC
        """,
        (product_key,),
    ).fetchall()
    history_series = [{"date": r["day"], "unit_price": round(float(r["unit_p"]), 2)} for r in hist_rows]

    barcodes = [
        dict(b)
        for b in db.conn.execute(
            "SELECT gtin, variant_label, net_amount FROM product_barcodes WHERE product_key = ?",
            (product_key,),
        ).fetchall()
    ]

    offers_out = []
    for q in quotes:
        is_trusted = q.seller_type in ("OFFICIAL", "AUTHORIZED") and q.seller_rating >= 8.8
        npv_val = (
            audit_engine.calculate_installment_npv(q.effective_pack_price, q.zero_interest_months)
            if prod["category"] in UNITLESS_ITEM_CATEGORIES and q.zero_interest_months >= 2
            else q.effective_pack_price
        )
        offers_out.append(
            {
                "platform": q.platform,
                "seller_name": q.seller_name,
                "seller_type": q.seller_type,
                "seller_rating": q.seller_rating,
                "trusted": is_trusted,
                "title": q.title,
                "pack_price": q.effective_pack_price,
                "list_price": q.list_price,
                "unit_price": q.effective_unit_price,
                "npv_real_price": npv_val,
                "net_amount": q.net_amount,
                "base_unit": q.base_unit,
                "zero_interest_months": q.zero_interest_months,
                "tracked_url": affiliate_builder.build_tracking_url(q.url, sub_id="dipci_urun") if is_trusted else q.url,
            }
        )

    offers_out.sort(key=lambda x: (0 if x["trusted"] else 1, x["pack_price"], x["unit_price"]))

    # Toplu alım (koli) teklifleri: tekli fiyatlarla karışmaz, tanesi kaça geldiği gösterilir (Kemal, 18 Kas 2026)
    koli_out = []
    try:
        for r in db.conn.execute(
            """SELECT platform, seller_name, title, price, adet, birim_fiyat, url FROM koli_teklifleri
               WHERE product_key = ? AND captured_at >= date('now', '-14 days') ORDER BY birim_fiyat LIMIT 6""",
            (product_key,),
        ).fetchall():
            koli_out.append({**dict(r), "tracked_url": affiliate_builder.build_tracking_url(r["url"] or "", sub_id="dipci_koli")
                             if r["url"] else ""})
    except Exception:
        koli_out = []

    return {
        "product": dict(prod),
        "barcodes": barcodes,
        "audit": asdict(finding) if finding else None,
        "offers": offers_out,
        "koli": koli_out,
        "history_series": history_series,
        "vs_comparison": VS_COMPARISONS.get(product_key),
        "social_posts": social_service.list_product_posts(product_key),
    }


# ----------------- Kamera Barkod + AI Görsel + Sabotaj Kalkanı API -----------------

@app.post("/api/scan/barcode")
def api_scan_barcode(
    payload: BarcodeScanPayload,
    x_session_token: Optional[str] = Header(default=None),
):
    res = scanner_engine.scan_barcode(payload.gtin, shelf_price_tl=payload.shelf_price_tl)
    if not res:
        return JSONResponse(
            status_code=404,
            content={"ok": False, "error": "Bu barkod henüz katalogda yok; 'AI Görsel & Etiket' moduyla okutarak ilk kaydı açabilirsiniz."},
        )

    # Giriş yapmış doğrulamalı üye ise 'Ürünü Kamerayla Doğruladı' rozet hakkını işle!
    member = social_service.get_member_by_token(x_session_token or "")
    if member:
        social_service.record_member_camera_scan(member["member_id"], res.product_key)

    sabotage_audit = None
    if payload.save_crowd_shelf_tag and payload.shelf_price_tl:
        hist = db.get_historical_stats(res.product_key)
        guard_res = ShelfTagAntiSabotageGuard.verify(
            ShelfTagAuditInput(
                product_gtin=res.gtin or payload.gtin,
                label_printed_gtin=payload.label_printed_gtin or (res.gtin or payload.gtin),
                label_sale_price_tl=payload.shelf_price_tl,
                label_printed_unit_price=payload.label_printed_unit_price,
                net_amount=res.scanned_net_amount,
                user_trust_score=member["trust_score"] if member else 50.0,
            ),
            online_median_unit_price=hist.median_30d_unit_price,
        )
        sabotage_audit = asdict(guard_res)

    return {
        "ok": True,
        "camera_badge_unlocked": bool(member),
        "result": asdict(res),
        "sabotage_guard": sabotage_audit,
    }


@app.post("/api/scan/vision")
def api_scan_vision(
    payload: VisionScanPayload,
    x_session_token: Optional[str] = Header(default=None),
):
    obs = VisualObservation(
        brand=payload.brand,
        product_name=payload.product_name,
        net_amount_value=payload.net_amount_value,
        model_code=payload.model_code,
        gtin_visible=payload.gtin_visible,
        shelf_price_tl=payload.shelf_price_tl,
    )
    res = scanner_engine.scan_visual(obs)
    if not res:
        return JSONResponse(
            status_code=404,
            content={"ok": False, "error": "Görseldeki ürün eşleştirilemedi; marka veya model kodunu kontrol edin."},
        )

    member = social_service.get_member_by_token(x_session_token or "")
    if member:
        social_service.record_member_camera_scan(member["member_id"], res.product_key)

    return {
        "ok": True,
        "camera_badge_unlocked": bool(member),
        "result": asdict(res),
    }


@app.post("/api/scan/photo")
def api_scan_photo(
    payload: PhotoScanPayload,
    request: Request,
    x_session_token: Optional[str] = Header(default=None),
):
    _enforce_rate_limit(request, "vision", 10, 60)
    parsed = analyze_shelf_photo_base64(
        image_base64=payload.image_base64,
        mime_type=payload.mime_type,
        detected_gtin=payload.detected_gtin,
        hint_text=payload.hint_text,
    )
    if not parsed.get("ok"):
        return JSONResponse(status_code=400, content=parsed)

    shelf_p = payload.shelf_price_tl if payload.shelf_price_tl is not None else parsed.get("shelf_price_tl")
    res = None
    if parsed.get("gtin"):
        res = scanner_engine.scan_barcode(parsed["gtin"], shelf_price_tl=shelf_p)

    if not res and (parsed.get("brand") or parsed.get("product_name") or parsed.get("model_code")):
        obs = VisualObservation(
            brand=parsed.get("brand") or "",
            product_name=parsed.get("product_name") or "",
            net_amount_value=parsed.get("net_amount_value"),
            model_code=parsed.get("model_code") or "",
            gtin_visible=parsed.get("gtin") or "",
            shelf_price_tl=shelf_p,
        )
        res = scanner_engine.scan_visual(obs)

    if not res:
        return JSONResponse(
            status_code=404,
            content={
                "ok": False,
                "extracted": parsed,
                "error": "Fotoğraftaki ürün henüz 75+ ürünlük kataloğumuzda eşleşmedi; ürün adını arama çubuğunda deneyebilirsiniz.",
            },
        )

    member = social_service.get_member_by_token(x_session_token or "")
    if member:
        social_service.record_member_camera_scan(member["member_id"], res.product_key)

    return {
        "ok": True,
        "vision_mode": parsed.get("mode"),
        "vision_note": parsed.get("note"),
        "camera_badge_unlocked": bool(member),
        "result": asdict(res),
        "sabotage_guard": None,
    }


@app.post("/api/scan/lens")
def api_scan_lens(
    payload: LensScanPayload,
    request: Request,
    x_session_token: Optional[str] = Header(default=None),
):
    _enforce_rate_limit(request, "vision", 10, 60)
    result = lens_engine.analyze_and_compare(
        image_base64=payload.image_base64,
        mime_type=payload.mime_type,
        detected_gtin=payload.detected_gtin,
        query_override=payload.query_override,
    )
    if not result.get("ok"):
        return JSONResponse(status_code=400, content=result)

    # Kamerayla veya arama ile bulunan her ürüne kalıcı /urun/<slug> Google SEO sayfası aç
    auto_catalog.persist_lens_bundle(result, ping_search_engines=True)

    member = social_service.get_member_by_token(x_session_token or "")
    local_pkey = (result.get("product_info") or {}).get("local_product_key")
    if member and local_pkey:
        social_service.record_member_camera_scan(member["member_id"], local_pkey)

    result["camera_badge_unlocked"] = bool(member)
    return result


@app.post("/api/scan/lens-upload")
async def api_scan_lens_upload(
    request: Request,
    image: Optional[UploadFile] = File(default=None),
    detected_gtin: str = Form(default=""),
    query_override: str = Form(default=""),
    x_session_token: Optional[str] = Header(default=None),
):
    """
    Mobil tarayıcılar için ikili (multipart/form-data) doğrudan fotoğraf yükleme uç noktası.
    Büyük base64 JSON dizilerinin güvenlik duvarı (WAF) tarafından yanlışlıkla engellenmesini %100 önler.
    """
    _enforce_rate_limit(request, "vision", 10, 60)
    raw_bytes = b""
    mime_type = "image/jpeg"
    if image is not None:
        mime_type = image.content_type or "image/jpeg"
        raw_bytes = await image.read()
        if len(raw_bytes) > 10_000_000:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": "Fotoğraf boyutu çok büyük (maksimum 10 MB)."},
            )

    result = lens_engine.analyze_and_compare(
        mime_type=mime_type,
        detected_gtin=detected_gtin,
        query_override=query_override,
        raw_image_bytes=raw_bytes,
    )
    if not result.get("ok"):
        return JSONResponse(status_code=400, content=result)

    # Mobil fotoğrafla bulunan her ürüne kalıcı /urun/<slug> Google SEO sayfası aç
    auto_catalog.persist_lens_bundle(result, ping_search_engines=True)

    member = social_service.get_member_by_token(x_session_token or "")
    local_pkey = (result.get("product_info") or {}).get("local_product_key")
    if member and local_pkey:
        social_service.record_member_camera_scan(member["member_id"], local_pkey)

    result["camera_badge_unlocked"] = bool(member)
    return result


# ----------------- Birim Fiyat Düşüş Alarmları API -----------------

# Güvenlik: Alarmlar yalnızca oturum (doğrulanmış üye) ile görülebilir/silinebilir.
# Eskiden ?email= parametresi yetiyordu; bu, başkasının e-postasıyla alarmlarını görmeye/silmeye izin veriyordu.
# Oturumsuz kurulan alarmlar, aynı e-postayla giriş yapıldığında hesaba otomatik bağlanır.

@app.get("/api/alerts")
def api_list_alerts(
    email: str = "",
    x_session_token: Optional[str] = Header(default=None),
):
    member = social_service.get_member_by_token(x_session_token or "")
    if not member:
        return {
            "ok": True,
            "login_required": True,
            "alerts": [],
            "live_email_enabled": alert_service.email_client.is_configured,
        }
    return {
        "ok": True,
        "alerts": alert_service.list_alerts(member_id=member["member_id"]),
        "live_email_enabled": alert_service.email_client.is_configured,
    }


@app.post("/api/alerts")
def api_create_alert(
    payload: CreatePriceAlertPayload,
    request: Request,
    x_session_token: Optional[str] = Header(default=None),
):
    _enforce_rate_limit(request, "alert-create", 10, 3600)
    member = social_service.get_member_by_token(x_session_token or "")
    mid = member["member_id"] if member else None
    res = alert_service.create_alert(
        raw_email=payload.email,
        product_key=payload.product_key,
        target_unit_price=payload.target_unit_price,
        target_pack_price=payload.target_pack_price,
        member_id=mid,
    )
    if res.get("ok"):
        fast_radar.sync_watchlist()
    return res


@app.get("/api/alerts/notifications")
def api_pop_alert_notifications(
    email: str = "",
    x_session_token: Optional[str] = Header(default=None),
):
    member = social_service.get_member_by_token(x_session_token or "")
    if not member:
        return {"ok": True, "triggered": [], "notifications": []}
    items = alert_service.pop_pending_browser_notifications(member_id=member["member_id"])
    return {
        "ok": True,
        "triggered": items,
        "notifications": items,
    }


@app.delete("/api/alerts/{alert_id}")
def api_delete_alert(
    alert_id: int,
    email: str = "",
    x_session_token: Optional[str] = Header(default=None),
):
    member = social_service.get_member_by_token(x_session_token or "")
    if not member:
        raise HTTPException(status_code=401, detail="Alarm silmek için giriş yapın.")
    res = alert_service.delete_alert(alert_id, member_id=member["member_id"])
    fast_radar.sync_watchlist()
    return res


# ----------------- 15 Dakikalık Hızlı İndirim Radarı & Market Dedektifi Canlı Besleme API -----------------

@app.get("/api/price-drops")
def api_list_price_drops(category: str = "", limit: int = 48, min_drop_pct: float = 10.0):
    drops = fast_radar.list_live_drops(category=category, limit=limit, min_drop_pct=min_drop_pct)
    # Şeritte önce çok alınan ürünlerin indirimi, sonra indirim oranı (Kemal, 17 Kas 2026)
    drops.sort(key=lambda d: (
        -(_talep.puan(_norm_search_tr(f"{d.get('brand','')} {d.get('family_name','')}"), d.get("category", "")) >= 80),
        -float(d.get("drop_pct") or 0),
    ))
    return {
        "ok": True,
        "count": len(drops),
        "radar_interval_minutes": 15,
        "drops": drops,
        "items": drops,
    }


@app.get("/api/dedektif/firsatlar")
def api_market_dedektifi_feed(category: str = "", limit: int = 30, min_drop_pct: float = 12.0):
    """
    Market Dedektifi botunun / editörünün takipçilerine anlık, kanıtlı gerçek indirim tavsiyesi
    verebilmesi için hazır paylaşım metni (`share_text`), 30 günlük medyan kanıtı ve mağaza linki döner.
    """
    drops = fast_radar.list_live_drops(category=category, limit=limit, min_drop_pct=min_drop_pct)
    by_category: dict = {}
    for d in drops:
        by_category.setdefault(d["category"], []).append(d)
    return {
        "ok": True,
        "generated_at": datetime.now().isoformat(),
        "radar_interval_minutes": 15,
        "total_verified_drops": len(drops),
        "items": drops,
        "by_category": by_category,
    }


@app.get("/api/dedektif/rss.xml")
def api_market_dedektifi_rss():
    return Response(content=fast_radar.build_rss_xml(limit=50), media_type="application/rss+xml")


# ----------------- Akıllı Market Sepeti & Bölünmüş Rota API -----------------

@app.get("/api/basket/presets")
def api_basket_presets():
    return {
        "ok": True,
        "presets": list(PRESET_BASKETS.values()),
        "quick_staples": QUICK_STAPLES,
    }


@app.post("/api/basket/optimize")
def api_basket_optimize(payload: BasketOptimizePayload):
    raw_items = [{"product_key": item.product_key, "qty": item.qty} for item in payload.items]
    return basket_optimizer.optimize(raw_items)


@app.get("/api/custom-basket/discover")
def api_custom_basket_discover(need: str = "peynir", q: str = ""):
    return custom_basket_engine.discover_brands(need_key=need, q=q)


@app.post("/api/custom-basket/compare")
def api_custom_basket_compare(
    payload: CustomBasketComparePayload,
    x_session_token: Optional[str] = Header(default=None),
):
    member = social_service.get_member_by_token(x_session_token or "")
    prof_key = store_account_service.resolve_profile_key(member, payload.client_profile_id)
    acc_map = store_account_service.get_accounts_map(prof_key)
    raw_items = [{"item_id": item.item_id, "qty": item.qty} for item in payload.items]
    return custom_basket_engine.compare_custom_basket(
        raw_items,
        delivery_filter=payload.delivery_filter,
        user_accounts=acc_map,
    )


# ----------------- Bağlı Market Hesapları & Ortak Sipariş Zemini API -----------------

@app.get("/api/store-accounts")
def api_get_store_accounts(
    client_profile_id: str = "",
    x_session_token: Optional[str] = Header(default=None),
):
    member = social_service.get_member_by_token(x_session_token or "")
    prof_key = store_account_service.resolve_profile_key(member, client_profile_id)
    return store_account_service.get_full_bundle(prof_key)


@app.post("/api/store-accounts/address")
def api_save_delivery_address(
    payload: SaveDeliveryProfilePayload,
    x_session_token: Optional[str] = Header(default=None),
):
    member = social_service.get_member_by_token(x_session_token or "")
    prof_key = store_account_service.resolve_profile_key(member, payload.client_profile_id)
    mid = int(member["member_id"]) if member and member.get("member_id") else None
    return store_account_service.save_delivery_profile(
        prof_key,
        city=payload.city,
        district=payload.district,
        neighborhood=payload.neighborhood,
        address_title=payload.address_title,
        member_id=mid,
    )


@app.post("/api/store-accounts/link")
def api_link_store_account(
    payload: LinkStoreAccountPayload,
    x_session_token: Optional[str] = Header(default=None),
):
    member = social_service.get_member_by_token(x_session_token or "")
    prof_key = store_account_service.resolve_profile_key(member, payload.client_profile_id)
    return store_account_service.upsert_store_account(
        prof_key,
        platform=payload.platform,
        account_identifier=payload.account_identifier,
        loyalty_or_pass_active=payload.loyalty_or_pass_active,
        preferred_fulfillment=payload.preferred_fulfillment,
    )


@app.post("/api/store-accounts/order-intent")
def api_create_order_intent(
    payload: OrderIntentPayload,
    x_session_token: Optional[str] = Header(default=None),
):
    member = social_service.get_member_by_token(x_session_token or "")
    prof_key = store_account_service.resolve_profile_key(member, payload.client_profile_id)
    acc_map = store_account_service.get_accounts_map(prof_key)
    raw_items = [{"item_id": item.item_id, "qty": item.qty} for item in payload.items]
    cmp_res = custom_basket_engine.compare_custom_basket(
        raw_items,
        delivery_filter=payload.delivery_filter,
        user_accounts=acc_map,
    )
    plan = cmp_res.get("smart_online_split") or cmp_res.get("pure_cheapest_split") or {}
    stops = plan.get("stops") or []
    return store_account_service.record_order_intent(
        prof_key,
        basket_items=raw_items,
        store_split=stops,
        raw_items_total=float(plan.get("raw_items_total") or 0.0),
        delivery_fees_total=float(plan.get("delivery_fees_total") or 0.0),
    )


# ----------------- Doğrulanmış Üyelik (Google + E-Posta OTP + SMS OTP) & Sosyal Alan API -----------------

@app.get("/api/auth/config")
def api_auth_config():
    from email_auth import EmailOTPClient
    from netgsm_sms import NetgsmClient

    demo = os.getenv("DIPCI_DEMO_AUTH", "0").strip() == "1"
    return {
        "google_client_id": os.getenv("GOOGLE_CLIENT_ID", "").strip(),
        "email_otp_enabled": demo or EmailOTPClient().is_configured,
        "sms_otp_enabled": demo or NetgsmClient().is_configured,
    }


@app.post("/api/auth/request-otp")
def api_request_otp(payload: OTPRequestPayload, request: Request):
    _enforce_rate_limit(request, "otp-request", 5, 3600)
    return social_service.request_phone_otp(payload.phone, payload.display_name)


@app.post("/api/auth/verify-otp")
def api_verify_otp(payload: OTPVerifyPayload, request: Request):
    _enforce_rate_limit(request, "otp-verify", 20, 3600)
    return social_service.verify_phone_otp(payload.phone, payload.otp_code)


@app.post("/api/auth/request-email-otp")
def api_request_email_otp(payload: EmailOTPRequestPayload, request: Request):
    _enforce_rate_limit(request, "otp-request", 5, 3600)
    return social_service.request_email_otp(payload.email, payload.display_name)


@app.post("/api/auth/verify-email-otp")
def api_verify_email_otp(payload: EmailOTPVerifyPayload, request: Request):
    _enforce_rate_limit(request, "otp-verify", 20, 3600)
    return social_service.verify_email_otp(payload.email, payload.otp_code)


@app.post("/api/auth/google")
def api_auth_google(payload: GoogleAuthPayload, request: Request):
    _enforce_rate_limit(request, "google-auth", 20, 3600)
    return social_service.login_with_google_token(payload.credential)


@app.get("/api/auth/me")
def api_auth_me(x_session_token: Optional[str] = Header(default=None)):
    member = social_service.get_member_by_token(x_session_token or "")
    return {"authenticated": bool(member), "member": member}


@app.post("/api/social/posts")
def api_create_social_post(
    payload: SocialPostPayload,
    x_session_token: Optional[str] = Header(default=None),
):
    return social_service.add_product_post(
        session_token=x_session_token or "",
        product_key=payload.product_key,
        post_type=payload.post_type,
        content=payload.content,
        coupon_code=payload.coupon_code,
        usage_months=payload.usage_months,
        seen_store=payload.seen_store,
        seen_price=payload.seen_price,
    )


@app.get("/api/social/feed")
def api_social_feed(limit: int = 40):
    """Topluluk akışı (Kemal, 17 Kas 2026): son yorumlar, gördüğüm fiyatlar, kupon notları + üye fırsatları."""
    return {"ok": True, "posts": social_service.community_feed(limit), "deals": social_service.list_community_deals()}


@app.post("/api/social/posts/{post_id}/helpful")
def api_vote_helpful(
    post_id: int,
    x_session_token: Optional[str] = Header(default=None),
):
    return social_service.vote_post_helpful(x_session_token or "", post_id)


@app.get("/api/social/deals")
def api_list_deals():
    return {"deals": social_service.list_community_deals()}


@app.post("/api/social/deals")
def api_share_deal(
    payload: ShareDealPayload,
    x_session_token: Optional[str] = Header(default=None),
):
    return social_service.share_community_deal(
        session_token=x_session_token or "",
        title=payload.title,
        raw_url=payload.raw_url,
        sale_price=payload.sale_price,
        coupon_code=payload.coupon_code,
        note=payload.note,
        product_key=payload.product_key,
    )


@app.post("/api/social/deals/{deal_id}/vote")
def api_vote_deal(
    deal_id: int,
    payload: VotePayload,
    x_session_token: Optional[str] = Header(default=None),
):
    return social_service.vote_deal_temperature(x_session_token or "", deal_id, payload.delta)


# ----------------- İş Ortağı Mağaza XML Kayıt Kapısı -----------------

def _is_public_http_url(raw_url: str) -> bool:
    """SSRF koruması: yalnızca herkese açık internet adreslerine giden http(s) URL'lerini kabul eder."""
    try:
        parsed = urlparse(raw_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return False
        for info in socket.getaddrinfo(parsed.hostname, parsed.port or 443, proto=socket.IPPROTO_TCP):
            ip = ipaddress.ip_address(info[4][0])
            if not ip.is_global:
                return False
        return True
    except (ValueError, OSError):
        return False


@app.post("/api/partners/xml-register")
def api_register_partner_xml(payload: PartnerXMLPayload, request: Request):
    _enforce_rate_limit(request, "partner-xml", 3, 3600)
    store_name = payload.store_name.strip()[:120]
    xml_url = payload.xml_url.strip()
    if not store_name or len(xml_url) > 2048 or not _is_public_http_url(xml_url):
        return {"ok": False, "error": "Lütfen geçerli mağaza adı ve herkese açık https:// XML besleme adresi girin."}

    db.conn.execute(
        """
        INSERT INTO partner_xml_feeds (store_name, brand_color, xml_url, contact_email, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            store_name,
            payload.brand_color[:16],
            xml_url,
            payload.contact_email.strip()[:200],
            datetime.now().isoformat(),
        ),
    )
    db.conn.commit()
    return {
        "ok": True,
        "message": f"{payload.store_name} XML ürün beslemesi doğrulama kuyruğuna alındı.",
    }
