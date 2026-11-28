/* The standalone explorer (mount_explorer): one page with a tab per part the
   app enabled. Each tab is a panel from console.js or events.js. */
(function () {
  var R = window.ResourceExplorer;
  if (!R) return;
  var C = R.config, L = R.labels, el = R.el;
  var root = document.getElementById("explorer");
  if (!root) return;

  function operations() {
    var names = Object.keys(C.resources).filter(function (n) { return C.resources[n].length; });
    var picker = el("select", { "aria-label": L.resource });
    names.forEach(function (n) { picker.appendChild(el("option", { value: n, text: n })); });
    var holder = el("div", {});
    function render() {
      holder.textContent = "";
      if (!picker.value) return;
      var panel = R.resourceConsole(picker.value, C.resources[picker.value], true);
      panel.open = true;
      holder.appendChild(panel);
    }
    var wanted = R.load("re-explorer-resource");
    if (wanted && names.indexOf(wanted) >= 0) picker.value = wanted;
    picker.addEventListener("change", function () { R.save("re-explorer-resource", picker.value); render(); });
    render();
    var parts = [el("label", { className: "explorer-picker" }, [el("span", { text: L.resource }), picker]), holder];
    if (C.me) parts.push(R.whoAmI(true));
    return el("div", {}, parts);
  }

  var tabs = [{ key: "operations", label: L.tab_operations, build: operations }];
  if (R.panels.events) tabs.push({ key: "events", label: L.tab_events, build: R.panels.events });
  ["jobs", "publish"].forEach(function (key) {
    if (R.panels[key]) tabs.push({ key: key, label: L["tab_" + key], build: R.panels[key] });
  });

  var nav = el("nav", { className: "explorer-tabs", role: "tablist" });
  var body = el("section", { className: "explorer-body", role: "tabpanel" });
  var built = {};
  function show(key) {
    tabs.forEach(function (tab) {
      tab.button.setAttribute("aria-selected", tab.key === key ? "true" : "false");
    });
    var tab = tabs.filter(function (t) { return t.key === key; })[0] || tabs[0];
    if (!built[tab.key]) built[tab.key] = tab.build();
    body.textContent = "";
    body.appendChild(built[tab.key]);  // a panel keeps its state (a live stream) across tabs
    R.save("re-explorer-tab", tab.key);
    if (location.hash !== "#" + tab.key) history.replaceState(null, "", "#" + tab.key);
  }
  tabs.forEach(function (tab) {
    tab.button = el("button", { type: "button", role: "tab", text: tab.label });
    tab.button.addEventListener("click", function () { show(tab.key); });
    nav.appendChild(tab.button);
  });
  root.appendChild(el("p", { className: "note", text: L.intro + (R.api ? " " + R.api : "") }));
  root.appendChild(R.settings());
  root.appendChild(nav);
  root.appendChild(body);
  show(location.hash.slice(1) || R.load("re-explorer-tab") || "operations");
})();
