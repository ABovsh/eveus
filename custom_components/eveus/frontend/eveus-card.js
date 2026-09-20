// Eveus card — one card, four layouts: compact / status / control / full.
const CARD = "eveus-card";
const C = { red: "#E74C3C", orange: "#F39C12", green: "#2ECC71", grey: "#95A5A6", blue: "#3498DB", purple: "#A78BFA", off: "#78828F" };
const LAYOUTS = ["compact", "status", "control", "full"];

const I18N = {
  en: {
    soc: "SOC", eta: "To target", current: "Current", session: "Session", power: "Power",
    one: "One charge", stop: "Stop", on: "On", off: "Off", setCurrent: "Set",
    initial: "Initial", target: "Target", capacity: "Capacity", correction: "Loss", socLimit: "SOC limit",
    temp: "Temp", voltage: "Voltage", paused: "Paused", offline: "Offline",
    lastSession: "Last charge", adaptive: "Adaptive", groundProt: "Protection", sure: "Sure?",
    mn: "m", hr: "h", noDevice: "No Eveus charger found", ground: "No ground",
  },
  uk: {
    soc: "Заряд", eta: "До цілі", current: "Струм", session: "Сесія", power: "Потужність",
    one: "Один заряд", stop: "Стоп", on: "Увімк", off: "Вимк", setCurrent: "Задати",
    initial: "Початковий", target: "Ціль", capacity: "Ємність", correction: "Втрати", socLimit: "Ліміт SOC",
    temp: "Темп.", voltage: "Напруга", paused: "Пауза", offline: "Немає зв'язку",
    lastSession: "Минулий заряд", adaptive: "Адаптивний", groundProt: "Захист", sure: "Точно?",
    mn: "хв", hr: "год", noDevice: "Станцію Eveus не знайдено", ground: "Немає заземлення",
  },
};

const EDITOR = {
  en: {
    fields: { layout: "Layout", device_id: "Charger", mode: "Integration mode", language: "Language" },
    layouts: { compact: "Compact", status: "Status", control: "Control", full: "Full" },
    advanced: "Advanced", basic: "Basic",
    languages: { auto: "Home Assistant language", uk: "Українська", en: "English" },
    modeHelp: "Empty follows the integration. Advanced needs the integration in Advanced mode.",
  },
  uk: {
    fields: { layout: "Вигляд", device_id: "Станція", mode: "Режим інтеграції", language: "Мова" },
    layouts: { compact: "Компактний", status: "Стан", control: "Керування", full: "Повний" },
    advanced: "Розширений", basic: "Базовий",
    languages: { auto: "Мова Home Assistant", uk: "Українська", en: "English" },
    modeHelp: "Порожнє поле — як в інтеграції. Розширений працює, лише якщо інтеграція в Розширеному режимі.",
  },
};

// Card keys → entity keys (the part of unique_id after "eveus<N>_").
const KEYS = {
  state: "state", substate: "substate", reason: "not_charging_reason",
  current: "current", currentSet: "current_set", power: "power", voltage: "voltage",
  sessionEnergy: "session_energy", sessionCost: "session_cost", sessionTime: "session_time",
  soc: "soc_percent", socEnergy: "soc_energy", eta: "time_to_target_soc",
  finish: "charging_finish_time", energyToTarget: "energy_to_target_soc",
  costToTarget: "cost_to_target_soc", ground: "ground", boxTemp: "box_temperature",
  plugTemp: "plug_temperature", adaptiveLimit: "adaptive_current_limit",
  lastSession: "last_session_energy", groundProt: "ground_protection",
  oneCharge: "one_charge", stop: "stop_charging", socLimit: "limit_soc_enabled",
  chargingCurrent: "charging_current", initialSoc: "initial_soc", targetSoc: "target_soc",
  capacity: "battery_capacity", correction: "soc_correction",
};

const num = (s) => (s && s.state !== "unknown" && s.state !== "unavailable" && !isNaN(parseFloat(s.state)) ? parseFloat(s.state) : null);
const fmt = (v, d = 0) => (v === null ? "--" : Number(v.toFixed(d)).toString());
// One rule for every duration the card prints, and one for every energy.
const dur = (s) => (s && /\d/.test(s) ? s.replace(/\s+/g, "") : null);
const kwh = (v) => (v === null ? null : `${v < 10 ? Number(v.toFixed(1)) : Math.round(v)}kWh`);
const kw = (v) => (v === null ? "--" : `${(v / 1000).toFixed(1)}kW`);

class EveusCard extends HTMLElement {
  static getConfigForm() {
    // Field and mode names are the integration's own ("Integration mode", Advanced / Basic).
    const e = (document.documentElement.lang || "en").startsWith("uk") ? EDITOR.uk : EDITOR.en;
    const opts = (o) => Object.entries(o).map(([value, label]) => ({ value, label }));
    return {
      schema: [
        { name: "layout", selector: { select: { mode: "dropdown", options: opts(e.layouts) } } },
        { name: "device_id", selector: { device: { integration: "eveus" } } },
        { name: "mode", selector: { select: { options: [{ value: "advanced", label: e.advanced }, { value: "basic", label: e.basic }] } } },
        { name: "language", selector: { select: { options: opts(e.languages) } } },
      ],
      computeLabel: (f) => e.fields[f.name],
      computeHelper: (f) => (f.name === "mode" ? e.modeHelp : undefined),
    };
  }
  static getStubConfig() { return { layout: "control" }; }

  setConfig(config) {
    this._config = { layout: "control", language: "auto", ...config };
    if (!LAYOUTS.includes(this._config.layout)) this._config.layout = "control";
    this._pending = {};
    this._sig = null;
    this._idsFor = undefined;
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.shadowRoot.addEventListener("click", (e) => this._onClick(e));
      this.shadowRoot.addEventListener("keydown", (e) => this._onKey(e));
      this.shadowRoot.addEventListener("pointerdown", (e) => this._holdStart(e));
      for (const ev of ["pointerup", "pointercancel", "pointerleave"]) this.shadowRoot.addEventListener(ev, () => clearTimeout(this._holdTimer));
      this.shadowRoot.addEventListener("pointermove", (e) => {
        if (this._holdAt && Math.hypot(e.clientX - this._holdAt[0], e.clientY - this._holdAt[1]) > 10) clearTimeout(this._holdTimer);
      });
      this.shadowRoot.addEventListener("contextmenu", (e) => { if (e.target.closest("[data-hold]")) e.preventDefault(); });
      this.shadowRoot.addEventListener("input", (e) => this._onSlide(e, false));
      this.shadowRoot.addEventListener("change", (e) => this._onSlide(e, true));
    }
  }

  set hass(hass) {
    this._hass = hass;
    this._resolve();
    const sig = Object.values(this._ids || {}).map((id) => hass.states[id]?.state).join("|") + JSON.stringify(this._pending) + this._confirm + hass.locale?.language + hass.themes?.darkMode;
    if (sig !== this._sig && !this._sliding) { this._sig = sig; this._render(); }
  }

  disconnectedCallback() { clearTimeout(this._staleTimer); clearTimeout(this._confirmTimer); }

  // Basic Full renders Control; only Advanced adds the settings rows.
  getCardSize() {
    const base = { compact: 1, status: 2, control: 3, full: 4 }[this._config.layout] ?? 3;
    return this._config.layout === "full" && this._advanced ? base + 1 : base;
  }
  getGridOptions() { return { columns: 12, min_columns: 6, rows: "auto" }; }

  // Entities come from the registry by unique_id, so renamed entity_ids still resolve.
  _resolve() {
    const want = this._config.device_id || null;
    if (this._idsFor === want || this._resolving === want) return;
    this._resolving = want;
    const msg = { type: "eveus/card_entities" };
    if (want) msg.device_id = want;
    this._hass
      .callWS(msg)
      .then((res) => {
        this._ids = {};
        for (const [k, key] of Object.entries(KEYS)) if (res.entities[key]) this._ids[k] = res.entities[key];
      })
      .catch(() => { this._ids = null; })
      .finally(() => {
        this._idsFor = want;
        this._resolving = undefined;
        this._sig = null;
        if (this._hass) this.hass = this._hass;
      });
  }

  _s(k) { return this._ids?.[k] && this._hass ? this._hass.states[this._ids[k]] : undefined; }
  _v(k) { return k in this._pending ? this._pending[k] : num(this._s(k)); }
  get _t() {
    const l = this._config.language !== "auto" ? this._config.language : this._hass.locale?.language || "en";
    return l.startsWith("uk") ? I18N.uk : I18N.en;
  }
  get _advanced() { return this._config.mode !== "basic" && !!this._s("soc"); }
  get _charging() { return this._s("state")?.state === "Charging"; }
  // The charger-backed reading that says whether anything else is live. The SOC
  // numbers are HA-local and survive an outage, so they may never answer this.
  get _online() {
    const s = this._s("state")?.state;
    return !!s && s !== "unavailable" && s !== "unknown";
  }
  get _reached() {
    const soc = num(this._s("soc")), tgt = num(this._s("targetSoc"));
    return this._online && this._advanced && soc !== null && tgt !== null && soc >= tgt;
  }

  _socColor() {
    if (!this._online) return C.off;
    if (this._reached) return C.purple;
    if (!this._charging) return C.grey;
    const soc = num(this._s("soc"));
    if (!this._advanced || soc === null) return C.blue;
    return soc < 40 ? C.red : soc < 75 ? C.orange : C.green;
  }

  _batteryIcon() {
    const soc = this._advanced ? num(this._s("soc")) : null;
    if (soc === null) return this._charging ? "mdi:flash" : "mdi:ev-station";
    const step = Math.min(100, Math.max(0, Math.round(soc / 20) * 20));
    if (this._charging) return step >= 100 ? "mdi:battery-charging-100" : step < 20 ? "mdi:battery-charging-outline" : `mdi:battery-charging-${step}`;
    return step >= 100 ? "mdi:battery" : step < 20 ? "mdi:battery-outline" : `mdi:battery-${step}`;
  }

  _stateText() {
    if (!this._online) return this._t.offline;
    const s = this._s("state");
    const txt = this._hass.formatEntityState ? this._hass.formatEntityState(s) : s.state;
    // Stopped by the user reads as one word, so it fits a phone tile.
    return this._s("stop")?.state === "on" && !this._charging ? this._t.paused : txt;
  }

  _money(v) {
    const u = this._s("sessionCost")?.attributes?.unit_of_measurement || "₴";
    return v === null ? "--" : u.length > 1 ? `${fmt(v)} ${u}` : `${u}${fmt(v)}`;
  }

  _eta() {
    const s = this._s("eta");
    return this._charging && s ? dur(s.state) : null;
  }

  _finish() {
    const s = this._s("finish");
    const d = s && !isNaN(Date.parse(s.state)) ? new Date(s.state) : null;
    return d ? d.toLocaleTimeString(this._hass.locale?.language, { hour: "2-digit", minute: "2-digit" }) : null;
  }

  // How long the last charger reading has been stale.
  _since() {
    const t = Date.parse(this._s("state")?.last_changed);
    if (isNaN(t)) return "";
    const m = Math.floor((Date.now() - t) / 60000);
    return m < 1 ? "" : m < 60 ? `${m}${this._t.mn}` : `${Math.floor(m / 60)}${this._t.hr}`;
  }

  // ---------- building blocks ----------
  // `hold`: the setting a long press opens (SOC → Initial SOC, To target → Target SOC, Current → Charging Current).
  _tile({ icon, label, value, color = C.grey, more, toggle, hold, cls = "" }) {
    const data = (toggle ? `data-toggle="${toggle}"` : more ? `data-more="${more}"` : "") + (hold ? ` data-hold="${hold}"` : "");
    const active = color !== C.grey && color !== C.off;
    return `<div class="t ${active ? "act" : ""} ${cls}" role="button" tabindex="0" style="--c:${color}" ${data}>
      <div class="th"><ha-icon icon="${icon}"></ha-icon><span class="l">${label}</span></div><span class="v">${value}</span></div>`;
  }
  _pair(from, to, color) { return `<i>${from}</i><i class="ar">→</i><b style="--value-color:${color}">${to}</b>`; }
  // A reading the charger cannot supply right now is left out, never printed as "--".
  _rows(...parts) { return parts.filter(Boolean).join('<span class="nl"></span>'); }

  _stepper(k, label, unit, d = 0) {
    const s = this._s(k);
    if (!s) return "";
    const v = this._v(k);
    return `<div class="st" role="button" tabindex="0" data-more="${this._ids[k]}"><span class="l">${label}</span>
      <div class="sr"><button data-step="${k}" data-dir="-1">−</button><b>${fmt(v, d)}<small class="${unit.length > 1 ? "lu" : ""}">${unit}</small></b><button data-step="${k}" data-dir="1">+</button></div></div>`;
  }

  _slider() {
    const s = this._s("chargingCurrent");
    if (!s) return "";
    const a = s.attributes, v = this._v("chargingCurrent");
    const lim = num(this._s("adaptiveLimit"));
    const min = a.min ?? 6, max = a.max ?? 32;
    const mark = lim !== null && lim < max && lim > min ? `<span class="mk" style="left:${((lim - min) / (max - min)) * 100}%" title="adaptive ${lim}A"></span>` : "";
    return `<div class="sl"><ha-icon icon="mdi:current-ac"></ha-icon><span class="l">${this._t.setCurrent}</span>
      <div class="rw">${mark}<input type="range" data-slide="chargingCurrent" ${this._online ? "" : "disabled"} min="${min}" max="${max}" step="${a.step ?? 1}" value="${v ?? min}"></div>
      <b class="sv">${fmt(v)}A</b></div>`;
  }

  // Only a live SOC may fill the bar: Initial and Target SOC are HA-local numbers
  // that keep their value through an outage and would draw a charge that is not there.
  _bar() {
    if (!this._advanced) return "";
    const soc = num(this._s("soc"));
    const ini = num(this._s("initialSoc")) ?? 0, tgt = num(this._s("targetSoc")) ?? 100;
    const live = this._online && soc !== null;
    const col = this._socColor();
    return `<div class="bar">${live ? `<span class="base" style="width:${ini}%"></span>
      <span class="fill" style="left:${ini}%;width:${Math.max(0, soc - ini)}%;background:${col}"></span>
      ${ini > 0 ? `<span class="tg" style="left:${ini}%"></span>` : ""}` : ""}<span class="tg" style="left:${tgt}%"></span></div>`;
  }

  // Three primary tiles — the middle one spans two rows — plus the fourth tile
  // below the first. Every layout puts the same block in the same places.
  _metrics() {
    const t = this._t, col = this._socColor();
    const cur = num(this._s("current")), set = num(this._s("currentSet"));
    const session = `<b style="--value-color:${col}">${kwh(num(this._s("sessionEnergy"))) ?? "--"}</b><i class="ar">·</i><b style="--value-color:${col}">${this._money(num(this._s("sessionCost")))}</b>`;
    const tiles = [];
    let sess;
    if (this._advanced) {
      const soc = num(this._s("soc")), ini = this._v("initialSoc"), tgt = this._v("targetSoc");
      const et = num(this._s("energyToTarget")), ct = num(this._s("costToTarget"));
      const eta = this._eta(), fin = this._finish();
      // Charging: how long is left. Idle: how far there is to go — both are useful,
      // three dashes are not.
      const head = eta ? this._pair(`${fmt(tgt)}%`, eta, col) : this._pair(`${fmt(soc)}%`, `${fmt(tgt)}%`, col);
      const left = [kwh(et), ct === null ? null : this._money(ct)].filter(Boolean).join(" · ");
      const goal = this._rows(head, left && `<i>${left}</i>`, fin && `<i><ha-icon icon="mdi:flag-checkered"></ha-icon> ${fin}</i>`);
      tiles.push(this._tile({ icon: this._batteryIcon(), label: t.soc, value: this._pair(`${fmt(ini)}%`, `${fmt(soc)}%`, col), color: col, more: this._ids.soc, hold: this._ids.initialSoc }));
      tiles.push(this._tile({ icon: "mdi:flag-checkered", label: t.eta, value: goal, color: col, more: this._ids.eta, hold: this._ids.targetSoc, cls: "tall" }));
      sess = this._tile({ icon: "mdi:counter", label: t.session, value: session, color: col, more: this._ids.sessionEnergy });
    } else {
      const bt = num(this._s("boxTemp")), pt = num(this._s("plugTemp"));
      const time = dur(this._s("sessionTime")?.state);
      tiles.push(this._tile({ icon: "mdi:flash", label: t.power, value: `<b style="--value-color:${col}">${kw(num(this._s("power")))}</b><i class="ar">·</i><i>${fmt(num(this._s("voltage")))}V</i>`, color: col, more: this._ids.power }));
      tiles.push(this._tile({ icon: "mdi:timer-outline", label: t.session, value: this._rows(`<b style="--value-color:${col}">${time ?? "--"}</b>`, session), color: col, more: this._ids.sessionTime, cls: "tall" }));
      sess = this._tile({ icon: "mdi:thermometer", label: t.temp, value: `<i>${fmt(bt)}° · ${fmt(pt)}°</i>`, more: this._ids.boxTemp });
    }
    tiles.push(this._tile({ icon: "mdi:current-ac", label: t.current, value: this._pair(`${fmt(set)}A`, `${fmt(cur)}A`, col), color: col, more: this._ids.current, hold: this._ids.chargingCurrent }));
    return { tiles, col, sess };
  }

  // State on the left, SOC bar filling the rest, and below them the one line
  // that says why the charger is not charging — or how stale the readings are.
  _strip() {
    return `<div class="sb" role="button" tabindex="0" data-more="${this._ids.state}"><span class="sst">${this._stateText()}</span>${this._bar()}${this._note()}</div>`;
  }

  _note() {
    if (!this._online) {
      const since = this._since();
      return `<span class="sn"><ha-icon icon="mdi:clock-alert-outline"></ha-icon>${since || ""}</span>`;
    }
    const s = this._s("reason"), r = s?.state;
    if (this._charging || !r || r === "Charging" || r === "unknown" || r === "unavailable") return "";
    const txt = this._hass.formatEntityState ? this._hass.formatEntityState(s) : r;
    // "Charge Complete · Charge Complete" says nothing the state did not.
    if (txt === this._stateText()) return "";
    return `<span class="sn" data-more="${this._ids.reason}">${txt}</span>`;
  }

  // Stale readings raise no alarm: everything below is the charger's last word, not its current one.
  _alerts() {
    if (!this._online) return "";
    const out = [];
    if (this._s("state")?.state === "Error") out.push(`${this._stateText()} · ${this._s("substate")?.state ?? ""}`);
    const g = this._s("ground")?.state;
    if (g && g !== "Connected" && g !== "unavailable" && g !== "unknown") out.push(this._t.ground);
    return out.length ? `<div class="al"><ha-icon icon="mdi:alert"></ha-icon>${out.join(" · ")}</div>` : "";
  }

  // ---------- layouts ----------
  _compact() {
    const col = this._socColor();
    const e = num(this._s("sessionEnergy")), cost = num(this._s("sessionCost"));
    const timing = this._advanced ? this._eta() : dur(this._s("sessionTime")?.state);
    const chips = [];
    if (this._advanced) chips.push(`<b style="--value-color:${col}">${fmt(num(this._s("soc")))}%</b>`);
    chips.push(`<span>${kw(num(this._s("power")))}</span>`);
    if (timing) chips.push(`<span>${timing}</span>`);
    chips.push(`<span class="ce">${kwh(e) ?? "--"}</span>`);
    chips.push(`<span>${this._money(cost)}</span>`);
    return `<div class="cp" style="--c:${col}"><div class="cr">
      <ha-icon icon="${this._batteryIcon()}" style="color:${col}"></ha-icon>
      <div class="ch">${chips.join("")}</div></div>${this._strip()}${this._alerts()}</div>`;
  }

  _status() {
    const t = this._t, { tiles, sess, col } = this._metrics();
    const extra = this._advanced
      ? this._tile({ icon: "mdi:flash", label: t.power, value: `<b style="--value-color:${col}">${kw(num(this._s("power")))}</b><i class="ar">·</i><i>${fmt(num(this._s("voltage")))}V</i>`, color: col, more: this._ids.power })
      : this._tile({ icon: "mdi:history", label: t.lastSession, value: `<i>${kwh(num(this._s("lastSession"))) ?? "--"}</i>`, more: this._ids.lastSession });
    return `<div class="g3">${tiles.join("")}${sess}${extra}</div>${this._strip()}${this._alerts()}`;
  }

  // One charge ignores every limit; Stop is a road sign. Stopping a running
  // charge asks once, in the tile itself.
  _controls() {
    const t = this._t, { tiles, sess } = this._metrics();
    const one = this._s("oneCharge")?.state === "on", stop = this._s("stop")?.state === "on";
    const btn = (k, on, color, label, icon) => {
      const ask = this._confirm === k;
      return `<div class="t bn ${on || ask ? "act" : ""}" style="--c:${ask ? C.red : on ? color : C.grey}" data-toggle="${k}" role="switch" tabindex="0" aria-checked="${on}" aria-label="${label}">${icon}<span class="l">${ask ? t.sure : label}</span></div>`;
    };
    return `<div class="g3">${tiles.join("")}${sess}
      <div class="bt">${btn("oneCharge", one, C.green, t.one, '<ha-icon icon="mdi:lightning-bolt-circle"></ha-icon>')}${btn("stop", stop, C.red, t.stop, STOP_SIGN)}</div>
      </div>${this._strip()}${this._slider()}${this._alerts()}`;
  }

  _full() {
    const t = this._t;
    let extra = "";
    if (this._advanced) {
      const lim = this._s("socLimit")?.state === "on";
      const bt = num(this._s("boxTemp")), pt = num(this._s("plugTemp"));
      extra = `<div class="g4">${this._stepper("initialSoc", t.initial, "%")}${this._stepper("targetSoc", t.target, "%")}
        ${this._stepper("capacity", t.capacity, "kWh")}${this._stepper("correction", t.correction, "%", 1)}</div>
        <div class="g3">
        ${this._tile({ icon: "mdi:flash", label: t.power, value: `<i>${kw(num(this._s("power")))} · ${fmt(num(this._s("voltage")))}V</i>`, more: this._ids.power })}
        ${this._tile({ icon: "mdi:thermometer", label: t.temp, value: `<i>${fmt(bt)}° · ${fmt(pt)}°</i>`, more: this._ids.boxTemp })}
        ${this._tile({ icon: "mdi:battery-lock", label: t.socLimit, value: lim ? t.on : t.off, color: lim ? C.green : C.grey, toggle: "socLimit" })}</div>`;
    } else {
      const prot = this._s("groundProt")?.state === "on";
      extra = `<div class="g3">
        ${this._tile({ icon: "mdi:history", label: t.lastSession, value: `<i>${kwh(num(this._s("lastSession"))) ?? "--"}</i>`, more: this._ids.lastSession })}
        ${this._tile({ icon: "mdi:speedometer", label: t.adaptive, value: `<i>${fmt(num(this._s("adaptiveLimit")))}A</i>`, more: this._ids.adaptiveLimit })}
        ${this._tile({ icon: "mdi:shield-flash", label: t.groundProt, value: prot ? t.on : t.off, color: prot ? C.green : C.grey, toggle: "groundProt" })}</div>`;
    }
    return this._controls() + extra;
  }

  _render() {
    const root = this.shadowRoot;
    if (this._idsFor === undefined) { root.innerHTML = "<ha-card></ha-card>"; return; } // still looking up
    if (!this._ids || !this._s("state")) {
      root.innerHTML = `<ha-card><div class="empty">${this._t.noDevice}</div></ha-card>`;
      return;
    }
    const body = this[`_${this._config.layout === "control" ? "controls" : this._config.layout}`]();
    // Keep the charging pulse at its current phase when a reading updates.
    const phases = new Map();
    for (const animation of root.getAnimations()) {
      if (!phases.has(animation.animationName)) phases.set(animation.animationName, animation.currentTime);
    }
    root.innerHTML = `<style>${STYLE}</style><ha-card class="${this._charging ? "chg" : ""} ${this._online ? "" : "off"} ${this._config.layout}" style="--c:${this._socColor()};--value-weight:${this._hass.themes?.darkMode === false ? "60%" : "100%"}">${body}</ha-card>`;
    for (const animation of root.getAnimations()) {
      const phase = phases.get(animation.animationName);
      if (phase !== undefined && phase !== null) animation.currentTime = phase;
    }
    // Nothing changes state while the charger is unreachable, so the age of the
    // readings has to move the card by itself.
    clearTimeout(this._staleTimer);
    if (!this._online) this._staleTimer = setTimeout(() => { this._sig = null; if (this._hass) this.hass = this._hass; }, 60000);
  }

  // ---------- actions ----------
  _more(entityId) {
    const ev = new Event("hass-more-info", { bubbles: true, composed: true });
    ev.detail = { entityId };
    this.dispatchEvent(ev);
  }

  _holdStart(e) {
    this._held = false;
    clearTimeout(this._holdTimer);
    const el = e.target.closest("[data-hold]");
    if (!el) return;
    this._holdAt = [e.clientX, e.clientY];
    this._holdTimer = setTimeout(() => { this._held = true; this._more(el.dataset.hold); }, 500);
  }

  // Tiles are divs, so Enter and Space have to be wired by hand. Native
  // controls bring their own.
  _onKey(e) {
    if (e.key !== "Enter" && e.key !== " ") return;
    if (["BUTTON", "INPUT"].includes(e.target.tagName)) return;
    if (!e.target.closest("[data-toggle],[data-more]")) return;
    e.preventDefault();
    e.target.click();
  }

  _onClick(e) {
    if (this._held) { this._held = false; e.stopPropagation(); return; }
    const step = e.target.closest("[data-step]");
    if (step) { e.stopPropagation(); return this._online ? this._step(step.dataset.step, Number(step.dataset.dir)) : undefined; }
    const tog = e.target.closest("[data-toggle]");
    if (tog) {
      const k = tog.dataset.toggle, s = this._s(k);
      if (!s || !this._online) return;
      const on = s.state === "on";
      // Stopping a running charge asks once, in the tile; a browser dialog is
      // unstyled and some webviews never show it.
      if (k === "stop" && !on && this._charging && this._confirm !== k) return this._ask(k);
      this._confirm = null;
      return this._hass.callService("switch", on ? "turn_off" : "turn_on", { entity_id: this._ids[k] });
    }
    const more = e.target.closest("[data-more]");
    if (more && !e.target.closest("input")) this._more(more.dataset.more);
  }

  _ask(k) {
    this._confirm = k;
    clearTimeout(this._confirmTimer);
    this._confirmTimer = setTimeout(() => { this._confirm = null; this._sig = null; if (this._hass) this.hass = this._hass; }, 4000);
    this._render();
  }

  // Steppers accumulate taps and send one command after a short pause.
  _step(k, dir) {
    const s = this._s(k), a = s.attributes, stepSize = a.step ?? 1;
    const base = this._v(k) ?? a.min ?? 0;
    const next = Math.min(a.max ?? Infinity, Math.max(a.min ?? -Infinity, Math.round((base + dir * stepSize) / stepSize) * stepSize));
    this._pending[k] = Number(next.toFixed(3));
    this._render();
    clearTimeout(this._timers?.[k]);
    this._timers = this._timers || {};
    this._timers[k] = setTimeout(() => this._commit(k), 800);
  }

  _onSlide(e, commit) {
    const k = e.target.dataset?.slide;
    if (!k) return;
    this._pending[k] = Number(e.target.value);
    this._sliding = !commit;
    const out = this.shadowRoot.querySelector(".sv");
    if (out) out.textContent = `${e.target.value}A`;
    if (commit) this._commit(k);
  }

  async _commit(k) {
    const value = this._pending[k];
    try { await this._hass.callService("number", "set_value", { entity_id: this._ids[k], value }); }
    finally { setTimeout(() => { delete this._pending[k]; this._sig = null; this.hass = this._hass; }, 4000); }
  }
}

const STOP_SIGN = `<svg viewBox="0 0 24 24" aria-hidden="true"><polygon points="7,1 17,1 23,7 23,17 17,23 7,23 1,17 1,7" fill="var(--c)"/>`
  + `<polygon points="7.6,2.4 16.4,2.4 21.6,7.6 21.6,16.4 16.4,21.6 7.6,21.6 2.4,16.4 2.4,7.6" fill="none" stroke="#fff" stroke-width="1"/>`
  + `<text x="12" y="15" text-anchor="middle" font-size="7.2" font-weight="800" font-family="Arial,sans-serif" fill="#fff">STOP</text></svg>`;

const STYLE = `
ha-card{container-type:inline-size;box-sizing:border-box;padding:4px;display:flex;flex-direction:column;gap:4px;border-radius:14px;overflow:hidden;
  line-height:1.2;transition:box-shadow .3s}
ha-card.chg{animation:bp 2.4s ease-in-out infinite}
@keyframes bp{0%,100%{box-shadow:0 1px 10px color-mix(in srgb,var(--c) 22%,transparent),inset 0 0 6px color-mix(in srgb,var(--c) 5%,transparent)}50%{box-shadow:0 2px 22px color-mix(in srgb,var(--c) 48%,transparent),inset 0 0 12px color-mix(in srgb,var(--c) 12%,transparent)}}
ha-card.off .t,ha-card.off .st,ha-card.off .sl,ha-card.off .ch{opacity:.5}
.g3,.g4{display:grid;gap:4px;grid-template-columns:repeat(3,minmax(0,1fr))}
.g4{grid-template-columns:repeat(4,minmax(0,1fr))}
.t,.st,.sl,.cp{box-sizing:border-box;border-radius:10px;padding:3px 5px;cursor:pointer;min-width:0;
  background:rgba(127,127,127,.05);border:1px solid rgba(127,127,127,.16);transition:background .2s,border-color .2s}
.t.act{background:linear-gradient(145deg,color-mix(in srgb,var(--c) 23%,transparent),color-mix(in srgb,var(--c) 8%,transparent));
  border-color:color-mix(in srgb,var(--c) 55%,transparent)}
.chg .t.act{animation:tile-glow 2.4s ease-in-out infinite}
@keyframes tile-glow{0%,100%{box-shadow:inset 0 0 5px color-mix(in srgb,var(--c) 8%,transparent)}50%{box-shadow:inset 0 0 13px color-mix(in srgb,var(--c) 25%,transparent);border-color:color-mix(in srgb,var(--c) 80%,transparent)}}
.t{position:relative;overflow:hidden;display:flex;flex-direction:column;justify-content:center;align-items:stretch;gap:1px;text-align:center}
.chg .t.act::after{content:"";position:absolute;inset:0;pointer-events:none;background:linear-gradient(110deg,transparent 30%,rgba(255,255,255,.16) 50%,transparent 70%);animation:tile-sheen 5s ease-in-out infinite}
@keyframes tile-sheen{0%,45%{transform:translateX(-130%);opacity:0}55%{opacity:1}85%,100%{transform:translateX(130%);opacity:0}}
.th{display:flex;align-items:center;justify-content:center;gap:5px;text-align:center;min-width:0}
[data-hold]{-webkit-user-select:none;user-select:none;-webkit-touch-callout:none}
.t[data-hold]::before{content:"";position:absolute;right:3px;top:3px;width:3px;height:3px;border-radius:50%;background:var(--c);opacity:.45}
.t ha-icon{--mdc-icon-size:13px;color:var(--c);flex:none;transform:translateY(-1px)}
.chg .t.act ha-icon,.chg .cp ha-icon{animation:ig 2.4s ease-in-out infinite}
@keyframes ig{0%,100%{filter:drop-shadow(0 0 2px color-mix(in srgb,var(--c) 45%,transparent))}50%{filter:drop-shadow(0 0 8px color-mix(in srgb,var(--c) 90%,transparent))}}
.l{font-size:14px;line-height:1.2;font-weight:600;letter-spacing:0;color:color-mix(in srgb,var(--primary-text-color) 80%,var(--secondary-text-color));min-width:0;overflow-wrap:anywhere}
.v,.sr b,.sv{font-size:14px;line-height:1.2;font-weight:600;font-variant-numeric:tabular-nums;color:var(--primary-text-color);min-width:0;max-width:100%;overflow-wrap:anywhere}
.v{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:center;column-gap:2px}
.v i{font-style:normal;color:var(--secondary-text-color);font-weight:500}
.v>b,.v>i{max-width:100%}
.v b[style],.ch b[style]{color:color-mix(in srgb,var(--value-color) var(--value-weight,100%),var(--primary-text-color))}
.chg .v b[style]{text-shadow:0 0 10px color-mix(in srgb,var(--value-color) 25%,transparent)}
.ar{opacity:.65;margin:0 1px}
.t.tall{grid-row:span 2}
.t.w2{grid-column:span 2}
.nl{flex-basis:100%;height:0}
.v i ha-icon{--mdc-icon-size:12px;color:var(--secondary-text-color);transform:none;animation:none}
.bt{display:grid;grid-template-columns:1fr 1fr;gap:4px;min-width:0}
.bn{align-items:center;justify-content:center;gap:2px;padding:3px}
.bn .l{font-size:11px;overflow-wrap:anywhere}
.bn ha-icon{--mdc-icon-size:22px;transform:none}
.bn svg{width:22px;height:22px;display:block}
.st{display:flex;flex-direction:column;justify-content:center;align-items:center;gap:1px;padding:3px 2px}
.st .l{font-size:12px}
.sr{display:flex;align-items:center;justify-content:space-between;gap:1px;width:100%}
.sr b{text-align:center;flex:1}
.sr small{font-size:10px;font-weight:500;color:var(--secondary-text-color);margin-left:1px}
button{all:unset;box-sizing:border-box;cursor:pointer;width:20px;height:24px;border-radius:6px;text-align:center;line-height:24px;font-size:16px;
  background:rgba(127,127,127,.12);color:var(--primary-text-color);flex:none;touch-action:manipulation}
button:active{background:var(--primary-color);color:var(--text-primary-color)}
button:focus-visible,input:focus-visible,[tabindex]:focus-visible{outline:2px solid var(--primary-color);outline-offset:2px}
@media (hover:hover){.t:hover,.st:hover,.cp:hover{border-color:color-mix(in srgb,var(--c) 55%,var(--secondary-text-color))}button:hover{background:rgba(127,127,127,.25)}}
.sl{display:flex;align-items:center;gap:6px;cursor:default;padding:2px 8px}
.sl ha-icon{--mdc-icon-size:16px;color:var(--secondary-text-color);flex:none}
.sl .l{font-size:13px;flex:none;white-space:nowrap}
.rw{position:relative;flex:1;display:flex;align-items:center;min-width:0}
input[type=range]{width:100%;min-width:0;margin:0;accent-color:var(--primary-color);height:24px;cursor:pointer}
input[type=range]:disabled{cursor:not-allowed;opacity:.45}
.mk{position:absolute;top:3px;width:2px;height:18px;background:${C.orange};border-radius:1px;pointer-events:none;transform:translateX(-1px)}
.sv{min-width:36px;text-align:right}
.bar{position:relative;height:4px;border-radius:2px;background:rgba(127,127,127,.15);margin:0 5px 2px;overflow:visible}
.bar .base{position:absolute;left:0;top:0;height:100%;border-radius:2px;background:color-mix(in srgb,var(--c) 30%,transparent)}
.bar .fill{position:absolute;top:0;height:100%;border-radius:2px;overflow:hidden}
.chg .bar .fill::after{content:"";position:absolute;inset:0;background:linear-gradient(100deg,transparent 15%,rgba(255,255,255,.6) 50%,transparent 85%);animation:flow 3s ease-in-out infinite}
@keyframes flow{0%{transform:translateX(-100%);opacity:0}20%,80%{opacity:.8}100%{transform:translateX(100%);opacity:0}}
.bar .tg{position:absolute;top:-2px;width:2px;height:8px;background:var(--primary-text-color);opacity:.65;transform:translateX(-1px)}
.al{display:flex;align-items:center;gap:6px;font-size:12px;color:${C.red};padding:2px 6px;overflow-wrap:anywhere}
.al ha-icon{--mdc-icon-size:16px;flex:none}
.cp{display:flex;flex-direction:column;gap:5px;padding:5px 8px;cursor:default}
.cr{display:flex;flex-wrap:wrap;align-items:center;gap:4px 6px;min-width:0}
.cp ha-icon{--mdc-icon-size:18px;flex:none}
.ch{flex:1 1 auto;min-width:0;display:flex;flex-wrap:wrap;align-items:baseline;justify-content:flex-end;gap:2px 7px;font-size:14px;line-height:1.2;font-weight:600;font-variant-numeric:tabular-nums;color:var(--primary-text-color)}
.ch span,.ch b{max-width:100%;overflow-wrap:anywhere}
.cp .bar{margin:0}
.cp .sb{padding:0}
.sb{display:flex;flex-wrap:wrap;align-items:center;gap:2px 8px;padding:0 5px;cursor:pointer;min-width:0}
.sb .sst{flex:none;font-size:12px;font-weight:600;color:var(--c);white-space:nowrap}
.sb .bar{flex:1 1 60px;margin:0}
.sn{flex-basis:100%;display:flex;align-items:center;gap:3px;font-size:11px;line-height:1.3;color:var(--secondary-text-color);overflow-wrap:anywhere}
.sn ha-icon{--mdc-icon-size:12px;color:var(--secondary-text-color)}
.compact{padding:0}
.compact .cp{border:none;background:none}
.empty{padding:16px;color:var(--secondary-text-color)}
@container (max-width: 360px){.t{padding:3px}.th{gap:2px}.sr small{display:block;margin:0}.sr b{font-size:13px}.sl{gap:4px;padding:2px 5px}}
@media (prefers-reduced-motion:reduce){ha-card.chg,.chg .t.act,.chg .t.act::after,.chg .t.act ha-icon,.chg .cp ha-icon,.chg .bar .fill::after{animation:none}.t,.st,.sl,.cp,ha-card{transition:none}.chg .bar .fill::after,.chg .t.act::after{display:none}}
`;

if (!customElements.get(CARD)) customElements.define(CARD, EveusCard);
window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === CARD)) {
  window.customCards.push({ type: CARD, name: "Eveus EV Charger", description: "Compact Eveus charger card: compact / status / control / full", preview: true });
}
