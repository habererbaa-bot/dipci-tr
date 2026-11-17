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

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

# basınca kartı 3:4 dikey: matbaa 1152x1536 basıp 900x1200'e küçültüyor (Kemal, 17 Kas 2026).
W, H = 900, 1200
RW, RH = 1800, 2400  # 2 kat büyük çizilip 900x1200'e küçültülür: yazılar ve görsel net olsun
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


def _tr_upper(t: str) -> str:
    return t.replace("i", "İ").replace("ı", "I").upper()


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


DISCOUNT_CHAINS = ("bim", "a101", "sok")

# 8 reklam kodu (Kemal, 17 Kas 2026: "bana 8 farklı reklam kodu yaz; hepsini ayrı ayrı kartlara yükleyelim").
# kod: (kart etiketi, açıklama)
KINDS = {
    "dip": ("GERÇEK İNDİRİM", "30 günlük ortalamanın belirgin altına düşen ürünler"),
    "sok": ("ŞOK İNDİRİM", "Son 24 saatte fiyatı birden %25 ve üstü düşen ürünler"),
    "kampanya": ("ONLINE'DA EN UCUZ", "En ucuz fiyatı bir online mağazada olan ve marketten ucuz ürünler"),
    "fark": ("AYNI ÜRÜN, DEV FARK", "Marketler arasında aynı ürün için en büyük fiyat farkı"),
    "indirim-marketi": ("BİM · A101 · ŞOK'TA UCUZ", "En ucuz fiyatı BİM, A101 ya da ŞOK'ta olan ürünler"),
    "amazon": ("AMAZON FIRSATI", "En ucuz fiyatı Amazon'da olan ürünler"),
    "temizlik-bakim": ("TEMİZLİK & BAKIMDA DİP", "Temizlik ve kişisel bakımda en ucuz mağaza"),
    "teknoloji-ev": ("TEKNOLOJİ & EVDE DİP", "Teknoloji, beyaz eşya ve ev ürünlerinde en ucuz mağaza"),
}


def _drop_for(db, product_key: str, since_hours: int = 0):
    q = "SELECT * FROM live_price_drops WHERE product_key = ?"
    args = [product_key]
    if since_hours:
        q += " AND detected_at > ?"
        args.append(datetime.fromtimestamp(time.time() - since_hours * 3600).isoformat())
    return db.conn.execute(q + " ORDER BY detected_at DESC LIMIT 1", args).fetchone()


def _candidates(db, kind: str):
    """Her kod için aday ürün satırları (en iyiler önce)."""
    base = "SELECT * FROM product_catalog_index WHERE offer_count >= 3 AND image_url != ''"
    if kind in ("dip", "sok"):
        since = "" if kind == "dip" else datetime.fromtimestamp(time.time() - 86400).isoformat()
        min_pct = 15 if kind == "dip" else 25
        order = "l.drop_pct DESC" if kind == "dip" else "l.detected_at DESC"
        sql = f"""SELECT p.* FROM live_price_drops l JOIN product_catalog_index p ON p.product_key = l.product_key
                  WHERE l.drop_pct >= ? AND p.offer_count >= 3 AND p.image_url != ''
                  {"AND l.detected_at > ?" if since else ""} GROUP BY p.product_key ORDER BY {order} LIMIT 200"""
        return db.conn.execute(sql, [min_pct] + ([since] if since else [])).fetchall()
    if kind == "kampanya":
        marks = ",".join("?" for _ in ONLINE_PLATFORMS)
        return db.conn.execute(base + f" AND best_platform IN ({marks}) ORDER BY savings_pct DESC LIMIT 200", ONLINE_PLATFORMS).fetchall()
    if kind == "amazon":
        return db.conn.execute(base + " AND best_platform = 'amazon' ORDER BY savings_pct DESC LIMIT 200").fetchall()
    if kind == "indirim-marketi":
        return db.conn.execute(base + " AND best_platform IN ('bim','a101','sok') ORDER BY savings_pct DESC LIMIT 200").fetchall()
    if kind == "temizlik-bakim":
        return db.conn.execute(base + " AND category IN ('temizlik','kisisel_bakim') ORDER BY savings_pct DESC LIMIT 200").fetchall()
    if kind == "teknoloji-ev":
        return db.conn.execute(base + " AND category IN ('teknoloji_beyaz_esya','ev_yasam_mutfak') ORDER BY savings_pct DESC LIMIT 200").fetchall()
    # fark
    return db.conn.execute(base + " ORDER BY savings_pct DESC LIMIT 300").fetchall()


def evaluate(db, kind: str, r) -> Optional[Dict]:
    """Ürün bu reklam koduna uyuyor mu? Uyuyorsa rozet, özet ve mağaza sıralaması döner."""
    quotes = _clean_quotes(json.loads(r["top_quotes_json"] or "[]"))
    if len(quotes) < 3:
        return None
    best = quotes[0]
    bn = BRANDS.get(best["platform"], DEFAULT_BRAND)[0]
    markets = [q for q in quotes if q["platform"] in MARKET_PLATFORMS]
    badge, ozet = "", ""
    if kind in ("dip", "sok"):
        d = _drop_for(db, r["product_key"], 24 if kind == "sok" else 0)
        if not d:
            return None
        new_price = float(d["new_price"] or 0)
        # Fiyat geri çıktıysa (radar kaydı bayat) reklam yapma.
        if not new_price or best["pack_price"] > new_price * 1.05:
            return None
        pct = round(float(d["drop_pct"] or 0))
        badge = f"-%{pct}"
        ozet = (f"Fiyatı birden %{pct} düştü! " if kind == "sok" else f"%{pct} indirim. ") + f"En ucuz {bn}: {_tl(best['pack_price'])}."
    elif kind in ("kampanya", "amazon"):
        # Online fiyat gerçekten bir market fiyatından ucuz olmalı; yoksa paket eşleşmesi şüpheli.
        if not markets or best["platform"] not in ONLINE_PLATFORMS:
            return None
        if kind == "amazon" and best["platform"] != "amazon":
            return None
        gap = round((1 - best["pack_price"] / markets[0]["pack_price"]) * 100)
        if gap < 8:
            return None
        badge = f"%{gap} UCUZ"
        ozet = f"{bn}: {_tl(best['pack_price'])}. Market fiyatından %{gap} ucuz."
    elif kind == "indirim-marketi":
        others = [q for q in markets if q["platform"] not in DISCOUNT_CHAINS]
        if best["platform"] not in DISCOUNT_CHAINS or not others:
            return None
        gap = round((1 - best["pack_price"] / others[0]["pack_price"]) * 100)
        if gap < 10:
            return None
        badge = f"%{gap} UCUZ"
        ozet = f"{bn}: {_tl(best['pack_price'])} · {BRANDS.get(others[0]['platform'], DEFAULT_BRAND)[0]}: {_tl(others[0]['pack_price'])}."
    elif kind == "fark":
        # Yalnız marketler: online çoklu paketler yanlış fark gösterebiliyor.
        if len(markets) < 3:
            return None
        quotes = markets[:5]
        best = quotes[0]
        bn = BRANDS.get(best["platform"], DEFAULT_BRAND)[0]
        gap = round((1 - best["pack_price"] / quotes[-1]["pack_price"]) * 100)
        if gap < 20:
            return None
        badge = f"%{gap} FARK"
        ozet = f"Aynı ürün · {bn}: {_tl(best['pack_price'])} · {BRANDS.get(quotes[-1]['platform'], DEFAULT_BRAND)[0]}: {_tl(quotes[-1]['pack_price'])}."
    else:  # temizlik-bakim, teknoloji-ev
        # Teknoloji/ev kategorisine yanlış düşmüş market ürünlerini (ör. kola) ele.
        if kind == "teknoloji-ev" and best["pack_price"] < 300:
            return None
        gap = round((1 - best["pack_price"] / quotes[-1]["pack_price"]) * 100)
        if gap < 15:
            return None
        badge = f"%{gap} FARK"
        ozet = f"En ucuz {bn}: {_tl(best['pack_price'])}. {len(quotes)} mağaza karşılaştırıldı."
    return {"quotes": quotes, "badge": badge, "ozet": ozet}


def list_ads(db, fast_radar=None, kind: str = "dip", limit: int = 12, exclude: Optional[set] = None) -> List[Dict]:
    """basınca akışına konacak reklam kayıtları. En az 3 mağazada satılan, görseli açılan ürünler."""
    if kind not in KINDS:
        return []
    limit = max(1, min(int(limit or 12), 40))
    items = []
    for r in _candidates(db, kind):
        if exclude and r["product_key"] in exclude:
            continue
        ev = evaluate(db, kind, r)
        if not ev or not _image_ok(r["image_url"]):
            continue
        quotes = ev["quotes"]
        best = quotes[0]
        items.append(
            {
                "id": f"dipci-{kind}-{r['product_key']}",
                "tur": kind,
                "etiket": KINDS[kind][0],
                "reklam": True,
                "baslik": f"{r['brand']} {r['family_name']}".strip(),
                "ozet": ev["ozet"],
                "rozet": ev["badge"],
                "urun_gorseli": r["image_url"],
                "gorsel": f"{CANONICAL}/reklam/basinca/{kind}/{r['product_key']}.png?v=k3-{datetime.now():%Y%m%d%H%M}"[:-1],  # 10 dakikada bir yeni adres: basınca eski görseli önbellekte tutmasın
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
CACHE_SECONDS = 3600


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


def _trim_white(im: Image.Image) -> Image.Image:
    """Ürün fotoğrafının etrafındaki boş beyaz alanı kırpar (ürün kartta büyük görünsün)."""
    try:
        g = ImageChops.difference(im.convert("RGB"), Image.new("RGB", im.size, (255, 255, 255))).convert("L")
        g = g.point(lambda v: 255 if v > 18 else 0)
        bb = g.getbbox()
        if not bb:
            return im
        pad = int(max(bb[2] - bb[0], bb[3] - bb[1]) * 0.03)
        bb = (max(0, bb[0] - pad), max(0, bb[1] - pad), min(im.width, bb[2] + pad), min(im.height, bb[3] + pad))
        return im.crop(bb)
    except Exception:
        return im


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


FONT_DIR = Path(__file__).resolve().parent / "fonts"


def _cfont(name: str, size: int, weight: str = ""):
    """basınca kartlarıyla aynı yazı tipleri: Anton (manşet) ve Oswald (etiket, fiyat)."""
    key = (name, size, weight)
    if key in _font_cache:
        return _font_cache[key]
    try:
        f = ImageFont.truetype(str(FONT_DIR / f"{name}.ttf"), size)
        if weight:
            try:
                f.set_variation_by_name(weight)
            except Exception:
                pass
    except Exception:
        f = _font(size, True)
    _font_cache[key] = f
    return f


def _fit_lines(dr, text: str, name: str, max_w: int, max_lines: int, start: int, minimum: int):
    """Metni en büyük puntoyla en çok max_lines satıra sığdırır."""
    size = start
    while size >= minimum:
        f = _cfont(name, size)
        lines = _wrap(dr, text, f, max_w, max_lines)
        if not lines[-1].endswith("…") or size == minimum:
            return f, lines
        size -= 6
    f = _cfont(name, minimum)
    return f, _wrap(dr, text, f, max_w, max_lines)


def _gradient_text(img, xy, text: str, font, top="#FFE14D", bottom="#FF9F0A"):
    """basınca kartlarındaki sarı-turuncu geçişli yazı."""
    l, t, r, b = font.getbbox(text)
    w, h = r, b
    grad = Image.new("RGB", (w, h), top)
    gd = ImageDraw.Draw(grad)
    c1 = tuple(int(top[i:i + 2], 16) for i in (1, 3, 5))
    c2 = tuple(int(bottom[i:i + 2], 16) for i in (1, 3, 5))
    for yy in range(h):
        k = max(0.0, (yy - t)) / max(1, (h - t))
        gd.line([(0, yy), (w, yy)], fill=tuple(int(c1[j] + (c2[j] - c1[j]) * k) for j in range(3)))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).text((0, 0), text, font=font, fill=255)
    img.paste(grad, (int(xy[0]), int(xy[1])), mask)


def render_card_png(db, fast_radar, kind: str, product_key: str) -> Optional[bytes]:
    ck = f"{kind}:{product_key}"
    with _img_lock:
        hit = _img_cache.get(ck)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]

    if kind not in KINDS:
        return None
    r = db.conn.execute("SELECT * FROM product_catalog_index WHERE product_key = ?", (product_key,)).fetchone()
    if not r:
        return None
    ev = evaluate(db, kind, r)
    if not ev:
        return None
    quotes = ev["quotes"]
    pic = _fetch_image(r["image_url"]) if r["image_url"] else None
    if not pic:
        return None

    # dipci.tr kurumsal kart (Kemal, 17 Kas 2026: "gazete manşeti gibi değil, kurumsal kimliğine göre; fiyat öne çıksın").
    # Beyaz zemin, üstte lacivert dipci şeridi, ürün görseli, ürün adı ve büyük fiyat satırları.
    def P(v):
        return int(round(v * S))

    def F(size, bold=True):
        return _font(P(size), bold)

    img = Image.new("RGB", (RW, RH), "#FFFFFF")
    dr = ImageDraw.Draw(img)
    M = 40

    # Üst şerit: dipci.tr + reklam türü
    dr.rectangle([0, 0, RW, P(88)], fill="#0B132B")
    lf = F(38)
    dr.text((P(M), P(20)), "dipci", font=lf, fill="#FFFFFF")
    dr.text((P(M) + dr.textlength("dipci", font=lf), P(20)), ".tr", font=lf, fill="#10B981")
    tag = KINDS[kind][0]
    tf = F(20)
    tw = dr.textlength(tag, font=tf)
    dr.rounded_rectangle([P(W - M) - tw - P(32), P(24), P(W - M), P(64)], radius=P(20), fill="#10B981")
    dr.text((P(W - M) - tw - P(16), P(32)), tag, font=tf, fill="#FFFFFF")

    # Yerleşim: mağaza satırları kartın dibine oturur, ad üstünde; kalan bütün yükseklik ürün görseline (boşluk kalmaz).
    footer_top = P(H - 84)
    n = min(len(quotes), 5)
    row_h, gap = P(88), P(12)
    rows_top = footer_top - P(24) - n * row_h - (n - 1) * gap
    nf = F(36)
    name_lines = _wrap(dr, r["family_name"] or "", nf, P(W - 2 * M), 2)
    name_block = P(36) + len(name_lines) * P(46) + P(18)
    name_top = rows_top - name_block
    box = (P(M), P(104), P(W - M), name_top - P(16))

    # Ürün görseli: fotoğraftaki boş beyaz kenarlar kırpılır, ürün kutuya sığacak kadar büyütülür ve netleştirilir.
    pic = _trim_white(pic)
    bw, bh = box[2] - box[0], box[3] - box[1]
    sc = min(bw / pic.width, bh / pic.height)
    pic = pic.resize((max(1, int(pic.width * sc)), max(1, int(pic.height * sc))), Image.LANCZOS)
    if sc > 1.2:
        pic = pic.filter(ImageFilter.UnsharpMask(radius=2, percent=80, threshold=2))
    img.paste(pic, (box[0] + (bw - pic.width) // 2, box[1] + (bh - pic.height) // 2))
    badge = ev["badge"]
    if badge:
        bf = F(34)
        bw2 = dr.textlength(badge, font=bf)
        dr.rounded_rectangle([box[0], box[1], box[0] + bw2 + P(32), box[1] + P(58)], radius=P(12), fill="#059669")
        dr.text((box[0] + P(16), box[1] + P(10)), badge, font=bf, fill="#FFFFFF")

    # Ürün adı
    y = name_top
    dr.text((P(M), y), (r["brand"] or "").upper()[:34], font=F(22), fill="#64748B")
    y += P(36)
    for line in name_lines:
        dr.text((P(M), y), line, font=nf, fill="#0F172A")
        y += P(46)

    # Fiyat satırları
    y = rows_top
    for i, q in enumerate(quotes[:n]):
        name, bg, fg = BRANDS.get(q["platform"], DEFAULT_BRAND)
        top = y + i * (row_h + gap)
        win = i == 0
        dr.rounded_rectangle([P(M), top, P(W - M), top + row_h], radius=P(14),
                             fill="#ECFDF5" if win else "#F8FAFC",
                             outline="#10B981" if win else "#E2E8F0", width=P(3) if win else P(2))
        cf = F(26)
        cw = dr.textlength(name, font=cf) + P(30)
        ch = P(48)
        cy = top + (row_h - ch) // 2
        dr.rounded_rectangle([P(M + 14), cy, P(M + 14) + cw, cy + ch], radius=P(10), fill=bg)
        dr.text((P(M + 29), cy + (ch - P(28)) // 2), name, font=cf, fill=fg)
        ps = _tl(q["pack_price"])
        pf = F(42 if win else 34)
        ph = P(48 if win else 39)
        px = P(W - M - 18) - dr.textlength(ps, font=pf)
        dr.text((px, top + (row_h - ph) // 2), ps, font=pf, fill="#047857" if win else "#334155")
        if win:
            lbl, lf2 = "EN UCUZ", F(19)
            lx = P(M + 14) + cw + P(12)
            if lx + dr.textlength(lbl, font=lf2) < px - P(10):
                dr.text((lx, top + (row_h - P(22)) // 2), lbl, font=lf2, fill="#059669")

    # Alt şerit
    dr.rectangle([0, footer_top, RW, RH], fill="#F1F5F9")
    cf2 = F(28)
    dr.text((P(M), footer_top + P(26)), "Tüm fiyatları karşılaştır →", font=cf2, fill="#0B132B")
    dr.text((P(W - M) - dr.textlength("dipci.tr", font=cf2), footer_top + P(26)), "dipci.tr", font=cf2, fill="#059669")

    img = img.resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    data = buf.getvalue()
    with _img_lock:
        if len(_img_cache) > 300:
            _img_cache.clear()
        _img_cache[ck] = (time.time(), data)
    return data


# ——— Dönüşümlü gösterim (Kemal, 17 Kas 2026: "sürekli aynı ürünü göstermemeli") ———
import random

_pool: Dict[str, tuple] = {}
_refreshing: set = set()


# Aynı ürün iki karta birden düşmesin: önce şok, sonra diğerleri; bir türe giren ürün sonrakilere girmez.
POOL_ORDER = ("sok", "dip", "fark", "indirim-marketi", "amazon", "kampanya", "temizlik-bakim", "teknoloji-ev")
_pool_lock = threading.Lock()
POOL_SECONDS = 300  # 5 dakikada bir tazele: yeni kampanya ve ani düşüş hemen girsin


ILK_GORULME = Path(__file__).resolve().parent / "data" / "reklam_ilk_gorulme.json"
ACIL_SAAT = 3


def _acil_isaretle(db, items_by_kind: Dict[str, List[Dict]]) -> None:
    """Olağanüstü fiyat düşüşü (son 3 saatte %40+) ya da yeni giren kampanya ürünü 'acil' olur ve öne geçer."""
    try:
        ilk = json.loads(ILK_GORULME.read_text(encoding="utf-8")) if ILK_GORULME.exists() else None
    except Exception:
        ilk = None
    simdi = time.time()
    ilk_kayit = ilk is None
    ilk = ilk or {}
    sinir = datetime.fromtimestamp(simdi - ACIL_SAAT * 3600).isoformat()
    for k, items in items_by_kind.items():
        for it in items:
            it["acil"] = False
            key = it["id"]
            if key not in ilk:
                ilk[key] = simdi
            pk = key[len(f"dipci-{k}-"):]
            d = db.conn.execute(
                "SELECT drop_pct FROM live_price_drops WHERE product_key = ? AND detected_at > ? AND drop_pct >= 40", (pk, sinir)
            ).fetchone()
            if d:
                it["acil"], it["acil_neden"] = True, "olağanüstü fiyat düşüşü"
            elif not ilk_kayit and k in ("kampanya", "amazon", "indirim-marketi", "fark", "teknoloji-ev") and simdi - ilk[key] < ACIL_SAAT * 3600:
                it["acil"], it["acil_neden"] = True, "yeni kampanya"
    # 3 günden eski kayıtları unut (dosya şişmesin)
    ilk = {k: v for k, v in ilk.items() if simdi - v < 3 * 86400}
    try:
        ILK_GORULME.write_text(json.dumps(ilk), encoding="utf-8")
    except Exception:
        pass


def _build_all_pools(db) -> None:
    used: set = set()
    yeni = {}
    for k in POOL_ORDER:
        items = list_ads(db, None, k, 30, exclude=used)
        for it in items:
            used.add(it["id"][len(f"dipci-{k}-"):])
        yeni[k] = items
    _acil_isaretle(db, yeni)
    t = time.time()
    _pool.update({k: (t, v) for k, v in yeni.items()})
    # Kart görsellerini önceden çiz: okur kutuyu açınca beklemesin.
    for k, items in yeni.items():
        for it in items:
            try:
                render_card_png(db, None, k, it["id"][len(f"dipci-{k}-"):])
            except Exception:
                pass


def warm_pools(db) -> None:
    """Site açılınca 8 kodun havuzunu arka planda hazırla."""
    def _run():
        with _pool_lock:
            try:
                _build_all_pools(db)
            except Exception:
                pass
    threading.Thread(target=_run, daemon=True).start()


def rotating_ads(db, fast_radar, kind: str, limit: int) -> List[Dict]:
    """Her istekte havuzdan rastgele, farklı ürünler. Havuz 15 dakikada bir tazelenir."""
    kinds = tuple(KINDS) if kind not in KINDS else (kind,)
    oldest = min((_pool[k][0] for k in KINDS if k in _pool), default=0)
    if len(_pool) < len(KINDS):
        with _pool_lock:
            if len(_pool) < len(KINDS):
                _build_all_pools(db)
    elif time.time() - oldest > POOL_SECONDS and not _refreshing:
        # Eski havuzu hemen ver, yenisini arka planda hazırla (okur beklemesin).
        _refreshing.add("hepsi")

        def _yenile():
            try:
                with _pool_lock:
                    _build_all_pools(db)
            finally:
                _refreshing.discard("hepsi")

        threading.Thread(target=_yenile, daemon=True).start()
    picked = []
    for k in kinds:
        items = list(_pool.get(k, (0, []))[1])
        random.shuffle(items)
        # Acil olanlar (olağanüstü düşüş, yeni kampanya) en öne
        items.sort(key=lambda it: 0 if it.get("acil") else 1)
        picked.append(items)
    out = []
    for i in range(max((len(x) for x in picked), default=0)):
        for x in picked:
            if i < len(x):
                out.append(x[i])
    out.sort(key=lambda it: 0 if it.get("acil") else 1)
    return out[:limit]


def current_slot(db, kind: str, saniye: int = 20) -> Optional[Dict]:
    """Sabit adresli kart için şu anki ürün: acil varsa o, yoksa havuzda zamana göre sırayla döner.
    Görsel ve tıklama adresi aynı ürünü göstersin diye seçim saate bağlı (20 saniyelik dilim)."""
    rotating_ads(db, None, kind, 1)  # havuzu hazırla/tazele
    items = list(_pool.get(kind, (0, []))[1])
    if not items:
        return None
    acil = [it for it in items if it.get("acil")]
    liste = acil or items
    return liste[int(time.time() // saniye) % len(liste)]
