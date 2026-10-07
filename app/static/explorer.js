// Client-side project explorer for the static (GitHub Pages) build.
// Mirrors the server's filters (app/core.py query_projects): every search word must appear in the search text;
// category / status / department / link confidence match exactly; signal = has that signal code; depth = lifecycle >= n;
// "has" checkboxes = project has every chosen kind of evidence. Filters are kept in the URL, so links like
// projects/?signal=paid_over_contract keep working.
(function () {
  "use strict";
  var EXACT = { category: "category", status: "status", dept: "dept", link: "link" };
  var SORTS = {
    recent: function (a, b) { return cmp(b.dataset.recent, a.dataset.recent); },
    value: function (a, b) { return num(b.dataset.value) - num(a.dataset.value); },
    paid: function (a, b) { return num(b.dataset.paid) - num(a.dataset.paid); },
    depth: function (a, b) { return num(b.dataset.depth) - num(a.dataset.depth) || cmp(b.dataset.recent, a.dataset.recent); },
    signals: function (a, b) { return num(b.dataset.siglevel) - num(a.dataset.siglevel) || num(b.dataset.nsig) - num(a.dataset.nsig); },
    oldest: function (a, b) { return cmp(a.dataset.oldest, b.dataset.oldest); }
  };
  function num(x) { return parseFloat(x) || 0; }
  function cmp(a, b) { return a < b ? -1 : a > b ? 1 : 0; }

  // d: a card's data attributes; s: the filter state. Pure, so it can be tested without a browser.
  function matches(d, s) {
    var words = (s.q || "").toLowerCase().split(/\s+/).filter(Boolean);
    var ok = words.every(function (w) { return d.search.indexOf(w) >= 0; });
    for (var k in EXACT) if (ok && s[k]) ok = d[EXACT[k]] === s[k];
    if (ok && s.signal) ok = d.signals.indexOf(" " + s.signal + " ") >= 0;
    if (ok && s.depth) ok = num(d.depth) >= num(s.depth);
    if (ok) ok = (s.has || []).every(function (f) { return d.flags.indexOf(" " + f + " ") >= 0; });
    return ok;
  }

  if (typeof module !== "undefined" && module.exports) { module.exports = { matches: matches, SORTS: SORTS }; }
  if (typeof document === "undefined") return;

  var form = document.getElementById("explorer");
  var list = document.querySelector(".plist");
  if (!form || !list) return;
  var cards = Array.prototype.slice.call(list.children);
  var count = document.getElementById("count");
  var empty = document.getElementById("empty");

  function state() {
    var fd = new FormData(form), s = { has: fd.getAll("has") };
    ["q", "category", "status", "signal", "depth", "dept", "link", "sort"].forEach(function (k) { s[k] = (fd.get(k) || "").trim(); });
    return s;
  }

  function fromUrl() {
    var p = new URLSearchParams(location.search);
    Array.prototype.forEach.call(form.elements, function (el) {
      if (!el.name) return;
      if (el.type === "checkbox") el.checked = p.getAll(el.name).indexOf(el.value) >= 0;
      else if (p.has(el.name)) el.value = p.get(el.name);
    });
  }

  function apply(push) {
    var s = state(), shown = 0;
    cards.forEach(function (c) {
      var ok = matches(c.dataset, s);
      c.hidden = !ok;
      if (ok) shown++;
    });
    cards.slice().sort(SORTS[s.sort] || SORTS.recent).forEach(function (c) { list.appendChild(c); });
    if (count) count.textContent = shown + (shown === 1 ? " project" : " projects");
    if (empty) empty.hidden = shown > 0;
    if (push) {
      var p = new URLSearchParams();
      Object.keys(s).forEach(function (k) {
        if (k === "has") s.has.forEach(function (v) { p.append("has", v); });
        else if (s[k] && !(k === "sort" && s[k] === "recent")) p.set(k, s[k]);
      });
      var qs = p.toString();
      history.replaceState(null, "", location.pathname + (qs ? "?" + qs : ""));
    }
  }

  form.addEventListener("submit", function (e) { e.preventDefault(); apply(true); });
  form.addEventListener("change", function () { apply(true); });
  form.addEventListener("input", function (e) { if (e.target.name === "q") apply(true); });
  var reset = form.querySelector(".reset");
  if (reset) reset.addEventListener("click", function (e) { e.preventDefault(); form.reset(); apply(true); });
  fromUrl();
  apply(false);
})();
