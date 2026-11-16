"""
Market Dedektifi — basinca.com Yapay Zeka Köşe Yazarı Persona ve İçerik Üretim Motoru
Karakter: 35 yaşında, analitik zekâsı yüksek, hesap makinesi elinde ama dili komşu kahvesi
kadar sıcak ve samimi bir anne. Asla uydurma rakam yazmaz; sadece AuditEngine'in kanıtladığı
verileri hikayeleştirir.
"""

import json
from dataclasses import asdict
from typing import List

from affiliate import AffiliateLinkBuilder
from audit_engine import AuditFinding

MARKET_DEDEKTIFI_SYSTEM_PROMPT = """Sen basinca.com yeni nesil dijital haber platformunun sevilen alışveriş ve ev ekonomisi köşe yazarı **"Market Dedektifi"**sin (35 yaşında, çalışan, bir çocuk annesi, mutfak ve ev bütçesini kuruşu kuruşuna yöneten, analitik zekâsı çok güçlü ama üslubu son derece sıcak, samimi, esprili ve bizden biri).

### KESİN KURALLARIN (ASLA İHLAL ETME):
1. **Sıfır Halüsinasyon (Veriye Sadakat):** Sana JSON olarak verilen `audit_findings` içindeki fiyat, gramaj, birim maliyet (TL/kg, TL/yıkama, TL/100 yaprak), gerçek indirim yüzdesi ve rakip market fiyatlarını kuruşu kuruşuna doğru kullan. Veride olmayan hiçbir fiyatı uydurma.
2. **Etik Bağımsızlık:** Komisyon linki olsun ya da olmasın okuyucunun menfaatini koru. BİM veya yerel market daha ucuzsa açıkça "İnternetten sipariş vermeyin, mahallenizdeki marketten alın" de.
3. **Birim Fiyat Odaklılık:** Okuyucuya her zaman paket fiyatının aldatmacasını göster; 1 yıkamanın, 1 yaprağın, 1 kilonun kaça geldiğini samimi bir dille anlat.
4. **Sahte İndirim & Gramaj İfşası:** `SAHTE_INDIRIM_TUZAGI`, `GRAMAJ_OYUNU_SHRINKFLATION`, `COKLU_ALIM_TUZAGI` ve `SATICI_VE_SAHTE_URUN_RISKI` etiketli ürünleri "Sakın Kanmayın!" bölümünde nedenleriyle deşifre et.
5. **Bölüm Yapısı:**
   - **Başlık:** Merak uyandıran, samimi ve rakam içeren vurucu bir köşe başlığı.
   - **Mutfaktan Merhaba:** 2-3 cümlelik sıcak anne/ev gözlemi.
   - **Haftanın Gerçek Yıldızları (Stoklamalık Fırsatlar):** Gerçekten dip fiyata inmiş ürünler ve birim fiyat kanıtları (varsa komisyonlu inceleme linkiyle).
   - **Dedektifin Kara Listesi ("Sakın Kanmayın!"):** Haftanın sahte indirimleri, gramajı küçültülenler, çoklu alım tuzakları ve sahte satıcı uyarıları.
   - **Muadil Köşesi:** Ünlü markaya servet ödemek istemeyenler için fiyat/performans market markası tüyosu.
   - **Evin Büyük İhtiyaçları (Teknoloji & Beyaz Eşya Radarı):** Vade farksız taksit avantajı (reel bugünkü maliyeti), garantisi ve model kodu doğrulanmış büyük fırsat.
   - **Şeffaflık Notu:** En altta yasal #işbirliği bilgilendirmesi.
"""


class MarketDedektifiColumnist:
    def __init__(self, link_builder: AffiliateLinkBuilder | None = None):
        self.link_builder = link_builder or AffiliateLinkBuilder()

    def prepare_llm_payload(self, findings: List[AuditFinding]) -> str:
        """
        basinca.com yapay zeka editör API'sine gönderilecek hazır veri paketini üretir.
        """
        enriched = []
        for f in findings:
            item = asdict(f)
            # Sadece gerçekten önerilen ürünlere tıklanabilir izleme linki eklenir
            if f.verdict in ("STOKLA_GERCEK_DIP_FIYAT", "AVANTAJLI_FIRSAT", "TAKSIT_AVANTAJLI_TEKNOLOJI"):
                item["tracking_url"] = self.link_builder.build_tracking_url(f.url)
            else:
                item["tracking_url"] = None
            enriched.append(item)

        return json.dumps(
            {
                "columnist": "Market Dedektifi",
                "platform": "basinca.com",
                "audit_findings": enriched,
                "legal_footer": self.link_builder.legal_disclosure_footer(),
            },
            ensure_ascii=False,
            indent=2,
        )

    def render_sample_column(self, findings: List[AuditFinding]) -> str:
        """Denetim sonuçlarından basinca.com yayınına hazır örnek köşe yazısı üretir."""
        winners = [f for f in findings if f.verdict in ("STOKLA_GERCEK_DIP_FIYAT", "AVANTAJLI_FIRSAT")]
        traps = [
            f for f in findings
            if f.verdict in (
                "SAHTE_INDIRIM_TUZAGI",
                "GRAMAJ_OYUNU_SHRINKFLATION",
                "COKLU_ALIM_TUZAGI",
                "SATICI_VE_SAHTE_URUN_RISKI",
            )
        ]
        tech = [f for f in findings if f.verdict == "TAKSIT_AVANTAJLI_TEKNOLOJI"]
        pl_tips = [f.private_label_tip for f in findings if f.private_label_tip]

        lines = [
            "# Bulaşık Kapsülünde 90 Günün Dip Fiyatı, Kaşar Peynirinde Gizli Gramaj Oyunu! (Haftanın Alışveriş Röntgeni)",
            "",
            "**Market Dedektifi | basinca.com**",
            "",
            "Sabah kahvemi koydum, oğlanı okula uğurladım ve her hafta olduğu gibi online marketlerin 'Dev İndirim!' afişlerinin arkasına büyütecimi tuttum. Açık söyleyeyim; bu hafta raflarda öyle kurnaz 'gramaj küçültme' ve 'sözde kampanya' tuzakları var ki hesap makinesi olmadan sepete el sürenin vay haline! Ama merak etmeyin, hem mutfak dolabını gerçekten ucuza dolduracağımız dip fiyatları hem de uzak durmanız gereken sahte indirimleri tek tek ayıkladım.",
            "",
            "---",
            "",
            "### 🌟 Haftanın Gerçek Yıldızları: Gönül Rahatlığıyla Stoklayın!",
            "",
        ]

        for w in winners:
            tracked_url = self.link_builder.build_tracking_url(w.url)
            lines.append(
                f"* **{w.product_name} ({w.platform_display}) — Dedektif Puanı: {w.detective_score}/100**"
            )
            lines.append(
                f"  Kasada paket başına **{w.effective_pack_price:.2f} TL**'ye, yani **1 {w.base_unit} başına sadece {w.effective_unit_price:.2f} TL**'ye geliyor!"
            )
            for note in w.evidence_notes:
                lines.append(f"  - *Kanıt:* {note}")
            lines.append(f"  - 🔗 [Ürünü İncelemek İçin Tıklayın]({tracked_url})")
            lines.append("")

        if pl_tips:
            lines.extend([
                "### 💡 Dedektifin Muadil Sırrı",
                f"> {pl_tips[0]}",
                "",
            ])

        lines.extend([
            "---",
            "",
            "### 🚨 Dedektifin Kara Listesi: Bu Hafta Sakın Kanmayın!",
            "",
        ])

        for t in traps:
            badge_map = {
                "SAHTE_INDIRIM_TUZAGI": "Sahte İndirim (Önce Bindir Sonra İndir)",
                "GRAMAJ_OYUNU_SHRINKFLATION": "Gizli Gramaj Küçültme (Shrinkflation)",
                "COKLU_ALIM_TUZAGI": "Çoklu Paket Tuzağı",
                "SATICI_VE_SAHTE_URUN_RISKI": "Şüpheli Satıcı / Sahte veya Yakın SKT Riski",
            }
            badge = badge_map.get(t.verdict, t.verdict)
            lines.append(f"* ❌ **{t.product_name} — {t.platform_display} ({badge})**")
            for note in t.evidence_notes:
                lines.append(f"  - {note}")
            lines.append(f"  - *Alternatif:* {t.next_rival_summary}")
            lines.append("")

        if tech:
            lines.extend([
                "---",
                "",
                "### 🔌 Evin Büyük İhtiyaçları: Teknoloji ve Beyaz Eşya Radarı",
                "",
            ])
            for tc in tech:
                tracked_url = self.link_builder.build_tracking_url(tc.url)
                lines.append(
                    f"* **{tc.product_name} ({tc.platform_display}) — Etiket: {tc.effective_pack_price:,.0f} TL | Reel Taksitli Maliyet: ~{tc.real_npv_price:,.0f} TL**"
                )
                for note in tc.evidence_notes:
                    lines.append(f"  - {note}")
                lines.append(f"  - 🔗 [Fırsatı İncelemek İçin Tıklayın]({tracked_url})")
                lines.append("")

        lines.extend([
            "---",
            self.link_builder.legal_disclosure_footer(),
        ])
        return "\n".join(lines)
