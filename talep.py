"""
Ürün talep puanı (Kemal, 17 Kas 2026: "çok sık kullanılan, çok aranan, çok alınan, çok harcama yapılan ürünler ana ekranda daha fazla yer alsın").

Kaynak: TÜİK TÜFE sepetinde gıda payı ~%24 (en büyük harcama kalemi temel gıda: ekmek, süt, yağ, peynir, et/tavuk, yumurta),
temizlik/kişisel bakım düzenli harcaması ve fiyat karşılaştırma sitelerinde en çok aranan elektronik/beyaz eşya
(iPhone, PS5, klima, Dyson, airfryer, buzdolabı, çamaşır makinesi).
Yılda bir iki kez alınan ya da ihtiyaç dışı ürünler (çikolata, gofret, krem, parfüm) ana sayfada geriye düşer.
"""
import hashlib
import re
import sqlite3
import time

# kategori -> [(puan, anahtar kelimeler)] ; ilk eşleşen kural kazanır (önce ihtiyaç dışılar)
_KURALLAR = {
    "gida": [
        (15, ["cikolata", "gofret", "sekerleme", "sakiz", "lokum", "draje", "jelibon", "kek", "pasta sus", "kurabiye", "burger", "bar"]),
        (55, ["ice tea", "soguk cay", "makarna sosu", "protein"]),
        (100, ["ekmek", "sut", "yumurta", "aycicek yagi", "zeytinyagi", "zeytin yagi", "toz seker", "kup seker",
               "cay", "beyaz peynir", "kasar", "peynir", "tavuk", "kiyma", "dana", "pirinc", "makarna", "bulgur",
               "un", "yogurt", "tereyag", "salca", "mercimek", "nohut", "kuru fasulye", "icme suyu", "damacana",
               "dogal kaynak suyu", "maden suyu"]),
        (85, ["zeytin", "kahve", "nescafe", "margarin", "sivi yag", "misir yagi", "ayran", "kefir", "sucuk", "salam",
              "tuz", "bal", "recel", "tahin", "pekmez", "domates", "patates", "sogan", "corba", "konserve", "ton baligi",
              "yulaf", "seker", "su"]),
        (55, ["biskuvi", "kraker", "cips", "gazli icecek", "kola", "meyve suyu", "dondurma", "gazoz", "soda"]),
    ],
    "temizlik": [
        (100, ["camasir deterjan", "sivi deterjan", "toz deterjan", "matik", "bulasik", "tuvalet kagidi", "kagit havlu",
               "pecete", "camasir suyu", "yumusatici"]),
        (85, ["yuzey temizleyici", "cop torbasi", "sivi sabun", "sabun", "cam temizleyici", "kirec", "yag cozucu"]),
    ],
    "kisisel_bakim": [
        (15, ["parfum", "kolonya", "ruj", "oje", "far", "rimel", "maskara", "makyaj", "fondoten", "allik", "maske",
              "serum", "krem", "losyon", "kozmetik"]),
        (85, ["sampuan", "dis macunu", "dis fircasi", "sabun", "dus jeli", "deodorant", "islak mendil", "pecete",
              "ped", "hijyenik", "tiras", "pamuk"]),
    ],
    "anne_bebek": [
        (100, ["bebek bezi", "cocuk bezi", "kulot bez", "islak mendil", "bebek mamasi", "devam sutu", "bez"]),
        (85, ["bebek sampuani", "pisik", "biberon", "mama"]),
    ],
    "evcil_hayvan": [(85, ["kedi mamasi", "kopek mamasi", "mama", "kum"])],
    "teknoloji_beyaz_esya": [
        (85, ["iphone", "samsung galaxy", "xiaomi", "redmi", "playstation", "ps5", "klima", "dyson", "airfryer",
              "air fryer", "robot supurge", "supurge", "buzdolabi", "camasir makinesi", "bulasik makinesi", "televizyon",
              "tv", "laptop", "dizustu", "notebook", "tablet", "ipad", "airpods", "kulaklik", "akilli saat", "kombi",
              "firin", "kurutma makinesi", "derin dondurucu", "monitor"]),
        (70, ["tost makinesi", "kahve makinesi", "cay makinesi", "su isitici", "kettle", "utu", "sac kurutma", "blender",
              "mikser", "yazici"]),
    ],
    "ev_yasam_mutfak": [(55, ["tencere", "tava", "saklama kabi", "termos", "bardak", "catal", "bicak"])],
}
# Kategori varsayılanı (kuralla eşleşmeyen ürün)
_VARSAYILAN = {"gida": 45, "temizlik": 60, "kisisel_bakim": 40, "anne_bebek": 60, "evcil_hayvan": 55,
               "teknoloji_beyaz_esya": 55, "ev_yasam_mutfak": 25, "saglik_spor_outdoor": 25}

# Kelime başında başlar, sonunda en çok 4 harflik Türkçe ek alabilir (süt/sütü, yağı, bezi) — "sütlaç" gibi uzun türetmeler tutmaz
_DESEN = {
    kat: [(p, re.compile(r"(?<![a-z])(?:" + "|".join(re.escape(k) for k in ks) + r")[a-z]{0,3}(?![a-z])"))
          for p, ks in kurallar]
    for kat, kurallar in _KURALLAR.items()
}
_onbellek: dict = {}


def puan(search_norm: str, kategori: str = "") -> int:
    anahtar = f"{kategori}|{search_norm}"
    v = _onbellek.get(anahtar)
    if v is not None:
        return v
    s = search_norm or ""
    v = _VARSAYILAN.get(kategori, 35)
    for p, rx in _DESEN.get(kategori, []):
        if rx.search(s):
            v = p
            break
    if len(_onbellek) > 20000:
        _onbellek.clear()
    _onbellek[anahtar] = v
    return v


def karisim(product_key: str) -> int:
    """Aynı puan grubundakileri her 3 saatte bir başka sırayla göster (sürekli aynı ürünler dönmesin)."""
    dilim = int(time.time() // 10800)
    return int(hashlib.md5(f"{product_key}|{dilim}".encode()).hexdigest()[:8], 16)


def kaydet(conn: sqlite3.Connection) -> None:
    conn.create_function("dipci_talep", 2, puan, deterministic=True)
    conn.create_function("dipci_karisim", 1, karisim)
