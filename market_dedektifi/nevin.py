"""
MARKET DEDEKTİFİ — köşe yazarı Nevin Özdemir (Kemal, 12 ve 17 Kas 2026).

Tarif MTR1'deki src/yazar/marketDedektifi.ts ile aynı: içten, "Dostlar" diye seslenen, 35 yaşlarında tatlı dilli
bir kadın; madde listesi, tablo, başlıklı bölüm yok; düz bir köşe yazısı. Fark: rakamlar dipci.tr'nin kendi
verisinden gelir ve önerdiği ürünlerin adı yazının içinde dipci.tr sayfasına küçük, doğal bir bağlantı olur.

Model: Coarena DeepSeek v4.1 flash (Kemal, 17 Kas); dolu ise Coarena'nın diğerleri, son çare ücretsiz Gemini.
Çıktı: /opt/dipci/data/dedektif/<zaman>.json  → https://dipci.tr/api/dedektif/gunun-yazisi
"""
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ORIGIN = os.getenv("DIPCI_ORIGIN", "https://dipci.tr")
CIKTI = Path(os.getenv("DEDEKTIF_CIKTI_KLASORU") or (Path(__file__).resolve().parent / "uretilen_yazilar"))
MODEL = os.getenv("NEVIN_GEMINI_MODEL", "gemini-3.8-flash")
# Ücretsiz katmanda biri doluysa (503/429) sıradakine geçilir.
MODELLER = [MODEL] + [m for m in ("gemini-3.5-flash", "gemini-3.5-flash-lite") if m != MODEL]

# Her türden birkaç ürün: şok indirim, gerçek indirim, fiyat farkı, indirim marketi, online kampanya...
TURLER = [("sok", 3), ("dip", 3), ("fark", 2), ("indirim-marketi", 2), ("kampanya", 1), ("amazon", 1),
          ("temizlik-bakim", 2), ("teknoloji-ev", 2)]

KARAKTER = "\n".join([
    "Sen Nevin Özdemir'sin; basınca.com'da \"MARKET DEDEKTİFİ\" köşesini yazıyorsun. 35 yaşlarında, tatlı dilli bir kadınsın.",
    "Okurlarına \"Dostlar\" diye seslenirsin; içten, candan, bir abla bir kardeş gibi konuşursun. Ev ekonomisini bilen, hesabını kitabını yapan, kandırılmaya gelmeyen bir kadınsın.",
    "Anlattığını gündelik hayattan küçük örneklerle (mahalledeki manav, mutfak, çocukların okul alışverişi gibi) somutlaştırırsın; yerinde bir deyime başvurur, zaman zaman bir atasözüyle sözünü güçlendirirsin — ama her paragrafa doldurmazsın, doğal kalırsın.",
    "Örneklerinde KİŞİ ADI kullanmazsın (\"bir komşum\", \"bir arkadaşım\" dersin).",
    "Yapay zekâ gibi yazmazsın: madde listesi, tablo, başlıklı bölüm, kalın yazı, \"sonuç olarak\", \"unutmayın ki\", \"özetle\" gibi kalıplar yok. Cümle uzunlukların değişir; bazen kısa bir cümleyle vurgu yaparsın.",
    "Kitap, dergi gibi kültür ürünlerini fiyatı ucuz diye ÖNERMEZSİN; kitap fiyatıyla ölçülmez (Kemal, 18 Kas 2026).",
    "Yazım kuralı: virgülden sonra gelen kelime (özel ad değilse) küçük harfle başlar: \"bereketi de artıyor dostlar\" ya da \"…artıyor, dostlar.\" — \", Dostlar\" YAZILMAZ. Yalnız yazının ilk kelimesi \"Dostlar,\" büyük harfle başlar.",
    "Okura ASLA \"pazarlık edin\" demezsin: marketlerde ve online mağazalarda fiyat sabittir, pazarlık olmaz (Kemal, 17 Kas 2026). Bunun yerine mağazaları karşılaştırmayı, en ucuz yeri seçmeyi önerirsin.",
    "YALNIZ sana verilen ürün, fiyat ve mağaza bilgilerini kullanırsın; fiyat, kampanya, mağaza ya da indirim UYDURMAZSIN.",
    "Fiyatların gün içinde değişebileceğini bir yerde doğal bir dille hatırlatırsın. Marka kötülemez, hakaret etmezsin.",
])


def _get(path: str):
    req = urllib.request.Request(ORIGIN + path, headers={"User-Agent": "dipci-nevin/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


KATEGORI_ADI = {
    "gida": "gıda / market ürünü", "temizlik": "temizlik ürünü", "kisisel_bakim": "kişisel bakım / kozmetik ürünü",
    "anne_bebek": "anne-bebek ürünü", "evcil_hayvan": "evcil hayvan ürünü", "teknoloji_beyaz_esya": "elektronik / beyaz eşya",
    "ev_yasam_mutfak": "ev ve mutfak eşyası", "saglik_spor_outdoor": "spor / sağlık ürünü", "yapi_oto_bahce": "yapı market / oto / bahçe ürünü",
    "giyim_aksesuar": "giyim / aksesuar", "kitap_kirtasiye": "kitap / kırtasiye", "oyuncak_hobi": "oyuncak / hobi ürünü",
}


def _cins(link: str) -> str:
    """Ürünün ne olduğu (kitap mı, deterjan mı) — Kemal, 18 Kas 2026: "Ahmet Ümit ne ya?... Kitap desenize"."""
    try:
        anahtar = link.split("/urun/")[1].split("?")[0].replace("-", "_")
        p = (_get(f"/api/products/{anahtar}") or {}).get("product") or {}
        return KATEGORI_ADI.get(p.get("category") or "", p.get("category") or "")
    except Exception:
        return ""


# Kemal, 18 Kas 2026: "kitabın ucuzu yazılmaz... etik değil... kitap fiyatıyla ölçülmemeli". Kitaplar ucuz diye
# önerilmez. (Okurların çok beğendiği popüler kitap indirime girerse yazı sonunda anılabilir; bunun için güvenilir
# beğeni verisi gelene kadar kitap hiç alınmaz.)
_KITAP = re.compile(r"^[^\d]+ - [A-ZÇĞİÖŞÜ][a-zçğıöşü'’]+(?: [A-ZÇĞİÖŞÜ][a-zçğıöşü'’.]+){1,3}$|\b(kitap|roman|hikaye|şiir|ciltli|karton kapak|yayınları|yayınevi)\b", re.I)


def kitap_mi(ad: str) -> bool:
    return bool(_KITAP.search(ad or ""))


def urunleri_topla():
    secilen, gorulen = [], set()
    for tur, adet in TURLER:
        try:
            items = _get(f"/api/basinca/reklamlar?tur={tur}&limit=12").get("items") or []
        except Exception:
            items = []
        n = 0
        for it in items:
            link = it["link"].split("?")[0]
            if link in gorulen or kitap_mi(it.get("baslik", "")):
                continue
            gorulen.add(link)
            secilen.append({
                "tur": it.get("etiket") or tur,
                "cins": _cins(link),
                "ad": it["baslik"],
                "link": link + "?utm_source=basinca&utm_medium=market_dedektifi",
                "durum": it.get("ozet", ""),
                "magazalar": ", ".join(f"{m['magaza']} {m['fiyat']:.2f} TL".replace(".", ",") for m in it.get("magazalar", [])),
            })
            n += 1
            if n >= adet:
                break
    return secilen


def gemini(sistem: str, soru: str) -> str:
    """Yazıyı Coarena yazar (Kemal, 17 Kas 2026: "dipci coarena ile yazdır"): önce DeepSeek v4.1 flash,
    sonra Coarena'nın diğer modelleri; hepsi doluysa son çare ücretsiz Gemini."""
    son = None
    for m in ("deepseek-v4-1-flash-1017", "gemini-3-8-flash", "gpt-6-luna"):
        try:
            return _coarena(m, sistem, soru)
        except Exception as e:
            son = e
    for m in MODELLER:
        try:
            return _gemini(m, sistem, soru)
        except Exception as e:
            son = e
    raise RuntimeError(f"Modellerin hepsi dolu: {son}")


def _coarena(model: str, sistem: str, soru: str) -> str:
    anahtar = os.getenv("COARENA_API_KEY", "").strip()
    if not anahtar:
        raise RuntimeError("COARENA_API_KEY yok")
    govde = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": sistem}, {"role": "user", "content": soru}],
        "temperature": 0.9,
        "max_tokens": 8000,
        # Coarena notu (ORTAK/COARENA-AYAR.md): gönderilmezse içerik sık sık boş döner.
        "reasoning_effort": "none",
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    for i in range(3):
        req = urllib.request.Request(
            "https://coarena.ai/api/v1/chat/completions", data=govde, method="POST",
            headers={"content-type": "application/json", "authorization": f"Bearer {anahtar}", "user-agent": "dipci-nevin/1.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                j = json.loads(r.read().decode("utf-8"))
            return j["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503):
                time.sleep(20 * (i + 1))
                continue
            raise
        except (TimeoutError, urllib.error.URLError, OSError):
            time.sleep(20 * (i + 1))
            continue
    raise RuntimeError("Coarena yoğun")


def _gemini(model: str, sistem: str, soru: str) -> str:
    anahtar = os.getenv("GEMINI_UCRETSIZ_KEY", "").strip()
    if not anahtar:
        raise RuntimeError("GEMINI_UCRETSIZ_KEY yok")
    govde = json.dumps({
        "systemInstruction": {"parts": [{"text": sistem}]},
        "contents": [{"role": "user", "parts": [{"text": soru}]}],
        "generationConfig": {"maxOutputTokens": 20000, "temperature": 0.95, "responseMimeType": "application/json"},
    }).encode("utf-8")
    for i in range(3):
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            data=govde, method="POST",
            headers={"content-type": "application/json", "x-goog-api-key": anahtar},
        )
        try:
            with urllib.request.urlopen(req, timeout=420) as r:
                j = json.loads(r.read().decode("utf-8"))
            return "".join(p.get("text", "") for p in j["candidates"][0]["content"]["parts"])
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503):
                time.sleep(20 * (i + 1))
                continue
            raise
        except (TimeoutError, urllib.error.URLError, OSError):
            # Ücretsiz katman yoğunken cevap geç gelebiliyor; bekleyip yeniden dene.
            time.sleep(30 * (i + 1))
            continue
    raise RuntimeError("Gemini ücretsiz: yoğunluk geçmedi")


def yazim_duzelt(t: str) -> str:
    """Kemal, 18 Kas 2026: "bereketi de artıyor, Dostlar olmaz... virgülden sonra gelen kelimenin baş harfi büyük olmaz".
    Hitap sözleri cümle ortasında/sonunda küçük harfle yazılır (özel adlar dokunulmaz)."""
    return re.sub(r"([,;:–—-]\s*)(Dostlar|Sevgili dostlar|Arkadaşlar|Komşular)\b", lambda m: m.group(1) + m.group(2)[0].lower() + m.group(2)[1:], t)


def yaz(urunler):
    tarih = datetime.now().strftime("%d.%m.%Y")
    bilgi = "\n".join(
        f"- {u['ad']} (ÜRÜN CİNSİ: {u.get('cins') or 'adından anlaşılır'}) [{u['tur']}]: {u['durum']} Mağazalar: {u['magazalar']}. BAĞLANTI: {u['link']}" for u in urunler
    )
    soru = "\n".join([
        f"Bugün {tarih}. Sabah dipci.tr'de market ve mağaza fiyatlarını taradın, şunları buldun:",
        bilgi,
        "Bunlardan gıdada, temizlikte, teknolojide ve günlük ihtiyaçlarda gerçekten hesaplı olanları anlatan bir köşe yazısı yaz (550–800 kelime). \"Dostlar,\" diye başla.",
        "Bugün birden ucuzlayan (şok indirim) ürünleri, aynı ürünün mağazalar arasındaki fiyat farkını ve kampanyaları dedektif gözüyle tart. Hepsini saymak zorunda değilsin; en anlamlı 5-7 tanesini seç.",
        "Her ürünün NE OLDUĞUNU okur ilk okuyuşta anlasın: ürün adını tek başına bırakma, cinsini cümlede söyle. Örnek: \"Ahmet Ümit'in Roma'nın Beş Günü romanı\", \"Ariel'in 4 kiloluk toz çamaşır deterjanı\", \"Philips'in airfryer'ı (yağsız fritöz)\". Kitapsa kitap/roman olduğunu ve yazarını, elektronikse ne cihaz olduğunu yaz. Ürün adındaki büyük-küçük harf hatalarını düzelt (Roma'Nın değil Roma'nın).",
        "Bahsettiğin her ürünün adını İLK geçtiği yerde markdown bağlantısı yap: [ürün adı](BAĞLANTI). Bağlantıyı aynen kullan, değiştirme. Başka bağlantı ekleme.",
        "Başlık kısa ve merak uyandırıcı olsun (büyük harf değil). Spot tek cümle.",
        "Metinde paragraflar arasında boş satır bırak. Yalnız JSON: {\"baslik\": \"\", \"spot\": \"\", \"metin\": \"\"}",
    ])
    cevap = gemini(KARAKTER, soru)
    y = json.loads(cevap[cevap.index("{"): cevap.rindex("}") + 1])
    y["metin"] = yazim_duzelt(y.get("metin") or "")
    if not y.get("metin") or len(y["metin"]) < 1500:
        raise RuntimeError("Yazı kısa ya da boş geldi")
    return y


def html_yap(metin: str, izinli: set) -> str:
    """Düz paragraflar; yalnız verilen dipci bağlantıları küçük, sade link olur."""
    parcalar = []
    for p in [x.strip() for x in re.split(r"\n\s*\n", metin) if x.strip()]:
        p = html.escape(p.replace("**", "").replace("__", ""))

        def bag(m):
            ad, url = m.group(1), html.unescape(m.group(2))
            if url not in izinli:
                return ad
            return f'<a href="{html.escape(url)}" target="_blank" rel="noopener">{ad}</a>'

        p = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", bag, p)
        parcalar.append(f"<p>{p}</p>")
    return "\n".join(parcalar)


def main() -> int:
    # Elle: son yazıyı basınca'ya (yeniden) gönder.  python nevin.py --gonder [basinca_id]
    if len(sys.argv) > 1 and sys.argv[1] == "--gonder":
        yol = sorted(CIKTI.glob("*.json"))[-1]
        cikti = json.loads(yol.read_text(encoding="utf-8"))
        print(basincaya_gonder(cikti, yol, int(sys.argv[2]) if len(sys.argv) > 2 else 0))
        return 0
    bugun = datetime.now().strftime("%Y%m%d")
    if any(CIKTI.glob(f"{bugun}_*.json")):
        print("Bugünün yazısı zaten var.")
        return 0
    urunler = urunleri_topla()
    if len(urunler) < 6:
        print("Yeterli ürün yok, bugün yazı yok.")
        return 1
    y = yaz(urunler)
    govde = html_yap(y["metin"], {u["link"] for u in urunler})
    simdi = datetime.now()
    cikti = {
        "ok": True,
        "uretim_zamani": simdi.isoformat(timespec="seconds"),
        "yazar": "Nevin Özdemir",
        "kose": "Market Dedektifi",
        "baslik": y["baslik"].strip(),
        "ozet_spot": y.get("spot", "").strip(),
        "html": govde,
        "markdown": y["metin"],
        "kullanilan_motor": "coarena",
        "urunler": urunler,
    }
    CIKTI.mkdir(parents=True, exist_ok=True)
    yol = CIKTI / f"{simdi:%Y%m%d_%H%M%S}.json"
    yol.write_text(json.dumps(cikti, ensure_ascii=False, indent=2), encoding="utf-8")
    durum = basincaya_gonder(cikti, yol)
    print(json.dumps({"baslik": cikti["baslik"], "kelime": len(y["metin"].split()), "basinca": durum}, ensure_ascii=False))
    return 0


def basinca_metni(markdown: str) -> str:
    """basınca köşe metni: dipci bağlantıları basınca'nın biçimiyle ⟦adres|yazı⟧ (yazının içinde tıklanır link olur)."""
    def bag(m):
        yazi, url = m.group(1), m.group(2)
        # basınca canlı sitesi ⟦adres|yazı⟧ işaretini çizebildiğinde açılır (BASINCA_LINK_ISARETI=1); yoksa düz yazı kalır.
        if url.startswith("https://dipci.tr/") and os.getenv("BASINCA_LINK_ISARETI", "0") == "1":
            return "⟦" + url + "|" + yazi + "⟧"
        return yazi
    t = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", bag, markdown)
    return t.replace("**", "").replace("__", "").strip()


def _motor(govde: dict) -> dict:
    site = os.getenv("BASINCA_SITE", "").strip().rstrip("/")
    anahtar = os.getenv("BASINCA_MOTOR_TOKEN", "").strip()
    if not site or not anahtar:
        raise RuntimeError("basınca motor anahtarı yok")
    req = urllib.request.Request(site + "/api/motor-yonet", data=json.dumps(govde, ensure_ascii=False).encode("utf-8"),
                                 method="POST", headers={"content-type": "application/json", "authorization": f"Bearer {anahtar}",
                                                         "user-agent": "dipci-nevin/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def basincaya_gonder(cikti: dict, yol: Path, basinca_id: int = 0) -> str:
    """Yazıyı basınca.com Market Dedektifi köşesine dipci KENDİSİ ekler (Kemal, 17 Kas 2026:
    "Bu yazıları dipçi ekleyecek"; MTR1'in kullandığı motor kapısı /api/motor-yonet, bölüm kose-yazilari)."""
    try:
        yazarlar = _motor({"koleksiyon": "kose-yazarlari", "islem": "listele", "limit": 100}).get("kayitlar") or []
        yazar = next((y for y in yazarlar if "DEDEKT" in str(y.get("kose", "")).upper().replace("İ", "I") and y.get("durum") == "aktif"), None)
        if not yazar:
            return "hata:Market Dedektifi yazari yok"
        veri = {"yazarId": yazar["id"], "baslik": cikti["baslik"], "spot": cikti["ozet_spot"],
                "metin": basinca_metni(cikti["markdown"]), "durum": "yayinda"}
        mevcut = basinca_id or int(cikti.get("basinca_id") or 0)
        if mevcut:
            r = _motor({"koleksiyon": "kose-yazilari", "islem": "guncelle", "id": mevcut, "veri": veri})
            yeni = mevcut
        else:
            r = _motor({"koleksiyon": "kose-yazilari", "islem": "ekle", "veri": veri})
            yeni = int(r.get("id") or 0)
        if not r.get("ok", True) or not yeni:
            return "hata:" + str(r)[:150]
        cikti["basinca_id"] = yeni
        yol.write_text(json.dumps(cikti, ensure_ascii=False, indent=2), encoding="utf-8")
        return f"yayinda:{yeni}"
    except Exception as e:
        return f"hata:{str(e)[:150]}"

if __name__ == "__main__":
    sys.exit(main())
