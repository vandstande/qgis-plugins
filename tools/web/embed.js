/*
 * Indlejring af plugin-oversigt på vandstande.dk
 *
 * Indsæt på fx vandstande.dk/timflow:
 *   <div data-vandstande-plugins="timflow"></div>
 *   <script src="https://<bruger>.github.io/qgis-plugins/embed.js" defer></script>
 *
 * data-vandstande-plugins = gruppe fra plugins.toml ("timflow", "anlaeg" eller "alle").
 * Viser altid nyeste version, downloadlink og repository-URL – ingen manuel opdatering.
 */
(function () {
  var script = document.currentScript;
  var base = script ? script.src.replace(/[^/]*$/, "") : "";

  var css =
    ".vp-box{font:inherit;color:inherit;margin:1.5em 0}" +
    ".vp-install{border:1px solid #d9e2e6;border-radius:10px;padding:16px;margin-bottom:14px;background:#fff}" +
    ".vp-url{display:flex;gap:8px;margin:10px 0}" +
    ".vp-url code{flex:1;background:#f6f9fa;border:1px solid #d9e2e6;border-radius:6px;padding:7px 10px;overflow-x:auto;white-space:nowrap;font-size:.9em}" +
    ".vp-url button{border:0;background:#1f6f8b;color:#fff;border-radius:6px;padding:7px 12px;cursor:pointer;font:inherit}" +
    ".vp-install ol{margin:6px 0 0;padding-left:20px}" +
    ".vp-card{display:flex;gap:14px;border:1px solid #d9e2e6;border-radius:10px;padding:14px;margin-bottom:8px;background:#fff}" +
    ".vp-card img,.vp-noicon{width:40px;height:40px;flex:none;border-radius:8px}" +
    ".vp-noicon{background:#d9e2e6}" +
    ".vp-card h4{margin:0 0 3px;font-size:1.05em}" +
    ".vp-ver{font-weight:400;opacity:.65;font-size:.9em}" +
    ".vp-card p{margin:0 0 6px;opacity:.85}" +
    ".vp-card .vp-links{font-size:.9em;margin:0}";

  function esc(s) {
    return String(s || "").replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function render(el, m) {
    var group = el.getAttribute("data-vandstande-plugins") || "alle";
    var showInstall = el.getAttribute("data-install") !== "nej";
    var list = m.plugins.filter(function (p) { return group === "alle" || p.gruppe === group; });
    var h = '<div class="vp-box">';
    if (showInstall) {
      h += '<div class="vp-install"><strong>Installér i QGIS med automatiske opdateringer</strong>' +
        '<div class="vp-url"><code>' + esc(m.repository_url) + '</code><button type="button">Kopiér</button></div>' +
        "<ol><li>QGIS: <em>Plugins → Administrer og installer plugins → Indstillinger</em></li>" +
        "<li>Tryk <em>Tilføj…</em>, giv det et navn (fx „Vandstande“) og indsæt URL'en</li>" +
        "<li>Tryk <em>Genindlæs alle repositories</em> og installér pluginsene under <em>Alle</em></li></ol></div>";
    }
    list.forEach(function (p) {
      h += '<div class="vp-card">' +
        (p.icon ? '<img src="' + esc(p.icon) + '" alt="">' : '<div class="vp-noicon"></div>') +
        "<div><h4>" + esc(p.name) + ' <span class="vp-ver">v' + esc(p.version) + "</span></h4>" +
        "<p>" + esc(p.description) + "</p>" +
        '<p class="vp-links"><a href="' + esc(p.download_url_latest) + '">Download zip</a> · ' +
        '<a href="' + esc(p.repository) + '">Kildekode</a></p></div></div>';
    });
    h += "</div>";
    el.innerHTML = h;
    var btn = el.querySelector(".vp-url button");
    if (btn) btn.addEventListener("click", function () {
      navigator.clipboard.writeText(m.repository_url).then(function () { btn.textContent = "Kopieret"; });
    });
  }

  function start() {
    var els = document.querySelectorAll("[data-vandstande-plugins]");
    if (!els.length) return;
    var style = document.createElement("style");
    style.textContent = css;
    document.head.appendChild(style);
    fetch(base + "manifest.json", { cache: "no-cache" })
      .then(function (r) { return r.json(); })
      .then(function (m) { els.forEach(function (el) { render(el, m); }); })
      .catch(function () {
        els.forEach(function (el) {
          el.innerHTML = '<p>Pluginoversigten kunne ikke hentes. Se <a href="' + base + '">' + base + "</a></p>";
        });
      });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
