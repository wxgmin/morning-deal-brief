(() => {
  "use strict";

  const root = document.querySelector("[data-settings]");
  if (!root) return;
  const el = (selector, scope = root) => scope.querySelector(selector);
  const all = (selector, scope = root) => [...scope.querySelectorAll(selector)];
  const saveStatus = el("[data-save-status]");
  const saveButtons = [el("[data-save]"), el("[data-save-collect]")];
  const token = document.querySelector('meta[name="run-token"]').content;
  const marketFields = all('[name="market"]');
  const countryFields = all('[name="calendar-country"]');
  const themes = all("[data-theme]");
  let profile, revision, saved, searchRequest;

  const authButtons = all("[data-chatgpt-url]");
  authButtons.forEach(control => control.addEventListener("click", async () => {
    const status = el("[data-chatgpt-status]");
    authButtons.forEach(button => { button.disabled = true; });
    status.textContent = "Contacting ChatGPT…";
    try {
      const response = await fetch(control.dataset.chatgptUrl, {
        method: "POST",
        headers: {"Content-Type": "application/json", "X-Run-Token": token},
        body: JSON.stringify({client_id: control.dataset.chatgptClient}),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.message || "ChatGPT connection could not be changed.");
      window.location.assign(result.url);
    } catch (error) {
      status.textContent = error.message;
      authButtons.forEach(button => { button.disabled = false; });
    }
  }));

  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text) element.textContent = text;
    if (className) element.className = className;
    return element;
  }

  function button(text, action, label) {
    const element = node("button", text, "btn");
    element.type = "button";
    if (label) element.setAttribute("aria-label", label);
    element.addEventListener("click", action);
    return element;
  }

  function check(text, checked, change, label) {
    const field = node("label", "", "setting-check");
    const input = node("input");
    input.type = "checkbox";
    input.checked = checked;
    if (label) input.setAttribute("aria-label", label);
    input.addEventListener("change", () => { change(input.checked); changed(); });
    field.append(input, node("span", text));
    return field;
  }

  function identify(heading, detail) {
    const label = node("div", "", "setting-name");
    label.append(node("strong", heading));
    if (detail) label.append(node("small", detail));
    return label;
  }

  function status(target, text, state = "") {
    const region = el(target);
    region.textContent = text;
    region.dataset.state = state;
  }

  function readControls() {
    profile.sector = el("[data-sector]").value;
    profile.markets = marketFields.filter(field => field.checked).map(field => field.value);
    profile.calendar.countries = countryFields.filter(field => field.checked).map(field => field.value);
    profile.calendar.min_impact = el("[data-impact]").value;
    profile.calendar.horizon_days = el("[data-horizon]").valueAsNumber;
    profile.calendar.earnings = el("[data-earnings]").checked;
    profile.calendar.auctions = el("[data-auctions]").checked;
    profile.headlines.max_items = el("[data-news-limit]").valueAsNumber;
    profile.headlines.max_age_hours = el("[data-news-lookback]").valueAsNumber;
    themes.forEach(field => { profile.headlines.themes[field.dataset.theme] = field.valueAsNumber; });
    profile.alerts = {
      enabled: el("[data-alerts-enabled]").checked,
      move_pct: el("[data-alerts-move]").valueAsNumber || 3,
      keywords: el("[data-alerts-keywords]").value.split(",").map(word => word.trim()).filter(Boolean).slice(0, 20)
    };
  }

  function changed() {
    readControls();
    if (JSON.stringify(profile) !== saved) status("[data-save-status]", "Unsaved changes");
  }

  function removeRow(rows, index, render, selector, focusBack) {
    rows.splice(index, 1);
    render();
    changed();
    const controls = all(selector);
    (controls[Math.min(index, controls.length - 1)] || el(focusBack))?.focus();
  }

  function renderWatchlist() {
    const list = el("[data-watchlist]");
    const roles = { watch: "Watch", peer: "Peer", client: "Focus" };
    list.replaceChildren(...profile.watchlist.map((company, index) => {
      const row = node("li", "", "setting-row");
      const select = node("select");
      select.setAttribute("aria-label", `Role for ${company.ticker}`);
      for (const [value, label] of Object.entries(roles)) select.add(new Option(label, value));
      select.value = company.role;
      select.addEventListener("change", () => {
        if (select.value === "client") {
          profile.watchlist.filter(other => other.role === "client").forEach(other => { other.role = "watch"; });
        }
        company.role = select.value;
        all("[data-watchlist] select").forEach((control, i) => { control.value = profile.watchlist[i].role; });
        changed();
      });
      const remove = button("Remove", () => {
        removeRow(profile.watchlist, index, renderWatchlist, "[data-watchlist] select", "#company-search");
        status("[data-watch-status]", `${company.ticker} removed.`);
      }, `Remove ${company.ticker}`);
      row.append(identify(company.ticker, company.name), select, remove);
      return row;
    }));
    if (!profile.watchlist.length) list.append(node("li", "Your watchlist is empty.", "set-status"));
  }

  function addCompany(company) {
    if (profile.watchlist.some(row => row.ticker === company.ticker)) {
      status("[data-watch-status]", `${company.ticker} is already on the watchlist.`, "error");
      return false;
    }
    if (profile.watchlist.length >= 60) {
      status("[data-watch-status]", "The watchlist can hold up to 60 names.", "error");
      return false;
    }
    profile.watchlist.push({ ticker: company.ticker, name: company.name || company.ticker, role: "watch", cik: "" });
    renderWatchlist();
    changed();
    status("[data-watch-status]", `${company.ticker} added.`);
    return true;
  }

  function renderInstruments() {
    all("[data-group]").forEach(group => {
      const rows = profile.instruments.filter(item => item.group === group.dataset.group);
      el("[data-group-items]", group).replaceChildren(...rows.map(item => {
        const line = node("div", "", "setting-row");
        const pin = check("Pin", item.pinned, value => { item.pinned = value; }, `Pin ${item.label}`);
        const enabled = check("Include", item.enabled, value => { item.enabled = value; }, `Include ${item.label}`);
        line.append(identify(item.label, item.symbol), enabled, pin);
        if (item.custom) line.append(button("Remove", () => {
          profile.instruments = profile.instruments.filter(other => other !== item);
          renderInstruments();
          changed();
          group.querySelector("summary").focus();
          status("[data-instrument-status]", `${item.label} removed.`);
        }, `Remove ${item.label}`));
        return line;
      }));
    });
  }

  function renderFeeds() {
    el("[data-feeds]").replaceChildren(...profile.feeds.map((feed, index) => {
      const row = node("li", "", "setting-row");
      row.append(check(feed.label, feed.enabled, value => { feed.enabled = value; }));
      if (feed.custom) row.append(button("Remove", () => {
        removeRow(profile.feeds, index, renderFeeds, "[data-feeds] input", "[data-add-feed] input");
        status("[data-feed-status]", `${feed.label} removed.`);
      }, `Remove ${feed.label}`));
      return row;
    }));
  }

  function renderOrder() {
    el("[data-section-order]").replaceChildren(...profile.sections.map((section, index) => {
      const row = node("li", "", "setting-row");
      row.append(check(section.label, section.enabled, value => { section.enabled = value; }));
      for (const [delta, text] of [[-1, "Up"], [1, "Down"]]) {
        const target = index + delta;
        const move = button(text, () => {
          [profile.sections[index], profile.sections[target]] = [profile.sections[target], profile.sections[index]];
          renderOrder();
          changed();
          const controls = all("button", el("[data-section-order]").children[target]);
          (controls.find(control => control.textContent === text && !control.disabled)
            || controls.find(control => !control.disabled))?.focus();
        }, `Move ${section.label} ${text.toLowerCase()}`);
        move.disabled = target < 0 || target >= profile.sections.length;
        row.append(move);
      }
      return row;
    }));
  }

  function show(data) {
    profile = data.profile;
    revision = data.revision;
    saved = JSON.stringify(profile);
    renderAll();
  }

  function renderAll() {
    el("[data-sector]").value = profile.sector;
    marketFields.forEach(field => { field.checked = profile.markets.includes(field.value); });
    countryFields.forEach(field => { field.checked = profile.calendar.countries.includes(field.value); });
    el("[data-impact]").value = profile.calendar.min_impact;
    el("[data-horizon]").value = profile.calendar.horizon_days;
    el("[data-earnings]").checked = profile.calendar.earnings;
    el("[data-auctions]").checked = profile.calendar.auctions;
    el("[data-news-limit]").value = profile.headlines.max_items;
    el("[data-news-lookback]").value = profile.headlines.max_age_hours;
    themes.forEach(field => { field.value = profile.headlines.themes[field.dataset.theme]; });
    const alertRules = profile.alerts || { enabled: true, move_pct: 3, keywords: [] };
    el("[data-alerts-enabled]").checked = alertRules.enabled;
    el("[data-alerts-move]").value = alertRules.move_pct;
    el("[data-alerts-keywords]").value = alertRules.keywords.join(", ");
    renderWatchlist();
    renderInstruments();
    renderFeeds();
    renderOrder();
  }

  // Quick setup: preview a role or starter watchlist on the unsaved form, then let the user review and save.
  const setup = el("#setup");
  async function applySetup(body, label) {
    readControls();
    const result = el("[data-setup-result]");
    all("[data-role], [data-preset]").forEach(control => { control.disabled = true; });
    try {
      const data = await request(setup.dataset.applyUrl, {
        method: "POST", headers: { "Content-Type": "application/json", "X-Run-Token": token },
        body: JSON.stringify({ profile, ...body })
      });
      profile = data.profile;
      renderAll();
      changed();
      result.replaceChildren(node("p", `${label} applied. Review below, then press Save preferences.`, "set-status is-ok"));
      const list = node("ul", "", "snap-list");
      data.changes.forEach(text => list.append(node("li", text)));
      result.append(list);
      result.hidden = false;
      all("[data-role]").forEach(control => control.setAttribute("aria-pressed", String(control.dataset.role === body.role)));
    } catch (error) {
      result.replaceChildren(node("p", error.message, "set-status is-bad"));
      result.hidden = false;
    } finally {
      all("[data-role], [data-preset]").forEach(control => { control.disabled = false; });
    }
  }
  all("[data-role]").forEach(control => control.addEventListener("click", () =>
    applySetup({ role: control.dataset.role }, control.querySelector("b").textContent)));
  all("[data-preset]").forEach(control => control.addEventListener("click", () =>
    applySetup({ preset: control.dataset.preset }, `${control.textContent} watchlist`)));

  async function request(url, options) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.message || "The local server could not process the request.");
    return data;
  }

  el("[data-search]").addEventListener("submit", async event => {
    event.preventDefault();
    searchRequest?.abort();
    const controller = new AbortController();
    searchRequest = controller;
    const list = el("[data-search-results]");
    const query = new FormData(event.currentTarget).get("q").trim();
    list.hidden = true;
    list.replaceChildren();
    if (query.length < 2) {
      status("[data-search-status]", "Enter at least two characters.");
      return;
    }
    status("[data-search-status]", "Searching…");
    try {
      const { results } = await request(`${root.dataset.searchUrl}?q=${encodeURIComponent(query)}`,
        { signal: controller.signal });
      if (controller.signal.aborted) return;
      status("[data-search-status]", results.length ? "" : "No matches. Try another name or add the ticker manually.");
      list.replaceChildren(...results.map(item => {
        const row = node("li");
        const detail = [item.name, item.exchange].filter(Boolean).join(" · ");
        const add = button("Add", () => {
          if (addCompany(item)) {
            add.textContent = "Added";
            add.disabled = true;
            el("#company-search").focus();
          }
        }, `Add ${item.ticker}`);
        if (profile.watchlist.some(company => company.ticker === item.ticker)) {
          add.disabled = true;
          add.textContent = "Added";
        }
        row.append(identify(item.ticker, detail), add);
        return row;
      }));
      list.hidden = !results.length;
    } catch (error) {
      if (!controller.signal.aborted) status("[data-search-status]", error.message, "error");
    }
  });

  el("[data-add-company]").addEventListener("submit", event => {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = new FormData(form);
    if (addCompany({ ticker: fields.get("ticker").trim().toUpperCase(), name: fields.get("name").trim() })) {
      form.reset();
      form.elements.ticker.focus();
    }
  });

  el("[data-add-instrument]").addEventListener("submit", event => {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = new FormData(form);
    const symbol = fields.get("symbol").trim().toUpperCase();
    if (!/^[A-Z0-9^=.\-]{1,24}$/.test(symbol) || profile.instruments.some(item => item.symbol.toUpperCase() === symbol)) {
      status("[data-instrument-status]", "Enter a valid symbol that is not already listed.", "error");
      return;
    }
    if (profile.instruments.length >= 200) {
      status("[data-instrument-status]", "The market boards can hold up to 200 instruments.", "error");
      return;
    }
    const kind = fields.get("kind");
    profile.instruments.push({ symbol, label: fields.get("label").trim() || symbol, kind,
      group: fields.get("group"), region: fields.get("region") || null,
      dp: kind === "fx" ? 4 : kind === "yield" ? 3 : 2, pinned: false, enabled: true, custom: true });
    renderInstruments();
    changed();
    form.reset();
    status("[data-instrument-status]", `${symbol} added.`);
  });

  el("[data-add-feed]").addEventListener("submit", event => {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = new FormData(form);
    const url = fields.get("url").trim();
    const label = fields.get("label").trim();
    if (!label || !url.startsWith("https://") || profile.feeds.some(feed => feed.url === url)) {
      status("[data-feed-status]", "Enter a name and an HTTPS URL that is not already listed.", "error");
      return;
    }
    if (profile.feeds.length >= 40) {
      status("[data-feed-status]", "The brief can use up to 40 feeds.", "error");
      return;
    }
    profile.feeds.push({ id: `custom-${crypto.randomUUID().slice(0, 18)}`, label, url, category: "general",
      enabled: true, custom: true });
    renderFeeds();
    changed();
    form.reset();
    status("[data-feed-status]", `${label} added.`);
  });

  el("[data-editor]").addEventListener("input", event => {
    if (!event.target.closest("form")) changed();
  });

  async function save(collect) {
    const active = document.activeElement;
    const fields = [...marketFields, ...countryFields, ...themes,
      ...all("[data-horizon], [data-news-limit], [data-news-lookback]")];
    const invalid = fields.find(field => !field.checkValidity() || (field.type === "number" && !field.value));
    if (invalid) {
      if (invalid.closest("details")) invalid.closest("details").open = true;
      status("[data-save-status]", "Check the highlighted value.", "error");
      invalid.reportValidity();
      invalid.focus();
      return;
    }
    readControls();
    const editor = el("[data-editor]");
    editor.inert = true;
    saveButtons.forEach(control => { control.disabled = true; });
    status("[data-save-status]", "Saving…");
    try {
      show(await request(root.dataset.profileUrl, {
        method: "PUT", headers: { "Content-Type": "application/json", "X-Run-Token": token },
        body: JSON.stringify({ profile, revision })
      }));
      if (collect) {
        sessionStorage.setItem("mdb.collectAfterSave", "1");
        location.assign(root.dataset.homeUrl);
      } else {
        status("[data-save-status]", "Preferences saved.", "ok");
      }
    } catch (error) {
      status("[data-save-status]", error.message, "error");
    } finally {
      editor.inert = false;
      saveButtons.forEach(control => { control.disabled = false; });
      active?.focus();
    }
  }

  saveButtons.forEach((control, index) => control.addEventListener("click", () => save(index === 1)));
  window.addEventListener("beforeunload", event => {
    if (profile && JSON.stringify(profile) !== saved) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
  request(root.dataset.profileUrl).then(data => {
    show(data);
    el("[data-editor]").hidden = false;
    saveButtons.forEach(control => { control.disabled = false; });
    saveStatus.textContent = "";
    if (location.hash) document.getElementById(location.hash.slice(1))?.scrollIntoView();
  }).catch(() => { status("[data-save-status]", "Preferences could not be loaded. Reload to try again.", "error"); });
})();
