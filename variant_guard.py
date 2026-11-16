"""
dipci.tr — Ürün Varyant Koruması

Aynı marka ve gramajdaki FARKLI ürünlerin (ör. "Pınar %0,5 Yağlı Süt", "Pınar Yarım Yağlı Süt",
"Pınar Laktozsuz Süt") tek ürün gibi karşılaştırılmasını engeller. Yanlış eşleşme hem fiyat
karşılaştırmasını hem de "gerçek indirim" hesabını bozar.

Kural: Referans ürün adı ile aday mağaza başlığının varyant imzası (yağ oranı + ayırt edici
özellikler) BİREBİR aynı olmalı. "Tam yağlı" varsayılan kabul edilir (imzaya girmez).
"""

import re
import sqlite3
from typing import FrozenSet, Iterable, Optional


def _norm(text: str) -> str:
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


# (imza etiketi, regex) — normalize edilmiş (ascii, küçük harf) metin üzerinde aranır
_FLAG_PATTERNS = [
    ("laktozsuz", r"\blaktoz\s*suz\b|\blaktozsuz\b"),
    ("protein", r"\bprotein\w*"),
    ("organik", r"\borganik\b"),
    ("glutensiz", r"\bglutensiz\b"),
    ("sekersiz", r"\bsekersiz\b|\bilave\s+seker\s+icermez\b"),
    ("light", r"\blight\b|\blite\b"),
    ("zero", r"\bzero\b"),
    ("keci", r"\bkeci\b"),
    ("koyun", r"\bkoyun\b"),
    ("manda", r"\bmanda\b"),
    ("kakao", r"\bkakao\w*"),
    ("cikolata", r"\bcikolata\w*"),
    ("cilek", r"\bcilek\w*"),
    ("muz", r"\bmuz(?:lu)?\b"),
    ("vanilya", r"\bvanilya\w*"),
    ("karamel", r"\bkaramel\w*"),
    ("cocuk", r"\bcocuk\w*|\bkids?\b|\bkido\b"),
    ("barista", r"\bbarista\b"),
]
_FLAG_RE = [(tag, re.compile(rx)) for tag, rx in _FLAG_PATTERNS]


_DAIRY_RE = re.compile(r"\b(?:sut|sutu|yogurt|peynir\w*|ayran|kefir|krema|kaymak|tereyag\w*|labne|lor)\b")


def _fat_level(t: str) -> Optional[str]:
    """Süt ürünlerinde yağ seviyesini döndürür; tam yağlı/belirtilmemiş -> None.
    (Süt ürünü olmayan metinlerde, ör. "yağsız fritöz", yağ seviyesi aranmaz.)"""
    if not _DAIRY_RE.search(t):
        return None
    if re.search(r"\byagsiz\b", t):
        return "yagsiz"
    if re.search(r"\byarim\s*yag", t):
        return "yarim"
    if re.search(r"\b(?:az|dusuk)\s*yag", t):
        return "az"
    m = re.search(r"%\s*(\d+(?:[.,]\d+)?)\s*(?:yag|ya[gğ]li)", t) or re.search(r"(\d+(?:[.,]\d+)?)\s*%\s*yag", t)
    if m:
        pct = float(m.group(1).replace(",", "."))
        if pct <= 0.1:
            return "yagsiz"
        if pct < 1.2:
            return "az"
        if pct < 2.5:
            return "yarim"
    return None


def variant_signature(text: str) -> FrozenSet[str]:
    t = _norm(text)
    sig = {tag for tag, rx in _FLAG_RE if rx.search(t)}
    fat = _fat_level(t)
    if fat:
        sig.add(f"yag:{fat}")
    return frozenset(sig)


def is_same_variant(reference_text: str, candidate_text: str) -> bool:
    return variant_signature(reference_text) == variant_signature(candidate_text)


def purge_variant_mismatches(conn: sqlite3.Connection, product_keys: Optional[Iterable[str]] = None) -> list:
    """
    Ürünün kendi adıyla varyantı uyuşmayan mağaza fiyat kayıtlarını siler.
    Silinen kayıtların ürün anahtarlarını döndürür (katalog indeksi yeniden kurulmalı).
    """
    keys = list(product_keys) if product_keys else None
    if keys:
        ph = ",".join("?" for _ in keys)
        prods = conn.execute(
            f"SELECT product_key, brand, family_name FROM canonical_products WHERE product_key IN ({ph})", keys
        ).fetchall()
    else:
        prods = conn.execute("SELECT product_key, brand, family_name FROM canonical_products").fetchall()

    touched = []
    for p in prods:
        ref_sig = variant_signature(f"{p['brand']} {p['family_name']}")
        rows = conn.execute(
            "SELECT id, title FROM price_snapshots WHERE product_key = ? AND seller_name NOT LIKE '%Referans%'",
            (p["product_key"],),
        ).fetchall()
        bad = [r["id"] for r in rows if variant_signature(r["title"]) != ref_sig]
        if bad:
            for i in range(0, len(bad), 500):
                chunk = bad[i : i + 500]
                conn.execute(f"DELETE FROM price_snapshots WHERE id IN ({','.join('?' for _ in chunk)})", chunk)
            touched.append(p["product_key"])
    conn.commit()
    return touched


def purge_synthetic_reference_history(conn: sqlite3.Connection) -> int:
    """Eskiden yeni ürünler için uydurulan geriye tarihli referans kayıtlarını siler
    ('Piyasa Medyan Referansı', 'Piyasa Referansı')."""
    cur = conn.execute(
        "DELETE FROM price_snapshots WHERE seller_name IN ('Piyasa Medyan Referansı', 'Piyasa Referansı')"
    )
    conn.commit()
    return cur.rowcount or 0


if __name__ == "__main__":
    # Tek seferlik temizlik: python variant_guard.py [veritabanı_yolu]
    import os
    import sys

    from memory_db import PriceMemoryDB

    path = sys.argv[1] if len(sys.argv) > 1 else os.getenv(
        "DIPCI_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "dipci_platform.db")
    )
    db = PriceMemoryDB(path)
    removed_refs = purge_synthetic_reference_history(db.conn)
    touched = purge_variant_mismatches(db.conn)
    db.rebuild_catalog_index()
    print(f"Uydurma referans kaydı silindi: {removed_refs}")
    print(f"Varyantı karışmış ürün düzeltildi: {len(touched)}")
