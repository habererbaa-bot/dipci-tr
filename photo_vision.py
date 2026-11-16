"""
dipci.tr — Reyon Fotoğrafından AI Raf Etiketi & Ürün Okuma Motoru
- GEMINI_API_KEY ortam değişkeni tanımlıysa Gemini 2.5 Flash multimodal API ile
  fotoğraftaki markayı, ürün adını, gramajı, barkodu ve raf etiket fiyatını milisaniyeler içinde çıkarır.
- Tarayıcı BarcodeDetector API'sinin fotoğraftan okuduğu barkodu veya kullanıcının ipucu metnini birleştirir.
"""

import base64
import json
import os
import re
import urllib.request
from typing import Dict, Optional


def analyze_shelf_photo_base64(
    image_base64: str,
    mime_type: str = "image/jpeg",
    detected_gtin: str = "",
    hint_text: str = "",
) -> Dict:
    """
    Fotoğraftan çıkarılan yapılandırılmış gözlemi döner:
    {
      "ok": bool,
      "mode": "gemini_vision" | "barcode_hybrid" | "hint_fallback",
      "brand": str,
      "product_name": str,
      "gtin": str,
      "net_amount_value": Optional[float],
      "model_code": str,
      "shelf_price_tl": Optional[float],
      "printed_unit_price": Optional[float],
      "note": str
    }
    """
    clean_b64 = (image_base64 or "").strip()
    if "," in clean_b64 and clean_b64.startswith("data:"):
        header, clean_b64 = clean_b64.split(",", 1)
        m_mime = re.search(r"data:([^;]+);base64", header)
        if m_mime:
            mime_type = m_mime.group(1)

    clean_gtin = "".join(ch for ch in (detected_gtin or "") if ch.isdigit())
    api_key = os.getenv("GEMINI_API_KEY", "").strip()

    if api_key and clean_b64:
        try:
            # Boyut doğrulama (en fazla ~6 MB base64)
            raw_bytes = base64.b64decode(clean_b64[:8_000_000], validate=False)
            if len(raw_bytes) > 6_000_000:
                return {"ok": False, "error": "Fotoğraf boyutu çok büyük (maksimum 5 MB)."}

            prompt = (
                "Sen Türkiye market reyonlarındaki ürün ambalajlarını ve raf fiyat etiketlerini okuyan "
                "uzman bir optik denetçisin. Görseldeki ürünü ve varsa raf etiketini oku ve SADECE şu JSON şemasında yanıt ver:\n"
                '{"brand": "Marka adı", "product_name": "Ürün tam adı", "gtin": "Varsa 8-13 haneli barkod rakamları", '
                '"net_amount_value": 1.0, "base_unit_guess": "kg|litre|adet|yıkama", '
                '"model_code": "Elektronikse model kodu", "shelf_price_tl": 0.0, "printed_unit_price": 0.0}\n'
                "Önemli: Gramaj 600g ise net_amount_value 0.6 (kg), 750ml ise 0.75 (litre), 50 kapsül ise 50 (yıkama/adet) yaz. "
                "Fiyat görünmüyorsa null yaz."
            )
            payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": prompt},
                            {"inline_data": {"mime_type": mime_type or "image/jpeg", "data": clean_b64}},
                        ]
                    }
                ],
                "generationConfig": {
                    "temperature": 0.1,
                    "responseMimeType": "application/json",
                },
            }
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = json.loads(resp.read().decode("utf-8", errors="replace"))
            cand_text = (
                body.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "{}")
            )
            parsed = json.loads(cand_text)
            shelf_p = parsed.get("shelf_price_tl")
            unit_p = parsed.get("printed_unit_price")
            net_v = parsed.get("net_amount_value")
            return {
                "ok": True,
                "mode": "gemini_vision",
                "brand": str(parsed.get("brand") or "").strip(),
                "product_name": str(parsed.get("product_name") or "").strip(),
                "gtin": clean_gtin or "".join(ch for ch in str(parsed.get("gtin") or "") if ch.isdigit()),
                "net_amount_value": float(net_v) if isinstance(net_v, (int, float)) and net_v > 0 else None,
                "model_code": str(parsed.get("model_code") or "").strip(),
                "shelf_price_tl": float(shelf_p) if isinstance(shelf_p, (int, float)) and shelf_p > 0 else None,
                "printed_unit_price": float(unit_p) if isinstance(unit_p, (int, float)) and unit_p > 0 else None,
                "note": "Gemini Vision AI ile raf etiketi ve ambalaj okundu.",
            }
        except Exception as e:
            if not clean_gtin and not hint_text.strip():
                return {"ok": False, "error": f"AI görsel okuma hatası: {e}"}

    if clean_gtin:
        return {
            "ok": True,
            "mode": "barcode_hybrid",
            "brand": "",
            "product_name": hint_text.strip(),
            "gtin": clean_gtin,
            "net_amount_value": None,
            "model_code": "",
            "shelf_price_tl": None,
            "printed_unit_price": None,
            "note": f"Fotoğraftaki barkod ({clean_gtin}) optik olarak çözümlendi.",
        }

    if hint_text.strip():
        parts = hint_text.strip().split(maxsplit=1)
        brand_guess = parts[0]
        prod_guess = parts[1] if len(parts) > 1 else parts[0]
        return {
            "ok": True,
            "mode": "hint_fallback",
            "brand": brand_guess,
            "product_name": prod_guess,
            "gtin": "",
            "net_amount_value": None,
            "model_code": "",
            "shelf_price_tl": None,
            "printed_unit_price": None,
            "note": "Görsel ipucu üzerinden katalog eşleştirmesi yapıldı.",
        }

    return {
        "ok": False,
        "needs_hint": True,
        "error": (
            "Fotoğrafta net bir barkod tespit edilemedi. Ürün adını/markasını kutucuğa yazarak "
            "veya barkodu yakından çekerek tekrar deneyin."
        ),
    }
