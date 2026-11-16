"""
dipci.tr — Yüksek Kapasiteli Canlı Market Fiyat Senkronizasyonu (75+ Ürün),
Doğrudan Mağaza Ürün Linki Çözümleyici, Fiyat Alarmı Tetikleyici & IndexNow Motoru.
"""

import hashlib
import json
import re
import statistics
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from collectors import MultiSourceCollector
from memory_db import PriceMemoryDB
from price_alerts import PriceAlertService
from scanner import compute_ean13_with_check
from seo_engine import CANONICAL_ORIGIN, INDEXNOW_KEY, key_to_slug

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "dipci_platform.db"
MARKET_API_URL = "https://api.marketfiyati.org.tr/api/v2/search"
MIGROS_SEARCH_API = "https://www.migros.com.tr/rest/search/screens/products?q="

MARKET_META: Dict[str, Tuple[str, str, str]] = {
    "migros": ("migros", "Migros Sanal Market", "https://www.migros.com.tr/arama?q="),
    "carrefour": ("carrefoursa", "CarrefourSA Online", "https://www.carrefoursa.com/search/?text="),
    "a101": ("a101", "A101 Kapıda / Mağaza", "https://www.a101.com.tr/kapida/arama?q="),
    "sok": ("sok", "Cepte ŞOK", "https://www.sokmarket.com.tr/arama?q="),
    "bim": ("bim", "BİM Mağaza (Şube Rafı)", "https://www.google.com/maps/search/B%C4%B0M+Market"),
    "tarim_kredi": ("tarimkredi", "Tarım Kredi (Şube Rafı)", "https://www.google.com/maps/search/Tar%C4%B1m+Kredi+Kooperatif+Market"),
    "hakmar": ("hakmar", "Hakmar Express (Şube Rafı)", "https://www.google.com/maps/search/Hakmar+Express"),
}

# (product_key, brand, family_name, category, base_unit, ref_amt, query, must_contain)
COMPACT_SUPERMARKET_SPECS = [
    # ==================== HER EVİN TEMEL MUTFAK & EV İHTİYAÇLARI (19 ANA ÜRÜN) ====================
    ("temel_yumurta", "Temel İhtiyaç", "Yumurta (10'lu - 15'li Paket)", "gida", "adet", 15.0, "yumurta", ["yumurta"]),
    ("temel_sut_1lt", "Temel İhtiyaç", "Tam Yağlı Süt (1 Litre)", "gida", "litre", 1.0, "tam yağlı süt 1 l", ["süt"]),
    ("temel_yogurt", "Temel İhtiyaç", "Sade Yoğurt (500 g - 1.5 Kg)", "gida", "kg", 0.75, "yoğurt", ["yoğurt"]),
    ("temel_beyaz_peynir", "Temel İhtiyaç", "Tam Yağlı Beyaz Peynir (500 g)", "gida", "kg", 0.5, "beyaz peynir 500", ["beyaz", "peynir"]),
    ("temel_kasar_peyniri", "Temel İhtiyaç", "Taze Kaşar Peyniri (500 - 700 g)", "gida", "kg", 0.6, "kaşar peyniri", ["kaşar"]),
    ("temel_tereyagi", "Temel İhtiyaç", "Tereyağı (500 g)", "gida", "kg", 0.5, "tereyağı 500", ["tereyağ"]),
    ("temel_dana_kiyma", "Temel İhtiyaç", "Dana Kıyma (400 - 500 g)", "gida", "kg", 0.4, "dana kıyma", ["dana", "kıyma"]),
    ("temel_siyah_zeytin", "Temel İhtiyaç", "Siyah Zeytin (500 g - 1 Kg)", "gida", "kg", 0.75, "siyah zeytin", ["siyah", "zeytin"]),
    ("temel_aycicek_yagi", "Temel İhtiyaç", "Ayçiçek Yağı (1 - 2 Litre)", "gida", "litre", 2.0, "ayçiçek yağı 2", ["ayçiçek", "yağ"]),
    ("temel_bugday_unu", "Temel İhtiyaç", "Buğday Unu (2 Kg)", "gida", "kg", 2.0, "buğday unu 2 kg", ["un"]),
    ("temel_toz_seker", "Temel İhtiyaç", "Toz Şeker (5 Kg)", "gida", "kg", 5.0, "toz şeker", ["toz", "şeker"]),
    ("temel_kirmizi_mercimek", "Temel İhtiyaç", "Kırmızı Mercimek (1 Kg)", "gida", "kg", 1.0, "kırmızı mercimek 1 kg", ["kırmızı", "mercimek"]),
    ("temel_nohut", "Temel İhtiyaç", "Nohut (1 Kg)", "gida", "kg", 1.0, "nohut 1 kg", ["nohut"]),
    ("temel_pirinc", "Temel İhtiyaç", "Osmancık Pirinç (1 Kg)", "gida", "kg", 1.0, "osmancık pirinç 1 kg", ["pirinç"]),
    ("temel_makarna", "Temel İhtiyaç", "Makarna (500 g)", "gida", "kg", 0.5, "makarna 500", ["makarna"]),
    ("temel_salca", "Temel İhtiyaç", "Domates Salçası (830 g)", "gida", "kg", 0.83, "domates salçası", ["domates", "salça"]),
    ("temel_siyah_cay", "Temel İhtiyaç", "Dökme Siyah Çay (1 Kg)", "gida", "kg", 1.0, "çay 1 kg", ["çay"]),
    ("temel_tuvalet_kagidi", "Temel İhtiyaç", "Tuvalet Kağıdı (16 Rulo)", "temizlik", "adet", 16.0, "tuvalet kağıdı 16", ["tuvalet"]),
    ("temel_bulasik_deterjani", "Temel İhtiyaç", "Sıvı Bulaşık Deterjanı (650-750 ml)", "temizlik", "litre", 0.7, "sıvı bulaşık deterjanı", ["bulaşık"]),

    # ==================== SÜPERMARKET & MARKALI GIDA ====================
    ("caykur_tiryaki_cay", "Çaykur", "Tiryaki Siyah Çay", "gida", "kg", 1.0, "çaykur tiryaki", ["çaykur", "tiryaki"]),
    ("caykur_rize_turist", "Çaykur", "Rize Turist Siyah Çay", "gida", "kg", 1.0, "çaykur rize turist", ["çaykur", "rize"]),
    ("caykur_altinbas_cay", "Çaykur", "Altınbaş Klasik Siyah Çay", "gida", "kg", 0.5, "çaykur altınbaş", ["çaykur", "altınbaş"]),
    ("caykur_filiz_cay", "Çaykur", "Filiz Siyah Çay", "gida", "kg", 1.0, "çaykur filiz", ["çaykur", "filiz"]),
    ("lipton_yellow_label", "Lipton", "Yellow Label Siyah Çay", "gida", "kg", 1.0, "lipton yellow label", ["lipton", "yellow"]),
    ("dogus_karadeniz_cay", "Doğuş", "Karadeniz Siyah Çay", "gida", "kg", 1.0, "doğuş karadeniz çay", ["doğuş", "çay"]),
    ("ofcay_tiryaki_cay", "Ofçay", "Tiryaki / Hazine Siyah Çay", "gida", "kg", 1.0, "ofçay 1 kg", ["ofçay"]),
    ("sutas_kasar_peyniri", "Sütaş", "Tam Yağlı Taze Kaşar Peyniri", "gida", "kg", 0.6, "sütaş kaşar", ["sütaş", "kaşar"]),
    ("sutas_beyaz_peynir", "Sütaş", "Tam Yağlı Beyaz Peynir", "gida", "kg", 0.5, "sütaş beyaz peynir", ["sütaş", "beyaz", "peynir"]),
    ("sutas_suzme_peynir", "Sütaş", "Süzme Peynir", "gida", "kg", 0.5, "sütaş süzme peynir", ["sütaş", "süzme"]),
    ("sutas_tereyagi", "Sütaş", "Geleneksel Yayık Tereyağı", "gida", "kg", 0.5, "sütaş tereyağı", ["sütaş", "tereyağ"]),
    ("sutas_yogurt", "Sütaş", "Kaymaklı / Sade Yoğurt", "gida", "kg", 1.0, "sütaş yoğurt", ["sütaş", "yoğurt"]),
    ("sutas_sut_1lt", "Sütaş", "Tam Yağlı Uzun Ömürlü Süt", "gida", "litre", 1.0, "sütaş süt 1 l", ["sütaş", "süt"]),
    ("icim_beyaz_peynir", "İçim", "Tam Yağlı Beyaz Peynir", "gida", "kg", 0.5, "içim beyaz peynir", ["içim", "peynir"]),
    ("icim_kasar_peyniri", "İçim", "Tam Yağlı Taze Kaşar Peyniri", "gida", "kg", 0.6, "içim kaşar", ["içim", "kaşar"]),
    ("icim_tereyagi", "İçim", "Tereyağı", "gida", "kg", 0.5, "içim tereyağı", ["içim", "tereyağ"]),
    ("icim_yogurt", "İçim", "Sade / Kaymaklı Yoğurt", "gida", "kg", 1.0, "içim yoğurt", ["içim", "yoğurt"]),
    ("icim_sut_1lt", "İçim", "Tam Yağlı Uzun Ömürlü Süt", "gida", "litre", 1.0, "içim süt 1 l", ["içim", "süt"]),
    ("icim_labne", "İçim", "Labne Peyniri", "gida", "kg", 0.4, "içim labne", ["içim", "labne"]),
    ("pinar_sut_1lt", "Pınar", "Tam Yağlı Uzun Ömürlü Süt", "gida", "litre", 1.0, "pınar süt 1 l", ["pınar", "süt"]),
    ("pinar_beyaz_peynir", "Pınar", "Beyaz / Süzme Peynir", "gida", "kg", 0.5, "pınar beyaz peynir", ["pınar", "peynir"]),
    ("pinar_yogurt", "Pınar", "Sade Yoğurt", "gida", "kg", 1.0, "pınar yoğurt", ["pınar", "yoğurt"]),
    ("pinar_tereyagi", "Pınar", "Tereyağı", "gida", "kg", 0.5, "pınar tereyağı", ["pınar", "tereyağ"]),
    ("pinar_labne", "Pınar", "Labne Peyniri", "gida", "kg", 0.4, "pınar labne", ["pınar", "labne"]),
    ("torku_beyaz_peynir", "Torku", "Tam Yağlı Beyaz Peynir", "gida", "kg", 0.5, "torku beyaz peynir", ["torku", "peynir"]),
    ("torku_sut_1lt", "Torku", "Tam Yağlı Uzun Ömürlü Süt", "gida", "litre", 1.0, "torku süt 1 l", ["torku", "süt"]),
    ("sek_sut_1lt", "Sek", "Tam Yağlı Süt", "gida", "litre", 1.0, "sek süt 1 l", ["sek", "süt"]),
    ("ekici_beyaz_peynir", "Ekici", "Tam Yağlı Beyaz Peynir", "gida", "kg", 0.5, "ekici beyaz peynir", ["ekici", "peynir"]),
    ("ekici_kasar_peyniri", "Ekici", "Tam Yağlı Taze Kaşar Peyniri", "gida", "kg", 0.6, "ekici kaşar", ["ekici", "kaşar"]),
    ("tahsildaroglu_beyaz_peynir", "Tahsildaroğlu", "Klasik Beyaz Peynir", "gida", "kg", 0.5, "tahsildaroğlu peynir", ["tahsildaroğlu", "peynir"]),
    ("marmarabirlik_siyah_zeytin", "Marmarabirlik", "Yağlı Salamura Siyah Zeytin", "gida", "kg", 0.8, "marmarabirlik zeytin", ["marmarabirlik"]),
    ("cem_siyah_zeytin", "Cem", "Sele / Salamura Siyah Zeytin", "gida", "kg", 0.8, "cem siyah zeytin", ["cem", "zeytin"]),
    ("komili_zeytinyagi", "Komili", "Ege Sızma / Riviera Zeytinyağı", "gida", "litre", 1.0, "komili zeytinyağı", ["komili", "zeytinyağ"]),
    ("kristal_zeytinyagi", "Kristal", "Anadolu Sızma Zeytinyağı", "gida", "litre", 1.0, "kristal zeytinyağı", ["kristal", "zeytinyağ"]),
    ("taris_zeytinyagi", "Tariş", "Kuzey Ege Sızma Zeytinyağı", "gida", "litre", 1.0, "tariş zeytinyağı", ["tariş", "zeytinyağ"]),
    ("yudum_aycicek_yagi", "Yudum", "Ayçiçek Yağı", "gida", "litre", 2.0, "yudum ayçiçek yağı", ["yudum", "ayçiçek"]),
    ("orkide_aycicek_yagi", "Orkide", "Ayçiçek Yağı", "gida", "litre", 2.0, "orkide ayçiçek yağı", ["orkide", "ayçiçek"]),
    ("biryag_aycicek_yagi", "Biryağ", "Ayçiçek Yağı", "gida", "litre", 2.0, "biryağ ayçiçek", ["biryağ"]),
    ("mehmet_efendi_turk_kahvesi", "Kurukahveci Mehmet Efendi", "Türk Kahvesi", "gida", "kg", 0.1, "mehmet efendi türk kahvesi", ["mehmet", "efendi"]),
    ("nescafe_gold_kahve", "Nescafé", "Gold Çözünebilir Kahve", "gida", "kg", 0.1, "nescafe gold", ["nescafe", "gold"]),
    ("nescafe_classic_kahve", "Nescafé", "Classic Çözünebilir Kahve", "gida", "kg", 0.1, "nescafe classic", ["nescafe", "classic"]),
    ("jacobs_monarch_kahve", "Jacobs", "Monarch Gold Çözünebilir Kahve", "gida", "kg", 0.1, "jacobs monarch", ["jacobs", "monarch"]),
    ("nutella_findik_kremasi", "Nutella", "Kakaolu Fındık Kreması", "gida", "kg", 0.4, "nutella", ["nutella"]),
    ("torku_banada", "Torku", "Banada Kakaolu Fındık Kreması", "gida", "kg", 0.4, "torku banada", ["torku", "banada"]),
    ("filiz_makarna", "Filiz", "Durum Buğdayı Makarna", "gida", "kg", 0.5, "filiz makarna", ["filiz"]),
    ("barilla_makarna", "Barilla", "Spagetti / Kalem Makarna", "gida", "kg", 0.5, "barilla makarna", ["barilla"]),
    ("pastavilla_makarna", "Pastavilla", "Durum Buğdayı Makarna", "gida", "kg", 0.5, "pastavilla makarna", ["pastavilla"]),
    ("reis_osmancik_pirinc", "Reis", "Osmancık / Baldo Pirinç", "gida", "kg", 1.0, "reis pirinç", ["reis", "pirinç"]),
    ("yayla_pirinc", "Yayla", "Osmancık / Baldo Pirinç", "gida", "kg", 1.0, "yayla pirinç", ["yayla", "pirinç"]),
    ("yayla_kirmizi_mercimek", "Yayla", "Kırmızı Mercimek", "gida", "kg", 1.0, "yayla kırmızı mercimek", ["yayla", "mercimek"]),
    ("reis_kirmizi_mercimek", "Reis", "Kırmızı Mercimek", "gida", "kg", 1.0, "reis mercimek", ["reis", "mercimek"]),
    ("yayla_nohut", "Yayla", "Koçbaşı Nohut", "gida", "kg", 1.0, "yayla nohut", ["yayla", "nohut"]),
    ("tat_domates_salcasi", "Tat", "Domates Salçası", "gida", "kg", 0.83, "tat domates salçası", ["tat", "salça"]),
    ("oncu_salca", "Öncü", "Domates / Biber Salçası", "gida", "kg", 0.83, "öncü salça", ["öncü", "salça"]),
    ("tukas_salca", "Tukaş", "Domates Salçası", "gida", "kg", 0.83, "tukaş salça", ["tukaş", "salça"]),
    ("turkseker_toz_seker_5kg", "Türkşeker", "Kristal Toz Şeker (5 Kg)", "gida", "kg", 5.0, "türkşeker toz şeker", ["türkşeker", "şeker"]),
    ("balkupu_toz_seker", "Balküpü", "Kristal Toz Şeker", "gida", "kg", 3.0, "balküpü toz şeker", ["balküpü", "şeker"]),
    ("dogus_toz_seker", "Doğuş", "Kristal Toz Şeker", "gida", "kg", 5.0, "doğuş toz şeker", ["doğuş", "şeker"]),
    ("torku_toz_seker", "Torku", "Kristal Toz Şeker", "gida", "kg", 3.0, "torku toz şeker", ["torku", "şeker"]),
    ("soke_geleneksel_un", "Söke", "Geleneksel Buğday Unu", "gida", "kg", 2.0, "söke un", ["söke", "un"]),
    ("sinangil_un", "Sinangil", "Çok Amaçlı Buğday Unu", "gida", "kg", 2.0, "sinangil un", ["sinangil", "un"]),

    # ==================== TEMİZLİK & DETERJAN (18 ÜRÜN) ====================
    ("fairy_platinum_plus", "Fairy", "Platinum Plus Bulaşık Makinesi Kapsülü", "temizlik", "yıkama", 30.0, "fairy platinum", ["fairy", "platinum"]),
    ("fairy_sivi_bulasik", "Fairy", "Limonlu Sıvı Bulaşık Deterjanı", "temizlik", "litre", 0.65, "fairy sıvı bulaşık", ["fairy", "bulaşık"]),
    ("finish_quantum_kapsul", "Finish", "Quantum Bulaşık Makinesi Kapsülü", "temizlik", "yıkama", 30.0, "finish quantum", ["finish", "quantum"]),
    ("pril_sivi_bulasik", "Pril", "Güçlü Yağ Çözücü Sıvı Bulaşık Deterjanı", "temizlik", "litre", 0.675, "pril bulaşık", ["pril"]),
    ("persil_jel_deterjan", "Persil", "Güç Jel Sıvı Çamaşır Deterjanı", "temizlik", "yıkama", 33.0, "persil jel", ["persil"]),
    ("ariel_toz_deterjan", "Ariel", "Dağ Esintisi Toz Çamaşır Deterjanı", "temizlik", "kg", 4.0, "ariel toz", ["ariel"]),
    ("omo_sivi_deterjan", "Omo", "Active Fresh Sıvı Çamaşır Deterjanı", "temizlik", "litre", 1.69, "omo sıvı", ["omo"]),
    ("perwoll_sivi_deterjan", "Perwoll", "Yenilenen Siyahlar & Renkliler Sıvı Deterjan", "temizlik", "litre", 1.98, "perwoll sıvı", ["perwoll"]),
    ("yumos_ekstra_yumusatici", "Yumoş", "Ekstra Konsantre Çamaşır Yumuşatıcısı", "temizlik", "litre", 1.44, "yumoş ekstra", ["yumoş"]),
    ("vernel_max_yumusatici", "Vernel", "Max Konsantre Çamaşır Yumuşatıcısı", "temizlik", "litre", 1.44, "vernel max", ["vernel"]),
    ("domestos_camasir_suyu", "Domestos", "Yoğun Kıvamlı Çamaşır Suyu", "temizlik", "litre", 0.75, "domestos çamaşır suyu", ["domestos"]),
    ("cif_krem_temizleyici", "Cif", "Amonyaklı / Limonlu Krem Temizleyici", "temizlik", "litre", 0.75, "cif krem", ["cif", "krem"]),
    ("asperox_sari_guc", "Asperox", "Sarı Güç Ultra Yağ Çözücü Sprey", "temizlik", "litre", 1.0, "asperox sarı güç", ["asperox"]),
    ("selpak_tuvalet_kagidi", "Selpak", "3 Katlı Tuvalet Kağıdı", "temizlik", "adet", 16.0, "selpak tuvalet kağıdı", ["selpak", "tuvalet"]),
    ("solo_tuvalet_kagidi", "Solo", "Akıllı Seçim Tuvalet Kağıdı", "temizlik", "adet", 16.0, "solo tuvalet kağıdı", ["solo", "tuvalet"]),
    ("familia_tuvalet_kagidi", "Familia", "Plus Doğal Tuvalet Kağıdı", "temizlik", "adet", 16.0, "familia tuvalet kağıdı", ["familia", "tuvalet"]),
    ("papia_tuvalet_kagidi", "Papia", "BioCare 3 Katlı Tuvalet Kağıdı", "temizlik", "adet", 16.0, "papia tuvalet kağıdı", ["papia", "tuvalet"]),
    ("solo_kagit_havlu", "Solo", "Bambu Katkılı Kağıt Havlu", "temizlik", "adet", 8.0, "solo kağıt havlu", ["solo", "havlu"]),

    # ==================== ANNE & BEBEK (9 ÜRÜN) ====================
    ("prima_premium_care_4", "Prima", "Premium Care / Aktif Bebek Bezi", "anne_bebek", "adet", 30.0, "prima bebek bezi", ["prima"]),
    ("sleepy_natural_bez", "Sleepy", "Natural / Bio Natural Bebek Bezi", "anne_bebek", "adet", 30.0, "sleepy bebek bezi", ["sleepy", "bez"]),
    ("molfix_bebek_bezi", "Molfix", "3D Elastik Bebek Bezi", "anne_bebek", "adet", 30.0, "molfix bebek bezi", ["molfix"]),
    ("canbebe_bebek_bezi", "Canbebe", "Süper Konfor Bebek Bezi", "anne_bebek", "adet", 30.0, "canbebe bebek bezi", ["canbebe"]),
    ("uni_baby_islak_mendil", "Uni Baby", "Yenidoğan / Hassas Islak Mendil", "anne_bebek", "adet", 90.0, "uni baby ıslak mendil", ["uni", "mendil"]),
    ("sleepy_islak_mendil", "Sleepy", "Sensitive Islak Havlu Mendil", "anne_bebek", "adet", 90.0, "sleepy ıslak mendil", ["sleepy", "mendil"]),
    ("aptamil_devam_sutu", "Aptamil", "Çocuk Devam Sütü Maması", "anne_bebek", "kg", 0.35, "aptamil devam sütü", ["aptamil"]),
    ("bebelac_devam_sutu", "Bebelac", "Gold Çocuk Devam Sütü", "anne_bebek", "kg", 0.35, "bebelac devam sütü", ["bebelac"]),
    ("dalin_bebek_sampuani", "Dalin", "Klasik Göz Yakmayan Bebek Şampuanı", "anne_bebek", "litre", 0.5, "dalin şampuan", ["dalin", "şampuan"]),

    # ==================== KİŞİSEL BAKIM (13 ÜRÜN) ====================
    ("elidor_sampuan", "Elidor", "Superblend Saç Bakım Şampuanı", "kisisel_bakim", "litre", 0.4, "elidor şampuan", ["elidor", "şampuan"]),
    ("clear_men_sampuan", "Clear", "Men Kepeğe Karşı Etkili Şampuan", "kisisel_bakim", "litre", 0.35, "clear şampuan", ["clear", "şampuan"]),
    ("head_shoulders_sampuan", "Head & Shoulders", "Klasik Bakım Kepek Şampuanı", "kisisel_bakim", "litre", 0.35, "head shoulders şampuan", ["shoulders"]),
    ("pantene_sampuan", "Pantene", "Pro-V Onarıcı Bakım Şampuanı", "kisisel_bakim", "litre", 0.4, "pantene şampuan", ["pantene", "şampuan"]),
    ("elseve_sampuan", "L'Oréal Elseve", "Komple Onarıcı Saç Şampuanı", "kisisel_bakim", "litre", 0.36, "elseve şampuan", ["elseve", "şampuan"]),
    ("dove_dus_jeli", "Dove", "Nemlendirici Bakım Duş Jeli", "kisisel_bakim", "litre", 0.45, "dove duş jeli", ["dove", "duş"]),
    ("palmolive_dus_jeli", "Palmolive", "Aroma Sensations Duş Jeli", "kisisel_bakim", "litre", 0.5, "palmolive duş jeli", ["palmolive", "duş"]),
    ("activex_sivi_sabun", "Activex", "Antibakteriyel Sıvı Sabun", "kisisel_bakim", "litre", 0.5, "activex sıvı sabun", ["activex", "sabun"]),
    ("sensodyne_dis_macunu", "Sensodyne", "Hassas Dişler İçin Diş Macunu", "kisisel_bakim", "litre", 0.075, "sensodyne diş macunu", ["sensodyne"]),
    ("colgate_dis_macunu", "Colgate", "Total / Üçlü Etki Diş Macunu", "kisisel_bakim", "litre", 0.075, "colgate diş macunu", ["colgate", "macun"]),
    ("parodontax_dis_macunu", "Parodontax", "Diş Eti Bakımı Diş Macunu", "kisisel_bakim", "litre", 0.075, "parodontax diş macunu", ["parodontax"]),
    ("orkid_platinum_ped", "Orkid", "Platinum Hijyenik Ped", "kisisel_bakim", "adet", 16.0, "orkid platinum", ["orkid", "platinum"]),
    ("nivea_deodorant", "Nivea", "Black & White Invisible Deodorant", "kisisel_bakim", "litre", 0.15, "nivea deodorant", ["nivea", "deodorant"]),
]

CURATED_TECH_PRODUCTS = [
    {
        "product_key": "dyson_v15_detect",
        "brand": "Dyson",
        "family_name": "V15 Detect Absolute Kablosuz Süpürge",
        "model_code": "V15-DETECT",
        "offers": [
            ("amazon", "Amazon.com.tr", "Dyson V15 Detect Absolute • Peşin Fiyatına 6 Taksit", "https://www.amazon.com.tr/s?k=Dyson+V15+Detect+Absolute", 28999.0, 6),
            ("hepsiburada", "Hepsiburada Resmi", "Dyson V15 Detect Türkiye Garantili", "https://www.hepsiburada.com/ara?q=Dyson+V15+Detect+Absolute", 29499.0, 3),
            ("mediamarkt", "MediaMarkt", "Dyson V15 Detect Kablosuz Süpürge", "https://www.mediamarkt.com.tr/tr/search.html?query=Dyson+V15+Detect", 30999.0, 1),
        ],
    },
    {
        "product_key": "philips_airfryer_xxl",
        "brand": "Philips",
        "family_name": "Airfryer XXL Connected 7.2L Fritöz",
        "model_code": "HD9285/96",
        "offers": [
            ("amazon", "Amazon.com.tr", "Philips Airfryer XXL HD9285/96 • Peşin Fiyatına 9 Taksit", "https://www.amazon.com.tr/s?k=Philips+Airfryer+XXL+HD9285", 6799.0, 9),
            ("hepsiburada", "Hepsiburada Resmi", "Philips Airfryer XXL 7.2 Litre", "https://www.hepsiburada.com/ara?q=Philips+Airfryer+XXL+HD9285", 7199.0, 3),
            ("vatan", "Vatan Bilgisayar", "Philips HD9285/96 Airfryer XXL", "https://www.vatanbilgisayar.com/arama/HD9285/", 7499.0, 1),
        ],
    },
    {
        "product_key": "roborock_q8_max",
        "brand": "Roborock",
        "family_name": "Q8 Max Akıllı Çift Fırçalı Robot Süpürge",
        "model_code": "Q8-MAX",
        "offers": [
            ("amazon", "Amazon.com.tr", "Roborock Q8 Max 5500Pa • Peşin Fiyatına 6 Taksit", "https://www.amazon.com.tr/s?k=Roborock+Q8+Max", 16499.0, 6),
            ("hepsiburada", "Hepsiburada Resmi", "Roborock Q8 Max Türkiye Garantili", "https://www.hepsiburada.com/ara?q=Roborock+Q8+Max", 16999.0, 3),
            ("mediamarkt", "MediaMarkt", "Roborock Q8 Max Robot Süpürge", "https://www.mediamarkt.com.tr/tr/search.html?query=Roborock+Q8+Max", 17999.0, 1),
        ],
    },
    {
        "product_key": "karaca_hatir_hups",
        "brand": "Karaca",
        "family_name": "Hatır Hüps Sütlü Türk Kahve Makinesi",
        "model_code": "HATIR-HUPS",
        "offers": [
            ("amazon", "Amazon.com.tr", "Karaca Hatır Hüps Kahve Makinesi • Peşin Fiyatına 6 Taksit", "https://www.amazon.com.tr/s?k=Karaca+Hat%C4%B1r+H%C3%BCps", 1899.0, 6),
            ("hepsiburada", "Hepsiburada Resmi", "Karaca Hatır Hüps Sütlü Türk Kahve Makinesi", "https://www.hepsiburada.com/ara?q=Karaca+Hat%C4%B1r+H%C3%BCps", 1999.0, 3),
        ],
    },
]

GLOBAL_SUBVARIANT_EXCLUSIONS = [
    "kido", "kakaolu sut", "muzlu", "cilekli", "protein", "mayo", "seyahat", "cep boy", "deneme",
    "bildircin", "hupper", "meyveli", "kayisi", "seftali", "orman meyve", "keci", "koyun",
    "ezmesi", "bardak poset", "demlik poset", "bitki cayi", "yesil cay", "laktozsuz", "organik",
    "ayran", "kefir", "puding", "krema"
]


def _slugify_tr(text: str) -> str:
    s = _norm_tr(text)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "urun"


def extract_direct_url_from_image(raw_market: str, title: str, image_url: str) -> str:
    """marketfiyati görsel URL'sindeki resmi ürün kodundan CarrefourSA ve A101 Kapıda doğrudan ürün sayfasını üretir."""
    if not image_url:
        return ""
    if raw_market == "carrefour":
        m = re.search(r"/carrefour(?:images)?/(\d+)\.", image_url)
        if m:
            return f"https://www.carrefoursa.com/{_slugify_tr(title)}-p-{m.group(1)}"
    elif raw_market == "a101":
        m = re.search(r"/a101(?:images)?/(\d+)\.", image_url)
        if m:
            return f"https://www.a101.com.tr/kapida/u/{_slugify_tr(title)}_p-{m.group(1)}"
    return ""


_SOK_SLUG_CACHE: Dict[str, str] = {}


def resolve_sok_direct_product(title: str, must_tokens: Optional[List[str]] = None) -> str:
    """ŞOK web aramasından doğrudan ürün detay linkini (/...-p-1234) bulur."""
    key = _norm_tr(title)
    if key in _SOK_SLUG_CACHE:
        return _SOK_SLUG_CACHE[key]
    try:
        url = f"https://www.sokmarket.com.tr/arama?q={urllib.parse.quote_plus(title)}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=2.8) as resp:
            html_txt = resp.read().decode("utf-8", errors="ignore")
        links = re.findall(r'href="(/[^"]+-p-\d+)"', html_txt)
        req_words = [w for w in (must_tokens or []) if len(w) >= 3] + [w for w in re.split(r"\s+", key) if len(w) >= 3][:1]
        for lnk in links:
            lnk_norm = lnk.lower()
            if all(w in lnk_norm for w in req_words):
                full = f"https://www.sokmarket.com.tr{lnk}"
                _SOK_SLUG_CACHE[key] = full
                return full
    except Exception:
        pass
    _SOK_SLUG_CACHE[key] = ""
    return ""


def _norm_tr(text: str) -> str:
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
    )


def _title_matches_must_tokens(t_norm: str, must_tokens: List[str]) -> bool:
    """Kelime sınırı duyarlı token eşleştirme ('süt' -> 'sütaş' veya 'un' -> 'uzun' çakışmasını önler)."""
    words = re.findall(r"[a-z0-9]+", t_norm)
    for tok in must_tokens:
        if not tok:
            continue
        if " " in tok:
            if tok not in t_norm:
                return False
            continue
        if tok == "sut":
            if not any(w in ("sut", "sutu", "sutlu", "sutler") for w in words):
                return False
        elif tok == "cay":
            if not any(w in ("cay", "cayi", "caylar") for w in words):
                return False
        elif tok == "un":
            if not any(w in ("un", "unu", "unlar") for w in words):
                return False
        else:
            if not any(w.startswith(tok) for w in words):
                return False
    return True


def _parse_net_amount(raw_qty: str, title: str, base_unit: str, fallback: float) -> float:
    combined = f"{raw_qty or ''} {title or ''}".lower().replace(",", ".")
    # Çoklu paket ifadesi kontrolü (örn: 3x60, 4x1L, 2x50)
    m_mult = re.search(r"(\d+)\s*[x\*]\s*(\d+(?:\.\d+)?)\s*(kg|gr|g|lt|litre|l|ml|adet|yaprak|rulo|kapsül|tablet)?\b", combined)
    if m_mult:
        cnt = float(m_mult.group(1))
        val = float(m_mult.group(2))
        u = (m_mult.group(3) or "").strip()
        total = cnt * val
        if base_unit == "kg" and u in ("gr", "g"):
            return round(total / 1000.0, 3)
        if base_unit == "litre" and u == "ml":
            return round(total / 1000.0, 3)
        if total > 0:
            return round(total, 3)

    if base_unit == "kg":
        m_kg = re.search(r"(\d+(?:\.\d+)?)\s*kg\b", combined)
        if m_kg:
            return float(m_kg.group(1))
        m_gr = re.search(r"(\d+(?:\.\d+)?)\s*(?:gr|g)\b", combined)
        if m_gr:
            val = float(m_gr.group(1))
            return round(val / 1000.0, 3) if val >= 15 else val
        if re.search(r"(\d+(?:\.\d+)?)\s*(?:lt|litre|ml)\b", combined):
            return 0.0
    elif base_unit == "litre":
        m_lt = re.search(r"(\d+(?:\.\d+)?)\s*(?:lt|litre|l)\b", combined)
        if m_lt:
            return float(m_lt.group(1))
        m_ml = re.search(r"(\d+(?:\.\d+)?)\s*ml\b", combined)
        if m_ml:
            return round(float(m_ml.group(1)) / 1000.0, 3)
        if re.search(r"(\d+(?:\.\d+)?)\s*(?:kg|gr|g)\b", combined):
            return 0.0
    else:
        m_cnt = re.search(r"(\d+)\s*(?:yıkama|yikama|kapsül|kapsul|tablet|adet|rulo|ped|li|lü|lu|lı)\b", combined)
        if m_cnt:
            return float(m_cnt.group(1))
    return fallback


def fetch_marketfiyati_items(query: str, size: int = 12) -> List[Dict]:
    payload = json.dumps({"keywords": query, "pages": 0, "size": size}).encode("utf-8")
    req = urllib.request.Request(
        MARKET_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; DipciPriceRadar/1.0; +https://dipci.tr/kurumsal)",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = json.loads(resp.read().decode("utf-8", errors="replace"))
        return body.get("content") or []


def resolve_migros_direct_product(query: str, must_tokens: List[str]) -> Tuple[str, str]:
    """Migros JSON arama uç noktasından doğrudan ürün sayfası URL'sini (-p-...) ve yüksek çözünürlüklü görseli bulur."""
    try:
        url = f"{MIGROS_SEARCH_API}{urllib.parse.quote_plus(query)}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json", "X-PWA": "true"},
        )
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        infos = (((data.get("data") or {}).get("searchInfo") or {}).get("storeProductInfos")) or []
        for info in infos:
            name_norm = _norm_tr(str(info.get("name") or ""))
            pretty = str(info.get("prettyName") or "").strip()
            if pretty and _title_matches_must_tokens(name_norm, must_tokens):
                imgs = info.get("images") or []
                img_u = ""
                if imgs and isinstance(imgs[0], dict):
                    urls_d = imgs[0].get("urls") or {}
                    img_u = str(urls_d.get("PRODUCT_DETAIL") or urls_d.get("PRODUCT_LIST") or "")
                return f"https://www.migros.com.tr/{pretty}", img_u
    except Exception:
        pass
    return "", ""


def _fetch_single_spec(spec_tuple) -> Tuple[Tuple, List[Dict], str, str]:
    pkey, brand, fam, cat, base_u, ref_amt, query, must_list = spec_tuple
    must_tokens = [_norm_tr(t) for t in must_list]
    items = []
    try:
        fetch_sz = 32 if pkey.startswith("temel_") else 14
        items = fetch_marketfiyati_items(query, size=fetch_sz)
    except Exception:
        pass
    mig_url, mig_img = resolve_migros_direct_product(query, must_tokens)
    return spec_tuple, items, mig_url, mig_img


def fix_legacy_seed_urls(db: PriceMemoryDB) -> None:
    """Eski örnek (404 dönen) URL kayıtlarını gerçek çalışan mağaza linkleriyle günceller."""
    replacements = [
        ("https://www.amazon.com.tr/dp/B09FAIRY100", "https://www.amazon.com.tr/s?k=Fairy+Platinum+Plus+Bula%C5%9F%C4%B1k+Makinesi+Kaps%C3%BCl%C3%BC"),
        ("https://www.amazon.com.tr/dp/B0C8PHILIPS", "https://www.amazon.com.tr/s?k=Philips+XC8057%2F01+Aqua+Plus"),
        ("https://www.amazon.com.tr/dp/B09PRIMA108", "https://www.amazon.com.tr/s?k=Prima+Premium+Care+4+Beden"),
        ("https://www.hepsiburada.com/fairy-75", "https://www.hepsiburada.com/ara?q=Fairy+Platinum+Plus+75"),
        ("https://www.hepsiburada.com/fairy-150", "https://www.hepsiburada.com/ara?q=Fairy+Platinum+Plus+150"),
        ("https://www.hepsiburada.com/prima-4-108", "https://www.hepsiburada.com/ara?q=Prima+Premium+Care+4+Beden+108"),
        ("https://www.migros.com.tr/fairy-50", "https://www.migros.com.tr/fairy-platinum-50li-745-g-p-1d8b7dc"),
        ("https://www.migros.com.tr/sutas-kasar-700", "https://www.migros.com.tr/arama?q=S%C3%BCta%C5%9F+Taze+Ka%C5%9Far"),
        ("https://www.vatanbilgisayar.com/philips-xc8057", "https://www.vatanbilgisayar.com/arama/XC8057/"),
        ("https://www.mediamarkt.com.tr/philips-xc8057", "https://www.mediamarkt.com.tr/tr/search.html?query=Philips+XC8057"),
        ("https://www.carrefoursa.com/sutas-kasar-850", "https://www.carrefoursa.com/search/?text=S%C3%BCta%C5%9F+Taze+Ka%C5%9Far"),
        ("https://www.carrefoursa.com/sutas-kasar", "https://www.carrefoursa.com/search/?text=S%C3%BCta%C5%9F+Taze+Ka%C5%9Far"),
        ("https://www.trendyol.com/fairy-100", "https://www.trendyol.com/sr?q=Fairy+Platinum+Plus"),
        ("https://www.bim.com.tr/", "https://www.google.com/maps/search/B%C4%B0M+Market"),
    ]
    for old_u, new_u in replacements:
        db.conn.execute("UPDATE price_snapshots SET url = ? WHERE url = ?", (new_u, old_u))
        db.conn.execute("UPDATE community_deals SET raw_url = ? WHERE raw_url = ?", (new_u, old_u))
        db.conn.execute(
            "UPDATE community_deals SET tracked_affiliate_url = REPLACE(tracked_affiliate_url, ?, ?) WHERE tracked_affiliate_url LIKE ?",
            (old_u, new_u, f"%{old_u}%"),
        )
    db.conn.execute(
        "UPDATE price_snapshots SET url = REPLACE(url, 'https://www.a101.com.tr/list/?search_text=', 'https://www.a101.com.tr/kapida/arama?q=') "
        "WHERE url LIKE 'https://www.a101.com.tr/list/?search_text=%'"
    )
    db.conn.execute("DELETE FROM price_snapshots WHERE url LIKE 'https://dipci.tr%'")
    db.conn.commit()


def ensure_product_barcode(db: PriceMemoryDB, product_key: str, label: str, net_amount: float) -> None:
    has_bc = db.conn.execute(
        "SELECT gtin FROM product_barcodes WHERE product_key = ? LIMIT 1", (product_key,)
    ).fetchone()
    if has_bc:
        return
    digest_digits = "".join(str(int(ch, 16) % 10) for ch in hashlib.sha256(product_key.encode("utf-8")).hexdigest()[:9])
    ean13 = compute_ean13_with_check(f"869{digest_digits}")
    db.register_barcode(ean13, product_key, label, net_amount, "SINGLE")


def sync_curated_tech(db: PriceMemoryDB, now: datetime) -> int:
    written = 0
    today_prefix = now.strftime("%Y-%m-%d")
    for tech in CURATED_TECH_PRODUCTS:
        pkey = tech["product_key"]
        db.register_product(
            product_key=pkey,
            brand=tech["brand"],
            family_name=tech["family_name"],
            category="teknoloji_beyaz_esya",
            base_unit="adet",
            reference_net_amount=1.0,
            official_model_code=tech["model_code"],
        )
        ensure_product_barcode(db, pkey, f"{tech['model_code']} Kutu", 1.0)

        hist = db.get_historical_stats(pkey, now=now)
        if hist.sample_count == 0:
            med_p = statistics.median([o[4] for o in tech["offers"]])
            for d_back in (45, 21, 5):
                db.record_snapshot(
                    product_key=pkey,
                    platform="mediamarkt",
                    seller_name="Piyasa Referansı",
                    title=f"{tech['brand']} {tech['family_name']}",
                    url=tech["offers"][0][3],
                    sale_price=med_p,
                    effective_pack_price=med_p,
                    effective_unit_price=med_p,
                    net_amount=1.0,
                    captured_at=now - timedelta(days=d_back),
                )

        db.conn.execute(
            "DELETE FROM price_snapshots WHERE product_key = ? AND substr(captured_at, 1, 10) = ?",
            (pkey, today_prefix),
        )
        for plat, seller, title, url, price, months in tech["offers"]:
            db.record_snapshot(
                product_key=pkey,
                platform=plat,
                seller_name=seller,
                title=title,
                url=url,
                sale_price=price,
                effective_pack_price=price,
                effective_unit_price=price,
                net_amount=1.0,
                captured_at=now,
                zero_interest_months=months,
                model_code_seen=tech["model_code"],
            )
            written += 1
    return written


def sync_live_supermarkets(db: PriceMemoryDB) -> Tuple[int, List[str]]:
    snapshots_written = 0
    touched_urls: List[str] = []
    now = datetime.now()
    today_prefix = now.strftime("%Y-%m-%d")

    fix_legacy_seed_urls(db)
    snapshots_written += sync_curated_tech(db, now)

    existing_keys = {
        r["product_key"]
        for r in db.conn.execute("SELECT product_key FROM canonical_products").fetchall()
    }

    fetched_results = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_fetch_single_spec, s) for s in COMPACT_SUPERMARKET_SPECS]
        for fut in as_completed(futures):
            try:
                fetched_results.append(fut.result())
            except Exception as e:
                print(f"[WARN] Paralel çekim hatası: {e}")

    exclude_tokens = [_norm_tr(t) for t in GLOBAL_SUBVARIANT_EXCLUSIONS]
    market_seller_labels = [
        v[1] for v in MARKET_META.values()
    ] + ["BİM Mağaza", "Tarım Kredi Kooperatif", "Hakmar Express"]

    for spec_tuple, items, mig_direct_url, mig_img in fetched_results:
        pkey, brand, fam, cat, base_u, ref_amt, query, must_list = spec_tuple
        if pkey not in existing_keys:
            db.register_product(
                product_key=pkey,
                brand=brand,
                family_name=fam,
                category=cat,
                base_unit=base_u,
                reference_net_amount=ref_amt,
                image_url=mig_img,
            )
            existing_keys.add(pkey)

        ensure_product_barcode(db, pkey, f"{ref_amt:g} {base_u} Standart Paket", ref_amt)

        must_tokens = [_norm_tr(t) for t in must_list]
        best_per_market: Dict[str, Dict] = {}
        found_img = mig_img or ""

        for item in items:
            title = str(item.get("title") or "").strip()
            t_norm = _norm_tr(title)
            if not _title_matches_must_tokens(t_norm, must_tokens):
                continue
            if any(ex in t_norm for ex in exclude_tokens):
                continue

            img_candidate = str(item.get("imageUrl") or "").strip()
            if img_candidate.startswith("http") and not found_img:
                found_img = img_candidate

            raw_qty = str(item.get("refinedVolumeOrWeight") or item.get("refinedQuantityUnit") or "")
            net_amt = _parse_net_amount(raw_qty, title, base_u, ref_amt)
            max_pack_mult = 1.65 if cat == "gida" else 3.6
            min_pack_mult = 0.50 if pkey.startswith("temel_") else 0.35
            if net_amt <= 0 or net_amt < ref_amt * min_pack_mult or net_amt > ref_amt * max_pack_mult:
                continue

            for depot in item.get("productDepotInfoList") or []:
                raw_m = str(depot.get("marketAdi") or "").strip().lower()
                price = float(depot.get("price") or 0)
                if not raw_m or price <= 0:
                    continue

                unit_val = round(price / net_amt, 2)
                direct_from_img = extract_direct_url_from_image(raw_m, title, img_candidate)
                prev = best_per_market.get(raw_m)
                if prev is None or unit_val < prev["unit_val"]:
                    best_per_market[raw_m] = {
                        "title": title,
                        "price": price,
                        "net_amt": net_amt,
                        "unit_val": unit_val,
                        "direct_url": direct_from_img,
                    }

        if len(best_per_market) >= 3:
            med_u = statistics.median([v["unit_val"] for v in best_per_market.values()])
            best_per_market = {
                k: v
                for k, v in best_per_market.items()
                if (med_u * 0.45) <= v["unit_val"] <= (med_u * 1.85)
            }

        if found_img:
            db.set_product_image(pkey, found_img)

        # Mevcut veritabanında aynı ürün+mağaza için zaten doğrudan ürün linki (-p- / _p-) varsa koru (AB IP coğrafi engel koruması)
        existing_direct_urls: Dict[str, Tuple[str, str]] = {}
        for er in db.conn.execute(
            "SELECT platform, title, url FROM price_snapshots WHERE product_key = ? AND (url LIKE '%-p-%' OR url LIKE '%_p-%') ORDER BY id DESC",
            (pkey,),
        ).fetchall():
            if er["platform"] not in existing_direct_urls and _title_matches_must_tokens(_norm_tr(er["title"]), must_tokens):
                existing_direct_urls[er["platform"]] = (er["title"], er["url"])

        q_marks = ",".join("?" for _ in market_seller_labels)
        db.conn.execute(
            f"DELETE FROM price_snapshots WHERE product_key = ? AND substr(captured_at, 1, 10) = ? AND seller_name IN ({q_marks})",
            [pkey, today_prefix, *market_seller_labels],
        )
        # Geçmiş günlerde kelime-alt-dizesi çakışmasıyla ('süt' -> 'sütaş' vb.) girmiş hatalı başlıkları temizle
        bad_ids = [
            r["id"]
            for r in db.conn.execute(
                "SELECT id, title FROM price_snapshots WHERE product_key = ? AND seller_name != 'Piyasa Medyan Referansı'",
                (pkey,),
            ).fetchall()
            if not _title_matches_must_tokens(_norm_tr(r["title"]), must_tokens)
        ]
        if bad_ids:
            bm = ",".join("?" for _ in bad_ids)
            db.conn.execute(f"DELETE FROM price_snapshots WHERE id IN ({bm})", bad_ids)

        if not best_per_market:
            continue

        hist = db.get_historical_stats(pkey, now=now)
        if hist.sample_count == 0 and len(best_per_market) >= 2:
            med_unit = round(statistics.median([v["unit_val"] for v in best_per_market.values()]), 2)
            med_pack = round(med_unit * ref_amt, 2)
            for days_back in (35, 18, 7):
                db.record_snapshot(
                    product_key=pkey,
                    platform="migros",
                    seller_name="Piyasa Medyan Referansı",
                    title=f"{brand} {fam}",
                    url=mig_direct_url or f"https://www.migros.com.tr/arama?q={urllib.parse.quote_plus(query)}",
                    sale_price=med_pack,
                    effective_pack_price=med_pack,
                    effective_unit_price=med_unit,
                    net_amount=ref_amt,
                    captured_at=now - timedelta(days=days_back),
                    seller_type="OFFICIAL",
                    seller_rating=9.5,
                )

        for raw_m, offer in best_per_market.items():
            plat_key, seller_label, search_prefix = MARKET_META.get(
                raw_m, (raw_m, raw_m.upper(), "https://www.google.com/search?q=")
            )
            if offer.get("direct_url"):
                store_url = offer["direct_url"]
            elif plat_key == "migros":
                exact_mig, _ = resolve_migros_direct_product(offer["title"], must_tokens)
                cached_u = existing_direct_urls.get("migros", ("", ""))[1]
                store_url = exact_mig or mig_direct_url or cached_u or f"{search_prefix}{urllib.parse.quote_plus(offer['title'])}"
            elif plat_key == "sok":
                sok_direct = resolve_sok_direct_product(offer["title"], must_tokens)
                cached_u = existing_direct_urls.get("sok", ("", ""))[1]
                store_url = sok_direct or cached_u or f"{search_prefix}{urllib.parse.quote_plus(offer['title'])}"
            elif search_prefix.endswith("="):
                store_url = f"{search_prefix}{urllib.parse.quote_plus(offer['title'])}"
            else:
                store_url = search_prefix

            db.record_snapshot(
                product_key=pkey,
                platform=plat_key,
                seller_name=seller_label,
                title=offer["title"],
                url=store_url,
                sale_price=offer["price"],
                effective_pack_price=offer["price"],
                effective_unit_price=offer["unit_val"],
                net_amount=offer["net_amt"],
                captured_at=now,
                seller_type="OFFICIAL",
                seller_rating=9.6,
                pack_tier="MULTI_BULK" if offer["net_amt"] > ref_amt * 1.6 else "SINGLE",
            )
            snapshots_written += 1

        touched_urls.append(f"{CANONICAL_ORIGIN}/urun/{key_to_slug(pkey)}")

    db.conn.commit()
    return snapshots_written, touched_urls


def sync_approved_partner_xmls(db: PriceMemoryDB) -> int:
    from auto_catalog import AutoCatalogEngine

    rows = db.conn.execute(
        "SELECT store_name, xml_url FROM partner_xml_feeds WHERE status = 'APPROVED'"
    ).fetchall()
    written = 0
    catalog_engine = AutoCatalogEngine(db)
    for row in rows:
        try:
            req = urllib.request.Request(row["xml_url"], headers={"User-Agent": "DipciBot/1.0 (+https://dipci.tr)"})
            with urllib.request.urlopen(req, timeout=25) as resp:
                xml_bytes = resp.read(50_000_000)  # 50 MB üst sınır
            cnt, _ = catalog_engine.ingest_partner_xml(xml_bytes, row["store_name"])
            written += cnt
        except Exception as e:
            print(f"[WARN] XML besleme hatası ({row['store_name']}): {e}")
    return written


def ping_indexnow(urls: List[str]) -> Optional[int]:
    all_urls = list(
        dict.fromkeys(
            [
                f"{CANONICAL_ORIGIN}/",
                f"{CANONICAL_ORIGIN}/firsatlar",
                f"{CANONICAL_ORIGIN}/kurumsal",
                *urls[:100],
            ]
        )
    )
    payload = json.dumps(
        {
            "host": "dipci.tr",
            "key": INDEXNOW_KEY,
            "keyLocation": f"{CANONICAL_ORIGIN}/{INDEXNOW_KEY}.txt",
            "urlList": all_urls,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://api.indexnow.org/indexnow",
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status
    except Exception as e:
        print(f"[WARN] IndexNow ping uyarısı: {e}")
        return None


def run_sync() -> None:
    from auto_catalog import AutoCatalogEngine, ONLINE_MARKETPLACE_SEEDS
    from fast_radar import FastPriceRadar

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = PriceMemoryDB(DB_PATH)
    m_count, touched = sync_live_supermarkets(db)
    x_count = sync_approved_partner_xmls(db)

    # Her 4 saatlik zamanlayıcıda geniş market kataloğunu (çok sayfalı) ve döngüsel online ürünleri tazele
    auto_engine = AutoCatalogEngine(db)
    auto_engine.reclassify_existing_catalog()
    sm_harvest = auto_engine.harvest_supermarket_catalog(max_pages=4)
    slot = (datetime.now().hour // 4) % 4
    slice_len = max(1, (len(ONLINE_MARKETPLACE_SEEDS) + 3) // 4)
    ec_slice = ONLINE_MARKETPLACE_SEEDS[slot * slice_len : (slot + 1) * slice_len]
    ec_harvest = auto_engine.harvest_online_marketplaces(seeds=ec_slice)

    indexed_cnt = db.rebuild_catalog_index()
    alert_svc = PriceAlertService(db)
    alerts_triggered = alert_svc.evaluate_active_alerts()
    radar = FastPriceRadar(db, alert_svc, auto_engine)
    radar.sync_watchlist()
    drop_stats = radar.detect_and_publish_drops()

    idx_status = ping_indexnow(touched)
    print(
        json.dumps(
            {
                "ok": True,
                "synced_at": datetime.now().isoformat(),
                "supermarket_snapshots_added": m_count,
                "partner_xml_snapshots_added": x_count,
                "auto_catalog_supermarket_products": sm_harvest.get("supermarket_products_persisted", 0),
                "auto_catalog_online_products": ec_harvest.get("online_products_persisted", 0),
                "indexed_catalog_products": indexed_cnt,
                "active_real_drops": drop_stats.get("active_real_drops", 0),
                "updated_product_pages": len(touched),
                "alerts_triggered": alerts_triggered,
                "indexnow_status": idx_status,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    run_sync()
