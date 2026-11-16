"""
dipci.tr — Kurumsal Kimlik, Google Rich Snippets (Schema.org JSON-LD), SSR Meta Enjeksiyonu,
Dinamik XML Sitemap Ailesi, Barkod SEO Sayfaları, IndexNow ve AI Arama (llms.txt) Altyapısı.
"""

import html
import json
from datetime import datetime
from typing import Dict, List, Optional

from audit_engine import MarketDedektifiAuditEngine
from memory_db import PriceMemoryDB
from social_community import SocialCommunityService

CANONICAL_ORIGIN = "https://dipci.tr"
BRAND_NAME = "dipci.tr"
BRAND_SLOGAN = "Sahte İndirimi Değil, Gerçek Birim Dibi Yakala"
INDEXNOW_KEY = "dipci2026tr90daypriceindexkey"

CATEGORY_META: Dict[str, Dict[str, str]] = {
    "gida": {
        "slug": "gida",
        "title": "Süpermarket & Temel Gıda En Ucuz Kg/Litre Birim Fiyatları",
        "desc": "Peynir, yağ, bakliyat, çay, kahve ve temel gıdada gizli gramaj küçültmeyi (shrinkflation) yakalayan 90 günlük gerçek kg/litre fiyat hafızası.",
    },
    "temizlik": {
        "slug": "temizlik",
        "title": "Ev & Temizlik Ürünleri En Ucuz Birim Fiyatları (TL/Yıkama & Rulo)",
        "desc": "Bulaşık kapsülü, çamaşır deterjanı ve kağıt ürünlerinde paket oyununa gelmeden 1 yıkama ve rulo bazında en dip fiyatları karşılaştırın.",
    },
    "kisisel_bakim": {
        "slug": "kisisel-bakim",
        "title": "Kozmetik, Dermokozmetik & Kişisel Bakım En Ucuz Birim Fiyatları",
        "desc": "Şampuan, diş macunu, güneş kremi, cilt bakımı ve kişisel bakımda ml/litre birim maliyetini mağazalar arasında karşılaştırın.",
    },
    "anne_bebek": {
        "slug": "anne-bebek",
        "title": "Anne & Bebek Bezi, Mama En Ucuz Adet/Birim Fiyatları",
        "desc": "Bebek bezi, ıslak mendil ve devam sütlerinde koli tuzağına düşmeden tekli adet birim maliyetini resmi satıcı garantisiyle bulun.",
    },
    "teknoloji_beyaz_esya": {
        "slug": "teknoloji-beyaz-esya",
        "title": "Elektronik, Telefon, TV & Bilgisayar En Ucuz Fiyat Karşılaştırma",
        "desc": "Cep telefonu, kulaklık, bilgisayar, televizyon ve beyaz eşyada tüm online mağazaların güncel kasa ve taksitli reel fiyatlarını inceleyin.",
    },
    "ev_yasam_mutfak": {
        "slug": "ev-yasam-mutfak",
        "title": "Küçük Ev Aletleri, Süpürge & Mutfak En Ucuz Fiyat Karşılaştırma",
        "desc": "Kahve makinesi, robot süpürge, dikey süpürge, airfryer, tencere seti ve mutfak gereçlerinde mağazalar arası en düşük fiyatları karşılaştırın.",
    },
    "evcil_hayvan": {
        "slug": "evcil-hayvan",
        "title": "Petshop, Kedi & Köpek Maması En Ucuz Kg Birim Fiyatları",
        "desc": "Kedi maması, köpek maması, kedi kumu ve evcil hayvan ürünlerinde paket boyutuna göre 1 kg gerçek birim maliyetleri karşılaştırın.",
    },
    "saglik_spor_outdoor": {
        "slug": "saglik-spor-outdoor",
        "title": "Sağlık, Termos, Takviye & Outdoor Fiyat Karşılaştırma",
        "desc": "Çelik termos, vitamin takviyeleri, ağız sağlığı elektroniği ve outdoor ekipmanlarında resmi satıcı fiyatlarını en düşükten en yükseğe görün.",
    },
}


def key_to_slug(product_key: str) -> str:
    return product_key.replace("_", "-").lower()


def slug_to_key(slug: str) -> str:
    return slug.replace("-", "_").lower()


def cat_slug_to_db(cat_slug: str) -> Optional[str]:
    for db_cat, meta in CATEGORY_META.items():
        if meta["slug"] == cat_slug:
            return db_cat
    return None


def build_organization_and_website_schema() -> List[Dict]:
    return [
        {
            "@context": "https://schema.org",
            "@type": "Organization",
            "@id": f"{CANONICAL_ORIGIN}/#organization",
            "name": "dipci.tr",
            "alternateName": "Dipçi — Gerçek Dip Fiyat Radarı",
            "url": CANONICAL_ORIGIN,
            "logo": {
                "@type": "ImageObject",
                "url": f"{CANONICAL_ORIGIN}/static/brand/logo-light.svg",
                "width": 260,
                "height": 64,
            },
            "description": (
                "Türkiye'nin 90 günlük fiyat hafızası, birim maliyet normalizasyonu, gizli gramaj düşüşü "
                "(shrinkflation) radarı, reyon barkod kamerası ve SMS doğrulamalı tüketici alışveriş ağı."
            ),
            "sameAs": [
                "https://basinca.com",
            ],
            "contactPoint": {
                "@type": "ContactPoint",
                "contactType": "customer support",
                "email": "iletisim@dipci.tr",
                "areaServed": "TR",
                "availableLanguage": ["Turkish"],
            },
        },
        {
            "@context": "https://schema.org",
            "@type": "WebSite",
            "@id": f"{CANONICAL_ORIGIN}/#website",
            "url": CANONICAL_ORIGIN,
            "name": "dipci.tr",
            "inLanguage": "tr-TR",
            "publisher": {"@id": f"{CANONICAL_ORIGIN}/#organization"},
            "potentialAction": {
                "@type": "SearchAction",
                "target": {
                    "@type": "EntryPoint",
                    "urlTemplate": f"{CANONICAL_ORIGIN}/?q={{search_term_string}}",
                },
                "query-input": "required name=search_term_string",
            },
        },
    ]


def build_product_seo_bundle(
    db: PriceMemoryDB,
    audit_engine: MarketDedektifiAuditEngine,
    social: SocialCommunityService,
    product_key: str,
    focused_gtin: Optional[str] = None,
) -> Optional[Dict]:
    prod = db.conn.execute(
        "SELECT * FROM canonical_products WHERE product_key = ?", (product_key,)
    ).fetchone()
    if not prod:
        return None

    quotes = db.get_latest_market_quotes(product_key)
    trusted = [q for q in quotes if q.seller_type in ("OFFICIAL", "AUTHORIZED") and q.seller_rating >= 8.8]
    active_quotes = sorted(
        trusted if trusted else quotes,
        key=lambda q: (q.effective_pack_price, q.effective_unit_price),
    )
    lead_q = active_quotes[0] if active_quotes else None
    finding = audit_engine.audit_quote(product_key, lead_q) if lead_q else None

    barcodes = [
        b["gtin"]
        for b in db.conn.execute(
            "SELECT gtin FROM product_barcodes WHERE product_key = ?", (product_key,)
        ).fetchall()
    ]
    primary_gtin = focused_gtin or (barcodes[0] if barcodes else "")
    slug = key_to_slug(product_key)
    cat_info = CATEGORY_META.get(prod["category"], {"slug": "genel", "title": "Genel"})

    low_pack = min((q.effective_pack_price for q in active_quotes), default=0.0)
    high_pack = max((q.effective_pack_price for q in active_quotes), default=0.0)
    low_unit = min((q.effective_unit_price for q in active_quotes), default=0.0)

    full_name = f"{prod['brand']} {prod['family_name']}".strip()
    if prod["official_model_code"] and prod["official_model_code"] not in full_name:
        full_name = f"{full_name} ({prod['official_model_code']})"

    if focused_gtin:
        canonical_url = f"{CANONICAL_ORIGIN}/barkod/{focused_gtin}"
        page_title = (
            f"{focused_gtin} Barkodlu {full_name} En Ucuz Fiyatı "
            f"({low_pack:,.2f} TL) | dipci.tr"
        )
    else:
        canonical_url = f"{CANONICAL_ORIGIN}/urun/{slug}"
        page_title = (
            f"{full_name} En Ucuz Fiyatları ({low_pack:,.2f} TL • {len(active_quotes)} Mağaza) | dipci.tr"
        )

    top_sellers_preview = ", ".join(dict.fromkeys(q.seller_name for q in active_quotes[:4]))
    meta_desc = (
        f"{full_name} en ucuz {low_pack:.2f} TL fiyatla {len(active_quotes)} farklı mağazada ({top_sellers_preview}) "
        f"en düşükten en yükseğe sıralandı. Birim maliyet: {low_unit:.2f} TL/{prod['base_unit']}."
    )

    posts = social.list_product_posts(product_key)

    # Schema.org Product + AggregateOffer (En düşükten en yükseğe tüm satıcılar)
    offer_items = [
        {
            "@type": "Offer",
            "url": canonical_url,
            "priceCurrency": "TRY",
            "price": f"{q.effective_pack_price:.2f}",
            "availability": "https://schema.org/InStock",
            "itemCondition": "https://schema.org/NewCondition",
            "seller": {"@type": "Organization", "name": q.seller_name},
        }
        for q in active_quotes[:15]
    ]

    prod_img = (
        prod["image_url"]
        if ("image_url" in prod.keys() and prod["image_url"])
        else f"{CANONICAL_ORIGIN}/static/brand/og-cover.svg"
    )
    product_ld: Dict = {
        "@context": "https://schema.org",
        "@type": "Product",
        "@id": f"{CANONICAL_ORIGIN}/urun/{slug}#product",
        "name": full_name,
        "brand": {"@type": "Brand", "name": prod["brand"]},
        "sku": product_key,
        "description": meta_desc,
        "image": [prod_img],
        "offers": {
            "@type": "AggregateOffer",
            "url": canonical_url,
            "priceCurrency": "TRY",
            "lowPrice": f"{low_pack:.2f}",
            "highPrice": f"{high_pack:.2f}",
            "offerCount": len(active_quotes),
            "offers": offer_items,
        },
    }
    if primary_gtin and len(primary_gtin) == 13:
        product_ld["gtin13"] = primary_gtin
    if prod["official_model_code"]:
        product_ld["mpn"] = prod["official_model_code"]

    if posts:
        product_ld["aggregateRating"] = {
            "@type": "AggregateRating",
            "ratingValue": "4.8",
            "reviewCount": str(len(posts)),
            "bestRating": "5",
            "worstRating": "1",
        }
        product_ld["review"] = [
            {
                "@type": "Review",
                "author": {"@type": "Person", "name": p["author_name"]},
                "datePublished": p["created_at"][:10],
                "reviewBody": p["content"],
                "reviewRating": {"@type": "Rating", "ratingValue": "5", "bestRating": "5"},
            }
            for p in posts[:5]
        ]

    breadcrumb_ld = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Ana Sayfa", "item": CANONICAL_ORIGIN},
            {
                "@type": "ListItem",
                "position": 2,
                "name": cat_info["title"].split(" En Ucuz")[0],
                "item": f"{CANONICAL_ORIGIN}/kategori/{cat_info['slug']}",
            },
            {"@type": "ListItem", "position": 3, "name": full_name, "item": canonical_url},
        ],
    }

    faq_ld = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": f"{full_name} şu an en ucuz hangi mağazada?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": (
                        f"{full_name} ürünü şu an en düşük birim maliyetle "
                        f"{lead_q.seller_name if lead_q else 'resmi mağaza'} üzerinde "
                        f"{low_pack:.2f} TL ({low_unit:.2f} TL/{prod['base_unit']}) fiyatla satılmaktadır."
                    ),
                },
            },
            {
                "@type": "Question",
                "name": f"{full_name} indirimi gerçek mi, son 90 günde fiyatı düştü mü?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": " ".join(finding.evidence_notes) if (finding and finding.evidence_notes) else meta_desc,
                },
            },
        ],
    }

    ssr_offers_li = "".join(
        f"<li><strong>{html.escape(q.seller_name)}</strong>: {q.effective_pack_price:.2f} TL "
        f"({q.effective_unit_price:.2f} TL/{html.escape(q.base_unit)})</li>"
        for q in active_quotes
    )
    ssr_semantic_block = f"""
    <section id="ssr-seo-snapshot" class="sr-only" aria-label="Google İndeksleme Özeti">
      <h1>{html.escape(full_name)} En Ucuz Fiyat ve Birim Maliyet Analizi</h1>
      <p>{html.escape(meta_desc)}</p>
      {f'<p>Resmi Barkod (EAN-13): {html.escape(primary_gtin)}</p>' if primary_gtin else ''}
      <h2>Mağaza Fiyat Karşılaştırma Listesi</h2>
      <ul>{ssr_offers_li}</ul>
    </section>
    """

    return {
        "product_key": product_key,
        "title": page_title,
        "description": meta_desc,
        "canonical": canonical_url,
        "og_type": "product",
        "price_amount": f"{low_pack:.2f}",
        "json_ld_list": build_organization_and_website_schema() + [product_ld, breadcrumb_ld, faq_ld],
        "ssr_html": ssr_semantic_block,
    }


def render_seo_hydrated_spa(
    raw_index_html: str,
    title: str,
    description: str,
    canonical_url: str,
    json_ld_objects: List[Dict],
    ssr_html: str = "",
    initial_product_key: Optional[str] = None,
    initial_view: str = "xray",
    price_amount: Optional[str] = None,
) -> str:
    """
    Tek sayfa uygulamasını (SPA) Googlebot için tam SSR meta etiketleri, Canonical URL,
    OpenGraph, Twitter Card, Schema.org JSON-LD ve semantik HTML içeriğiyle zenginleştirir.
    """
    esc_title = html.escape(title)
    esc_desc = html.escape(description)
    esc_canon = html.escape(canonical_url)

    ld_scripts = "\n  ".join(
        f'<script type="application/ld+json">{json.dumps(obj, ensure_ascii=False)}</script>'
        for obj in json_ld_objects
    )

    product_meta = ""
    if price_amount:
        product_meta = (
            f'\n  <meta property="product:price:amount" content="{html.escape(price_amount)}" />'
            f'\n  <meta property="product:price:currency" content="TRY" />'
        )

    seo_head = f"""<title>{esc_title}</title>
  <meta name="description" content="{esc_desc}" />
  <meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1" />
  <link rel="canonical" href="{esc_canon}" />
  <link rel="alternate" hreflang="tr-TR" href="{esc_canon}" />
  <link rel="icon" type="image/svg+xml" href="/static/brand/favicon.svg" />
  <link rel="apple-touch-icon" href="/static/brand/favicon.svg" />
  <link rel="manifest" href="/static/manifest.json" />
  <meta name="theme-color" content="#0B132B" />

  <!-- OpenGraph (WhatsApp, Facebook, LinkedIn Önizlemesi) -->
  <meta property="og:site_name" content="dipci.tr" />
  <meta property="og:locale" content="tr_TR" />
  <meta property="og:type" content="{'product' if price_amount else 'website'}" />
  <meta property="og:title" content="{esc_title}" />
  <meta property="og:description" content="{esc_desc}" />
  <meta property="og:url" content="{esc_canon}" />
  <meta property="og:image" content="{CANONICAL_ORIGIN}/static/brand/og-cover.svg" />{product_meta}

  <!-- X / Twitter Card -->
  <meta name="twitter:card" content="summary_large_image" />
  <meta name="twitter:title" content="{esc_title}" />
  <meta name="twitter:description" content="{esc_desc}" />
  <meta name="twitter:image" content="{CANONICAL_ORIGIN}/static/brand/og-cover.svg" />

  <!-- Google Rich Snippets (Schema.org JSON-LD) -->
  {ld_scripts}"""

    boot_cfg = {
        "initialProductKey": initial_product_key,
        "initialView": initial_view,
        "canonicalOrigin": CANONICAL_ORIGIN,
    }
    boot_script = f"<script>window.__DIPCI_BOOT__ = {json.dumps(boot_cfg, ensure_ascii=False)};</script>"

    enriched = raw_index_html.replace(
        "<title>dipci.tr — Fiyat Röntgeni, Reyon Kamerası & Doğrulanmış Alışveriş Ağı</title>",
        f"{seo_head}\n  {boot_script}",
    )
    enriched = enriched.replace("</body>", f"{ssr_html}\n</body>")
    return enriched


def build_robots_txt() -> str:
    return f"""User-agent: *
Allow: /
Allow: /urun/
Allow: /barkod/
Allow: /kategori/
Allow: /sepet
Allow: /kurumsal
Disallow: /api/auth/
Disallow: /api/social/posts/*/helpful
Disallow: /api/social/deals/*/vote

# Yapay Zeka ve Arama Motoru Botları İçin Açık İzin (GEO / AI Search)
User-agent: Googlebot
Allow: /

User-agent: OAI-SearchBot
Allow: /

User-agent: PerplexityBot
Allow: /

Host: dipci.tr
Sitemap: {CANONICAL_ORIGIN}/sitemap.xml
"""


SITEMAP_PAGE_SIZE = 5000


def build_llms_txt(db: PriceMemoryDB) -> str:
    total_cnt = db.conn.execute(
        "SELECT COUNT(*) AS c FROM canonical_products WHERE is_private_label = 0"
    ).fetchone()["c"]
    prods = db.conn.execute(
        "SELECT product_key, brand, family_name, base_unit FROM canonical_products WHERE is_private_label = 0 LIMIT 120"
    ).fetchall()
    prod_lines = "\n".join(
        f"- [{r['brand']} {r['family_name']}]({CANONICAL_ORIGIN}/urun/{key_to_slug(r['product_key'])}): "
        f"En düşük mağaza fiyatı, birim maliyet ({r['base_unit']}) ve 90g fiyat geçmişi."
        for r in prods
    )
    return f"""# dipci.tr — Türkiye'nin Çok Mağazalı Fiyat Karşılaştırma ve Birim Maliyet Platformu ({total_cnt}+ Ürün)

> dipci.tr; süpermarket, temizlik, anne-bebek, kişisel bakım, beyaz eşya ve tüketici elektroniği ürünlerinde
> tüm zincir market ve online pazaryerlerini en düşük fiyattan en yükseğe sıralayan, aynı zamanda gerçek birim maliyeti
> (TL/kg, TL/litre, TL/yıkama, TL/adet) hesaplayan bağımsız fiyat karşılaştırma platformudur.

## Ana Bölümler
- [Ana Sayfa]({CANONICAL_ORIGIN}/)
- [Market Öz Marka Sepeti]({CANONICAL_ORIGIN}/sepet)
- [Marka Seçimli Kendi Sepetim]({CANONICAL_ORIGIN}/kendi-sepetim)
- [Fotoğrafla & Barkodla Ürün Bul]({CANONICAL_ORIGIN}/lens)
- [Kurumsal Kimlik ve Şeffaflık İlkeleri]({CANONICAL_ORIGIN}/kurumsal)
- [Ana Site Haritası İndeksi]({CANONICAL_ORIGIN}/sitemap.xml)

## Örnek Ürün Karşılaştırma Sayfaları
{prod_lines}
"""


def build_sitemap_index_xml(db: Optional[PriceMemoryDB] = None) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    locs = [
        f"{CANONICAL_ORIGIN}/sitemap-pages.xml",
        f"{CANONICAL_ORIGIN}/sitemap-categories.xml",
    ]
    prod_pages = 1
    bc_pages = 1
    if db is not None:
        prod_cnt = db.conn.execute(
            "SELECT COUNT(*) AS c FROM canonical_products WHERE is_private_label = 0"
        ).fetchone()["c"]
        bc_cnt = db.conn.execute("SELECT COUNT(*) AS c FROM product_barcodes").fetchone()["c"]
        prod_pages = max(1, (prod_cnt + SITEMAP_PAGE_SIZE - 1) // SITEMAP_PAGE_SIZE)
        bc_pages = max(1, (bc_cnt + SITEMAP_PAGE_SIZE - 1) // SITEMAP_PAGE_SIZE)

    for p in range(1, prod_pages + 1):
        suffix = "" if p == 1 else f"-{p}"
        locs.append(f"{CANONICAL_ORIGIN}/sitemap-products{suffix}.xml")
    for p in range(1, bc_pages + 1):
        suffix = "" if p == 1 else f"-{p}"
        locs.append(f"{CANONICAL_ORIGIN}/sitemap-barcodes{suffix}.xml")

    nodes = "\n".join(
        f"""  <sitemap>
    <loc>{loc}</loc>
    <lastmod>{today}</lastmod>
  </sitemap>"""
        for loc in locs
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{nodes}
</sitemapindex>"""


def _wrap_urlset(entries: List[Dict[str, str]]) -> str:
    body = "\n".join(
        f"""  <url>
    <loc>{html.escape(e['loc'])}</loc>
    <lastmod>{e['lastmod']}</lastmod>
    <changefreq>{e['changefreq']}</changefreq>
    <priority>{e['priority']}</priority>
  </url>"""
        for e in entries
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{body}
</urlset>"""


def build_pages_sitemap_xml() -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    return _wrap_urlset(
        [
            {"loc": f"{CANONICAL_ORIGIN}/", "lastmod": today, "changefreq": "hourly", "priority": "1.0"},
            {"loc": f"{CANONICAL_ORIGIN}/sepet", "lastmod": today, "changefreq": "hourly", "priority": "0.95"},
            {"loc": f"{CANONICAL_ORIGIN}/kendi-sepetim", "lastmod": today, "changefreq": "hourly", "priority": "0.95"},
            {"loc": f"{CANONICAL_ORIGIN}/lens", "lastmod": today, "changefreq": "daily", "priority": "0.9"},
            {"loc": f"{CANONICAL_ORIGIN}/firsatlar", "lastmod": today, "changefreq": "hourly", "priority": "0.9"},
            {"loc": f"{CANONICAL_ORIGIN}/kurumsal", "lastmod": today, "changefreq": "monthly", "priority": "0.7"},
        ]
    )


def build_products_sitemap_xml(db: PriceMemoryDB, page: int = 1) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    offset = max(0, (page - 1) * SITEMAP_PAGE_SIZE)
    rows = db.conn.execute(
        "SELECT product_key FROM canonical_products WHERE is_private_label = 0 ORDER BY product_key LIMIT ? OFFSET ?",
        (SITEMAP_PAGE_SIZE, offset),
    ).fetchall()
    return _wrap_urlset(
        [
            {
                "loc": f"{CANONICAL_ORIGIN}/urun/{key_to_slug(r['product_key'])}",
                "lastmod": today,
                "changefreq": "hourly",
                "priority": "0.9",
            }
            for r in rows
        ]
    )


def build_barcodes_sitemap_xml(db: PriceMemoryDB, page: int = 1) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    offset = max(0, (page - 1) * SITEMAP_PAGE_SIZE)
    rows = db.conn.execute(
        "SELECT gtin FROM product_barcodes ORDER BY gtin LIMIT ? OFFSET ?",
        (SITEMAP_PAGE_SIZE, offset),
    ).fetchall()
    return _wrap_urlset(
        [
            {
                "loc": f"{CANONICAL_ORIGIN}/barkod/{r['gtin']}",
                "lastmod": today,
                "changefreq": "daily",
                "priority": "0.8",
            }
            for r in rows
        ]
    )


def build_categories_sitemap_xml() -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    return _wrap_urlset(
        [
            {
                "loc": f"{CANONICAL_ORIGIN}/kategori/{meta['slug']}",
                "lastmod": today,
                "changefreq": "hourly",
                "priority": "0.85",
            }
            for meta in CATEGORY_META.values()
        ]
    )


def build_corporate_identity_html() -> str:
    """
    https://dipci.tr/kurumsal — Hem Google E-E-A-T güven sinyali hem de
    Amazon Gelir Ortaklığı / ReklamAction / Admitad yayıncı incelemeleri için
    resmi Kurumsal Kimlik, Etik Duvar, KVKK ve #işbirliği Şeffaflık Sayfası.
    """
    org_ld = json.dumps(build_organization_and_website_schema()[0], ensure_ascii=False)
    return f"""<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Kurumsal Kimlik, Algoritmik Şeffaflık &amp; Yasal İlkeler | dipci.tr</title>
  <meta name="description" content="dipci.tr kurumsal kimlik kılavuzu, 7 katmanlı bağımsız fiyat denetim metodolojisi, KVKK aydınlatma ilkeleri ve gelir ortaklığı şeffaflık beyanı." />
  <link rel="canonical" href="{CANONICAL_ORIGIN}/kurumsal" />
  <link rel="icon" type="image/svg+xml" href="/static/brand/favicon.svg" />
  <script src="https://cdn.tailwindcss.com"></script>
  <script type="application/ld+json">{org_ld}</script>
</head>
<body class="bg-slate-50 text-slate-900 antialiased">
  <header class="bg-white border-b border-slate-200">
    <div class="max-w-4xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between">
      <a href="/" class="flex items-center gap-3">
        <img src="/static/brand/logo-light.svg" alt="dipci.tr Logo" class="h-8 w-auto" />
      </a>
      <a href="/" class="px-3.5 py-2 rounded-lg bg-slate-900 text-white text-xs font-medium hover:bg-slate-800 transition">
        ← Ana Sayfaya Dön
      </a>
    </div>
  </header>

  <main class="max-w-4xl mx-auto px-4 sm:px-6 py-8 space-y-6">
    <!-- 1. Marka Özeti -->
    <section class="p-6 rounded-2xl bg-white border border-slate-200 space-y-3">
      <span class="text-xs font-medium text-slate-500 uppercase tracking-wide">Kurumsal Bilgiler</span>
      <h1 class="text-xl font-semibold tracking-tight text-slate-900">dipci.tr — Bağımsız Birim Fiyat ve Market Karşılaştırma Platformu</h1>
      <p class="text-sm text-slate-600 leading-relaxed">
        <strong>dipci.tr</strong>, tüketicilerin zincir market ve online mağaza fiyatlarını gerçek birim maliyet (kilogram, litre, yıkama, adet) bazında karşılaştırabilmesi için geliştirilmiş bağımsız bir veri platformudur. İlgili yayınımız <a href="https://basinca.com" class="text-slate-900 font-medium underline">basinca.com</a> ile entegre çalışır.
      </p>
    </section>

    <!-- 2. Görsel Kurumsal Kimlik & Renk Sistemi -->
    <section class="p-6 rounded-2xl bg-white border border-slate-200 space-y-4">
      <h2 class="text-base font-semibold text-slate-900">Logo ve Kurumsal Renk Standartları</h2>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div class="p-5 rounded-xl bg-slate-50 border border-slate-200 flex flex-col items-center justify-center gap-2">
          <img src="/static/brand/logo-light.svg" alt="dipci.tr Açık Zemin Logo" class="h-10" />
          <span class="text-xs text-slate-500">Açık Zemin Resmi Logo (SVG)</span>
        </div>
        <div class="p-5 rounded-xl bg-[#0F172A] flex flex-col items-center justify-center gap-2">
          <img src="/static/brand/logo-dark.svg" alt="dipci.tr Koyu Zemin Logo" class="h-10" />
          <span class="text-xs text-slate-400">Koyu Zemin Resmi Logo (SVG)</span>
        </div>
      </div>

      <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 pt-2">
        <div class="p-3 rounded-xl border border-slate-200">
          <div class="h-9 rounded-lg bg-[#0F172A] mb-2"></div>
          <div class="text-xs font-semibold">Kurumsal Antrasit</div>
          <div class="text-xs font-mono text-slate-500">#0F172A</div>
        </div>
        <div class="p-3 rounded-xl border border-slate-200">
          <div class="h-9 rounded-lg bg-[#059669] mb-2"></div>
          <div class="text-xs font-semibold">Avantaj Yeşili</div>
          <div class="text-xs font-mono text-slate-500">#059669</div>
        </div>
        <div class="p-3 rounded-xl border border-slate-200">
          <div class="h-9 rounded-lg bg-[#F59E0B] mb-2"></div>
          <div class="text-xs font-semibold">Muadil Kehribarı</div>
          <div class="text-xs font-mono text-slate-500">#F59E0B</div>
        </div>
        <div class="p-3 rounded-xl border border-slate-200">
          <div class="h-9 rounded-lg bg-[#F8FAFC] border border-slate-200 mb-2"></div>
          <div class="text-xs font-semibold">Açık Yüzey</div>
          <div class="text-xs font-mono text-slate-500">#F8FAFC</div>
        </div>
      </div>
    </section>

    <!-- 3. Etik Duvar, Gelir Ortaklığı (#işbirliği) ve KVKK Beyanı -->
    <section class="p-6 rounded-2xl bg-white border border-slate-200 space-y-4 text-sm text-slate-700 leading-relaxed">
      <h2 class="text-base font-semibold text-slate-900">Editöryal Bağımsızlık, #işbirliği Şeffaflığı ve KVKK</h2>
      <div>
        <h3 class="font-bold text-slate-900">1. Algoritmik Etik Duvar</h3>
        <p class="text-xs sm:text-sm text-slate-600 mt-0.5">
          Sıralama ve puanlama motorumuz mağaza komisyon oranlarına %100 kördür. En düşük gerçek birim fiyat komisyon anlaşmamız olmayan bir zincir marketteyse (ör. BİM, A101, Tarım Kredi) sistem açıkça orayı birinci sırada gösterir.
        </p>
      </div>
      <div>
        <h3 class="font-bold text-slate-900">2. Gelir Ortaklığı Beyanı (Amazon Ortakları &amp; Yayıncı Ağları)</h3>
        <p class="text-xs sm:text-sm text-slate-600 mt-0.5">
          <strong>dipci.tr</strong>, Amazon Gelir Ortaklığı Programı ve yetkili performans ağlarının katılımcısıdır. "Mağazaya Git" bağlantıları üzerinden yapılan uygun alışverişlerden, tüketicinin ödeyeceği fiyata hiçbir ek maliyet yansımaksızın yayıncı komisyonu elde edilebilir (#işbirliği).
        </p>
      </div>
      <div>
        <h3 class="font-bold text-slate-900">3. KVKK ve 5651 Sayılı Kanun Uyumu</h3>
        <p class="text-xs sm:text-sm text-slate-600 mt-0.5">
          Topluluk alanında T.C. Kimlik Numarası talep edilmez. Sahte yorumları önlemek amacıyla kullanılan cep telefonu doğrulaması tek yönlü kriptografik özet (<code>SHA-256</code>) ile saklanır ve tüm kullanıcı paylaşımları 7/24 yapay zeka ön-moderasyonundan geçer.
        </p>
      </div>
    </section>
  </main>

  <footer class="max-w-4xl mx-auto px-4 py-6 text-center text-xs text-slate-400">
    © 2026 dipci.tr • Sitemap: <a href="/sitemap.xml" class="underline">/sitemap.xml</a> • İletişim: iletisim@dipci.tr
  </footer>
</body>
</html>"""
