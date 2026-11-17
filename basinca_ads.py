"""
basinca.com için dipci.tr reklam kartları (Kemal, 17 Kas 2026).

İki reklam türü kodla, veritabanından çekilir:
  - "dip"      : Gerçek İndirimler — 30 günlük ortalamanın belirgin altına düşen market ürünleri.
  - "kampanya" : Online'da en ucuz — en ucuz fiyatı bir online mağazada (Amazon, Hepsiburada, n11...) olan ürünler.

Her reklam, basınca kartının 3:4 dikey ölçüsünde 900x1200 PNG ile gelir (1152x1536 çizilip küçültülür):
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

# basınca kartı 3:4 dikey: matbaa 1152x1536 basıp 900x1200'e küçültüyor (Kemal, 17 Kas 2026).
W, H = 900, 1200
RW, RH = 1152, 1536
S = RW / W
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
        if not _image_ok(r["image_url"]):
            continue
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


_img_ok: Dict[str, tuple] = {}


def _image_ok(url: str) -> bool:
    """Reklamda görseli olmayan ürün çıkmasın; sonuç 6 saat hatırlanır."""
    if not url:
        return False
    hit = _img_ok.get(url)
    if hit and time.time() - hit[0] < 21600:
        return hit[1]
    ok = _fetch_image(url) is not None
    _img_ok[url] = (time.time(), ok)
    return ok


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

    def P(v):  # 900x1200 tasarım ölçüsünü 1152x1536 çizim ölçüsüne çevir
        return int(round(v * S))

    def F(size, bold=False):
        return _font(P(size), bold)

    img = Image.new("RGB", (RW, RH), "#FFFFFF")
    dr = ImageDraw.Draw(img)
    M = 40  # kenar boşluğu

    # Üst şerit: marka + reklam türü
    dr.rectangle([0, 0, RW, P(92)], fill="#0B132B")
    lf = F(40, True)
    dr.text((P(M), P(22)), "dipci", font=lf, fill="#FFFFFF")
    dr.text((P(M) + dr.textlength("dipci", font=lf), P(22)), ".tr", font=lf, fill="#10B981")
    tag = "GERÇEK İNDİRİM" if kind == "dip" else "ONLINE'DA EN UCUZ"
    tf = F(22, True)
    tw = dr.textlength(tag, font=tf)
    dr.rounded_rectangle([P(W - M) - tw - P(32), P(24), P(W - M), P(68)], radius=P(22), fill="#10B981")
    dr.text((P(W - M) - tw - P(16), P(33)), tag, font=tf, fill="#FFFFFF")

    # Ürün görseli: üstte, büyük, beyaz zeminde (katmansız)
    box = (P(M), P(116), P(W - M), P(580))
    pic = _fetch_image(r["image_url"]) if r["image_url"] else None
    if not pic:
        return None
    if pic:
        bw, bh = box[2] - box[0], box[3] - box[1]
        scale = min(bw / pic.width, bh / pic.height)
        pic = pic.resize((max(1, int(pic.width * scale)), max(1, int(pic.height * scale))), Image.LANCZOS)
        img.paste(pic, (box[0] + (bw - pic.width) // 2, box[1] + (bh - pic.height) // 2))
    else:
        dr.rounded_rectangle(box, radius=P(24), fill="#F1F5F9")
        f = F(72, True)
        ini = (r["brand"] or "DP")[:3].upper()
        dr.text(((box[0] + box[2]) / 2 - dr.textlength(ini, font=f) / 2, (box[1] + box[3]) / 2 - P(40)), ini, font=f, fill="#64748B")
    if drop_pct:
        badge = f"-%{drop_pct}"
        bf = F(44, True)
        bw2 = dr.textlength(badge, font=bf)
        dr.rounded_rectangle([box[0], box[1], box[0] + bw2 + P(36), box[1] + P(68)], radius=P(14), fill="#059669")
        dr.text((box[0] + P(18), box[1] + P(9)), badge, font=bf, fill="#FFFFFF")

    # Ürün adı
    y = P(606)
    dr.text((P(M), y), (r["brand"] or "").upper()[:32], font=F(24, True), fill="#64748B")
    y += P(38)
    name_f = F(40, True)
    for line in _wrap(dr, r["family_name"] or "", name_f, P(W - 2 * M), 2):
        dr.text((P(M), y), line, font=name_f, fill="#0F172A")
        y += P(50)
    y += P(14)

    # Fiyat sıralaması (3-5 mağaza), alt şeride kadar sığdır
    footer_top = P(H - 96)
    n = len(quotes)
    gap = P(10)
    row_h = min(P(76), (footer_top - P(20) - y - gap * (n - 1)) // n)
    chip_f = F(24, True)
    for i, q in enumerate(quotes):
        name, bg, fg = BRANDS.get(q["platform"], DEFAULT_BRAND)
        top = y + i * (row_h + gap)
        win = i == 0
        dr.rounded_rectangle([P(M), top, P(W - M), top + row_h], radius=P(14),
                             fill="#ECFDF5" if win else "#F8FAFC",
                             outline="#10B981" if win else "#E2E8F0", width=P(3) if win else P(2))
        cw = dr.textlength(name, font=chip_f) + P(28)
        ch = P(42)
        cy = top + (row_h - ch) // 2
        dr.rounded_rectangle([P(M + 14), cy, P(M + 14) + cw, cy + ch], radius=P(10), fill=bg)
        dr.text((P(M + 28), cy + P(7)), name, font=chip_f, fill=fg)
        ps = _tl(q["pack_price"])
        pf = F(36, True) if win else F(32, True)
        ph = P(40) if win else P(36)
        px = P(W - M - 18) - dr.textlength(ps, font=pf)
        dr.text((px, top + (row_h - ph) // 2), ps, font=pf, fill="#047857" if win else "#334155")
        if win:
            lbl = "EN UCUZ"
            lf2 = F(18, True)
            lx = P(M + 14) + cw + P(12)
            if lx + dr.textlength(lbl, font=lf2) < px - P(10):
                dr.text((lx, top + (row_h - P(20)) // 2), lbl, font=lf2, fill="#059669")

    # Alt şerit: çağrı
    dr.rectangle([0, footer_top, RW, RH], fill="#F1F5F9")
    dr.text((P(M), footer_top + P(30)), "Tüm fiyatları karşılaştır →", font=F(30, True), fill="#0B132B")
    sf = F(30, True)
    dr.text((P(W - M) - dr.textlength("dipci.tr", font=sf), footer_top + P(30)), "dipci.tr", font=sf, fill="#059669")

    img = img.resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    data = buf.getvalue()
    with _img_lock:
        if len(_img_cache) > 300:
            _img_cache.clear()
        _img_cache[ck] = (time.time(), data)
    return data
