"""
MARKET DEDEKTİFİ — köşe yazarı Nevin Özdemir (Kemal, 12 ve 17 Kas 2026).

Tarif MTR1'deki src/yazar/marketDedektifi.ts ile aynı: içten, "Dostlar" diye seslenen, 35 yaşlarında tatlı dilli
bir kadın; madde listesi, tablo, başlıklı bölüm yok; düz bir köşe yazısı. Fark: rakamlar dipci.tr'nin kendi
verisinden gelir ve önerdiği ürünlerin adı yazının içinde dipci.tr sayfasına küçük, doğal bir bağlantı olur.

Model: yalnız ÜCRETSİZ Gemini anahtarı (GEMINI_UCRETSIZ_KEY). Ücretli anahtara düşmez; cevap yoksa o gün yazı yok.
Çıktı: /opt/dipci/data/dedektif/<zaman>.json  → https://dipci.tr/api/dedektif/gunun-yazisi
"""
import html
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ORIGIN = os.getenv("DIPCI_ORIGIN", "https://dipci.tr")
CIKTI = Path(os.getenv("DEDEKTIF_CIKTI_KLASORU") or (Path(__file__).resolve().parent / "uretilen_yazilar"))
MODEL = os.getenv("NEVIN_GEMINI_MODEL", "gemini-3.8-flash")

# Her türden birkaç ürün: şok indirim, gerçek indirim, fiyat farkı, indirim marketi, online kampanya...
TURLER = [("sok", 3), ("dip", 3), ("fark", 2), ("indirim-marketi", 2), ("kampanya", 1), ("amazon", 1),
          ("temizlik-bakim", 2), ("teknoloji-ev", 2)]

KARAKTER = "\n".join([
    "Sen Nevin Özdemir'sin; basınca.com'da \"MARKET DEDEKTİFİ\" köşesini yazıyorsun. 35 yaşlarında, tatlı dilli bir kadınsın.",
    "Okurlarına \"Dostlar\" diye seslenirsin; içten, candan, bir abla bir kardeş gibi konuşursun. Ev ekonomisini bilen, pazarlığı seven, kandırılmaya gelmeyen bir kadınsın.",
    "Anlattığını gündelik hayattan küçük örneklerle (mahalledeki manav, mutfak, çocukların okul alışverişi gibi) somutlaştırırsın; yerinde bir deyime başvurur, zaman zaman bir atasözüyle sözünü güçlendirirsin — ama her paragrafa doldurmazsın, doğal kalırsın.",
    "Örneklerinde KİŞİ ADI kullanmazsın (\"bir komşum\", \"bir arkadaşım\" dersin).",
    "Yapay zekâ gibi yazmazsın: madde listesi, tablo, başlıklı bölüm, kalın yazı, \"sonuç olarak\", \"unutmayın ki\", \"özetle\" gibi kalıplar yok. Cümle uzunlukların değişir; bazen kısa bir cümleyle vurgu yaparsın.",
    "YALNIZ sana verilen ürün, fiyat ve mağaza bilgilerini kullanırsın; fiyat, kampanya, mağaza ya da indirim UYDURMAZSIN.",
    "Fiyatların gün içinde değişebileceğini bir yerde doğal bir dille hatırlatırsın. Marka kötülemez, hakaret etmezsin.",
])


def _get(path: str):
    req = urllib.request.Request(ORIGIN + path, headers={"User-Agent": "dipci-nevin/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


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
            if link in gorulen:
                continue
            gorulen.add(link)
            secilen.append({
                "tur": it.get("etiket") or tur,
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
    anahtar = os.getenv("GEMINI_UCRETSIZ_KEY", "").strip()
    if not anahtar:
        raise RuntimeError("GEMINI_UCRETSIZ_KEY yok")
    govde = json.dumps({
        "systemInstruction": {"parts": [{"text": sistem}]},
        "contents": [{"role": "user", "parts": [{"text": soru}]}],
        "generationConfig": {"maxOutputTokens": 20000, "temperature": 0.95, "responseMimeType": "application/json"},
    }).encode("utf-8")
    for i in range(5):
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
            data=govde, method="POST",
            headers={"content-type": "application/json", "x-goog-api-key": anahtar},
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                j = json.loads(r.read().decode("utf-8"))
            return "".join(p.get("text", "") for p in j["candidates"][0]["content"]["parts"])
        except urllib.error.HTTPError as e:
            if e.code in (429, 503):
                time.sleep(20 * (i + 1))
                continue
            raise
    raise RuntimeError("Gemini ücretsiz: yoğunluk geçmedi")


def yaz(urunler):
    tarih = datetime.now().strftime("%d.%m.%Y")
    bilgi = "\n".join(
        f"- {u['ad']} [{u['tur']}]: {u['durum']} Mağazalar: {u['magazalar']}. BAĞLANTI: {u['link']}" for u in urunler
    )
    soru = "\n".join([
        f"Bugün {tarih}. Sabah dipci.tr'de market ve mağaza fiyatlarını taradın, şunları buldun:",
        bilgi,
        "Bunlardan gıdada, temizlikte, teknolojide ve günlük ihtiyaçlarda gerçekten hesaplı olanları anlatan bir köşe yazısı yaz (550–800 kelime). \"Dostlar,\" diye başla.",
        "Bugün birden ucuzlayan (şok indirim) ürünleri, aynı ürünün mağazalar arasındaki fiyat farkını ve kampanyaları dedektif gözüyle tart. Hepsini saymak zorunda değilsin; en anlamlı 5-7 tanesini seç.",
        "Bahsettiğin her ürünün adını İLK geçtiği yerde markdown bağlantısı yap: [ürün adı](BAĞLANTI). Bağlantıyı aynen kullan, değiştirme. Başka bağlantı ekleme.",
        "Başlık kısa ve merak uyandırıcı olsun (büyük harf değil). Spot tek cümle.",
        "Metinde paragraflar arasında boş satır bırak. Yalnız JSON: {\"baslik\": \"\", \"spot\": \"\", \"metin\": \"\"}",
    ])
    cevap = gemini(KARAKTER, soru)
    y = json.loads(cevap[cevap.index("{"): cevap.rindex("}") + 1])
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
        "kullanilan_motor": f"gemini-ucretsiz:{MODEL}",
        "urunler": urunler,
    }
    CIKTI.mkdir(parents=True, exist_ok=True)
    (CIKTI / f"{simdi:%Y%m%d_%H%M%S}.json").write_text(json.dumps(cikti, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"baslik": cikti["baslik"], "kelime": len(y["metin"].split())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
