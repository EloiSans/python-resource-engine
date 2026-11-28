/* PUB: publishing an event by hand. R.publish(resource, event) is the body
   of a PUB row: the schema version, a payload to start from, a dry run
   against the event and its consumers, and publishing through the outbox
   (it needs "Allow changes" and typing the event's name). */
(function () {
  var R = window.ResourceExplorer;
  if (!R || !R.config.publish) return;
  var L = R.labels, el = R.el, P = R.config.publish;

  function problemsTable(problems) {
    var head = el("tr", {}, [L.check, L.field, L.message].map(function (h) {
      return el("th", { text: h });
    }));
    var rows = problems.map(function (p) {
      return el("tr", {}, [
        el("td", { text: p.check === "event" ? L.check_event : p.check.replace("consumer:", L.check_consumer + " ") }),
        el("td", {}, [el("code", { text: p.path || "—" })]),
        el("td", { text: p.message }),
      ]);
    });
    return el("div", { className: "table-wrap" }, [
      el("table", {}, [el("thead", {}, [head]), el("tbody", {}, rows)])]);
  }

  function form(resource, event) {
    var versions = event.versions;
    var version = el("select", { "aria-label": L.filter_version });
    versions.forEach(function (v) {
      version.appendChild(el("option", { value: v.version,
        text: "v" + v.version + (v.deprecated ? " · " + L.deprecated : "") }));
    });
    version.value = versions[versions.length - 1].version;
    var payload = el("textarea", { rows: "8", spellcheck: "false", "aria-label": L.payload });
    function fill() {
      var chosen = versions.filter(function (v) { return v.version === version.value; })[0];
      payload.value = JSON.stringify(chosen.example, null, 2);
    }
    version.addEventListener("change", fill);
    fill();
    var confirm = el("input", { type: "text", spellcheck: "false", "aria-label": L.confirm_type,
      placeholder: event.type });
    var output = el("div", { className: "console-output", "aria-live": "polite" });
    var check = el("button", { type: "button", className: "secondary", text: L.dry_run });
    var send = el("button", { type: "button", className: "execute", text: L.publish });

    function body() {
      try {
        return { type: event.type, version: Number(version.value), payload: JSON.parse(payload.value || "{}") };
      } catch (e) {
        output.textContent = L.bad_json + ": " + e.message;
        return null;
      }
    }

    check.addEventListener("click", function () {
      var request = body();
      if (!request) return;
      output.textContent = L.sending;
      R.call("POST", P.dry_run, request).then(function (result) {
        output.textContent = "";
        if (!result.ok || !result.data) { R.show(output, result); return; }
        var problems = result.data.problems || [];
        if (!problems.length) {
          output.appendChild(el("p", { className: "console-status ok", text: L.no_problems }));
          return;
        }
        output.appendChild(el("p", {
          className: "console-status " + (result.data.blocking ? "fail" : "ok"),
          text: result.data.blocking ? L.problems_blocking : L.problems_consumers }));
        output.appendChild(problemsTable(problems));
      });
    });

    send.addEventListener("click", function () {
      if (!P.enabled) { output.textContent = L.publish_off; return; }
      if (!R.allowed()) { output.textContent = L.changes_off; return; }
      if (confirm.value.trim() !== event.type) {
        output.textContent = R.labels.confirm_needed.replace("{type}", event.type);
        return;
      }
      var request = body();
      if (!request) return;
      output.textContent = L.sending;
      R.call("POST", P.publish, request).then(function (result) {
        output.textContent = "";
        if (!result.ok || !result.data) {
          R.show(output, result);
          var details = result.data && result.data.error && result.data.error.details;
          if (Array.isArray(details)) output.appendChild(problemsTable(details));
          return;
        }
        var envelopes = result.data.envelopes || [];
        output.appendChild(el("p", { className: "console-status ok", text: L.published }));
        output.appendChild(el("pre", { text: envelopes.map(function (e) {
          return e.type + " v" + e.schema_version + " · " + e.id;
        }).join("\n") }));
        confirm.value = "";
      });
    });

    var actions = el("div", { className: "wide" }, [check, send]);
    if (!P.enabled) actions.appendChild(el("span", { className: "note", text: " " + L.publish_off }));
    else if (!R.allowed()) actions.appendChild(el("span", { className: "note", text: " " + L.changes_off }));
    return el("div", { className: "try-it" }, [
      el("div", { className: "console-form" }, [
        el("label", {}, [el("span", { text: L.filter_version }), version]),
        el("label", { className: "required" }, [el("span", { text: L.confirm_type }), confirm]),
        el("label", { className: "wide" }, [el("span", { text: L.payload }), payload]),
        actions,
      ]),
      el("p", { className: "note", text: L.publish_note }),
      output,
    ]);
  }

  R.publish = form;
})();
