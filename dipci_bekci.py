"""
dipci.tr — Otonom Sağlık Bekçisi ve Kendini Onaran Koruma Motoru (dipci_bekci.py)
Her 5 dakikada bir systemd (`dipci-bekci.timer`) tarafından otomatik çalıştırılır.

Tekrar eden tüm sorunları kullanıcı fark etmeden ÖNCE veya OLUR OLMAZ çözer:
  1. Süresini aşan (takılmış) arka plan tarayıcılarını otomatik durdurur.
  2. SQLite WAL dosyası 300 MB'ı geçince PASSIVE, 900 MB'ı geçince TRUNCATE checkpoint ile
     şişmeyi anında sıfırlar (4–24 GB WAL şişmesini kalıcı engeller).
  3. Canlı web sunucusunu (127.0.0.1:8090) ve /api/products yanıt süresini ölçer;
     yanıt vermezse, 7 sn'yi aşarsa veya bir işçi >4.5 GB RAM'e ulaşırsa WAL'ı boşaltıp
     `dipci.service` servisini yeniden başlatır ve önbelleği ısıtır.
  4. Her gece 04:00–04:15 arasında fiyatsız (offer_count <= 0) ölü kayıtları temizler,
     `PRAGMA optimize` çalıştırır ve günlük log dosyasını 2 MB altında tutar.
"""

import json
import os
import signal
import sqlite3
import subprocess
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

KOK = Path(__file__).resolve().parent
DATA_DIR = KOK / "data"
DB_PATH = Path(os.getenv("DIPCI_DB_PATH", str(DATA_DIR / "dipci_platform.db")))
WAL_PATH = Path(str(DB_PATH) + "-wal")
LOG_PATH = DATA_DIR / "bekci.log"
STATE_PATH = DATA_DIR / "bekci_durum.json"

# Arka plan işleri için azami izin verilen çalışma süreleri (saniye)
MAX_RUNTIME_SEC: Dict[str, int] = {
    "reklamaction_feed.py": 75 * 60,   # en fazla 75 dk (eski: 5 saat takılıyordu)
    "akakce_dusenler.py": 25 * 60,     # en fazla 25 dk
    "akakce_katalog.py": 20 * 60,      # en fazla 20 dk
    "live_sync.py": 25 * 60,           # en fazla 25 dk
    "fast_radar.py": 10 * 60,          # en fazla 10 dk
    "denetim.py": 20 * 60,             # en fazla 20 dk
    "gorsel_tamamla.py": 20 * 60,      # en fazla 20 dk
    "auto_catalog.py": 30 * 60,        # en fazla 30 dk
}

WAL_PASSIVE_MB = 300.0
WAL_TRUNCATE_MB = 900.0
WORKER_MAX_RSS_MB = 6000.0  # yalnız gerçek (anonim) bellek; sunucu 48 GB (27 Kas)
HTTP_TIMEOUT_SEC = 7.5


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > 2 * 1024 * 1024:
            # 2 MB üstünde son 400 satırı koru
            lines = LOG_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()[-400:]
            LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
    print(line, end="")


def _list_dipci_processes() -> List[Tuple[int, int, float, str]]:
    """Dönüş: [(pid, elapsed_sec, rss_mb, cmdline), ...]"""
    out: List[Tuple[int, int, float, str]] = []
    try:
        raw = subprocess.check_output(
            ["ps", "-eo", "pid=,etimes=,rss=,args="],
            text=True,
            timeout=5,
        )
        for line in raw.splitlines():
            parts = line.strip().split(None, 3)
            if len(parts) < 4:
                continue
            pid_s, etime_s, rss_s, cmd = parts
            if "/opt/dipci/" not in cmd or "dipci_bekci.py" in cmd:
                continue
            out.append((int(pid_s), int(etime_s), round(int(rss_s) / 1024.0, 1), cmd))
    except Exception:
        pass
    return out


def _terminate_pid(pid: int, reason: str) -> None:
    _log(f"ONARIM: PID {pid} sonlandırılıyor ({reason})")
    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(2)
        os.kill(pid, 0)
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except Exception as exc:
        _log(f"UYARI: PID {pid} sonlandırma hatası: {exc}")


def kill_runaway_jobs() -> List[str]:
    killed = []
    for pid, etimes, rss_mb, cmd in _list_dipci_processes():
        if "uvicorn" in cmd or "multiprocessing" in cmd:
            continue
        for script_name, max_sec in MAX_RUNTIME_SEC.items():
            if script_name in cmd and etimes > max_sec:
                _terminate_pid(pid, f"{script_name} {etimes}s > {max_sec}s sınırı")
                killed.append(f"{script_name}:{pid}:{etimes}s")
                break
    return killed


def _anon_mb(pid: int, varsayilan: float) -> float:
    """RSS veritabanı dosyasının bellek eşlemesini (mmap) de sayar; o bellek gerçek tüketim değildir.
    Yalnız anonim belleği (RssAnon) ölç (27 Kas 2026: yanlış alarm her 5 dk siteyi yeniden başlatıp aktarımları kesiyordu)."""
    try:
        for satir in open(f"/proc/{pid}/status", encoding="utf-8"):
            if satir.startswith("RssAnon:"):
                return int(satir.split()[1]) / 1024.0
    except Exception:
        pass
    return varsayilan


def check_worker_memory() -> bool:
    for pid, etimes, rss_mb, cmd in _list_dipci_processes():
        if ("uvicorn" in cmd or "multiprocessing.spawn" in cmd):
            rss_mb = _anon_mb(pid, rss_mb)
        if ("uvicorn" in cmd or "multiprocessing.spawn" in cmd) and rss_mb > WORKER_MAX_RSS_MB:
            _log(f"UYARI: Web işçisi PID {pid} yüksek bellek kullanıyor ({rss_mb:.0f} MB > {WORKER_MAX_RSS_MB:.0f} MB)")
            return True
    return False


def wal_size_mb() -> float:
    try:
        return round(WAL_PATH.stat().st_size / (1024 * 1024), 1) if WAL_PATH.exists() else 0.0
    except Exception:
        return 0.0


def run_wal_checkpoint(mode: str = "PASSIVE") -> Tuple[bool, float]:
    before = wal_size_mb()
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=20)
        conn.execute("PRAGMA busy_timeout = 20000")
        conn.execute("PRAGMA journal_size_limit = 268435456")
        res = conn.execute(f"PRAGMA wal_checkpoint({mode})").fetchone()
        conn.close()
        after = wal_size_mb()
        _log(f"WAL checkpoint({mode}): {before} MB -> {after} MB (sonuç={res})")
        return True, after
    except Exception as exc:
        _log(f"UYARI: WAL checkpoint({mode}) başarısız: {exc}")
        return False, before


def probe_http(path: str, timeout: float = HTTP_TIMEOUT_SEC) -> Tuple[int, float]:
    url = f"http://127.0.0.1:8090{path}"
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "dipci-bekci/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read(4096)
            return resp.status, round((time.perf_counter() - t0) * 1000, 1)
    except Exception:
        return 0, round((time.perf_counter() - t0) * 1000, 1)


def warm_web_caches() -> None:
    for path in (
        "/",
        "/api/products?sort=ihtiyac&page=1&limit=48",
        "/api/products?limit=24",
        "/api/price-drops?limit=60",
        "/sitemap.xml",
    ):
        st, ms = probe_http(path, timeout=20.0)
        _log(f"ISITMA: {path} -> HTTP {st} ({ms} ms)")


def stop_active_batch_writers() -> int:
    count = 0
    for pid, etimes, rss_mb, cmd in _list_dipci_processes():
        if "uvicorn" in cmd or "multiprocessing" in cmd:
            continue
        if any(s in cmd for s in MAX_RUNTIME_SEC):
            _terminate_pid(pid, "Acil WAL/Sağlık onarımı için duraklatıldı")
            count += 1
    return count


def restart_dipci_service() -> bool:
    _log("ONARIM: dipci.service yeniden başlatılıyor...")
    try:
        subprocess.run(["systemctl", "restart", "dipci.service"], check=True, timeout=45)
        # Uvicorn açılışını bekle (en fazla 25 sn)
        for _ in range(12):
            time.sleep(2)
            st, _ = probe_http("/", timeout=4.0)
            if st == 200:
                _log("ONARIM: dipci.service sağlıklı ayağa kalktı.")
                warm_web_caches()
                return True
        _log("HATA: dipci.service yeniden başlatıldı ama 25 sn içinde yanıt vermedi.")
        return False
    except Exception as exc:
        _log(f"HATA: systemctl restart dipci.service başarısız: {exc}")
        return False


def daily_hygiene_if_due() -> bool:
    now = datetime.now()
    if not (now.hour == 4 and now.minute < 15):
        return False
    today_tag = now.strftime("%Y-%m-%d")
    prev_day = ""
    if STATE_PATH.exists():
        try:
            prev_day = json.loads(STATE_PATH.read_text(encoding="utf-8")).get("last_hygiene_day", "")
        except Exception:
            pass
    if prev_day == today_tag:
        return False

    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=25)
        conn.execute("PRAGMA busy_timeout = 25000")
        dead = conn.execute("SELECT COUNT(*) FROM product_catalog_index WHERE offer_count <= 0").fetchone()[0]
        if dead:
            conn.execute("DELETE FROM product_catalog_index WHERE offer_count <= 0")
        conn.execute("PRAGMA optimize")
        conn.commit()
        conn.close()
        run_wal_checkpoint("TRUNCATE")
        _log(f"GÜNLÜK BAKIM: {dead} fiyatsız ölü kayıt temizlendi, PRAGMA optimize ve WAL temizliği yapıldı.")
        return True
    except Exception as exc:
        _log(f"UYARI: Günlük bakım atlandı: {exc}")
        return False


def emergency_wal_truncate_and_restart() -> bool:
    """
    Kritik WAL şişmesinde (>900 MB) okuyucu kilitlerini geçici olarak bırakıp
    WAL dosyasını kesin olarak 0 MB'a indirir, ardından dipci.service'i başlatıp ısıtır.
    """
    _log("ONARIM: Kritik WAL temizliği için dipci.service geçici durduruluyor...")
    stop_active_batch_writers()
    try:
        subprocess.run(["systemctl", "stop", "dipci.service"], check=False, timeout=30)
    except Exception:
        pass
    time.sleep(2)
    ok_cp, after_mb = run_wal_checkpoint("TRUNCATE")
    _log(f"ONARIM: Kesin WAL temizliği tamamlandı -> {after_mb} MB (ok={ok_cp})")
    try:
        subprocess.run(["systemctl", "start", "dipci.service"], check=True, timeout=30)
        for _ in range(15):
            time.sleep(2)
            st, _ = probe_http("/", timeout=4.0)
            if st == 200:
                _log("ONARIM: dipci.service temiz WAL ile sağlıklı ayağa kalktı.")
                warm_web_caches()
                return True
    except Exception as exc:
        _log(f"HATA: dipci.service başlatılamadı: {exc}")
    return False


def main() -> None:
    actions: List[str] = []

    # 1) Süresini aşmış takılı arka plan işlerini temizle
    killed = kill_runaway_jobs()
    if killed:
        actions.append(f"killed_runaway:{','.join(killed)}")

    # 2) WAL şişme kontrolü
    wal_mb = wal_size_mb()
    if wal_mb >= WAL_TRUNCATE_MB:
        actions.append(f"wal_critical:{wal_mb}MB")
        ok_cp, new_wal = run_wal_checkpoint("PASSIVE")
        if new_wal >= WAL_TRUNCATE_MB:
            emergency_wal_truncate_and_restart()
            actions.append(f"emergency_wal_truncate_to_{wal_size_mb()}MB")
    elif wal_mb >= WAL_PASSIVE_MB:
        run_wal_checkpoint("PASSIVE")
        actions.append(f"wal_passive:{wal_mb}MB")

    # 3) Canlı HTTP ve bellek kontrolü
    home_st, home_ms = probe_http("/")
    api_st, api_ms = probe_http("/api/products?sort=ihtiyac&page=1&limit=48")
    high_mem = check_worker_memory()

    unhealthy = (home_st != 200) or (api_st != 200)
    if unhealthy:
        # Tek seferlik anlık dalgalanma mı? 3 sn sonra tekrar doğrula
        time.sleep(3)
        home_st, home_ms = probe_http("/")
        api_st, api_ms = probe_http("/api/products?sort=ihtiyac&page=1&limit=48")
        unhealthy = (home_st != 200) or (api_st != 200)

    if unhealthy or high_mem:
        reason = f"home={home_st}({home_ms}ms) api={api_st}({api_ms}ms) high_mem={high_mem}"
        _log(f"ALARM: Sağlık eşiği aşıldı ({reason}) — otomatik onarım başlatılıyor.")
        if unhealthy:  # yalnız bellek yüksekse arka plan aktarımları kesilmez, yalnız web yeniden başlar (27 Kas)
            stop_active_batch_writers()
            run_wal_checkpoint("TRUNCATE")
        restart_dipci_service()
        actions.append(f"auto_restarted:{reason}")

    # 4) Gece 04:00 günlük bakım
    hygiene_done = daily_hygiene_if_due()
    if hygiene_done:
        actions.append("daily_hygiene_ok")

    prev_state = {}
    if STATE_PATH.exists():
        try:
            prev_state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass

    state = {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "home_http": home_st,
        "home_ms": home_ms,
        "api_http": api_st,
        "api_ms": api_ms,
        "wal_mb": wal_size_mb(),
        "actions": actions,
        "last_hygiene_day": datetime.now().strftime("%Y-%m-%d") if hygiene_done else prev_state.get("last_hygiene_day", ""),
    }
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    if not actions:
        _log(f"SAĞLIKLI: home={home_st}({home_ms}ms) api={api_st}({api_ms}ms) wal={state['wal_mb']}MB")


if __name__ == "__main__":
    main()
