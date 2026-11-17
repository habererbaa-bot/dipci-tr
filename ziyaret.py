"""
dipci.tr ziyaretçi ölçümü (Kemal, 17 Kas 2026: "basınca'daki gibi; anlık, günlük, haftalık, aylık; nereden geliyorlar?").

- Sayfa görüntülemeleri tarayıcıdan küçük bir sinyalle gelir (/api/z): sayfa, geldiği site, utm kaynağı, cihaz.
- Ana veritabanını kilitlemesin diye AYRI dosyada tutulur: data/ziyaret.db
- Kişisel bilgi saklanmaz: IP yazılmaz; günlük tekil ziyaretçi için IP+tarayıcı+gün tuzlu özetle tek yönlü kısaltılır.
- Robotlar (Google, Bing, tarayıcı otomasyonu vb.) sayılmaz.
"""
import hashlib
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

DB_YOLU = Path(__file__).resolve().parent / "data" / "ziyaret.db"
_TUZ = os.getenv("ZIYARET_TUZ", "dipci-ziyaret")
_kilit = threading.Lock()
_conn: Optional[sqlite3.Connection] = None

BOT = re.compile(r"bot|crawl|spider|slurp|bingpreview|facebookexternalhit|headless|lighthouse|python|curl|wget|httpx|"
                 r"go-http|java/|okhttp|axios|node-fetch|scrapy|ahrefs|semrush|mj12|petal|yandex|baidu|dataprovider", re.I)

KAYNAK_ADLARI = [
    ("Google", ("google.",)), ("Bing", ("bing.com",)), ("Yandex", ("yandex.",)), ("DuckDuckGo", ("duckduckgo.",)),
    ("basinca.com", ("basinca.com",)), ("Facebook", ("facebook.com", "fb.com", "l.facebook")), ("Instagram", ("instagram.com",)),
    ("X (Twitter)", ("t.co", "twitter.com", "x.com")), ("WhatsApp", ("whatsapp",)), ("Telegram", ("t.me", "telegram")),
    ("YouTube", ("youtube.com", "youtu.be")), ("TikTok", ("tiktok.com",)), ("LinkedIn", ("linkedin.",)),
]


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_YOLU.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(DB_YOLU), check_same_thread=False, timeout=10)
        c.row_factory = sqlite3.Row
        c.executescript(
            """
            PRAGMA journal_mode = WAL;
            CREATE TABLE IF NOT EXISTS gor (
                ts INTEGER NOT NULL,          -- unix saniye
                gun TEXT NOT NULL,            -- YYYY-MM-DD (Türkiye)
                ziyaretci TEXT NOT NULL,      -- günlük tekil kimlik (tek yönlü özet)
                sayfa TEXT NOT NULL,
                kaynak TEXT NOT NULL,         -- Google, basinca.com, Doğrudan...
                kaynak_site TEXT NOT NULL,    -- geldiği alan adı
                utm TEXT NOT NULL DEFAULT '',
                cihaz TEXT NOT NULL           -- mobil / masaüstü / tablet
            );
            CREATE INDEX IF NOT EXISTS gor_ts ON gor(ts);
            CREATE INDEX IF NOT EXISTS gor_gun ON gor(gun);
            """
        )
        _conn = c
    return _conn


def _kaynak(ref: str, utm: str, kendi_host: str = "dipci.tr") -> tuple:
    host = (urlparse(ref).hostname or "").lower().removeprefix("www.") if ref else ""
    if utm:
        u = re.split(r"[^a-z0-9]+", utm.lower())[0]
        utm_adlari = {"google": "Google", "bing": "Bing", "yandex": "Yandex", "basinca": "basinca.com", "facebook": "Facebook",
                      "fb": "Facebook", "instagram": "Instagram", "ig": "Instagram", "twitter": "X (Twitter)", "x": "X (Twitter)",
                      "whatsapp": "WhatsApp", "wa": "WhatsApp", "telegram": "Telegram", "tg": "Telegram", "youtube": "YouTube",
                      "tiktok": "TikTok", "linkedin": "LinkedIn"}
        return utm_adlari.get(u, utm[:40]), host
    if not host or host.endswith(kendi_host):
        return "Doğrudan", host or ""
    for ad, parcalar in KAYNAK_ADLARI:
        if any(p in host for p in parcalar):
            return ad, host
    return host, host


def _cihaz(ua: str) -> str:
    u = ua.lower()
    if "ipad" in u or "tablet" in u:
        return "tablet"
    if "mobi" in u or "android" in u or "iphone" in u:
        return "mobil"
    return "masaüstü"


def kaydet(ip: str, ua: str, sayfa: str, ref: str, utm: str) -> None:
    if not ua or BOT.search(ua):
        return
    simdi = time.time()
    gun = (datetime.utcnow() + timedelta(hours=3)).strftime("%Y-%m-%d")
    kim = hashlib.sha256(f"{_TUZ}|{ip}|{ua}|{gun}".encode()).hexdigest()[:16]
    sayfa = re.sub(r"[?#].*$", "", sayfa or "/")[:200] or "/"
    kaynak, site = _kaynak(ref or "", (utm or "")[:60])
    with _kilit:
        c = _db()
        c.execute("INSERT INTO gor(ts, gun, ziyaretci, sayfa, kaynak, kaynak_site, utm, cihaz) VALUES (?,?,?,?,?,?,?,?)",
                  (int(simdi), gun, kim, sayfa, kaynak, site[:80], (utm or "")[:60], _cihaz(ua)))
        c.commit()


def _sayfa_adi(p: str) -> str:
    if p == "/":
        return "Ana sayfa"
    if p.startswith("/urun/"):
        return "Ürün: " + p[6:].replace("-", " ")[:60]
    if p == "/sepet":
        return "Sepet"
    if p.startswith("/kategori/"):
        return "Kategori: " + p[10:]
    return p


def rapor(aralik: str = "gun") -> Dict:
    c = _db()
    simdi = int(time.time())
    tr_simdi = datetime.utcnow() + timedelta(hours=3)
    bugun = tr_simdi.strftime("%Y-%m-%d")
    gun_sayisi = {"gun": 1, "hafta": 7, "ay": 30}.get(aralik, 1)
    bas_gun = (tr_simdi - timedelta(days=gun_sayisi - 1)).strftime("%Y-%m-%d")
    w = "gun >= ?"

    def tek(sql, *a):
        r = c.execute(sql, a).fetchone()
        return r[0] if r and r[0] is not None else 0

    anlik = {
        "ziyaretci_5dk": tek("SELECT COUNT(DISTINCT ziyaretci) FROM gor WHERE ts >= ?", simdi - 300),
        "goruntuleme_5dk": tek("SELECT COUNT(*) FROM gor WHERE ts >= ?", simdi - 300),
        "ziyaretci_30dk": tek("SELECT COUNT(DISTINCT ziyaretci) FROM gor WHERE ts >= ?", simdi - 1800),
        "sayfalar": [{"sayfa": _sayfa_adi(r[0]), "adres": r[0], "n": r[1]} for r in c.execute(
            "SELECT sayfa, COUNT(DISTINCT ziyaretci) n FROM gor WHERE ts >= ? GROUP BY sayfa ORDER BY n DESC LIMIT 8", (simdi - 300,))],
    }
    ozet = {
        "bugun_ziyaretci": tek("SELECT COUNT(DISTINCT ziyaretci) FROM gor WHERE gun = ?", bugun),
        "bugun_goruntuleme": tek("SELECT COUNT(*) FROM gor WHERE gun = ?", bugun),
        "dun_ziyaretci": tek("SELECT COUNT(DISTINCT ziyaretci) FROM gor WHERE gun = ?", (tr_simdi - timedelta(days=1)).strftime("%Y-%m-%d")),
        "hafta_ziyaretci": tek("SELECT COUNT(DISTINCT gun||ziyaretci) FROM gor WHERE gun >= ?", (tr_simdi - timedelta(days=6)).strftime("%Y-%m-%d")),
        "ay_ziyaretci": tek("SELECT COUNT(DISTINCT gun||ziyaretci) FROM gor WHERE gun >= ?", (tr_simdi - timedelta(days=29)).strftime("%Y-%m-%d")),
        "ay_goruntuleme": tek("SELECT COUNT(*) FROM gor WHERE gun >= ?", (tr_simdi - timedelta(days=29)).strftime("%Y-%m-%d")),
    }
    if aralik == "gun":
        seri = [{"etiket": f"{int(r[0]):02d}:00", "ziyaretci": r[1], "goruntuleme": r[2]} for r in c.execute(
            "SELECT strftime('%H', ts + 10800, 'unixepoch') s, COUNT(DISTINCT ziyaretci), COUNT(*) FROM gor WHERE gun = ? GROUP BY s ORDER BY s",
            (bugun,))]
        dolu = {x["etiket"]: x for x in seri}
        seri = [dolu.get(f"{h:02d}:00", {"etiket": f"{h:02d}:00", "ziyaretci": 0, "goruntuleme": 0}) for h in range(24)]
    else:
        satir = {r[0]: (r[1], r[2]) for r in c.execute(
            "SELECT gun, COUNT(DISTINCT ziyaretci), COUNT(*) FROM gor WHERE gun >= ? GROUP BY gun", (bas_gun,))}
        seri = []
        for i in range(gun_sayisi - 1, -1, -1):
            g = (tr_simdi - timedelta(days=i)).strftime("%Y-%m-%d")
            z, v = satir.get(g, (0, 0))
            seri.append({"etiket": g[8:10] + "." + g[5:7], "ziyaretci": z, "goruntuleme": v})

    def grup(alan, limit=12):
        return [{"ad": r[0] or "-", "ziyaretci": r[1], "goruntuleme": r[2]} for r in c.execute(
            f"SELECT {alan}, COUNT(DISTINCT gun||ziyaretci) z, COUNT(*) FROM gor WHERE {w} GROUP BY {alan} ORDER BY z DESC LIMIT ?",
            (bas_gun, limit))]

    kaynaklar = grup("kaynak")
    siteler = [x for x in grup("kaynak_site", 15) if x["ad"] not in ("-", "", "dipci.tr")]
    sayfalar = [{"ad": _sayfa_adi(x["ad"]), "adres": x["ad"], "ziyaretci": x["ziyaretci"], "goruntuleme": x["goruntuleme"]}
                for x in grup("sayfa", 15)]
    return {
        "ok": True, "aralik": aralik, "anlik": anlik, "ozet": ozet, "seri": seri,
        "kaynaklar": kaynaklar, "siteler": siteler, "sayfalar": sayfalar,
        "cihazlar": grup("cihaz", 5), "kampanyalar": [x for x in grup("utm", 10) if x["ad"] not in ("-", "")],
    }
