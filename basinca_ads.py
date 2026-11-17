"""
basinca.com için dipci.tr reklam kartları (Kemal, 17 Kas 2026).

İki reklam türü kodla, veritabanından çekilir:
  - "dip"      : Gerçek İndirimler — 30 günlük ortalamanın belirgin altına düşen market ürünleri.
  - "kampanya" : Online'da en ucuz — en ucuz fiyatı bir online mağazada (Amazon, Hepsiburada, n11...) olan ürünler.

Her reklam, basınca akış kartının 4:3 görsel kutusuna birebir oturan 1200x900 PNG ile gelir:
ürün görseli + 3-5 mağazanın fiyat sıralaması. Görselle yazı arasına katman/geçiş konmaz.
"""
import io
import json
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 900
CANONICAL = "https://dipci.tr"
ONLINE_PLATFORMS = ("amazon", "hepsiburada", "n11", "pttavm", "trendyol", "koctas", "pazarama", "idefix")
MARKET_PLATFORMS = ("migros", "carrefoursa", "a101", "sok", "bim", "hakmar", "tarimkredi", "getir")

# Mağaza adı, zemin rengi, yazı rengi (static/index.html BRAND_IDENTITY ile aynı)
BRANDS = {
    "amazon": ("Amazon", "#232F3E", "#FF9900"),
    "hepsiburada": ("Hepsiburada", "#FF6000", "#FFFFFF"),
    "migros": ("Migros", "#FF6600", "#FFFFFF"),
    "vatan": ("Vatan", "#004B93", "#FFFFFF"),
    "mediamarkt": ("MediaMarkt", "#DF0000", "#FFFFFF"),
    "carrefoursa": ("CarrefourSA", "#004E9E", "#FFFFFF"),
    "bim": ("BİM", "#E30613", "#FFFFFF"),
    "a101": ("A101", "#00A0DF", "#FFFFFF"),
    "sok": ("ŞOK", "#EAB308", "#0F172A"),
    "tarimkredi": ("Tarım Kredi", "#16A34A", "#FFFFFF"),
    "hakmar": ("Hakmar", "#7C3AED", "#FFFFFF"),
    "trendyol": ("Trendyol", "#F27A1A", "#FFFFFF"),
    "n11": ("n11", "#5D3EBC", "#FFFFFF"),
    "pazarama": ("Pazarama", "#E11D48", "#FFFFFF"),
    "teknosa": ("Teknosa", "#F97316", "#FFFFFF"),
    "pttavm": ("PttAVM", "#0284C7", "#FFFFFF"),
    "getir": ("Getir", "#5D3EBC", "#FFD300"),
    "idefix": ("İdefix", "#1E293B", "#FFFFFF"),
    "koctas": ("Koçtaş", "#EA580C", "#FFFFFF"),
    "ciceksepeti": ("Çiçeksepeti", "#2563EB", "#FFFFFF"),
}
DEFAULT_BRAND = ("Mağaza", "#475569", "#FFFFFF")

FONT_DIRS = ["/usr/share/fonts/truetype/dejavu", "C:/Windows/Fonts"]
_font_cache: Dict = {}


def _font(size: int, bold: bool = False):
    key = (size, bold)
    if key in _font_cache:
        return _font_cache[key]
    names = ["DejaVuSans-Bold.ttf", "arialbd.ttf"] if bold else ["DejaVuSans.ttf", "arial.ttf"]
    f = None
    for d in FONT_DIRS:
        for n in names:
            p = Path(d) / n
            if p.exists():
                f = ImageFont.truetype(str(p), size)
                break
        if f:
            break
    f = f or ImageFont.load_default()
    _font_cache[key] = f
    return f


def _tl(v: float) -> str:
    s = f"{float(v):,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".") + " TL"


def _clean_quotes(top_quotes: List[Dict], limit: int = 5) -> List[Dict]:
    """Aynı mağazayı bir kez al, en ucuza göre sırala; en ucuzun 3 katından pahalıyı (yanlış paket eşleşmesi) at."""
    seen, out = set(), []
    for q in sorted(top_quotes, key=lambda x: float(x.get("pack_price") or 0)):
        pl = str(q.get("platform") or "").lower()
        price = float(q.get("pack_price") or 0)
        if not pl or pl == "default" or price <= 0 or pl in seen:
            continue
        if out and price > float(out[0]["pack_price"]) * 3:
            continue
        seen.add(pl)
        out.append({"platform": pl, "pack_price": price})
        if len(out) >= limit:
            break
    return out


def _product_link(product_key: str, kind: str) -> str:
    slug = product_key.replace("_", "-")
    return f"{CANONICAL}/urun/{slug}?utm_source=basinca&utm_medium=reklam&utm_campaign={kind}"


def list_ads(db, fast_radar, kind: str = "dip", limit: int = 12) -> List[Dict]:
    """basınca akışına konacak reklam kayıtları. Ürünü en az 3 mağazada satılan, görseli olanlar."""
    limit = max(1, min(int(limit or 12), 40))
    rows = []
    if kind == "dip":
        drops = fast_radar.list_live_drops(category="", limit=120, min_drop_pct=15.0)
        keys = [d["product_key"] for d in drops if d.get("image_url")]
        drop_by_key = {d["product_key"]: d for d in drops}
        for k in keys:
            r = db.conn.execute("SELECT * FROM product_catalog_index WHERE product_key = ?", (k,)).fetchone()
            if r:
                rows.append((r, drop_by_key[k]))
    else:
        marks = ",".join("?" for _ in ONLINE_PLATFORMS)
        for r in db.conn.execute(
            f"""SELECT * FROM product_catalog_index
                WHERE offer_count >= 3 AND image_url != '' AND best_platform IN ({marks})
                ORDER BY savings_pct DESC LIMIT 150""",
            ONLINE_PLATFORMS,
        ).fetchall():
            rows.append((r, None))

    items = []
    for r, drop in rows:
        quotes = _clean_quotes(json.loads(r["top_quotes_json"] or "[]"))
        if len(quotes) < 3:
            continue
        best = quotes[0]
        worst = quotes[-1]
        best_name = BRANDS.get(best["platform"], DEFAULT_BRAND)[0]
        if kind == "dip":
            # Radar kaydı bayatsa (fiyat geri çıktıysa) reklam yapma.
            new_price = float(drop.get("new_price") or drop.get("new_pack_price") or 0)
            if not new_price or best["pack_price"] > new_price * 1.05:
                continue
            pct = round(float(drop.get("drop_pct") or 0))
            ozet = f"%{pct} indirim. En ucuz {best_name}: {_tl(best['pack_price'])}. {len(quotes)} mağazanın fiyatı dipci.tr'de."
        else:
            # Online fiyat gerçekten bir market fiyatından ucuz olmalı; yoksa paket eşleşmesi şüpheli.
            markets = [q for q in quotes if q["platform"] in MARKET_PLATFORMS]
            if not markets or best["platform"] not in ONLINE_PLATFORMS:
                continue
            gap_pct = round((1 - best["pack_price"] / markets[0]["pack_price"]) * 100)
            if gap_pct < 8:
                continue
            ozet = f"Online'da en ucuz {best_name}: {_tl(best['pack_price'])}. Market fiyatından %{gap_pct} ucuz."
        items.append(
            {
                "id": f"dipci-{kind}-{r['product_key']}",
                "tur": kind,
                "reklam": True,
                "baslik": f"{r['brand']} {r['family_name']}".strip(),
                "ozet": ozet,
                "gorsel": f"{CANONICAL}/reklam/basinca/{kind}/{r['product_key']}.png?v={datetime.now():%Y%m%d%H}",
                "link": _product_link(r["product_key"], kind),
                "en_ucuz": {"magaza": BRANDS.get(best["platform"], DEFAULT_BRAND)[0], "fiyat": best["pack_price"]},
                "magazalar": [
                    {"magaza": BRANDS.get(q["platform"], DEFAULT_BRAND)[0], "fiyat": q["pack_price"]} for q in quotes
                ],
            }
        )
        if len(items) >= limit:
            break
    return items


# ——— Görsel üretimi ———

_img_cache: Dict[str, tuple] = {}
_img_lock = threading.Lock()
CACHE_SECONDS = 1800


def _fetch_image(url: str) -> Optional[Image.Image]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (dipci-reklam)"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = resp.read(6_000_000)
        im = Image.open(io.BytesIO(data))
        im.load()
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
            bg.alpha_composite(im)
            im = bg
        return im.convert("RGB")
    except Exception:
        return None


def _wrap(draw, text: str, font, max_w: int, max_lines: int) -> List[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= max_w:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
            if len(lines) == max_lines:
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if len(lines) == max_lines and " ".join(lines) != text:
        last = lines[-1]
        while last and draw.textlength(last + "…", font=font) > max_w:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines


def render_card_png(db, fast_radar, kind: str, product_key: str) -> Optional[bytes]:
    ck = f"{kind}:{product_key}"
    with _img_lock:
        hit = _img_cache.get(ck)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]

    r = db.conn.execute("SELECT * FROM product_catalog_index WHERE product_key = ?", (product_key,)).fetchone()
    if not r:
        return None
    quotes = _clean_quotes(json.loads(r["top_quotes_json"] or "[]"))
    if not quotes:
        return None
    drop_pct = 0
    if kind == "dip":
        for d in fast_radar.list_live_drops(category="", limit=200, min_drop_pct=10.0):
            if d["product_key"] == product_key:
                drop_pct = round(float(d.get("drop_pct") or 0))
                break

    img = Image.new("RGB", (W, H), "#FFFFFF")
    dr = ImageDraw.Draw(img)

    # Üst şerit: marka + reklam türü
    dr.rectangle([0, 0, W, 96], fill="#0B132B")
    dr.text((44, 26), "dipci", font=_font(42, True), fill="#FFFFFF")
    dr.text((44 + dr.textlength("dipci", font=_font(42, True)), 26), ".tr", font=_font(42, True), fill="#10B981")
    tag = "GERÇEK İNDİRİM" if kind == "dip" else "ONLINE'DA EN UCUZ"
    tf = _font(26, True)
    tw = dr.textlength(tag, font=tf)
    dr.rounded_rectangle([W - 44 - tw - 36, 24, W - 44, 72], radius=24, fill="#10B981")
    dr.text((W - 44 - tw - 18, 33), tag, font=tf, fill="#FFFFFF")

    # Sol: ürün görseli (beyaz zeminde, katmansız)
    box = (44, 132, 524, 760)
    pic = _fetch_image(r["image_url"]) if r["image_url"] else None
    if pic:
        pic.thumbnail((box[2] - box[0], box[3] - box[1]), Image.LANCZOS)
        px = box[0] + (box[2] - box[0] - pic.width) // 2
        py = box[1] + (box[3] - box[1] - pic.height) // 2
        img.paste(pic, (px, py))
    else:
        dr.rounded_rectangle(box, radius=24, fill="#F1F5F9")
        f = _font(64, True)
        ini = (r["brand"] or "DP")[:3].upper()
        dr.text(((box[0] + box[2]) / 2 - dr.textlength(ini, font=f) / 2, (box[1] + box[3]) / 2 - 36), ini, font=f, fill="#64748B")
    if drop_pct:
        badge = f"-%{drop_pct}"
        bf = _font(46, True)
        bw = dr.textlength(badge, font=bf)
        dr.rounded_rectangle([box[0], box[1], box[0] + bw + 40, box[1] + 72], radius=16, fill="#059669")
        dr.text((box[0] + 20, box[1] + 10), badge, font=bf, fill="#FFFFFF")

    # Sağ: ürün adı + fiyat sıralaması
    x0, x1 = 572, W - 44
    y = 130
    brand_f = _font(28, True)
    dr.text((x0, y), (r["brand"] or "").upper()[:28], font=brand_f, fill="#64748B")
    y += 44
    name_f = _font(44, True)
    for line in _wrap(dr, r["family_name"] or "", name_f, x1 - x0, 2):
        dr.text((x0, y), line, font=name_f, fill="#0F172A")
        y += 56
    y += 18

    row_h = 78 if len(quotes) <= 4 else 66
    chip_f = _font(26, True)
    price_f = _font(34, True)
    for i, q in enumerate(quotes):
        name, bg, fg = BRANDS.get(q["platform"], DEFAULT_BRAND)
        top = y + i * (row_h + 10)
        win = i == 0
        dr.rounded_rectangle([x0, top, x1, top + row_h], radius=16,
                             fill="#ECFDF5" if win else "#F8FAFC",
                             outline="#10B981" if win else "#E2E8F0", width=3 if win else 2)
        cw = dr.textlength(name, font=chip_f) + 32
        cy = top + (row_h - 44) // 2
        dr.rounded_rectangle([x0 + 16, cy, x0 + 16 + cw, cy + 44], radius=10, fill=bg)
        dr.text((x0 + 32, cy + 7), name, font=chip_f, fill=fg)
        ps = _tl(q["pack_price"])
        pf = _font(38, True) if win else price_f
        dr.text((x1 - 20 - dr.textlength(ps, font=pf), top + (row_h - (44 if win else 40)) // 2), ps,
                font=pf, fill="#047857" if win else "#334155")
        if win:
            lbl = "EN UCUZ"
            lf = _font(20, True)
            lx = x0 + 16 + cw + 14
            if lx + dr.textlength(lbl, font=lf) < x1 - 20 - dr.textlength(ps, font=pf) - 12:
                dr.text((lx, top + (row_h - 22) // 2), lbl, font=lf, fill="#059669")

    # Alt şerit: çağrı
    dr.rectangle([0, H - 100, W, H], fill="#F1F5F9")
    cta = "Tüm market fiyatlarını karşılaştır →"
    dr.text((44, H - 70), cta, font=_font(32, True), fill="#0B132B")
    site = "dipci.tr"
    sf = _font(32, True)
    dr.text((W - 44 - dr.textlength(site, font=sf), H - 70), site, font=sf, fill="#059669")

    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    data = buf.getvalue()
    with _img_lock:
        if len(_img_cache) > 300:
            _img_cache.clear()
        _img_cache[ck] = (time.time(), data)
    return data
