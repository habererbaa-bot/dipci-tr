"""
Engellenen sitelere (Akakçe, Trendyol, Hepsiburada...) Kemal'in ev internetinden gitme (Kemal, 17 Kas 2026).

Kemal'in bilgisayarındaki deploy/ev_proxy.py, SSH tüneliyle sunucuda 127.0.0.1:8899'u açar.
Tünel açıksa bu sitelere giden urllib istekleri oradan geçer; kapalıysa istek eskisi gibi doğrudan gider.
kur() bir kez çağrılır (urllib'in genel açıcısını değiştirir; diğer sitelere dokunmaz).
"""
import base64
import os
import socket
import time
import urllib.parse
import urllib.request

EV_PORT = 8899
IZINLI = ("akakce.com", "cimri.com", "epey.com", "trendyol.com", "hepsiburada.com", "n11.com", "pazarama.com",
          "mediamarkt.com.tr", "teknosa.com", "vatanbilgisayar.com", "pttavm.com", "idefix.com", "akakce.io")
_durum = {"ts": 0.0, "acik": False}


def tunel_acik() -> bool:
    if time.time() - _durum["ts"] < 20:
        return _durum["acik"]
    try:
        with socket.create_connection(("127.0.0.1", EV_PORT), timeout=0.4):
            acik = True
    except OSError:
        acik = False
    _durum.update(ts=time.time(), acik=acik)
    return acik


def _izinli(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    return any(h == d or h.endswith("." + d) for d in IZINLI)


class _EvYonlendirici(urllib.request.BaseHandler):
    handler_order = 99  # ProxyHandler'dan (100) önce

    def _yonlendir(self, req):
        if getattr(req, "_tunnel_host", None) or not _izinli(req.host):
            return None
        # 1) Türkiye ISP proxy'si (.env TR_PROXY_URL; Kemal 17 Kas 2026'da aldı) — bilgisayar kapalıyken de çalışır
        tr = os.getenv("TR_PROXY_URL", "").strip()
        if tr:
            u = urllib.parse.urlsplit(tr)
            req.set_proxy(f"{u.hostname}:{u.port}", "http")
            if u.username:
                kimlik = f"{urllib.parse.unquote(u.username)}:{urllib.parse.unquote(u.password or '')}"
                req.add_header("Proxy-Authorization", "Basic " + base64.b64encode(kimlik.encode()).decode())
        # 2) Yedek: Kemal'in ev interneti (SSH tüneli açıksa)
        elif tunel_acik():
            req.set_proxy(f"127.0.0.1:{EV_PORT}", "http")
        return None  # isteği asıl HTTPS işleyicisi göndersin

    https_open = _yonlendir


_kuruldu = False


def kur() -> None:
    global _kuruldu
    if _kuruldu:
        return
    urllib.request.install_opener(urllib.request.build_opener(_EvYonlendirici()))
    _kuruldu = True
