"""
dipci.tr — Dipçi Lens: Kamera & Görselden Ürün Tanıma + Canlı En Ucuz Mağaza Sıralama Motoru
Google Lens benzeri görsel ürün tanıma (Gemini Vision + ONNX RapidOCR + Barkod + OpenFoodFacts)
ve tüm Türkiye zincir marketleri + e-ticaret sitelerinde canlı en düşük fiyat sıralaması.
"""

import base64
import html as html_lib
import io
import json
import math
import os
import re
import statistics
import threading
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from live_sync import (
    MARKET_API_URL,
    MARKET_META,
    _norm_tr,
    _slugify_tr,
    extract_direct_url_from_image,
    resolve_migros_direct_product,
    resolve_sok_direct_product,
)
from memory_db import PriceMemoryDB

AMAZON_ASSOCIATE_TAG = os.getenv("AMAZON_PARTNER_TAG", "marketdedekti-21").strip() or "marketdedekti-21"
MARKET_IDENTITY_URL = "https://api.marketfiyati.org.tr/api/v2/searchByIdentity"
MODELS_DIR = Path(__file__).resolve().parent / "data" / "models"

_OCR_LOCK = threading.Lock()
_OCR_INSTANCE = None
_OBJ_LOCK = threading.Lock()
_OBJ_SESSION = None
_OBJ_CLASSES: List[str] = []


def _get_rapid_ocr():
    global _OCR_INSTANCE
    if _OCR_INSTANCE is not None:
        return _OCR_INSTANCE
    with _OCR_LOCK:
        if _OCR_INSTANCE is None:
            from rapidocr_onnxruntime import RapidOCR
            _OCR_INSTANCE = RapidOCR()
    return _OCR_INSTANCE


def _get_object_classifier():
    global _OBJ_SESSION, _OBJ_CLASSES
    if _OBJ_SESSION is not None:
        return _OBJ_SESSION, _OBJ_CLASSES
    with _OBJ_LOCK:
        if _OBJ_SESSION is None:
            model_path = MODELS_DIR / "mobilenetv2-12.onnx"
            cls_path = MODELS_DIR / "imagenet_classes.txt"
            if model_path.exists() and cls_path.exists():
                import onnxruntime as ort
                _OBJ_SESSION = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
                _OBJ_CLASSES = cls_path.read_text(encoding="utf-8").splitlines()
    return _OBJ_SESSION, _OBJ_CLASSES


# Yazısız / barkodsuz günlük nesne çekimlerinde ("bir şeyin fotoğrafını çektim") Türkçe ürün karşılıkları
IMAGENET_TR_SHOPPING_MAP: Dict[str, Tuple[str, str]] = {
    "coffee mug": ("Kahve Kupası", "Mutfak & Sofra"),
    "cup": ("Fincan & Kupa", "Mutfak & Sofra"),
    "espresso": ("Espresso Kahve", "Süpermarket & Gıda"),
    "espresso maker": ("Espresso Kahve Makinesi", "Küçük Ev Aletleri"),
    "coffeepot": ("Filtre Kahve Makinesi", "Küçük Ev Aletleri"),
    "teapot": ("Çaydanlık", "Mutfak & Sofra"),
    "water bottle": ("Su Matarası & Termos", "Mutfak & Outdoor"),
    "pop bottle": ("Soğuk İçecek & Gazoz", "Süpermarket & Gıda"),
    "water jug": ("Su Sürahisi", "Mutfak & Sofra"),
    "pitcher": ("Cam Sürahi", "Mutfak & Sofra"),
    "frying pan": ("Yanmaz Yapışmaz Tava", "Mutfak Gereçleri"),
    "wok": ("Wok Tava", "Mutfak Gereçleri"),
    "dutch oven": ("Döküm Tencere", "Mutfak Gereçleri"),
    "caldron": ("Çelik Tencere", "Mutfak Gereçleri"),
    "crock pot": ("Düdüklü & Yavaş Pişirici Tencere", "Mutfak Gereçleri"),
    "toaster": ("Ekmek Kızartma & Tost Makinesi", "Küçük Ev Aletleri"),
    "waffle iron": ("Tost & Waffle Makinesi", "Küçük Ev Aletleri"),
    "microwave": ("Mikrodalga Fırın", "Beyaz Eşya & Mutfak"),
    "refrigerator": ("Buzdolabı", "Beyaz Eşya"),
    "dishwasher": ("Bulaşık Makinesi", "Beyaz Eşya"),
    "washer": ("Çamaşır Makinesi", "Beyaz Eşya"),
    "iron": ("Buharlı Ütü", "Küçük Ev Aletleri"),
    "vacuum": ("Elektrikli Süpürge", "Küçük Ev Aletleri"),
    "hand blower": ("Saç Kurutma Makinesi", "Kişisel Bakım"),
    "electric fan": ("Vantilatör", "Isıtma & Soğutma"),
    "space heater": ("Elektrikli Isıtıcı", "Isıtma & Soğutma"),
    "mouse": ("Kablosuz Bilgisayar Mouse", "Bilgisayar & Aksesuar"),
    "computer keyboard": ("Bilgisayar Klavyesi", "Bilgisayar & Aksesuar"),
    "notebook": ("Dizüstü Bilgisayar Laptop", "Bilgisayar & Teknoloji"),
    "laptop": ("Dizüstü Bilgisayar Laptop", "Bilgisayar & Teknoloji"),
    "desktop computer": ("Masaüstü Bilgisayar Monitör", "Bilgisayar & Teknoloji"),
    "monitor": ("Bilgisayar Monitörü", "Bilgisayar & Teknoloji"),
    "screen": ("Bilgisayar Monitörü", "Bilgisayar & Teknoloji"),
    "television": ("Smart TV Televizyon", "TV & Ses Sistemleri"),
    "remote control": ("Akıllı TV Kumandası", "TV & Aksesuar"),
    "cellular telephone": ("Akıllı Cep Telefonu", "Telefon & Aksesuar"),
    "ipod": ("Taşınabilir Müzik Çalar & Kulaklık", "Elektronik"),
    "loudspeaker": ("Bluetooth Hoparlör", "Ses Sistemleri"),
    "microphone": ("Yaka & Stüdyo Mikrofonu", "Ses & Elektronik"),
    "joystick": ("Oyun Kolu Gamepad", "Oyun & Konsol"),
    "modem": ("Wi-Fi Modem & Router", "Ağ Ürünleri"),
    "printer": ("Yazıcı", "Ofis & Kırtasiye"),
    "running shoe": ("Spor Ayakkabı", "Ayakkabı & Giyim"),
    "sneaker": ("Günlük Spor Ayakkabı", "Ayakkabı & Giyim"),
    "loafer": ("Klasik Günlük Ayakkabı", "Ayakkabı & Giyim"),
    "sandal": ("Sandalet & Terlik", "Ayakkabı & Giyim"),
    "clog": ("Ortopedik Terlik", "Ayakkabı & Giyim"),
    "sunglasses": ("Güneş Gözlüğü", "Moda & Aksesuar"),
    "sunglass": ("Güneş Gözlüğü", "Moda & Aksesuar"),
    "digital watch": ("Akıllı Saat & Dijital Kol Saati", "Saat & Aksesuar"),
    "analog clock": ("Duvar & Masa Saati", "Ev Dekorasyon"),
    "stopwatch": ("Kol Saati", "Saat & Aksesuar"),
    "backpack": ("Sırt Çantası", "Çanta & Aksesuar"),
    "mailbag": ("Omuz & Evrak Çantası", "Çanta & Aksesuar"),
    "purse": ("Kadın El & Omuz Çantası", "Çanta & Aksesuar"),
    "wallet": ("Deri Cüzdan", "Çanta & Aksesuar"),
    "umbrella": ("Yağmur Şemsiyesi", "Aksesuar"),
    "perfume": ("Parfüm", "Kozmetik & Kişisel Bakım"),
    "lotion": ("Nemlendirici Cilt Kremi", "Kozmetik & Kişisel Bakım"),
    "sunscreen": ("Güneş Kremi", "Kozmetik & Kişisel Bakım"),
    "lipstick": ("Ruj", "Kozmetik"),
    "soap dispenser": ("Sıvı El Sabunu", "Temizlik & Kişisel Bakım"),
    "hair spray": ("Saç Spreyi & Şampuan", "Kişisel Bakım"),
    "toilet tissue": ("Tuvalet Kağıdı", "Temizlik & Kağıt Ürünleri"),
    "paper towel": ("Kağıt Havlu", "Temizlik & Kağıt Ürünleri"),
    "diaper": ("Bebek Bezi", "Anne & Bebek"),
    "banana": ("İthal & Yerli Muz", "Meyve & Sebze"),
    "granny smith": ("Elma", "Meyve & Sebze"),
    "orange": ("Portakal", "Meyve & Sebze"),
    "lemon": ("Limon", "Meyve & Sebze"),
    "strawberry": ("Çilek", "Meyve & Sebze"),
    "pineapple": ("Ananas", "Meyve & Sebze"),
    "pomegranate": ("Nar", "Meyve & Sebze"),
    "broccoli": ("Brokoli", "Meyve & Sebze"),
    "cucumber": ("Salatalık", "Meyve & Sebze"),
    "bell pepper": ("Biber", "Meyve & Sebze"),
    "mushroom": ("Kültür Mantarı", "Meyve & Sebze"),
    "chocolate sauce": ("Sürülebilir Çikolata & Sos", "Süpermarket & Gıda"),
    "peanut butter": ("Fıstık Ezmesi", "Süpermarket & Gıda"),
    "honeycomb": ("Süzme Çiçek Balı", "Süpermarket & Gıda"),
    "french loaf": ("Ekmek", "Süpermarket & Gıda"),
    "bagel": ("Simit & Poğaça", "Süpermarket & Gıda"),
    "pretzel": ("Tuzlu Kraker", "Atıştırmalık"),
    "ice cream": ("Dondurma", "Süpermarket & Gıda"),
    "pillow": ("Uyku Yastığı", "Ev Tekstili"),
    "quilt": ("Yorgan & Nevresim", "Ev Tekstili"),
    "bath towel": ("Banyo Havlusu", "Ev Tekstili"),
    "desk": ("Çalışma Masası", "Mobilya"),
    "folding chair": ("Katlanır Kamp Sandalyesi", "Ev & Kamp"),
    "rocking chair": ("Tekli Koltuk & Sandalye", "Mobilya"),
    "barber chair": ("Ofis & Oyuncu Koltuğu", "Ofis Mobilyası"),
    "table lamp": ("Masa Lambası", "Aydınlatma"),
    "ballpoint": ("Tükenmez Kalem Seti", "Ofis & Kırtasiye"),
    "fountain pen": ("Dolma Kalem", "Ofis & Kırtasiye"),
    "pencil box": ("Kalem Kutusu", "Kırtasiye"),
    "notebook computer": ("Dizüstü Bilgisayar Laptop", "Bilgisayar & Teknoloji"),
    "teddy": ("Peluş Oyuncak Ayı", "Oyuncak"),
    "soccer ball": ("Futbol Topu", "Spor & Outdoor"),
    "basketball": ("Basketbol Topu", "Spor & Outdoor"),
    "dumbbell": ("Dambıl Ağırlık Seti", "Spor & Fitness"),
    "screwdriver": ("Tornavida Seti", "Yapı Market & Hırdavat"),
    "power drill": ("Şarjlı Matkap Vidalama", "Yapı Market & Hırdavat"),
    "hammer": ("Çekiç", "Yapı Market & Hırdavat"),
}


# OCR'da Türkçe karakter düzeltme ve bilinen marka sözlüğü (norm -> görüntülenecek ad)
KNOWN_BRANDS: Dict[str, str] = {
    "ulker": "Ülker",
    "eti": "Eti",
    "torku": "Torku",
    "caykur": "Çaykur",
    "lipton": "Lipton",
    "dogus": "Doğuş",
    "dogadan": "Doğadan",
    "sutas": "Sütaş",
    "icim": "İçim",
    "pinar": "Pınar",
    "sek": "Sek",
    "ekici": "Ekici",
    "tahsildaroglu": "Tahsildaroğlu",
    "muratbey": "Muratbey",
    "bahcivan": "Bahçıvan",
    "komili": "Komili",
    "taris": "Tariş",
    "kristal": "Kristal",
    "yudum": "Yudum",
    "biryag": "Biryağ",
    "orkide": "Orkide",
    "mehmet efendi": "Kurukahveci Mehmet Efendi",
    "kahve dunyasi": "Kahve Dünyası",
    "nescafe": "Nescafé",
    "jacobs": "Jacobs",
    "tchibo": "Tchibo",
    "kurukahveci": "Kurukahveci Mehmet Efendi",
    "nutella": "Nutella",
    "ferrero": "Ferrero",
    "kinder": "Kinder",
    "tadelle": "Tadelle",
    "sarelle": "Sarelle",
    "fiskobirlik": "Fiskobirlik",
    "filiz": "Filiz",
    "barilla": "Barilla",
    "pastavilla": "Pastavilla",
    "mutlu": "Mutlu",
    "reis": "Reis",
    "yayla": "Yayla",
    "duru": "Duru",
    "tat": "Tat",
    "oncu": "Öncü",
    "tukas": "Tukaş",
    "burcu": "Burcu",
    "soke": "Söke",
    "sinangil": "Sinangil",
    "balkupu": "Balküpü",
    "knorr": "Knorr",
    "calve": "Calvé",
    "heinz": "Heinz",
    "coca cola": "Coca-Cola",
    "cocacola": "Coca-Cola",
    "pepsi": "Pepsi",
    "sprite": "Sprite",
    "fanta": "Fanta",
    "red bull": "Red Bull",
    "redbull": "Red Bull",
    "beypazari": "Beypazarı",
    "eriklli": "Erikli",
    "erikli": "Erikli",
    "sirma": "Sırma",
    "damla": "Damla",
    "lays": "Lay's",
    "ruffles": "Ruffles",
    "doritos": "Doritos",
    "cheetos": "Cheetos",
    "pringles": "Pringles",
    "cizi": "Ülker Çizi",
    "biskrem": "Ülker Biskrem",
    "halley": "Ülker Halley",
    "albeni": "Ülker Albeni",
    "metro": "Ülker Metro",
    "tutku": "Eti Tutku",
    "burcak": "Eti Burçak",
    "nero": "Eti Nero",
    "cin": "Eti Cin",
    "hosbes": "Eti Hoşbeş",
    "karam": "Eti Karam",
    "browni": "Eti Browni",
    "gong": "Eti Gong",
    "form": "Eti Form",
    "cicibebe": "Eti Cicibebe",
    "fairy": "Fairy",
    "finish": "Finish",
    "pril": "Pril",
    "persil": "Persil",
    "ariel": "Ariel",
    "omo": "Omo",
    "perwoll": "Perwoll",
    "bingo": "Bingo",
    "tursil": "Tursil",
    "alo": "Alo",
    "rinso": "Rinso",
    "yumos": "Yumoş",
    "vernel": "Vernel",
    "abc": "ABC",
    "domestos": "Domestos",
    "cif": "Cif",
    "asperox": "Asperox",
    "marc": "Marc",
    "porcoz": "Porçöz",
    "selpak": "Selpak",
    "solo": "Solo",
    "familia": "Familia",
    "papia": "Papia",
    "maylo": "Maylo",
    "sofia": "Sofia",
    "prima": "Prima",
    "pampers": "Pampers",
    "sleepy": "Sleepy",
    "molfix": "Molfix",
    "canbebe": "Canbebe",
    "uni baby": "Uni Baby",
    "aptamil": "Aptamil",
    "bebelac": "Bebelac",
    "sma": "SMA",
    "hipp": "Hipp",
    "dalin": "Dalin",
    "sebamed": "Sebamed",
    "mustela": "Mustela",
    "elidor": "Elidor",
    "clear": "Clear",
    "pantene": "Pantene",
    "elseve": "Elseve",
    "loreal": "L'Oréal",
    "gliss": "Gliss",
    "blendax": "Blendax",
    "bioblas": "Bioblas",
    "head shoulders": "Head & Shoulders",
    "shoulders": "Head & Shoulders",
    "dove": "Dove",
    "palmolive": "Palmolive",
    "activex": "Activex",
    "protex": "Protex",
    "sensodyne": "Sensodyne",
    "colgate": "Colgate",
    "parodontax": "Parodontax",
    "signal": "Signal",
    "ipana": "Ipana",
    "oral b": "Oral-B",
    "orkid": "Orkid",
    "kotex": "Kotex",
    "molped": "Molped",
    "nivea": "Nivea",
    "rexona": "Rexona",
    "axe": "Axe",
    "gillette": "Gillette",
    "arko": "Arko",
    "neutrogena": "Neutrogena",
    "cerave": "CeraVe",
    "la roche": "La Roche-Posay",
    "bioderma": "Bioderma",
    "garnier": "Garnier",
    "dyson": "Dyson",
    "philips": "Philips",
    "tefal": "Tefal",
    "arzum": "Arzum",
    "karaca": "Karaca",
    "fakir": "Fakir",
    "arnica": "Arnica",
    "korkmaz": "Korkmaz",
    "emsan": "Emsan",
    "schafer": "Schafer",
    "bosch": "Bosch",
    "siemens": "Siemens",
    "profilo": "Profilo",
    "arcelik": "Arçelik",
    "beko": "Beko",
    "vestel": "Vestel",
    "grundig": "Grundig",
    "altus": "Altus",
    "kumtel": "Kumtel",
    "xiaomi": "Xiaomi",
    "roborock": "Roborock",
    "dreame": "Dreame",
    "samsung": "Samsung",
    "apple": "Apple",
    "iphone": "Apple iPhone",
    "airpods": "Apple AirPods",
    "ipad": "Apple iPad",
    "macbook": "Apple MacBook",
    "sony": "Sony",
    "jbl": "JBL",
    "anker": "Anker",
    "huawei": "Huawei",
    "honor": "Honor",
    "logitech": "Logitech",
    "asus": "Asus",
    "lenovo": "Lenovo",
    "acer": "Acer",
    "msi": "MSI",
    "monster": "Monster",
    "stanley": "Stanley",
    "contigo": "Contigo",
    "thermos": "Thermos",
    "lego": "Lego",
    "adidas": "Adidas",
    "nike": "Nike",
    "puma": "Puma",
    "skechers": "Skechers",
    "crocs": "Crocs",
    "casio": "Casio",
    "pedigree": "Pedigree",
    "whiskas": "Whiskas",
    "felix": "Felix",
    "proplan": "Pro Plan",
    "royal canin": "Royal Canin",
}

# Ambalaj üzerindeki slogan / pazarlama gürültü kelimeleri
PACKAGE_NOISE_PHRASES = [
    "mega ekonomik", "mega ekonomix", "ekonomik boy", "ekonomik paket", "avantaj paketi",
    "super boy", "dev boy", "aile boyu", "yeni", "hediye", "bedava", "ozel fiyat",
    "zorlu kirlerde", "bile etkili", "hepsi bir arada", "filtrenizi", "temizler",
    "yikamada", "ilk yikamadan", "bizim cayimiz", "tadi", "acligini", "yatistir",
    "proprietary", "hazelnut", "spread", "with cocoa", "www.", ".com", ".eu", ".tr",
    "turk mali", "yerli uretim", "helal", "trans yag", "icermemektedir", "icermez",
    "koruyucu", "renklendirici", "dogal", "geleneksel", "lezzet", "enfes", "citir",
    "mensei", "saklayiniz", "tuketim", "tarihi", "parti no", "isletme", "nerede",
    "kalite", "garanti", "orijinal", "tavsiye edilen", "porsiyon", "enerji", "kcal",
]

OCR_WORD_FIXES = {
    "caykur": "Çaykur",
    "tiryaki": "Tiryaki",
    "cayi": "Çayı",
    "siyahcaye": "Siyah Çay",
    "siyahcay": "Siyah Çay",
    "ulker": "Ülker",
    "cizi": "Çizi",
    "sutas": "Sütaş",
    "icim": "İçim",
    "kasar": "Kaşar",
    "peyniri": "Peyniri",
    "peynirli": "Peynirli",
    "tereyagi": "Tereyağı",
    "zeytinyagi": "Zeytinyağı",
    "aycicek": "Ayçiçek",
    "yogurt": "Yoğurt",
    "sut": "Süt",
    "bulasik": "Bulaşık",
    "camasir": "Çamaşır",
    "yumusatici": "Yumuşatıcı",
    "sampuan": "Şampuan",
    "sampuani": "Şampuanı",
    "kagit": "Kağıt",
    "tuvalet": "Tuvalet",
    "kahve": "Kahve",
    "turk": "Türk",
    "findik": "Fındık",
    "cikolatali": "Çikolatalı",
    "cikolata": "Çikolata",
    "gofret": "Gofret",
    "biskuvi": "Bisküvi",
}


# ReklamAction (ReklamStore) komisyon linkleri — Kemal 17 Kas 2026. Mağazanın kendi sitesine giden link,
# ad.reklm.com takip linkine sarılır (url= derin bağlantı). Teklif no'ları yayıncı panelinden.
REKLAMACTION_AFF_ID = os.getenv("REKLAMACTION_AFF_ID", "43815").strip()
REKLAMACTION_OFFERS = {
    # alan adı: (teklif no, derin linke eklenecek takip parametreleri — teklifin kendi varsayılan linkiyle AYNI olmalı;
    #            None = derin link desteklenmiyor, ana sayfaya giden takip linki kullanılır)
    "koctas.com.tr": ("8937", "utm_source=reklamaction&utm_medium=affiliate&utm_campaign={aff}&transaction_id={transaction_id}"),
    "mi.com": ("62394", "utm_campaign=lo_tr&utm_channel=affiliate&utm_source=ReklamStore&utm_medium=paid-affiliate&utm_type=1&transaction_id={transaction_id}"),
    "koton.com": ("5533", "utm_source=reklamstore&utm_medium=affiliate&utm_campaign={aff}&transaction_id={transaction_id}"),
    "flo.com.tr": ("62376", None),  # FLO takibi Adjust üzerinden; derin link takibi düşürür
}


def _apply_affiliate_url(url: str) -> str:
    if not url:
        return ""
    low = url.lower()
    if "amazon.com.tr" in low:
        sep = "&" if "?" in url else "?"
        if "tag=" not in low:
            return f"{url}{sep}tag={AMAZON_ASSOCIATE_TAG}"
        return url
    if REKLAMACTION_AFF_ID and "reklm.com" not in low:
        try:
            host = urllib.parse.urlparse(url).netloc.lower().split(":")[0]
        except Exception:
            host = ""
        for dom, (offer_id, track_qs) in REKLAMACTION_OFFERS.items():
            if host == dom or host.endswith("." + dom):
                base = f"https://ad.reklm.com/aff_c?offer_id={offer_id}&aff_id={REKLAMACTION_AFF_ID}"
                if not track_qs:
                    return base
                # Derin linkte takip parametreleri kendiliğinden eklenmiyor; {transaction_id} makrosunu ağ dolduruyor
                deep = url + ("&" if "?" in url else "?") + track_qs.replace("{aff}", REKLAMACTION_AFF_ID)
                return f"{base}&url={urllib.parse.quote(deep, safe='')}"
    return url


def _fmt_tl(val: float) -> str:
    return f"{val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _seller_to_platform_key(seller_name: str, url: str) -> Tuple[str, str]:
    combined = f"{_norm_tr(seller_name)} {(url or '').lower()}"
    mapping = [
        ("amazon", "amazon", "Amazon.com.tr"),
        ("hepsiburada", "hepsiburada", "Hepsiburada"),
        ("trendyol", "trendyol", "Trendyol"),
        ("migros", "migros", "Migros Sanal Market"),
        ("carrefour", "carrefoursa", "CarrefourSA"),
        ("a101", "a101", "A101 Kapıda"),
        ("sokmarket", "sok", "Cepte ŞOK"),
        ("getir", "getir", "GetirBüyük"),
        ("n11", "n11", "n11"),
        ("pazarama", "pazarama", "Pazarama"),
        ("mediamarkt", "mediamarkt", "MediaMarkt"),
        ("teknosa", "teknosa", "Teknosa"),
        ("vatan", "vatan", "Vatan Bilgisayar"),
        ("pttavm", "pttavm", "PttAVM"),
        ("idefix", "idefix", "İdefix"),
        ("koctas", "koctas", "Koçtaş"),
        ("ciceksepeti", "ciceksepeti", "Çiçeksepeti"),
    ]
    for needle, pkey, label in mapping:
        if needle in combined:
            # Alt satıcı bilgisi varsa koru (örn: Pazarama / MediaMarkt)
            clean_s = (seller_name or "").strip()
            if "/" in clean_s:
                sub = clean_s.split("/", 1)[1].strip()
                if sub and _norm_tr(sub) != needle:
                    return pkey, f"{label} ({sub})"
            return pkey, label
    return "default", (seller_name or "Online Mağaza").strip()


def _extract_pack_info_from_text(text: str) -> Optional[Tuple[float, str, str]]:
    """Metinden (standart_miktar, base_unit, okunabilir_etiket) çıkarır."""
    t = (text or "").lower().replace(",", ".")
    # Ambalajdaki "500ge" veya "Net:70ge" (Avrupa 'e' işareti) temizliği
    t = re.sub(r"(\d)\s*(kg|gr|g|ml|lt|l)e\b", r"\1 \2", t)
    t = re.sub(r"net\s*[:\.]?\s*", " ", t)

    m_mult = re.search(r"\b(\d{1,2})\s*[x\*]\s*(\d+(?:\.\d+)?)\s*(kg|gr|g|lt|litre|l|ml)\b", t)
    if m_mult:
        cnt = float(m_mult.group(1))
        val = float(m_mult.group(2))
        u = m_mult.group(3)
        tot = cnt * val
        if u in ("gr", "g"):
            return round(tot / 1000.0, 3), "kg", f"{int(cnt)}x{int(val)} g"
        if u == "ml":
            return round(tot / 1000.0, 3), "litre", f"{int(cnt)}x{int(val)} ml"
        if u == "kg":
            return round(tot, 3), "kg", f"{int(cnt)}x{val:g} kg"
        return round(tot, 3), "litre", f"{int(cnt)}x{val:g} L"

    m_kg = re.search(r"\b(\d+(?:\.\d+)?)\s*kg\b", t)
    if m_kg:
        v = float(m_kg.group(1))
        if 0.05 <= v <= 30:
            return v, "kg", f"{v:g} kg"

    m_gr = re.search(r"\b(\d{2,4})\s*(?:gr|g)\b", t)
    if m_gr:
        v = float(m_gr.group(1))
        if 15 <= v <= 5000:
            return round(v / 1000.0, 3), "kg", f"{int(v)} g"

    m_lt = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:lt|litre|l)\b", t)
    if m_lt:
        v = float(m_lt.group(1))
        if 0.1 <= v <= 25:
            return v, "litre", f"{v:g} L"

    m_ml = re.search(r"\b(\d{2,4})\s*ml\b", t)
    if m_ml:
        v = float(m_ml.group(1))
        if 20 <= v <= 5000:
            return round(v / 1000.0, 3), "litre", f"{int(v)} ml"

    m_cnt = re.search(r"\b(\d{1,3})\s*(?:kapsül|kapsul|tablet|yıkama|yikama|rulo|adet|li|lü|lu|lı)\b", t)
    if m_cnt:
        v = float(m_cnt.group(1))
        if 2 <= v <= 300:
            return v, "adet", f"{int(v)} adet"
    return None


class DipciLensEngine:
    def __init__(self, db: PriceMemoryDB):
        self.db = db

    # ----------------- 1. GÖRSEL & BARKOD TANIMA KATMANI -----------------

    def _lookup_barcode_everywhere(self, gtin: str) -> Optional[Dict]:
        clean = "".join(ch for ch in (gtin or "") if ch.isdigit())
        if len(clean) < 8:
            return None

        # 1A. Yerel veritabanı kontrolü
        local_hit = self.db.lookup_by_barcode(clean)
        if local_hit:
            return {
                "brand": local_hit.get("brand") or "",
                "product_name": f"{local_hit.get('brand') or ''} {local_hit.get('family_name') or ''}".strip(),
                "pack_label": f"{local_hit.get('net_amount') or ''} {local_hit.get('base_unit') or ''}".strip(),
                "gtin": clean,
                "mode": "barcode_db",
                "product_key": local_hit.get("product_key"),
            }

        # 1B. MarketFiyatı (TÜBİTAK / Bakanlık 50.000+ barkod dizini)
        try:
            req = urllib.request.Request(
                MARKET_IDENTITY_URL,
                data=json.dumps({"identity": clean, "identityType": "BARCODE"}).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=6) as resp:
                body = json.loads(resp.read().decode("utf-8", errors="replace"))
            content = body.get("content") or []
            if content:
                c0 = content[0]
                return {
                    "brand": str(c0.get("brand") or "").strip(),
                    "product_name": str(c0.get("title") or "").strip(),
                    "pack_label": str(c0.get("refinedVolumeOrWeight") or "").strip(),
                    "gtin": clean,
                    "mode": "barcode_marketfiyati",
                    "prefetched_mf_items": content,
                    "image_url": str(c0.get("imageUrl") or "").strip(),
                    "category_label": str(c0.get("main_category") or c0.get("menu_category") or "").strip(),
                }
        except Exception:
            pass

        # 1C. OpenFoodFacts Küresel Barkod Veritabanı (3M+ ürün)
        try:
            off_url = f"https://world.openfoodfacts.org/api/v2/product/{clean}.json?fields=product_name,product_name_tr,brands,quantity,image_url,categories"
            req = urllib.request.Request(off_url, headers={"User-Agent": "DipciLens/1.0 (+https://dipci.tr)"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            if data.get("status") == 1 and data.get("product"):
                p = data["product"]
                brand = str(p.get("brands") or "").split(",")[0].strip()
                pname = str(p.get("product_name_tr") or p.get("product_name") or "").strip()
                qty = str(p.get("quantity") or "").strip()
                full_q = f"{brand} {pname} {qty}".strip()
                if full_q:
                    return {
                        "brand": brand,
                        "product_name": f"{brand} {pname}".strip() if brand.lower() not in pname.lower() else pname,
                        "pack_label": qty,
                        "gtin": clean,
                        "mode": "barcode_openfoodfacts",
                        "image_url": str(p.get("image_url") or "").strip(),
                    }
        except Exception:
            pass

        return None

    def _recognize_with_coarena_if_configured(self, raw_bytes: bytes, mime_type: str) -> Optional[Dict]:
        """Fotoğrafı Coarena (OpenAI uyumlu) görüntü modelleriyle tanır: önce gpt-6-luna, olmazsa gemini-3-8-flash.
        Model emin değilse boş ürün adı döner; uydurma ürün gösterilmez (Kemal, 16 Kas 2026)."""
        api_key = os.getenv("COARENA_API_KEY", "").strip()
        if not api_key or not raw_bytes:
            return None
        b64 = base64.b64encode(raw_bytes).decode("ascii")
        prompt = (
            "Görseldeki ürünü tanı (market ürünü, gıda, temizlik, elektronik, kozmetik, mobilya, ev eşyası, giyim vb.). "
            "Marka/model ancak görselde okunuyorsa ya da kesin tanınıyorsa yazılır; tahmin edilmez. Kutuda ya da üründe model kodu (örn: ST-9632) yazıyorsa product_name içine mutlaka eklenir. "
            "Ürünün ne olduğundan emin değilsen product_name boş bırakılır. "
            "product_name, Türkiye'deki e-ticaret sitelerinde aranacak kısa Türkçe addır (varsa marka + ürün türü + belirgin özellik; örn: 'kolçaksız döner çalışma koltuğu'). "
            "SADECE geçerli JSON döndür:\n"
            '{"brand": "", "product_name": "", "pack_label": "Varsa gramaj/hacim/adet", '
            '"gtin": "Görünüyorsa barkod rakamları", "category_label": "Kısa kategori adı", '
            '"generic_query": "Marka ve model kodu OLMADAN yalnız ürün türü, 2-4 kelime (örn: taşınabilir hava kompresörü)", '
            '"short_info": "Ürün hakkında 1 cümlelik Türkçe bilgi"}'
        )
        for model in ("gpt-6-luna", "gemini-3-8-flash"):
            try:
                payload = {
                    "model": model,
                    "messages": [{"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type or 'image/jpeg'};base64,{b64}"}},
                    ]}],
                    "max_tokens": 2000,
                    "reasoning_effort": "low",
                    "temperature": 0.1,
                }
                req = urllib.request.Request(
                    "https://coarena.ai/api/v1/chat/completions",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=25) as resp:
                    body = json.loads(resp.read().decode("utf-8", errors="replace"))
                txt = str((body.get("choices") or [{}])[0].get("message", {}).get("content") or "").strip()
                m = re.search(r"\{.*\}", txt, re.S)
                if not m:
                    continue
                parsed = json.loads(m.group(0))
                return {
                    "brand": str(parsed.get("brand") or "").strip(),
                    "product_name": str(parsed.get("product_name") or "").strip(),
                    "pack_label": str(parsed.get("pack_label") or "").strip(),
                    "gtin": "".join(c for c in str(parsed.get("gtin") or "") if c.isdigit()),
                    "category_label": str(parsed.get("category_label") or "").strip(),
                    "generic_query": str(parsed.get("generic_query") or "").strip(),
                    "short_info": str(parsed.get("short_info") or "").strip(),
                    "mode": f"coarena_vision:{model}",
                }
            except Exception:
                continue
        return None

    def _recognize_with_gemini_if_configured(self, raw_bytes: bytes, mime_type: str) -> Optional[Dict]:
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key or not raw_bytes:
            return None
        try:
            b64 = base64.b64encode(raw_bytes).decode("ascii")
            prompt = (
                "Görseldeki ürünü tanı (market ürünü, gıda, temizlik, elektronik, kozmetik, ev eşyası vb.). "
                "SADECE geçerli JSON döndür:\n"
                '{"brand": "Marka", "product_name": "Marka + Tam Ürün Adı + Model", '
                '"pack_label": "Varsa gramaj/hacim/adet (örn: 500 g, 1 L)", '
                '"gtin": "Görünüyorsa barkod rakamları", '
                '"category_label": "Kısa kategori adı", '
                '"short_info": "Ürün hakkında 1 cümlelik Türkçe özet bilgi"}'
            )
            payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": prompt},
                            {"inline_data": {"mime_type": mime_type or "image/jpeg", "data": b64}},
                        ]
                    }
                ],
                "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
            }
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                body = json.loads(resp.read().decode("utf-8", errors="replace"))
            txt = body.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "{}")
            parsed = json.loads(txt)
            if parsed.get("product_name") or parsed.get("brand"):
                return {
                    "brand": str(parsed.get("brand") or "").strip(),
                    "product_name": str(parsed.get("product_name") or "").strip(),
                    "pack_label": str(parsed.get("pack_label") or "").strip(),
                    "gtin": "".join(c for c in str(parsed.get("gtin") or "") if c.isdigit()),
                    "category_label": str(parsed.get("category_label") or "").strip(),
                    "short_info": str(parsed.get("short_info") or "").strip(),
                    "mode": "gemini_vision",
                }
        except Exception:
            pass
        return None

    def _normalize_mobile_image(self, raw_bytes: bytes):
        """Mobil EXIF yönünü (dikey/yatay) otomatik düzeltir ve RGB PIL nesnesi + temiz JPEG döndürür."""
        if not raw_bytes:
            return b"", None
        try:
            from PIL import Image, ImageOps
            img = Image.open(io.BytesIO(raw_bytes))
            img = ImageOps.exif_transpose(img)
            if img.mode != "RGB":
                img = img.convert("RGB")
            w, h = img.size
            max_dim = 1150
            if max(w, h) > max_dim:
                scale = max_dim / float(max(w, h))
                img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return buf.getvalue(), img
        except Exception:
            return raw_bytes, None

    def _detect_barcode_opencv(self, pil_img) -> str:
        """iOS Safari gibi tarayıcı BarcodeDetector desteği olmayan mobil cihazlar için sunucu taraflı barkod çözücü."""
        if pil_img is None:
            return ""
        try:
            import cv2
            import numpy as np
            bd = cv2.barcode.BarcodeDetector()
            arr_rgb = np.array(pil_img)
            arr_bgr = cv2.cvtColor(arr_rgb, cv2.COLOR_RGB2BGR)
            for candidate in (arr_bgr, cv2.rotate(arr_bgr, cv2.ROTATE_90_CLOCKWISE)):
                res = bd.detectAndDecodeWithType(candidate)
                if res and len(res) >= 2 and res[0]:
                    decoded_list = res[1] if isinstance(res[1], (list, tuple)) else [res[1]]
                    for code in decoded_list:
                        clean = "".join(ch for ch in str(code or "") if ch.isdigit())
                        if len(clean) in (8, 12, 13, 14):
                            return clean
        except Exception:
            pass
        return ""

    def _recognize_everyday_object_onnx(self, pil_img) -> Optional[Dict]:
        """
        Yazısız ve barkodsuz çekilen günlük eşya, meyve, teknoloji veya mutfak ürünlerini
        ('bir şeyin fotoğrafını çektim') yerel MobileNetV2 ONNX modeliyle tanır.
        """
        if pil_img is None:
            return None
        try:
            sess, classes = _get_object_classifier()
            if sess is None or not classes:
                return None
            import numpy as np
            from PIL import Image

            resized = pil_img.resize((256, 256), Image.Resampling.BILINEAR)
            left = (256 - 224) // 2
            top = (256 - 224) // 2
            cropped = resized.crop((left, top, left + 224, top + 224))

            arr = np.asarray(cropped, dtype=np.float32) / 255.0
            mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
            std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
            arr = (arr - mean) / std
            chw = np.transpose(arr, (2, 0, 1))[np.newaxis, :, :, :]

            in_name = sess.get_inputs()[0].name
            logits = sess.run(None, {in_name: chw})[0][0]
            logits = logits - np.max(logits)
            probs = np.exp(logits) / np.sum(np.exp(logits))
            top_indices = np.argsort(probs)[::-1][:5]

            for idx in top_indices:
                prob = float(probs[int(idx)])
                if prob < 0.11:
                    break
                cls_en = classes[int(idx)].strip().lower()
                mapped = IMAGENET_TR_SHOPPING_MAP.get(cls_en)
                if mapped:
                    tr_title, tr_cat = mapped
                    return {
                        "brand": "",
                        "product_name": tr_title,
                        "pack_label": "",
                        "gtin": "",
                        "category_label": tr_cat,
                        "short_info": (
                            f"Görselde marka/yazı olmadan '{tr_title}' nesnesi tanındı ve Türkiye'deki mağazalarda "
                            f"en düşük fiyatlı seçenekler sıralandı. Tam marka/model arıyorsanız kutucuktan daraltabilirsiniz."
                        ),
                        "mode": "dipci_visual_object",
                    }
        except Exception:
            pass
        return None

    def _recognize_with_onnx_visual_ocr(self, raw_bytes: bytes, pil_img=None) -> Optional[Dict]:
        """
        API anahtarı gerektirmeyen yerel ONNX Görsel Ambalaj & Etiket Okuyucu.
        Yazı puntosu yüksekliği (bounding box height), marka sözlüğü, model kodu ve gramaj analiziyle
        ürün adını çıkarır. Mobilde yan tutulmuş ürünlerde 90°/270° otomatik dener.
        """
        if not raw_bytes:
            return None
        try:
            ocr = _get_rapid_ocr()
            detections, _ = ocr(raw_bytes)
            if (not detections or len(detections) <= 1) and pil_img is not None:
                from PIL import Image
                for rot in (Image.Transpose.ROTATE_270, Image.Transpose.ROTATE_90):
                    rot_buf = io.BytesIO()
                    pil_img.transpose(rot).save(rot_buf, format="JPEG", quality=84)
                    alt_det, _ = ocr(rot_buf.getvalue())
                    if alt_det and len(alt_det) > len(detections or []):
                        detections = alt_det
                        if len(detections) >= 3:
                            break
        except Exception:
            return None

        if not detections:
            return None

        lines = []
        full_raw_concat = " ".join(str(txt) for _, txt, _ in detections)

        # Görselde barkod numarası basılıysa yakala (8 veya 13 hane)
        m_ean = re.search(r"\b(86[89]\d{10}|\d{13})\b", full_raw_concat)
        ocr_gtin = m_ean.group(1) if m_ean else ""
        if ocr_gtin:
            bc_hit = self._lookup_barcode_everywhere(ocr_gtin)
            if bc_hit:
                return bc_hit

        pack_info = _extract_pack_info_from_text(full_raw_concat)
        pack_label = pack_info[2] if pack_info else ""

        # Model kodu yakalama (örn: HD9285/96, V15, XC8057, WH-1000XM5)
        m_model = re.search(r"\b([A-Z]{1,4}[-]?\d{2,5}(?:/\d{1,3})?[A-Z]{0,2})\b", full_raw_concat)
        model_code = ""
        if m_model:
            cand_m = m_model.group(1)
            if not re.match(r"^\d+(?:GR|G|KG|ML|LT|L)$", cand_m, re.I):
                model_code = cand_m

        detected_brand = ""
        for box, raw_txt, conf in detections:
            if float(conf) < 0.52:
                continue
            txt = str(raw_txt or "").strip()
            if len(txt) < 2:
                continue
            ys = [pt[1] for pt in box] if box else [0, 10]
            height = max(ys) - min(ys)
            norm_line = _norm_tr(txt)
            norm_alpha = re.sub(r"[^a-z0-9\s]", " ", norm_line).strip()
            if not norm_alpha:
                continue

            # Marka eşleşmesi kontrolü
            if not detected_brand:
                for b_norm, b_disp in KNOWN_BRANDS.items():
                    if re.search(rf"\b{re.escape(b_norm)}\b", norm_alpha):
                        detected_brand = b_disp
                        break

            # Slogan / gürültü elemesi
            if any(np in norm_line for np in PACKAGE_NOISE_PHRASES):
                continue
            # Sadece gramaj olan satırı başlık kelimesi yapma (zaten pack_label olarak ayrıldı)
            if re.match(r"^(?:net\s*[:\.]?\s*)?\d+(?:[\.,]\d+)?\s*(?:kg|gr|g|ge|ml|lt|l|le|adet|li|lu)$", norm_line):
                continue
            # Anlamsız OCR paraziti (sesli harfi olmayan veya bozuk sembol dizisi) elemesi
            letters_only = re.sub(r"[^a-z]", "", norm_alpha)
            if len(letters_only) >= 4 and not re.search(r"[aeiou]", letters_only):
                continue
            if re.search(r"[\[\]\{\}\|\\]", txt):
                continue

            # Kelime düzeltmesi uygula
            cleaned_words = []
            for w in re.split(r"\s+", txt):
                w_clean = re.sub(r"^[^A-Za-z0-9ÇĞİÖŞÜçğıöşü]+|[^A-Za-z0-9ÇĞİÖŞÜçğıöşü/+-]+$", "", w)
                if len(w_clean) < 2 and not w_clean.isdigit():
                    continue
                w_n = _norm_tr(w_clean)
                if w_n in ("net", "ge", "gr", "kg", "ml", "lt"):
                    continue
                cleaned_words.append(OCR_WORD_FIXES.get(w_n, w_clean))

            if cleaned_words:
                lines.append((height, float(conf), " ".join(cleaned_words)))

        # En büyük puntolu (ana ürün adı ve marka) satırları öne al
        lines.sort(key=lambda x: (x[0] * (0.75 + 0.25 * x[1])), reverse=True)

        chosen_tokens: List[str] = []
        seen_norm = set()

        if detected_brand:
            for bw in detected_brand.split():
                seen_norm.add(_norm_tr(bw))
                chosen_tokens.append(bw)

        for _, _, phrase in lines[:5]:
            for w in phrase.split():
                wn = _norm_tr(w)
                if wn in seen_norm or len(wn) < 2:
                    continue
                seen_norm.add(wn)
                chosen_tokens.append(w)
                if len(chosen_tokens) >= 6:
                    break
            if len(chosen_tokens) >= 6:
                break

        if model_code and _norm_tr(model_code) not in seen_norm:
            chosen_tokens.append(model_code)

        product_guess = " ".join(chosen_tokens).strip()
        if not product_guess and not detected_brand:
            return None

        return {
            "brand": detected_brand or (chosen_tokens[0] if chosen_tokens else ""),
            "product_name": product_guess,
            "pack_label": pack_label,
            "model_code": model_code,
            "gtin": ocr_gtin,
            "mode": "dipci_lens_ocr",
        }

    # ----------------- 2. CANLI ÇOKLU MAĞAZA EN DÜŞÜK FİYAT ARAMA KATMANI -----------------

    def _search_marketfiyati_live(
        self,
        query: str,
        target_pack: Optional[Tuple[float, str, str]] = None,
        prefetched_items: Optional[List[Dict]] = None,
    ) -> Tuple[List[Dict], Dict]:
        """
        BİM, A101, ŞOK, Migros, CarrefourSA, Tarım Kredi ve Hakmar'da canlı fiyatları çeker.
        """
        items = list(prefetched_items or [])
        if not items and query:
            queries_to_try = [query]
            # Gramaj etiketi yüzünden eşleşme daralırsa gramajsız sorguyu da dene
            stripped_q = re.sub(r"\b\d+(?:[\.,]\d+)?\s*(?:kg|gr|g|ml|lt|l|adet)\b", "", query, flags=re.I).strip()
            if stripped_q and stripped_q.lower() != query.lower() and len(stripped_q) >= 3:
                queries_to_try.append(stripped_q)
            # Fotoğraftan gelen uzun adlar ("SEK Çiftlik tam yağlı pastörize taze süt") marketfiyati'nda boş dönüyor (Kemal, 17 Kas 2026):
            # kelime sayısını adım adım azaltarak yeniden dene.
            base_words = (stripped_q or query).split()
            for n in (4, 3, 2):
                if len(base_words) > n:
                    cand_q = " ".join(base_words[:n])
                    if cand_q.lower() not in [x.lower() for x in queries_to_try]:
                        queries_to_try.append(cand_q)
            if len(base_words) >= 3:
                cand_q = f"{base_words[0]} {base_words[-1]}"
                if cand_q.lower() not in [x.lower() for x in queries_to_try]:
                    queries_to_try.append(cand_q)

            for q_try in queries_to_try:
                try:
                    req = urllib.request.Request(
                        MARKET_API_URL,
                        data=json.dumps({"keywords": q_try, "pages": 0, "size": 24}).encode("utf-8"),
                        headers={
                            "Content-Type": "application/json; charset=utf-8",
                            "Accept": "application/json",
                            "User-Agent": "Mozilla/5.0 (compatible; DipciLens/1.0; +https://dipci.tr)",
                        },
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=8) as resp:
                        body = json.loads(resp.read().decode("utf-8", errors="replace"))
                        items = body.get("content") or []
                    if items:
                        break
                except Exception:
                    continue

        if not items:
            return [], {}

        q_tokens = [
            t for t in re.split(r"\s+", _norm_tr(query))
            if len(t) >= 3 and not re.match(r"^\d+(?:g|gr|kg|ml|l|lt)?$", t)
        ]

        # En alakalı referans ürünü ve paket gramajını seç
        scored_items: List[Tuple[float, Dict, Optional[Tuple[float, str, str]]]] = []
        for it in items:
            title = str(it.get("title") or "").strip()
            brand = str(it.get("brand") or "").strip()
            ref_w = str(it.get("refinedVolumeOrWeight") or "").strip()
            t_norm = _norm_tr(f"{brand} {title}")
            matched_toks = sum(1 for tok in q_tokens if tok in t_norm)
            if q_tokens and matched_toks == 0 and not prefetched_items:
                continue

            p_info = _extract_pack_info_from_text(f"{ref_w} {title}")
            score = matched_toks * 10.0 + len(it.get("productDepotInfoList") or []) * 1.5
            if target_pack and p_info and p_info[1] == target_pack[1]:
                ratio = p_info[0] / max(target_pack[0], 0.001)
                if 0.80 <= ratio <= 1.25:
                    score += 25.0
                elif 0.55 <= ratio <= 1.65:
                    score += 8.0
                else:
                    score -= 15.0
            scored_items.append((score, it, p_info))

        if not scored_items:
            return [], {}

        scored_items.sort(key=lambda x: x[0], reverse=True)
        best_it = scored_items[0][1]
        best_pack = target_pack or scored_items[0][2]

        meta = {
            "matched_title": str(best_it.get("title") or "").strip(),
            "matched_brand": str(best_it.get("brand") or "").strip(),
            "matched_pack": str(best_it.get("refinedVolumeOrWeight") or (best_pack[2] if best_pack else "")).strip(),
            "image_url": str(best_it.get("imageUrl") or "").strip(),
            "category_label": str(best_it.get("main_category") or best_it.get("menu_category") or "").strip(),
        }

        # Aynı ürünün (ve yakın gramajın) zincir marketlerdeki en düşük fiyatlarını topla
        best_title_tokens = [
            t for t in re.split(r"\s+", _norm_tr(meta["matched_title"]))
            if len(t) >= 3 and not re.match(r"^\d+", t)
        ][:3]

        per_market_best: Dict[str, Dict] = {}
        for sc, it, p_info in scored_items:
            if sc < scored_items[0][0] * 0.45 and not prefetched_items:
                continue
            title = str(it.get("title") or "").strip()
            t_norm = _norm_tr(title)
            if best_title_tokens and sum(1 for bt in best_title_tokens if bt in t_norm) < max(1, len(best_title_tokens) - 1):
                continue

            # Farklı boy/çoklu paket karışmasını önle
            if best_pack and p_info:
                if p_info[1] != best_pack[1]:
                    continue
                ratio = p_info[0] / max(best_pack[0], 0.001)
                if ratio < 0.72 or ratio > 1.35:
                    continue

            img_u = str(it.get("imageUrl") or "").strip()
            if img_u and not meta["image_url"]:
                meta["image_url"] = img_u

            for dep in it.get("productDepotInfoList") or []:
                raw_m = str(dep.get("marketAdi") or "").lower().strip()
                if raw_m not in MARKET_META:
                    continue
                try:
                    price = float(dep.get("price") or 0)
                except Exception:
                    price = 0.0
                if price <= 1.0:
                    continue

                plat_key, seller_label, base_search_url = MARKET_META[raw_m]
                unit_txt = str(dep.get("unitPrice") or "").strip()
                if not unit_txt and p_info and p_info[0] > 0:
                    u_val = round(price / p_info[0], 2)
                    unit_txt = f"{u_val:,.2f} TL/{p_info[1]}".replace(",", "X").replace(".", ",").replace("X", ".")

                # Doğrudan ürün linki çözümleme
                direct_url = extract_direct_url_from_image(raw_m, title, img_u)
                is_branch = plat_key in ("bim", "tarimkredi", "hakmar")
                final_url = direct_url or (
                    base_search_url if is_branch else f"{base_search_url}{urllib.parse.quote_plus(title)}"
                )

                cand = {
                    "platform": plat_key,
                    "seller_name": seller_label,
                    "title": title,
                    "price": round(price, 2),
                    "unit_price_text": unit_txt,
                    "url": final_url,
                    "is_direct_product": bool(direct_url),
                    "is_branch_only": is_branch,
                    "source": "zincir_market",
                }
                prev = per_market_best.get(plat_key)
                if not prev or cand["price"] < prev["price"]:
                    per_market_best[plat_key] = cand

        # TR IP üzerinde çalışıyorsa Migros ve ŞOK doğrudan ürün sayfasını canlı tamamla
        if "migros" in per_market_best and not per_market_best["migros"]["is_direct_product"]:
            m_url, m_img = resolve_migros_direct_product(per_market_best["migros"]["title"], best_title_tokens[:2])
            if m_url:
                per_market_best["migros"]["url"] = m_url
                per_market_best["migros"]["is_direct_product"] = True
            if m_img and not meta["image_url"]:
                meta["image_url"] = m_img

        if "sok" in per_market_best and not per_market_best["sok"]["is_direct_product"]:
            s_url = resolve_sok_direct_product(per_market_best["sok"]["title"], best_title_tokens[:2])
            if s_url:
                per_market_best["sok"]["url"] = s_url
                per_market_best["sok"]["is_direct_product"] = True

        return list(per_market_best.values()), meta

    def _search_online_marketplaces_live(
        self,
        query: str,
        target_pack: Optional[Tuple[float, str, str]] = None,
    ) -> Tuple[List[Dict], Dict]:
        """
        Amazon.com.tr, Hepsiburada, Trendyol, n11, Pazarama, MediaMarkt, Teknosa, PttAVM vb.
        online mağazalardaki canlı fiyatları ve doğrudan ürün linklerini schema.org JSON-LD üzerinden çeker.
        """
        if not query or len(query.strip()) < 2:
            return [], {}
        # Yabancı aksanlı harfler (Nescafé) Akakçe aramasını ve kelime eşleşmesini bozuyor (16 Kas 2026)
        query = query.translate(str.maketrans("éèêëáàâäíìîóòôúùû", "eeeeaaaaiiiooouuu"))

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "tr-TR,tr;q=0.9",
        }
        try:
            # Akakçe "100 g" ile "100 gr"yi farklı arıyor; doğru ürün "gr" ile geliyor (16 Kas 2026, Nescafe Gold)
            ak_query = re.sub(r"(\d)\s*g\b", r"\1 gr", query, flags=re.I)
            search_url = f"https://www.akakce.com/arama/?q={urllib.parse.quote_plus(ak_query)}"
            req = urllib.request.Request(search_url, headers=headers)
            with urllib.request.urlopen(req, timeout=6.5) as resp:
                html_txt = resp.read().decode("utf-8", errors="ignore")
        except Exception:
            return [], {}

        # Akakçe arama sonuçları (16 Kas 2026'dan beri) sayfa içi JSON'da (Astro props) geliyor; eski HTML kart yapısı yedek
        candidates: List[Tuple[str, str]] = []
        m_props = re.search(r'component-export="SearchWrapper"[^>]*props="([^"]+)"', html_txt)
        if m_props:
            try:
                def _dv(x):
                    if isinstance(x, list) and len(x) == 2 and isinstance(x[0], int):
                        return [_dv(i) for i in x[1]] if x[0] == 1 else _dv(x[1])
                    if isinstance(x, dict):
                        return {k: _dv(v) for k, v in x.items()}
                    return x
                props = _dv(json.loads(html_lib.unescape(m_props.group(1))))
                for pr in ((props.get("searchData") or {}).get("productList") or {}).get("products") or []:
                    u = str(pr.get("url") or "")
                    if u.startswith("/") and u.endswith(".html") and "," in u:
                        candidates.append((str(pr.get("name") or ""), u))
            except Exception:
                candidates = []
        if not candidates:
            for _, inner in re.findall(r'<li[^>]+data-pr="(\d+)"[^>]*>(.*?)</li>', html_txt, re.S)[:20]:
                t_m = re.search(r'<h3[^>]*class="pn_v8"[^>]*>(.*?)</h3>', inner, re.S) or re.search(r'title="([^"]+)"', inner)
                h_m = re.search(r'href="(/[^"]+\.html)"', inner)
                if t_m and h_m and "," in h_m.group(1):
                    candidates.append((re.sub(r"<[^>]+>", "", t_m.group(1)), h_m.group(1)))
        if not candidates:
            return [], {}

        q_tokens = [
            t for t in re.split(r"\s+", _norm_tr(query))
            if len(t) >= 2 and not re.match(r"^(?:gr|kg|ml|lt|adet)$", t)
        ]

        word_toks = [t for t in q_tokens if not re.search(r"\d", t)]
        head_stem = word_toks[-1][:5] if word_toks and len(word_toks[-1]) >= 4 else ""

        best_href = ""
        best_score = -999.0
        for raw_title, href in candidates[:24]:
            t_clean = html_lib.unescape(raw_title.strip())
            t_norm = _norm_tr(t_clean)
            matched = sum(1 for tok in q_tokens if tok in t_norm)
            # Tek ortak kelime yetmez ("hava" → hava temizleyici): sorgu kelimelerinin en az %60'ı başlıkta geçmeli
            # (Kemal, 16 Kas 2026: ST-9632 hava kompresörü fotoğrafına hava temizleyici fiyatı geldi).
            if q_tokens and matched < max(1, math.ceil(len(q_tokens) * 0.6)):
                continue
            # Türkçede ürünün ne olduğu son kelimededir ("çalışma koltuğu" → koltuk): o kelimenin kökü başlıkta yoksa ürün başkadır
            if head_stem and head_stem not in t_norm:
                continue
            sc = matched * 12.0
            if any(re.search(r"\d", tok) and re.search(r"[a-z]", tok) and tok in t_norm for tok in q_tokens):
                sc += 40.0  # model kodu eşleşmesi
            if target_pack:
                c_pack = _extract_pack_info_from_text(t_clean)
                if c_pack and c_pack[1] == target_pack[1]:
                    ratio = c_pack[0] / max(target_pack[0], 0.001)
                    if 0.80 <= ratio <= 1.25:
                        sc += 25.0
                    elif ratio > 1.8 or ratio < 0.5:
                        continue  # farklı paket boyu başka üründür (16 Kas: 100 g yerine 200 g geliyordu)
            # Çoklu paket (4'lü, 12'li, x 4 adet) sorguda yoksa başka üründür
            multi_re = r"\d+\s*['’]?\s*(?:li|lı|lu|lü)\b|\bx\s*\d+\s*adet"
            if re.search(multi_re, t_clean, re.I) and not re.search(multi_re, query, re.I):
                continue
            # Yedek parça / kılıf / aksesuar başlıklarını ana ürün aramasında geri plana at
            if any(bad in t_norm for bad in ("kilif", "ekran koruyucu", "yedek", "uyumlu", "aksesuar", "aparati")) and not any(
                bad in _norm_tr(query) for bad in ("kilif", "yedek", "aksesuar")
            ):
                sc -= 30.0

            if sc > best_score:
                best_score = sc
                best_href = f"https://www.akakce.com{href}"

        if not best_href:
            return [], {}

        try:
            with urllib.request.urlopen(urllib.request.Request(best_href, headers=headers), timeout=6.5) as r2:
                detail_html = r2.read().decode("utf-8", errors="ignore")
        except Exception:
            return [], {}

        for ld_raw in re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', detail_html, re.S):
            if ('"Product"' not in ld_raw and '"ProductGroup"' not in ld_raw) or '"offers"' not in ld_raw:
                continue
            try:
                prod_ld = json.loads(ld_raw)
            except Exception:
                continue
            if prod_ld.get("@type") not in ("Product", "ProductGroup"):
                continue

            p_name = html_lib.unescape(str(prod_ld.get("name") or "").strip())
            b_obj = prod_ld.get("brand")
            b_name = html_lib.unescape(str(b_obj.get("name") if isinstance(b_obj, dict) else (b_obj or "")).strip())
            desc = html_lib.unescape(str(prod_ld.get("description") or "").strip())

            img_url = ""
            imgs = prod_ld.get("image")
            if isinstance(imgs, list) and imgs:
                im0 = imgs[0]
                img_url = str(im0.get("contentUrl") if isinstance(im0, dict) else im0).strip()
            elif isinstance(imgs, dict):
                img_url = str(imgs.get("contentUrl") or imgs.get("url") or "").strip()
            elif isinstance(imgs, str):
                img_url = imgs.strip()
            if img_url.startswith("//"):
                img_url = "https:" + img_url

            p_pack = _extract_pack_info_from_text(p_name) or target_pack
            agg = prod_ld.get("offers") or {}
            raw_offers = agg.get("offers") if isinstance(agg, dict) else (agg if isinstance(agg, list) else [])
            if not isinstance(raw_offers, list):
                raw_offers = []
            if not raw_offers and isinstance(prod_ld.get("hasVariant"), list):
                for var_obj in prod_ld["hasVariant"][:4]:
                    v_off = var_obj.get("offers")
                    if isinstance(v_off, list):
                        raw_offers.extend(v_off)
                    elif isinstance(v_off, dict) and isinstance(v_off.get("offers"), list):
                        raw_offers.extend(v_off["offers"])

            offers_out: List[Dict] = []
            seen_urls = set()
            for off in raw_offers[:16]:
                try:
                    price = float(off.get("price") or 0)
                except Exception:
                    price = 0.0
                if price <= 1.0:
                    continue
                u = str(off.get("url") or "").strip()
                if not u or u in seen_urls:
                    continue
                seen_urls.add(u)

                seller_obj = off.get("seller") or {}
                raw_seller = str(seller_obj.get("name") if isinstance(seller_obj, dict) else seller_obj).strip()
                plat_key, seller_disp = _seller_to_platform_key(raw_seller, u)

                unit_txt = ""
                if p_pack and p_pack[0] > 0:
                    u_val = round(price / p_pack[0], 2)
                    unit_txt = f"{u_val:,.2f} TL/{p_pack[1]}".replace(",", "X").replace(".", ",").replace("X", ".")

                offers_out.append(
                    {
                        "platform": plat_key,
                        "seller_name": seller_disp,
                        "title": p_name,
                        "price": round(price, 2),
                        "unit_price_text": unit_txt,
                        "url": _apply_affiliate_url(u),
                        "is_direct_product": True,
                        "is_branch_only": False,
                        "source": "online_pazaryeri",
                    }
                )

            meta = {
                "matched_title": p_name,
                "matched_brand": b_name,
                "matched_pack": p_pack[2] if p_pack else "",
                "image_url": img_url,
                "description": desc,
            }
            return offers_out, meta

        return [], {}

    # ----------------- 3. ANA LENS İŞ AKIŞI -----------------

    def analyze_and_compare(
        self,
        image_base64: str = "",
        mime_type: str = "image/jpeg",
        detected_gtin: str = "",
        query_override: str = "",
        raw_image_bytes: Optional[bytes] = None,
    ) -> Dict:
        clean_gtin = "".join(ch for ch in (detected_gtin or "") if ch.isdigit())
        q_manual = (query_override or "").strip()

        raw_bytes = raw_image_bytes or b""
        if not raw_bytes and image_base64 and not q_manual:
            clean_b64 = image_base64.strip()
            if "," in clean_b64 and clean_b64.startswith("data:"):
                hdr, clean_b64 = clean_b64.split(",", 1)
                m_mime = re.search(r"data:([^;]+);base64", hdr)
                if m_mime:
                    mime_type = m_mime.group(1)
            try:
                raw_bytes = base64.b64decode(clean_b64[:8_000_000], validate=False)
            except Exception:
                raw_bytes = b""

        pil_img = None
        if raw_bytes and not q_manual:
            raw_bytes, pil_img = self._normalize_mobile_image(raw_bytes)
            mime_type = "image/jpeg"
            if not clean_gtin and pil_img is not None:
                clean_gtin = self._detect_barcode_opencv(pil_img)

        recog: Optional[Dict] = None

        # Adım 1: Kullanıcı doğrudan ürün adını düzenlediyse veya yazdıysa
        if q_manual:
            if re.match(r"^\d{8,14}$", q_manual):
                clean_gtin = q_manual
                recog = self._lookup_barcode_everywhere(clean_gtin)
            if not recog:
                pk = _extract_pack_info_from_text(q_manual)
                recog = {
                    "brand": "",
                    "product_name": q_manual,
                    "pack_label": pk[2] if pk else "",
                    "gtin": clean_gtin,
                    "mode": "manual_query",
                }

        # Adım 2: Barkod tespit edildiyse (Tarayıcı veya Sunucu OpenCV)
        if not recog and clean_gtin:
            recog = self._lookup_barcode_everywhere(clean_gtin)

        # Adım 3: Coarena görüntü modeli (gpt-6-luna → gemini-3-8-flash). Model cevap verdiyse
        # sonucuna güvenilir; emin değilse boş döner ve kullanıcıdan ürün adı istenir (uydurma yok).
        coarena_answered = False
        if not recog and raw_bytes:
            recog = self._recognize_with_coarena_if_configured(raw_bytes, mime_type)
            coarena_answered = recog is not None

        # Adım 4: Gemini Vision (yapılandırılmışsa)
        if not recog and raw_bytes:
            recog = self._recognize_with_gemini_if_configured(raw_bytes, mime_type)

        # Adım 5: Yerel ONNX Görsel Ambalaj & Etiket Okuyucu — yalnız görüntü modeli hiç cevap vermediyse
        if not recog and raw_bytes and not coarena_answered:
            recog = self._recognize_with_onnx_visual_ocr(raw_bytes, pil_img=pil_img)

        # Küçük yerel nesne modeli (MobileNet) KALDIRILDI: ofis koltuğuna "sallanan sandalye" diyordu (Kemal, 16 Kas 2026).

        if not recog or not (recog.get("product_name") or recog.get("brand")):
            return {
                "ok": False,
                "needs_query": True,
                "error": (
                    "Fotoğraftaki ürünü net seçemedik. Ürünü biraz daha yakından çekebilir "
                    "veya ne aradığınızı aşağıdaki kutuya yazarak en ucuz mağazaları hemen görebilirsiniz."
                ),
            }

        search_query = recog.get("product_name") or recog.get("brand") or ""
        pack_label = recog.get("pack_label") or ""
        if pack_label and pack_label.lower() not in search_query.lower():
            full_search_query = f"{search_query} {pack_label}".strip()
        else:
            full_search_query = search_query

        target_pack = _extract_pack_info_from_text(f"{full_search_query} {pack_label}")
        prefetched_mf = recog.get("prefetched_mf_items")

        # Adım 5: Zincir marketleri VE online e-ticaret sitelerini paralel canlı sorgula
        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_mf = pool.submit(self._search_marketfiyati_live, full_search_query, target_pack, prefetched_mf)
            fut_ec = pool.submit(self._search_online_marketplaces_live, full_search_query, target_pack)
            mf_offers, mf_meta = fut_mf.result()
            ec_offers, ec_meta = fut_ec.result()

        # MarketFiyatı temiz başlık bulduysa ve online pazaryeri OCR yazım farkından boş döndüyse temiz başlıkla tamamla
        if mf_offers and not ec_offers and mf_meta.get("matched_title"):
            ec_offers, ec_meta = self._search_online_marketplaces_live(mf_meta["matched_title"], target_pack)

        # İki kaynak da boş döndüyse ve sorgu çok uzunsa ilk 3 anahtar kelimeyle ikinci deneme yap
        if not mf_offers and not ec_offers:
            # Görüntü modeli markasız ürün türünü verdiyse onunla, yoksa ilk 3 kelimeyle ikinci deneme
            short_q = (recog.get("generic_query") or "").strip()
            if not short_q:
                short_words = [w for w in search_query.split() if len(w) >= 2][:3]
                short_q = " ".join(short_words)
            if short_q and short_q.lower() != full_search_query.lower():
                with ThreadPoolExecutor(max_workers=2) as pool:
                    fut_mf = pool.submit(self._search_marketfiyati_live, short_q, target_pack, None)
                    fut_ec = pool.submit(self._search_online_marketplaces_live, short_q, target_pack)
                    mf_offers, mf_meta = fut_mf.result()
                    ec_offers, ec_meta = fut_ec.result()

        # Adım 6: Teklifleri birleştir, aykırı paket/çoklu koli sapmalarını temizle ve EN UCUZDAN PAHALIYA sırala
        dedup_map: Dict[Tuple, Dict] = {}
        for off in mf_offers + ec_offers:
            if not off.get("is_direct_product") and off["platform"] in ("migros", "sok", "carrefoursa", "a101"):
                row_u = self.db.conn.execute(
                    """
                    SELECT url FROM price_snapshots
                    WHERE platform = ? AND title LIKE ?
                      AND (url LIKE '%-p-%' OR url LIKE '%_p-%')
                    ORDER BY id DESC LIMIT 1
                    """,
                    (off["platform"], f"%{off['title'][:18]}%"),
                ).fetchone()
                if row_u and row_u["url"]:
                    off["url"] = row_u["url"]
                    off["is_direct_product"] = True

            sig = (
                (off["platform"], round(off["price"], 1))
                if off["platform"] in ("migros", "carrefoursa", "a101", "sok", "bim", "tarimkredi", "hakmar")
                else (off["platform"], off["seller_name"], round(off["price"], 1))
            )
            prev = dedup_map.get(sig)
            if not prev or (off.get("is_direct_product") and not prev.get("is_direct_product")):
                dedup_map[sig] = off

        combined: List[Dict] = list(dedup_map.values())

        # Hem market hem online pazaryeri varsa çoklu koli (örn. 12'li paket) aykırı fiyatlarını filtrele
        if mf_offers and ec_offers:
            mf_med = statistics.median([x["price"] for x in mf_offers])
            combined = [
                x for x in combined
                if x["source"] == "zincir_market" or (0.35 * mf_med <= x["price"] <= 2.6 * mf_med)
            ]

        combined.sort(key=lambda x: x["price"])

        # En ucuz rozetini ve fiyat farkını hesapla
        lowest_price = combined[0]["price"] if combined else 0.0
        highest_price = combined[-1]["price"] if len(combined) >= 2 else lowest_price
        median_price = round(statistics.median([x["price"] for x in combined]), 2) if combined else 0.0
        savings_tl = round(max(0.0, highest_price - lowest_price), 2)
        savings_pct = round((savings_tl / highest_price) * 100, 1) if highest_price > 0 and savings_tl > 0 else 0.0

        for idx, off in enumerate(combined):
            off["rank"] = idx + 1
            off["is_cheapest"] = idx == 0
            off["diff_vs_cheapest_tl"] = round(off["price"] - lowest_price, 2)

        # Yerel katalogda 90 günlük geçmişi olan bir ürünse product_key bağla
        matched_local_key = recog.get("product_key") or ""
        if not matched_local_key:
            q_n = _norm_tr(mf_meta.get("matched_title") or ec_meta.get("matched_title") or search_query)
            for r in self.db.conn.execute("SELECT product_key, brand, family_name FROM canonical_products WHERE is_private_label = 0").fetchall():
                b_n = _norm_tr(r["brand"])
                f_n = _norm_tr(r["family_name"])
                if b_n and b_n in q_n and any(w in q_n for w in f_n.split() if len(w) >= 4):
                    matched_local_key = r["product_key"]
                    break

        display_title = (
            mf_meta.get("matched_title")
            or ec_meta.get("matched_title")
            or recog.get("product_name")
            or search_query
        )
        display_brand = (
            mf_meta.get("matched_brand")
            or ec_meta.get("matched_brand")
            or recog.get("brand")
            or ""
        )
        display_pack = (
            mf_meta.get("matched_pack")
            or ec_meta.get("matched_pack")
            or pack_label
        )
        display_img = (
            mf_meta.get("image_url")
            or ec_meta.get("image_url")
            or recog.get("image_url")
            or ""
        )
        display_cat = (
            mf_meta.get("category_label")
            or recog.get("category_label")
            or ("Süpermarket & Gıda" if mf_offers else "Online Mağaza Ürünü")
        )

        # Ürün özet bilgi metni (sade, anlaşılır Türkçe)
        if combined:
            cheapest_seller = combined[0]["seller_name"]
            info_summary = recog.get("short_info") or (
                f"{display_title} ürünü şu an {len(combined)} farklı mağazada bulundu. "
                f"En düşük fiyat {_fmt_tl(lowest_price)} TL ile {cheapest_seller} mağazasında"
                + (
                    f"; en yüksek fiyatlı mağazaya ({_fmt_tl(highest_price)} TL) göre {_fmt_tl(savings_tl)} TL (%{savings_pct}) daha ucuz."
                    if savings_tl > 1
                    else "."
                )
            )
        else:
            info_summary = (
                f"'{display_title}' için anlık stok fiyatı bulunamadı; aşağıdaki doğrudan mağaza arama "
                "butonlarıyla tüm sitelerde kontrol edebilirsiniz."
            )

        # Doğrudan diğer büyük pazaryerlerinde hızlı arama kısayolları
        q_enc = urllib.parse.quote_plus(display_title)
        quick_search_links = [
            {
                "platform": "amazon",
                "label": "Amazon.com.tr'de Ara",
                "url": f"https://www.amazon.com.tr/s?k={q_enc}&tag={AMAZON_ASSOCIATE_TAG}",
            },
            {
                "platform": "hepsiburada",
                "label": "Hepsiburada'da Ara",
                "url": f"https://www.hepsiburada.com/ara?q={q_enc}",
            },
            {
                "platform": "trendyol",
                "label": "Trendyol'da Ara",
                "url": f"https://www.trendyol.com/sr?q={q_enc}",
            },
        ]

        return {
            "ok": True,
            "recognition_mode": recog.get("mode") or "dipci_lens",
            "recognized_query": full_search_query,
            "product_info": {
                "title": display_title,
                "brand": display_brand,
                "pack_size": display_pack,
                "category": display_cat,
                "gtin": recog.get("gtin") or clean_gtin,
                "image_url": display_img,
                "summary": info_summary,
                "local_product_key": matched_local_key,
                "lowest_price": lowest_price,
                "median_price": median_price,
                "highest_price": highest_price,
                "savings_tl": savings_tl,
                "savings_pct": savings_pct,
                "store_count": len(combined),
            },
            "offers": combined,
            "quick_search_links": quick_search_links,
        }
