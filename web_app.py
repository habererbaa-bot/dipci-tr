"""
dipci.tr — Yeni Nesil Fiyat, Birim Maliyet, Kamera Okuyucu ve Doğrulanmış Sosyal Alışveriş Platformu
Tam Kapsamlı Web Sunucusu (FastAPI + SQLite WAL + 7 Katmanlı Market Dedektifi Denetim Motoru)
"""

import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
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
if _idx_count < _prod_count:
    auto_catalog.reclassify_existing_catalog()
    db.rebuild_catalog_index()
    fast_radar.sync_watchlist()
    fast_radar.detect_and_publish_drops()

app = FastAPI(
    title="dipci.tr — Fiyat Röntgeni, Reyon Kamerası & Doğrulanmış Alışveriş Ağı",
    version="1.0.0",
)


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
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


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


def _read_index_template() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


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
def serve_product_seo_page(slug: str):
    product_key = slug_to_key(slug)
    bundle = build_product_seo_bundle(db, audit_engine, social_service, product_key)
    if not bundle:
        human_query = slug.replace("-", " ").replace("_", " ").strip()
        if len(human_query) >= 3:
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
def serve_barcode_seo_page(gtin: str):
    matched = db.lookup_by_barcode(gtin)
    target_key = matched["product_key"] if matched else ""
    target_gtin = matched["gtin"] if matched else "".join(ch for ch in gtin if ch.isdigit())

    if not target_key and len(target_gtin) >= 8:
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


@app.get("/kurumsal", response_class=HTMLResponse)
def serve_corporate_page():
    return HTMLResponse(build_corporate_identity_html(), headers=NO_CACHE_HTML_HEADERS)


@app.get("/favicon.ico")
def serve_favicon():
    return FileResponse(str(STATIC_DIR / "brand" / "favicon.svg"), media_type="image/svg+xml")


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


@app.get("/api/products")
def list_products(
    q: str = "",
    category: str = "",
    brand: str = "",
    store: str = "",
    min_price: float = 0.0,
    max_price: float = 0.0,
    only_drops: int = 0,
    min_stores: int = 0,
    sort: str = "savings",
    page: int = 1,
    limit: int = 72,
    live: int = 0,
):
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
    if q_raw and len(q_raw) >= 3 and ((total_matching == 0 and not brand_clean and not store_clean) or live == 1):
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
        "savings": "CASE WHEN offer_count >= 2 THEN 1 ELSE 0 END DESC, savings_pct DESC, offer_count DESC",
        "drops": "is_real_drop DESC, drop_vs_median_pct DESC, savings_pct DESC",
        "price_asc": "best_pack_price ASC, offer_count DESC",
        "price_desc": "best_pack_price DESC, offer_count DESC",
        "offers": "offer_count DESC, savings_pct DESC",
        "score": "detective_score DESC, savings_pct DESC",
    }
    order_sql = order_map.get(sort, order_map["savings"])

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
    brand_facets = [
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
    cat_counts = {
        cr["category"]: int(cr["c"])
        for cr in db.conn.execute(
            "SELECT category, COUNT(*) AS c FROM product_catalog_index WHERE offer_count >= 1 GROUP BY category"
        ).fetchall()
    }

    global_stats = db.conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM product_catalog_index WHERE offer_count >= 1) AS prod_cnt,
            (SELECT COALESCE(SUM(offer_count), 0) FROM product_catalog_index WHERE offer_count >= 1) AS quote_cnt,
            (SELECT COUNT(*) FROM product_catalog_index WHERE is_real_drop = 1 AND offer_count >= 2) AS drop_cnt,
            (SELECT COALESCE(MAX(savings_pct), 0) FROM product_catalog_index WHERE offer_count >= 2) AS max_sav
        """
    ).fetchone()

    items = []
    for r in page_rows:
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
        items.append(
            {
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
        )

    return {
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


@app.get("/api/products/{product_key}")
def get_product_xray(product_key: str):
    prod = db.conn.execute(
        "SELECT * FROM canonical_products WHERE product_key = ?", (product_key,)
    ).fetchone()
    if not prod:
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
        SELECT substr(captured_at, 1, 10) AS day, MIN(effective_unit_price) AS unit_p
        FROM price_snapshots
        WHERE product_key = ? AND seller_type IN ('OFFICIAL', 'AUTHORIZED')
        GROUP BY substr(captured_at, 1, 10)
        ORDER BY day ASC
        LIMIT 15
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

    return {
        "product": dict(prod),
        "barcodes": barcodes,
        "audit": asdict(finding) if finding else None,
        "offers": offers_out,
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
    x_session_token: Optional[str] = Header(default=None),
):
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
    x_session_token: Optional[str] = Header(default=None),
):
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
    image: Optional[UploadFile] = File(default=None),
    detected_gtin: str = Form(default=""),
    query_override: str = Form(default=""),
    x_session_token: Optional[str] = Header(default=None),
):
    """
    Mobil tarayıcılar için ikili (multipart/form-data) doğrudan fotoğraf yükleme uç noktası.
    Büyük base64 JSON dizilerinin güvenlik duvarı (WAF) tarafından yanlışlıkla engellenmesini %100 önler.
    """
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

@app.get("/api/alerts")
def api_list_alerts(
    email: str = "",
    x_session_token: Optional[str] = Header(default=None),
):
    member = social_service.get_member_by_token(x_session_token or "")
    mid = member["member_id"] if member else None
    return {
        "ok": True,
        "alerts": alert_service.list_alerts(email=email, member_id=mid),
        "live_email_enabled": alert_service.email_client.is_configured,
    }


@app.post("/api/alerts")
def api_create_alert(
    payload: CreatePriceAlertPayload,
    x_session_token: Optional[str] = Header(default=None),
):
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
    mid = member["member_id"] if member else None
    items = alert_service.pop_pending_browser_notifications(email=email, member_id=mid)
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
    mid = member["member_id"] if member else None
    res = alert_service.delete_alert(alert_id, email=email, member_id=mid)
    fast_radar.sync_watchlist()
    return res


# ----------------- 15 Dakikalık Hızlı İndirim Radarı & Market Dedektifi Canlı Besleme API -----------------

@app.get("/api/price-drops")
def api_list_price_drops(category: str = "", limit: int = 48, min_drop_pct: float = 10.0):
    drops = fast_radar.list_live_drops(category=category, limit=limit, min_drop_pct=min_drop_pct)
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
    return {
        "google_client_id": os.getenv("GOOGLE_CLIENT_ID", "").strip(),
        "email_otp_enabled": True,
        "sms_otp_enabled": True,
    }


@app.post("/api/auth/request-otp")
def api_request_otp(payload: OTPRequestPayload):
    return social_service.request_phone_otp(payload.phone, payload.display_name)


@app.post("/api/auth/verify-otp")
def api_verify_otp(payload: OTPVerifyPayload):
    return social_service.verify_phone_otp(payload.phone, payload.otp_code)


@app.post("/api/auth/request-email-otp")
def api_request_email_otp(payload: EmailOTPRequestPayload):
    return social_service.request_email_otp(payload.email, payload.display_name)


@app.post("/api/auth/verify-email-otp")
def api_verify_email_otp(payload: EmailOTPVerifyPayload):
    return social_service.verify_email_otp(payload.email, payload.otp_code)


@app.post("/api/auth/google")
def api_auth_google(payload: GoogleAuthPayload):
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
    )


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

@app.post("/api/partners/xml-register")
def api_register_partner_xml(payload: PartnerXMLPayload):
    if not payload.store_name.strip() or not payload.xml_url.strip().startswith(("http://", "https://")):
        return {"ok": False, "error": "Lütfen geçerli mağaza adı ve https:// XML besleme adresi girin."}

    db.conn.execute(
        """
        INSERT INTO partner_xml_feeds (store_name, brand_color, xml_url, contact_email, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            payload.store_name.strip(),
            payload.brand_color,
            payload.xml_url.strip(),
            payload.contact_email.strip(),
            datetime.now().isoformat(),
        ),
    )
    db.conn.commit()
    return {
        "ok": True,
        "message": f"{payload.store_name} XML ürün beslemesi doğrulama kuyruğuna alındı.",
    }
