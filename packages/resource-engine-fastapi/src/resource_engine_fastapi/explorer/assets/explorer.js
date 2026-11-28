/* The explorer (mount_explorer), laid out like Swagger UI: one section per
   resource, one row per verb.
   - GET, POST, PUT, PATCH, DELETE: the HTTP operations (console.js);
   - SUB: an event type the resource publishes, live (events.js);
   - CRON: a timed transition of the resource, or a worker job in the
     "worker" section (jobs.js);
   - PUB: publishing an event, when the application enables it.
   Each row opens into its "Try it" form. Inside a resource the rows are
   grouped in accordions by what they do (CRUD, transitions and actions,
   relations, documents, export, events, schedule). */
(function () {
  var R = window.ResourceExplorer;
  if (!R) return;
  var C = R.config, L = R.labels, el = R.el;
  var root = document.getElementById("explorer");
  if (!root) return;
  var rows = [];  // {element, text, group}

  function verbOf(method) { return method.toLowerCase(); }

  // A collapsible row: the verb, its path, a summary; the body is built when
  // it first opens.
  function row(group, verb, path, summary, build, extra) {
    var badge = el("span", { className: "verb", text: verb });
    var head = el("summary", {}, [
      badge,
      el("code", { className: "path", text: path }),
      el("span", { className: "summary", text: summary || "" }),
      extra || null,
    ]);
    var details = el("details", { className: "op op-" + verb.toLowerCase() }, [head]);
    var built = false;
    details.addEventListener("toggle", function () {
      if (details.open && !built) {
        built = true;
        details.appendChild(build());
      }
    });
    rows.push({ element: details, text: (verb + " " + path + " " + (summary || "")).toLowerCase(),
      group: group });
    return details;
  }

  function section(name, summary, children) {
    var body = el("div", { className: "ops" }, children);
    var header = el("button", { type: "button", className: "tag", "aria-expanded": "true" }, [
      el("span", { className: "tag-name", text: name }),
      el("span", { className: "tag-summary", text: summary || "" }),
    ]);
    header.addEventListener("click", function () {
      var open = header.getAttribute("aria-expanded") !== "true";
      header.setAttribute("aria-expanded", open ? "true" : "false");
      body.hidden = !open;
    });
    var wrapper = el("section", { className: "group" }, [header, body]);
    wrapper.dataset.group = name;
    return wrapper;
  }

  // What a row does, from its path after the resource's name.
  var KINDS = ["crud", "lifecycle", "relations", "documents", "export", "events", "schedule",
    "other"];
  function kindOf(name, op) {
    var rest = op.path.indexOf("/" + name) === 0 ? op.path.slice(name.length + 1) : op.path;
    if (rest === "" || /^\/\{[^}/]+\}$/.test(rest)) return "crud";
    if (/^\/exports?$/.test(rest) || /\/printouts\//.test(rest)) return "export";
    if (/\/(transitions|actions)(\/|$)|\/history$/.test(rest)) return "lifecycle";
    if (/\/documents(\/|$)/.test(rest) || /^\/\{[^}/]+\}\/(content|confirm)$/.test(rest)) {
      return "documents";
    }
    if (/^\/\{[^}/]+\}\/[a-z_]+$/.test(rest)) return "relations";
    return "other";
  }

  // One accordion of a resource's rows: its name, how many, which verbs.
  function subgroup(kind, children, open) {
    var verbs = [];
    children.forEach(function (child) {
      var verb = child.querySelector(".verb").textContent;
      if (verbs.indexOf(verb) < 0) verbs.push(verb);
    });
    var head = el("summary", {}, [
      el("span", { className: "kind", text: L["group_" + kind] }),
      el("span", { className: "count", text: String(children.length) }),
      el("span", { className: "chips" }, verbs.map(function (verb) {
        return el("span", { className: "op-" + verb.toLowerCase() }, [
          el("span", { className: "verb chip", text: verb })]);
      })),
    ]);
    var details = el("details", { className: "subgroup" }, [head, el("div", {}, children)]);
    details.open = open;
    details.dataset.open = open ? "1" : "";
    return details;
  }

  function jump(op) {
    var target = rows.filter(function (r) { return r.op === op; })[0];
    if (!target) return;
    target.element.open = true;
    var parent = target.element.closest("details.subgroup");
    if (parent) parent.open = true;
    target.element.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ------------------------------------------------------------ resources
  var groups = el("div", { className: "groups" });
  (C.groups || []).forEach(function (group) {
    var name = group.name, buckets = {};
    function add(kind, element) { (buckets[kind] = buckets[kind] || []).push(element); }
    var ops = C.resources[name] || [];
    ops.forEach(function (op) {
      var element = row(name, op.method, op.path, op.summary, function () {
        return R.operationForm(name, op, ops, jump);
      });
      rows[rows.length - 1].op = op;
      add(kindOf(name, op), element);
    });
    var events = C.events && C.events.channels ? C.events.channels[name] || [] : [];
    events.forEach(function (event) {
      var version = el("span", { className: "badge", text: "v" + event.version });
      add("events", row(name, "SUB", name + "/" + event.type, event.summary, function () {
        return el("div", { className: "try-it" }, [R.subscribe({ resource: name, type: event.type })]);
      }, version));
    });
    var timers = C.jobs && C.jobs.timers_by_resource ? C.jobs.timers_by_resource[name] || [] : [];
    timers.forEach(function (timer) {
      var text = timer.description || timer.event;
      add("schedule", row(name, "CRON", name + "/" + timer.event, text, function () {
        return R.cron.timer(name, timer);
      }));
    });
    var kinds = KINDS.filter(function (kind) { return buckets[kind]; });
    if (!kinds.length) return;
    var children = kinds.map(function (kind) {
      // CRUD opens at first; a resource with one kind of rows shows them
      return subgroup(kind, buckets[kind], kind === "crud" || kinds.length === 1);
    });
    groups.appendChild(section(name, group.summary, children));
  });

  // -------------------------------------------------------- worker jobs
  var worker = null;
  if (R.cron) {
    var holder = el("div", { className: "ops" }, [el("p", { className: "note", text: L.sending })]);
    worker = section(L.worker_jobs, L.jobs_intro, [holder]);
    groups.appendChild(worker);
    var loadJobs = function () {
      R.cron.load(function (result) {
        holder.textContent = "";
        rows = rows.filter(function (r) { return r.group !== L.worker_jobs; });
        if (!result.ok || !result.data) { R.show(holder, result); return; }
        var jobs = result.data.jobs || [];
        if (!jobs.length) { holder.appendChild(el("p", { className: "note", text: L.no_jobs })); return; }
        jobs.forEach(function (job) {
          holder.appendChild(row(L.worker_jobs, "CRON", job.name, R.cron.schedule(job), function () {
            return R.cron.job(job);
          }));
        });
        filter();
      });
    };
    loadJobs();
    window.addEventListener("re-token", loadJobs);
  }

  // ---------------------------------------------------------- the page
  var search = el("input", { type: "search", className: "filter", placeholder: L.filter,
    "aria-label": L.filter });
  var filtering = false;
  function filter() {
    var words = search.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    rows.forEach(function (r) {
      r.element.hidden = !words.every(function (w) { return r.text.indexOf(w) >= 0; });
    });
    // while filtering, open the accordions with matches and hide the rest;
    // back to how they were once the filter is empty
    var wasFiltering = filtering;
    filtering = words.length > 0;
    [].forEach.call(groups.querySelectorAll("details.subgroup"), function (d) {
      var visible = [].some.call(d.querySelectorAll("details.op"), function (o) { return !o.hidden; });
      d.hidden = filtering && !visible;
      if (filtering) d.open = visible;
      else if (wasFiltering) d.open = d.dataset.open === "1";
    });
    [].forEach.call(groups.querySelectorAll("section.group"), function (s) {
      var visible = [].some.call(s.querySelectorAll("details.op"), function (d) { return !d.hidden; });
      s.hidden = words.length > 0 && !visible;
    });
  }
  search.addEventListener("input", filter);

  var settings = R.settings();
  // a new token: rows that load on their own (the worker's jobs) load again
  [].forEach.call(settings.querySelectorAll("input[type=password]"), function (input) {
    input.addEventListener("change", function () { window.dispatchEvent(new Event("re-token")); });
  });
  var legend = el("p", { className: "legend" }, ["GET", "POST", "PUT", "PATCH", "DELETE", "PUB",
    "SUB", "CRON"].filter(function (verb) {
    if (verb === "SUB") return C.events;
    if (verb === "CRON") return C.jobs;
    if (verb === "PUB") return C.publish;
    return true;
  }).map(function (verb) {
    return el("span", { className: "op-" + verb.toLowerCase() }, [
      el("span", { className: "verb", text: verb }),
      el("span", { text: L["verb_" + verb.toLowerCase()] })]);
  }));
  root.appendChild(el("div", { className: "auth" }, [
    el("p", { className: "note", text: L.intro + (R.api ? " " + R.api : "") }), settings]));
  if (C.me) root.appendChild(R.whoAmI(true));
  root.appendChild(legend);
  root.appendChild(search);
  root.appendChild(groups);
})();
