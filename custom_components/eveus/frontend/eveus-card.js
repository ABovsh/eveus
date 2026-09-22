// Eveus card: one frame of sections the user picks, hides and reorders in the editor.
// Dashboards saved with the old four-layout card (`layout:`) open as the matching sections.
const currentNumber = (entity) => {
  const raw = entity?.state;
  if (typeof raw !== 'string' || !raw.trim()) return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
};
const amps = (value) => value === null ? '—' : String(Number(value.toFixed(1)));
// Mirrors custom_components/eveus/safety.py TEMPERATURE_HIGH_C / LEAKAGE_HIGH_MA — the
// charger's own firmware fault trip points. No live attribute exposes these, so this UI
// threshold is duplicated here and must be kept in sync if the firmware limits change.
const SAFETY_TEMP_HIGH_C = 80;
const SAFETY_LEAK_HIGH_MA = 30;
// The integration's own Connection Quality (HA→charger poll success %), with its Poor/Critical
// boundary: a poor connection also means the rest of the row may be stale. Kept out of the
// card-wide safety tint below — a connectivity hiccup is not an electrical hazard.
const SAFETY_CONN_QUALITY_BAD_PCT = 60;
// The integration's Excellent/Good/Fair/Poor/Critical brackets (95/80/60/30) as MDI wifi icons.
const connectionIcon = (pct) => {
  if (pct === null) return 'mdi:wifi-strength-outline';
  if (pct > 95) return 'mdi:wifi-strength-4';
  if (pct > 80) return 'mdi:wifi-strength-3';
  if (pct > 60) return 'mdi:wifi-strength-2';
  if (pct > 30) return 'mdi:wifi-strength-1';
  return 'mdi:wifi-strength-alert-outline';
};
// The IEC 60417-5017 protective-earth symbol (stem + three descending bars) has no MDI
// equivalent — checked live against this exact HA frontend: `mdi:ground`, `mdi:earth-ground`,
// `mdi:ground-fault` and `mdi:transformer` all resolve to nothing. Drawn inline instead of
// approximating with an unrelated icon, so it reads unambiguously as "grounding" at a glance.
const GROUND_SVG = `<svg class="safety-ground-icon" viewBox="0 0 24 24" aria-hidden="true">
  <rect x="10.8" y="2.5" width="2.4" height="8.5" rx="1.2"/>
  <rect x="3.5" y="11" width="17" height="2.6" rx="1.3"/>
  <rect x="6.5" y="15.3" width="11" height="2.6" rx="1.3"/>
  <rect x="9.3" y="19.6" width="5.4" height="2.6" rx="1.3"/>
</svg>`;
// The 4.24.0 card's road sign: red octagon, white rim, STOP lettering (legible at the Actions size).
const STOP_SVG = `<svg class="act-stop-icon" viewBox="0 0 24 24" aria-hidden="true"><polygon points="7,1 17,1 23,7 23,17 17,23 7,23 1,17 1,7" fill="#e74c3c"/>`
  + `<polygon points="7.6,2.4 16.4,2.4 21.6,7.6 21.6,16.4 16.4,21.6 7.6,21.6 2.4,16.4 2.4,7.6" fill="none" stroke="#fff" stroke-width="1"/>`
  + `<text x="12" y="14.6" text-anchor="middle" font-size="6.4" font-weight="800" font-family="Arial,sans-serif" textLength="15" lengthAdjust="spacingAndGlyphs" fill="#fff">STOP</text></svg>`;
// Actions: the same three switches the 4.24.0 card offers, with its icons.
// Unlimited (limit_disable_all) belongs to Limits, not here.
const STATUS_BUTTONS = [
  {key: 'connect_to_ocpp', label: 'ocpp', icon: '<ha-icon icon="mdi:cloud-sync"></ha-icon>', title: 'ocppTitle'},
  {key: 'one_charge', label: 'one', icon: '<ha-icon icon="mdi:lightning-bolt-circle"></ha-icon>', title: 'oneTitle'},
  {key: 'stop_charging', label: 'stop', icon: STOP_SVG, title: 'stopTitle'},
];
// Card words. English is the source; Ukrainian follows the integration's own uk.json names
// (заряджання, Одноразове заряджання, Лічильник A, SOC) and the classic card's short labels.
const I18N = {
  en: {
    loading: 'Loading…', details: 'tap for details',
    none: {status: 'No Eveus status found', actions: 'No Eveus switches found', current: 'No Eveus current control found',
      limits: 'No Eveus limit controls found', basic_info: 'No Eveus readings found', session: 'No Eveus session readings found',
      history: 'No Eveus counters found', adaptive: 'No Eveus adaptive mode found', safety: 'No Eveus safety telemetry found'},
    aria: {status: 'Status', actions: 'Actions', battery: 'Battery charge', controls: 'Advanced controls', meter: 'Basic information',
      current: 'Charging current', adaptive: 'Adaptive charging', session: 'Session', limits: 'Charging limits', history: 'History', safety: 'Safety'},
    offline: 'Offline', stopped: 'Stopped', error: 'Error', stateTitle: 'Charger state',
    ago: (m) => m < 60 ? `${m}m ago` : `${Math.floor(m / 60)}h ago`,
    ocpp: 'OCPP', ocppTitle: 'Connect to OCPP', one: 'One charge', oneTitle: 'One charge: ignore limits for this session',
    stop: 'Stop', stopTitle: 'Stop charging', sure: 'Sure?', tapAgain: 'Tap again', tapResume: 'Tap to resume', tapStop: 'Tap to stop', on: 'On', off: 'Off',
    battery: 'Battery', est: 'est.', inBattery: 'in battery', toTarget: 'To target', target: 'Target', finish: 'Finish',
    finishTitle: 'Estimated finish time', reached: 'Target reached', toGo: 'to go',
    batteryTitle: 'Estimated state of charge: session start → now. Hold: set Initial SOC',
    targetTitle: 'To target: time left, energy and cost to go, finish time. Hold: set Target SOC',
    initial: 'Initial', initialTitle: 'Battery level when the session started', targetSocTitle: 'Target state of charge',
    capacity: 'Capacity', capacityTitle: 'Usable battery capacity',
    loss: 'Loss', lossTitle: 'Charging losses: share of delivered energy that does not reach the battery',
    voltage: 'Voltage', power: 'Power', current: 'Current',
    actual: 'Actual current', requested: 'Requested current', requestedCharging: 'Requested charging current',
    confirmIncrease: 'Confirm increase to', setFailed: 'Could not set current',
    adaptive: 'Adaptive charging', adaptiveMode: 'Adaptive mode', slowBelow: 'Slow down below',
    slowTitle: 'Voltage mode: reduce current below this voltage', threshold: 'Undervoltage threshold',
    cap: 'cap', capped: 'Current capped at', capTitle: 'Current cap chosen by adaptive mode', modes: {},
    session: 'Session', sessionEnergy: 'Session energy', sessionCost: 'Session cost', sessionTime: 'Session duration',
    limits: 'Limits', disableAll: 'Disable all', soc: 'SOC', energy: 'Energy', time: 'Time', cost: 'Cost', limit: 'limit',
    set: 'Set', decrease: 'Decrease', increase: 'Increase',
    total: 'Total', allTime: 'all time', counterA: 'Counter A', counterB: 'Counter B', reset: 'Reset',
    resetQ: (label) => `Reset ${label}?`, toZero: 'will be set to 0', cancel: 'Cancel',
    safety: 'Safety', box: 'Box temperature', plug: 'Plug temperature', groundProt: 'Ground protection',
    groundTitle: 'Ground: tap to arm/disarm protection', leak: 'Leakage current', conn: 'Connection quality', ok: 'OK', bad: 'Bad',
    // The integration's "5h 30m": English keeps it as text, compact.
    duration: (text) => text.replace(/\s+/g, ''), sessionDuration: (text) => text,
    states: {},
  },
  uk: {
    loading: 'Завантаження…', details: 'натисніть, щоб побачити деталі',
    none: {status: 'Не знайдено стану Eveus', actions: 'Не знайдено перемикачів Eveus', current: 'Не знайдено керування струмом Eveus',
      limits: 'Не знайдено лімітів Eveus', basic_info: 'Не знайдено показників Eveus', session: 'Не знайдено даних сесії Eveus',
      history: 'Не знайдено лічильників Eveus', adaptive: 'Не знайдено адаптивного режиму Eveus', safety: 'Не знайдено даних безпеки Eveus'},
    aria: {status: 'Стан', actions: 'Кнопки', battery: 'Заряд батареї', controls: 'Налаштування SOC', meter: 'Показники',
      current: 'Струм заряджання', adaptive: 'Адаптивне заряджання', session: 'Сесія', limits: 'Ліміти заряджання', history: 'Лічильники', safety: 'Безпека'},
    offline: "Немає зв'язку", stopped: 'Зупинено', error: 'Помилка', stateTitle: 'Стан станції',
    ago: (m) => m < 60 ? `${m} хв тому` : `${Math.floor(m / 60)} год тому`,
    ocpp: 'OCPP', ocppTitle: 'Підключення до OCPP', one: 'Одноразове', oneTitle: 'Одноразове заряджання: без лімітів для цієї сесії',
    stop: 'Стоп', stopTitle: 'Зупинити заряджання', sure: 'Точно?', tapAgain: 'Ще раз', tapResume: 'Відновити', tapStop: 'Зупинити', on: 'Увімк', off: 'Вимк',
    battery: 'Батарея', est: 'оцін.', inBattery: 'у батареї', toTarget: 'До цілі', target: 'Ціль', finish: 'Завершення',
    finishTitle: 'Орієнтовний час завершення', reached: 'Ціль досягнуто', toGo: 'залишилось',
    batteryTitle: 'Орієнтовний рівень заряду: початок сесії → зараз. Утримання: початковий SOC',
    targetTitle: 'До цілі: час, енергія й вартість, що залишились, час завершення. Утримання: цільовий SOC',
    initial: 'Початковий', initialTitle: 'Рівень заряду батареї на початку сесії', targetSocTitle: 'Цільовий рівень заряду',
    capacity: 'Ємність', capacityTitle: 'Корисна ємність батареї',
    loss: 'Втрати', lossTitle: 'Втрати заряджання: частка енергії, що не потрапляє в батарею',
    voltage: 'Напруга', power: 'Потужність', current: 'Струм',
    actual: 'Фактичний струм', requested: 'Заданий струм', requestedCharging: 'Заданий струм заряджання',
    confirmIncrease: 'Підтвердити збільшення до', setFailed: 'Не вдалося встановити струм',
    adaptive: 'Адаптивне заряджання', adaptiveMode: 'Адаптивний режим', slowBelow: 'Напруга нижче',
    slowTitle: 'Режим «Напруга»: зменшувати струм, коли напруга нижча за це значення', threshold: 'Поріг зниження напруги',
    cap: 'ліміт', capped: 'Струм обмежено до', capTitle: 'Ліміт струму, який обрав адаптивний режим',
    modes: {Off: 'Вимк', Voltage: 'Напруга', Auto: 'Авто', Power: 'Потужність'},
    session: 'Сесія', sessionEnergy: 'Енергія сесії', sessionCost: 'Вартість сесії', sessionTime: 'Час сесії',
    limits: 'Ліміти', disableAll: 'Без лімітів', soc: 'SOC', energy: 'Енергія', time: 'Час', cost: 'Вартість', limit: 'ліміт',
    set: 'Встановити', decrease: 'Зменшити', increase: 'Збільшити',
    total: 'Загалом', allTime: 'за весь час', counterA: 'Лічильник A', counterB: 'Лічильник B', reset: 'Скинути',
    resetQ: (label) => `Скинути ${label[0].toLowerCase()}${label.slice(1)}?`, toZero: 'буде обнулено', cancel: 'Скасувати',
    safety: 'Безпека', box: 'Температура корпусу', plug: 'Температура конектора', groundProt: 'Захист заземлення',
    groundTitle: 'Заземлення: натисніть, щоб увімкнути чи вимкнути захист', leak: 'Струм витоку', conn: "Якість зв'язку", ok: 'Є', bad: 'Немає',
    // "1d 02h 05m" / "5h 30m" / "45m" → numbers with small Ukrainian units.
    duration: (text) => text.trim().split(/\s+/).map((part) => {
      const m = /^(\d+)([dhm])$/.exec(part);
      return m ? `${m[1]}<small>${{d: 'д', h: 'год', m: 'хв'}[m[2]]}</small>` : part;
    }).join(' '),
    sessionDuration: (text) => I18N.uk.duration(text),
    // The integration's charger state, fault, substate and not-charging reason values.
    states: {
      'Startup': 'Запуск', 'System Test': 'Самотестування', 'Standby': 'Очікування', 'Connected': 'Підключено',
      'Charging': 'Заряджання', 'Charge Complete': 'Заряджання завершено', 'Paused': 'Пауза', 'Error': 'Помилка', 'Unknown': 'Невідомо',
      'No Error': 'Без помилок', 'Grounding Error': 'Помилка заземлення', 'Current Leak High': 'Витік струму: високий поріг',
      'Relay Error': 'Помилка реле', 'Current Leak Low': 'Витік струму: низький поріг', 'Box Overheat': 'Перегрів корпусу',
      'Plug Overheat': 'Перегрів конектора', 'Pilot Error': 'Помилка пілот-сигналу', 'Low Voltage': 'Низька напруга',
      'Diode Error': 'Помилка діода', 'Overcurrent': 'Перевищення струму', 'Interface Timeout': 'Тайм-аут інтерфейсу',
      'Software Failure': 'Програмний збій', 'GFCI Test Failure': 'Збій тесту ПЗВ', 'High Voltage': 'Висока напруга',
      'No Limits': 'Без лімітів', 'Limited by User': 'Обмежено користувачем', 'Energy Limit': 'Ліміт енергії',
      'Time Limit': 'Ліміт часу', 'Cost Limit': 'Ліміт вартості', 'Schedule 1 Limit': 'Розклад 1: ліміт',
      'Schedule 1 Energy Limit': 'Розклад 1: ліміт енергії', 'Schedule 2 Limit': 'Розклад 2: ліміт',
      'Schedule 2 Energy Limit': 'Розклад 2: ліміт енергії', 'Waiting for Activation': 'Очікує активації',
      'Paused by Adaptive Mode': 'Пауза адаптивного режиму', 'Starting Up': 'Запуск', 'Cable Not Connected': 'Кабель не підключено',
      'Waiting for Car': 'Очікує авто', 'Stopped by User': 'Зупинено користувачем', 'Energy Limit Reached': 'Досягнуто ліміту енергії',
      'Time Limit Reached': 'Досягнуто ліміту часу', 'Cost Limit Reached': 'Досягнуто ліміту вартості',
      'Waiting for Schedule': 'Очікує розкладу', 'Schedule Energy Limit Reached': 'Досягнуто ліміту енергії розкладу',
      'Controlled by OCPP': 'Керується через OCPP',
    },
  },
};
const langOf = (config, hass) => {
  const l = config?.language && config.language !== 'auto' ? config.language : hass?.locale?.language || 'en';
  return l.startsWith('uk') ? 'uk' : 'en';
};
// Default order reads top to bottom: what the charger is doing and its switches, the battery and
// its settings, live power with the current control and what adapts it, this session and its
// limits, counters, safety.
const SECTIONS = ['status', 'actions', 'advanced_info', 'advanced_controls', 'basic_info', 'current', 'adaptive', 'session', 'limits', 'history', 'safety'];
// The 4.24.0 card's `layout` values, as the sections that show the same things.
// Advanced-only sections render nothing in Basic mode, so one list serves both modes.
const LEGACY_LAYOUTS = {
  compact: ['status', 'advanced_info', 'basic_info', 'session'],
  status: ['status', 'advanced_info', 'basic_info', 'session', 'safety'],
  control: ['status', 'actions', 'advanced_info', 'basic_info', 'current', 'session'],
  full: SECTIONS,
};
const sectionsOf = (config) => config.sections ?? LEGACY_LAYOUTS[config.layout] ?? SECTIONS;
// Editor words: what each section shows, in words a first-time user recognises.
const EDITOR_I18N = {
  en: {
    names: {
      status: ['Status', 'charger state'], actions: ['Buttons', 'OCPP · One charge · Stop'],
      advanced_info: ['Battery SOC', 'advanced mode'], advanced_controls: ['SOC settings', 'advanced mode'],
      basic_info: ['Meter', 'voltage · power · current'], current: ['Current slider', ''],
      adaptive: ['Adaptive charging', ''], session: ['Session', 'energy · cost · time'],
      limits: ['Limits', 'energy · time · cost'], history: ['Counters', 'total · A · B'], safety: ['Safety', ''],
    },
    head: 'Sections', reset: 'Default order', advOnly: 'advanced mode only', up: 'Move up', down: 'Move down',
    device: 'Charger', mode: 'Mode', modeHelp: 'Empty follows the integration', language: 'Language',
    advanced: 'Advanced', basic: 'Basic', languages: {auto: 'Home Assistant language', uk: 'Українська', en: 'English'},
  },
  uk: {
    names: {
      status: ['Стан', 'стан станції'], actions: ['Кнопки', 'OCPP · Одноразове · Стоп'],
      advanced_info: ['Заряд батареї', 'розширений режим'], advanced_controls: ['Налаштування SOC', 'розширений режим'],
      basic_info: ['Показники', 'напруга · потужність · струм'], current: ['Повзунок струму', ''],
      adaptive: ['Адаптивне заряджання', ''], session: ['Сесія', 'енергія · вартість · час'],
      limits: ['Ліміти', 'енергія · час · вартість'], history: ['Лічильники', 'загалом · A · B'], safety: ['Безпека', ''],
    },
    head: 'Розділи', reset: 'Типовий порядок', advOnly: 'лише розширений режим', up: 'Вище', down: 'Нижче',
    device: 'Станція', mode: 'Режим', modeHelp: 'Порожнє поле — як в інтеграції', language: 'Мова картки',
    advanced: 'Розширений', basic: 'Базовий', languages: {auto: 'Мова Home Assistant', uk: 'Українська', en: 'English'},
  },
};
const CURRENCY = {UAH: '₴', EUR: '€', USD: '$', GBP: '£', PLN: 'zł', CZK: 'Kč'};
const money = (value, rawUnit = 'UAH') => {
  const u = CURRENCY[rawUnit] || rawUnit;
  // After the number, like kWh and the way ₴ is written in Ukraine.
  return `${value}<small>${u}</small>`;
};
// "5h 30m" → "5h30m"; the integration's non-duration texts ("Not charging") carry no digit.
const duration = (text) => typeof text === 'string' && /\d/.test(text) ? text.replace(/\s+/g, '') : null;
// The existing card's working band for mains voltage: outside it the charger itself struggles.
const VOLTAGE_LOW_V = 205, VOLTAGE_HIGH_V = 253;

class EveusCard extends HTMLElement {
  static getStubConfig() { return {sections: [...SECTIONS]}; }
  static getConfigElement() { return document.createElement('eveus-card-editor'); }
  setConfig(config) {
    const sections = sectionsOf(config);
    if (!Array.isArray(sections) || sections.some((section) => !SECTIONS.includes(section))) {
      throw new Error(`Available sections: ${SECTIONS.join(', ')}`);
    }
    this._reset();
    this._config = {...config, sections: [...new Set(sections)]};
    this._ids = null;
    this._resolved = false;
    this._generation = (this._generation || 0) + 1;
    this._resolving = false;
    this._limitPending = {};
    this._limitTimers = {};
    this._editingLimit = null;
    if (!this.shadowRoot) {
      this.attachShadow({mode: 'open'});
      this.shadowRoot.addEventListener('input', (e) => this._onSlide(e, false));
      this.shadowRoot.addEventListener('change', (e) => {
        if (e.target.dataset?.limitEdit) { this._commitLimitEdit(e.target.dataset.limitEdit, e.target.value); return; }
        if (e.target.dataset?.select) { this._selectOption(e.target.dataset.select, e.target.value); return; }
        this._onSlide(e, true);
      });
      this.shadowRoot.addEventListener('keydown', (e) => {
        if (!e.target.dataset?.limitEdit) return;
        if (e.key === 'Enter') { e.preventDefault(); e.target.blur(); }
        else if (e.key === 'Escape') { e.preventDefault(); this._editingLimit = null; this._render(); }
      });
      this.shadowRoot.addEventListener('focusout', (e) => {
        if (e.target.dataset?.limitEdit && this._editingLimit === e.target.dataset.limitEdit) {
          this._editingLimit = null;
          this._render();
        }
      });
      this.shadowRoot.addEventListener('click', (e) => this._onClick(e));
      // The 4.24.0 long press: hold a SOC tile to open the setting behind it.
      this.shadowRoot.addEventListener('pointerdown', (e) => this._holdStart(e));
      for (const ev of ['pointerup', 'pointerleave']) this.shadowRoot.addEventListener(ev, () => this._holdEnd());
      this.shadowRoot.addEventListener('pointermove', (e) => {
        if (this._holdAt && Math.hypot(e.clientX - this._holdAt[0], e.clientY - this._holdAt[1]) > 10) this._holdEnd();
      });
      this.shadowRoot.addEventListener('contextmenu', (e) => { if (e.target.closest?.('[data-hold]')) e.preventDefault(); });
      this.shadowRoot.addEventListener('pointercancel', () => { this._holdEnd(); this._reset(); this._render(); });
      // A dashboard/grid resize (e.g. rotation, editor column change) does not
      // touch hass, so refitting must react to width changes on its own.
      if (typeof ResizeObserver !== 'undefined') {
        this._resizeObserver = new ResizeObserver(() => this._fitLimitValues());
        this._resizeObserver.observe(this);
      }
      // The real HA frontend swaps a webfont in after our first paint (fit runs
      // against a fallback font, then Roboto lands wider with no resize/DOM
      // event); refit once fonts finish loading so that swap can't leave stale,
      // overflowing text on screen.
      if (typeof document !== 'undefined' && document.fonts) {
        document.fonts.ready.then(() => this._fitLimitValues());
      }
    }
    if (this._hass) this.hass = this._hass;
  }
  _onClick(e) {
    // The click that ends a long press belongs to the press, not to a tap.
    if (this._held) { this._held = false; return; }
    const step = e.target.closest('[data-limit-step]');
    const toggle = e.target.closest('[data-limit-toggle]');
    const editTrigger = e.target.closest('[data-limit-edit-trigger]');
    const moreInfo = e.target.closest('[data-more-info]');
    const statusToggle = e.target.closest('[data-status-toggle]');
    const reset = e.target.closest('[data-reset],[data-reset-confirm],[data-reset-cancel]');
    if (reset) {
      if (reset.dataset.reset) this._resetCounter(reset.dataset.reset);
      else if (reset.dataset.resetConfirm) this._resetCounter(reset.dataset.resetConfirm, true);
      else this._cancelReset();
    } else if (statusToggle) this._statusToggle(statusToggle.dataset.statusToggle);
    else if (step) this._stepLimit(step.dataset.limitStep, Number(step.dataset.dir));
    else if (toggle) this._toggleLimit(toggle.dataset.limitToggle);
    else if (editTrigger) this._startLimitEdit(editTrigger.dataset.limitEditTrigger);
    else if (moreInfo) this._moreInfo(moreInfo.dataset.moreInfo);
    else if (e.target.closest('[data-confirm]')) this._confirm();
  }
  _holdStart(e) {
    this._held = false;
    clearTimeout(this._holdTimer);
    const el = e.target.closest?.('[data-hold]');
    if (!el) return;
    this._holdAt = [e.clientX, e.clientY];
    this._holdTimer = setTimeout(() => { this._held = true; this._holdAt = null; this._moreInfo(el.dataset.hold); }, 500);
  }
  _holdEnd() {
    clearTimeout(this._holdTimer);
    this._holdAt = null;
  }
  set hass(hass) {
    this._hass = hass;
    if (!this._resolved && !this._resolving) this._resolve();
    for (const [key, pending] of Object.entries(this._limitPending || {})) {
      const entity = this._state(key);
      const actual = typeof pending === 'boolean' ? entity?.state === 'on' : typeof pending === 'string' ? entity?.state : currentNumber(entity);
      if (actual === pending) {
        clearTimeout(this._limitTimers[key]);
        delete this._limitPending[key];
      }
    }
    // Bounds may change without a new state value. They participate in validation
    // and render scheduling, including a change received during a drag.
    if (this._draft !== null && (!this._canControl || !this._valid(this._draft))) this._reset();
    if (this._sent && currentNumber(this._state('charging_current')) === this._draft) this._reset();
    if (this._editingLimit) return;
    if (!this._sliding) this._render();
    else this._updateActual();
  }
  connectedCallback() { if (this._hass) this.hass = this._hass; }
  disconnectedCallback() {
    this._reset();
    clearTimeout(this._statusTimer);
    this._statusAsk = null;
    clearTimeout(this._resetTimer);
    this._resetAsk = null;
    this._holdEnd();
    Object.values(this._limitTimers || {}).forEach(clearTimeout);
    this._limitPending = {};
    this._editingLimit = null;
    this._resizeObserver?.disconnect();
  }
  getCardSize() { return 1; }
  getGridOptions() { return {columns: 12, min_columns: 6, rows: 'auto'}; }
  async _resolve() {
    const generation = this._generation;
    this._resolving = true;
    const msg = {type: 'eveus/card_entities'};
    if (this._config.device_id) msg.device_id = this._config.device_id;
    try {
      const result = await this._hass.callWS(msg);
      if (generation !== this._generation) return;
      this._ids = result.entities;
    } catch {
      if (generation !== this._generation) return;
      this._ids = null;
    }
    if (generation !== this._generation) return;
    this._resolved = true;
    this._resolving = false;
    this.hass = this._hass;
  }
  _state(key) { return this._hass?.states[this._ids?.[key]]; }
  get _online() {
    const state = this._state('state')?.state;
    return !!state && !['unknown', 'unavailable'].includes(state);
  }
  get _bounds() {
    const a = this._state('charging_current')?.attributes;
    if (!a || ![a.min, a.max, a.step].every(Number.isFinite) || a.min < 0 || a.max <= a.min || a.step <= 0) return null;
    return {min: a.min, max: a.max, step: a.step};
  }
  get _canControl() {
    return this._online && this._bounds !== null && currentNumber(this._state('charging_current')) !== null;
  }
  _valid(value) {
    const b = this._bounds;
    if (!b || !Number.isFinite(value) || value < b.min || value > b.max) return false;
    const steps = (value - b.min) / b.step;
    return Math.abs(steps - Math.round(steps)) < 1e-6;
  }
  _reset() {
    clearTimeout(this._timer);
    this._draft = null;
    this._asking = false;
    this._sliding = false;
    this._sent = false;
    this._error = false;
    this._request = (this._request || 0) + 1;
  }
  _expire() {
    clearTimeout(this._timer);
    this._timer = setTimeout(() => { this._reset(); this._render(); }, 4000);
  }
  _onSlide(e, commit) {
    if (e.target.dataset?.slide !== 'current') return;
    const value = Number(e.target.value);
    if (!this._canControl || this._sent || !this._valid(value)) {
      if (!this._sent) this._reset();
      this._render();
      return;
    }
    clearTimeout(this._timer);
    this._error = false;
    this._draft = value;
    this._sliding = !commit;
    const out = this.shadowRoot.querySelector('.cv');
    if (out) out.textContent = `${amps(value)} A`;
    const b = this._bounds;
    if (b && e.target.style?.setProperty) e.target.style.setProperty('--fill', `${100 * (value - b.min) / (b.max - b.min)}%`);
    if (!commit) return;
    const current = currentNumber(this._state('charging_current'));
    if (value > current) {
      this._asking = true;
      this._expire();
      this._render();
    } else if (value < current) {
      this._asking = false;
      this._send();
    } else { this._reset(); this._render(); }
  }
  async _confirm() {
    if (!this._asking || this._sliding || this._sent) return;
    this._asking = false;
    await this._send();
  }
  async _send() {
    if (!this._canControl || !this._valid(this._draft)) { this._reset(); this._render(); return; }
    const value = this._draft, request = ++this._request;
    clearTimeout(this._timer);
    this._sent = true;
    this._render();
    // Finite feedback even when HA never answers. Each response belongs to its
    // own request, so a late failure cannot undo a newer interaction.
    this._expire();
    try {
      await this._hass.callService('number', 'set_value', {entity_id: this._ids.charging_current, value});
    } catch {
      if (request !== this._request) return;
      this._reset();
      this._error = true;
      this._render();
    }
  }
  _actual() { return this._online ? currentNumber(this._state('current')) : null; }
  _actualMarker() {
    const bounds = this._bounds, actual = this._actual();
    // Only a live charge draws the tick; a 0 A reading at the scale's left edge just looked like noise.
    if (!bounds || actual === null || actual <= 0 || !this._charging) return '';
    const position = Math.max(0, Math.min(100, 100 * (actual - bounds.min) / (bounds.max - bounds.min)));
    const left = actual === 0 ? '0px' : `calc(${position}% + ${7 - 14 * position / 100}px)`;
    return `<span class="mk" style="left:${left}" title="${this._t.actual} ${amps(actual)} A"></span>`;
  }
  _updateActual() {
    const markers = this.shadowRoot.querySelector('.markers');
    if (markers) markers.innerHTML = this._actualMarker();
  }
  _limitOn(key) {
    const pending = this._limitPending?.[key];
    return typeof pending === 'boolean' ? pending : this._state(key)?.state === 'on';
  }
  _limitValue(key) {
    const pending = this._limitPending?.[key];
    return typeof pending === 'number' ? pending : currentNumber(this._state(key));
  }
  _setLimitPending(key, value) {
    clearTimeout(this._limitTimers[key]);
    this._limitPending[key] = value;
    this._limitTimers[key] = setTimeout(() => {
      delete this._limitPending[key];
      this._render();
    }, 4000);
  }
  // Standard HA custom-card pattern: a bubbling, shadow-boundary-crossing event that the
  // frontend's global more-info-dialog listener picks up. No entity_id resolved (key not in
  // `_ids`) is a no-op rather than opening a dialog for `undefined`.
  _moreInfo(key) {
    const entityId = this._ids?.[key];
    if (!entityId) return;
    this.dispatchEvent(new CustomEvent('hass-more-info', {detail: {entityId}, bubbles: true, composed: true}));
  }
  async _toggleLimit(key) {
    const entity = this._state(key);
    if (!this._online || !entity || !['on', 'off'].includes(entity.state)) return;
    const value = !this._limitOn(key);
    this._setLimitPending(key, value);
    this._render();
    try {
      await this._hass.callService('switch', value ? 'turn_on' : 'turn_off', {entity_id: this._ids[key]});
    } catch {
      clearTimeout(this._limitTimers[key]);
      delete this._limitPending[key];
      this._render();
    }
  }
  async _stepLimit(key, direction) {
    const entity = this._state(key), attrs = entity?.attributes;
    const value = this._limitValue(key);
    if (!this._online || value === null || !attrs || ![attrs.min, attrs.max, attrs.step].every(Number.isFinite)) return;
    const next = Math.max(attrs.min, Math.min(attrs.max, value + direction * attrs.step));
    if (next === value) return;
    const rounded = Number(next.toFixed(6));
    this._setLimitPending(key, rounded);
    this._render();
    try {
      await this._hass.callService('number', 'set_value', {entity_id: this._ids[key], value: rounded});
    } catch {
      clearTimeout(this._limitTimers[key]);
      delete this._limitPending[key];
      this._render();
    }
  }
  async _commitLimitEdit(key, raw) {
    this._editingLimit = null;
    const entity = this._state(key), attrs = entity?.attributes;
    if (!this._online || !attrs || ![attrs.min, attrs.max, attrs.step].every(Number.isFinite)) { this._render(); return; }
    const parsed = Number(raw);
    if (!Number.isFinite(parsed)) { this._render(); return; }
    const clamped = Math.max(attrs.min, Math.min(attrs.max, parsed));
    const steps = Math.round((clamped - attrs.min) / attrs.step);
    const rounded = Number((attrs.min + steps * attrs.step).toFixed(6));
    if (rounded === this._limitValue(key)) { this._render(); return; }
    this._setLimitPending(key, rounded);
    this._render();
    try {
      await this._hass.callService('number', 'set_value', {entity_id: this._ids[key], value: rounded});
    } catch {
      clearTimeout(this._limitTimers[key]);
      delete this._limitPending[key];
      this._render();
    }
  }
  _startLimitEdit(key) {
    if (!this._online) return;
    this._editingLimit = key;
    this._render();
  }
  // The stepper + tap-to-edit value shared by Limits, Advanced controls and Adaptive.
  // `readonly` keeps the reading but drops every control (its editor lives elsewhere).
  // `unit: false` leaves the unit to the tile header (see _unit) so a narrow tile keeps a big number.
  _unit(key, fallbackUnit) {
    const rawUnit = this._state(key)?.attributes?.unit_of_measurement || fallbackUnit;
    return CURRENCY[rawUnit] || rawUnit;
  }
  _numberValue({key, label, fallbackUnit, readonly = false, unit: showUnit = true}) {
    const entity = this._state(key), attrs = entity?.attributes || {};
    const value = this._limitValue(key);
    const unit = showUnit ? `<small>${this._unit(key, fallbackUnit)}</small>` : '';
    const disabled = !this._online || value === null || ![attrs.min, attrs.max, attrs.step].every(Number.isFinite);
    const shown = value === null ? '—' : Number(value.toFixed(attrs.step < 1 ? 1 : 0));
    if (readonly) return `<div class="limit-value readonly"><b data-fit>${shown}${unit}</b></div>`;
    const editing = this._editingLimit === key;
    const display = editing
      ? `<input type="number" class="limit-edit-input" data-limit-edit="${key}" value="${value ?? ''}" min="${attrs.min ?? ''}" max="${attrs.max ?? ''}" step="${attrs.step ?? 1}" inputmode="decimal" aria-label="${this._t.set}: ${label}" ${disabled ? 'disabled' : ''}>`
      : `<button class="limit-value-btn" data-limit-edit-trigger="${key}" aria-label="${this._t.set}: ${label}" ${disabled ? 'disabled' : ''}>
        <b data-fit>${shown}${unit}</b>
      </button>`;
    return `<div class="limit-value">
        <button data-limit-step="${key}" data-dir="-1" aria-label="${this._t.decrease}: ${label}" ${disabled ? 'disabled' : ''}>−</button>
        ${display}
        <button data-limit-step="${key}" data-dir="1" aria-label="${this._t.increase}: ${label}" ${disabled ? 'disabled' : ''}>+</button>
      </div>`;
  }
  _limitTile({key, toggle, label, fallbackUnit, readonly = false}) {
    const enabled = this._limitOn(toggle);
    const suspended = this._limitOn('limit_disable_all');
    return `<div class="limit-tile ${enabled && !suspended ? 'active' : ''} ${enabled && suspended ? 'saved' : ''}">
      <button class="limit-toggle" data-limit-toggle="${toggle}" role="switch" aria-checked="${enabled}" aria-label="${label}: ${this._t.limit}" ${!this._online ? 'disabled' : ''}>
        <span data-fit="10.5">${label}<small class="tile-unit">${this._unit(key, fallbackUnit)}</small></span><i></i>
      </button>
      ${this._numberValue({key, label, fallbackUnit, readonly, unit: false})}
    </div>`;
  }
  _limitsSection() {
    const t = this._t;
    if (!this._ids?.limit_disable_all) return `<div class="message">${t.none.limits}</div>`;
    const suspended = this._limitOn('limit_disable_all');
    const items = [];
    // One Target SOC editor per card: when Advanced controls shows it, this tile keeps only the stop switch.
    const targetOwned = this._advanced && this._config.sections.includes('advanced_controls');
    // SOC is an Advanced-mode feature: Basic keeps Energy, Time and Cost only.
    if (this._advanced && this._ids.limit_soc_enabled && this._ids.target_soc) items.push(targetOwned
      ? {key:'target_soc',toggle:'limit_soc_enabled',label:t.soc,fallbackUnit:'%',readonly:true}
      : {key:'target_soc',toggle:'limit_soc_enabled',label:t.soc,fallbackUnit:'%'});
    items.push(
      {key:'limit_energy',toggle:'limit_energy_enabled',label:t.energy,fallbackUnit:'kWh'},
      {key:'limit_time',toggle:'limit_time_enabled',label:t.time,fallbackUnit:'min'},
      {key:'limit_cost',toggle:'limit_cost_enabled',label:t.cost,fallbackUnit:'UAH'},
    );
    return `<section class="limits ${this._online ? '' : 'off'}" aria-label="${t.aria.limits}">
      <div class="limits-head"><span><ha-icon icon="mdi:speedometer"></ha-icon><b>${t.limits}</b></span>
        <button class="disable-all ${suspended ? 'active' : ''}" data-limit-toggle="limit_disable_all" role="switch" aria-checked="${suspended}" ${!this._online ? 'disabled' : ''}>
          <ha-icon icon="mdi:cancel"></ha-icon><span>${t.disableAll}</span><i></i>
        </button>
      </div>
      <div class="limits-grid ${items.length === 4 ? 'four' : 'three'} ${suspended ? 'suspended' : ''}">${items.map((item) => this._limitTile(item)).join('')}</div>
    </section>`;
  }
  // Shared by the section markup and the card-wide alert tint so the two can never disagree
  // about what "bad" means. WiFi is deliberately excluded from `anyBad`/`anyKnown`: it has no
  // firmware safety policy and a weak signal is a monitoring nuisance, not an electrical hazard.
  _safetyReadings() {
    if (!this._ids?.ground_protection) return null;
    const box = currentNumber(this._state('box_temperature'));
    const plug = currentNumber(this._state('plug_temperature'));
    const leak = currentNumber(this._state('leakage_current'));
    const conn = currentNumber(this._state('connection_quality'));
    const groundState = this._state('ground')?.state;
    const groundKnown = groundState === 'Connected' || groundState === 'Not Connected';
    const groundOk = groundState === 'Connected';
    const protectionKnown = ['on', 'off'].includes(this._state('ground_protection')?.state);
    const protectionOn = this._limitOn('ground_protection');
    const tempCls = (value) => value === null ? '' : value >= SAFETY_TEMP_HIGH_C ? 'bad' : 'good';
    const leakCls = leak === null ? '' : leak >= SAFETY_LEAK_HIGH_MA ? 'bad' : 'good';
    const connCls = conn === null ? '' : conn <= SAFETY_CONN_QUALITY_BAD_PCT ? 'bad' : 'good';
    const groundCls = groundKnown ? (groundOk ? 'good' : 'bad') : '';
    const protectionCls = protectionKnown ? (protectionOn ? 'good' : 'bad') : '';
    const anyKnown = box !== null || plug !== null || leak !== null || groundKnown || protectionKnown;
    const anyBad = tempCls(box) === 'bad' || tempCls(plug) === 'bad' || leakCls === 'bad'
      || groundCls === 'bad' || protectionCls === 'bad';
    return {box, plug, leak, conn, groundKnown, groundOk, protectionKnown, protectionOn,
      tempCls, leakCls, connCls, groundCls, protectionCls, anyKnown, anyBad};
  }
  // null (no telemetry / offline) = no tint; 'bad' beats 'good' the moment any one reading trips.
  _safetyStatus() {
    if (!this._online) return null;
    const r = this._safetyReadings();
    if (!r || !r.anyKnown) return null;
    return r.anyBad ? 'bad' : 'good';
  }
  _safetySection() {
    const r = this._safetyReadings();
    const t = this._t, d = t.details;
    if (!r) return `<div class="message">${t.none.safety}</div>`;
    const disabled = !this._online || !r.protectionKnown;
    const status = this._safetyStatus();
    const rowClass = ['sl', 'safety', status ? `safety-${status}` : '', this._online ? '' : 'off'].filter(Boolean).join(' ');
    return `<section class="${rowClass}" aria-label="${t.aria.safety}">
      <ha-icon icon="mdi:shield-check"></ha-icon><span class="label safety-label">${t.safety}</span>
      <div class="safety-row">
        <button class="safety-item" data-more-info="box_temperature" title="${t.box} — ${d}"><ha-icon icon="mdi:ev-station"></ha-icon><span class="safety-value ${r.tempCls(r.box)}">${r.box === null ? '—' : Math.round(r.box)}°</span></button>
        <button class="safety-item" data-more-info="plug_temperature" title="${t.plug} — ${d}"><ha-icon icon="mdi:ev-plug-type2"></ha-icon><span class="safety-value ${r.tempCls(r.plug)}">${r.plug === null ? '—' : Math.round(r.plug)}°</span></button>
        <button class="safety-item safety-ground" data-limit-toggle="ground_protection" role="switch" aria-checked="${r.protectionOn}" aria-label="${t.groundProt}" title="${t.groundTitle}" ${disabled ? 'disabled' : ''}>
          ${GROUND_SVG}<span class="safety-value ${r.groundCls}">${r.groundKnown ? (r.groundOk ? t.ok : t.bad) : '—'}</span><i class="${r.protectionCls}"></i>
        </button>
        <button class="safety-item" data-more-info="leakage_current" title="${t.leak} — ${d}"><ha-icon icon="mdi:current-dc"></ha-icon><span class="safety-value ${r.leakCls}">${r.leak === null ? '—' : Math.round(r.leak)}<small>mA</small></span></button>
        <button class="safety-item" data-more-info="connection_quality" title="${t.conn} — ${d}"><ha-icon icon="${connectionIcon(r.conn)}"></ha-icon><span class="safety-value ${r.connCls}">${r.conn === null ? '—' : Math.round(r.conn)}<small>%</small></span></button>
      </div>
    </section>`;
  }
  get _charging() { return this._state('state')?.state === 'Charging'; }
  get _t() { return I18N[langOf(this._config, this._hass)]; }
  // Advanced mode comes from the integration (the SOC helpers exist); a card may opt down to basic.
  get _advanced() { return this._config.mode !== 'basic' && !!this._ids?.soc_percent; }
  // `fit`: this item absorbs a narrow row by shrinking its own text; its siblings keep their size.
  _infoItem(key, icon, value, cls = '', title = '', fit = false) {
    return `<button class="info-item${fit ? ' grow' : ''}" data-more-info="${key}" title="${title} — ${this._t.details}"><ha-icon icon="${icon}"></ha-icon><span class="${['info-value', cls].filter(Boolean).join(' ')}"${fit ? ' data-fit="15"' : ''}>${value}</span></button>`;
  }
  // The charger's own screen: three big live readings, voltage · power · current.
  _basic_infoSection() {
    const t = this._t;
    if (!this._ids?.power) return `<div class="message">${t.none.basic_info}</div>`;
    const on = this._online;
    const v = on ? currentNumber(this._state('voltage')) : null;
    const w = on ? currentNumber(this._state('power')) : null;
    const a = on ? currentNumber(this._state('current')) : null;
    const vCls = v === null ? '' : v < VOLTAGE_LOW_V || v > VOLTAGE_HIGH_V ? 'bad' : '';
    const item = (key, icon, label, value, cls = '') => this._ids[key] ? `<button class="meter-item" data-more-info="${key}" title="${label} — ${t.details}">
        <span class="meter-head"><ha-icon icon="${icon}"></ha-icon>${label}</span><span class="${['meter-value', cls].filter(Boolean).join(' ')}" data-fit="24">${value}</span></button>` : '';
    return `<section class="${['panel meter', on ? '' : 'off'].filter(Boolean).join(' ')}" aria-label="${t.aria.meter}">
      ${item('voltage', 'mdi:sine-wave', t.voltage, v === null ? '—' : `${Math.round(v)}<small>V</small>`, vCls)}
      ${item('power', 'mdi:flash', t.power, w === null ? '—' : `${(w / 1000).toFixed(1)}<small>kW</small>`)}
      ${item('current', 'mdi:current-ac', t.current, a === null ? '—' : `${amps(a)}<small>A</small>`)}
    </section>`;
  }
  // Energy, cost and duration spread over the row, each its own More Info.
  _sessionSection() {
    const t = this._t;
    if (!this._ids?.session_energy) return `<div class="message">${t.none.session}</div>`;
    const on = this._online;
    const e = on ? currentNumber(this._state('session_energy')) : null;
    const c = on ? currentNumber(this._state('session_cost')) : null;
    const raw = on ? this._state('session_time')?.state : null;
    const time = typeof raw === 'string' && /\d/.test(raw) ? t.sessionDuration(raw.replace(/\s+/g, ' ').trim()) : null;
    const items = [
      this._infoItem('session_energy', 'mdi:lightning-bolt', e === null ? '—' : `${Number(e.toFixed(e < 100 ? 1 : 0))}<small>kWh</small>`, '', t.sessionEnergy, true),
      this._ids.session_cost ? this._infoItem('session_cost', 'mdi:cash', c === null ? '—' : money(c < 1000 ? c.toFixed(2) : Math.round(c), this._state('session_cost')?.attributes?.unit_of_measurement), '', t.sessionCost, true) : '',
      this._ids.session_time ? this._infoItem('session_time', 'mdi:timer-outline', time ?? '—', '', t.sessionTime, true) : '',
    ].join('');
    return `<section class="${['sl', 'session', on ? '' : 'off'].filter(Boolean).join(' ')}" aria-label="${t.aria.session}">
      <ha-icon icon="mdi:counter"></ha-icon><span class="label">${t.session}</span><div class="info-row session-row">${items}</div>
    </section>`;
  }
  // The 4.24.0 card's battery pair: where the charge is (start → now) and how far to go
  // (target → time left), over one full-width bar with the session's start, progress and target.
  get _socColor() {
    const soc = currentNumber(this._state('soc_percent')), target = currentNumber(this._state('target_soc'));
    if (!this._online) return 'off';
    if (soc !== null && target !== null && soc >= target) return 'reached';
    if (!this._charging || soc === null) return 'idle';
    return soc < 40 ? 'low' : soc < 75 ? 'mid' : 'high';
  }
  _advanced_infoSection() {
    if (!this._advanced) return '';
    const t = this._t;
    const soc = currentNumber(this._state('soc_percent'));
    const initial = currentNumber(this._state('initial_soc'));
    const target = currentNumber(this._state('target_soc'));
    const color = this._socColor, reached = color === 'reached';
    const etaRaw = this._online && this._charging ? duration(this._state('time_to_target_soc')?.state) : null;
    const eta = etaRaw && t.duration(this._state('time_to_target_soc').state);
    const finishRaw = this._state('charging_finish_time')?.state;
    const finishDate = eta && finishRaw && !Number.isNaN(Date.parse(finishRaw)) ? new Date(finishRaw) : null;
    const finish = finishDate ? finishDate.toLocaleTimeString(this._hass.locale?.language, {hour: '2-digit', minute: '2-digit'}) : '';
    const step = soc === null ? null : Math.min(100, Math.max(0, Math.round(soc / 20) * 20));
    const icon = step === null ? 'mdi:battery-unknown' : `mdi:battery${this._charging ? '-charging' : ''}${step >= 100 ? (this._charging ? '-100' : '') : step < 20 ? '-outline' : `-${step}`}`;
    const pc = (v) => v === null ? '—' : `${Math.round(v)}<small>%</small>`;
    const clamp = (v) => Math.max(0, Math.min(100, Math.round(v)));
    const arrow = '<i class="soc-arrow">→</i>';
    const stored = this._online ? currentNumber(this._state('soc_energy')) : null;
    const et = currentNumber(this._state('energy_to_target_soc')), ct = currentNumber(this._state('cost_to_target_soc'));
    const toGo = reached ? [] : [
      et === null ? '' : `${Number(et.toFixed(et < 100 ? 1 : 0))}<small>kWh</small>`,
      ct === null ? '' : money(Math.round(ct), this._state('cost_to_target_soc')?.attributes?.unit_of_measurement),
    ].filter(Boolean);
    const goalBig = reached ? `<b data-fit="22">${t.reached}</b>`
      : eta ? `<span class="soc-from">${pc(target)}</span>${arrow}<b data-fit="22">${eta}</b>`
      : `<span class="soc-from">${pc(soc)}</span>${arrow}<b data-fit="22">${pc(target)}</b>`;
    const goalSub = toGo.join('<i class="info-sep">·</i>');
    const base = this._online && soc !== null && initial !== null ? clamp(initial) : 0;
    const fill = this._online && soc !== null ? Math.max(0, clamp(soc) - base) : 0;
    return `<section class="${['panel soc', `soc-${color}`, this._online ? '' : 'off'].filter(Boolean).join(' ')}" aria-label="${t.aria.battery}">
      <div class="tiles two">
        <button class="tile soc-tile" data-more-info="soc_percent" data-hold="initial_soc" title="${t.batteryTitle}">
          <span class="tile-head"><ha-icon icon="${icon}"></ha-icon>${t.battery}<span class="tile-aside">${t.est}</span></span>
          <span class="tile-big"><span class="soc-from">${pc(initial)}</span>${arrow}<b data-fit="22">${soc === null ? '—' : `≈${pc(soc)}`}</b></span>
          <span class="tile-sub" data-fit="12">${stored === null ? '&nbsp;' : `≈${Number(stored.toFixed(stored < 100 ? 1 : 0))}<small>kWh</small> ${t.inBattery}`}</span>
        </button>
        <button class="tile soc-tile" data-more-info="time_to_target_soc" data-hold="target_soc" title="${t.targetTitle}">
          <span class="tile-head"><ha-icon icon="mdi:flag-checkered"></ha-icon>${reached ? `${t.target} ${pc(target)}` : finish ? `<span class="soc-finish" data-fit="11.5" title="${t.finishTitle}">${t.finish} ${finish}</span>` : eta ? t.toTarget : t.target}</span>
          <span class="tile-big">${goalBig}</span>
          <span class="tile-sub" data-fit="12">${goalSub ? `${goalSub} <span class="tile-note">${t.toGo}</span>` : '&nbsp;'}</span>
        </button>
      </div>
      <div class="soc-bar" aria-hidden="true"><span class="soc-base" style="width:${base}%"></span><span class="soc-fill" style="left:${base}%;width:${fill}%"></span>${target === null ? '' : `<span class="soc-target" style="left:${clamp(target)}%"></span>`}</div>
    </section>`;
  }
  // The charger's T/A/B counters. Resetting A or B is irreversible, so it always asks first.
  _historySection() {
    const t = this._t;
    const items = [
      {key: 'total_energy', label: t.total, sub: t.allTime},
      {key: 'counter_a_energy', label: t.counterA, cost: 'counter_a_cost', reset: 'reset_counter_a'},
      {key: 'counter_b_energy', label: t.counterB, cost: 'counter_b_cost', reset: 'reset_counter_b'},
    ].filter((item) => this._ids?.[item.key]);
    if (!items.length) return `<div class="message">${t.none.history}</div>`;
    const value = (key, cost) => {
      const e = this._online ? currentNumber(this._state(key)) : null;
      const c = cost && this._online ? currentNumber(this._state(cost)) : null;
      return {e, c, money: c === null ? '—' : money(Math.round(c), this._state(cost)?.attributes?.unit_of_measurement)};
    };
    const asking = items.find((item) => item.reset && item.reset === this._resetAsk);
    if (asking) {
      const {e, money: m} = value(asking.key, asking.cost);
      return `<section class="panel history asking-reset" aria-label="${t.aria.history}">
        <div class="reset-ask" role="alertdialog" aria-label="${t.resetQ(asking.label)}">
          <ha-icon icon="mdi:restore-alert"></ha-icon>
          <span class="reset-text"><b>${t.resetQ(asking.label)}</b><small>${e === null ? '—' : Math.round(e)} kWh · ${m} ${t.toZero}</small></span>
          <button class="reset-no" data-reset-cancel>${t.cancel}</button>
          <button class="reset-yes" data-reset-confirm="${asking.reset}">${t.reset}</button>
        </div>
      </section>`;
    }
    const tiles = items.map(({key, label, cost, reset, sub}) => {
      const {e, money: m} = value(key, cost);
      const resetBtn = reset && this._ids[reset]
        ? `<button class="reset-btn" data-reset="${reset}" title="${t.reset}: ${label}" aria-label="${t.reset}: ${label}" ${!this._online || this._state(reset)?.state === 'unavailable' ? 'disabled' : ''}><ha-icon icon="mdi:restore"></ha-icon></button>` : '';
      return `<div class="tile hist-tile">
        <span class="tile-head"><span class="hist-label" data-fit="10.5">${label}</span>${resetBtn}</span>
        <button class="tile-body" data-more-info="${key}" title="${label} — ${t.details}">
          <span class="tile-big"><b data-fit="18">${e === null ? '—' : Math.round(e)}<small>kWh</small></b></span>
          <span class="tile-sub">${cost ? m : sub}</span>
        </button>
      </div>`;
    }).join('');
    return `<section class="${['panel history', this._online ? '' : 'off'].filter(Boolean).join(' ')}" aria-label="${t.aria.history}">
      <div class="tiles ${items.length === 3 ? 'three' : 'two'}">${tiles}</div>
    </section>`;
  }
  // First call asks (8 s to answer); `confirmed` presses the integration's own reset button.
  async _resetCounter(key, confirmed = false) {
    const entity = this._state(key);
    if (!this._online || !entity || entity.state === 'unavailable') return;
    if (!confirmed) {
      this._resetAsk = key;
      clearTimeout(this._resetTimer);
      this._resetTimer = setTimeout(() => { this._resetAsk = null; this._render(); }, 8000);
      this._render();
      return;
    }
    if (this._resetAsk !== key) return;
    clearTimeout(this._resetTimer);
    this._resetAsk = null;
    this._render();
    try {
      await this._hass.callService('button', 'press', {entity_id: this._ids[key]});
    } catch { /* HA reports a refused reset itself (button.py raises HomeAssistantError). */ }
  }
  _cancelReset() {
    clearTimeout(this._resetTimer);
    this._resetAsk = null;
    this._render();
  }
  _advanced_controlsSection() {
    if (!this._advanced) return '';
    const t = this._t;
    const items = [
      {key: 'initial_soc', label: t.initial, fallbackUnit: '%', title: t.initialTitle},
      {key: 'target_soc', label: t.target, fallbackUnit: '%', title: t.targetSocTitle},
      {key: 'battery_capacity', label: t.capacity, fallbackUnit: 'kWh', title: t.capacityTitle},
      {key: 'soc_correction', label: t.loss, fallbackUnit: '%', title: t.lossTitle},
    ].filter((item) => this._ids[item.key]);
    const tiles = items.map((item) => `<div class="limit-tile" title="${item.title}"><div class="limit-toggle"><span data-fit="10.5">${item.label}<small class="tile-unit">${this._unit(item.key, item.fallbackUnit)}</small></span></div>${this._numberValue({...item, unit: false})}</div>`).join('');
    // Battery settings wear the battery section's colour, so the pair reads as one group.
    return `<section class="${['limits controls', `soc-${this._socColor}`, this._online ? '' : 'off'].filter(Boolean).join(' ')}" aria-label="${t.aria.controls}">
      <div class="limits-grid ${items.length === 4 ? 'four' : 'three'}">${tiles}</div>
    </section>`;
  }
  async _selectOption(key, option) {
    const entity = this._state(key);
    const options = entity?.attributes?.options;
    if (!this._online || !Array.isArray(options) || !options.includes(option) || option === (this._limitPending[key] ?? entity.state)) { this._render(); return; }
    this._setLimitPending(key, option);
    this._render();
    try {
      await this._hass.callService('select', 'select_option', {entity_id: this._ids[key], option});
    } catch {
      clearTimeout(this._limitTimers[key]);
      delete this._limitPending[key];
      this._render();
    }
  }
  _adaptiveSection() {
    const entity = this._state('adaptive_mode');
    const t = this._t;
    if (!entity) return `<div class="message">${t.none.adaptive}</div>`;
    const mode = this._limitPending.adaptive_mode ?? entity.state;
    const options = Array.isArray(entity.attributes?.options) ? entity.attributes.options : [];
    const known = options.includes(mode);
    const limit = this._online ? currentNumber(this._state('adaptive_current_limit')) : null;
    const select = `<select class="adaptive-mode" data-select="adaptive_mode" aria-label="${t.adaptiveMode}" ${!this._online || !known ? 'disabled' : ''}>
      ${known ? '' : '<option selected>—</option>'}${options.map((o) => `<option value="${o}"${o === mode ? ' selected' : ''}>${t.modes[o] ?? o}</option>`).join('')}</select>`;
    // Only Voltage mode uses the threshold; Off has no cap to show.
    const threshold = mode === 'Voltage' && this._ids.undervoltage_threshold
      ? `<span class="adaptive-hint">${t.slowBelow}</span><div class="adaptive-threshold" title="${t.slowTitle}">${this._numberValue({key: 'undervoltage_threshold', label: t.threshold, fallbackUnit: 'V'})}</div>` : '';
    const cap = known && mode !== 'Off' && this._ids.adaptive_current_limit
      ? `<button class="adaptive-cap" data-more-info="adaptive_current_limit" title="${t.capTitle} — ${t.details}"><span>${threshold ? t.cap : t.capped}</span><b>${limit === null ? '—' : `${amps(limit)}<small>A</small>`}</b></button>` : '';
    return `<section class="${['panel adaptive', this._online ? '' : 'off'].filter(Boolean).join(' ')}" aria-label="${t.aria.adaptive}">
      <div class="panel-head"><span class="panel-title"><ha-icon icon="mdi:auto-mode"></ha-icon><b data-fit="14">${t.adaptive}</b></span>${select}</div>
      ${threshold || cap ? `<div class="adaptive-row">${threshold}${cap}</div>` : ''}
    </section>`;
  }
  _stateLabel(entity) {
    if (!entity) return '—';
    const own = this._t.states[entity.state];
    if (own) return own;
    return typeof this._hass?.formatEntityState === 'function' ? this._hass.formatEntityState(entity) : entity.state;
  }
  // How long the last charger reading has been stale; '' under a minute or unknown.
  _since() {
    const t = Date.parse(this._state('state')?.last_changed);
    if (!Number.isFinite(t)) return '';
    const m = Math.floor((Date.now() - t) / 60000);
    return m < 1 ? '' : this._t.ago(m);
  }
  // Card-level alert: the charger is unreachable or reports a firmware fault. Only these
  // two, because every other section already colours its own abnormal readings.
  _alert() {
    if (!this._resolved || !this._ids?.state) return null;
    if (!this._online) return {kind: 'offline', icon: 'mdi:clock-alert-outline', text: [this._t.offline, this._since()].filter(Boolean).join(' · ')};
    if (this._state('state')?.state !== 'Error') return null;
    const sub = this._state('substate');
    return {kind: 'fault', icon: 'mdi:alert', text: [this._t.error, sub && !['unknown', 'unavailable'].includes(sub.state) ? this._stateLabel(sub) : ''].filter(Boolean).join(' · ')};
  }
  _alertStrip() {
    if (this._config.sections.includes('status')) return '';
    const a = this._alert();
    return a ? `<div class="alert-strip ${a.kind}" role="alert"><ha-icon icon="${a.icon}"></ha-icon><span>${a.text}</span></div>` : '';
  }
  _statusView() {
    if (!this._online) return {cls: 'offline', icon: 'mdi:clock-alert-outline', text: this._t.offline, note: this._since()};
    const entity = this._state('state'), raw = entity.state;
    if (raw === 'Error') {
      const sub = this._state('substate');
      // The fault itself is the news; "Error" alone would push it off a narrow row.
      const fault = sub && !['unknown', 'unavailable'].includes(sub.state) ? this._stateLabel(sub) : '';
      return {cls: 'fault', icon: 'mdi:alert', text: fault || this._stateLabel(entity), note: ''};
    }
    const charging = raw === 'Charging', stopped = !charging && this._limitOn('stop_charging');
    const text = stopped ? this._t.stopped : this._stateLabel(entity);
    const reason = this._state('not_charging_reason');
    const reasonText = !charging && reason && !['unknown', 'unavailable', 'Charging'].includes(reason.state) ? this._stateLabel(reason) : '';
    // "Stopped · Stopped by User" and "Charge Complete · Charge Complete" say nothing twice.
    const note = reasonText && reasonText !== text && !(stopped && reason.state === 'Stopped by User') ? reasonText : '';
    return {cls: charging ? 'charging' : stopped ? 'paused' : 'idle', icon: charging ? 'mdi:flash' : 'mdi:ev-station', text, note};
  }
  // Stopping a running charge asks once, in the button itself; everything else is one tap.
  async _statusToggle(key) {
    const entity = this._state(key);
    if (!this._online || !entity || !['on', 'off'].includes(entity.state)) return;
    if (key === 'stop_charging' && !this._limitOn(key) && this._charging && this._statusAsk !== key) {
      this._statusAsk = key;
      clearTimeout(this._statusTimer);
      this._statusTimer = setTimeout(() => { this._statusAsk = null; this._render(); }, 4000);
      this._render();
      return;
    }
    clearTimeout(this._statusTimer);
    this._statusAsk = null;
    await this._toggleLimit(key);
  }
  // Full width for the state and its reason: nothing else competes for this row.
  _statusSection() {
    const t = this._t;
    if (!this._ids?.state) return `<div class="message">${t.none.status}</div>`;
    const v = this._statusView();
    const alert = v.cls === 'fault' || v.cls === 'offline' ? `status-alert-${v.cls}` : '';
    const rowClass = ['sl', 'status', alert, this._online ? '' : 'off'].filter(Boolean).join(' ');
    return `<section class="${rowClass}" aria-label="${t.aria.status}">
      <button class="status-main" data-more-info="state" title="${t.stateTitle} — ${t.details}"><ha-icon icon="${v.icon}"></ha-icon><span class="status-state ${v.cls}">${v.text}</span>${v.note ? `<span class="status-note">${v.note}</span>` : ''}</button>
    </section>`;
  }
  // Three big buttons: icon, name, and what the switch is doing now (or what a tap will do).
  _actionsSection() {
    const buttons = STATUS_BUTTONS.filter((b) => this._ids?.[b.key]);
    const t = this._t;
    if (!buttons.length) return `<div class="message">${t.none.actions}</div>`;
    return `<section class="${['panel actions', this._online ? '' : 'off'].filter(Boolean).join(' ')}" aria-label="${t.aria.actions}">${buttons.map((b) => {
      const on = this._limitOn(b.key), ask = this._statusAsk === b.key;
      const known = ['on', 'off'].includes(this._state(b.key)?.state);
      const stop = b.key === 'stop_charging';
      const label = ask ? t.sure : stop && on ? t.stopped : t[b.label];
      const state = ask ? t.tapAgain : stop ? (on ? t.tapResume : t.tapStop) : on ? t.on : t.off;
      const icon = stop && on && !ask ? '<ha-icon icon="mdi:play-circle"></ha-icon>' : b.icon;
      const cls = ['act-btn', `status-${b.key}`, on ? 'on' : '', ask ? 'asking' : ''].filter(Boolean).join(' ');
      return `<button class="${cls}" data-status-toggle="${b.key}" role="switch" aria-checked="${on}" title="${t[b.title]}" ${!this._online || !known ? 'disabled' : ''}><span class="act-icon">${icon}</span><span class="act-text"><span class="act-label" data-fit="14">${label}</span><span class="act-state" data-fit="11.5">${state}</span></span></button>`;
    }).join('')}</section>`;
  }
  _currentSection() {
    const t = this._t;
    if (!this._ids?.charging_current) return `<div class="message">${t.none.current}</div>`;
    const bounds = this._bounds;
    const value = this._draft ?? (this._online ? currentNumber(this._state('charging_current')) : null);
    const disabled = !this._canControl || this._sent;
    const label = t.current;
    const range = bounds ? `min="${bounds.min}" max="${bounds.max}" step="${bounds.step}"` : 'min="0" max="0" step="1"';
    const output = this._asking
      ? `<button class="sv ok" data-confirm aria-label="${t.confirmIncrease} ${amps(value)} A"><span class="cv">${amps(value)} A</span><ha-icon icon="mdi:check"></ha-icon></button>`
      : `<b class="sv" title="${t.requested}"><span class="cv">${amps(value)} A</span></b>`;
    return `<section class="${['sl current', this._asking ? 'ask' : '', this._online ? '' : 'off'].filter(Boolean).join(' ')}" aria-label="${t.aria.current}">
      <ha-icon icon="mdi:current-ac"></ha-icon><span class="label">${label}</span>
      <div class="rw"><div class="markers">${this._actualMarker()}</div><input aria-label="${t.requestedCharging}" type="range" data-slide="current" ${range} value="${value ?? bounds?.min ?? 0}" style="--fill:${bounds && value !== null ? Math.max(0, Math.min(100, 100 * (value - bounds.min) / (bounds.max - bounds.min))) : 0}%" ${disabled ? 'disabled' : ''}></div>
      <div class="readout">${output}</div>
      </section>${this._error ? `<div class="error" role="alert">${t.setFailed}</div>` : ''}`;
  }
  _render() {
    if (!this.shadowRoot || !this._hass) return;
    const focus = this.shadowRoot.activeElement;
    const selector = this._editingLimit ? `[data-limit-edit="${this._editingLimit}"]`
      : focus?.dataset?.slide ? 'input'
      : focus?.hasAttribute('data-confirm') ? '[data-confirm]'
      : null;
    const body = !this._resolved ? `<div class="message">${this._t.loading}</div>`
      : this._alertStrip() + this._config.sections.map((section) => this[`_${section}Section`]()).join('');
    // The charger's state colours the card itself (glow, status badge); charging adds the
    // old card's slow pulse. A data attribute, not a class: alert tints stay section-scoped.
    const mood = this._resolved && this._ids?.state ? this._statusView().cls : 'idle';
    this.shadowRoot.innerHTML = `<style>${MODULAR_STYLE}</style><ha-card data-state="${mood}">${body}</ha-card>`;
    this._fitLimitValues();
    // A synchronous read right after a full innerHTML replacement can land before
    // the nested CSS Grid has finished sizing (observed live: a widened reading
    // measured as fitting when it hadn't), so re-check once the browser has
    // actually painted a layout for the new DOM.
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(() => this._fitLimitValues());
    const el = selector ? this.shadowRoot.querySelector(selector) : null;
    if (el) { el.focus(); if (typeof el.select === 'function') el.select(); }
  }
  // Values share one column width across four tiles: shrink each reading only as
  // far as its own text needs, so a normal 85% stays big while a 1440-minute
  // ceiling still fits without clipping.
  _fitLimitValues() {
    if (typeof this.shadowRoot?.querySelectorAll !== 'function') return;
    for (const el of this.shadowRoot.querySelectorAll('[data-fit]')) {
      let size = Number(el.dataset.fit) || 18;
      el.style.fontSize = `${size}px`;
      while (size > 7 && el.scrollWidth > el.clientWidth) {
        size -= 1;
        el.style.fontSize = `${size}px`;
      }
    }
  }
}

const MODULAR_STYLE = `
:host{display:block}
ha-card{box-sizing:border-box;display:flex;flex-direction:column;gap:1px;padding:2px 4px 1px;border-radius:14px;overflow:hidden;line-height:1.2}
.sl{box-sizing:border-box;display:flex;align-items:center;gap:6px;min-width:0;padding:1px 8px;border:1px solid rgba(127,127,127,.16);border-radius:10px;background:rgba(127,127,127,.05)}
ha-icon{--mdc-icon-size:16px;color:var(--secondary-text-color);flex:none}
.label{font-size:13px;font-weight:600;white-space:nowrap;color:var(--primary-text-color)}
.rw{position:relative;flex:1;display:flex;align-items:center;min-width:0}
input[type=range]{appearance:none;-webkit-appearance:none;width:100%;min-width:0;height:26px;margin:0;background:transparent;cursor:pointer}
input::-webkit-slider-runnable-track{height:4px;border-radius:4px;background:#3498db}
input::-moz-range-track{height:4px;border-radius:4px;background:#3498db}
input::-webkit-slider-thumb{appearance:none;-webkit-appearance:none;width:14px;height:14px;margin-top:-5px;border-radius:50%;background:#3498db;border:1px solid #3498db}
input::-moz-range-thumb{width:12px;height:12px;border-radius:50%;background:#3498db;border:1px solid #3498db}
input:disabled{cursor:not-allowed;opacity:.45}
.readout{display:flex;flex-direction:column;align-items:flex-end;justify-content:center;flex:none;min-width:34px;gap:0;font-variant-numeric:tabular-nums}
.sv{font-size:15px;font-weight:600;line-height:17px;white-space:nowrap;color:var(--primary-text-color)}
.ok{box-sizing:border-box;display:flex;align-items:center;gap:2px;border:1px solid #f39c12;border-radius:6px;padding:0 3px;background:#f39c1233;font-family:inherit;cursor:pointer;touch-action:manipulation;position:relative}
.ok::after{content:'';position:absolute;inset:-7px -3px}
.ok ha-icon{--mdc-icon-size:14px;color:#f39c12}
.ask{border-color:#f39c1299;background:#f39c1219}.ask .label{color:#f39c12}.off{opacity:.55}
.markers{position:absolute;inset:0;z-index:1;pointer-events:none}
.mk{position:absolute;top:4px;width:3px;height:18px;border-radius:2px;background:#f39c12;box-shadow:0 0 0 1px #332000,0 0 4px #f39c1280;transform:translateX(-50%)}
input:focus-visible,button:focus-visible{outline:2px solid var(--primary-color,#3498db);outline-offset:2px}
.message,.error{font-size:12px;padding:2px 8px;color:var(--secondary-text-color)}.error{color:var(--error-color,#e74c3c)}
.limits{box-sizing:border-box;display:flex;flex-direction:column;gap:1px;min-width:0;padding:2px 2px 0;border:1px solid rgba(127,127,127,.16);border-radius:10px;background:rgba(127,127,127,.04)}
.limits-head{display:flex;align-items:center;justify-content:space-between;min-height:16px;padding:0 3px;gap:6px}
.limits-head>span{display:flex;align-items:center;gap:5px;font-size:13px}.limits-head>span ha-icon{color:var(--primary-color,#3498db)}
.disable-all,.limit-toggle,.limit-value button{box-sizing:border-box;border:0;font-family:inherit;color:inherit;cursor:pointer;touch-action:manipulation}
.disable-all{display:flex;align-items:center;gap:4px;min-height:20px;padding:1px 6px;border:1px solid rgba(127,127,127,.25);border-radius:8px;background:rgba(127,127,127,.08);font-size:11px;font-weight:600}
.disable-all i,.limit-toggle i{width:7px;height:7px;border-radius:50%;background:var(--secondary-text-color);opacity:.45}
.disable-all.active{border-color:#f39c1299;background:#f39c1224;color:#f39c12}.disable-all.active i{background:#f39c12;opacity:1}
.limits-grid{display:grid;gap:1px;min-width:0}.limits-grid.four{grid-template-columns:repeat(4,minmax(0,1fr))}.limits-grid.three{grid-template-columns:repeat(3,minmax(0,1fr))}
.limit-tile{box-sizing:border-box;min-width:0;padding:1px 1px 0;border:1px solid rgba(127,127,127,.18);border-radius:8px;background:rgba(127,127,127,.06)}
.limit-tile.active{border-color:#2ecc7188;background:#2ecc7118}.limit-tile.active .limit-toggle{color:#2ecc71}.limit-tile.active .limit-toggle i{background:#2ecc71;opacity:1}
.limits-grid.suspended .limit-tile{border-color:rgba(127,127,127,.18);background:rgba(127,127,127,.035)}.limits-grid.suspended .limit-tile.saved .limit-toggle i{background:#f39c12;opacity:.7}
.limit-toggle{display:flex;align-items:center;width:100%;min-width:0;height:14px;padding:0 2px;background:transparent;font-size:10px;font-weight:600;gap:2px}
.limit-toggle span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.limit-toggle i{margin-left:auto;flex:none;width:6px;height:6px}
.limit-value{display:grid;grid-template-columns:15px minmax(0,1fr) 15px;align-items:center;min-width:0;height:21px}
.limit-value>button[data-limit-step]{display:flex;align-items:center;justify-content:center;width:15px;height:100%;padding:0;background:transparent;border-radius:5px;font-size:16px;color:var(--secondary-text-color)}
.limit-value-btn{box-sizing:border-box;display:flex;align-items:center;justify-content:center;gap:1px;width:100%;height:100%;min-width:0;padding:0;margin:0;border:0;border-radius:6px;background:transparent;font-family:inherit;color:inherit;cursor:pointer;touch-action:manipulation}
.limit-value-btn:hover,.limit-value-btn:focus-visible{background:rgba(127,127,127,.14)}
.limit-value b{min-width:0;overflow:hidden;text-align:center;white-space:nowrap;font-size:18px;font-weight:700;line-height:1.1;font-variant-numeric:tabular-nums}.limit-value small{display:inline;margin:0 0 0 1px;font-size:.62em;line-height:1;font-weight:500;color:var(--secondary-text-color);white-space:nowrap}
.limit-edit-input{box-sizing:border-box;width:100%;height:100%;min-width:0;padding:0 2px;margin:0;border:1px solid var(--primary-color,#3498db);border-radius:5px;background:transparent;color:inherit;font-family:inherit;font-size:14px;font-weight:700;text-align:center;font-variant-numeric:tabular-nums;-moz-appearance:textfield}
.limit-edit-input::-webkit-outer-spin-button,.limit-edit-input::-webkit-inner-spin-button{-webkit-appearance:none;margin:0}
button:disabled{cursor:not-allowed;opacity:.4}.limits.off{opacity:.55}
.safety-row{display:flex;align-items:center;justify-content:space-between;gap:1px;flex:1;min-width:0;overflow:hidden}
.safety-item{display:flex;align-items:center;gap:1px;flex:none;white-space:nowrap;box-sizing:border-box;border:0;padding:0;margin:0;background:transparent;font-family:inherit;color:inherit;cursor:pointer;touch-action:manipulation;-webkit-tap-highlight-color:transparent}
.safety-item:hover,.safety-item:focus-visible{opacity:.8}
.safety-item ha-icon{--mdc-icon-size:14px;color:var(--secondary-text-color);margin-right:1px}
.safety-ground-icon{width:15.5px;height:15.5px;margin-right:1px;flex:none;fill:var(--secondary-text-color)}
.safety-ground{display:flex;cursor:pointer;touch-action:manipulation}
.safety-ground i{width:6px;height:6px;margin-left:1px;border-radius:50%;background:var(--secondary-text-color);opacity:.45}
.safety-ground i.good{background:#2ecc71;opacity:1}.safety-ground i.bad{background:#e74c3c;opacity:1}
.safety-value{white-space:nowrap;font-size:13px;font-weight:700;line-height:1.1;font-variant-numeric:tabular-nums;color:var(--primary-text-color)}
.safety-value small{margin-left:1px;font-size:.75em;font-weight:500;color:inherit}
.safety-value.good{color:#2ecc71}.safety-value.bad{color:#e74c3c}
.sl.safety.safety-good{background:rgba(46,204,113,.08);border-color:rgba(46,204,113,.35)}
.sl.safety.safety-bad{background:rgba(231,76,60,.16);border-color:rgba(231,76,60,.5)}
.sl.status{gap:4px;padding:1px 2px 1px 6px}.sl.status.off{opacity:1}
.status-main{display:flex;align-items:center;gap:5px;flex:1;min-width:0;overflow:hidden;height:24px;padding:0;margin:0;border:0;background:transparent;font-family:inherit;color:inherit;cursor:pointer;text-align:left;-webkit-tap-highlight-color:transparent}
.status-state{flex:0 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;font-size:13px;font-weight:600;white-space:nowrap;color:var(--primary-text-color)}
.status-state.charging{color:#2ecc71}.status-state.paused{color:#f39c12}.status-state.fault{color:#e74c3c}.status-state.offline{color:var(--secondary-text-color)}
.status-note{flex:0 1000 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11px;color:var(--secondary-text-color)}
.sl.status.status-alert-fault{background:rgba(231,76,60,.16);border-color:rgba(231,76,60,.5)}
.sl.status.status-alert-offline{background:rgba(127,127,127,.12);border-color:rgba(127,127,127,.4)}
ha-card{container-type:inline-size}
.info-row{display:flex;align-items:center;justify-content:space-between;gap:6px;flex:1;min-width:0}
.info-item{display:flex;flex:none;align-items:center;gap:3px;min-width:0;height:24px;padding:0;margin:0;border:0;background:transparent;font-family:inherit;color:inherit;cursor:pointer;white-space:nowrap;touch-action:manipulation;-webkit-tap-highlight-color:transparent}
.info-item.grow{flex:0 1 auto}.info-item:hover,.info-item:focus-visible{opacity:.8}
.info-item ha-icon{--mdc-icon-size:15px}
.info-value{min-width:0;overflow:hidden;text-overflow:ellipsis;font-size:15px;font-weight:600;font-variant-numeric:tabular-nums;color:var(--primary-text-color)}
.info-value small{font-size:.7em;font-weight:500;color:var(--secondary-text-color)}
.info-sep{font-style:normal;margin:0 2px;color:var(--secondary-text-color)}
.limits.controls{padding-top:1px}.limits.controls .limit-toggle{cursor:default;color:var(--secondary-text-color)}
.limit-value.readonly{grid-template-columns:minmax(0,1fr)}.limit-value.readonly b{color:var(--secondary-text-color)}

.adaptive-mode{box-sizing:border-box;height:22px;min-width:0;padding:0 4px;border:1px solid rgba(127,127,127,.3);border-radius:8px;background:rgba(127,127,127,.08);color:var(--primary-text-color);font-family:inherit;font-size:13px;font-weight:600;cursor:pointer}
.adaptive-mode option{color:#000}
.adaptive-threshold{flex:1;min-width:84px;max-width:130px}.adaptive-threshold .limit-value{height:22px}
.sl.adaptive .info-item{margin-left:auto}
.alert-strip{display:flex;align-items:center;gap:5px;min-height:20px;padding:0 8px;border-radius:10px;font-size:12px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.alert-strip ha-icon{--mdc-icon-size:14px;color:inherit}
.alert-strip.fault{color:#e74c3c;background:rgba(231,76,60,.16);border:1px solid rgba(231,76,60,.5)}
.alert-strip.offline{color:var(--secondary-text-color);background:rgba(127,127,127,.12);border:1px solid rgba(127,127,127,.4)}
/* ---- One skin for every section: flat panels, no resting borders,
   one accent for section icons, one chip style, one type scale. Borders appear only as state
   (active / suspended / alert), so a border always means something. ---- */
ha-card{--accent:var(--primary-color,#3498db);--panel:rgba(127,127,127,.075);--tile:rgba(127,127,127,.09);--chip:rgba(127,127,127,.12);gap:3px;padding:4px}
.sl,.limits,.panel{border:1px solid transparent;border-radius:12px;background:var(--panel)}
.sl{min-height:30px;padding:1px 8px}
.sl>ha-icon,.limits-head>span ha-icon{--mdc-icon-size:17px;color:var(--accent)}
.label,.limits-head>span{font-size:14px;font-weight:600}
.limits{gap:2px;padding:2px 3px 3px}.limits-head{min-height:22px;padding:0 1px 0 5px}
.limits-grid,.tiles{gap:3px}
.limit-tile,.tile{border:1px solid transparent;border-radius:9px;background:var(--tile)}
.limit-tile{padding:2px 2px 1px}
.limit-toggle,.tile-head{font-size:10.5px;font-weight:600;letter-spacing:.02em;color:var(--secondary-text-color)}
.limit-tile.active .limit-toggle{color:#2ecc71}
.limit-value{height:22px}.limit-value>button[data-limit-step]{font-size:17px}
.disable-all,.adaptive-mode{height:24px;min-height:22px;border:1px solid transparent;border-radius:12px;background:var(--chip);font-size:12px;font-weight:600}
.status-state{font-size:14px}.status-main>ha-icon{--mdc-icon-size:17px;color:var(--accent)}
.status-state.charging~*,.status-note{font-size:12px}
.info-value,.sv{font-size:15px;font-weight:650}
.safety-value{font-size:14px}
.sl.safety>ha-icon{color:var(--accent)}.sl.safety.safety-good>ha-icon{color:#2ecc71}.sl.safety.safety-bad>ha-icon{color:#e74c3c}
.sl.safety.safety-good{background:rgba(46,204,113,.09);border-color:transparent}
.sl.adaptive{gap:8px}.adaptive-mode{padding:0 8px}
.adaptive-threshold{flex:none;width:112px;min-width:0;max-width:none}.adaptive-threshold .limit-value{height:24px}
.panel{box-sizing:border-box;display:flex;flex-direction:column;gap:4px;min-width:0;padding:3px}
.tiles{display:grid;min-width:0}.tiles.two{grid-template-columns:repeat(2,minmax(0,1fr))}.tiles.three{grid-template-columns:repeat(3,minmax(0,1fr))}
.tile{box-sizing:border-box;display:flex;flex-direction:column;align-items:flex-start;gap:0;min-width:0;padding:2px 7px 3px;margin:0;font-family:inherit;color:inherit;text-align:left;cursor:pointer;touch-action:manipulation;-webkit-tap-highlight-color:transparent}
.tile:hover,.tile:focus-visible{background:rgba(127,127,127,.15)}
.tile-head{display:flex;align-items:center;gap:4px;width:100%;white-space:nowrap}.tile-aside{margin-left:auto;font-weight:600;color:var(--primary-text-color);opacity:.85}.tile-aside small{font-size:.9em}.tile-head ha-icon{--mdc-icon-size:14px;color:var(--accent)}
.tile-big{display:flex;align-items:baseline;max-width:100%;min-width:0;white-space:nowrap;font-size:19px;font-weight:700;line-height:1.15;font-variant-numeric:tabular-nums;color:var(--primary-text-color)}
.tile-big b{min-width:0;overflow:hidden;font-weight:700}.tile-big small{font-size:.6em;font-weight:500;color:var(--secondary-text-color);margin-left:1px}
.tile-sub{max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11.5px;font-weight:600;color:var(--secondary-text-color);font-variant-numeric:tabular-nums}
.tile-sub small{font-size:.85em;font-weight:500}.tile-note{font-weight:500;opacity:.8}
/* ---- Glass and glow from the 4.24.0 card, one hue per section.
   --c = charger state colour (card glow, status badge). --h = the section's own hue: its icon
   badge, a faint glass gradient and a hairline border. Structure and sizes stay as above. ---- */
ha-card{--c:#7f8c8d;position:relative;isolation:isolate;
  background:radial-gradient(140% 90px at 0 0,color-mix(in srgb,var(--c) 22%,transparent),transparent 70%),var(--ha-card-background,var(--card-background-color,#1c1c1c));transition:box-shadow .4s}
ha-card[data-state=charging]{--c:#2ecc71;animation:card-pulse 2.6s ease-in-out infinite}
ha-card[data-state=paused]{--c:#f39c12}ha-card[data-state=fault]{--c:#e74c3c}ha-card[data-state=offline]{--c:#636e72}ha-card[data-state=idle]{--c:#3498db}
@keyframes card-pulse{0%,100%{box-shadow:0 1px 10px color-mix(in srgb,var(--c) 18%,transparent)}50%{box-shadow:0 2px 24px color-mix(in srgb,var(--c) 42%,transparent)}}
.sl,.limits,.panel{--h:#95a5a6;border-color:color-mix(in srgb,var(--h) 16%,transparent);
  background:linear-gradient(135deg,color-mix(in srgb,var(--h) 13%,transparent),color-mix(in srgb,var(--h) 3%,transparent) 65%)}
.sl.status{--h:var(--c)}.sl.current{--h:#3498db}.sl.info.basic{--h:#f5c542}.limits{--h:#f39c12}
.panel.adaptive{--h:#22c1c3}.panel.meter{--h:#f5c542}.sl.session{--h:#e67e22}.panel.actions{--h:#95a5a6}.panel.history{--h:#ff6b9a}.sl.safety{--h:#2ecc71}.sl.safety.safety-bad{--h:#e74c3c}
.panel.soc,.limits.controls{--h:#3498db}:is(.panel.soc,.limits.controls):is(.soc-low){--h:#e74c3c}:is(.panel.soc,.limits.controls):is(.soc-mid){--h:#f39c12}:is(.panel.soc,.limits.controls):is(.soc-high){--h:#2ecc71}:is(.panel.soc,.limits.controls):is(.soc-reached){--h:#a78bfa}:is(.panel.soc,.limits.controls):is(.soc-off){--h:#636e72}
/* Icon badges: every section's lead icon sits in a tinted rounded square of its hue. */
.sl>ha-icon,.limits-head>span ha-icon,.status-main>ha-icon,.panel-title>ha-icon{--mdc-icon-size:16px;box-sizing:content-box;padding:4px;border-radius:8px;color:var(--h,var(--c));
  background:color-mix(in srgb,var(--h,var(--c)) 20%,transparent);box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--h,var(--c)) 28%,transparent)}
.status-main>ha-icon{color:var(--c);background:color-mix(in srgb,var(--c) 22%,transparent)}
.sl{padding:2px 8px 2px 3px}.limits-head{padding-left:1px}
.panel.history .tile-head,.limits.controls .limit-toggle{color:color-mix(in srgb,var(--h) 75%,var(--secondary-text-color))}
.limit-tile,.tile{background:linear-gradient(160deg,rgba(255,255,255,.06),rgba(127,127,127,.05))}
.limit-tile.active{background:linear-gradient(145deg,color-mix(in srgb,#2ecc71 24%,transparent),color-mix(in srgb,#2ecc71 7%,transparent));border-color:color-mix(in srgb,#2ecc71 55%,transparent)}
/* Charging glow on live values, like 4.24.0. */
ha-card[data-state=charging] .sl.status>.status-main>ha-icon{animation:icon-glow 2.6s ease-in-out infinite}
@keyframes icon-glow{0%,100%{filter:drop-shadow(0 0 2px color-mix(in srgb,var(--c) 40%,transparent))}50%{filter:drop-shadow(0 0 8px color-mix(in srgb,var(--c) 85%,transparent))}}
.panel.soc{flex-direction:row;align-items:center;gap:8px;padding:4px 6px 4px 4px;overflow:hidden;position:relative}
/* Slider: filled gradient up to the thumb, dim rest, glowing thumb. */
.sl.current input::-webkit-slider-runnable-track{background:linear-gradient(90deg,#1f6fb2,#3498db var(--fill,100%),rgba(127,127,127,.28) var(--fill,100%))}
.sl.current input::-moz-range-track{background:rgba(127,127,127,.28)}.sl.current input::-moz-range-progress{height:4px;border-radius:4px;background:linear-gradient(90deg,#1f6fb2,#3498db)}
.sl.current input::-webkit-slider-thumb{box-shadow:0 0 0 3px color-mix(in srgb,#3498db 28%,transparent),0 0 8px #3498db99}
.sv{font-weight:750}
/* ---- Bigger values and touch targets, one job per section. ---- */
.sl{min-height:32px}
.sl.status{min-height:34px;padding:2px 8px 2px 3px}.status-main{height:28px;gap:7px}.status-state{font-size:15px;font-weight:700}.status-note{font-size:12.5px}
/* Actions: three big buttons, icon over name over state (4.24.0 icons). */
.panel.actions{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:4px;padding:4px}
.act-btn{--b:#95a5a6;box-sizing:border-box;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:0;min-width:0;min-height:56px;padding:4px 2px 3px;margin:0;
  border:1px solid rgba(127,127,127,.18);border-radius:11px;background:linear-gradient(160deg,rgba(255,255,255,.07),rgba(127,127,127,.05));font-family:inherit;color:var(--primary-text-color);cursor:pointer;touch-action:manipulation;-webkit-tap-highlight-color:transparent;transition:background .2s,box-shadow .2s}
.act-btn:active{transform:scale(.97)}
.act-icon{display:flex;height:25px;align-items:center}.act-icon ha-icon{--mdc-icon-size:25px;color:var(--b)}.act-stop-icon{width:26px;height:26px}
.act-label{max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px;font-weight:700;line-height:1.15}
.act-state{max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11px;font-weight:600;color:var(--secondary-text-color)}
.status-connect_to_ocpp{--b:#3498db}.status-one_charge{--b:#2ecc71}.status-stop_charging{--b:#e74c3c}
.act-btn:not(.on):not(.asking) .act-icon ha-icon{color:color-mix(in srgb,var(--b) 70%,var(--secondary-text-color))}
.act-btn.on,.act-btn.asking{border-color:color-mix(in srgb,var(--b) 60%,transparent);background:linear-gradient(145deg,color-mix(in srgb,var(--b) 26%,transparent),color-mix(in srgb,var(--b) 8%,transparent));box-shadow:0 0 12px color-mix(in srgb,var(--b) 30%,transparent)}
.act-btn.on .act-state,.act-btn.asking .act-state{color:var(--b)}
.act-btn.status-stop_charging.asking{--b:#e74c3c;animation:ask-blink 1s ease-in-out infinite}
@keyframes ask-blink{50%{box-shadow:0 0 18px color-mix(in srgb,#e74c3c 55%,transparent)}}
/* Meter: the charger screen's three big numbers. */
.panel.meter{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:4px;padding:4px}
.meter-item{box-sizing:border-box;display:flex;flex-direction:column;align-items:flex-start;min-width:0;padding:3px 8px 4px;margin:0;border:0;border-radius:9px;background:linear-gradient(160deg,rgba(255,255,255,.06),rgba(127,127,127,.04));font-family:inherit;color:inherit;text-align:left;cursor:pointer;-webkit-tap-highlight-color:transparent}
.meter-head{display:flex;align-items:center;gap:3px;font-size:11px;font-weight:600;color:color-mix(in srgb,var(--h) 70%,var(--secondary-text-color))}.meter-head ha-icon{--mdc-icon-size:14px;color:var(--h)}
.meter-value{display:block;max-width:100%;overflow:hidden;white-space:nowrap;font-size:24px;font-weight:800;line-height:1.15;letter-spacing:-.01em;font-variant-numeric:tabular-nums;color:var(--primary-text-color)}
.meter-value small{font-size:.58em;font-weight:600;margin-left:1px;color:var(--secondary-text-color)}
.meter-value.bad{color:#e74c3c}
ha-card[data-state=charging] .meter-value:not(.bad){color:#2ecc71;text-shadow:0 0 12px color-mix(in srgb,#2ecc71 40%,transparent)}
/* Session: one thin row under the meter. */
.sl.session{gap:6px}.sl.session .info-item{flex:1;justify-content:flex-end}
/* Current: a slider anyone can grab. */
.sl.current{min-height:42px}
.sl.current input[type=range]{height:36px}
.sl.current input::-webkit-slider-runnable-track{height:6px;border-radius:6px}
.sl.current input::-webkit-slider-thumb{width:22px;height:22px;margin-top:-8px;border:2px solid #fff}
.sl.current input::-moz-range-thumb{width:20px;height:20px;border:2px solid #fff}
.sl.current input::-moz-range-track,.sl.current input::-moz-range-progress{height:6px}
.sl.current .mk{top:7px;height:22px}
.sv{font-size:17px}
/* Steppers: wider +/−, taller value. */
.limit-value{grid-template-columns:18px minmax(0,1fr) 18px;height:26px}
.limit-value>button[data-limit-step]{position:relative;width:18px;font-size:20px;font-weight:500;border-radius:7px}
.limit-value>button[data-limit-step]::after{content:'';position:absolute;inset:-4px -2px}
.limit-value>button[data-limit-step]:active{background:rgba(127,127,127,.2)}
.limit-toggle{height:16px;font-size:10.5px;letter-spacing:0;padding:0 1px 0 3px}.limit-toggle i{width:8px;height:8px}
.disable-all{height:24px;font-size:12.5px}
/* Battery: 4.24.0 pair + bar, coloured by SOC, glowing while charging. */
.panel.soc{flex-direction:column;align-items:stretch;gap:6px;padding:4px 4px 7px}
.soc-tile{gap:1px;padding:4px 9px 5px}
.soc-tile .tile-head{font-size:11.5px}.soc-tile .tile-head ha-icon{--mdc-icon-size:16px;color:var(--h)}
.soc-tile .tile-big{font-size:22px;font-weight:800;align-items:baseline}.soc-tile .tile-big b{color:var(--h)}
.panel.soc.soc-idle .soc-tile .tile-big b,.panel.soc.soc-off .soc-tile .tile-big b{color:var(--primary-text-color)}
.soc-from{flex:none;font-size:15px;font-weight:600;color:var(--secondary-text-color)}.soc-from small{font-size:.7em}
.soc-arrow{flex:none;font-style:normal;font-size:14px;margin:0 4px;color:var(--secondary-text-color)}
.soc-tile .tile-sub{font-size:12px}
ha-card[data-state=charging] .panel.soc:not(.soc-reached) .soc-tile{border-color:color-mix(in srgb,var(--h) 50%,transparent);background:linear-gradient(145deg,color-mix(in srgb,var(--h) 20%,transparent),color-mix(in srgb,var(--h) 5%,transparent));box-shadow:0 0 10px color-mix(in srgb,var(--h) 22%,transparent)}
.panel.soc.soc-reached .soc-tile{border-color:color-mix(in srgb,#a78bfa 45%,transparent)}
.soc-bar{position:relative;height:8px;margin:0 6px;border-radius:4px;background:rgba(127,127,127,.2)}
.soc-base{position:absolute;left:0;top:0;bottom:0;border-radius:4px 0 0 4px;background:rgba(127,127,127,.42)}
.soc-fill{position:absolute;top:0;bottom:0;border-radius:0 4px 4px 0;background:linear-gradient(90deg,color-mix(in srgb,var(--h) 55%,transparent),var(--h));box-shadow:0 0 8px color-mix(in srgb,var(--h) 60%,transparent)}
ha-card[data-state=charging] .soc-fill{animation:icon-glow 2.6s ease-in-out infinite}
.soc-target{position:absolute;top:-4px;width:3px;height:16px;border-radius:2px;background:var(--primary-text-color);transform:translateX(-50%);box-shadow:0 0 0 1px rgba(0,0,0,.35)}
.limits.controls .limit-toggle{color:color-mix(in srgb,var(--h) 80%,var(--secondary-text-color))}
/* Adaptive: named, mode on the right, what it does underneath. */
.panel.adaptive{gap:2px;padding:3px 4px 3px 3px}
.panel-head{display:flex;align-items:center;justify-content:space-between;gap:6px;min-height:28px}
.panel-title{display:flex;align-items:center;gap:6px;min-width:0;font-size:14px;white-space:nowrap}.panel-title b{font-weight:600;overflow:hidden;text-overflow:ellipsis}
.adaptive-mode{height:30px;padding:0 10px;font-size:13.5px;border:1px solid color-mix(in srgb,var(--h) 45%,transparent);background:color-mix(in srgb,var(--h) 14%,transparent)}
.adaptive-row{display:flex;align-items:center;gap:5px;min-height:30px;padding:0 4px 0 6px}
.adaptive-hint{flex:0 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;font-size:12px;color:var(--secondary-text-color);white-space:nowrap}
.adaptive-threshold{flex:none;width:104px}.adaptive-threshold .limit-value{height:28px}
.adaptive-cap{display:flex;align-items:baseline;gap:4px;margin:0 0 0 auto;padding:0;border:0;background:transparent;font-family:inherit;color:inherit;cursor:pointer;white-space:nowrap}
.adaptive-cap span{font-size:12.5px;color:var(--secondary-text-color)}.adaptive-cap b{font-size:17px;font-weight:750;color:var(--h)}.adaptive-cap small{font-size:.7em;font-weight:500;color:var(--secondary-text-color)}
/* History: labelled counters, reset asks in a full-width confirm. */
.hist-tile{padding:3px 3px 4px 7px;cursor:default}.hist-tile .tile-head{gap:2px;letter-spacing:0}
.hist-tile .tile-head{min-height:22px}
.tile-body{all:unset;box-sizing:border-box;display:flex;flex-direction:column;width:100%;min-width:0;cursor:pointer;-webkit-tap-highlight-color:transparent}
.hist-tile .tile-big{font-size:18px}.hist-tile .tile-sub{font-size:12px}
.reset-btn{position:relative;flex:none;display:flex;align-items:center;justify-content:center;width:22px;height:22px;margin:0 0 0 auto;padding:0;border:0;border-radius:7px;background:rgba(127,127,127,.12);color:var(--secondary-text-color);cursor:pointer}
.reset-btn::after{content:'';position:absolute;inset:-6px 0}
.reset-btn ha-icon{--mdc-icon-size:15px;color:inherit}
.reset-ask{display:flex;align-items:center;gap:6px;min-height:52px;padding:0 4px 0 6px}
.reset-ask>ha-icon{--mdc-icon-size:22px;color:#e74c3c}
.reset-text{display:flex;flex-direction:column;flex:1;min-width:0;line-height:1.25}.reset-text b{font-size:14px}.reset-text small{font-size:11.5px;color:var(--secondary-text-color)}
.reset-no,.reset-yes{flex:none;height:34px;padding:0 12px;border:1px solid rgba(127,127,127,.3);border-radius:10px;background:rgba(127,127,127,.12);font-family:inherit;font-size:13px;font-weight:700;color:inherit;cursor:pointer}
.reset-yes{border-color:#e74c3c;background:#e74c3c;color:#fff}
.panel.history.asking-reset{--h:#e74c3c}

.tile-unit{margin-left:2px;font-size:.85em;font-weight:500;opacity:.75;text-transform:none}
.limit-toggle{position:relative}.limit-toggle i{position:absolute;top:4px;right:2px;margin:0}
/* The on/off dot sits over the header's right edge; keep the unit clear of it. */
button.limit-toggle>span{box-sizing:border-box;padding-right:9px}
@container (max-width:340px){.limit-toggle{font-size:10px}.tile-unit{margin-left:1px}}
.soc-finish{min-width:0;overflow:hidden;white-space:nowrap}
/* ---- Longer Ukrainian words shrink to fit instead of clipping at 320 px. ---- */
.hist-label{flex:0 1 auto;min-width:0;overflow:hidden;white-space:nowrap}
/* Three equal thirds: one long reading (a Ukrainian "5 год 30 хв") no longer squeezes the other two. */
.session-row .info-item,.session-row .info-item.grow{flex:1 1 0;min-width:0;justify-content:center}
/* A narrow card asks on one line and answers on the next, instead of a four-line sliver. */
@container (max-width:340px){.reset-ask{flex-wrap:wrap;row-gap:6px;padding:6px}.reset-ask>ha-icon{display:none}.reset-text{flex-basis:100%}.reset-no,.reset-yes{flex:1}}
/* ---- Icon-left buttons, centred meter, spread session, labelled safety. ---- */
.act-btn{flex-direction:row;justify-content:flex-start;align-items:center;gap:7px;min-height:44px;padding:4px 8px 4px 7px;text-align:left}
.act-icon{flex:none;height:auto}.act-icon ha-icon{--mdc-icon-size:26px}.act-stop-icon{width:26px;height:26px}
.act-text{display:flex;flex-direction:column;align-items:flex-start;flex:1;min-width:0;line-height:1.2}
.act-label,.act-state{display:block;max-width:100%;overflow:hidden;white-space:nowrap;text-overflow:clip}
.act-label{font-size:14px}
.meter-item{align-items:center;text-align:center;padding:3px 4px 4px}
.meter-head{justify-content:center}
.sl.session{gap:6px}.session-row{flex:1;justify-content:space-around;gap:4px}
.session-row .info-item{flex:0 1 auto}.session-row .info-item ha-icon{--mdc-icon-size:15px;color:color-mix(in srgb,var(--h) 80%,var(--secondary-text-color))}
.sl.safety{gap:6px}.safety-label{flex:none}
@container (max-width:350px){.safety-label{display:none}}
@container (max-width:370px){.session-row .info-item ha-icon{display:none}}
.session-row .info-item.grow{flex:0 1 auto;min-width:0}
[data-hold]{position:relative;-webkit-user-select:none;user-select:none;-webkit-touch-callout:none}
.soc-tile[data-hold]::before{content:"";position:absolute;right:5px;bottom:5px;width:4px;height:4px;border-radius:50%;background:var(--h);opacity:.6}
`;

const ADVANCED_SECTIONS = ['advanced_info', 'advanced_controls'];

class EveusCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = {...config, sections: [...sectionsOf(config)]};
    this._resolve();
    this._render();
  }
  set hass(hass) { this._hass = hass; this._resolve(); this._render(); }

  // Same rule as the card: an explicit mode wins, otherwise Advanced exactly when the
  // integration publishes an SOC. Until the lookup answers, nothing is greyed.
  _resolve() {
    const want = this._config?.device_id || null;
    if (!this._hass?.callWS || this._config?.mode || this._socFor === want) return;
    this._socFor = want;
    const msg = {type: 'eveus/card_entities'};
    if (want) msg.device_id = want;
    this._hass.callWS(msg)
      .then((res) => { this._hasSoc = !!res?.entities?.soc_percent; })
      .catch(() => { this._hasSoc = true; })
      .finally(() => this._render());
  }
  get _basic() { return this._config.mode ? this._config.mode === 'basic' : this._hasSoc === false; }
  // Basic mode has no SOC: those rows stay in the config (back on switching to Advanced) but
  // are greyed, last, and out of the ordering.
  _na(key) { return this._basic && ADVANCED_SECTIONS.includes(key); }
  get _usable() { return this._config.sections.filter((s) => !this._na(s)); }

  // The editor speaks Home Assistant's language; the card's own `language` is a setting in it.
  _labels() { return EDITOR_I18N[langOf(null, this._hass)]; }
  _schema() {
    const l = this._labels();
    return [
      {name: 'device_id', selector: {device: {integration: 'eveus'}}},
      {name: 'mode', selector: {select: {options: [{value: 'advanced', label: l.advanced}, {value: 'basic', label: l.basic}]}}},
      {name: 'language', selector: {select: {mode: 'dropdown', options: Object.entries(l.languages).map(([value, label]) => ({value, label}))}}},
    ];
  }
  _sectionsHtml() {
    const l = this._labels(), on = this._config.sections, usable = this._usable;
    const rows = [...usable, ...SECTIONS.filter((s) => !on.includes(s) && !this._na(s)), ...SECTIONS.filter((s) => this._na(s))];
    return rows.map((key) => {
      const [name, hint] = l.names[key], shown = on.includes(key), i = usable.indexOf(key);
      if (this._na(key)) {
        return `<div class="row na"><label><input type="checkbox" data-toggle="${key}"${shown ? ' checked' : ''} disabled><span>${name}<small>${l.advOnly}</small></span></label></div>`;
      }
      const lock = shown && usable.length === 1 ? ' disabled' : '';
      const move = (dir, off) => `<button type="button" data-move="${key}" data-dir="${dir}"${off ? ' disabled' : ''} title="${dir < 0 ? l.up : l.down}"><ha-icon icon="mdi:chevron-${dir < 0 ? 'up' : 'down'}"></ha-icon></button>`;
      return `<div class="row${shown ? '' : ' off'}"><label><input type="checkbox" data-toggle="${key}"${shown ? ' checked' : ''}${lock}><span>${name}${hint ? `<small>${hint}</small>` : ''}</span></label>`
        + (shown ? move(-1, i === 0) + move(1, i === usable.length - 1) : '') + '</div>';
    }).join('');
  }
  _emit(sections) {
    // The first edit turns a saved layout into the sections it stood for.
    const {layout, ...rest} = this._config;
    this._config = {...rest, sections};
    this._render();
    this.dispatchEvent(new CustomEvent('config-changed', {detail: {config: this._config}, bubbles: true, composed: true}));
  }
  _toggle(key) {
    const on = this._config.sections;
    if (this._na(key)) return;
    if (on.includes(key)) { if (this._usable.length > 1) this._emit(on.filter((s) => s !== key)); }
    else this._emit([...on, key]);
  }
  // Swaps with the next usable neighbour, so a greyed SOC row in between never eats a tap.
  _move(key, dir) {
    const on = [...this._config.sections], usable = this._usable, k = usable.indexOf(key), other = usable[k + dir];
    if (k < 0 || !other) return;
    const i = on.indexOf(key), j = on.indexOf(other);
    [on[i], on[j]] = [on[j], on[i]];
    this._emit(on);
  }
  _resetOrder() { this._emit([...SECTIONS]); }

  _render() {
    if (!this._config || typeof document === 'undefined') return;
    if (!this._list) {
      this.innerHTML = `<style>${EDITOR_CSS}</style><div class="form"></div><div class="head"><b></b><button type="button" class="reset"></button></div><div class="list"></div>`;
      this._list = this.querySelector('.list');
      this.addEventListener('click', (e) => {
        const b = e.target.closest?.('button');
        if (b?.dataset.move) this._move(b.dataset.move, Number(b.dataset.dir));
        else if (b?.classList.contains('reset')) this._resetOrder();
      });
      this.addEventListener('change', (e) => { if (e.target.dataset?.toggle) this._toggle(e.target.dataset.toggle); });
    }
    const l = this._labels();
    this.querySelector('.head b').textContent = l.head;
    this.querySelector('.head .reset').textContent = l.reset;
    this._list.innerHTML = this._sectionsHtml();
    if (!this._hass) return;
    if (!this._form) {
      this._form = document.createElement('ha-form');
      this._form.computeLabel = (f) => ({device_id: this._labels().device, mode: this._labels().mode, language: this._labels().language})[f.name];
      this._form.computeHelper = (f) => f.name === 'mode' ? this._labels().modeHelp : undefined;
      this._form.addEventListener('value-changed', (e) => {
        e.stopPropagation();
        const {device_id, mode, language} = e.detail.value, next = {...this._config};
        // "auto" is the default, so it is not written into the card's YAML.
        for (const [k, v] of Object.entries({device_id, mode, language})) { if (v && v !== 'auto') next[k] = v; else delete next[k]; }
        this._config = next;
        this._resolve();
        this._render();
        this.dispatchEvent(new CustomEvent('config-changed', {detail: {config: next}, bubbles: true, composed: true}));
      });
      this.querySelector('.form').appendChild(this._form);
    }
    this._form.hass = this._hass;
    this._form.schema = this._schema();
    this._form.data = {device_id: this._config.device_id, mode: this._config.mode, language: this._config.language || 'auto'};
  }
}
const EDITOR_CSS = `
.form{margin-bottom:12px}
.head{display:flex;align-items:center;justify-content:space-between;margin:0 0 6px}
.head button{border:0;background:none;color:var(--primary-color);font:inherit;cursor:pointer}
.row{display:flex;align-items:center;gap:4px;min-height:44px;border-bottom:1px solid var(--divider-color)}
.row label{display:flex;align-items:center;gap:10px;flex:1;min-width:0;cursor:pointer}
.row input{width:20px;height:20px;margin:0;accent-color:var(--primary-color)}
.row span{display:flex;flex-direction:column}.row small{color:var(--secondary-text-color)}
.row.off span{opacity:.55}.row.na{opacity:.4}.row.na label{cursor:default}
.row button{display:flex;width:40px;height:40px;align-items:center;justify-content:center;border:0;border-radius:50%;background:none;color:var(--primary-text-color);cursor:pointer}
.row button:disabled{opacity:.25;cursor:default}
`;
// The integration and a hand-added resource may both load this file: first copy wins.
if (!customElements.get?.('eveus-card')) {
  customElements.define('eveus-card', EveusCard);
  customElements.define('eveus-card-editor', EveusCardEditor);
}
window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === 'eveus-card')) {
  window.customCards.push({type: 'eveus-card', name: 'Eveus EV Charger', description: 'Mobile-first Eveus card: pick, hide and reorder its sections', preview: true});
}
