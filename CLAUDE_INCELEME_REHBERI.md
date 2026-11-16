# dipci.tr — Sistem Mimarisi, Tasarım ve Kod İnceleme Rehberi

**Canlı Adres:** `https://dipci.tr`  
**Sunucu Konumu:** `/opt/dipci` (Ubuntu, `systemd` servisleri: `dipci.service`, `dipci-radar.timer`, `dipci-sync.timer`)  
**Yerel Proje Klasörü:** `C:\Users\HP\.gemini\antigravity\scratch\market-dedektifi`

---

## 1. Projenin Amacı
`dipci.tr`, Türkiye'deki zincir marketlerin (BİM, A101, ŞOK, Migros, CarrefourSA, Tarım Kredi, Hakmar) ve e-ticaret pazaryerlerinin (Amazon, Hepsiburada, Trendyol, MediaMarkt, Teknosa, Vatan, n11, Pazarama, PttAVM, İdefix) fiyatlarını **hem kasa (paket) fiyatı hem de birim maliyet (TL/kg, TL/litre, TL/adet)** bazında karşılaştıran, sahte indirimleri ve gizli gramaj küçültmeyi (shrinkflation) yakalayan hibrit SSR + SPA fiyat karşılaştırma platformudur.

---

## 2. Ana Dosyalar ve Görevleri

### Ön Yüz (Tasarım ve Kullanıcı Deneyimi)
- **`static/index.html`** — Tüm ön yüz tasarımını ve istemci tarafı mantığını barındıran tek sayfa uygulama (SPA) şablonu:
  - `/` (`renderVitrinView`): 8 ana kategori, mağaza/marka/fiyat aralığı/gerçek indirim filtreleri, 15 dakikalık canlı indirim vitrini ve ürün kartları.
  - `/urun/<slug>` & `/barkod/<gtin>` (`renderXrayView`): Mağaza fiyat tablosu, 90 günlük fiyat geçmişi SVG grafiği, birim maliyet analizi.
  - `/firsatlar` (`renderDealsView`): Algoritmik gerçek indirim radarı + doğrulanmış üye fırsatları.
  - `/sepet` (`renderBasketView`): Evin temel ihtiyaçları market sepeti optimizasyonu.
  - `/kendi-sepetim` (`renderCustomBasketView`): Markalı sepet oluşturma, muadil tamamlama, online teslimat alt limit/ücret hesabı ve karma sepet bölüştürme.
  - `/lens` (`renderScannerView`): Mobil kamera / fotoğraf yükleme (multipart binary upload) ve barkod ile canlı ürün/fiyat bulucu.

### Arka Uç (FastAPI, Veri Tabanı, Algoritmalar ve SEO)
- **`web_app.py`** — Ana FastAPI uygulaması; tüm REST API uç noktaları (`/api/products`, `/api/price-drops`, `/api/dedektif/firsatlar`, `/api/alerts`, `/api/scan/lens-upload`, `/api/custom-basket/compare` vb.) ve Googlebot SSR rotaları.
- **`memory_db.py`** — SQLite (WAL modu) veri katmanı: `canonical_products`, `product_barcodes`, `price_snapshots` (90 günlük fiyat hafızası) ve hızlı filtreleme için özet indeks tablosu `product_catalog_index` (gramaj uyuşmazlığı ve stok dışı fahiş fiyat ayıklama filtreleriyle birlikte).
- **`auto_catalog.py`** — Otomatik katalog genişletme motoru:
  1. MarketFiyatı çok sayfalı zincir market hasadı,
  2. Online pazaryeri ürün keşfi ve kalıcı `/urun/<slug>` sayfası üretimi,
  3. Akakçe / Cimri / Google Merchant / ReklamAction uyumlu evrensel XML besleme okuyucusu (`ingest_partner_xml`).
- **`fast_radar.py`** — 15 dakikada bir çalışan (`dipci-radar.timer`) hızlı fiyat radarı:
  - Aktif kullanıcı alarmlarını (`priority=100`) öncelikli tarar,
  - 30 günlük medyanın %10–%45 altına inen gerçek indirimleri (`live_price_drops`) tespit eder, RSS ve Webhook/Telegram çıkışı üretir.
- **`live_sync.py`** — 4 saatte bir çalışan (`dipci-sync.timer`) tam kapsamlı market + XML fiyat senkronizasyonu ve IndexNow arama motoru bildirimcisi.
- **`lens_engine.py` & `photo_vision.py` & `scanner.py`** — Gemini 2.5 Flash görsel ürün tanıma, EAN-13 barkod doğrulama ve reyon etiketi suistimal/manipülasyon kalkanı.
- **`audit_engine.py`** — 7 katmanlı Market Dedektifi fiyat/birim maliyet/taksit reel maliyet (NPV) denetim motoru.
- **`basket_optimizer.py` & `custom_basket_engine.py` & `store_accounts.py`** — Tek market vs bölünmüş karma rota optimizasyonu, bulunurluk sıralaması, muadil eşleştirme ve teslimat ücreti hesaplayıcısı.
- **`seo_engine.py`** — Dinamik `Product` / `AggregateOffer` JSON-LD şemaları, SSR HTML enjeksiyonu, parçalı `sitemap.xml` ve `llms.txt` üreticisi.
- **`price_alerts.py`** — Kasa fiyatı (TL) ve birim fiyat bazlı anlık e-posta + tarayıcı bildirimi alarm servisi.
- **`social_community.py` & `email_auth.py` & `netgsm_sms.py`** — Google OAuth, E-posta OTP ve Netgsm SMS doğrulamalı üyelik ve topluluk katmanı.
- **`affiliate.py` & `config.py`** — Etik duvar ilkeli (algoritma puanlamasından bağımsız) gelir ortaklığı bağlantı dönüştürücüsü.

---

## 3. İncelemede Odaklanılması İstenen Başlıklar
1. **Tasarım & UX (`static/index.html`):** Kurumsal sadelik, mobil kullanılabilirlik, filtreleme ergonomisi ve sayfa yüklenme performansı (`Tailwind CDN` yerine derlenmiş CSS geçişi vb.).
2. **Mimari & Ölçeklenebilirlik (`web_app.py`, `memory_db.py`, `auto_catalog.py`):** Ürün sayısı 2.400'den 100.000+ seviyesine çıktığında SQLite WAL -> PostgreSQL geçiş noktaları, eşzamanlı yazma kilitleri ve önbellekleme.
3. **Veri Doğruluğu & Eşleştirme:** Farklı marketlerdeki aynı ürünün gramaj/paket varyantlarını (`500g` vs `1kg`, tekli vs çoklu paket) eşleştirme isabeti.
4. **Ticari & Mağaza Entegrasyonu:** Mağaza XML kabulü, tıklama sayacı (CPC) doğrulaması, güvenlik ve SEO derinliği.
