"""
Ev interneti ara kapısı (Kemal, 17 Kas 2026: "benim internetimi kullansın").

Kemal'in bilgisayarında çalışır. Akakçe, Trendyol, Hepsiburada gibi siteler sunucumuzun (Almanya) adresini engelliyor;
bu betik sunucuyla bilgisayar arasında şifreli bir SSH tüneli açar ve sunucunun yalnız bu sitelere giden isteklerini
evin internetinden geçirir. Bilgisayar kapalıysa sunucu eskisi gibi doğrudan dener.

- Yalnız izin listesindeki sitelere izin verir (başka hiçbir yere aracılık etmez).
- Yalnız 127.0.0.1'i dinler; dışarıdan erişilemez, sunucu tünel üzerinden bağlanır.
- Tünel düşerse 20 sn içinde yeniden kurar.
Çalıştırma: pythonw ev_proxy.py   (Windows açılışında Başlangıç klasöründeki kısayolla kendiliğinden başlar)
"""
import select
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 8899
SUNUCU = "ubuntu@161.97.138.36"
IZINLI = ("akakce.com", "cimri.com", "epey.com", "trendyol.com", "hepsiburada.com", "n11.com", "pazarama.com",
          "mediamarkt.com.tr", "teknosa.com", "vatanbilgisayar.com", "pttavm.com", "idefix.com", "akakce.io")
GUNLUK = Path(__file__).with_name("ev_proxy.log")


def yaz(m: str) -> None:
    try:
        if GUNLUK.exists() and GUNLUK.stat().st_size > 2_000_000:
            GUNLUK.unlink()
        with GUNLUK.open("a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + m + "\n")
    except Exception:
        pass


def izinli(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    return any(h == d or h.endswith("." + d) for d in IZINLI)


class Kapi(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_CONNECT(self):
        host, _, port = self.path.partition(":")
        if not izinli(host) or port not in ("443", ""):
            self.send_error(403)
            return
        try:
            uzak = socket.create_connection((host, int(port or 443)), timeout=15)
        except Exception:
            self.send_error(502)
            return
        self.send_response(200, "Connection Established")
        self.end_headers()
        yerel = self.connection
        try:
            while True:
                r, _, _ = select.select([yerel, uzak], [], [], 60)
                if not r:
                    break
                for s in r:
                    veri = s.recv(65536)
                    if not veri:
                        return
                    (uzak if s is yerel else yerel).sendall(veri)
        except Exception:
            pass
        finally:
            uzak.close()


def tunel():
    while True:
        yaz("tünel açılıyor")
        p = subprocess.Popen(
            ["ssh", "-N", "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3", "-o", "ExitOnForwardFailure=yes",
             "-o", "BatchMode=yes", "-R", f"127.0.0.1:{PORT}:127.0.0.1:{PORT}", SUNUCU],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        p.wait()
        yaz(f"tünel kapandı (kod {p.returncode}), 20 sn sonra yeniden")
        time.sleep(20)


if __name__ == "__main__":
    threading.Thread(target=tunel, daemon=True).start()
    yaz("ara kapı başladı")
    ThreadingHTTPServer(("127.0.0.1", PORT), Kapi).serve_forever()
