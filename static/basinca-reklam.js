/*
  dipci.tr → basinca.com canlı reklam kodu (Kemal, 17 Kas 2026)

  Kullanım (her kart için bir satır; data-tur 8 koddan biri):
    <div class="dipci-reklam" data-tur="sok"></div>
    <script src="https://dipci.tr/reklam/basinca.js" async></script>

  Kod her açılışta dipci.tr'den o türün güncel ürünlerini çeker, rastgele birini seçer
  ve basınca kartı ölçüsünde (3:4 dikey) ürün görseli + 3-5 mağaza fiyatı olarak çizer.
  data-dondur="12" verilirse kart 12 saniyede bir başka ürüne geçer.
*/
(function () {
  "use strict";
  var API = "https://dipci.tr/api/basinca/reklamlar";
  var RENKLER = {
    "Amazon": ["#232F3E", "#FF9900"], "Hepsiburada": ["#FF6000", "#fff"], "Migros": ["#FF6600", "#fff"],
    "CarrefourSA": ["#004E9E", "#fff"], "BİM": ["#E30613", "#fff"], "A101": ["#00A0DF", "#fff"],
    "ŞOK": ["#EAB308", "#0F172A"], "Tarım Kredi": ["#16A34A", "#fff"], "Hakmar": ["#7C3AED", "#fff"],
    "Trendyol": ["#F27A1A", "#fff"], "n11": ["#5D3EBC", "#fff"], "Pazarama": ["#E11D48", "#fff"],
    "Teknosa": ["#F97316", "#fff"], "PttAVM": ["#0284C7", "#fff"], "Getir": ["#5D3EBC", "#FFD300"],
    "İdefix": ["#1E293B", "#fff"], "Koçtaş": ["#EA580C", "#fff"], "Vatan": ["#004B93", "#fff"],
    "MediaMarkt": ["#DF0000", "#fff"], "Çiçeksepeti": ["#2563EB", "#fff"]
  };

  // Yazı tipleri (basınca kartlarıyla aynı aile): sayfaya bir kez eklenir.
  if (!document.getElementById("dipci-reklam-font")) {
    var l = document.createElement("link");
    l.id = "dipci-reklam-font";
    l.rel = "stylesheet";
    l.href = "https://fonts.googleapis.com/css2?family=Anton&family=Oswald:wght@500;700&display=swap";
    document.head.appendChild(l);
  }

  var CSS =
    ":host{display:block;container-type:inline-size;width:100%}" +
    "a.k{position:relative;display:flex;flex-direction:column;aspect-ratio:3/4;width:100%;overflow:hidden;border-radius:12px;" +
    "background:#121826;color:#F8FAFC;text-decoration:none;font-family:Oswald,'Arial Narrow',Arial,sans-serif}" +
    ".g{position:relative;flex:1 1 0;min-height:36%;background:#fff;display:flex;align-items:center;justify-content:center;padding:7cqw 6cqw 3cqw}" +
    ".g img{max-width:100%;max-height:100%;object-fit:contain;display:block}" +
    ".rz{position:absolute;top:3cqw;left:4cqw;background:#16A34A;color:#fff;font-family:Anton,Impact,sans-serif;font-size:6.4cqw;line-height:1;padding:1.6cqw 2.6cqw;border-radius:1.6cqw}" +
    ".lg{position:absolute;top:3cqw;right:4cqw;background:#0B132B;color:#fff;font-family:Anton,Impact,sans-serif;font-size:4.6cqw;line-height:1;padding:1.6cqw 2.4cqw;border-radius:1.6cqw}" +
    ".lg b{color:#10B981;font-weight:400}" +
    ".c{height:.7cqw;background:#E11D2A;flex:0 0 auto}" +
    ".a{flex:0 0 auto;display:flex;flex-direction:column;padding:4cqw 4.8cqw 3.6cqw;min-height:0}" +
    ".e{flex:0 0 auto;align-self:flex-start;background:#E11D2A;color:#fff;font-weight:700;font-size:3.4cqw;letter-spacing:.02em;padding:.9cqw 2.4cqw;border-radius:.9cqw;text-transform:uppercase}" +
    ".b{font-family:Anton,Impact,sans-serif;font-size:8.2cqw;line-height:1.08;margin:2.6cqw 0 0;text-transform:uppercase;" +
    "display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}" +
    ".f{font-family:Anton,Impact,sans-serif;font-size:8.6cqw;line-height:1.15;margin-top:1.4cqw;" +
    "background:linear-gradient(180deg,#FFE14D,#FF9F0A);-webkit-background-clip:text;background-clip:text;color:transparent}" +
    ".m{margin-top:2.6cqw;display:grid;grid-template-columns:1fr 1fr;gap:1.4cqw}" +
    ".s{display:flex;align-items:center;justify-content:space-between;gap:1cqw;background:#1E2A3D;border:.3cqw solid #2B3A52;border-radius:1.2cqw;padding:1cqw 1.4cqw}" +
    ".s.w{border-color:#FACC15;background:#14321F}" +
    ".s i{font-style:normal;font-weight:700;font-size:2.9cqw;padding:.5cqw 1.4cqw;border-radius:.8cqw;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:55%}" +
    ".s span{font-weight:700;font-size:3.1cqw;white-space:nowrap;color:#E2E8F0}" +
    ".s.w span{color:#FACC15}" +
    ".k{transition:opacity .35s ease}.k.gizli{opacity:0}";

  function tl(v) {
    return Number(v || 0).toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " TL";
  }
  function esc(t) {
    return String(t == null ? "" : t).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function cizim(it) {
    var m = (it.magazalar || []).slice(0, 5).map(function (q, i) {
      var r = RENKLER[q.magaza] || ["#475569", "#fff"];
      return '<div class="s' + (i === 0 ? " w" : "") + '"><i style="background:' + r[0] + ";color:" + r[1] + '">' +
        esc(q.magaza) + "</i><span>" + tl(q.fiyat) + "</span></div>";
    }).join("");
    return '<a class="k" href="' + esc(it.link) + '" target="_blank" rel="sponsored noopener">' +
      '<div class="g">' + (it.rozet ? '<div class="rz">' + esc(it.rozet) + "</div>" : "") +
      '<div class="lg">dipci<b>.tr</b></div>' +
      '<img src="' + esc(it.urun_gorseli) + '" alt="' + esc(it.baslik) + '" loading="lazy"></div>' +
      '<div class="c"></div><div class="a">' +
      '<div class="e">' + esc(it.etiket) + "</div>" +
      '<div class="b">' + esc(it.baslik) + "</div>" +
      '<div class="f">En ucuz ' + tl(it.en_ucuz && it.en_ucuz.fiyat) + "</div>" +
      '<div class="m">' + m + "</div></div></a>";
  }

  function kur(el) {
    if (el.__dipci) return;
    el.__dipci = true;
    var tur = el.getAttribute("data-tur") || "karisik";
    var dondur = parseInt(el.getAttribute("data-dondur") || "0", 10);
    var kok = el.attachShadow ? el.attachShadow({ mode: "open" }) : el;
    var st = document.createElement("style");
    st.textContent = CSS;
    kok.appendChild(st);
    var yer = document.createElement("div");
    kok.appendChild(yer);

    fetch(API + "?tur=" + encodeURIComponent(tur) + "&limit=8", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var liste = (d && d.items) || [];
        if (!liste.length) { el.style.display = "none"; return; }
        var i = 0;
        yer.innerHTML = cizim(liste[0]);
        if (dondur >= 5 && liste.length > 1) {
          setInterval(function () {
            var k = yer.querySelector(".k");
            if (k) k.classList.add("gizli");
            setTimeout(function () {
              i = (i + 1) % liste.length;
              yer.innerHTML = cizim(liste[i]);
            }, 350);
          }, dondur * 1000);
        }
      })
      .catch(function () { el.style.display = "none"; });
  }

  function hepsi() {
    var l = document.querySelectorAll(".dipci-reklam");
    for (var i = 0; i < l.length; i++) kur(l[i]);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", hepsi);
  else hepsi();
  // Sonradan eklenen kartlar (sonsuz akış) için
  if (window.MutationObserver) {
    new MutationObserver(hepsi).observe(document.documentElement, { childList: true, subtree: true });
  }
})();
