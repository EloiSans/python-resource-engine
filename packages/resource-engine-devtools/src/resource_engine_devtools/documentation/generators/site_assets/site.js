(function () {
  var input = document.getElementById("search");
  var results = document.getElementById("results");
  var menu = document.getElementById("menu");
  var root = document.body.dataset.root || "";
  var nav = document.getElementById("nav");
  var toggle = document.getElementById("menu-toggle");
  toggle.addEventListener("click", function () {
    var open = nav.classList.toggle("open");
    toggle.setAttribute("aria-expanded", String(open));
  });
  function render(query) {
    var terms = query.toLowerCase().split(/\s+/).filter(Boolean);
    if (!terms.length) {
      results.hidden = true; menu.hidden = false; nav.classList.remove("searching"); return;
    }
    var hits = window.SITE_SEARCH.map(function (page) {
      var hay = (page.title + " " + page.text).toLowerCase();
      var score = 0;
      for (var i = 0; i < terms.length; i++) {
        if (hay.indexOf(terms[i]) < 0) return null;
        score += page.title.toLowerCase().indexOf(terms[i]) >= 0 ? 10 : 1;
      }
      return { page: page, score: score };
    }).filter(Boolean).sort(function (a, b) { return b.score - a.score; }).slice(0, 20);
    results.innerHTML = "";
    if (!hits.length) {
      var none = document.createElement("li");
      none.textContent = __NO_RESULTS__;
      results.appendChild(none);
    }
    hits.forEach(function (hit) {
      var li = document.createElement("li");
      var a = document.createElement("a");
      a.href = root + hit.page.path;
      a.textContent = hit.page.title;
      var at = hit.page.text.toLowerCase().indexOf(terms[0]);
      var small = document.createElement("small");
      small.textContent = hit.page.text.slice(Math.max(0, at - 40), at + 80);
      a.appendChild(small);
      li.appendChild(a);
      results.appendChild(li);
    });
    results.hidden = false; menu.hidden = true; nav.classList.add("searching");
  }
  input.addEventListener("input", function () { render(input.value); });
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter") { var first = results.querySelector("a"); if (first) location.href = first.href; }
    if (e.key === "Escape") { input.value = ""; render(""); }
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "/" && document.activeElement !== input) { e.preventDefault(); input.focus(); }
  });
})();
