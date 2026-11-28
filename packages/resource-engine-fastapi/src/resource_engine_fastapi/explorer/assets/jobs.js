/* Jobs panel: the workers' jobs with their schedules and last runs, a job's
   recent runs, "run now" (a request the worker claims, so leases hold), and
   the timed transitions waiting on a record. Registers
   window.ResourceExplorer.panels.jobs. */
(function () {
  var R = window.ResourceExplorer;
  if (!R || !R.config.jobs) return;
  var C = R.config, L = R.labels, el = R.el, J = C.jobs;

  function fill(template, values) {
    return template.replace(/\{(\w+)\}/g, function (_, key) {
      return values[key] === undefined ? "" : values[key];
    });
  }

  function seconds(value) {
    if (value >= 3600 && value % 3600 === 0) return value / 3600 + " h";
    if (value >= 60 && value % 60 === 0) return value / 60 + " min";
    return Math.round(value * 100) / 100 + " s";
  }

  function when(iso) { return iso ? iso.replace("T", " ").slice(0, 19) : "—"; }

  function cell(text, className) { return el("td", { text: text, className: className || "" }); }

  function table(headers, rows) {
    var head = el("tr", {}, headers.map(function (h) { return el("th", { text: h }); }));
    return el("div", { className: "table-wrap" }, [
      el("table", {}, [el("thead", {}, [head]), el("tbody", {}, rows)])]);
  }

  function panel() {
    var status = el("div", { className: "note", "aria-live": "polite" });
    var list = el("div", {});
    var detail = el("div", { className: "console-output" });
    var refresh = el("button", { type: "button", text: L.refresh });

    function load() {
      status.textContent = L.sending;
      R.call("GET", J.list).then(function (result) {
        status.textContent = "";
        list.textContent = "";
        if (!result.ok || !result.data) { R.show(list, result); return; }
        var jobs = result.data.jobs || [];
        if (!jobs.length) { list.appendChild(el("p", { className: "note", text: L.no_jobs })); return; }
        list.appendChild(table(
          [L.job, L.schedule, L.workers, L.last_run, ""],
          jobs.map(row)));
        var workers = (result.data.workers || []).map(function (w) {
          return w.owner + " · " + fill(L.seen, { when: when(w.seen_at) });
        });
        list.appendChild(el("p", { className: "note", text: workers.join(" — ") }));
      });
    }

    function row(job) {
      var schedule = fill(L.every, { every: seconds(job.every) }) +
        (job.single ? " · " + fill(L.single, { ttl: seconds(job.ttl) }) : "");
      var last = job.last_run
        ? job.last_run.outcome + " · " + seconds(job.last_run.seconds) + " · " + when(job.last_run.at)
        : L.never;
      var runs = el("button", { type: "button", className: "secondary", text: L.runs });
      runs.addEventListener("click", function () { history(job.name); });
      var now = el("button", { type: "button", text: L.run_now });
      now.addEventListener("click", function () { runNow(job.name); });
      return el("tr", {}, [
        el("td", {}, [el("code", { text: job.name })]),
        cell(schedule), cell(job.workers.length + ""),
        cell(last, job.last_run ? "outcome-" + job.last_run.outcome : ""),
        el("td", { className: "actions" }, [runs, now]),
      ]);
    }

    function history(name) {
      detail.textContent = L.sending;
      R.call("GET", J.runs.replace("{name}", encodeURIComponent(name))).then(function (result) {
        detail.textContent = "";
        detail.appendChild(el("h3", {}, [el("code", { text: name })]));
        if (!result.ok || !Array.isArray(result.data)) { R.show(detail, result); return; }
        if (!result.data.length) { detail.appendChild(el("p", { className: "note", text: L.never })); return; }
        detail.appendChild(table([L.when, L.outcome, L.duration, L.worker, L.error],
          result.data.map(function (run) {
            return el("tr", {}, [cell(when(run.at)), cell(run.outcome, "outcome-" + run.outcome),
              cell(seconds(run.seconds)), cell(run.owner), cell(run.error || "")]);
          })));
      });
    }

    function runNow(name) {
      if (!R.allowed()) { detail.textContent = L.changes_off; return; }
      detail.textContent = L.sending;
      R.call("POST", J.run.replace("{name}", encodeURIComponent(name))).then(function (result) {
        if (!result.ok || !result.data) { R.show(detail, result); return; }
        follow(result.data, 0);
      });
    }

    function follow(request, tries) {
      detail.textContent = "";
      detail.appendChild(el("h3", {}, [el("code", { text: request.job })]));
      var done = ["ok", "failed", "skipped"].indexOf(request.status) >= 0;
      var text = done ? fill(L.request_done, { status: request.status }) : L.request_pending;
      detail.appendChild(el("p", { className: "console-status " + (request.status === "failed" ? "fail" : "ok"),
        text: text + (request.claimed_by ? " · " + request.claimed_by : "") }));
      if (request.error) detail.appendChild(el("pre", { text: request.error }));
      if (done) { load(); return; }
      if (tries > 120) return;
      setTimeout(function () {
        R.call("GET", J.request.replace("{id}", encodeURIComponent(request.id))).then(function (result) {
          if (result.ok && result.data) follow(result.data, tries + 1);
          else R.show(detail, result);
        });
      }, 1000);
    }

    function timers() {
      var resource = el("select", { "aria-label": L.resource });
      (J.timer_resources || []).forEach(function (n) { resource.appendChild(el("option", { value: n, text: n })); });
      var id = el("input", { type: "text", "aria-label": L.filter_id, spellcheck: "false" });
      var show = el("button", { type: "button", text: L.show });
      var output = el("div", { className: "console-output" });
      show.addEventListener("click", function () {
        if (!id.value.trim()) { output.textContent = L.missing + ": " + L.filter_id; return; }
        output.textContent = L.sending;
        var path = J.timers.replace("{resource}", encodeURIComponent(resource.value))
          .replace("{id}", encodeURIComponent(id.value.trim()));
        R.call("GET", path).then(function (result) {
          output.textContent = "";
          if (!result.ok || !Array.isArray(result.data)) { R.show(output, result); return; }
          if (!result.data.length) { output.appendChild(el("p", { className: "note", text: L.no_timers })); return; }
          output.appendChild(table([L.transition, L.due, L.description], result.data.map(function (t) {
            var due = t.due_at ? when(t.due_at) + (t.overdue ? " · " + L.overdue : "") : "—";
            return el("tr", {}, [el("td", {}, [el("code", { text: t.event })]),
              cell(due, t.overdue ? "outcome-failed" : ""), cell(t.description || "")]);
          })));
        });
      });
      if (!(J.timer_resources || []).length) return null;
      return el("div", {}, [
        el("h3", { text: L.timers }),
        el("p", { className: "note", text: L.timers_intro }),
        el("div", { className: "console-form" }, [
          el("label", {}, [el("span", { text: L.resource }), resource]),
          el("label", {}, [el("span", { text: L.filter_id }), id]), show]),
        output,
      ]);
    }

    refresh.addEventListener("click", load);
    load();
    return el("div", { className: "jobs-panel" }, [
      el("p", { className: "note", text: L.jobs_intro }),
      refresh, status, list, detail, timers(),
    ]);
  }

  R.panels.jobs = panel;
})();
