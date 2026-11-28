/* SUB: committed events over server-sent events, read with fetch so the
   visitor's token goes in the Authorization header (EventSource cannot send
   it). R.subscribe({resource, type}) is the body of a SUB row: the stream of
   that event type of that resource; without a preset, every event. */
(function () {
  var R = window.ResourceExplorer;
  if (!R || !R.config.events) return;
  var C = R.config, L = R.labels, el = R.el, E = C.events;
  var MAX = 500;

  function headers() {
    var h = { "Accept": "text/event-stream" };
    var token = R.load("re-console-token");
    if (token) h["Authorization"] = "Bearer " + token;
    var lang = document.documentElement.lang;
    if (lang) h["Accept-Language"] = lang;
    return h;
  }

  function select(label, values, name) {
    var input = el("select", { "aria-label": label, name: name });
    input.appendChild(el("option", { value: "", text: L.any }));
    values.forEach(function (v) { input.appendChild(el("option", { value: v, text: v })); });
    return { input: input, label: el("label", {}, [el("span", { text: label }), input]) };
  }

  function text(label, name) {
    var input = el("input", { type: "text", "aria-label": label, name: name, spellcheck: "false" });
    return { input: input, label: el("label", {}, [el("span", { text: label }), input]) };
  }

  // Parses "event: x\nid: y\ndata: {...}\n\n" frames out of a growing buffer.
  function frames(buffer, onFrame) {
    var parts = buffer.split("\n\n");
    var rest = parts.pop();
    parts.forEach(function (frame) {
      var data = [], type = null;
      frame.split("\n").forEach(function (line) {
        if (line.indexOf("data:") === 0) data.push(line.slice(5).replace(/^ /, ""));
        else if (line.indexOf("event:") === 0) type = line.slice(6).trim();
      });
      if (data.length) onFrame(type, data.join("\n"));
    });
    return rest;
  }

  function panel(preset) {
    preset = preset || {};
    var resource = select(L.filter_resource, E.resources || [], "resource");
    var type = select(L.filter_type, (E.types || []).map(function (t) { return t.type; }), "type");
    if (preset.resource) resource.input.value = preset.resource;
    if (preset.type) type.input.value = preset.type;
    var version = text(L.filter_version, "version");
    var id = text(L.filter_id, "id");
    var connect = el("button", { type: "button", className: "execute", text: L.connect });
    var pause = el("button", { type: "button", text: L.pause, disabled: "disabled" });
    var clear = el("button", { type: "button", text: L.clear });
    var status = el("div", { className: "note", "aria-live": "polite", text: L.disconnected });
    var list = el("ol", { className: "events-list", reversed: "reversed" });
    var detail = el("div", { className: "console-output events-detail" });
    var controller = null, paused = false, held = [];

    function query() {
      var q = [];
      [["resource", resource], ["type", type], ["version", version], ["id", id]].forEach(
        function (pair) {
          var value = pair[1].input.value.trim();
          if (value) q.push(pair[0] + "=" + encodeURIComponent(value));
        });
      return q.length ? "?" + q.join("&") : "";
    }

    function add(event) {
      var env = event.envelope;
      var when = (env.occurred_at || "").replace("T", " ").slice(0, 19);
      var about = event.resource ? event.resource + " #" + event.entity_id : "—";
      var item = el("li", {}, [el("button", { type: "button", className: "event-row",
        text: when + "  " + env.type + " v" + (env.schema_version || "1") + "  " + about })]);
      item.firstChild.addEventListener("click", function () { open(event); });
      list.insertBefore(item, list.firstChild);
      while (list.children.length > MAX) list.removeChild(list.lastChild);
    }

    function open(event) {
      detail.textContent = "";
      var env = Object.assign({}, event.envelope);
      var payload = env.payload;
      if (!event.payload_visible) delete env.payload;
      detail.appendChild(el("h3", { text: L.envelope }));
      detail.appendChild(el("pre", { text: JSON.stringify(env, null, 2) }));
      if (event.payload_visible) {
        detail.appendChild(el("h3", { text: L.payload }));
        detail.appendChild(el("pre", { text: JSON.stringify(payload, null, 2) }));
      } else {
        detail.appendChild(el("p", { className: "note", text: L.payload_hidden }));
      }
      if (event.record) {
        detail.appendChild(el("h3", { text: L.record }));
        detail.appendChild(el("pre", { text: JSON.stringify(event.record, null, 2) }));
      }
      detail.appendChild(el("h3", { text: L.deliveries }));
      if (!E.deliveries) {
        detail.appendChild(el("p", { className: "note", text: L.deliveries_off }));
        return;
      }
      var box = el("div", {});
      detail.appendChild(box);
      R.call("GET", E.deliveries.replace("{message_id}", encodeURIComponent(env.id)))
        .then(function (result) {
          if (!result.ok || !Array.isArray(result.data)) { R.show(box, result); return; }
          if (!result.data.length) {
            box.appendChild(el("p", { className: "note", text: L.no_deliveries }));
            return;
          }
          box.appendChild(el("pre", { text: result.data.map(function (d) {
            return d.consumer + ": " + d.outcome + " · " + Math.round(d.seconds * 1000) + " ms" +
              (d.error ? " · " + d.error : "");
          }).join("\n") }));
        });
    }

    function stop(message) {
      if (controller) controller.abort();
      controller = null;
      connect.textContent = L.connect;
      pause.setAttribute("disabled", "disabled");
      status.textContent = message || L.disconnected;
    }

    function start() {
      controller = new AbortController();
      connect.textContent = L.disconnect;
      pause.removeAttribute("disabled");
      status.textContent = L.sending;
      var mine = controller;
      fetch(R.api + E.stream + query(), { headers: headers(), signal: mine.signal })
        .then(function (response) {
          if (!response.ok) {
            return response.text().then(function (body) {
              var data = null;
              try { data = JSON.parse(body); } catch (e) { /* not JSON */ }
              stop();
              R.show(status, { status: response.status, statusText: response.statusText,
                ok: false, headers: response.headers, text: body, data: data, ms: 0 });
            });
          }
          status.textContent = L.connected;
          var reader = response.body.getReader(), decoder = new TextDecoder(), buffer = "";
          function pump() {
            return reader.read().then(function (chunk) {
              if (chunk.done) { if (controller === mine) stop(); return; }
              buffer = frames(buffer + decoder.decode(chunk.value, { stream: true }),
                function (kind, data) {
                  if (kind !== "event") return;
                  var event;
                  try { event = JSON.parse(data); } catch (e) { return; }
                  if (paused) held.push(event); else add(event);
                });
              return pump();
            });
          }
          return pump();
        })
        .catch(function (error) {
          if (controller === mine) stop(L.unreachable + " (" + error.message + ")");
        });
    }

    connect.addEventListener("click", function () { if (controller) stop(); else start(); });
    pause.addEventListener("click", function () {
      paused = !paused;
      pause.textContent = paused ? L.resume : L.pause;
      if (!paused) { held.forEach(add); held = []; }
    });
    clear.addEventListener("click", function () { list.textContent = ""; detail.textContent = ""; });

    return el("div", { className: "events-panel" }, [
      preset.resource ? null : el("p", { className: "note", text: L.events_intro }),
      el("div", { className: "console-form" }, [
        preset.resource ? null : resource.label, preset.type ? null : type.label,
        version.label, id.label,
        el("div", { className: "wide" }, [connect, pause, clear])]),
      status,
      el("div", { className: "events-columns" }, [list, detail]),
    ]);
  }

  R.subscribe = panel;
})();
