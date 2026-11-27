/* Try-it console of the documentation site (resource-engine docs site --console).
   Calls the running API with the visitor's own token; reads by default, changes
   only after "Allow changes". Everything from the API is shown as text. */
(function () {
  var C = window.CONSOLE;
  if (!C) return;
  var L = C.labels;
  var api = (C.api || "").replace(/\/$/, "");
  var lang = document.documentElement.lang || "";

  function load(key) { try { return sessionStorage.getItem(key) || ""; } catch (e) { return ""; } }
  function save(key, value) { try { sessionStorage.setItem(key, value); } catch (e) { /* private mode */ } }

  function el(tag, props, children) {
    var node = document.createElement(tag);
    Object.keys(props || {}).forEach(function (k) {
      if (k === "text") node.textContent = props[k];
      else if (k === "className") node.className = props[k];
      else node.setAttribute(k, props[k]);
    });
    (children || []).forEach(function (child) { if (child) node.appendChild(child); });
    return node;
  }

  function settings() {
    var token = el("input", { type: "password", autocomplete: "off", spellcheck: "false",
      placeholder: L.token_hint, "aria-label": L.token });
    token.value = load("re-console-token");
    token.addEventListener("change", function () { save("re-console-token", token.value.trim()); });
    var allow = el("input", { type: "checkbox", "aria-label": L.allow_changes });
    allow.checked = load("re-console-allow") === "1";
    allow.addEventListener("change", function () { save("re-console-allow", allow.checked ? "1" : ""); });
    return el("div", { className: "console-settings" }, [
      el("label", {}, [el("span", { text: L.token }), token]),
      el("label", { className: "inline" }, [allow, el("span", { text: L.allow_changes })]),
    ]);
  }

  function allowed() { return load("re-console-allow") === "1"; }

  function call(method, path, body, headers) {
    var init = { method: method, headers: Object.assign({ "Accept": "application/json" }, headers || {}) };
    var token = load("re-console-token");
    if (token) init.headers["Authorization"] = "Bearer " + token;  // also API keys (rek_...)
    if (lang) init.headers["Accept-Language"] = lang;
    if (body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = typeof body === "string" ? body : JSON.stringify(body);
    }
    var started = performance.now();
    return fetch(api + path, init).then(function (response) {
      return response.text().then(function (text) {
        var data = null;
        try { data = JSON.parse(text); } catch (e) { /* not JSON */ }
        return { status: response.status, statusText: response.statusText, ok: response.ok,
          headers: response.headers, text: text, data: data,
          ms: Math.round(performance.now() - started) };
      });
    }, function (error) {
      return { status: 0, statusText: L.unreachable + " (" + error.message + ")", ok: false,
        headers: new Headers(), text: "", data: null, ms: 0 };
    });
  }

  function show(target, result) {
    target.textContent = "";
    var line = result.status ? result.status + " " + result.statusText + " · " + result.ms + " ms"
      : result.statusText;
    target.appendChild(el("p", { className: "console-status " + (result.ok ? "ok" : "fail"), text: line }));
    var shown = ["content-language", "etag", "x-request-id", "retry-after", "location"]
      .filter(function (h) { return result.headers.get(h); })
      .map(function (h) { return h + ": " + result.headers.get(h); });
    if (shown.length) target.appendChild(el("pre", { className: "console-headers", text: shown.join("\n") }));
    var body = result.data !== null ? JSON.stringify(result.data, null, 2) : result.text;
    if (body) target.appendChild(el("pre", { text: body.slice(0, 200000) }));
  }

  // ------------------------------------------------------------ a resource page
  function resourceConsole(resource, ops) {
    var select = el("select", { "aria-label": L.operation });
    ops.forEach(function (op, i) {
      select.appendChild(el("option", { value: String(i), text: op.method + " " + op.path +
        (op.summary ? " — " + op.summary : "") }));
    });
    var form = el("div", { className: "console-form" });
    var output = el("div", { className: "console-output", "aria-live": "polite" });
    var extra = el("div", { className: "console-extra" });
    var inputs = {};
    var bodyField = null;

    function render() {
      var op = ops[Number(select.value)];
      form.textContent = ""; inputs = {}; bodyField = null;
      op.params.forEach(function (p) {
        var input = el("input", { type: "text", "aria-label": p.name, placeholder: p.description || "" });
        if (p.in === "path" && /id$/.test(p.name)) input.value = load("re-console-id-" + resource);
        inputs[p.name] = { param: p, input: input };
        var label = el("label", { className: p.in === "path" ? "required" : "" }, [
          el("span", { text: p.name + (p.in === "header" ? " (" + L.header + ")" : "") }), input]);
        form.appendChild(label);
      });
      if (op.body !== null && op.body !== undefined) {
        bodyField = el("textarea", { rows: "8", spellcheck: "false", "aria-label": L.body });
        bodyField.value = JSON.stringify(op.body, null, 2);
        form.appendChild(el("label", { className: "wide" }, [el("span", { text: L.body }), bodyField]));
      }
      if (op.upload) form.appendChild(el("p", { className: "note", text: L.upload_note }));
      var send = el("button", { type: "button", text: op.method === "GET" ? L.send : L.send_change });
      send.addEventListener("click", function () { submit(op); });
      form.appendChild(send);
      if (op.method !== "GET" && !allowed()) form.appendChild(el("p", { className: "note", text: L.changes_off }));
    }

    function submit(op) {
      if (op.method !== "GET" && !allowed()) { output.textContent = L.changes_off; return; }
      var path = op.path, query = [], headers = {}, missing = [];
      Object.keys(inputs).forEach(function (name) {
        var item = inputs[name], value = item.input.value.trim();
        if (item.param.in === "path") {
          if (!value) missing.push(name);
          path = path.replace("{" + name + "}", encodeURIComponent(value));
          if (/id$/.test(name) && value) save("re-console-id-" + resource, value);
        } else if (value && item.param.in === "query") {
          query.push(encodeURIComponent(name) + "=" + encodeURIComponent(value));
        } else if (value) headers[name] = value;
      });
      if (missing.length) { output.textContent = L.missing + ": " + missing.join(", "); return; }
      var body;
      if (bodyField) {
        try { body = JSON.parse(bodyField.value || "null"); } catch (e) {
          output.textContent = L.bad_json + ": " + e.message; return;
        }
      }
      output.textContent = L.sending;
      extra.textContent = "";
      call(op.method, path + (query.length ? "?" + query.join("&") : ""), body, headers)
        .then(function (result) {
          show(output, result);
          // a record just created or read: its id fills the next forms
          if (result.ok && result.data && result.data.id !== undefined && result.data.id !== null) {
            save("re-console-id-" + resource, String(result.data.id));
          }
          if (result.status === 403 && op.action) explainButton(op, inputs);
          if (result.ok && op.method === "GET" && /\{[a-z_]*id\}$/.test(op.path)) nextSteps(path);
        });
    }

    function explainButton(op, current) {
      var button = el("button", { type: "button", text: L.why });
      button.addEventListener("click", function () {
        var id = null;
        Object.keys(current).forEach(function (n) {
          if (current[n].param.in === "path" && current[n].input.value) id = current[n].input.value;
        });
        var request = { action: op.action };
        if (id !== null) request.entity_id = /^\d+$/.test(id) ? Number(id) : id;
        call("POST", "/authz/explain", request).then(function (result) {
          var box = el("div", { className: "console-output" });
          extra.textContent = ""; extra.appendChild(box);
          if (result.status !== 403) { show(box, result); return; }
          // explaining is for policy editors: show the caller's own view instead
          call("GET", "/authz/me").then(function (me) {
            if (!me.ok || !me.data) { show(box, result); return; }
            var state = (me.data.actions || {})[op.action] || L.not_granted;
            box.appendChild(el("p", { text: L.explain_editors }));
            box.appendChild(el("pre", { text: L.your_roles + ": " + (me.data.roles || []).join(", ") +
              "\n" + op.action + ": " + state }));
          });
        });
      });
      extra.textContent = "";
      extra.appendChild(button);
    }

    function nextSteps(recordPath) {
      var transitions = ops.filter(function (op) { return /\/transitions\/[^/]+$/.test(op.path); });
      if (!transitions.length) return;
      call("GET", recordPath + "/transitions").then(function (result) {
        if (!result.ok || !Array.isArray(result.data)) return;
        var row = el("p", { className: "console-next" }, [el("span", { text: L.next + ": " })]);
        if (!result.data.length) row.appendChild(el("span", { text: L.none }));
        result.data.forEach(function (event) {
          var index = ops.findIndex(function (op) { return /\/transitions\/[^/]+$/.test(op.path) &&
            op.path.slice(op.path.lastIndexOf("/") + 1) === event; });
          var button = el("button", { type: "button", text: event });
          button.addEventListener("click", function () {
            if (index >= 0) { select.value = String(index); render(); select.focus(); }
          });
          row.appendChild(button);
        });
        extra.appendChild(row);
      });
    }

    select.addEventListener("change", render);
    render();
    return el("details", { className: "console", id: "try-it" }, [
      el("summary", { text: L.try_it }),
      el("p", { className: "note", text: L.intro + (api ? " " + api : "") }),
      settings(), select, form, output, extra,
    ]);
  }

  // ---------------------------------------------------------------- the home page
  function whoAmI() {
    var output = el("div", { className: "console-output", "aria-live": "polite" });
    var button = el("button", { type: "button", text: L.who_am_i });
    button.addEventListener("click", function () {
      output.textContent = L.sending;
      call("GET", "/authz/me").then(function (result) { show(output, result); });
    });
    return el("details", { className: "console", id: "try-it" }, [
      el("summary", { text: L.who_am_i }),
      el("p", { className: "note", text: L.who_intro }),
      settings(), button, output,
    ]);
  }

  var main = document.querySelector("main");
  var heading = main && main.querySelector("h1");
  if (!heading) return;
  var resource = document.body.dataset.resource;
  var panel = null;
  if (resource && C.resources[resource] && C.resources[resource].length) {
    panel = resourceConsole(resource, C.resources[resource]);
  } else if (document.body.dataset.page === "index" && C.me) {
    panel = whoAmI();
  }
  if (panel) heading.insertAdjacentElement("afterend", panel);
})();
