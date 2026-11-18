"""
dipci.tr — Otomatik Çok Mağazalı Katalog Genişletme, Dinamik Ürün Sayfası Üretici
ve Akıllı XML Ürün Beslemesi Motoru (Google SEO / Akakçe Ölçeği).

Üç kanalı tek motor altında birleştirir:
1. Geniş Kapsamlı Otomatik Hasat (Broad Catalog Harvester):
   Zincir marketlerden (BİM, A101, ŞOK, Migros, CarrefourSA, Tarım Kredi, Hakmar)
   ve online pazaryerlerinden (Amazon, Hepsiburada, Trendyol, MediaMarkt, Teknosa,
   Vatan, n11, Pazarama, PttAVM, İdefix, Koçtaş, Çiçeksepeti) çok mağazalı binlerce
   ürünü çekip her birine kalıcı `/urun/<slug>` ve `/barkod/<gtin>` sayfası açar.
2. Dinamik Arama / Lens / Doğrudan URL Sayfa Üretici:
   Kullanıcı veya Googlebot henüz katalogda olmayan bir ürünü aradığında, kamerayla
   okuttuğunda veya doğrudan `/urun/...` adresine geldiğinde canlı mağazaları sorgulayıp
   ürünü veritabanına kalıcı olarak kaydeder ve IndexNow ile arama motorlarına bildirir.
3. Evrensel İş Ortağı XML Besleme Üreticisi:
   Google Merchant (`g:gtin`, `g:price`), Cimri/Akakçe (`<urun>`, `<fiyat>`, `<barkod>`)
   ve ReklamAction/Admitad (`<offer>`) formatlarındaki XML beslemelerinden yeni ürünleri
   otomatik oluşturur ve mevcut ürünlerle barkod/slug üzerinden birleştirir.
"""

import argparse
import hashlib
import io
import json
import re
import statistics
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from lens_engine import (
    AMAZON_ASSOCIATE_TAG,
    KNOWN_BRANDS,
    DipciLensEngine,
    _apply_affiliate_url,
    _extract_pack_info_from_text,
    _seller_to_platform_key,
)
from live_sync import (
    MARKET_API_URL,
    MARKET_META,
    _norm_tr,
    extract_direct_url_from_image,
    ping_indexnow,
)
from memory_db import PriceMemoryDB
from variant_guard import is_same_variant
from scanner import compute_ean13_with_check
from seo_engine import CANONICAL_ORIGIN, key_to_slug
import ev_baglanti
ev_baglanti.kur()  # engellenen sitelere Kemal'in ev internetinden git (17 Kas 2026)

_DB_WRITE_LOCK = threading.RLock()
_DISCOVERY_CACHE: Dict[str, Tuple[float, Optional[str]]] = {}
_DISCOVERY_TTL_SECONDS = 1800.0  # 30 dakika tekrarlı dış sorgu koruması


# ==================== GENİŞ KAPSAMLI TÜRKİYE ÜRÜN HASAT TOHUMLARI ====================

MARKET_HARVEST_SEEDS: List[Tuple[str, str]] = [
    # Süt, Peynir, Kahvaltılık
    ("beyaz peynir", "gida"),
    ("süzme peynir", "gida"),
    ("kaşar peyniri", "gida"),
    ("tulum peyniri", "gida"),
    ("labne peyniri", "gida"),
    ("krem peynir", "gida"),
    ("örgü dil peyniri", "gida"),
    ("hellim peyniri", "gida"),
    ("tam yağlı süt", "gida"),
    ("yarım yağlı süt", "gida"),
    ("laktozsuz süt", "gida"),
    ("kakaolu süt 6x", "gida"),
    ("kefir", "gida"),
    ("ayran", "gida"),
    ("yoğurt", "gida"),
    ("süzme yoğurt", "gida"),
    ("tereyağı", "gida"),
    ("margarin", "gida"),
    ("siyah zeytin", "gida"),
    ("yeşil zeytin", "gida"),
    ("çiçek balı", "gida"),
    ("çam balı", "gida"),
    ("tahin", "gida"),
    ("pekmez", "gida"),
    ("reçel", "gida"),
    ("fındık kreması", "gida"),
    ("fıstık ezmesi", "gida"),
    ("dana sucuk", "gida"),
    ("hindi salam", "gida"),
    ("yumurta", "gida"),
    ("mısır gevreği yulaf", "gida"),
    # Temel Mutfak, Yağ, Bakliyat, Konserve
    ("zeytinyağı", "gida"),
    ("ayçiçek yağı", "gida"),
    ("mısırözü yağı", "gida"),
    ("osmancık pirinç", "gida"),
    ("baldo pirinç", "gida"),
    ("pilavlık bulgur", "gida"),
    ("kırmızı mercimek", "gida"),
    ("yeşil mercimek", "gida"),
    ("nohut", "gida"),
    ("kuru fasulye", "gida"),
    ("makarna", "gida"),
    ("arpa şehriye", "gida"),
    ("buğday unu", "gida"),
    ("toz şeker", "gida"),
    ("küp şeker", "gida"),
    ("domates salçası", "gida"),
    ("biber salçası", "gida"),
    ("ton balığı", "gida"),
    ("garnitür konserve mısır", "gida"),
    ("ketçap", "gida"),
    ("mayonez", "gida"),
    ("nar ekşisi", "gida"),
    ("üzüm elma sirkesi", "gida"),
    ("hazır çorba", "gida"),
    # Çay, Kahve, İçecek
    ("siyah çay", "gida"),
    ("demlik poşet çay", "gida"),
    ("bitki çayı", "gida"),
    ("türk kahvesi", "gida"),
    ("granül kahve", "gida"),
    ("filtre kahve", "gida"),
    ("maden suyu", "gida"),
    ("doğal kaynak suyu", "gida"),
    ("kola gazlı içecek", "gida"),
    ("soğuk çay", "gida"),
    ("meyve suyu", "gida"),
    ("şalgam suyu", "gida"),
    # Atıştırmalık, Çikolata, Bisküvi
    ("kare çikolata", "gida"),
    ("çikolatalı gofret", "gida"),
    ("petibör bisküvi", "gida"),
    ("yulaflı bisküvi", "gida"),
    ("tuzlu kraker", "gida"),
    ("kek", "gida"),
    ("patates cipsi", "gida"),
    ("kavrulmuş fındık badem", "gida"),
    ("ay çekirdeği", "gida"),
    # Temizlik & Kağıt Ürünleri
    ("bulaşık makinesi kapsülü", "temizlik"),
    ("bulaşık makinesi tableti", "temizlik"),
    ("sıvı bulaşık deterjanı", "temizlik"),
    ("toz çamaşır deterjanı", "temizlik"),
    ("sıvı çamaşır deterjanı", "temizlik"),
    ("çamaşır yumuşatıcı", "temizlik"),
    ("çamaşır suyu", "temizlik"),
    ("yüzey temizleyici", "temizlik"),
    ("yağ çözcü sprey", "temizlik"),
    ("kireç sökücü", "temizlik"),
    ("tuvalet kağıdı", "temizlik"),
    ("kağıt havlu", "temizlik"),
    ("peçete", "temizlik"),
    ("çöp torbası", "temizlik"),
    ("bulaşık süngeri", "temizlik"),
    # Kişisel Bakım
    ("şampuan", "kisisel_bakim"),
    ("saç kremi", "kisisel_bakim"),
    ("duş jeli", "kisisel_bakim"),
    ("sıvı sabun", "kisisel_bakim"),
    ("kalıp sabun", "kisisel_bakim"),
    ("diş macunu", "kisisel_bakim"),
    ("diş fırçası", "kisisel_bakim"),
    ("ağız bakım suyu", "kisisel_bakim"),
    ("deodorant roll on", "kisisel_bakim"),
    ("tıraş bıçağı köpüğü", "kisisel_bakim"),
    ("el yüz kremi", "kisisel_bakim"),
    ("güneş kremi", "kisisel_bakim"),
    ("hijyenik ped", "kisisel_bakim"),
    # Anne & Bebek + Evcil Hayvan
    ("bebek bezi", "anne_bebek"),
    ("ıslak mendil", "anne_bebek"),
    ("bebek şampuanı", "anne_bebek"),
    ("devam sütü bebek maması", "anne_bebek"),
    ("kedi maması", "gida"),
    ("köpek maması", "gida"),
    # Türkiye'nin En Çok Aranan Ana Markaları (Tam SKU Kapsaması)
    ("Ülker", "gida"),
    ("Eti", "gida"),
    ("Torku", "gida"),
    ("Sütaş", "gida"),
    ("İçim", "gida"),
    ("Pınar", "gida"),
    ("Tahsildaroğlu", "gida"),
    ("Muratbey", "gida"),
    ("Bahçıvan", "gida"),
    ("Yayla", "gida"),
    ("Reis", "gida"),
    ("Duru bakliyat", "gida"),
    ("Tat", "gida"),
    ("Öncü", "gida"),
    ("Tukaş", "gida"),
    ("Filiz makarna", "gida"),
    ("Barilla", "gida"),
    ("Pastavilla", "gida"),
    ("Nescafe", "gida"),
    ("Jacobs", "gida"),
    ("Tchibo", "gida"),
    ("Mehmet Efendi", "gida"),
    ("Kahve Dünyası", "gida"),
    ("Lipton", "gida"),
    ("Çaykur", "gida"),
    ("Doğadan", "gida"),
    ("Doğuş çay", "gida"),
    ("Coca Cola", "gida"),
    ("Pepsi", "gida"),
    ("Beypazarı", "gida"),
    ("Erikli", "gida"),
    ("Dimes", "gida"),
    ("Cappy", "gida"),
    ("Dr Oetker", "gida"),
    ("Knorr", "gida"),
    ("Calve", "gida"),
    ("Heinz", "gida"),
    ("Nutella", "gida"),
    ("Tadelle", "gida"),
    ("Sarelle", "gida"),
    ("Kinder", "gida"),
    ("Lays", "gida"),
    ("Doritos", "gida"),
    ("Ruffles", "gida"),
    ("Pringles", "gida"),
    ("Komili", "gida"),
    ("Tariş", "gida"),
    ("Yudum", "gida"),
    ("Marmarabirlik", "gida"),
    ("Fairy", "temizlik"),
    ("Finish", "temizlik"),
    ("Pril", "temizlik"),
    ("Ariel", "temizlik"),
    ("Persil", "temizlik"),
    ("Omo", "temizlik"),
    ("Perwoll", "temizlik"),
    ("Bingo", "temizlik"),
    ("Tursil", "temizlik"),
    ("Yumoş", "temizlik"),
    ("Vernel", "temizlik"),
    ("Domestos", "temizlik"),
    ("Cif", "temizlik"),
    ("Asperox", "temizlik"),
    ("Selpak", "temizlik"),
    ("Solo", "temizlik"),
    ("Familia", "temizlik"),
    ("Papia", "temizlik"),
    ("Maylo", "temizlik"),
    ("Sleepy", "anne_bebek"),
    ("Molfix", "anne_bebek"),
    ("Prima", "anne_bebek"),
    ("Dalin", "anne_bebek"),
    ("Uni Baby", "anne_bebek"),
    ("Elidor", "kisisel_bakim"),
    ("Clear", "kisisel_bakim"),
    ("Pantene", "kisisel_bakim"),
    ("Head Shoulders", "kisisel_bakim"),
    ("Elseve", "kisisel_bakim"),
    ("Gliss", "kisisel_bakim"),
    ("Bioblas", "kisisel_bakim"),
    ("Dove", "kisisel_bakim"),
    ("Palmolive", "kisisel_bakim"),
    ("Activex", "kisisel_bakim"),
    ("Sensodyne", "kisisel_bakim"),
    ("Colgate", "kisisel_bakim"),
    ("Parodontax", "kisisel_bakim"),
    ("Signal", "kisisel_bakim"),
    ("Orkid", "kisisel_bakim"),
    ("Kotex", "kisisel_bakim"),
    ("Molped", "kisisel_bakim"),
    ("Nivea", "kisisel_bakim"),
    ("Rexona", "kisisel_bakim"),
    ("Axe", "kisisel_bakim"),
    ("Gillette", "kisisel_bakim"),
    ("Neutrogena", "kisisel_bakim"),
    ("Garnier", "kisisel_bakim"),
    ("Whiskas", "evcil_hayvan"),
    ("Felix", "evcil_hayvan"),
    ("Purina", "evcil_hayvan"),
    ("Gourmet kedi", "evcil_hayvan"),
    ("Pedigree", "evcil_hayvan"),
    ("Friskies", "evcil_hayvan"),
    ("Goody mama", "evcil_hayvan"),
    ("Jungle mama", "evcil_hayvan"),
    ("kedi maması", "evcil_hayvan"),
    ("köpek maması", "evcil_hayvan"),
    ("kedi kumu", "evcil_hayvan"),
    ("kuş yemi", "evcil_hayvan"),
    ("Duracell", "saglik_spor_outdoor"),
    ("Varta pil", "saglik_spor_outdoor"),
    ("kalem pil ince pil", "saglik_spor_outdoor"),
    ("vitamin efervesan", "saglik_spor_outdoor"),
    ("multivitamin", "saglik_spor_outdoor"),
    ("omega 3", "saglik_spor_outdoor"),
    ("pastil boğaz", "saglik_spor_outdoor"),
    ("yara bandı", "saglik_spor_outdoor"),
    ("Durex", "saglik_spor_outdoor"),
    ("Okey prezervatif", "saglik_spor_outdoor"),
    ("yüksek protein süt", "saglik_spor_outdoor"),
    ("protein bar", "saglik_spor_outdoor"),
    ("termos matara", "saglik_spor_outdoor"),
    ("Paşabahçe", "ev_yasam_mutfak"),
    ("Lav bardak", "ev_yasam_mutfak"),
    ("Borcam", "ev_yasam_mutfak"),
    ("Tefal", "ev_yasam_mutfak"),
    ("Korkmaz", "ev_yasam_mutfak"),
    ("Arzum", "ev_yasam_mutfak"),
    ("Vileda", "ev_yasam_mutfak"),
    ("Parex", "ev_yasam_mutfak"),
    ("Scotch Brite", "ev_yasam_mutfak"),
    ("saklama kabı", "ev_yasam_mutfak"),
    ("cam kavanoz", "ev_yasam_mutfak"),
    ("su bardağı çay bardağı", "ev_yasam_mutfak"),
    ("tava tencere", "ev_yasam_mutfak"),
    ("led ampul", "ev_yasam_mutfak"),
    ("grup priz uzatma", "ev_yasam_mutfak"),
    ("ütü masası çamaşır kurutmalık", "ev_yasam_mutfak"),
    ("Streç film alüminyum folyo", "temizlik"),
    ("pişirme kağıdı", "temizlik"),
    ("buzdolabı poşeti", "temizlik"),
    ("dondurma", "gida"),
    ("dondurulmuş patates", "gida"),
    ("piliç bonfile baget", "gida"),
    ("dana kıyma kuşbaşı", "gida"),
    ("baharat karabiber pul biber", "gida"),
    ("kabartma tozu vanilin", "gida"),
    ("puding krem şanti", "gida"),
    ("helva tahin helvası", "gida"),
    ("zeytin ezmesi", "gida"),
    ("sosis", "gida"),
    ("kavurma pastırma", "gida"),
    ("krema süt kreması", "gida"),
    ("soda meyveli", "gida"),
    ("enerji içeceği", "gida"),
    ("gofret", "gida"),
    ("bisküvi", "gida"),
    ("kurabiye", "gida"),
    ("sakız şekerleme", "gida"),
    ("bulaşık eldiveni", "temizlik"),
    ("cam temizleyici", "temizlik"),
    ("oda kokusu", "temizlik"),
    ("kolonya", "kisisel_bakim"),
    ("pamuk makyaj temizleme", "kisisel_bakim"),
    ("kulak çubuğu", "kisisel_bakim"),
    ("biberon emzik", "anne_bebek"),
    ("pişik kremi", "anne_bebek"),
]

# Genişletilmiş tohumlar + bilinen markalar (Kemal 16 Kas 2026: ürün sayısında rakiplere yetişmek)
EXTRA_HARVEST_SEEDS: List[Tuple[str, str]] = [
    ("pirinç", "gida"),
    ("bulgur", "gida"),
    ("kırmızı mercimek", "gida"),
    ("yeşil mercimek", "gida"),
    ("nohut", "gida"),
    ("kuru fasulye", "gida"),
    ("makarna", "gida"),
    ("spagetti", "gida"),
    ("erişte", "gida"),
    ("un", "gida"),
    ("toz şeker", "gida"),
    ("kesme şeker", "gida"),
    ("tuz", "gida"),
    ("ayçiçek yağı", "gida"),
    ("zeytinyağı", "gida"),
    ("mısırözü yağı", "gida"),
    ("margarin", "gida"),
    ("tereyağı", "gida"),
    ("yumurta", "gida"),
    ("bal", "gida"),
    ("reçel", "gida"),
    ("tahin", "gida"),
    ("pekmez", "gida"),
    ("fındık ezmesi", "gida"),
    ("fıstık ezmesi", "gida"),
    ("siyah zeytin", "gida"),
    ("yeşil zeytin", "gida"),
    ("sucuk", "gida"),
    ("salam", "gida"),
    ("sosis", "gida"),
    ("pastırma", "gida"),
    ("tavuk göğsü", "gida"),
    ("kıyma", "gida"),
    ("ton balığı", "gida"),
    ("domates salçası", "gida"),
    ("biber salçası", "gida"),
    ("ketçap", "gida"),
    ("mayonez", "gida"),
    ("hardal", "gida"),
    ("sirke", "gida"),
    ("bulyon", "gida"),
    ("hazır çorba", "gida"),
    ("konserve bezelye", "gida"),
    ("mısır konservesi", "gida"),
    ("turşu", "gida"),
    ("poşet çay", "gida"),
    ("demlik poşet çay", "gida"),
    ("yeşil çay", "gida"),
    ("bitki çayı", "gida"),
    ("türk kahvesi", "gida"),
    ("filtre kahve", "gida"),
    ("hazır kahve", "gida"),
    ("3ü1 arada kahve", "gida"),
    ("kakao", "gida"),
    ("sıcak çikolata", "gida"),
    ("sütlü çikolata", "gida"),
    ("bitter çikolata", "gida"),
    ("gofret", "gida"),
    ("bisküvi", "gida"),
    ("kraker", "gida"),
    ("kek", "gida"),
    ("kurabiye", "gida"),
    ("patates cipsi", "gida"),
    ("mısır cipsi", "gida"),
    ("leblebi", "gida"),
    ("ay çekirdeği", "gida"),
    ("kaju", "gida"),
    ("badem", "gida"),
    ("ceviz", "gida"),
    ("lokum", "gida"),
    ("helva", "gida"),
    ("sakız", "gida"),
    ("şekerleme", "gida"),
    ("dondurma", "gida"),
    ("maden suyu", "gida"),
    ("doğal kaynak suyu", "gida"),
    ("kola", "gida"),
    ("gazlı içecek", "gida"),
    ("meyve suyu", "gida"),
    ("tost ekmeği", "gida"),
    ("lavaş", "gida"),
    ("galeta", "gida"),
    ("mısır gevreği", "gida"),
    ("yulaf ezmesi", "gida"),
    ("granola", "gida"),
    ("puding", "gida"),
    ("krem şanti", "gida"),
    ("instant maya", "gida"),
    ("kabartma tozu", "gida"),
    ("pul biber", "gida"),
    ("karabiber", "gida"),
    ("kimyon", "gida"),
    ("nane", "gida"),
    ("kekik", "gida"),
    ("irmik", "gida"),
    ("nişasta", "gida"),
    ("köftelik bulgur", "gida"),
    ("yufka", "gida"),
    ("mantı", "gida"),
    ("dondurulmuş pizza", "gida"),
    ("dondurulmuş patates", "gida"),
    ("sütlaç", "gida"),
    ("krema", "gida"),
    ("enerji içeceği", "gida"),
    ("soğuk çay", "gida"),
    ("ayran 1 lt", "gida"),
    ("süt 1 lt", "gida"),
    ("sıvı çamaşır deterjanı", "temizlik"),
    ("toz çamaşır deterjanı", "temizlik"),
    ("yumuşatıcı", "temizlik"),
    ("bulaşık deterjanı", "temizlik"),
    ("bulaşık makinesi tableti", "temizlik"),
    ("bulaşık makinesi parlatıcı", "temizlik"),
    ("çamaşır suyu", "temizlik"),
    ("yüzey temizleyici", "temizlik"),
    ("cam temizleyici", "temizlik"),
    ("kireç çözücü", "temizlik"),
    ("tuvalet temizleyici", "temizlik"),
    ("banyo temizleyici", "temizlik"),
    ("yağ çözücü", "temizlik"),
    ("sabun", "temizlik"),
    ("sıvı sabun", "temizlik"),
    ("arap sabunu", "temizlik"),
    ("oda spreyi", "temizlik"),
    ("çöp poşeti", "temizlik"),
    ("bulaşık süngeri", "temizlik"),
    ("ıslak mendil", "temizlik"),
    ("kağıt havlu", "temizlik"),
    ("tuvalet kağıdı", "temizlik"),
    ("peçete", "temizlik"),
    ("alüminyum folyo", "temizlik"),
    ("streç film", "temizlik"),
    ("şampuan", "kisisel_bakim"),
    ("saç kremi", "kisisel_bakim"),
    ("saç maskesi", "kisisel_bakim"),
    ("duş jeli", "kisisel_bakim"),
    ("deodorant", "kisisel_bakim"),
    ("roll on", "kisisel_bakim"),
    ("parfüm", "kisisel_bakim"),
    ("diş macunu", "kisisel_bakim"),
    ("diş fırçası", "kisisel_bakim"),
    ("ağız gargarası", "kisisel_bakim"),
    ("tıraş bıçağı", "kisisel_bakim"),
    ("tıraş köpüğü", "kisisel_bakim"),
    ("el kremi", "kisisel_bakim"),
    ("yüz kremi", "kisisel_bakim"),
    ("nemlendirici krem", "kisisel_bakim"),
    ("güneş kremi", "kisisel_bakim"),
    ("yüz temizleme jeli", "kisisel_bakim"),
    ("kolonya", "kisisel_bakim"),
    ("pamuk", "kisisel_bakim"),
    ("kulak çubuğu", "kisisel_bakim"),
    ("hijyenik ped", "kisisel_bakim"),
    ("günlük ped", "kisisel_bakim"),
    ("saç boyası", "kisisel_bakim"),
    ("saç spreyi", "kisisel_bakim"),
    ("wax", "kisisel_bakim"),
    ("bebek bezi", "anne_bebek"),
    ("bebek ıslak mendil", "anne_bebek"),
    ("bebek şampuanı", "anne_bebek"),
    ("bebek maması", "anne_bebek"),
    ("devam sütü", "anne_bebek"),
    ("bebek bisküvisi", "anne_bebek"),
    ("pişik kremi", "anne_bebek"),
    ("biberon", "anne_bebek"),
    ("kuru kedi maması", "evcil_hayvan"),
    ("yaş kedi maması", "evcil_hayvan"),
    ("köpek maması", "evcil_hayvan"),
    ("kedi kumu", "evcil_hayvan"),
    ("kuş yemi", "evcil_hayvan"),
    ("c vitamini", "saglik_spor_outdoor"),
    ("d vitamini", "saglik_spor_outdoor"),
    ("multivitamin", "saglik_spor_outdoor"),
    ("omega 3", "saglik_spor_outdoor"),
    ("protein tozu", "saglik_spor_outdoor"),
]

ONLINE_MARKETPLACE_SEEDS: List[Tuple[str, str]] = [
    # Küçük Ev Aletleri, Süpürge & Mutfak
    ("Arzum Okka Minio Türk Kahve Makinesi", "ev_yasam_mutfak"),
    ("Karaca Hatır Hüps Türk Kahve Makinesi", "ev_yasam_mutfak"),
    ("Philips Seri 2200 Tam Otomatik Espresso Makinesi", "ev_yasam_mutfak"),
    ("Philips LatteGo 5400 Kahve Makinesi", "ev_yasam_mutfak"),
    ("Nespresso Essenza Mini Kahve Makinesi", "ev_yasam_mutfak"),
    ("DeLonghi Magnifica S Espresso Kahve Makinesi", "ev_yasam_mutfak"),
    ("Philips Airfryer XXL Fritöz", "ev_yasam_mutfak"),
    ("Cosori Airfryer Sıcak Hava Fritözü", "ev_yasam_mutfak"),
    ("Xiaomi Smart Air Fryer 6.5 L", "ev_yasam_mutfak"),
    ("Tefal Actifry Fritöz", "ev_yasam_mutfak"),
    ("Arzum Shake'n Take Kişisel Blender", "ev_yasam_mutfak"),
    ("Braun MultiQuick El Blender Seti", "ev_yasam_mutfak"),
    ("Philips Daily Collection Blender Seti", "ev_yasam_mutfak"),
    ("Korkmaz Tostkolik Tost Makinesi", "ev_yasam_mutfak"),
    ("Arzum Tostçu Delux Tost Makinesi", "ev_yasam_mutfak"),
    ("Tefal Toast Expert Tost Makinesi", "ev_yasam_mutfak"),
    ("Korkmaz Çaytema Elektrikli Çaycı", "ev_yasam_mutfak"),
    ("Karaca Çaysever Robotea Çay Makinesi", "ev_yasam_mutfak"),
    ("Philips Azur Buharlı Ütü", "ev_yasam_mutfak"),
    ("Tefal Ultimate Pure Buharlı Ütü", "ev_yasam_mutfak"),
    ("Philips Buhar Kazanlı Ütü", "ev_yasam_mutfak"),
    ("Tefal Titanium Ingenio Tencere Tava Seti", "ev_yasam_mutfak"),
    ("Karaca Biogranit Tencere Seti", "ev_yasam_mutfak"),
    ("Korkmaz Astra Çelik Tencere Seti", "ev_yasam_mutfak"),
    ("Fissler Vitaquick Düdüklü Tencere 6 L", "ev_yasam_mutfak"),
    ("Paşabahçe Su Bardağı Seti", "ev_yasam_mutfak"),
    ("Dyson V15 Detect Kablosuz Süpürge", "ev_yasam_mutfak"),
    ("Dyson V12 Detect Slim Kablosuz Süpürge", "ev_yasam_mutfak"),
    ("Dyson V8 Absolute Dikey Süpürge", "ev_yasam_mutfak"),
    ("Philips SpeedPro Max Dikey Süpürge", "ev_yasam_mutfak"),
    ("Philips AquaTrio Kablosuz Süpürge", "ev_yasam_mutfak"),
    ("Philips Marathon Ultimate Toz Torbasız Süpürge", "ev_yasam_mutfak"),
    ("Roborock Q8 Max Robot Süpürge", "ev_yasam_mutfak"),
    ("Roborock S8 Robot Süpürge", "ev_yasam_mutfak"),
    ("Xiaomi Robot Vacuum S10 Plus", "ev_yasam_mutfak"),
    ("Xiaomi Robot Vacuum X20 Plus", "ev_yasam_mutfak"),
    ("Dreame L10s Ultra Robot Süpürge", "ev_yasam_mutfak"),
    ("Karcher K2 Basınçlı Yıkama Makinesi", "ev_yasam_mutfak"),
    ("Karcher SC3 Buharlı Temizlik Makinesi", "ev_yasam_mutfak"),
    # Kişisel Bakım Elektroniği & Dermokozmetik
    ("Dyson Airwrap Saç Şekillendirici", "kisisel_bakim"),
    ("Dyson Supersonic Saç Kurutma Makinesi", "kisisel_bakim"),
    ("Philips OneBlade Tıraş Makinesi", "kisisel_bakim"),
    ("Braun Series 5 Tıraş Makinesi", "kisisel_bakim"),
    ("Oral-B Pro 3 Şarjlı Diş Fırçası", "kisisel_bakim"),
    ("Oral-B CrossAction Yedek Başlık 4'lü", "kisisel_bakim"),
    ("Philips Lumea IPL Lazer Epilasyon", "kisisel_bakim"),
    ("La Roche-Posay Effaclar Yüz Yıkama Jeli 400 ml", "kisisel_bakim"),
    ("La Roche-Posay Anthelios Güneş Kremi 50 ml", "kisisel_bakim"),
    ("La Roche-Posay Cicaplast Baume B5 100 ml", "kisisel_bakim"),
    ("CeraVe Köpüren Temizleyici 473 ml", "kisisel_bakim"),
    ("CeraVe Nemlendirici Krem 340 g", "kisisel_bakim"),
    ("Bioderma Sensibio H2O Misel Su 500 ml", "kisisel_bakim"),
    ("Vichy Dercos Kepek Karşıtı Şampuan 390 ml", "kisisel_bakim"),
    # Elektronik, Telefon, Ses, Bilgisayar & TV
    ("Apple iPhone 16 Pro 128 GB", "teknoloji_beyaz_esya"),
    ("Apple iPhone 16 128 GB", "teknoloji_beyaz_esya"),
    ("Apple iPhone 15 128 GB", "teknoloji_beyaz_esya"),
    ("Apple iPhone 13 128 GB", "teknoloji_beyaz_esya"),
    ("Samsung Galaxy S24 Ultra 256 GB", "teknoloji_beyaz_esya"),
    ("Samsung Galaxy S24 256 GB", "teknoloji_beyaz_esya"),
    ("Samsung Galaxy S24 FE 256 GB", "teknoloji_beyaz_esya"),
    ("Samsung Galaxy A55 256 GB", "teknoloji_beyaz_esya"),
    ("Samsung Galaxy A35 256 GB", "teknoloji_beyaz_esya"),
    ("Samsung Galaxy A15 128 GB", "teknoloji_beyaz_esya"),
    ("Xiaomi Redmi Note 13 Pro 256 GB", "teknoloji_beyaz_esya"),
    ("Honor 200 256 GB Cep Telefonu", "teknoloji_beyaz_esya"),
    ("Apple AirPods 4 Kulaklık", "teknoloji_beyaz_esya"),
    ("Apple AirPods Pro 2. Nesil USB-C", "teknoloji_beyaz_esya"),
    ("Samsung Galaxy Buds3", "teknoloji_beyaz_esya"),
    ("JBL Tune 520BT Kablosuz Kulaklık", "teknoloji_beyaz_esya"),
    ("JBL Go 4 Bluetooth Hoparlör", "teknoloji_beyaz_esya"),
    ("JBL Flip 6 Bluetooth Hoparlör", "teknoloji_beyaz_esya"),
    ("Sony WH-1000XM5 Kulaklık", "teknoloji_beyaz_esya"),
    ("Anker Soundcore Life Q30 Kulaklık", "teknoloji_beyaz_esya"),
    ("Anker Powerbank 10000 mAh", "teknoloji_beyaz_esya"),
    ("Apple 20W USB-C Güç Adaptörü", "teknoloji_beyaz_esya"),
    ("Apple Watch Series 10 46mm", "teknoloji_beyaz_esya"),
    ("Apple Watch SE 2. Nesil 44mm", "teknoloji_beyaz_esya"),
    ("Huawei Watch GT 4 Akıllı Saat", "teknoloji_beyaz_esya"),
    ("Samsung Galaxy Watch7 44mm", "teknoloji_beyaz_esya"),
    ("Xiaomi Smart Band 9 Akıllı Bileklik", "teknoloji_beyaz_esya"),
    ("Apple iPad 10. Nesil 64 GB Wi-Fi", "teknoloji_beyaz_esya"),
    ("Apple iPad Air M2 11 inç", "teknoloji_beyaz_esya"),
    ("Samsung Galaxy Tab A9 Plus", "teknoloji_beyaz_esya"),
    ("Samsung Galaxy Tab S9 FE", "teknoloji_beyaz_esya"),
    ("Apple MacBook Air M3 13 inç", "teknoloji_beyaz_esya"),
    ("Apple MacBook Air M2 13 inç", "teknoloji_beyaz_esya"),
    ("Asus Vivobook 15 Dizüstü Bilgisayar", "teknoloji_beyaz_esya"),
    ("Lenovo IdeaPad Slim 3 Laptop", "teknoloji_beyaz_esya"),
    ("Logitech MX Master 3S Kablosuz Mouse", "teknoloji_beyaz_esya"),
    ("Logitech G102 Lightsync Oyuncu Mouse", "teknoloji_beyaz_esya"),
    ("Samsung 990 PRO 1 TB NVMe M.2 SSD", "teknoloji_beyaz_esya"),
    ("Sony PlayStation 5 Slim Oyun Konsolu", "teknoloji_beyaz_esya"),
    ("Sony DualSense PS5 Kablosuz Oyun Kolu", "teknoloji_beyaz_esya"),
    ("Samsung 55 inç 4K Crystal UHD Smart TV", "teknoloji_beyaz_esya"),
    ("LG 55 inç 4K Ultra HD Smart LED TV", "teknoloji_beyaz_esya"),
    ("TCL 55 inç 4K Google Smart TV", "teknoloji_beyaz_esya"),
    ("Bosch Serie 6 Çamaşır Makinesi 9 kg", "teknoloji_beyaz_esya"),
    ("Bosch 4 Programlı Bulaşık Makinesi", "teknoloji_beyaz_esya"),
    # Sağlık, Termos & Outdoor
    ("Stanley Quencher Pipetli Termos 1.18 L", "saglik_spor_outdoor"),
    ("Stanley Klasik Vakumlu Çelik Termos 1 L", "saglik_spor_outdoor"),
    ("Stanley Klasik Vakumlu Çelik Termos 1.9 L", "saglik_spor_outdoor"),
    ("Stanley Neverleak Termos Bardak 0.47 L", "saglik_spor_outdoor"),
    ("Supradyn All Day 30 Film Tablet Multivitamin", "saglik_spor_outdoor"),
    ("Ocean Plus 1200 mg Omega 3 50 Kapsül", "saglik_spor_outdoor"),
    ("Ocean Extramag Üçlü Magnezyum 60 Tablet", "saglik_spor_outdoor"),
    ("Omron M2 Basic Üst Koldan Tansiyon Aleti", "saglik_spor_outdoor"),
    ("Braun ThermoScan Kulaktan Ateş Ölçer", "saglik_spor_outdoor"),
    # Anne & Bebek + Petshop
    ("Mustela Yenidoğan Bebek Şampuanı 500 ml", "anne_bebek"),
    ("Sudocrem Bebek Bakım Kremi 125 g", "anne_bebek"),
    ("Aptamil 2 Devam Sütü 800 g", "anne_bebek"),
    ("Bebelac Gold 2 Devam Sütü 800 g", "anne_bebek"),
    ("SMA Optipro 2 Devam Sütü 800 g", "anne_bebek"),
    ("Philips Avent Doğal Tepkili Biberon", "anne_bebek"),
    ("Royal Canin Kısırlaştırılmış Kedi Maması 2 kg", "evcil_hayvan"),
    ("Royal Canin Sterilised Kedi Maması 4 kg", "evcil_hayvan"),
    ("Pro Plan Sterilised Somonlu Kedi Maması 3 kg", "evcil_hayvan"),
    ("Pro Plan Kuzu Etli Köpek Maması 3 kg", "evcil_hayvan"),
    ("N&D Düşük Tahıllı Kısır Kedi Maması 1.5 kg", "evcil_hayvan"),
    ("Ever Clean Kokusuz Topaklaşan Kedi Kumu 10 L", "evcil_hayvan"),
]


PRIVATE_LABEL_BRANDS_NORM: Set[str] = {
    "dost", "ucuz", "mis", "birma", "birinci", "piyale", "emin", "anadolu mutfagi",
    "eon", "clean", "mintax", "peros", "mendi", "blume", "bebe", "ekmekecik",
    "sabah", "kardelen", "binboğa", "tarim kredi", "coop", "carrefour", "migros",
    "bim", "a101", "sok", "hakmar",
}

ALL_VALID_CATEGORIES: Set[str] = {
    "gida",
    "temizlik",
    "kisisel_bakim",
    "anne_bebek",
    "teknoloji_beyaz_esya",
    "ev_yasam_mutfak",
    "evcil_hayvan",
    "saglik_spor_outdoor",
}

DIPCI_KATEGORILERI = {"gida", "temizlik", "kisisel_bakim", "anne_bebek", "teknoloji_beyaz_esya", "ev_yasam_mutfak",
                      "evcil_hayvan", "saglik_spor_outdoor", "yapi_oto_bahce", "giyim_aksesuar"}

UNITLESS_ITEM_CATEGORIES: Set[str] = {
    "yapi_oto_bahce",
    "giyim_aksesuar",
    "teknoloji_beyaz_esya",
    "ev_yasam_mutfak",
    "saglik_spor_outdoor",
}


EXTRA_KNOWN_BRANDS: Set[str] = {
    "whiskas", "felix", "purina", "gourmet", "pedigree", "friskies", "goody", "jungle",
    "champion", "ever clean", "proline", "reflex", "royal canin", "pro plan",
    "duracell", "varta", "energizer", "panasonic", "philips", "osram",
    "pasabahce", "lav", "borcam", "tefal", "korkmaz", "arzum", "karaca", "sinbo", "fakir",
    "vileda", "parex", "scotch brite", "macromax", "koroplast", "cook",
    "durex", "okey", "hansaplast", "band aid", "strepsils", "otrivine", "bepanthol",
    "supradyn", "redoxon", "ocean", "solgar", "pharmaton", "sambucol", "stanley",
    "fellas", "zuber", "proline", "chicco", "wee baby", "avent", "bebelac", "aptamil", "sma",
}


def classify_category(text: str, hint: str = "") -> str:
    # Kaynak kategoriyi kesin biliyorsa (Akakçe kategori ağacı, 17 Kas 2026) onu kullan
    if hint in DIPCI_KATEGORILERI:
        return hint
    norm = _norm_tr(text)
    full_norm = _norm_tr(f"{hint} {text}")

    pet_kw = (
        "kedi mamasi", "kopek mamasi", "kedi kumu", "yas mama", "kuru mama", "kus yemi",
        "kedi odul", "kopek odul", "konserve kedi", "konserve kopek", "yavru kedi", "kisir kedi",
        "whiskas", "felix", "royal canin", "pro plan", "ever clean", "purina", "gourmet",
        "pedigree", "friskies", "goody", "jungle",
    )
    if any(k in full_norm for k in pet_kw):
        return "evcil_hayvan"

    outdoor_health_kw = (
        "termos", "stanley", "matara", "multivitamin", "vitamin ", "efervesan", "omega 3",
        "extramag", "magnezyum", "supradyn", "redoxon", "solgar", "pharmaton", "sambucol",
        "tansiyon aleti", "ates olcer", "duracell", "varta", "alkalin pil", "kalem pil", "ince pil",
        "pastil", "yara bandi", "prezervatif", "kondom", "durex", "protein bar", "yuksek protein",
        "whey protein",
    )
    if any(k in full_norm for k in outdoor_health_kw):
        return "saglik_spor_outdoor"

    home_kitchen_kw = (
        "kahve makinesi", "espresso makinesi", "airfryer", "fritoz", "supurge", "roborock",
        "dreame", "dyson v", "blender", "mikser", "tost makinesi", "cayci", "cay makinesi",
        "buharli utu", "kazanli utu", "tencere", "tava", "duduklu", "bardak", "kadeh", "fincan",
        "kupa", "yemek takimi", "saklama kabi", "kavanoz", "borcam", "pasabahce", "lav ",
        "vileda", "parex", "temizlik seti", "mop", "paspas", "ampul", "grup priz", "uzatma kablo",
        "utu masasi", "kurutmalik", "brita", "su aritma", "basıncli yikama", "basincli yikama",
        "buharli temizlik", "karcher", "tefal", "korkmaz", "arzum", "karaca",
    )
    if any(k in norm for k in home_kitchen_kw):
        return "ev_yasam_mutfak"

    tech_kw = (
        "telefon", "iphone", "galaxy", "redmi", "honor ", "kulaklik", "airpods", "hoparlor",
        "akilli saat", "watch", "smart band", "bileklik", "tablet", "ipad", "macbook", "laptop",
        "vivobook", "ideapad", "bilgisayar", "mouse", "klavye", "monitor", "ssd", "bellek", "usb",
        "televizyon", "smart tv", "camasir makinesi", "bulasik makinesi", "buzdolabi", "klima",
        "playstation", "dualsense", "powerbank", "sarj kablo", "sarj aleti", "adaptor",
    )
    if any(k in norm for k in tech_kw):
        return "teknoloji_beyaz_esya"

    baby_kw = (
        "bebek bezi", "külot bez", "kulot bez", "islak mendil", "islak havlu", "devam sutu",
        "bebek mamasi", "kavanoz mama", "bebek sampuani", "pisik", "biberon", "emzik", "sudocrem",
        "mustela", "aptamil", "bebelac", "sma optipro", "molfix", "prima", "sleepy", "canbebe",
        "dalin", "uni baby", "wee baby", "chicco",
    )
    if any(k in full_norm for k in baby_kw):
        return "anne_bebek"

    care_kw = (
        "sampuan", "sac kremi", "sac sekillendirici", "sac kurutma", "airwrap", "supersonic",
        "sac boyasi", "dus jeli", "sivi sabun", "kalip sabun", "dis macunu", "dis fircasi",
        "yedek baslik", "agiz bakim", "deodorant", "roll on", "parfum", "kolonya", "tiras",
        "oneblade", "epilasyon", "agda", "lumea", "gunes kremi", "yuz yikama", "nemlendirici",
        "el kremi", "vucut losyonu", "cicaplast", "misel su", "makyaj", "pamuk", "kulak cubugu",
        "hijyenik ped", "gunluk ped", "orkid", "kotex", "molped", "cerave", "bioderma", "la roche",
        "vichy", "sensodyne", "colgate", "parodontax", "signal", "gillette", "nivea", "neutrogena",
    )
    if any(k in norm for k in care_kw):
        return "kisisel_bakim"

    clean_kw = (
        "deterjan", "bulasik", "kapsul", "yumusatici", "camasir suyu", "yuzey temizleyici",
        "kirec sokucu", "yag cozucu", "tuvalet kagidi", "kagit havlu", "pecete", "cop torbasi",
        "strec film", "pisirme kagidi", "aluminyum folyo", "buzdolabi poseti", "sunger",
        "bulasik eldiveni", "cam temizleyici", "oda kokusu", "fairy", "finish", "pril", "ariel",
        "persil", "omo", "perwoll", "bingo", "domestos", "asperox", "selpak", "solo", "familia", "papia",
    )
    if any(k in norm for k in clean_kw):
        return "temizlik"

    if hint in ALL_VALID_CATEGORIES:
        return hint
    return "gida"


def extract_clean_brand_and_family(raw_brand: str, raw_title: str) -> Tuple[str, str]:
    title_clean = re.sub(r"\s+", " ", (raw_title or "").strip())
    brand_clean = re.sub(r"\s+", " ", (raw_brand or "").strip())

    t_norm = _norm_tr(title_clean)
    if not brand_clean:
        # En uzun eşleşen bilinen markayı bul
        for b_key in sorted(KNOWN_BRANDS.keys(), key=len, reverse=True):
            if re.search(rf"\b{re.escape(b_key)}\b", t_norm):
                brand_clean = KNOWN_BRANDS[b_key]
                break

    if not brand_clean:
        words = [w for w in title_clean.split() if len(w) >= 2]
        brand_clean = words[0].title() if words else "Genel"
    else:
        b_norm_lookup = _norm_tr(brand_clean)
        if b_norm_lookup in KNOWN_BRANDS:
            brand_clean = KNOWN_BRANDS[b_norm_lookup]
        elif brand_clean.isupper() or brand_clean.islower():
            brand_clean = brand_clean.title()

    # Başlığın başındaki tekrar eden marka adını soy ("Çaykur Çaykur Tiryaki" -> "Tiryaki")
    b_norm = _norm_tr(brand_clean)
    fam = title_clean
    fam_words = fam.split()
    b_word_cnt = len(brand_clean.split())
    if len(fam_words) > b_word_cnt:
        head_norm = _norm_tr(" ".join(fam_words[:b_word_cnt]))
        if head_norm == b_norm:
            fam = " ".join(fam_words[b_word_cnt:]).strip(" -–•:")

    if not fam:
        fam = title_clean
    return brand_clean, fam


def make_product_key(brand: str, family_name: str, pack_label: str = "") -> str:
    combined = f"{brand} {family_name}"
    if pack_label and pack_label.strip().lower() != "1 adet" and _norm_tr(pack_label) not in _norm_tr(combined):
        combined = f"{combined} {pack_label}"
    s = _norm_tr(combined)
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    # Çok uzun anahtarları anlamlı kelime sınırında kısalt
    if len(s) > 64:
        parts = s.split("_")
        acc: List[str] = []
        cur = 0
        for p in parts:
            if cur + len(p) + 1 > 60 and len(acc) >= 3:
                break
            acc.append(p)
            cur += len(p) + 1
        s = "_".join(acc)
    return s or "urun_genel"


def deterministic_ean13(product_key: str) -> str:
    digest = hashlib.sha256(product_key.encode("utf-8")).hexdigest()
    digits9 = "".join(str(int(ch, 16) % 10) for ch in digest[:9])
    return compute_ean13_with_check("868" + digits9)


def _parse_price_str(val: str) -> float:
    if not val:
        return 0.0
    s = re.sub(r"[^\d,\.]", "", str(val).strip())
    if not s:
        return 0.0
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        # "149,90" -> "149.90"
        parts = s.split(",")
        if len(parts[-1]) in (1, 2):
            s = "".join(parts[:-1]) + "." + parts[-1]
        else:
            s = "".join(parts)
    try:
        return round(float(s), 2)
    except Exception:
        return 0.0


class AutoCatalogEngine:
    def __init__(self, db: PriceMemoryDB, lens_engine: Optional[DipciLensEngine] = None):
        self.db = db
        self.lens = lens_engine or DipciLensEngine(db)

    def _resolve_or_create_product_key(
        self,
        brand: str,
        family_name: str,
        category: str,
        base_unit: str,
        net_amount: float,
        pack_label: str = "",
        gtin: str = "",
        image_url: str = "",
        model_code: str = "",
        commit: bool = True,
    ) -> Tuple[str, bool]:
        """
        Ürünün mevcut katalogda karşılığı varsa onun `product_key`'ini döner,
        yoksa temiz ve çakışmasız yeni bir `product_key` oluşturup kaydeder.
        Dönüş: `(product_key, is_newly_created)`
        """
        clean_gtin = "".join(ch for ch in (gtin or "") if ch.isdigit())
        if len(clean_gtin) in (8, 12, 13, 14):
            hit = self.db.lookup_by_barcode(clean_gtin)
            if hit and hit.get("product_key"):
                pkey = hit["product_key"]
                if image_url:
                    self.db.set_product_image(pkey, image_url, commit=commit)
                return pkey, False

        cand_key = make_product_key(brand, family_name, pack_label)
        row = self.db.conn.execute(
            "SELECT product_key, brand, reference_net_amount FROM canonical_products WHERE product_key = ?",
            (cand_key,),
        ).fetchone()
        if row:
            if image_url:
                self.db.set_product_image(cand_key, image_url, commit=commit)
            return cand_key, False

        # Aynı marka altında çok yakın isimli ve aynı gramajlı mevcut küratörlü ürün var mı kontrol et (O(1) indeksli)
        b_norm = _norm_tr(brand)
        fam_tokens = {
            w for w in re.findall(r"[a-z0-9]+", _norm_tr(family_name))
            if len(w) >= 3 and w not in ("tam", "yagli", "yarim", "klasik", "dogal", "paket", "adet", "boy")
        }
        if b_norm and fam_tokens:
            same_brand_rows = self.db.conn.execute(
                """
                SELECT product_key, brand, family_name, base_unit, reference_net_amount
                FROM canonical_products
                WHERE brand = ? COLLATE NOCASE AND is_private_label = 0
                """,
                (brand,),
            ).fetchall()
            for r in same_brand_rows:
                r_tokens = {
                    w for w in re.findall(r"[a-z0-9]+", _norm_tr(r["family_name"]))
                    if len(w) >= 3
                }
                if not r_tokens:
                    continue
                overlap = len(fam_tokens & r_tokens)
                if overlap >= max(2, min(len(fam_tokens), len(r_tokens))) and is_same_variant(family_name, r["family_name"]):
                    ref_a = float(r["reference_net_amount"] or 1.0)
                    if r["base_unit"] == base_unit and ref_a > 0 and net_amount > 0:
                        ratio = net_amount / ref_a
                        if 0.85 <= ratio <= 1.18:
                            pkey = r["product_key"]
                            if image_url:
                                self.db.set_product_image(pkey, image_url, commit=commit)
                            return pkey, False

        # Yeni kanonik ürün oluştur
        self.db.register_product(
            product_key=cand_key,
            brand=brand,
            family_name=family_name,
            category=category,
            base_unit=base_unit,
            reference_net_amount=max(net_amount, 0.01),
            is_private_label=False,
            official_model_code=model_code,
            image_url=image_url,
            commit=commit,
        )
        final_gtin = clean_gtin if len(clean_gtin) in (8, 12, 13, 14) else deterministic_ean13(cand_key)
        existing_bc = self.db.conn.execute(
            "SELECT gtin FROM product_barcodes WHERE gtin = ?", (final_gtin,)
        ).fetchone()
        if not existing_bc:
            self.db.register_barcode(
                gtin=final_gtin,
                product_key=cand_key,
                variant_label=pack_label or f"{net_amount:g} {base_unit}",
                net_amount=max(net_amount, 0.01),
                pack_tier="SINGLE",
                commit=commit,
            )
        return cand_key, True

    def persist_offers_for_product(
        self,
        raw_brand: str,
        raw_title: str,
        offers: List[Dict],
        pack_label: str = "",
        category_hint: str = "",
        gtin: str = "",
        image_url: str = "",
        ping_search_engines: bool = False,
        commit: bool = True,
        update_index: bool = True,
    ) -> Optional[str]:
        koliler = []
        valid_offers = [
            o for o in (offers or [])
            if float(o.get("price") or 0) > 1.0
            # Başlığı farklı varyant olan mağaza teklifini (ör. laktozsuz, %0,5 yağlı, zero) bu ürüne yazma
            and is_same_variant(raw_title, str(o.get("title") or raw_title))
        ]
        if not valid_offers:
            return None

        brand, family_name = extract_clean_brand_and_family(raw_brand, raw_title)
        if _norm_tr(brand) in PRIVATE_LABEL_BRANDS_NORM and len(valid_offers) < 2:
            return None

        p_info = _extract_pack_info_from_text(f"{pack_label} {raw_title}")
        category = classify_category(f"{brand} {family_name}", hint=category_hint)
        if category not in ("teknoloji_beyaz_esya", "yapi_oto_bahce", "giyim_aksesuar"):
            # Koli (12 paket, 4'lü) ve farklı gramaj (500 g / 1 kg) teklifleri tekli ürüne yazılmaz (Kemal, 18 Kas 2026)
            from teklif_denetim import uyumlu, koli_adedi
            ana = f"{raw_title} {pack_label}"
            koliler = [(o, koli_adedi(ana, str(o.get("title") or ""))) for o in valid_offers]
            koliler = [(o, n) for o, n in koliler if n]  # koli: tekliye yazılmaz, "toplu alım" olarak ayrı tutulur
            valid_offers = [o for o in valid_offers if uyumlu(ana, str(o.get("title") or raw_title))]
            if not valid_offers:
                return None
        if category in UNITLESS_ITEM_CATEGORIES:
            net_amount = 1.0
            base_unit = "adet"
            p_lbl = pack_label or "1 adet"
        elif p_info:
            net_amount, base_unit, p_lbl = p_info
        else:
            net_amount, base_unit, p_lbl = 1.0, "adet", (pack_label or "1 adet")

        # Gramaj etiketi aile adında yoksa okunabilirlik için ekle (farklı gramajların birbirini ezmesini önler)
        if (
            category not in UNITLESS_ITEM_CATEGORIES
            and p_info
            and p_lbl
            and not re.search(r"\b\d+\s*(?:kg|gr|g|lt|litre|l|ml|adet|rulo|kapsül|yıkama)\b", family_name, re.I)
        ):
            family_name = f"{family_name} ({p_lbl})"

        now = datetime.now()
        today_prefix = now.strftime("%Y-%m-%d")

        with _DB_WRITE_LOCK:
            pkey, is_new = self._resolve_or_create_product_key(
                brand=brand,
                family_name=family_name,
                category=category,
                base_unit=base_unit,
                net_amount=net_amount,
                pack_label=p_lbl,
                gtin=gtin,
                image_url=image_url,
                commit=False,
            )

            if koliler:
                from teklif_denetim import koli_kaydet
                for off, adet in koliler:
                    koli_kaydet(self.db.conn, pkey, str(off.get("platform") or "default").strip().lower(),
                                str(off.get("seller_name") or "").strip(), str(off.get("title") or "").strip(),
                                float(off["price"]), adet, _apply_affiliate_url(str(off.get("url") or "").strip()))

            for off in valid_offers:
                price = round(float(off["price"]), 2)
                off_pack = _extract_pack_info_from_text(str(off.get("title") or ""))
                if category in UNITLESS_ITEM_CATEGORIES:
                    item_amt = 1.0
                elif off_pack and off_pack[1] == base_unit and off_pack[0] > 0:
                    item_amt = off_pack[0]
                else:
                    item_amt = max(net_amount, 0.01)

                u_price = round(price / max(item_amt, 0.01), 2)

                plat = str(off.get("platform") or "default").strip().lower()
                seller = str(off.get("seller_name") or plat.upper()).strip()
                url = _apply_affiliate_url(str(off.get("url") or "").strip())
                title = str(off.get("title") or f"{brand} {family_name}").strip()

                self.db.conn.execute(
                    """
                    DELETE FROM price_snapshots
                    WHERE product_key = ? AND platform = ? AND seller_name = ?
                      AND substr(captured_at, 1, 10) = ?
                    """,
                    (pkey, plat, seller, today_prefix),
                )
                self.db.conn.execute(
                    """
                    INSERT INTO price_snapshots
                    (product_key, platform, seller_name, seller_type, seller_rating, title, url,
                     captured_at, list_price, sale_price, effective_pack_price, effective_unit_price,
                     net_amount, pack_tier, warranty_type, zero_interest_months, energy_class, model_code_seen)
                    VALUES (?, ?, ?, 'OFFICIAL', 9.5, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'TR_OFFICIAL', 0, '', '')
                    """,
                    (
                        pkey,
                        plat,
                        seller,
                        title,
                        url,
                        now.isoformat(),
                        off.get("list_price"),
                        price,
                        price,
                        u_price,
                        item_amt,
                        "MULTI_BULK" if item_amt > net_amount * 1.65 else "SINGLE",
                    ),
                )

            # Not: Eskiden ilk kez eklenen ürüne geriye tarihli "Piyasa Medyan Referansı" kayıtları
            # yazılıyordu (sahte geçmiş). Kaldırıldı; geçmiş yalnızca gerçek gözlemlerden oluşur.
            if commit:
                self.db.conn.commit()
            if update_index:
                self.db.rebuild_catalog_index([pkey])

        if ping_search_engines and is_new:
            seo_url = f"{CANONICAL_ORIGIN}/urun/{key_to_slug(pkey)}"
            threading.Thread(target=ping_indexnow, args=([seo_url],), daemon=True).start()

        return pkey

    def persist_lens_bundle(
        self,
        lens_res: Dict,
        ping_search_engines: bool = True,
        commit: bool = True,
        update_index: bool = True,
    ) -> Optional[str]:
        if not lens_res or not lens_res.get("ok"):
            return None
        offers = lens_res.get("offers") or []
        if not offers:
            return None
        pinfo = lens_res.get("product_info") or {}
        pkey = self.persist_offers_for_product(
            raw_brand=str(pinfo.get("brand") or ""),
            raw_title=str(pinfo.get("title") or lens_res.get("recognized_query") or ""),
            offers=offers,
            pack_label=str(pinfo.get("pack_size") or ""),
            category_hint=str(pinfo.get("category") or ""),
            gtin=str(pinfo.get("gtin") or ""),
            image_url=str(pinfo.get("image_url") or ""),
            ping_search_engines=ping_search_engines,
            commit=commit,
            update_index=update_index,
        )
        if pkey and isinstance(pinfo, dict):
            pinfo["local_product_key"] = pkey
            pinfo["seo_url"] = f"/urun/{key_to_slug(pkey)}"
        return pkey

    def discover_and_persist_query(self, query: str = "", detected_gtin: str = "") -> Optional[str]:
        """
        Arama çubuğundan veya doğrudan `/urun/<slug>` / `/barkod/<gtin>` isteğinden gelen
        ve yerel katalogda bulunmayan bir ürünü canlı mağazalarda arayıp kalıcı sayfa açar.
        """
        q_clean = re.sub(r"\s+", " ", (query or "").strip())
        g_clean = "".join(ch for ch in (detected_gtin or "") if ch.isdigit())
        cache_key = f"{_norm_tr(q_clean)}|{g_clean}"
        if not q_clean and not g_clean:
            return None

        now_ts = time.time()
        cached = _DISCOVERY_CACHE.get(cache_key)
        if cached and (now_ts - cached[0]) < _DISCOVERY_TTL_SECONDS:
            return cached[1]

        try:
            res = self.lens.analyze_and_compare(
                detected_gtin=g_clean,
                query_override=q_clean,
            )
            pkey = self.persist_lens_bundle(res, ping_search_engines=True)
            _DISCOVERY_CACHE[cache_key] = (now_ts, pkey)
            return pkey
        except Exception:
            _DISCOVERY_CACHE[cache_key] = (now_ts, None)
            return None

    # ==================== EVRENSEL XML ÜRÜN BESLEMESİ MOTORU ====================

    def ingest_partner_xml(self, xml_content: str | bytes, store_name: str) -> Tuple[int, List[str]]:
        """
        Google Merchant, Cimri/Akakçe ve ReklamAction/Admitad XML beslemelerini
        ad-alanı (namespace) bağımsız okur; barkod veya ürün adından eşleştirir,
        katalogda olmayan ürünleri otomatik oluşturur.
        """
        raw_bytes = xml_content.encode("utf-8", errors="ignore") if isinstance(xml_content, str) else xml_content
        plat_key_guess, seller_disp = _seller_to_platform_key(store_name, "")
        if plat_key_guess == "default":
            plat_key_guess = re.sub(r"[^a-z0-9]", "", _norm_tr(store_name))[:20] or "partner"
            seller_disp = store_name.strip()

        written = 0
        touched_keys: List[str] = []
        touched_urls: List[str] = []
        row_tags = {"item", "entry", "offer", "urun", "product"}

        try:
            ctx = ET.iterparse(io.BytesIO(raw_bytes), events=("end",))
            for _, elem in ctx:
                tag = elem.tag.split("}")[-1].lower()
                if tag not in row_tags:
                    continue

                fields: Dict[str, str] = {}
                for child in elem:
                    ctag = child.tag.split("}")[-1].lower()
                    val = (child.text or "").strip()
                    if val and ctag not in fields:
                        fields[ctag] = val
                    if ctag == "link" and not val and child.attrib.get("href"):
                        fields["link"] = child.attrib["href"].strip()

                title = (
                    fields.get("title")
                    or fields.get("urunadi")
                    or fields.get("name")
                    or fields.get("isim")
                    or fields.get("baslik")
                    or ""
                )
                url = (
                    fields.get("link")
                    or fields.get("url")
                    or fields.get("urunurl")
                    or ""
                )
                sale_raw = (
                    fields.get("sale_price")
                    or fields.get("indirimlifiyat")
                    or fields.get("price")
                    or fields.get("fiyat")
                    or fields.get("kdv_dahil_fiyat")
                    or ""
                )
                list_raw = (
                    fields.get("oldprice")
                    or fields.get("piyasafiyati")
                    or fields.get("eskifiyat")
                    or (fields.get("price") if fields.get("sale_price") else "")
                    or ""
                )
                brand = fields.get("brand") or fields.get("marka") or fields.get("vendor") or ""
                gtin = fields.get("gtin") or fields.get("barkod") or fields.get("barcode") or fields.get("ean") or ""
                img = (
                    fields.get("image_link")
                    or fields.get("resim")
                    or fields.get("resimurl")
                    or fields.get("picture")
                    or fields.get("image")
                    or ""
                )
                cat_txt = fields.get("product_type") or fields.get("kategori") or fields.get("google_product_category") or ""

                sale_p = _parse_price_str(sale_raw)
                list_p = _parse_price_str(list_raw)
                elem.clear()

                if not title or not url or sale_p <= 1.0:
                    continue

                pkey = self.persist_offers_for_product(
                    raw_brand=brand,
                    raw_title=title,
                    offers=[
                        {
                            "platform": plat_key_guess,
                            "seller_name": seller_disp,
                            "title": title,
                            "price": sale_p,
                            "list_price": list_p if list_p > sale_p else None,
                            "url": url,
                        }
                    ],
                    category_hint=cat_txt,
                    gtin=gtin,
                    image_url=img,
                    ping_search_engines=False,
                    commit=False,
                    update_index=False,
                )
                if pkey:
                    written += 1
                    touched_keys.append(pkey)
                    touched_urls.append(f"{CANONICAL_ORIGIN}/urun/{key_to_slug(pkey)}")
                    if written % 250 == 0:
                        self.db.conn.commit()
            self.db.conn.commit()
            if touched_keys:
                self.db.rebuild_catalog_index(list(dict.fromkeys(touched_keys)))
        except Exception as exc:
            self.db.conn.commit()
            print(f"[WARN] XML ayrıştırma uyarısı ({store_name}): {exc}")

        return written, list(dict.fromkeys(touched_urls))

    # ==================== GENİŞ ÇOK MAĞAZALI OTOMATİK KATALOG HASADI ====================

    @staticmethod
    def _fetch_mf_seed(seed_query: str, size: int = 24, page: int = 0) -> Tuple[List[Dict], int]:
        payload = json.dumps({"keywords": seed_query, "pages": page, "size": size}).encode("utf-8")
        req = urllib.request.Request(
            MARKET_API_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "DipciBot/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            return (data.get("content") or []), int(data.get("numberOfFound") or 0)
        except Exception:
            return [], 0

    @classmethod
    def _fetch_mf_seed_pages(cls, seed_query: str, max_pages: int = 4, size: int = 24) -> List[Dict]:
        all_items: List[Dict] = []
        first_items, total_found = cls._fetch_mf_seed(seed_query, size=size, page=0)
        if not first_items:
            return []
        all_items.extend(first_items)
        total_pages = (total_found + size - 1) // size if total_found > 0 else 1
        target_pages = min(max_pages, max(1, total_pages))
        for p in range(1, target_pages):
            time.sleep(0.6)  # yavaş tarama (16 Kas: hızlı tarama kesildi)
            more, _ = cls._fetch_mf_seed(seed_query, size=size, page=p)
            if not more:
                break
            all_items.extend(more)
            if len(more) < size:
                break
        return all_items

    @staticmethod
    def _canonical_cluster_key(brand: str, fam: str, p_info: Optional[Tuple[float, str, str]], ref_w: str) -> Tuple[str, str]:
        fam_stripped = re.sub(
            r"\b\d+(?:[\.,]\d+)?\s*(?:kg|gr|g|lt|litre|l|ml|adet|rulo|kapsül|kapsul|tablet|yıkama|yikama|li|lü|lu|lı)\b",
            " ",
            fam,
            flags=re.I,
        )
        fam_stripped = re.sub(r"\s+", " ", fam_stripped).strip(" ()-–•")
        if p_info:
            amt, u, _ = p_info
            if u == "kg":
                canon_pack = f"{int(round(amt * 1000))} g" if amt < 1.0 else f"{amt:g} kg"
            elif u == "litre":
                canon_pack = f"{int(round(amt * 1000))} ml" if amt < 1.0 else f"{amt:g} L"
            else:
                canon_pack = f"{int(round(amt))} {u}"
        else:
            canon_pack = ref_w
        return make_product_key(brand, fam_stripped or fam, canon_pack), canon_pack

    def reclassify_existing_catalog(self) -> int:
        """Mevcut kanonik ürünlerin kategorilerini yeni 8 ana kategori yapısına göre günceller."""
        rows = self.db.conn.execute(
            "SELECT product_key, brand, family_name, category FROM canonical_products WHERE is_private_label = 0"
        ).fetchall()
        updated = 0
        for r in rows:
            new_cat = classify_category(f"{r['brand']} {r['family_name']}", hint=r["category"])
            if new_cat != r["category"]:
                self.db.conn.execute(
                    "UPDATE canonical_products SET category = ? WHERE product_key = ?",
                    (new_cat, r["product_key"]),
                )
                updated += 1
        if updated:
            self.db.conn.commit()
        return updated

    def harvest_supermarket_catalog(
        self,
        seeds: Optional[List[Tuple[str, str]]] = None,
        min_stores: int = 2,
        max_pages: int = 4,
        include_known_brand_singles: bool = True,
    ) -> Dict[str, int]:
        """
        MarketFiyatı üzerinden yüzlerce tohum sorguyu çok sayfalı (`pages: 0..max_pages`) tarar;
        aynı marka + ürün + gramajdaki farklı market kayıtlarını tek kanonik üründe birleştirir.
        """
        if seeds:
            active_seeds = seeds
        else:
            _seen_q = set()
            active_seeds = []
            for q, c in MARKET_HARVEST_SEEDS + EXTRA_HARVEST_SEEDS + [(b, "") for b in KNOWN_BRANDS.values()]:
                k = _norm_tr(q)
                if k and k not in _seen_q:
                    _seen_q.add(k)
                    active_seeds.append((q, c))
        clusters: Dict[str, Dict] = {}

        with ThreadPoolExecutor(max_workers=1) as pool:
            fut_map = {
                pool.submit(self._fetch_mf_seed_pages, q, max_pages, 24): (q, cat_hint)
                for q, cat_hint in active_seeds
            }
            for fut in as_completed(fut_map):
                _, cat_hint = fut_map[fut]
                items = fut.result() or []
                for it in items:
                    raw_title = str(it.get("title") or "").strip()
                    raw_brand = str(it.get("brand") or "").strip()
                    ref_w = str(it.get("refinedVolumeOrWeight") or "").strip()
                    img_u = str(it.get("imageUrl") or "").strip()
                    depots = it.get("productDepotInfoList") or []
                    if not raw_title or not depots:
                        continue

                    brand, fam = extract_clean_brand_and_family(raw_brand, raw_title)
                    b_norm = _norm_tr(brand)
                    if not brand or b_norm in ("genel", "diger", "markasiz"):
                        continue
                    if b_norm in PRIVATE_LABEL_BRANDS_NORM:
                        continue

                    p_info = _extract_pack_info_from_text(f"{ref_w} {raw_title}")
                    ckey, pack_lbl = self._canonical_cluster_key(brand, fam, p_info, ref_w)

                    bucket = clusters.get(ckey)
                    if bucket is None:
                        bucket = {
                            "brand": brand,
                            "brand_norm": b_norm,
                            "title": raw_title,
                            "pack_label": pack_lbl,
                            "category_hint": cat_hint,
                            "image_url": img_u,
                            "per_store": {},
                        }
                        clusters[ckey] = bucket
                    elif img_u and not bucket["image_url"]:
                        bucket["image_url"] = img_u

                    for dep in depots:
                        raw_m = str(dep.get("marketAdi") or "").strip().lower()
                        if raw_m not in MARKET_META:
                            continue
                        try:
                            price = float(dep.get("price") or 0)
                        except Exception:
                            price = 0.0
                        if price <= 1.0:
                            continue

                        plat_key, seller_label, base_search_url = MARKET_META[raw_m]
                        direct_u = extract_direct_url_from_image(raw_m, raw_title, img_u)
                        is_branch = plat_key in ("bim", "tarimkredi", "hakmar")
                        final_url = direct_u or (
                            base_search_url if is_branch else f"{base_search_url}{urllib.parse.quote_plus(raw_title)}"
                        )
                        cand = {
                            "platform": plat_key,
                            "seller_name": seller_label,
                            "title": raw_title,
                            "price": round(price, 2),
                            "url": final_url,
                        }
                        prev = bucket["per_store"].get(plat_key)
                        if prev is None or cand["price"] < prev["price"]:
                            bucket["per_store"][plat_key] = cand

        persisted_products = 0
        snapshots_written = 0
        for idx_b, bucket in enumerate(clusters.values(), start=1):
            offers = list(bucket["per_store"].values())
            is_known = (bucket["brand_norm"] in KNOWN_BRANDS) or (bucket["brand_norm"] in EXTRA_KNOWN_BRANDS)
            req_min = 1 if (include_known_brand_singles and is_known) else min_stores
            if len(offers) < req_min:
                continue
            if len(offers) >= 3:
                med = statistics.median([x["price"] for x in offers])
                offers = [x for x in offers if 0.42 * med <= x["price"] <= 2.15 * med]
            if len(offers) < req_min:
                continue

            pkey = self.persist_offers_for_product(
                raw_brand=bucket["brand"],
                raw_title=bucket["title"],
                offers=offers,
                pack_label=bucket["pack_label"],
                category_hint=bucket["category_hint"],
                image_url=bucket["image_url"],
                ping_search_engines=False,
                commit=False,
                update_index=False,
            )
            if pkey:
                persisted_products += 1
                snapshots_written += len(offers)
                if persisted_products % 250 == 0:
                    self.db.conn.commit()

        self.db.conn.commit()
        return {
            "clusters_scanned": len(clusters),
            "supermarket_products_persisted": persisted_products,
            "supermarket_offers_persisted": snapshots_written,
        }

    def harvest_online_marketplaces(
        self,
        seeds: Optional[List[Tuple[str, str]]] = None,
        min_offers: int = 2,
    ) -> Dict[str, int]:
        """
        Elektronik, küçük ev aletleri, telefon, süpürge, dermokozmetik ve çok satan ürünlerde
        Amazon, Hepsiburada, Trendyol, MediaMarkt, Teknosa, Vatan, n11, Pazarama vb. teklifleri
        çekerek kalıcı ürün sayfaları açar.
        """
        active_seeds = seeds or ONLINE_MARKETPLACE_SEEDS
        persisted = 0
        offers_count = 0

        def _fetch_one(q_and_cat: Tuple[str, str]) -> Optional[Tuple[Dict, str]]:
            q, c_hint = q_and_cat
            try:
                time.sleep(0.35)
                res = self.lens.analyze_and_compare(query_override=q)
                if res.get("ok") and len(res.get("offers") or []) >= min_offers:
                    return res, c_hint
            except Exception:
                pass
            return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            for item in pool.map(_fetch_one, active_seeds):
                if not item:
                    continue
                res, c_hint = item
                pinfo = res.get("product_info") or {}
                pinfo["category"] = c_hint
                pkey = self.persist_lens_bundle(
                    res,
                    ping_search_engines=False,
                    commit=False,
                    update_index=False,
                )
                if pkey:
                    persisted += 1
                    offers_count += len(res.get("offers") or [])
                    if persisted % 25 == 0:
                        self.db.conn.commit()

        self.db.conn.commit()
        return {
            "online_seeds_queried": len(active_seeds),
            "online_products_persisted": persisted,
            "online_offers_persisted": offers_count,
        }

    def _tazelik_tablosu(self) -> None:
        self.db.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS online_tazelik (product_key TEXT PRIMARY KEY, son TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS yenile_kuyrugu (product_key TEXT PRIMARY KEY, istendi TEXT NOT NULL);
            """
        )

    def tazele_kuyruk(self, limit: int = 15, magaza: bool = True) -> Dict:
        """Online fiyatı en çok gereken ürünleri yeniler (Kemal, 17 Kas 2026: "orada fiyat değiştiğinde bizde de değişiyor mu?"):
        1) ziyaretçinin baktığı ve fiyatı 6 saatten eski ürünler (yenile_kuyrugu),
        2) kalanı: temel ihtiyaçlardan (talep puanı yüksek) fiyatı 6 saatten eski olanlar."""
        self._tazelik_tablosu()
        import talep
        talep.kaydet(self.db.conn)
        kuyruk = [r[0] for r in self.db.conn.execute(
            "SELECT product_key FROM yenile_kuyrugu ORDER BY istendi LIMIT ?", (limit,)).fetchall()]
        if len(kuyruk) < limit:
            kuyruk += [r[0] for r in self.db.conn.execute(
                """SELECT i.product_key FROM product_catalog_index i
                   LEFT JOIN online_tazelik t ON t.product_key = i.product_key
                   WHERE (t.son IS NULL OR t.son < datetime('now', '-6 hours')) AND i.offer_count >= 2
                     AND dipci_talep(i.search_norm, i.category) >= 80
                   ORDER BY dipci_talep(i.search_norm, i.category) DESC, t.son LIMIT ?""",
                (limit - len(kuyruk),)).fetchall() if r[0] not in kuyruk]
        if not kuyruk:
            return {"ok": True, "scanned": 0}
        ph = ",".join("?" * len(kuyruk))
        rows = self.db.conn.execute(
            f"SELECT product_key, brand, family_name, category FROM product_catalog_index WHERE product_key IN ({ph})", kuyruk
        ).fetchall()
        self.db.conn.execute(f"DELETE FROM yenile_kuyrugu WHERE product_key IN ({ph})", kuyruk)
        self.db.conn.commit()
        return self.enrich_online_offers(limit=len(rows), magaza=magaza, batch_override=list(rows))

    def enrich_online_offers(self, limit: int = 250, state_path: Optional[Path] = None, magaza: bool = False,
                             batch_override: Optional[List] = None) -> Dict:
        """Katalogdaki market ürünlerine online mağaza (PTT AVM, n11, Hepsiburada, Amazon, Trendyol...) fiyatlarını ekler.
        Kemal 16 Kas 2026: Nescafe Gold'da PTT AVM ve n11 marketlerden ucuzdu ama kartta görünmüyordu.
        Her çalışmada sıradaki `limit` ürün taranır (dönüşümlü); Akakçe'yi yormamak için yavaş gider."""
        t0 = time.time()
        rows = self.db.conn.execute(
            "SELECT product_key, brand, family_name, category FROM product_catalog_index ORDER BY offer_count DESC, product_key"
        ).fetchall()
        if not rows:
            return {"ok": True, "enriched": 0}
        # Mağazaların kendi sitelerinden doğrulama/ek fiyat (Trendyol, n11, Pazarama, Amazon, Teknosa, Vatan) (Kemal, 17 Kas 2026)
        tarayici = None
        if magaza:
            try:
                from magaza_tarayici import MagazaTarayici
                tarayici = MagazaTarayici().__enter__()
            except Exception as e:
                print("magaza tarayici acilamadi:", e)
                tarayici = None
        magaza_eklenen = 0
        state_path = state_path or (Path(__file__).resolve().parent / "data" / "online_enrich_offset.txt")
        try:
            start = int(state_path.read_text().strip() or 0) % len(rows)
        except Exception:
            start = 0
        batch = batch_override if batch_override is not None else [rows[(start + i) % len(rows)] for i in range(min(limit, len(rows)))]
        self._tazelik_tablosu()
        enriched = 0
        offers_added = 0
        for r in batch:
            q = f"{r['brand']} {r['family_name']}".strip()
            try:
                offers, _meta = self.lens._search_online_marketplaces_live(q, _extract_pack_info_from_text(q))
            except Exception:
                offers = []
            if tarayici is not None:
                try:
                    ek = tarayici.ara(q, r["category"])
                except Exception:
                    ek = []
                var = {o["platform"]: o for o in offers}
                for o in ek:
                    eski = var.get(o["platform"])
                    if not eski or o["price"] < eski["price"]:
                        var[o["platform"]] = o
                        magaza_eklenen += 1
                offers = list(var.values())
            if offers:
                pk = self.persist_offers_for_product(
                    raw_brand=r["brand"], raw_title=q, offers=offers,
                    ping_search_engines=False, commit=False, update_index=False,
                )
                if pk:
                    enriched += 1
                    offers_added += len(offers)
            self.db.conn.execute(
                "INSERT INTO online_tazelik(product_key, son) VALUES (?, datetime('now')) "
                "ON CONFLICT(product_key) DO UPDATE SET son = excluded.son", (r["product_key"],))
            # Her üründen sonra kaydet: uzun yazma kilidi siteyi ve radarı kilitliyordu (17 Kas 2026).
            self.db.conn.commit()
            time.sleep(1.2)
        self.db.conn.commit()
        if tarayici is not None:
            try:
                tarayici.__exit__(None, None, None)
            except Exception:
                pass
        if batch_override is None:
            try:
                state_path.write_text(str((start + len(batch)) % len(rows)))
            except Exception:
                pass
        indexed = self.db.rebuild_catalog_index()
        return {"ok": True, "elapsed_sec": round(time.time() - t0, 1), "scanned": len(batch),
                "enriched": enriched, "online_offers_added": offers_added, "magaza_sitesi_teklif": magaza_eklenen, "indexed": indexed}

    def harvest_broad_catalog(self, include_online: bool = True, max_pages: int = 4) -> Dict:
        t0 = time.time()
        self.reclassify_existing_catalog()
        sm_stats = self.harvest_supermarket_catalog(max_pages=max_pages)
        ec_stats = self.harvest_online_marketplaces() if include_online else {
            "online_seeds_queried": 0,
            "online_products_persisted": 0,
            "online_offers_persisted": 0,
        }
        indexed_cnt = self.db.rebuild_catalog_index()

        total_prods = self.db.conn.execute(
            "SELECT COUNT(*) AS c FROM canonical_products WHERE is_private_label = 0"
        ).fetchone()["c"]
        total_barcodes = self.db.conn.execute(
            "SELECT COUNT(*) AS c FROM product_barcodes"
        ).fetchone()["c"]

        # Yeni site haritasını arama motorlarına bildir
        recent_rows = self.db.conn.execute(
            "SELECT product_key FROM canonical_products WHERE is_private_label = 0 ORDER BY rowid DESC LIMIT 80"
        ).fetchall()
        ping_urls = [f"{CANONICAL_ORIGIN}/urun/{key_to_slug(r['product_key'])}" for r in recent_rows]
        idx_code = ping_indexnow(ping_urls)

        return {
            "ok": True,
            "elapsed_sec": round(time.time() - t0, 2),
            **sm_stats,
            **ec_stats,
            "indexed_products": indexed_cnt,
            "total_catalog_products": total_prods,
            "total_barcodes": total_barcodes,
            "indexnow_status": idx_code,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="dipci.tr Otomatik Katalog Genişletme Motoru")
    parser.add_argument("--harvest", action="store_true", help="Tam kapsamlı market + online pazaryeri hasadı çalıştır")
    parser.add_argument("--supermarket-only", action="store_true", help="Yalnızca zincir market hasadını çalıştır")
    parser.add_argument("--max-pages", type=int, default=4, help="Tohum başına taranacak MarketFiyatı sayfa sayısı")
    parser.add_argument("--rebuild-index", action="store_true", help="Yalnızca katalog arama/filtre indeksini yeniden oluştur")
    parser.add_argument("--import-bundles", type=str, default="", help="Önceden çekilmiş JSON paketlerini içe aktar")
    parser.add_argument("--discover", type=str, default="", help="Tekil ürün sorgusunu keşfet ve kaydet")
    parser.add_argument("--enrich-online", type=int, default=0, help="Sıradaki N ürüne online mağaza fiyatı ekle")
    parser.add_argument("--tazele", type=int, default=0, help="Bakılan ve temel ihtiyaç ürünlerinden fiyatı eskiyen N ürünü yenile")
    parser.add_argument("--magaza", action="store_true", help="Mağazaların kendi sitelerini de gerçek tarayıcıyla oku")
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent
    db_path = Path(
        __import__("os").getenv("DIPCI_DB_PATH", str(base_dir / "data" / "dipci_platform.db"))
    )
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = PriceMemoryDB(db_path)
    engine = AutoCatalogEngine(db)

    if args.rebuild_index:
        recl = engine.reclassify_existing_catalog()
        cnt = db.rebuild_catalog_index()
        print(json.dumps({"ok": True, "reclassified": recl, "indexed": cnt}, ensure_ascii=False))
        return

    if args.import_bundles and Path(args.import_bundles).exists():
        imported = 0
        for b in json.loads(Path(args.import_bundles).read_text(encoding="utf-8")):
            if engine.persist_lens_bundle(b, ping_search_engines=False, commit=False, update_index=False):
                imported += 1
        db.conn.commit()
        recl = engine.reclassify_existing_catalog()
        cnt = db.rebuild_catalog_index()
        if not args.harvest and not args.supermarket_only:
            print(json.dumps({"ok": True, "imported_bundles": imported, "reclassified": recl, "indexed": cnt}, ensure_ascii=False))
            return

    if args.tazele:
        print(json.dumps(engine.tazele_kuyruk(limit=args.tazele, magaza=args.magaza), ensure_ascii=False, indent=2))
        return
    if args.enrich_online:
        print(json.dumps(engine.enrich_online_offers(limit=args.enrich_online, magaza=args.magaza), ensure_ascii=False, indent=2))
        return

    if args.discover:
        pkey = engine.discover_and_persist_query(args.discover)
        print(json.dumps({"ok": bool(pkey), "product_key": pkey}, ensure_ascii=False))
        return

    res = engine.harvest_broad_catalog(include_online=not args.supermarket_only, max_pages=max(1, args.max_pages))
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
