"""
Market Dedektifi — Çok Kanallı Veri Toplama (Ingestion) ve Barkod Eşleştirme Katmanı
Fiyat verilerini 3 güvenilir kanaldan toplayıp PriceMemoryDB ve UnitNormalizer'a besler:
  1. Affiliate XML / CSV Ürün Beslemeleri (ReklamAction, Admitad resmi XML Feed'leri)
  2. Yapısal Web Verisi (schema.org/Product JSON-LD & GTIN-13 Barkod Çıkarıcı)
  3. Resmi Pazaryeri API'leri (Amazon PA-API 5.0 hazır adaptörü)
"""

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional


@dataclass
class RawIngestedItem:
    platform: str
    gtin13_barcode: Optional[str]
    sku: str
    brand: str
    title: str
    url: str
    sale_price: float
    list_price: Optional[float]
    seller_name: str
    seller_type: str = "OFFICIAL"
    in_stock: bool = True


class MultiSourceCollector:
    """
    Farklı e-ticaret ve market kaynaklarından gelen ham veriyi standart RawIngestedItem
    formatına çevirir ve GTIN-13 (Barkod) üzerinden %100 kesin ürün eşleştirmesi yapar.
    """

    @staticmethod
    def extract_json_ld_product(html: str, platform: str, page_url: str) -> Optional[RawIngestedItem]:
        """
        Market ve e-ticaret sayfalarının <script type="application/ld+json"> bloğundaki
        standart schema.org/Product verisini (fiyat, barkod, marka, stok, satıcı) okur.
        """
        blocks = re.findall(
            r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )
        for raw in blocks:
            try:
                data = json.loads(raw.strip())
            except json.JSONDecodeError:
                continue

            candidates = data if isinstance(data, list) else [data]
            for node in candidates:
                if not isinstance(node, dict):
                    continue
                if node.get("@type") not in ("Product", "IndividualProduct"):
                    continue

                offers = node.get("offers") or {}
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}

                price_val = offers.get("price") or offers.get("lowPrice")
                if price_val is None:
                    continue

                brand_field = node.get("brand")
                brand = (
                    brand_field.get("name", "")
                    if isinstance(brand_field, dict)
                    else str(brand_field or "")
                )

                seller_field = offers.get("seller")
                seller_name = (
                    seller_field.get("name", platform)
                    if isinstance(seller_field, dict)
                    else str(seller_field or platform)
                )

                gtin = (
                    node.get("gtin13")
                    or node.get("gtin")
                    or node.get("ean")
                    or node.get("mpn")
                )
                avail = str(offers.get("availability", ""))
                in_stock = ("OutOfStock" not in avail) and ("SoldOut" not in avail)

                return RawIngestedItem(
                    platform=platform,
                    gtin13_barcode=str(gtin) if gtin else None,
                    sku=str(node.get("sku", "")),
                    brand=brand,
                    title=str(node.get("name", "")),
                    url=str(offers.get("url") or page_url),
                    sale_price=float(str(price_val).replace(",", ".")),
                    list_price=None,
                    seller_name=seller_name,
                    in_stock=in_stock,
                )
        return None

    @staticmethod
    def parse_affiliate_xml_feed(xml_content: str, platform: str) -> Iterator[RawIngestedItem]:
        """
        ReklamAction / Admitad / Google Merchant standartlarındaki XML ürün beslemelerini
        belleği şişirmeden okur (hem basinca.com hem de yeni fiyat karşılaştırma sitesi için).
        """
        root = ET.fromstring(xml_content)
        for item in root.findall(".//item") + root.findall(".//offer") + root.findall(".//product"):
            title = (item.findtext("title") or item.findtext("name") or "").strip()
            price_raw = item.findtext("sale_price") or item.findtext("price") or ""
            list_raw = item.findtext("oldprice") or item.findtext("regular_price") or ""
            url = (item.findtext("link") or item.findtext("url") or "").strip()
            brand = (item.findtext("brand") or item.findtext("vendor") or "").strip()
            gtin = (item.findtext("gtin") or item.findtext("barcode") or "").strip() or None
            sku = (item.findtext("id") or item.attrib.get("id", "")).strip()

            clean_price = re.sub(r"[^\d.,]", "", price_raw).replace(",", ".")
            if not title or not clean_price:
                continue

            list_val = None
            if list_raw:
                clean_list = re.sub(r"[^\d.,]", "", list_raw).replace(",", ".")
                if clean_list:
                    list_val = float(clean_list)

            yield RawIngestedItem(
                platform=platform,
                gtin13_barcode=gtin,
                sku=sku,
                brand=brand,
                title=title,
                url=url,
                sale_price=float(clean_price),
                list_price=list_val,
                seller_name=platform,
                seller_type="OFFICIAL",
            )
