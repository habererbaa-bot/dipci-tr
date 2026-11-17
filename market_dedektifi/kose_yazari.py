"""
Market Dedektifi — Canlı Veri Destekli Köşe Yazarı ve Kategori Tavsiye Motoru
Çalışma Prensibi:
  1. https://dipci.tr canlı API'lerinden gerçek zamanlı mağaza fiyatlarını,
     30 günlük medyan indirim kanıtlarını ve market sepeti hesaplarını çeker.
  2. Etik Duvar: En uygun mağaza BİM/A101 gibi komisyon vermeyen bir marketse
     açıkça orayı önerir; e-ticaret sitesiyse (Amazon, Admitad, ReklamAction, Awin)
     komisyonlu takip linkini otomatik enjekte eder.
  3. İster Yapay Zeka (Gemini / DeepSeek / OpenAI) ile hikayeleştirir, ister API anahtarı
     olmadan yerleşik Türkçe editöryal motorla yayına hazır köşe yazısı (Markdown + HTML +
     Sosyal Medya Paketi) üretir.
"""

import argparse
import html
import json
import os
import re
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from affiliate_motoru import AffiliateMotoru
from dipci_istemci import KATEGORI_BASLIKLARI, MAGAZA_ISIMLERI, DipciCanliIstemci

KLASOR = Path(__file__).resolve().parent
AYARLAR_YOLU = KLASOR / "ayarlar.json"
CIKTILAR_KLASORU = Path(os.getenv("DEDEKTIF_CIKTI_KLASORU") or (KLASOR / "uretilen_yazilar"))
CIKTILAR_KLASORU.mkdir(exist_ok=True)


MARKET_DEDEKTIFI_SISTEM_PROMPTU = """Sen Türkiye'nin bağımsız ev ekonomisi ve tüketici rehberi köşe yazarı **"Market Dedektifi"**sin (35 yaşında, çalışan, bir çocuk annesi, mutfak ve ev bütçesini kuruşu kuruşuna yöneten, analitik zekâsı çok yüksek ama üslubu komşu kahvesi kadar sıcak, samimi, akıcı ve güven veren bir yazar).

### KESİN KURALLARIN (ASLA İHLAL ETME):
1. **Sıfır Halüsinasyon (Veriye %100 Sadakat):** Sana aşağıda JSON olarak verilen canlı `dipci.tr` verilerindeki mağaza isimlerini, kasa fiyatlarını (TL), birim maliyetleri (TL/kg, TL/litre, TL/adet), 30 günlük referans fiyatları ve indirim yüzdelerini kuruşu kuruşuna AYNEN kullan. Veride olmayan hiçbir rakamı veya mağazayı uydurma.
2. **Etik Bağımsızlık:** Komisyon anlaşması olsun ya da olmasın okuyucunun cebini koru. BİM, A101, ŞOK veya Hakmar daha ucuzsa açıkça "İnternetten pahalıya sipariş vermeyin, mahallenizdeki marketten alın" de.
3. **Birim Maliyet Bilinci:** Sadece paket fiyatına değil; 1 kilonun, 1 litrenin veya 1 adedin kaça geldiğine dikkat çek.
4. **Bağlantıları Koru:** JSON içindeki `inceleme_linki` ve `tum_fiyatlar_linki` adreslerini Markdown linki olarak `[Mağazada Gör](...)` ve `[Tüm Mağazaları Karşılaştır](...)` şeklinde eksiksiz yerleştir; URL'leri değiştirme.
5. **Biçimlendirme:** Yazıyı doğrudan haber sitesinde (`basinca.com`) yayınlanacak kalitede Markdown formatında (ana başlık `#`, alt başlıklar `##` / `###`, maddeler ve özet tablo ile) yaz ve en sona verilen yasal şeffaflık notunu ekle.
"""


def tl_format(tutar: float) -> str:
    return f"{float(tutar or 0):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def slug_uret(metin: str) -> str:
    harita = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    temiz = metin.translate(harita).lower()
    temiz = re.sub(r"[^a-z0-9]+", "-", temiz).strip("-")
    return temiz[:70] or "market-dedektifi-yazisi"


def ayarlari_yukle() -> Dict:
    varsayilan = {
        "dipci_api_origin": "https://dipci.tr",
        "yayin_platformu": "basinca.com",
        "yazar_adi": "Market Dedektifi",
        "ai_provider": "auto",
        "gemini_api_key": "",
        "deepseek_api_key": "",
        "openai_api_key": "",
        "affiliate": {
            "amazon_tag": "marketdedekti-21",
            "reklamaction_pub_id": "",
            "admitad_campaign_code": "",
            "awin_aff_id": "",
            "awin_merchant_map": {},
            "utm_source": "basinca_market_dedektifi",
        },
        "basinca_webhook_url": "",
    }
    if AYARLAR_YOLU.exists():
        try:
            okunan = json.loads(AYARLAR_YOLU.read_text(encoding="utf-8"))
            varsayilan.update({k: v for k, v in okunan.items() if k != "affiliate"})
            if isinstance(okunan.get("affiliate"), dict):
                varsayilan["affiliate"].update(okunan["affiliate"])
        except Exception:
            pass
    return varsayilan


def ayarlari_kaydet(yeni_ayarlar: Dict) -> Dict:
    mevcut = ayarlari_yukle()
    for k, v in yeni_ayarlar.items():
        if k == "affiliate" and isinstance(v, dict):
            mevcut["affiliate"].update(v)
        else:
            mevcut[k] = v
    AYARLAR_YOLU.write_text(json.dumps(mevcut, ensure_ascii=False, indent=2), encoding="utf-8")
    return mevcut


def markdown_to_cms_html(md_text: str) -> str:
    """Üretilen Markdown köşe yazısını basinca.com CMS editörüne yapıştırmaya hazır temiz HTML'e çevirir."""
    lines = md_text.splitlines()
    html_out: List[str] = [
        '<article class="market-dedektifi-column" style="font-family:Inter,-apple-system,sans-serif;line-height:1.7;color:#0F172A;max-width:780px;">'
    ]
    in_list = False
    in_table = False

    def inline_fmt(s: str) -> str:
        esc = html.escape(s)
        esc = re.sub(
            r"\[([^\]]+)\]\((https?://[^\)]+)\)",
            r'<a href="\2" target="_blank" rel="noopener sponsored" style="color:#059669;font-weight:600;text-decoration:underline;">\1</a>',
            esc,
        )
        esc = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", esc)
        esc = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", esc)
        return esc

    for raw_line in lines:
        line = raw_line.strip()
        kart = re.match(r"^\[\[DIPCI_KART:([A-Za-z0-9_\-]+)\]\]$", line)
        if kart:
            if in_list:
                html_out.append("</ul>")
                in_list = False
            if in_table:
                html_out.append("</tbody></table></div>")
                in_table = False
            html_out.append(f"<!--DIPCI_KART:{kart.group(1)}-->")
            continue
        if not line:
            if in_list:
                html_out.append("</ul>")
                in_list = False
            if in_table:
                html_out.append("</tbody></table></div>")
                in_table = False
            continue

        if line.startswith("|") and line.endswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(re.match(r"^:?-{2,}:?$", c) for c in cells):
                continue
            if not in_table:
                if in_list:
                    html_out.append("</ul>")
                    in_list = False
                html_out.append(
                    '<div style="overflow-x:auto;margin:18px 0;"><table style="width:100%;border-collapse:collapse;font-size:14px;border:1px solid #E2E8F0;">'
                )
                html_out.append("<thead><tr style='background:#F8FAFC;border-bottom:2px solid #E2E8F0;'>")
                for c in cells:
                    html_out.append(f"<th style='padding:10px 12px;text-align:left;'>{inline_fmt(c)}</th>")
                html_out.append("</tr></thead><tbody>")
                in_table = True
            else:
                html_out.append("<tr style='border-bottom:1px solid #F1F5F9;'>")
                for c in cells:
                    html_out.append(f"<td style='padding:9px 12px;'>{inline_fmt(c)}</td>")
                html_out.append("</tr>")
            continue

        if in_table:
            html_out.append("</tbody></table></div>")
            in_table = False

        if line == "---":
            if in_list:
                html_out.append("</ul>")
                in_list = False
            html_out.append('<hr style="border:0;border-top:1px solid #E2E8F0;margin:24px 0;" />')
        elif line.startswith("# "):
            html_out.append(
                f'<h1 style="font-size:26px;line-height:1.3;font-weight:700;margin:0 0 12px;color:#0F172A;">{inline_fmt(line[2:])}</h1>'
            )
        elif line.startswith("## "):
            html_out.append(
                f'<h2 style="font-size:20px;font-weight:700;margin:26px 0 10px;color:#0F172A;">{inline_fmt(line[3:])}</h2>'
            )
        elif line.startswith("### "):
            html_out.append(
                f'<h3 style="font-size:17px;font-weight:600;margin:20px 0 8px;color:#0F172A;">{inline_fmt(line[4:])}</h3>'
            )
        elif line.startswith("> "):
            html_out.append(
                f'<blockquote style="margin:14px 0;padding:12px 16px;background:#ECFDF5;border-left:4px solid #059669;border-radius:8px;font-size:14px;color:#065F46;">{inline_fmt(line[2:])}</blockquote>'
            )
        elif line.startswith(("- ", "* ")):
            if not in_list:
                html_out.append('<ul style="padding-left:20px;margin:10px 0;">')
                in_list = True
            html_out.append(f'<li style="margin-bottom:8px;">{inline_fmt(line[2:])}</li>')
        else:
            if in_list:
                html_out.append("</ul>")
                in_list = False
            html_out.append(f'<p style="margin:10px 0;font-size:15px;">{inline_fmt(line)}</p>')

    if in_list:
        html_out.append("</ul>")
    if in_table:
        html_out.append("</tbody></table></div>")
    html_out.append("</article>")
    return "\n".join(html_out)


class MarketDedektifiKoseYazari:
    def __init__(self, ayarlar: Optional[Dict] = None):
        self.ayarlar = ayarlar or ayarlari_yukle()
        self.istemci = DipciCanliIstemci(self.ayarlar.get("dipci_api_origin", "https://dipci.tr"))
        self.affiliate = AffiliateMotoru(self.ayarlar.get("affiliate") or {})

    # -------------------- YAPAY ZEKA ÇAĞRI KATMANI (OPSİYONEL) --------------------

    def _llm_ile_yaz(self, kullanici_istegi: str) -> Optional[Tuple[str, str]]:
        """
        Ayarlarda Gemini, DeepSeek veya OpenAI anahtarı tanımlıysa yazıyı yapay zekaya yazdırır.
        Dönüş: (markdown_metni, kullanilan_motor_adi) veya None.
        """
        secim = (self.ayarlar.get("ai_provider") or "auto").lower()
        gemini_key = (self.ayarlar.get("gemini_api_key") or os.getenv("GEMINI_API_KEY") or "").strip()
        deepseek_key = (self.ayarlar.get("deepseek_api_key") or os.getenv("DEEPSEEK_API_KEY") or "").strip()
        openai_key = (self.ayarlar.get("openai_api_key") or os.getenv("OPENAI_API_KEY") or "").strip()

        if secim == "yerlesik":
            return None

        # 1) Google Gemini 2.5 Flash
        if secim in ("auto", "gemini") and gemini_key:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}"
                payload = {
                    "system_instruction": {"parts": [{"text": MARKET_DEDEKTIFI_SISTEM_PROMPTU}]},
                    "contents": [{"role": "user", "parts": [{"text": kullanici_istegi}]}],
                    "generationConfig": {"temperature": 0.45, "maxOutputTokens": 4096},
                }
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=35) as resp:
                    res = json.loads(resp.read().decode("utf-8"))
                parcalar = res["candidates"][0]["content"]["parts"]
                metin = "".join(p.get("text", "") for p in parcalar).strip()
                if len(metin) > 300:
                    return metin, "gemini-2.5-flash"
            except Exception:
                pass

        # 2) DeepSeek Chat
        if secim in ("auto", "deepseek") and deepseek_key:
            try:
                payload = {
                    "model": "deepseek-chat",
                    "messages": [
                        {"role": "system", "content": MARKET_DEDEKTIFI_SISTEM_PROMPTU},
                        {"role": "user", "content": kullanici_istegi},
                    ],
                    "temperature": 0.45,
                }
                req = urllib.request.Request(
                    "https://api.deepseek.com/chat/completions",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {deepseek_key}",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=40) as resp:
                    res = json.loads(resp.read().decode("utf-8"))
                metin = res["choices"][0]["message"]["content"].strip()
                if len(metin) > 300:
                    return metin, "deepseek-chat"
            except Exception:
                pass

        # 3) OpenAI GPT-4o-mini
        if secim in ("auto", "openai") and openai_key:
            try:
                payload = {
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": MARKET_DEDEKTIFI_SISTEM_PROMPTU},
                        {"role": "user", "content": kullanici_istegi},
                    ],
                    "temperature": 0.45,
                }
                req = urllib.request.Request(
                    "https://api.openai.com/v1/chat/completions",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {openai_key}",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=35) as resp:
                    res = json.loads(resp.read().decode("utf-8"))
                metin = res["choices"][0]["message"]["content"].strip()
                if len(metin) > 300:
                    return metin, "gpt-4o-mini"
            except Exception:
                pass

        return None

    # -------------------- VERİ ZENGİNLEŞTİRME (AFFILIATE ENJEKSİYONU) --------------------

    def _indirim_kartini_hazirla(self, d: Dict, alt_etiket: str) -> Dict:
        ham_magaza_url = str(d.get("product_url") or d.get("tracked_url") or d.get("seo_url") or "")
        seo_url = str(d.get("seo_url") or f"{self.istemci.origin}/urun/{d.get('product_key', '').replace('_', '-')}")
        return {
            "product_key": str(d.get("product_key") or ""),
            "urun_adi": f"{d.get('brand', '')} {d.get('family_name', '')}".strip(),
            "kategori": KATEGORI_BASLIKLARI.get(d.get("category", ""), d.get("category", "")),
            "magaza": MAGAZA_ISIMLERI.get(str(d.get("platform") or ""), str(d.get("seller_name") or "")),
            "platform_kodu": str(d.get("platform") or ""),
            "eski_referans_fiyat_tl": round(float(d.get("old_ref_price") or 0), 2),
            "guncel_dip_fiyat_tl": round(float(d.get("new_price") or 0), 2),
            "indirim_yuzdesi": round(float(d.get("drop_pct") or 0), 1),
            "birim_fiyat_tl": round(float(d.get("unit_price") or 0), 2),
            "birim": d.get("base_unit") or "adet",
            "inceleme_linki": self.affiliate.linki_donustur(ham_magaza_url, alt_etiket=alt_etiket),
            "tum_fiyatlar_linki": seo_url,
        }

    # -------------------- YERLEŞİK EDİTÖRYAL MOTOR (API ANAHTARSIZ ANINDA ÜRETİM) --------------------

    def _yerlesik_genel_kose(self, veri: Dict, tarih_str: str) -> Tuple[str, str, str]:
        yildizlar = veri["yildizlar"]
        ucurumlar = veri["fiyat_ucurumlari"]
        teknoloji = veri["teknoloji_ev_firsatlari"]
        sepet = veri["sepet_ozeti"]
        ozet_ist = veri["katalog_ozeti"]

        lider_urun = yildizlar[0]["urun_adi"] if yildizlar else "Temel Mutfak Ürünleri"
        lider_oran = int(round(yildizlar[0]["indirim_yuzdesi"])) if yildizlar else 35

        baslik = (
            f"Raflarda Gerçek İndirim Avı ({tarih_str}): {lider_urun} Fiyatında %{lider_oran} Düşüş ve Günün En Ucuz Market Sepeti!"
        )
        spot = (
            f"dipci.tr radarının taradığı {ozet_ist.get('catalog_total_products', 2400)} ürün ve "
            f"{ozet_ist.get('total_live_quotes', 4300)}+ mağaza fiyatı arasından bu haftanın gerçek dip fiyatlarını, "
            f"marketler arası fahiş fiyat uçurumlarını ve en ucuz sepet rotasını çıkardım."
        )

        urun_sayisi_str = f"{int(ozet_ist.get('catalog_total_products', 2400)):,}".replace(",", ".")
        fiyat_sayisi_str = f"{int(ozet_ist.get('total_live_quotes', 4300)):,}".replace(",", ".")

        satirlar = [
            f"# {baslik}",
            "",
            f"**{self.ayarlar.get('yazar_adi', 'Market Dedektifi')} | {self.ayarlar.get('yayin_platformu', 'basinca.com')} — {tarih_str}**",
            "",
            f"Sabah kahvemi yanıma aldım, hesap makinemi açtım ve her zaman olduğu gibi zincir marketlerle online alışveriş sitelerinin 'Dev İndirim' etiketlerinin arkasına büyütecimi tuttum. Şu an sistemimizde tam **{urun_sayisi_str} ürün** ve **19 farklı market/pazaryerinden {fiyat_sayisi_str} güncel fiyat** anlık taranıyor.",
            "",
            "Açık konuşayım; bu hafta aynı ürünün bir markette dip fiyata inerken hemen yan sokaktaki rakipte neredeyse iki katına satıldığı inanılmaz bir uçurum var. Lafı uzatmadan hem dolaba gönül rahatlığıyla koyacağımız **gerçek dip fiyatları** hem de sepetinizi koruyacak **fiyat uyarılarını** tek tek masaya yatırıyorum.",
            "",
            "---",
            "",
            "## 1. Haftanın Gerçek Yıldızları: 30 Günlük Ortalamanın Altına İnenler",
            "",
            "Aşağıdaki ürünlerde 'önce bindir sonra indir' aldatmacası yok; hem kasa fiyatları hem de kilo/litre/adet birim maliyetleri son 30 günlük piyasa ortalamasının net biçimde altına inmiş durumda:",
            "",
            "| Ürün | En Uygun Mağaza | 30G Referans | Güncel Dip Fiyat | Gerçek Avantaj |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ]

        for y in yildizlar[:6]:
            satirlar.append(
                f"| **{y['urun_adi']}** | {y['magaza']} | {tl_format(y['eski_referans_fiyat_tl'])} TL | **{tl_format(y['guncel_dip_fiyat_tl'])} TL** | **-%{y['indirim_yuzdesi']:.0f}** |"
            )

        satirlar.append("")
        for y in yildizlar[:5]:
            birim_ek = (
                f" (birim maliyeti **1 {y['birim']} başına {tl_format(y['birim_fiyat_tl'])} TL**)"
                if y["birim_fiyat_tl"] > 0
                else ""
            )
            satirlar.append(
                f"**[{y['urun_adi']}]({y['tum_fiyatlar_linki']}) ({y['magaza']}):** Piyasa referansı {tl_format(y['eski_referans_fiyat_tl'])} TL iken şu an **{tl_format(y['guncel_dip_fiyat_tl'])} TL** seviyesine geriledi{birim_ek}."
            )
            satirlar.append(f"[[DIPCI_KART:{y['product_key']}]]")
            satirlar.append("")

        if sepet and sepet.get("tek_market_siralamasi"):
            sira = sepet["tek_market_siralamasi"]
            en_iyi = sira[0]
            en_pahali_m = sira[-1] if len(sira) >= 2 else None
            karma = sepet.get("karma_optimum")

            sepet_baslik_temiz = re.sub(r"^[^\wÇĞİÖŞÜçğıöşü]+", "", str(sepet["sepet_basligi"])).strip()
            satirlar.extend(
                [
                    "",
                    "---",
                    "",
                    f"## 2. Haftanın Market Sepeti Savaşı ({sepet['kalem_sayisi']} Temel İhtiyaç)",
                    "",
                    f"Evin en temel ihtiyaçlarından oluşan **{sepet_baslik_temiz}** listesini bugün marketlerde karşılaştırdığımızda ortaya çıkan canlı tablo:",
                    "",
                ]
            )
            for idx, m in enumerate(sira[:5], start=1):
                tam_mi = m["bulunan_kalem"] >= sepet["kalem_sayisi"]
                durum_ek = " *(Tüm ürünler mevcut)*" if tam_mi else f" *({sepet['kalem_sayisi'] - m['bulunan_kalem']} ürün hariç ara toplam)*"
                satirlar.append(
                    f"- **{idx}. {m['magaza']}:** **{tl_format(m['toplam_tl'])} TL** ({m['bulunan_kalem']}/{sepet['kalem_sayisi']} ürün){durum_ek}"
                )

            if karma and karma.get("toplam_tl") and en_iyi["toplam_tl"] > karma["toplam_tl"]:
                ek_fark = round(en_iyi["toplam_tl"] - karma["toplam_tl"], 2)
                satirlar.extend(
                    [
                        "",
                        f"> **Dedektifin Karma Sepet Tüyosu:** Eğer alışverişi tek market yerine ürünlerin en ucuz olduğu marketlere bölerseniz toplam tutar **{tl_format(karma['toplam_tl'])} TL**'ye düşüyor; yani en ucuz tek markete göre bile cebinizde ekstra **{tl_format(ek_fark)} TL** kalıyor! Kendi listenizi denemek için [dipci.tr Market Sepeti]({self.istemci.origin}/sepet) sayfasına göz atabilirsiniz.",
                    ]
                )
            elif en_pahali_m and en_pahali_m["toplam_tl"] > en_iyi["toplam_tl"]:
                fark_m = round(en_pahali_m["toplam_tl"] - en_iyi["toplam_tl"], 2)
                satirlar.extend(
                    [
                        "",
                        f"> **Dedektifin Notu:** Aynı sepeti bugün {en_pahali_m['magaza']} yerine **{en_iyi['magaza']}** şubesinden topladığınızda kasada tam **{tl_format(fark_m)} TL** tasarruf ediyorsunuz.",
                    ]
                )

        if ucurumlar:
            satirlar.extend(
                [
                    "",
                    "---",
                    "",
                    "## 3. Dedektifin Uyarı Listesi: Aynı Ürüne Fazla Ödemeyin!",
                    "",
                    "İşte bu hafta fiyat karşılaştırması yapmadan alışverişe çıkanların en çok para kaybedeceği ürünler. Birebir aynı ürün, iki mağaza arasında uçurum yaratmış:",
                    "",
                ]
            )
            for u in ucurumlar[:4]:
                ucuz_link = self.affiliate.linki_donustur(u["ucuz_url"] or u["dipci_url"], alt_etiket="ucurum_uyari")
                satirlar.append(
                    f"- **{u['urun_adi']}:** {u['pahali_magaza']} üzerinde **{tl_format(u['pahali_fiyat'])} TL** fiyat etiketi taşırken, **{u['ucuz_magaza']}** tarafında **{tl_format(u['ucuz_fiyat'])} TL**'ye satılıyor! "
                    f"Aradaki fark tam **{tl_format(u['fark_tl'])} TL (%{u['fark_yuzde']:.0f})**. [En Ucuz Fiyatı Gör]({ucuz_link}) • [Karşılaştırma Tablosu]({u['dipci_url']})"
                )

        if teknoloji:
            satirlar.extend(
                [
                    "",
                    "---",
                    "",
                    "## 4. Evin Büyük İhtiyaçları: Mutfak Aletleri ve Teknoloji Radarı",
                    "",
                    "Sadece mutfak alışverişine değil, çeyizden küçük ev aletlerine ve elektroniğe kadar takipçilerimin sık sorduğu ürünlerdeki en uygun mağaza fiyatlarını da süzdüm:",
                    "",
                ]
            )
            for t in teknoloji[:4]:
                t_link = self.affiliate.linki_donustur(t["ucuz_url"] or t["dipci_url"], alt_etiket="teknoloji_radar")
                satirlar.append(
                    f"- **{t['urun_adi']}:** {t['magaza_sayisi']} mağaza arasında en düşük fiyat **{tl_format(t['ucuz_fiyat'])} TL** ile **{t['ucuz_magaza']}**'da (en pahalı satıcıda {tl_format(t['pahali_fiyat'])} TL — fark **%{t['fark_yuzde']:.0f}**). "
                    f"[Fırsatı İncele]({t_link}) • [Tüm Satıcılar]({t['dipci_url']})"
                )

        satirlar.extend(
            [
                "",
                "---",
                "",
                f"*{self.affiliate.yasal_seffaflik_notu()}*",
            ]
        )
        return baslik, spot, "\n".join(satirlar)

    def _yerlesik_kategori_rehberi(self, kategori: str, veri: Dict, tarih_str: str) -> Tuple[str, str, str]:
        kat_ad = KATEGORI_BASLIKLARI.get(kategori, kategori)
        yildizlar = veri["yildizlar"]
        ucurumlar = veri["fiyat_ucurumlari"]

        baslik = f"{kat_ad} Alışveriş Rehberi ({tarih_str}): En Ucuz Birim Fiyatlar ve Kaçınılması Gereken Etiketler!"
        spot = (
            f"{kat_ad} kategorisinde zincir market ve online pazaryerlerini tek tek karşılaştırdık; "
            f"işte bu haftanın gerçek dip fiyatları ve mağazalar arası en büyük fiyat farkları."
        )

        satirlar = [
            f"# {baslik}",
            "",
            f"**{self.ayarlar.get('yazar_adi', 'Market Dedektifi')} | {self.ayarlar.get('yayin_platformu', 'basinca.com')} — {tarih_str}**",
            "",
            f"Takipçilerimden en çok mesaj aldığım konulardan biri **{kat_ad}** reyonundaki baş döndürücü fiyat karmaşası. Aynı marka, aynı gramaj ürün bir sitede 'kampanya' diye yüksekten satılırken başka bir mağazada sessiz sedasız çok daha uyguna bulunabiliyor. Gelin bugünün canlı `dipci.tr` verileriyle bu kategorideki en kârlı tercihleri birlikte seçelim.",
            "",
            "---",
            "",
        ]

        if yildizlar:
            satirlar.extend(
                [
                    f"## {kat_ad} Kategorisinde 30 Günlük Ortalamanın Altına İnenler",
                    "",
                    "| Ürün | Mağaza | Referans Fiyat | Güncel Fiyat | İndirim |",
                    "| :--- | :--- | :--- | :--- | :--- |",
                ]
            )
            for y in yildizlar[:8]:
                satirlar.append(
                    f"| **{y['urun_adi']}** | {y['magaza']} | {tl_format(y['eski_referans_fiyat_tl'])} TL | **{tl_format(y['guncel_dip_fiyat_tl'])} TL** | **-%{y['indirim_yuzdesi']:.0f}** |"
                )
            satirlar.append("")
            for y in yildizlar[:6]:
                satirlar.append(
                    f"**[{y['urun_adi']}]({y['tum_fiyatlar_linki']}) ({y['magaza']}):** {tl_format(y['eski_referans_fiyat_tl'])} TL ortalamadan **{tl_format(y['guncel_dip_fiyat_tl'])} TL** seviyesine düştü."
                )
                satirlar.append(f"[[DIPCI_KART:{y['product_key']}]]")
                satirlar.append("")
            satirlar.append("")

        if ucurumlar:
            satirlar.extend(
                [
                    "---",
                    "",
                    "## Mağazalar Arası Fiyat Uçurumu Olan Ürünler (Dikkat!)",
                    "",
                ]
            )
            for u in ucurumlar[:6]:
                u_link = self.affiliate.linki_donustur(u["ucuz_url"] or u["dipci_url"], alt_etiket=f"kat_{kategori}")
                satirlar.append(
                    f"- **{u['urun_adi']}:** En uygun **{u['ucuz_magaza']}**'da **{tl_format(u['ucuz_fiyat'])} TL**, en yüksek **{u['pahali_magaza']}**'da {tl_format(u['pahali_fiyat'])} TL *(Tam **{tl_format(u['fark_tl'])} TL** fark!)*. "
                    f"[En Uygun Mağaza]({u_link}) • [Tüm Mağazalar]({u['dipci_url']})"
                )

        satirlar.extend(["", "---", "", f"*{self.affiliate.yasal_seffaflik_notu()}*"])
        return baslik, spot, "\n".join(satirlar)

    def _yerlesik_sepet_savasi(self, sepet_veri: Dict, tarih_str: str) -> Tuple[str, str, str]:
        sira = sepet_veri.get("tek_market_siralamasi") or []
        karma = sepet_veri.get("karma_optimum") or {}
        en_iyi = sira[0] if sira else {"magaza": "Market", "toplam_tl": 0}
        en_pahali = sira[-1] if len(sira) >= 2 else en_iyi
        fark = round(float(en_pahali["toplam_tl"]) - float(en_iyi["toplam_tl"]), 2)

        sepet_baslik_temiz = re.sub(r"^[^\wÇĞİÖŞÜçğıöşü]+", "", str(sepet_veri["sepet_basligi"])).strip()
        tam_marketler = [m for m in sira if m["bulunan_kalem"] >= sepet_veri["kalem_sayisi"]]
        referans_en_iyi = tam_marketler[0] if tam_marketler else en_iyi

        baslik = (
            f"Bu Hafta Hangi Market Daha Ucuz? ({tarih_str}): {sepet_veri['kalem_sayisi']} Temel Üründe "
            f"Zincir Marketlerin Canlı Kasa Fişi Karşılaştırması!"
        )
        spot = (
            f"{sepet_baslik_temiz} listesini BİM, A101, ŞOK, Migros ve CarrefourSA raflarında kuruşu kuruşuna karşılaştırdık; "
            f"işte tek market toplamları ve akıllı karma sepet tasarrufu."
        )

        satirlar = [
            f"# {baslik}",
            "",
            f"**{self.ayarlar.get('yazar_adi', 'Market Dedektifi')} | {self.ayarlar.get('yayin_platformu', 'basinca.com')} — {tarih_str}**",
            "",
            f"Her hafta mutfak alışverişine çıkmadan önce bana sorduğunuz o meşhur soruya bugün rakamlarla yanıt veriyorum: **'Elimizdeki listeyle hangi marketin kapısından girersek kasada daha az öderiz?'** **{sepet_baslik_temiz}** içindeki **{sepet_veri['kalem_sayisi']} temel ürünü** canlı `dipci.tr` sepet motorunda yarıştırdım.",
            "",
            "---",
            "",
            "## Tek Marketten Alırsanız Kasa Toplamı Sıralaması",
            "",
            "| Sıra | Zincir Market | Bulunan Ürün | Kasa Toplamı | Değerlendirme |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ]

        for idx, m in enumerate(sira, start=1):
            if m["bulunan_kalem"] >= sepet_veri["kalem_sayisi"]:
                ek = round(float(m["toplam_tl"]) - float(referans_en_iyi["toplam_tl"]), 2)
                fark_str = "Tam Liste En Uygun" if ek <= 0.01 else f"Tam Liste (+{tl_format(ek)} TL)"
            else:
                eksik = sepet_veri["kalem_sayisi"] - m["bulunan_kalem"]
                fark_str = f"{eksik} ürün hariç ara toplam"
            satirlar.append(
                f"| {idx} | **{m['magaza']}** | {m['bulunan_kalem']}/{sepet_veri['kalem_sayisi']} | **{tl_format(m['toplam_tl'])} TL** | {fark_str} |"
            )

        if karma and karma.get("duraklar"):
            satirlar.extend(
                [
                    "",
                    "---",
                    "",
                    f"## Akıllı Karma Rota: Sepeti Bölerek {tl_format(karma['toplam_tl'])} TL'ye Düşürmek",
                    "",
                    "Eğer yolunuzun üzerindeki marketlerden sadece en ucuz olan ürünleri toplarsanız durak durak alınacaklar listesi şöyle şekilleniyor:",
                    "",
                ]
            )
            for durak in karma["duraklar"]:
                satirlar.append(f"### {durak['magaza']} (Ara Toplam: {tl_format(durak['ara_toplam_tl'])} TL)")
                for kalem in durak["urunler"]:
                    satirlar.append(
                        f"- {kalem['adet']}x **{kalem['urun_adi']}** → {tl_format(kalem['satir_toplam_tl'])} TL"
                    )
                satirlar.append("")

        satirlar.extend(
            [
                f"Kendi evinizin marka ve ihtiyaç listesini oluşturup anlık karşılaştırmak için [dipci.tr Market Sepeti]({self.istemci.origin}/kendi-sepetim) aracını ücretsiz kullanabilirsiniz.",
                "",
                "---",
                "",
                f"*{self.affiliate.yasal_seffaflik_notu()}*",
            ]
        )
        return baslik, spot, "\n".join(satirlar)

    def _yerlesik_urun_rontgeni(self, detay: Dict, tarih_str: str) -> Tuple[str, str, str]:
        p = detay["product"]
        teklifler = sorted(
            detay.get("offers") or [],
            key=lambda x: (float(x.get("pack_price") or 0), float(x.get("unit_price") or 0)),
        )
        urun_tam_ad = f"{p['brand']} {p['family_name']}"
        en_ucuz = teklifler[0] if teklifler else {}
        en_pahali = teklifler[-1] if len(teklifler) >= 2 else en_ucuz
        ucuz_magaza = MAGAZA_ISIMLERI.get(str(en_ucuz.get("platform") or ""), en_ucuz.get("seller_name") or "Mağaza")
        fark_tl = round(float(en_pahali.get("pack_price") or 0) - float(en_ucuz.get("pack_price") or 0), 2)

        baslik = (
            f"{urun_tam_ad} En Ucuz Nerede? ({tarih_str}): {len(teklifler)} Mağaza Fiyat Karşılaştırması"
        )
        spot = (
            f"{urun_tam_ad} şu an en uygun {tl_format(en_ucuz.get('pack_price', 0))} TL fiyatla {ucuz_magaza} üzerinde; "
            f"en pahalı satıcıyla arasında {tl_format(fark_tl)} TL fark bulunuyor."
        )

        seo_link = f"{self.istemci.origin}/urun/{p['product_key'].replace('_', '-')}"
        satirlar = [
            f"# {baslik}",
            "",
            f"**{self.ayarlar.get('yazar_adi', 'Market Dedektifi')} | {self.ayarlar.get('yayin_platformu', 'basinca.com')} — {tarih_str}**",
            "",
            f"Bugün büyütecimizin altında **{urun_tam_ad}** var. Ürünü satıldığı **{len(teklifler)} farklı mağazada** hem paket fiyatı hem de **1 {p['base_unit']}** birim maliyetiyle tek tek sıraladım.",
            "",
            "| Sıra | Mağaza | Satış Fiyatı | Birim Maliyet | Bağlantı |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ]

        for idx, o in enumerate(teklifler, start=1):
            m_ad = MAGAZA_ISIMLERI.get(str(o.get("platform") or ""), o.get("seller_name") or "Mağaza")
            link = self.affiliate.linki_donustur(o.get("tracked_url") or o.get("url") or seo_link, alt_etiket="urun_rontgen")
            satirlar.append(
                f"| {idx} | **{m_ad}** | **{tl_format(o.get('pack_price', 0))} TL** | {tl_format(o.get('unit_price', 0))} TL/{p['base_unit']} | [Mağazada Gör]({link}) |"
            )

        satirlar.extend(
            [
                "",
                f"> **Dedektifin Tavsiyesi:** En uygun fiyatla en yüksek etiket arasında **{tl_format(fark_tl)} TL** fark var. Fiyat değişimlerinden anında haberdar olmak için [dipci.tr ürün sayfasından]({seo_link}) 15 dakikalık ücretsiz fiyat alarmı kurabilirsiniz.",
                "",
                "---",
                "",
                f"*{self.affiliate.yasal_seffaflik_notu()}*",
            ]
        )
        return baslik, spot, "\n".join(satirlar)

    # -------------------- ANA ÜRETİM METODU --------------------

    def yazi_uret(
        self,
        tur: str = "genel_kose",
        kategori: str = "gida",
        sorgu: str = "",
        kaydet: bool = True,
    ) -> Dict:
        simdi = datetime.now()
        tarih_str = simdi.strftime("%d.%m.%Y")

        if tur == "urun_rontgeni" and sorgu.strip():
            detay = self.istemci.tekil_urun_rontgeni_getir(sorgu.strip())
            if not detay or not detay.get("product"):
                raise ValueError(f"'{sorgu}' araması için ürün verisi bulunamadı.")
            baslik, spot, taslak_md = self._yerlesik_urun_rontgeni(detay, tarih_str)
            llm_veri_paketi = {"tur": tur, "tarih": tarih_str, "urun_detayi": detay}
        elif tur == "sepet_savasi":
            ham_sepet = self.istemci.sepet_savasi_getir(sorgu.strip())
            opt = ham_sepet.get("optimizasyon") or {}
            sira = [
                {
                    "magaza": MAGAZA_ISIMLERI.get(r["platform"], r["platform"].upper()),
                    "toplam_tl": round(float(r["checkout_total"]), 2),
                    "bulunan_kalem": int(r["matched_count"]),
                    "tam_eslesme": bool(r.get("is_full_match")),
                }
                for r in (opt.get("single_store_ranking") or [])
            ]
            sp = opt.get("split_optimum") or {}
            duraklar = [
                {
                    "magaza": MAGAZA_ISIMLERI.get(st["platform"], st["platform"].upper()),
                    "ara_toplam_tl": round(float(st["checkout_subtotal"]), 2),
                    "urunler": [
                        {
                            "adet": it["qty"],
                            "urun_adi": it["display_name"],
                            "satir_toplam_tl": round(float(it["checkout_line_total"]), 2),
                        }
                        for it in (st.get("items") or [])
                    ],
                }
                for st in (sp.get("stops") or [])
            ]
            sepet_veri = {
                "sepet_basligi": ham_sepet["sepet_basligi"],
                "kalem_sayisi": int(opt.get("item_count") or 0),
                "tek_market_siralamasi": sira,
                "karma_optimum": {
                    "toplam_tl": round(float(sp.get("checkout_total") or 0), 2),
                    "duraklar": duraklar,
                },
            }
            baslik, spot, taslak_md = self._yerlesik_sepet_savasi(sepet_veri, tarih_str)
            llm_veri_paketi = {"tur": tur, "tarih": tarih_str, "sepet_karsilastirmasi": sepet_veri}
        elif tur == "kategori_rehberi":
            analiz = self.istemci.kategori_analizi_getir(kategori=kategori, arama=sorgu, limit=48)
            yildizlar = [
                self._indirim_kartini_hazirla(d, alt_etiket=f"kat_{kategori}")
                for d in analiz["yildiz_indirimler"]
            ]
            veri = {
                "kategori": KATEGORI_BASLIKLARI.get(kategori, kategori),
                "yildizlar": yildizlar,
                "fiyat_ucurumlari": analiz["fiyat_ucurumlari"],
            }
            baslik, spot, taslak_md = self._yerlesik_kategori_rehberi(kategori, veri, tarih_str)
            llm_veri_paketi = {"tur": tur, "tarih": tarih_str, **veri}
        else:
            # Varsayılan: Haftalık / Günlük Ana Köşe Yazısı (genel_kose)
            genel = self.istemci.kategori_analizi_getir(kategori="", arama=sorgu, limit=60)
            ev_tek = self.istemci.kategori_analizi_getir(kategori="ev_yasam_mutfak", limit=24)
            tekno = self.istemci.kategori_analizi_getir(kategori="teknoloji_beyaz_esya", limit=24)
            ham_sepet = self.istemci.sepet_savasi_getir()
            opt = ham_sepet.get("optimizasyon") or {}
            sira = [
                {
                    "magaza": MAGAZA_ISIMLERI.get(r["platform"], r["platform"].upper()),
                    "toplam_tl": round(float(r["checkout_total"]), 2),
                    "bulunan_kalem": int(r["matched_count"]),
                }
                for r in (opt.get("single_store_ranking") or [])
            ]
            sp = opt.get("split_optimum") or {}
            # Aynı ürünün hem 3. bölümde hem 4. bölümde tekrar etmesini engelle
            tekno_adaylar = ev_tek["fiyat_ucurumlari"][:3] + tekno["fiyat_ucurumlari"][:3]
            tekno_keys = {t["product_key"] for t in tekno_adaylar[:4]}
            genel_ucurum_tekil = [u for u in genel["fiyat_ucurumlari"] if u["product_key"] not in tekno_keys][:5]
            veri = {
                "katalog_ozeti": genel["ozet"],
                "yildizlar": [
                    self._indirim_kartini_hazirla(d, alt_etiket="haftalik_kose")
                    for d in genel["yildiz_indirimler"]
                ],
                "fiyat_ucurumlari": genel_ucurum_tekil,
                "teknoloji_ev_firsatlari": tekno_adaylar[:4],
                "sepet_ozeti": {
                    "sepet_basligi": ham_sepet["sepet_basligi"],
                    "kalem_sayisi": int(opt.get("item_count") or 0),
                    "tek_market_siralamasi": sira,
                    "karma_optimum": {"toplam_tl": round(float(sp.get("checkout_total") or 0), 2)},
                },
            }
            baslik, spot, taslak_md = self._yerlesik_genel_kose(veri, tarih_str)
            llm_veri_paketi = {"tur": "genel_kose", "tarih": tarih_str, **veri}

        # Eğer yapay zeka API anahtarı girilmişse zenginleştirilmiş hikayeleştirme dene
        kullanilan_motor = "yerlesik_editoryal_motor"
        llm_istegi = (
            f"Tarih: {tarih_str}\nYayın Platformu: {self.ayarlar.get('yayin_platformu', 'basinca.com')}\n"
            f"Yazı Türü: {tur}\n"
            f"Aşağıdaki canlı dipci.tr verilerini kullanarak eksiksiz, samimi ve vurucu bir Market Dedektifi köşe yazısı yaz.\n"
            f"Yasal Alt Bilgi: {self.affiliate.yasal_seffaflik_notu()}\n\n"
            f"CANLI VERİ PAKETİ (JSON):\n{json.dumps(llm_veri_paketi, ensure_ascii=False, indent=2)}"
        )
        llm_sonuc = self._llm_ile_yaz(llm_istegi)
        final_md = taslak_md
        if llm_sonuc:
            final_md, kullanilan_motor = llm_sonuc
            ilk_satir = final_md.splitlines()[0].lstrip("# ").strip()
            if ilk_satir:
                baslik = ilk_satir

        final_html = markdown_to_cms_html(final_md)
        final_html = self._dipci_kartlarini_yerlestir(final_html)
        final_md = re.sub(
            r"\[\[DIPCI_KART:([A-Za-z0-9_\-]+)\]\]",
            lambda m: f"[dipci.tr'de tüm mağaza fiyatları →]({self.istemci.origin}/urun/{m.group(1).replace('_', '-')})",
            final_md,
        )
        sosyal_paket = self._sosyal_medya_paketi_uret(baslik, spot, llm_veri_paketi)

        cikti = {
            "ok": True,
            "uretim_zamani": simdi.isoformat(),
            "kullanilan_motor": kullanilan_motor,
            "tur": tur,
            "kategori": kategori,
            "baslik": baslik,
            "ozet_spot": spot,
            "markdown": final_md,
            "html": final_html,
            "sosyal_medya": sosyal_paket,
        }

        if kaydet:
            dosya_koku = f"{simdi.strftime('%Y%m%d_%H%M%S')}_{slug_uret(baslik)[:45]}"
            md_yol = CIKTILAR_KLASORU / f"{dosya_koku}.md"
            html_yol = CIKTILAR_KLASORU / f"{dosya_koku}.html"
            json_yol = CIKTILAR_KLASORU / f"{dosya_koku}.json"
            md_yol.write_text(final_md, encoding="utf-8")
            html_yol.write_text(final_html, encoding="utf-8")
            json_yol.write_text(json.dumps(cikti, ensure_ascii=False, indent=2), encoding="utf-8")
            cikti["kaydedilen_md"] = str(md_yol)
            cikti["kaydedilen_html"] = str(html_yol)

        # basinca.com CMS / Otomasyon Webhook tanımlıysa otomatik gönder
        hedef_webhook = (self.ayarlar.get("basinca_webhook_url") or "").strip()
        if hedef_webhook.startswith(("http://", "https://")):
            try:
                w_req = urllib.request.Request(
                    hedef_webhook,
                    data=json.dumps(cikti, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(w_req, timeout=10) as w_resp:
                    cikti["webhook_durumu"] = w_resp.status
            except Exception as exc:
                cikti["webhook_durumu"] = f"Hata: {exc}"

        return cikti

    def _dipci_kartlarini_yerlestir(self, govde_html: str) -> str:
        """Yazının içinde önerilen her ürün için dipci.tr kartı: ürün görseli + mağaza fiyat sıralaması (Kemal, 17 Kas 2026)."""
        def kart(m):
            key = m.group(1)
            try:
                with urllib.request.urlopen(
                    urllib.request.Request(
                        f"{self.istemci.origin}/api/dedektif/kart/{key}",
                        headers={"User-Agent": "MarketDedektifi-Columnist/2.0"},
                    ),
                    timeout=20,
                ) as r:
                    k = json.loads(r.read().decode("utf-8"))
            except Exception:
                return ""
            if not k.get("ok"):
                return ""
            satirlar = []
            for i, q in enumerate(k.get("magazalar") or []):
                kazanan = i == 0
                satirlar.append(
                    '<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;padding:7px 10px;margin-top:6px;border-radius:10px;'
                    + ("background:#ECFDF5;border:2px solid #10B981;" if kazanan else "background:#F8FAFC;border:1px solid #E2E8F0;")
                    + '">'
                    + f'<span style="background:{html.escape(q["renk"])};color:{html.escape(q["yazi_rengi"])};padding:3px 9px;border-radius:6px;font-weight:700;font-size:13px;">{html.escape(q["magaza"])}</span>'
                    + ('<span style="font-size:11px;font-weight:700;color:#059669;margin-right:auto;">EN UCUZ</span>' if kazanan else "")
                    + f'<span style="font-weight:800;font-size:15px;color:{"#047857" if kazanan else "#334155"};white-space:nowrap;">{html.escape(q["fiyat_yazi"])}</span>'
                    + "</div>"
                )
            return (
                f'<a href="{html.escape(k["link"])}" target="_blank" rel="noopener" '
                'style="display:block;max-width:520px;margin:12px 0 22px;border:1px solid #E2E8F0;border-radius:14px;overflow:hidden;background:#FFFFFF;color:#0F172A;text-decoration:none;">'
                f'<div style="background:#FFFFFF;text-align:center;padding:14px;"><img src="{html.escape(k["gorsel"])}" alt="{html.escape(k["baslik"])}" '
                'style="max-width:100%;max-height:220px;object-fit:contain;display:inline-block;" loading="lazy"></div>'
                '<div style="padding:12px 14px 14px;border-top:1px solid #F1F5F9;">'
                f'<div style="font-weight:800;font-size:16px;line-height:1.35;">{html.escape(k["baslik"])}</div>'
                + "".join(satirlar)
                + '<div style="margin-top:10px;font-weight:700;font-size:14px;color:#059669;">dipci.tr\'de tüm mağaza fiyatları →</div>'
                "</div></a>"
            )

        return re.sub(r"<!--DIPCI_KART:([A-Za-z0-9_\-]+)-->", kart, govde_html)

    def _sosyal_medya_paketi_uret(self, baslik: str, spot: str, paket: Dict) -> Dict[str, str]:
        yildizlar = paket.get("yildizlar") or []
        maddeler = []
        for y in yildizlar[:3]:
            maddeler.append(
                f"• {y['urun_adi']} ({y['magaza']}): {tl_format(y['eski_referans_fiyat_tl'])} TL yerine {tl_format(y['guncel_dip_fiyat_tl'])} TL (-%{y['indirim_yuzdesi']:.0f})"
            )
        madde_metni = "\n".join(maddeler)
        x_post = (
            f"Market Dedektifi Röntgeni: {baslik}\n\n"
            f"{madde_metni}\n\n"
            f"Canlı mağaza karşılaştırmaları: {self.istemci.origin}/firsatlar #işbirliği"
        ).strip()
        insta_caption = (
            f"{baslik}\n\n"
            f"{spot}\n\n"
            f"Öne Çıkan Gerçek İndirimler:\n{madde_metni}\n\n"
            f"Tüm market ve online mağaza birim fiyat karşılaştırmaları için profildeki dipci.tr bağlantısına göz atabilirsiniz.\n"
            f"#marketdedektifi #fiyatkarşılaştırma #indirim #mutfakekonomisi #işbirliği"
        ).strip()
        return {"x_twitter": x_post, "instagram": insta_caption}


def main() -> None:
    parser = argparse.ArgumentParser(description="Market Dedektifi Canlı Köşe Yazarı Motoru")
    parser.add_argument(
        "--tur",
        choices=["genel_kose", "kategori_rehberi", "sepet_savasi", "urun_rontgeni"],
        default="genel_kose",
        help="Üretilecek yazı formatı",
    )
    parser.add_argument("--kategori", default="gida", help="Kategori kodu (ör: gida, temizlik, ev_yasam_mutfak)")
    parser.add_argument("--sorgu", default="", help="Opsiyonel ürün/konu araması")
    args = parser.parse_args()

    yazar = MarketDedektifiKoseYazari()
    sonuc = yazar.yazi_uret(tur=args.tur, kategori=args.kategori, sorgu=args.sorgu, kaydet=True)
    print(
        json.dumps(
            {
                "ok": True,
                "baslik": sonuc["baslik"],
                "kullanilan_motor": sonuc["kullanilan_motor"],
                "kaydedilen_md": sonuc.get("kaydedilen_md"),
                "kaydedilen_html": sonuc.get("kaydedilen_html"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
