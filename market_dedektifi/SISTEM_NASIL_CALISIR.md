# Market Dedektifi — Köşe Yazarı ve Tavsiye Sistemi Nasıl Çalışır?

Bu klasör, **`https://dipci.tr`** üzerindeki canlı fiyat veri tabanından beslenerek hem **`basinca.com`** haber/köşe yazıları hem de takipçi tavsiyeleri üreten bağımsız **Market Dedektifi Köşe Yazarı** sistemidir.

---

## 1. Sistemin 5 Adımlı Çalışma Mantığı

```
[1. dipci.tr Canlı Veri Havuzu]
   19 market & e-ticaret sitesindeki 2.400+ ürün, 4.350+ canlı fiyat,
   15 dakikalık gerçek indirim radarı ve market sepeti hesaplayıcısı
          │
          ▼
[2. Canlı Veri İstemcisi (dipci_istemci.py)]
   Seçilen kategori veya konuya göre gerçek indirimleri (30g medyan altı),
   mağazalar arası fahiş fiyat farklarını ve en ucuz market sepetini çeker.
   (Yazar hiçbir rakamı kafasından uyduramaz — %100 gerçek veri!)
          │
          ▼
[3. Etik Duvar & Gelir Ortaklığı Motoru (affiliate_motoru.py)]
   • Önce tamamen bağımsız olarak EN UCUZ mağaza seçilir.
   • En ucuz yer BİM/A101/ŞOK ise dürüstçe "Marketten alın" yazar.
   • En ucuz yer Amazon, Hepsiburada, MediaMarkt, Teknosa, DeFacto vb. ise
     otomatik olarak sizin gelir ortaklığı (affiliate) kodunuzu linke ekler.
          │
          ▼
[4. Köşe Yazarı Üretim Motoru (kose_yazari.py)]
   • İster API anahtarı olmadan (0 maliyetle, 1 saniyede) yerleşik Türkçe
     editöryal motorla yayına hazır köşe yazısı yazar,
   • İster ayarlara Gemini / DeepSeek / OpenAI anahtarı girdiğinizde verileri
     "Market Dedektifi" üslubuyla hikayeleştirir.
          │
          ▼
[5. Çoklu Yayın Çıktısı (uretilen_yazilar/)]
   Her üretimde aynı anda 4 hazır çıktı verir:
   1. basinca.com yönetim paneline doğrudan yapıştırılabilir temiz HTML + Tablo
   2. Markdown metni (.md)
   3. X (Twitter) / Telegram anlık özet metni
   4. Instagram gönderi açıklaması
```

---

## 2. Üretebildiği 4 Farklı Köşe Yazısı Formatı

1. **Haftalık / Günlük Ana Köşe Yazısı (`genel_kose`):**
   - *30 Günlük Ortalamanın Altına İnen Gerçek Yıldızlar* + *Haftanın Market Sepeti Savaşı (BİM vs A101 vs ŞOK vs Migros vs CarrefourSA)* + *Aynı Ürüne İki Kat Ödemeyin Uyarı Listesi* + *Mutfak Aletleri & Teknoloji Radarı*.
2. **Kategori Özel Tavsiye Rehberi (`kategori_rehberi`):**
   - Seçtiğiniz 8 kategoriden birine (*Gıda*, *Temizlik*, *Kişisel Bakım*, *Anne & Bebek*, *Ev & Mutfak*, *Teknoloji & Beyaz Eşya*, *Sağlık & Vitamin*, *Evcil Hayvan*) özel tavsiye ve fiyat karşılaştırma yazısı.
3. **Market Sepeti Savaşı (`sepet_savasi`):**
   - Temel ev ihtiyaçları listesini tüm zincir marketlerde kuruşu kuruşuna yarıştırır; hem tek market şampiyonunu hem de "sepeti iki markete bölerek ekstra tasarruf" rotasını tablo halinde yazar.
4. **Tek Ürün Derin Fiyat Röntgeni (`urun_rontgeni`):**
   - Yazdığınız herhangi bir ürünün (örn. *Çaykur Filiz*, *Karaca Hatır*, *Persil*, *iPhone 16*) tüm mağazalardaki fiyatlarını ve birim maliyetini tek başına haber içeriğine dönüştürür.

---

## 3. Nasıl Kullanılır? (3 Farklı Yol)

### Yol A: Görsel Editör Stüdyosu (En Kolayı — Çift Tıkla Çalıştır)
Klasördeki **`Baslat_Yazar_Studyo.bat`** dosyasına çift tıklayın. Tarayıcınızda otomatik olarak `http://127.0.0.1:8095` açılır:
- Sol menüden yazı formatını ve kategoriyi seçip **"Canlı Verilerle Köşe Yazısını Hazırla"** butonuna basın.
- Sağ üstteki **"Gelir Ortaklığı & AI Ayarları"** butonundan onay aldığınız *Admitad*, *ReklamAction*, *Awin* kodlarınızı (Amazon `marketdedekti-21` zaten tanımlıdır) ve isterseniz *Gemini / DeepSeek* anahtarınızı kaydedin.

### Yol B: Komut Satırı / Zamanlanmış Görev (Otomasyon İçin)
```powershell
# 1. Haftalık genel köşe yazısı üret:
python kose_yazari.py --tur genel_kose

# 2. Belirli bir kategoride tavsiye yazısı üret:
python kose_yazari.py --tur kategori_rehberi --kategori anne_bebek

# 3. Bu hafta hangi market daha ucuz sepet yazısı üret:
python kose_yazari.py --tur sepet_savasi

# 4. Tek bir ürün için fiyat röntgeni yazısı üret:
python kose_yazari.py --tur urun_rontgeni --sorgu "Karaca Hatır"
```

### Yol C: `basinca.com` veya RBT Robotuna Otomatik Bağlama
`ayarlar.json` içindeki `"basinca_webhook_url"` alanına haber sitenizin veya RBT robotunuzun uç noktasını girdiğinizde, üretilen her yazı başlığı, spotu ve hazır HTML gövdesiyle otomatik olarak sitenize POST edilir.

---

## 4. Klasördeki Dosyalar
- **`Baslat_Yazar_Studyo.bat`** — Çift tıklamalı görsel editör başlatıcısı.
- **`studyo_arayuz.py`** — Tarayıcı tabanlı köşe yazarı stüdyosu (`127.0.0.1:8095`).
- **`kose_yazari.py`** — Ana köşe yazarı, AI entegrasyonu ve HTML/Markdown dönüştürücü.
- **`dipci_istemci.py`** — `https://dipci.tr` canlı API veri çekicisi.
- **`affiliate_motoru.py`** — Amazon, Admitad, ReklamAction, Awin ve UTM gelir ortaklığı link dönüştürücüsü.
- **`ayarlar.json`** — Gelir ortaklığı kodları, yayın platformu ve AI anahtarı ayar dosyası.
- **`uretilen_yazilar/`** — Üretilen tüm yazıların `.md`, `.html` ve `.json` arşiv klasörü.
