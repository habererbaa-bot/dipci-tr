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


def list_ads(db, fast_radar=None, kind: str = "dip", limit: int = 12) -> List[Dict]:
    """basınca akışına konacak reklam kayıtları. En az 3 mağazada satılan, görseli açılan ürünler."""
    if kind not in KINDS:
        return []
    limit = max(1, min(int(limit or 12), 40))
    items = []
    for r in _candidates(db, kind):
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

    # basınca kart düzeni (1152x1536 çizilir, 900x1200'e küçültülür):
    # üstte görsel alanı, kırmızı çizgi, koyu lacivert alt blok, kırmızı etiket, Anton manşet, sarı geçişli satır.
    img = Image.new("RGB", (RW, RH), "#FFFFFF")
    dr = ImageDraw.Draw(img)
    M = 56
    photo_h = 760

    # Görsel: üst alanı beyaz zeminiyle kaplar, ürün büyük ve ortada; arada katman yok.
    bw, bh = RW - 2 * M, photo_h - 120
    sc = min(bw / pic.width, bh / pic.height)
    pic = pic.resize((max(1, int(pic.width * sc)), max(1, int(pic.height * sc))), Image.LANCZOS)
    img.paste(pic, ((RW - pic.width) // 2, 60 + (bh - pic.height) // 2))

    # Sağ üst: dipci.tr imi
    lf = _cfont("Anton", 52)
    lw = dr.textlength("dipci.tr", font=lf)
    dr.rounded_rectangle([RW - M - lw - 36, 34, RW - M, 112], radius=18, fill="#0B132B")
    dr.text((RW - M - lw - 18, 40), "dipci", font=lf, fill="#FFFFFF")
    dr.text((RW - M - 18 - dr.textlength(".tr", font=lf), 40), ".tr", font=lf, fill="#10B981")
    # Sol üst: rozet (indirim yüzdesi ya da fark)
    badge = ev["badge"]
    if badge:
        bf = _cfont("Anton", 72)
        bw2 = dr.textlength(badge, font=bf)
        dr.rounded_rectangle([M, 34, M + bw2 + 48, 146], radius=18, fill="#16A34A")
        dr.text((M + 24, 40), badge, font=bf, fill="#FFFFFF")

    # Kırmızı çizgi + koyu blok
    dr.rectangle([0, photo_h, RW, photo_h + 8], fill="#E11D2A")
    dr.rectangle([0, photo_h + 8, RW, RH], fill="#121826")

    y = photo_h + 56
    tag = KINDS[kind][0] + " · REKLAM"
    tf = _cfont("Oswald", 40, "Bold")
    tw = dr.textlength(tag, font=tf)
    dr.rounded_rectangle([M, y, M + tw + 56, y + 76], radius=10, fill="#E11D2A")
    dr.text((M + 28, y + 10), tag, font=tf, fill="#FFFFFF")
    y += 104

    title = _tr_upper(f"{r['brand']} {r['family_name']}".strip())
    # Mağaza satırlarının yeri önceden ayrılır; manşet kalan alana göre küçülür.
    n_rows = (min(len(quotes), 5) + 1) // 2
    rows_top = RH - 40 - n_rows * 82

    def cap(f):  # büyük harf yüksekliği ve üst boşluk (Anton'un geniş satır aralığını atla)
        _, t, _, b = f.getbbox("ŞİĞÜ0")
        top = f.getbbox("H")[1]
        return top, b - top

    pf = _cfont("Anton", 96)
    p_top, p_h = cap(pf)
    price_block = p_h + 34
    head_space = rows_top - price_block - y - 10
    size = 118
    while True:
        hf, lines = _fit_lines(dr, title, "Anton", RW - 2 * M, 2, size, 56)
        h_top, h_h = cap(hf)
        lh = int(h_h * 1.22)
        if len(lines) * lh <= head_space or hf.size <= 56:
            break
        size = hf.size - 6
    for line in lines:
        dr.text((M, y - h_top), line, font=hf, fill="#F8FAFC")
        y += lh
    y += 14

    best = quotes[0]
    price_line = f"EN UCUZ {_tl(best['pack_price'])}"
    _gradient_text(img, (M, y - p_top), price_line, pf)
    y += price_block

    # Mağaza sıralaması: iki sütun, kurumsal renkli etiket + fiyat
    rest = quotes[:5]
    cols = 2
    col_w = (RW - 2 * M - 24) // cols
    row_h = 70
    y = max(y, rows_top)  # satırlar kartın dibine sabit; hepsi görünür
    cf = _cfont("Oswald", 32, "Bold")
    for i, q in enumerate(rest):
        name, bg, fg = BRANDS.get(q["platform"], DEFAULT_BRAND)
        cx = M + (i % cols) * (col_w + 24)
        cy = y + (i // cols) * (row_h + 12)
        win = i == 0
        dr.rounded_rectangle([cx, cy, cx + col_w, cy + row_h], radius=12,
                             fill="#1E2A3D" if not win else "#14321F",
                             outline="#FACC15" if win else "#2B3A52", width=3)
        nw = dr.textlength(name, font=cf) + 28
        dr.rounded_rectangle([cx + 10, cy + 10, cx + 10 + nw, cy + row_h - 10], radius=8, fill=bg)
        dr.text((cx + 24, cy + 13), name, font=cf, fill=fg)
        ps = _tl(q["pack_price"])
        dr.text((cx + col_w - 16 - dr.textlength(ps, font=cf), cy + 13), ps, font=cf,
                fill="#FACC15" if win else "#E2E8F0")

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


def warm_pools(db) -> None:
    """Site açılınca 8 kodun havuzunu arka planda hazırla."""
    def _run():
        for k in KINDS:
            try:
                _pool[k] = (time.time(), list_ads(db, None, k, 40))
            except Exception:
                pass
    threading.Thread(target=_run, daemon=True).start()
POOL_SECONDS = 900


def rotating_ads(db, fast_radar, kind: str, limit: int) -> List[Dict]:
    """Her istekte havuzdan rastgele, farklı ürünler. Havuz 15 dakikada bir tazelenir."""
    kinds = tuple(KINDS) if kind not in KINDS else (kind,)
    picked = []
    for k in kinds:
        hit = _pool.get(k)
        if not hit:
            hit = (time.time(), list_ads(db, fast_radar, k, 40))
            _pool[k] = hit
        elif time.time() - hit[0] > POOL_SECONDS and k not in _refreshing:
            # Eski havuzu hemen ver, yenisini arka planda hazırla (okur beklemesin).
            _refreshing.add(k)

            def _yenile(kk=k):
                try:
                    _pool[kk] = (time.time(), list_ads(db, fast_radar, kk, 40))
                finally:
                    _refreshing.discard(kk)

            threading.Thread(target=_yenile, daemon=True).start()
        items = list(hit[1])
        random.shuffle(items)
        picked.append(items)
    out = []
    for i in range(max((len(x) for x in picked), default=0)):
        for x in picked:
            if i < len(x):
                out.append(x[i])
    return out[:limit]
