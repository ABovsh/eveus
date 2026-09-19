// Eveus card — one card, four layouts: compact / status / control / full.
const CARD = "eveus-card";
const C = { red: "#E74C3C", orange: "#F39C12", green: "#2ECC71", grey: "#95A5A6", blue: "#3498DB" };
const LAYOUTS = ["compact", "status", "control", "full"];

const I18N = {
  en: {
    soc: "SOC", eta: "Time to SOC", current: "Current", session: "Session", power: "Power",
    state: "State", one: "One charge", stop: "Stop", on: "On", off: "Off", setCurrent: "Charging current",
    initial: "Initial", target: "Target", capacity: "Capacity", correction: "Loss", socLimit: "SOC limit",
    toTarget: "To target", finish: "Finish", temp: "Temp", voltage: "Voltage", paused: "Paused",
    stopConfirm: "Stop charging?", noDevice: "No Eveus charger found", ground: "No ground",
  },
  uk: {
    soc: "Заряд", eta: "До цілі", current: "Струм", session: "Сесія", power: "Потужність",
    state: "Стан", one: "Один заряд", stop: "Стоп", on: "Увімк", off: "Вимк", setCurrent: "Струм заряду",
    initial: "Початковий", target: "Ціль", capacity: "Ємність", correction: "Втрати", socLimit: "Ліміт SOC",
    toTarget: "До цілі", finish: "Кінець", temp: "Темп.", voltage: "Напруга", paused: "Пауза",
    stopConfirm: "Зупинити заряджання?", noDevice: "Станцію Eveus не знайдено", ground: "Немає заземлення",
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
  oneCharge: "one_charge", stop: "stop_charging", socLimit: "limit_soc_enabled",
  chargingCurrent: "charging_current", initialSoc: "initial_soc", targetSoc: "target_soc",
  capacity: "battery_capacity", correction: "soc_correction",
};

const num = (s) => (s && s.state !== "unknown" && s.state !== "unavailable" && !isNaN(parseFloat(s.state)) ? parseFloat(s.state) : null);
const fmt = (v, d = 0) => (v === null ? "--" : Number(v.toFixed(d)).toString());

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
    const sig = Object.values(this._ids || {}).map((id) => hass.states[id]?.state).join("|") + JSON.stringify(this._pending) + hass.locale?.language;
    if (sig !== this._sig && !this._sliding) { this._sig = sig; this._render(); }
  }

  getCardSize() { return { compact: 1, status: 2, control: 3, full: 5 }[this._config.layout]; }
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

  _s(k) { return this._ids?.[k] ? this._hass.states[this._ids[k]] : undefined; }
  _v(k) { return k in this._pending ? this._pending[k] : num(this._s(k)); }
  get _t() {
    const l = this._config.language !== "auto" ? this._config.language : this._hass.locale?.language || "en";
    return l.startsWith("uk") ? I18N.uk : I18N.en;
  }
  get _advanced() { return this._config.mode !== "basic" && !!this._s("soc"); }
  get _charging() { return this._s("state")?.state === "Charging"; }

  _socColor() {
    const soc = num(this._s("soc"));
    if (!this._charging) return C.grey;
    if (!this._advanced || soc === null) return C.blue;
    return soc < 40 ? C.red : soc < 75 ? C.orange : C.green;
  }

  _batteryIcon() {
    const soc = num(this._s("soc"));
    if (soc === null) return this._charging ? "mdi:battery-charging" : "mdi:ev-station";
    const step = Math.min(100, Math.max(0, Math.round(soc / 20) * 20));
    if (this._charging) return step >= 100 ? "mdi:battery-charging-100" : step < 20 ? "mdi:battery-charging-outline" : `mdi:battery-charging-${step}`;
    return step >= 100 ? "mdi:battery" : step < 20 ? "mdi:battery-outline" : `mdi:battery-${step}`;
  }

  _stateText() {
    const s = this._s("state");
    if (!s) return "--";
    const txt = this._hass.formatEntityState ? this._hass.formatEntityState(s) : s.state;
    // Stopped by the user reads as one word, so it fits a phone tile.
    return this._s("stop")?.state === "on" && !this._charging ? this._t.paused : txt;
  }

  _eta() {
    const s = this._s("eta");
    if (!this._charging || !s || !/\d/.test(s.state)) return "--";
    return s.state.replace(/\s+/g, "");
  }

  _finish() {
    const s = this._s("finish");
    const d = s && !isNaN(Date.parse(s.state)) ? new Date(s.state) : null;
    return d ? d.toLocaleTimeString(this._hass.locale?.language, { hour: "2-digit", minute: "2-digit" }) : "--";
  }

  // ---------- building blocks ----------
  // `hold`: the setting a long press opens (SOC → Initial SOC, Time to SOC → Target SOC, Current → Charging Current).
  _tile({ icon, label, value, color = C.grey, more, toggle, hold, cls = "" }) {
    const data = (toggle ? `data-toggle="${toggle}"` : more ? `data-more="${more}"` : "") + (hold ? ` data-hold="${hold}"` : "");
    const active = color !== C.grey;
    return `<div class="t ${active ? "act" : ""} ${cls}" style="--c:${color}" ${data}>
      <ha-icon icon="${icon}"></ha-icon><div class="tx"><span class="l">${label}</span><span class="v">${value}</span></div></div>`;
  }
  _pair(from, to, color) { return `<i>${from}</i><i class="ar">→</i><b style="color:${color}">${to}</b>`; }

  _stepper(k, label, unit, d = 0) {
    const s = this._s(k);
    if (!s) return "";
    const v = this._v(k);
    return `<div class="st" data-more="${this._ids[k]}"><span class="l">${label}</span>
      <div class="sr"><button data-step="${k}" data-dir="-1">−</button><b>${fmt(v, d)}<small class="${unit.length > 1 ? "lu" : ""}">${unit}</small></b><button data-step="${k}" data-dir="1">+</button></div></div>`;
  }

  _slider() {
    const s = this._s("chargingCurrent");
    if (!s) return "";
    const a = s.attributes, v = this._v("chargingCurrent");
    const lim = num(this._s("adaptiveLimit"));
    const min = a.min ?? 6, max = a.max ?? 32;
    const mark = lim !== null && lim < max && lim > min ? `<span class="mk" style="left:${((lim - min) / (max - min)) * 100}%" title="adaptive ${lim} A"></span>` : "";
    return `<div class="sl"><ha-icon icon="mdi:current-ac"></ha-icon><span class="l">${this._t.setCurrent}</span>
      <div class="rw">${mark}<input type="range" data-slide="chargingCurrent" min="${min}" max="${max}" step="${a.step ?? 1}" value="${v ?? min}"></div>
      <b class="sv">${fmt(v)} A</b></div>`;
  }

  _bar() {
    if (!this._advanced) return "";
    const ini = num(this._s("initialSoc")) ?? 0, soc = num(this._s("soc")) ?? 0, tgt = num(this._s("targetSoc")) ?? 100;
    const col = this._charging ? this._socColor() : C.grey;
    return `<div class="bar"><span class="fill" style="left:${ini}%;width:${Math.max(0, soc - ini)}%;background:${col}"></span>
      <span class="base" style="width:${ini}%"></span>${ini > 0 ? `<span class="tg" style="left:${ini}%"></span>` : ""}<span class="tg" style="left:${tgt}%"></span></div>`;
  }

  _metrics() {
    const t = this._t, col = this._socColor();
    const soc = this._s("soc"), socV = num(soc), ini = this._v("initialSoc"), tgt = this._v("targetSoc");
    const cur = num(this._s("current")), set = num(this._s("currentSet"));
    const e = num(this._s("sessionEnergy")), cost = num(this._s("sessionCost"));
    const tiles = [];
    if (this._advanced) {
      tiles.push(this._tile({ icon: this._batteryIcon(), label: t.soc, value: this._pair(`${fmt(ini)}%`, `${fmt(socV)}%`, col), color: col, more: this._ids.soc, hold: this._ids.initialSoc }));
      tiles.push(this._tile({ icon: "mdi:timer-outline", label: t.eta, value: this._pair(`${fmt(tgt)}%`, this._eta(), col), color: col, more: this._ids.eta, hold: this._ids.targetSoc }));
    } else {
      const p = num(this._s("power"));
      tiles.push(this._tile({ icon: this._batteryIcon(), label: t.power, value: `<b style="color:${col}">${p === null ? "--" : (p / 1000).toFixed(1)} kW</b>`, color: col, more: this._ids.power }));
      tiles.push(this._tile({ icon: "mdi:timer-outline", label: t.session, value: `<b style="color:${col}">${this._s("sessionTime")?.state ?? "--"}</b>`, color: col, more: this._ids.sessionTime }));
    }
    tiles.push(this._tile({ icon: this._charging ? "mdi:flash" : "mdi:flash-outline", label: t.current, value: this._pair(`${fmt(set)}A`, `${fmt(cur)}A`, col), color: col, more: this._ids.current, hold: this._ids.chargingCurrent }));
    return { tiles, col, sess: this._tile({ icon: "mdi:battery-charging", label: t.session, value: `<b style="color:${col}">${e === null ? "--" : e < 10 ? e.toFixed(1) : Math.round(e)}kWh</b><i class="ar">·</i><b style="color:${col}">₴${fmt(cost)}</b>`, color: col, more: this._ids.sessionEnergy }) };
  }

  _alerts() {
    const out = [];
    const st = this._s("state")?.state;
    if (st === "Error") out.push(`${this._stateText()} · ${this._s("substate")?.state ?? ""}`);
    const g = this._s("ground")?.state;
    if (g && g !== "Connected" && g !== "unavailable") out.push(this._t.ground);
    return out.length ? `<div class="al"><ha-icon icon="mdi:alert"></ha-icon>${out.join(" · ")}</div>` : "";
  }

  // ---------- layouts ----------
  _compact() {
    const col = this._socColor();
    const soc = num(this._s("soc")), p = num(this._s("power"));
    const e = num(this._s("sessionEnergy")), cost = num(this._s("sessionCost"));
    const chips = [];
    if (this._advanced) chips.push(`<b style="color:${col}">${fmt(soc)}%</b>`);
    chips.push(`<span>${p === null ? "--" : (p / 1000).toFixed(1)}kW</span>`);
    if (this._advanced) { if (this._charging) chips.push(`<span>${this._eta()}</span>`); }
    else chips.push(`<span>${this._s("sessionTime")?.state?.replace(/\s+/g, "") ?? "--"}</span>`);
    chips.push(`<span class="ce">${e === null ? "--" : e < 10 ? e.toFixed(1) : Math.round(e)}kWh</span>`);
    chips.push(`<span>₴${fmt(cost)}</span>`);
    return `<div class="cp" data-more="${this._ids.state}" style="--c:${col}"><div class="cr">
      <ha-icon icon="${this._batteryIcon()}" style="color:${col}"></ha-icon><span class="v cst">${this._stateText()}</span>
      <div class="ch">${chips.join('<i class="ar">·</i>')}</div></div>${this._bar()}</div>`;
  }

  _status() {
    const t = this._t, { tiles, sess, col } = this._metrics();
    const v = num(this._s("voltage")), p = num(this._s("power"));
    const reason = this._charging ? "" : this._s("reason")?.state;
    return `<div class="g3">${tiles.join("")}${sess}
      ${this._advanced
        ? this._tile({ icon: "mdi:sine-wave", label: t.power, value: `<b style="color:${col}">${p === null ? "--" : (p / 1000).toFixed(1)}kW</b><i class="ar">·</i><i>${fmt(v)}V</i>`, color: col, more: this._ids.power })
        : this._tile({ icon: "mdi:sine-wave", label: t.voltage, value: `<i>${fmt(v)} V</i>`, more: this._ids.voltage })}
      ${this._tile({ icon: "mdi:ev-station", label: t.state, value: `<b>${this._stateText()}</b>`, color: col, more: this._ids.state, cls: reason ? "wide" : "" })}
      </div>${this._bar()}${this._alerts()}`;
  }

  _controls() {
    const t = this._t, { tiles, sess } = this._metrics();
    const one = this._s("oneCharge")?.state === "on", stop = this._s("stop")?.state === "on";
    return `<div class="g3">${tiles.join("")}${sess}
      ${this._tile({ icon: "mdi:ev-station", label: t.one, value: one ? t.on : t.off, color: one ? C.green : C.grey, toggle: "oneCharge" })}
      ${this._tile({ icon: stop ? "mdi:play-circle" : "mdi:stop-circle", label: t.stop, value: stop ? t.paused : t.off, color: stop ? C.red : C.grey, toggle: "stop" })}
      </div>${this._bar()}${this._slider()}${this._alerts()}`;
  }

  _full() {
    const t = this._t;
    let extra = "";
    if (this._advanced) {
      const lim = this._s("socLimit")?.state === "on";
      const et = num(this._s("energyToTarget")), ct = num(this._s("costToTarget"));
      extra = `<div class="g4">${this._stepper("initialSoc", t.initial, "%")}${this._stepper("targetSoc", t.target, "%")}
        ${this._stepper("capacity", t.capacity, "kWh")}${this._stepper("correction", t.correction, "%", 1)}</div>
        <div class="g3">
        ${this._tile({ icon: "mdi:target", label: t.toTarget, value: `<i>${fmt(et, et !== null && et >= 10 ? 0 : 1)}kWh</i><i class="ar">·</i><i>₴${fmt(ct)}</i>`, more: this._ids.energyToTarget })}
        ${this._tile({ icon: "mdi:flag-checkered", label: t.finish, value: `<i>${this._finish()}</i>`, more: this._ids.finish })}
        ${this._tile({ icon: "mdi:battery-lock", label: t.socLimit, value: lim ? t.on : t.off, color: lim ? C.green : C.grey, toggle: "socLimit" })}</div>`;
    } else {
      const v = num(this._s("voltage")), bt = num(this._s("boxTemp")), pt = num(this._s("plugTemp"));
      extra = `<div class="g3">
        ${this._tile({ icon: "mdi:sine-wave", label: t.voltage, value: `<i>${fmt(v)} V</i>`, more: this._ids.voltage })}
        ${this._tile({ icon: "mdi:thermometer", label: t.temp, value: `<i>${fmt(bt)}° · ${fmt(pt)}°</i>`, more: this._ids.boxTemp })}
        ${this._tile({ icon: "mdi:ev-station", label: t.state, value: `<i>${this._stateText()}</i>`, more: this._ids.state })}</div>`;
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
    root.innerHTML = `<style>${STYLE}</style><ha-card class="${this._charging ? "chg" : ""} ${this._config.layout}" style="--c:${this._socColor()}">${body}</ha-card>`;
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

  _onClick(e) {
    if (this._held) { this._held = false; e.stopPropagation(); return; }
    const step = e.target.closest("[data-step]");
    if (step) { e.stopPropagation(); return this._step(step.dataset.step, Number(step.dataset.dir)); }
    const tog = e.target.closest("[data-toggle]");
    if (tog) {
      const k = tog.dataset.toggle, s = this._s(k);
      if (!s) return;
      const on = s.state === "on";
      if (k === "stop" && !on && this._charging && !confirm(this._t.stopConfirm)) return;
      return this._hass.callService("switch", on ? "turn_off" : "turn_on", { entity_id: this._ids[k] });
    }
    const more = e.target.closest("[data-more]");
    if (more && !e.target.closest("input")) this._more(more.dataset.more);
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
    if (out) out.textContent = `${e.target.value} A`;
    if (commit) this._commit(k);
  }

  async _commit(k) {
    const value = this._pending[k];
    try { await this._hass.callService("number", "set_value", { entity_id: this._ids[k], value }); }
    finally { setTimeout(() => { delete this._pending[k]; this._sig = null; this.hass = this._hass; }, 4000); }
  }
}

const STYLE = `
ha-card{container-type:inline-size;padding:4px;display:flex;flex-direction:column;gap:4px;border-radius:14px;overflow:hidden;
  transition:box-shadow .4s}
ha-card.chg{animation:bp 3s ease-in-out infinite}
@keyframes bp{0%,100%{box-shadow:0 1px 8px color-mix(in srgb,var(--c) 10%,transparent)}50%{box-shadow:0 2px 16px color-mix(in srgb,var(--c) 30%,transparent)}}
.g3,.g4{display:grid;gap:4px;grid-template-columns:repeat(3,minmax(0,1fr))}
.g4{grid-template-columns:repeat(4,minmax(0,1fr))}
.t,.st,.sl,.cp{box-sizing:border-box;border-radius:12px;padding:4px 6px;cursor:pointer;min-width:0;
  background:rgba(127,127,127,.06);border:1px solid rgba(127,127,127,.14);transition:all .3s}
.t.act{background:linear-gradient(145deg,color-mix(in srgb,var(--c) 14%,transparent),color-mix(in srgb,var(--c) 3%,transparent));
  border-color:color-mix(in srgb,var(--c) 30%,transparent)}
.t{display:flex;align-items:center;gap:6px}
[data-hold]{-webkit-user-select:none;user-select:none;-webkit-touch-callout:none}
.t ha-icon{--mdc-icon-size:22px;color:var(--c);flex:none}
.tx{flex:1;display:flex;flex-direction:column;align-items:center;text-align:center;min-width:0}
.chg .t.act ha-icon{animation:ig 2s ease-in-out infinite}
@keyframes ig{0%,100%{filter:drop-shadow(0 0 2px color-mix(in srgb,var(--c) 40%,transparent))}50%{filter:drop-shadow(0 0 7px color-mix(in srgb,var(--c) 75%,transparent))}}
.l{font-size:9px;font-weight:600;text-transform:uppercase;letter-spacing:.5px;color:var(--secondary-text-color);opacity:.8;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
.v,.sr b,.sv{font-size:12px;font-weight:600;font-variant-numeric:tabular-nums;color:var(--primary-text-color);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}
.v i{font-style:normal;color:var(--secondary-text-color);font-weight:500}
.ar{opacity:.5;margin:0 2px}
.st{display:flex;flex-direction:column;align-items:center;gap:2px;padding:4px 3px}
.sr{display:flex;align-items:center;justify-content:space-between;gap:1px;width:100%}
.sr b{text-align:center;flex:1;min-width:0}
.sr small{font-size:9px;font-weight:500;opacity:.7;margin-left:1px}
button{all:unset;cursor:pointer;width:20px;height:20px;border-radius:6px;text-align:center;line-height:20px;font-size:14px;
  background:rgba(127,127,127,.14);color:var(--primary-text-color);flex:none}
button:active{background:var(--primary-color);color:var(--text-primary-color)}
.sl{display:flex;align-items:center;gap:8px;cursor:default;padding:3px 10px}
.sl ha-icon{--mdc-icon-size:18px;color:var(--secondary-text-color);flex:none}
.sl .l{flex:none}
.rw{position:relative;flex:1;display:flex;align-items:center}
input[type=range]{width:100%;margin:0;accent-color:var(--primary-color);height:22px;cursor:pointer}
.mk{position:absolute;top:2px;width:2px;height:18px;background:${C.orange};border-radius:1px;pointer-events:none;transform:translateX(-1px)}
.sv{min-width:36px;text-align:right}
.bar{position:relative;height:4px;border-radius:2px;background:rgba(127,127,127,.15);margin:0 6px 2px;overflow:visible}
.bar .base{position:absolute;left:0;top:0;height:100%;border-radius:2px;background:rgba(127,127,127,.35)}
.bar .fill{position:absolute;top:0;height:100%;border-radius:2px}
.bar .tg{position:absolute;top:-2px;width:2px;height:8px;background:var(--primary-text-color);opacity:.6;transform:translateX(-1px)}
.al{display:flex;align-items:center;gap:6px;font-size:12px;color:${C.red};padding:2px 8px}
.al ha-icon{--mdc-icon-size:16px}
.cp{display:flex;flex-direction:column;gap:5px;padding:7px 10px}
.cr{display:flex;align-items:center;gap:8px;min-width:0}
.cp ha-icon{--mdc-icon-size:22px;flex:none}
.cst{flex:none;max-width:40%}
.ch{flex:1;min-width:0;display:flex;align-items:center;justify-content:flex-end;font-size:13px;font-weight:600;font-variant-numeric:tabular-nums;
  white-space:nowrap;overflow:hidden;color:var(--secondary-text-color)}
.cp .bar{margin:0}
.compact{padding:0}
.compact .cp{border:none;background:none}
.empty{padding:16px;color:var(--secondary-text-color)}
@container (max-width: 440px){.sr b{font-size:12px}.sr small{font-size:8px;margin-left:0}.ar{margin:0 1px}.ch{font-size:12px}}
@container (max-width: 400px){.t{padding:4px 5px;gap:4px}.t ha-icon{--mdc-icon-size:20px}.sr small.lu{display:none}.st{padding:3px 2px}.sr{gap:0}.sr button{width:18px;height:18px;line-height:18px;font-size:13px;border-radius:5px}.l{letter-spacing:.2px}}
@container (max-width: 360px){.v{font-size:11px}.sr b{font-size:11px}.t{gap:3px;padding:4px}.t ha-icon{--mdc-icon-size:18px}.ch{font-size:11px}.cp ha-icon{--mdc-icon-size:18px}}
@container (max-width: 350px){.t ha-icon{display:none}}
@container (max-width: 330px){.sr small{display:none}.t{padding:4px 3px}.v{font-size:10px}.sl .l{display:none}.ce,.ce+.ar{display:none}}
`;

if (!customElements.get(CARD)) customElements.define(CARD, EveusCard);
window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === CARD)) {
  window.customCards.push({ type: CARD, name: "Eveus EV Charger", description: "Compact Eveus charger card: compact / status / control / full", preview: true });
}
