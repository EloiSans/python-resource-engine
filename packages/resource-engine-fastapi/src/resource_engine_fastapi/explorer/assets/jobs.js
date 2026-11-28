/* CRON: what runs on a schedule. Two kinds of rows:
   - a worker job (R.cron.job): its schedule, workers, recent runs and "run
     now" (a request a worker claims, so single jobs keep their lease);
   - a timed transition of a resource (R.cron.timer): when it fires, and when
     it is due for one record.
   R.cron.load(callback) reads the jobs the running workers registered. */
(function () {
  var R = window.ResourceExplorer;
  if (!R || !R.config.jobs) return;
  var L = R.labels, el = R.el, J = R.config.jobs;

  function fill(template, values) {
    return template.replace(/\{(\w+)\}/g, function (_, key) {
      return values[key] === undefined ? "" : values[key];
    });
  }

  function seconds(value) {
    if (value >= 86400 && value % 86400 === 0) return value / 86400 + " d";
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

  function schedule(job) {
    return fill(L.every, { every: seconds(job.every) }) +
      (job.single ? " · " + fill(L.single, { ttl: seconds(job.ttl) }) : "");
  }

  function load(callback) {
    R.call("GET", J.list).then(callback);
  }

  // ------------------------------------------------------------- a worker job
  function job(info) {
    var summary = el("dl", { className: "facts" }, [
      el("dt", { text: L.schedule }), el("dd", { text: schedule(info) }),
      el("dt", { text: L.workers }), el("dd", { text: info.workers.join(", ") }),
      el("dt", { text: L.last_run }),
      el("dd", { text: info.last_run ? info.last_run.outcome + " · " +
        seconds(info.last_run.seconds) + " · " + when(info.last_run.at) : L.never }),
    ]);
    var output = el("div", { className: "console-output", "aria-live": "polite" });
    var runs = el("button", { type: "button", className: "secondary", text: L.runs });
    var now = el("button", { type: "button", className: "execute", text: L.run_now });
    runs.addEventListener("click", history);
    now.addEventListener("click", runNow);

    function history() {
      output.textContent = L.sending;
      R.call("GET", J.runs.replace("{name}", encodeURIComponent(info.name))).then(function (result) {
        output.textContent = "";
        if (!result.ok || !Array.isArray(result.data)) { R.show(output, result); return; }
        if (!result.data.length) { output.appendChild(el("p", { className: "note", text: L.never })); return; }
        output.appendChild(table([L.when, L.outcome, L.duration, L.worker, L.error],
          result.data.map(function (run) {
            return el("tr", {}, [cell(when(run.at)), cell(run.outcome, "outcome-" + run.outcome),
              cell(seconds(run.seconds)), cell(run.owner), cell(run.error || "")]);
          })));
      });
    }

    function runNow() {
      if (!R.allowed()) { output.textContent = L.changes_off; return; }
      output.textContent = L.sending;
      R.call("POST", J.run.replace("{name}", encodeURIComponent(info.name))).then(function (result) {
        if (!result.ok || !result.data) { R.show(output, result); return; }
        follow(result.data, 0);
      });
    }

    function follow(request, tries) {
      output.textContent = "";
      var done = ["ok", "failed", "skipped"].indexOf(request.status) >= 0;
      var text = done ? fill(L.request_done, { status: request.status }) : L.request_pending;
      output.appendChild(el("p", {
        className: "console-status " + (request.status === "failed" ? "fail" : "ok"),
        text: text + (request.claimed_by ? " · " + request.claimed_by : "") }));
      if (request.error) output.appendChild(el("pre", { text: request.error }));
      if (done || tries > 120) return;
      setTimeout(function () {
        R.call("GET", J.request.replace("{id}", encodeURIComponent(request.id))).then(function (result) {
          if (result.ok && result.data) follow(result.data, tries + 1);
          else R.show(output, result);
        });
      }, 1000);
    }

    var actions = el("div", {}, [now, runs]);
    if (!R.allowed()) actions.appendChild(el("span", { className: "note", text: " " + L.changes_off }));
    return el("div", { className: "try-it" }, [summary, actions, output]);
  }

  // ----------------------------------------------------- a timed transition
  function timer(resource, info) {
    var summary = el("dl", { className: "facts" }, [
      el("dt", { text: L.fires }),
      el("dd", { text: fill(L.after_in, { after: seconds(info.after_seconds),
        states: info.states.join(", "), since: info.since }) }),
    ]);
    var id = el("input", { type: "text", "aria-label": L.filter_id, spellcheck: "false" });
    id.value = R.load("re-console-id-" + resource);
    var show = el("button", { type: "button", className: "execute", text: L.show });
    var output = el("div", { className: "console-output", "aria-live": "polite" });
    show.addEventListener("click", function () {
      var value = id.value.trim();
      if (!value) { output.textContent = L.missing + ": " + L.filter_id; return; }
      output.textContent = L.sending;
      var path = J.timers.replace("{resource}", encodeURIComponent(resource))
        .replace("{id}", encodeURIComponent(value));
      R.call("GET", path).then(function (result) {
        output.textContent = "";
        if (!result.ok || !Array.isArray(result.data)) { R.show(output, result); return; }
        var mine = result.data.filter(function (t) { return t.event === info.event; });
        if (!mine.length) { output.appendChild(el("p", { className: "note", text: L.no_timers })); return; }
        mine.forEach(function (t) {
          output.appendChild(el("p", {
            className: "console-status " + (t.overdue ? "fail" : "ok"),
            text: L.due + ": " + when(t.due_at) + (t.overdue ? " · " + L.overdue : "") }));
        });
      });
    });
    return el("div", { className: "try-it" }, [
      summary,
      el("div", { className: "console-form" }, [
        el("label", { className: "required" }, [el("span", { text: L.filter_id }), id]),
        el("div", { className: "wide" }, [show])]),
      output,
    ]);
  }

  R.cron = { load: load, job: job, timer: timer, schedule: schedule, seconds: seconds };
})();
