/* Morning Deal Brief — progressive enhancement only. The page is fully readable without this file. */
(function () {
  "use strict";

  var doc = document;
  var $ = function (sel, root) { return (root || doc).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || doc).querySelectorAll(sel)); };

  function store(key, value) {
    try {
      if (value === undefined) return window.localStorage.getItem(key);
      window.localStorage.setItem(key, value);
    } catch (e) { /* storage unavailable: preferences simply don't persist */ }
    return null;
  }

  /* ── Timestamps: render in the desk's time zone (shown in the masthead). ── */
  function formatTimes() {
    var zone = doc.body.dataset.timezone;
    var fmt;
    try {
      fmt = new Intl.DateTimeFormat("en-US", {
        timeZone: zone, month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short"
      });
    } catch (e) {
      try { fmt = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" }); }
      catch (e2) { return; }
    }
    $$("time[data-ts]").forEach(function (node) {
      var raw = node.getAttribute("datetime") || "";
      if (raw.length <= 10) return; // date-only values are already human-readable
      var when = new Date(raw);
      if (isNaN(when.getTime())) return;
      node.title = raw;
      node.textContent = fmt.format(when).replace(",", "");
    });
  }

  /* ── Editions: keep the selected tab in view and support ← / → between editions. ── */
  function editions() {
    var strip = $("[data-strip]");
    var current = strip && $('[aria-current="page"]', strip);
    if (strip && current && strip.scrollWidth > strip.clientWidth) {
      var left = current.offsetLeft - strip.clientWidth / 2 + current.clientWidth / 2;
      strip.scrollLeft = Math.max(0, left);
    }

    doc.addEventListener("keydown", function (event) {
      if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      var target = event.target;
      var tag = target && target.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (target && target.isContentEditable)) return;
      var link = null;
      if (event.key === "ArrowLeft") link = $("[data-nav-prev]");
      if (event.key === "ArrowRight") link = $("[data-nav-next]");
      if (link && link.href) {
        event.preventDefault();
        window.location.assign(link.href);
      }
    });

    // Close the "All editions" popover on outside click or Escape.
    var menu = $(".all-editions");
    if (menu) {
      doc.addEventListener("click", function (event) {
        if (menu.open && !menu.contains(event.target)) menu.open = false;
      });
      doc.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && menu.open) {
          menu.open = false;
          $("summary", menu).focus();
        }
      });
    }
  }

  /* ── Watchlist: coverage order (client first) or largest absolute move. ── */
  function watchSort() {
    var group = $("[data-sort]");
    var list = $("[data-watch]");
    if (!group || !list) return;
    var items = $$(".watch-item", list);
    if (items.length < 2) return;
    group.hidden = false;
    var buttons = $$("button", group);

    function apply(key) {
      var sorted = items.slice().sort(function (a, b) {
        if (key === "move") {
          var am = a.dataset.move === "" ? -1 : Math.abs(parseFloat(a.dataset.move));
          var bm = b.dataset.move === "" ? -1 : Math.abs(parseFloat(b.dataset.move));
          if (bm !== am) return bm - am;
        }
        return Number(a.dataset.order) - Number(b.dataset.order);
      });
      sorted.forEach(function (item) { list.appendChild(item); });
      buttons.forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.sortKey === key)); });
      store("mdb.watchSort", key);
    }

    buttons.forEach(function (button) {
      button.addEventListener("click", function () { apply(button.dataset.sortKey); });
    });
    if (store("mdb.watchSort") === "move") apply("move");
  }

  /* ── Copy accession numbers. ── */
  function copyButtons() {
    if (!navigator.clipboard || !window.isSecureContext) return;
    $$("[data-copy]").forEach(function (button) {
      button.hidden = false;
      button.setAttribute("aria-label", "Copy accession number " + button.dataset.copy);
      button.addEventListener("click", function () {
        navigator.clipboard.writeText(button.dataset.copy).then(function () {
          button.textContent = "Copied";
          setTimeout(function () { button.textContent = "Copy"; }, 1600);
        }, function () {
          button.textContent = "Copy failed";
        });
      });
    });
  }

  /* ── Collection: CSRF-protected POST, then poll the job until it settles. ── */
  function collection() {
    var button = $("[data-run]");
    var panel = $("[data-status]");
    if (!button || !panel || !window.fetch) return;
    var label = $("[data-run-label]", button);
    var text = $("[data-status-text]", panel);
    var elapsed = $("[data-status-elapsed]", panel);
    var link = $("[data-status-link]", panel);
    var dismiss = $("[data-status-dismiss]", panel);
    var tokenMeta = $('meta[name="run-token"]');
    var token = tokenMeta ? tokenMeta.content : "";
    var idleLabel = label ? label.textContent : "";
    var pollTimer = null, clockTimer = null, startedAt = null, failures = 0;
    var collectAfterSave = false;
    try {
      collectAfterSave = sessionStorage.getItem("mdb.collectAfterSave") === "1";
      sessionStorage.removeItem("mdb.collectAfterSave");
    } catch (e) { /* storage may be disabled */ }

    function clock() {
      if (!startedAt) { elapsed.textContent = ""; return; }
      var s = Math.max(0, Math.round((Date.now() - startedAt) / 1000));
      elapsed.textContent = Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
    }

    function show(state, message, url) {
      panel.hidden = false;
      panel.dataset.state = state;
      text.textContent = message;
      var running = state === "running";
      button.disabled = running;
      button.setAttribute("aria-busy", String(running));
      if (label) label.textContent = running ? "Collecting…" : idleLabel;
      var safe = url && url.charAt(0) === "/" && url.charAt(1) !== "/" ? url : "";
      link.hidden = !safe;
      if (safe) link.setAttribute("href", safe);
      dismiss.hidden = running;
      if (running) {
        if (!startedAt) startedAt = Date.now();
        if (!clockTimer) clockTimer = setInterval(clock, 1000);
        clock();
      } else {
        clearInterval(clockTimer); clockTimer = null;
        clock();
        startedAt = null;
        store("mdb.runStarted", "");
      }
    }

    function settle(job) {
      var state = job && job.state;
      if (state === "running") {
        show("running", "Collecting prices, catalysts, news and filings. You can keep reading while this runs.");
        schedule();
      } else if (state === "done") {
        show("done", "Today’s brief is ready.", job.url);
        if ((collectAfterSave || saved || openedRun) && link && !link.hidden) {
          window.location.assign(link.href);
        } else if (link && !link.hidden) {
          link.focus({ preventScroll: true });
        }
      } else if (state === "failed") {
        show("failed", job.message || "Collection did not finish.", job.url);
      } else if (state === "busy") {
        show("busy", job.message || "A scheduled brief is already running. Try again after it finishes.");
      } else {
        panel.hidden = true;
        button.disabled = false;
        button.setAttribute("aria-busy", "false");
      }
    }

    function schedule() {
      clearTimeout(pollTimer);
      pollTimer = setTimeout(poll, Math.min(2000 * Math.pow(1.6, failures), 15000));
    }

    function poll() {
      fetch("/api/run", { credentials: "same-origin", headers: { "Accept": "application/json" }, cache: "no-store" })
        .then(function (res) { if (!res.ok) throw new Error(String(res.status)); return res.json(); })
        .then(function (job) { failures = 0; settle(job); })
        .catch(function () {
          failures += 1;
          if (failures > 6) {
            show("error", "Lost contact with the local server. Reload the page to check on collection.");
            return;
          }
          if (text) text.textContent = "Reconnecting to the local server…";
          schedule();
        });
    }

    function start() {
      if (!token) { show("error", "This page is missing its session token. Reload and try again."); return; }
      openedRun = true;
      button.disabled = true;
      startedAt = Date.now();
      store("mdb.runStarted", String(startedAt));
      show("running", "Starting collection…");
      fetch("/api/run", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "Accept": "application/json", "X-Run-Token": token },
        body: "{}"
      })
        .then(function (res) {
          if (res.status === 403) throw new Error("forbidden");
          return res.json().catch(function () { return {}; }).then(function (job) {
            if (!res.ok && job.state !== "running") { settle({ state: "failed", message: job.message || "Collection could not start." }); return; }
            settle(job.state ? job : { state: "running" });
          });
        })
        .catch(function (err) {
          show("error", err && err.message === "forbidden"
            ? "Your session expired. Reload the page, then collect again."
            : "Couldn’t reach the local server. Check that the dashboard is still running.");
        });
    }

    button.addEventListener("click", start);
    dismiss.addEventListener("click", function () { panel.hidden = true; button.focus(); });

    // Resume a run already in progress (e.g. after navigating between editions).
    var saved = Number(store("mdb.runStarted"));
    var openedRun = false;
    fetch("/api/run", { credentials: "same-origin", cache: "no-store" })
      .then(function (res) { return res.ok ? res.json() : { state: "idle" }; })
      .then(function (job) {
        if (job.state === "running") {
          if (collectAfterSave) {
            collectAfterSave = false;
            startedAt = saved && Date.now() - saved < 3600e3 ? saved : Date.now();
            show("running", "A brief was already collecting. Run it again after this edition to apply your saved preferences.");
            schedule();
            return;
          }
          startedAt = saved && Date.now() - saved < 3600e3 ? saved : Date.now();
          settle(job);
        } else if (collectAfterSave) {
          start();
        } else if (job.state === "done" || job.state === "failed") {
          // Only surface the result in the session that started it.
          if (saved) settle(job); else settle({ state: "idle" });
        } else {
          settle({ state: "idle" });
        }
      })
      .catch(function () { button.disabled = false; });
  }

  /* ── Section bar: highlight the panel in view; keep the active link visible on narrow screens. ── */
  function sectionSpy() {
    var links = $$(".brief-jumps a[href^='#']");
    if (!links.length || !("IntersectionObserver" in window)) return;
    var byId = {};
    links.forEach(function (a) { byId[a.getAttribute("href").slice(1)] = a; });
    var targets = Object.keys(byId).map(function (id) { return doc.getElementById(id); }).filter(Boolean);
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        links.forEach(function (a) { a.classList.remove("is-active"); a.removeAttribute("aria-current"); });
        var link = byId[entry.target.id];
        if (link) {
          link.classList.add("is-active");
          link.setAttribute("aria-current", "location");
          if (link.scrollIntoView && link.parentNode.scrollWidth > link.parentNode.clientWidth) {
            link.scrollIntoView({ block: "nearest", inline: "center" });
          }
        }
      });
    }, { rootMargin: "-70px 0px -70% 0px" });
    targets.forEach(function (t) { observer.observe(t); });
  }

  /* ── Ask this brief: grounded Q&A; answers carry citation links back to the panels. ── */
  function ask() {
    var form = $("[data-ask]");
    if (!form) return;
    var out = $("[data-ask-answer]", form);
    var token = (doc.querySelector('meta[name="run-token"]') || {}).content || "";
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var question = form.question.value.trim();
      if (question.length < 3) return;
      var button = $("button", form);
      button.disabled = true;
      out.hidden = false;
      out.textContent = "Checking the brief…";
      fetch(form.dataset.askUrl, {
        method: "POST", headers: { "Content-Type": "application/json", "X-Run-Token": token },
        body: JSON.stringify({ question: question, date: form.dataset.askDate })
      }).then(function (r) { return r.json(); }).then(function (data) {
        out.replaceChildren();
        if (data.state !== "ok") {
          var p = doc.createElement("p");
          p.className = "snap-sub";
          p.textContent = data.message || "No answer available.";
          out.append(p);
          return;
        }
        var para = doc.createElement("p");
        data.sentences.forEach(function (s) {
          para.append(doc.createTextNode(s.text + " "));
          s.facts.forEach(function (f) {
            var a = doc.createElement("a");
            a.className = "tom-cite";
            a.href = "#brief-" + f.section;
            a.textContent = f.id.slice(1);
            a.title = "Source fact " + f.id;
            para.append(a);
          });
          para.append(doc.createTextNode(" "));
        });
        var note = doc.createElement("p");
        note.className = "ai-read-note";
        note.textContent = "AI answer from this edition's facts; every figure checked. Numbers link to their panels.";
        out.append(para, note);
      }).catch(function () {
        out.textContent = "The question could not be answered right now.";
      }).finally(function () { button.disabled = false; });
    });
  }

  /* ── Chart popups: switch between 1M, 6M, 1Y and 5Y; data comes from /api/chart and is drawn as SVG. ── */
  function charts() {
    doc.addEventListener("click", function (event) {
      var button = event.target.closest && event.target.closest(".chart-ranges button");
      if (!button) return;
      var figure = button.closest("[data-chart]");
      var line = $("polyline", figure);
      var caption = $("figcaption", figure);
      $$("button", button.parentNode).forEach(function (b) { b.setAttribute("aria-pressed", String(b === button)); });
      caption.textContent = "Loading…";
      fetch("/api/chart?symbol=" + encodeURIComponent(figure.dataset.chart) + "&range=" + button.dataset.range)
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (!data.points) { caption.textContent = data.message || "History unavailable."; return; }
          var span = (data.high - data.low) || 1, n = data.points.length;
          line.setAttribute("points", data.points.map(function (p, i) {
            return (i * 300 / (n - 1)).toFixed(1) + "," + (94 - (p[1] - data.low) / span * 88).toFixed(1);
          }).join(" "));
          var fmt = function (v) {
            var a = Math.abs(v);
            return a >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 0 }) : v.toFixed(a >= 10 ? 2 : 3);
          };
          // Yields move in basis points; a percentage change of a yield (0.03% -> 3%) is meaningless.
          var change = figure.dataset.kind === "yield"
            ? ((data.last - data.first) * 100 >= 0 ? "+" : "") + ((data.last - data.first) * 100).toFixed(0) + "bp"
            : (data.chg_pct >= 0 ? "+" : "") + data.chg_pct.toFixed(2) + "%";
          var unit = figure.dataset.kind === "yield" ? "%" : "";
          caption.textContent = figure.dataset.chart + " · " + button.textContent + " · " + change + " · low " +
            fmt(data.low) + unit + " · high " + fmt(data.high) + unit + " · " + data.provider;
        })
        .catch(function () { caption.textContent = "History unavailable right now."; });
    });
  }

  function init() {
    charts();
    ask();
    sectionSpy();
    formatTimes();
    editions();
    watchSort();
    copyButtons();
    collection();
    var region = $("[data-calendar-filter]");
    if (region) region.addEventListener("change", function () {
      doc.querySelectorAll("[data-calendar-region]").forEach(function (row) {
        row.hidden = Boolean(region.value && row.dataset.calendarRegion !== region.value);
      });
    });
  }

  if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", init);
  else init();
})();
