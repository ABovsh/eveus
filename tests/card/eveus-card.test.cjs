const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../../custom_components/eveus/frontend/eveus-card.js'), 'utf8');
function setup(max = 16, step = 1) {
  const registry = {}, timers = new Map(); let timer = 0;
  class HTMLElement {
    attachShadow() { return this.shadowRoot = {innerHTML:'', addEventListener(){}, querySelector(){return null;}}; }
  }
  const sandbox = {HTMLElement, console, setTimeout(fn){timers.set(++timer, fn);return timer;}, clearTimeout(id){timers.delete(id);},
    customElements:{define:(k,v)=>registry[k]=v}, window:{customCards:[]}};
  vm.runInNewContext(source, sandbox);
  const card = new registry['eveus-card'](); card.setConfig({sections:['current']});
  const st = (state,attributes={})=>({state:String(state),attributes});
  const states = {'sensor.state':st('Charging'),'number.amps':st(10,{min:6,max,step}),'sensor.amps':st(8.4)};
  const calls=[];
  const hass = {states,callWS:async()=>({entities:{state:'sensor.state',charging_current:'number.amps',current:'sensor.amps'}}),
    callService:async(...args)=>{calls.push(args);}};
  card._ids={state:'sensor.state',charging_current:'number.amps',current:'sensor.amps'};card._resolved=true;card.hass=hass;
  const slide=(v,commit=true)=>card._onSlide({target:{dataset:{slide:'current'},value:String(v)}},commit);
  return {card,states,calls,hass,slide,timers};
}
for(const max of [16,32,40,48]) test(`model maximum ${max} A and actual current`,()=>{
  const {card,slide,calls}=setup(max);
  assert.match(card.shadowRoot.innerHTML,new RegExp(`max="${max}"`));
  assert.match(card.shadowRoot.innerHTML,/8.4 A/);
  slide(max+1); assert.equal(calls.length,0); assert.equal(card._draft,null);
});
test('increase waits for confirmation; decrease sends once; rendering never sends', async()=>{
  const {card,slide,calls,hass}=setup(32);
  card.hass=hass; assert.equal(calls.length,0);
  slide(24,false); slide(24); assert.equal(calls.length,0);assert.equal(card._asking,true);
  await card._confirm();assert.equal(calls.length,1);assert.equal(calls[0][2].value,24);
  await card._confirm();assert.equal(calls.length,1);
  const x=setup(); x.slide(8);assert.equal(x.calls.length,1);assert.equal(x.calls[0][2].value,8);
});
test('unanswered confirmation expires',()=>{
  const {card,slide,timers,calls}=setup();slide(14);
  for(const fn of [...timers.values()]) fn();
  assert.equal(card._draft,null);assert.equal(card._asking,false);assert.equal(calls.length,0);
});
test('bounds update without a state change cancels unsafe confirmation',async()=>{
  const {card,slide,states,hass,calls}=setup(32);slide(24);
  states['number.amps'].attributes.max=16;card.hass=hass;
  assert.match(card.shadowRoot.innerHTML,/max="16"/);
  await card._confirm();assert.equal(calls.length,0);
});
test('offline or unavailable number cannot send a pending confirmation',async()=>{
  for(const id of ['sensor.state','number.amps']) {
    const {card,slide,states,hass,calls}=setup();slide(14);states[id].state='unavailable';card.hass=hass;
    await card._confirm();slide(8);assert.equal(calls.length,0);assert.match(card.shadowRoot.innerHTML,/disabled/);
  }
});
test('missing bounds disable; fractional step is enforced',()=>{
  const x=setup();delete x.states['number.amps'].attributes.max;x.card.hass=x.hass;x.slide(14);
  assert.equal(x.calls.length,0);assert.match(x.card.shadowRoot.innerHTML,/disabled/);
  const y=setup(16,.5);y.slide(10.2);assert.equal(y.card._draft,null);y.slide(10.5);assert.equal(y.card._asking,true);
});
test('actual-current tick shows only while charging with a non-zero current; draft does not replace it',()=>{
  const {card,states,hass,slide}=setup();slide(14);assert.match(card.shadowRoot.innerHTML,/title="Actual current 8.4 A"/);
  states['sensor.amps'].state='0';card.hass=hass;assert.match(card.shadowRoot.innerHTML,/class="markers"><\/div>/);
  states['sensor.amps'].state='unavailable';card.hass=hass;assert.match(card.shadowRoot.innerHTML,/class="markers"><\/div>/);
  states['sensor.amps'].state='8.4';states['sensor.state'].state='Connected';card.hass=hass;assert.match(card.shadowRoot.innerHTML,/class="markers"><\/div>/);
});
test('disconnect cancels confirmation and draft',async()=>{
  const {card,slide,calls}=setup();slide(14);card.disconnectedCallback();await card._confirm();assert.equal(calls.length,0);
});
test('rejected service restores actual setting and shows an error',async()=>{
  const {card,slide,hass}=setup();hass.callService=async()=>{throw Error('rejected');};slide(14);await card._confirm();
  assert.equal(card._draft,null);assert.match(card.shadowRoot.innerHTML,/Could not set current/);
});

test('module label stays Current and actual reading is only a scale marker',()=>{
  const {card,states,hass}=setup();
  assert.match(card.shadowRoot.innerHTML,/class="mk"/);
  assert.doesNotMatch(card.shadowRoot.innerHTML,/class="actual"/);
  states['sensor.state'].state='unavailable';card.hass=hass;
  assert.match(card.shadowRoot.innerHTML,/class="label">Current</);
});

function setupLimits({advanced=true,suspended=false,online=true}={}) {
  const registry = {};
  class HTMLElement {
    attachShadow() { return this.shadowRoot = {innerHTML:'', addEventListener(){}, querySelector(){return null;}}; }
  }
  const sandbox = {HTMLElement, console, setTimeout, clearTimeout,
    customElements:{define:(k,v)=>registry[k]=v}, window:{customCards:[]}};
  vm.runInNewContext(source, sandbox);
  const card = new registry['eveus-card'](); card.setConfig({sections:['limits']});
  const st = (state,attributes={})=>({state:String(state),attributes});
  const states = {
    'sensor.state':st(online?'Charging':'unavailable'),
    'switch.disable':st(suspended?'on':'off'),
    'switch.energy':st('on'),'switch.time':st('off'),'switch.cost':st('on'),
    'number.energy':st(25,{min:0,max:100,step:1,unit_of_measurement:'kWh'}),
    'number.time':st(120,{min:0,max:1440,step:5,unit_of_measurement:'min'}),
    'number.cost':st(500,{min:0,max:10000,step:1,unit_of_measurement:'UAH'}),
  };
  const entities = {state:'sensor.state',limit_disable_all:'switch.disable',
    limit_energy_enabled:'switch.energy',limit_time_enabled:'switch.time',limit_cost_enabled:'switch.cost',
    limit_energy:'number.energy',limit_time:'number.time',limit_cost:'number.cost'};
  if (advanced) {
    states['switch.soc']=st('on');states['number.soc']=st(80,{min:0,max:100,step:1,unit_of_measurement:'%'});
    entities.limit_soc_enabled='switch.soc';entities.target_soc='number.soc';
    states['sensor.soc_pct']=st(44,{unit_of_measurement:'%'});entities.soc_percent='sensor.soc_pct'; // Advanced = the integration publishes an SOC
  }
  const calls=[];
  const hass={states,callWS:async()=>({entities}),callService:async(...args)=>{calls.push(args);}};
  card._ids=entities;card._resolved=true;card.hass=hass;
  return {card,states,calls,hass};
}

test('limits render four compact peer tiles in one row with the global override above',()=>{
  const {card}=setupLimits();
  const html=card.shadowRoot.innerHTML;
  assert.match(html,/class="limits-grid four /);
  assert.equal((html.match(/class="limit-tile/g)||[]).length,4);
  assert.match(html,/Disable all/);
  assert.match(html,/SOC/);assert.match(html,/Energy/);assert.match(html,/Time/);assert.match(html,/Cost/);
  // Units ride in the tile header so the number keeps its full size in a 4-wide row.
  assert.match(html,/Time<small class="tile-unit">min<\/small>[^]*data-fit>120<\/b>/);
});

test('Disable all calls only the existing global override and visibly suspends saved limits',async()=>{
  const {card,calls}=setupLimits({suspended:true});
  assert.match(card.shadowRoot.innerHTML,/limits-grid four suspended/);
  assert.doesNotMatch(card.shadowRoot.innerHTML,/limit-tile active/);
  await card._toggleLimit('limit_disable_all');
  assert.equal(JSON.stringify(calls),JSON.stringify([['switch','turn_off',{entity_id:'switch.disable'}]]));
});

test('limit toggles and thresholds use their existing switch and number entities',async()=>{
  const {card,calls}=setupLimits();
  await card._toggleLimit('limit_time_enabled');
  await card._stepLimit('limit_time',1);
  assert.equal(JSON.stringify(calls[0]),JSON.stringify(['switch','turn_on',{entity_id:'switch.time'}]));
  assert.equal(JSON.stringify(calls[1]),JSON.stringify(['number','set_value',{entity_id:'number.time',value:125}]));
});

test('Basic mode omits unsupported SOC and lets the three native limits fill the row',()=>{
  const {card}=setupLimits({advanced:false});
  const html=card.shadowRoot.innerHTML;
  assert.match(html,/class="limits-grid three /);
  assert.equal((html.match(/class="limit-tile/g)||[]).length,3);
  assert.doesNotMatch(html,/>SOC</);
});

test('offline limits remain informative but cannot operate',async()=>{
  const {card,calls}=setupLimits({online:false});
  assert.match(card.shadowRoot.innerHTML,/class="limits off"/);
  await card._toggleLimit('limit_energy_enabled');
  await card._stepLimit('limit_energy',1);
  assert.equal(calls.length,0);
});

test('tapping a limit value opens an inline editor bound to its entity bounds',()=>{
  const {card}=setupLimits();
  card._startLimitEdit('limit_time');
  const html=card.shadowRoot.innerHTML;
  assert.match(html,/data-limit-edit="limit_time"/);
  assert.match(html,/value="120"/);
  assert.match(html,/min="0"/);
  assert.match(html,/max="1440"/);
  assert.match(html,/step="5"/);
});

test('committing an inline edit rounds to the nearest step and clamps to bounds, then sends once',async()=>{
  const {card,calls}=setupLimits();
  card._startLimitEdit('limit_time');
  await card._commitLimitEdit('limit_time',503);
  assert.equal(JSON.stringify(calls[0]),JSON.stringify(['number','set_value',{entity_id:'number.time',value:505}]));
  assert.equal(card._editingLimit,null);
  const {card:c2,calls:c2calls}=setupLimits();
  c2._startLimitEdit('limit_cost');
  await c2._commitLimitEdit('limit_cost',99999);
  assert.equal(JSON.stringify(c2calls[0]),JSON.stringify(['number','set_value',{entity_id:'number.cost',value:10000}]));
});

test('an unparsable or unchanged inline edit sends nothing',async()=>{
  const {card,calls}=setupLimits();
  card._startLimitEdit('limit_energy');
  await card._commitLimitEdit('limit_energy','abc');
  assert.equal(calls.length,0);
  card._startLimitEdit('limit_energy');
  await card._commitLimitEdit('limit_energy',25);
  assert.equal(calls.length,0);
});

test('offline cannot open the inline editor',()=>{
  const {card}=setupLimits({online:false});
  card._startLimitEdit('limit_energy');
  assert.equal(card._editingLimit,null);
});

function setupSafety({online=true,box=20,plug=12,leak=0,conn=98,ground='Connected',protection='on'}={}) {
  const registry = {};
  class HTMLElement extends EventTarget {
    attachShadow() { return this.shadowRoot = {innerHTML:'', addEventListener(){}, querySelector(){return null;}}; }
  }
  const sandbox = {HTMLElement, CustomEvent, console, setTimeout, clearTimeout,
    customElements:{define:(k,v)=>registry[k]=v}, window:{customCards:[]}};
  vm.runInNewContext(source, sandbox);
  const card = new registry['eveus-card'](); card.setConfig({sections:['safety']});
  const st = (state,attributes={})=>({state:String(state),attributes});
  const states = {
    'sensor.state':st(online?'Charging':'unavailable'),
    'sensor.box':st(box,{unit_of_measurement:'°C'}),
    'sensor.plug':st(plug,{unit_of_measurement:'°C'}),
    'sensor.leak':st(leak,{unit_of_measurement:'mA'}),
    'sensor.conn':st(conn,{unit_of_measurement:'%'}),
    'sensor.ground':st(ground),
    'switch.protection':st(protection),
  };
  const entities = {state:'sensor.state',box_temperature:'sensor.box',plug_temperature:'sensor.plug',
    leakage_current:'sensor.leak',connection_quality:'sensor.conn',ground:'sensor.ground',ground_protection:'switch.protection'};
  const calls=[];
  const hass={states,callWS:async()=>({entities}),callService:async(...args)=>{calls.push(args);}};
  card._ids=entities;card._resolved=true;card.hass=hass;
  return {card,states,calls,hass};
}

test('safety is one thin row (the .sl pattern, not a boxed limits grid), icon-identified items',()=>{
  const {card}=setupSafety();
  const html=card.shadowRoot.innerHTML;
  assert.match(html,/class="sl safety /);
  assert.doesNotMatch(html,/class="limits-grid/);
  assert.doesNotMatch(html,/class="limit-tile/);
  assert.match(html,/title="Box temperature/);assert.match(html,/title="Plug temperature/);
  assert.match(html,/title="Ground: tap to arm\/disarm protection"/);assert.match(html,/title="Leakage current/);
  assert.match(html,/20°/);assert.match(html,/12°/);assert.match(html,/>OK</);assert.match(html,/0<small>mA/);
});

test('normal readings render the good color, not bad',()=>{
  const {card}=setupSafety();
  const html=card.shadowRoot.innerHTML;
  assert.doesNotMatch(html,/safety-value bad/);
  assert.doesNotMatch(html,/<i class="bad">/);
});

test('high temperature turns only that reading bad, the other stays good',()=>{
  const {card}=setupSafety({box:85,plug:12});
  const html=card.shadowRoot.innerHTML;
  assert.match(html,/safety-value bad">85°/);
  assert.match(html,/safety-value good">12°/);
});

test('high leakage current turns bad',()=>{
  const {card}=setupSafety({leak:32});
  assert.match(card.shadowRoot.innerHTML,/safety-value bad">32<small>mA/);
});

test('ground not connected is bad even while protection stays on',()=>{
  const {card}=setupSafety({ground:'Not Connected'});
  const html=card.shadowRoot.innerHTML;
  assert.match(html,/safety-value bad">Bad</);
  assert.match(html,/<i class="good">/);
});

test('ground protection off marks its dot bad and the toggle unchecked',()=>{
  const {card}=setupSafety({protection:'off'});
  const html=card.shadowRoot.innerHTML;
  assert.match(html,/<i class="bad">/);
  assert.match(html,/aria-checked="false"/);
});

test('toggling ground protection uses the existing switch entity',async()=>{
  const {card,calls}=setupSafety({protection:'off'});
  await card._toggleLimit('ground_protection');
  assert.equal(JSON.stringify(calls),JSON.stringify([['switch','turn_on',{entity_id:'switch.protection'}]]));
});

test('unknown readings show a dash with no color class',()=>{
  const {card,states,hass}=setupSafety();
  states['sensor.box'].state='unavailable';
  states['sensor.ground'].state='unavailable';
  states['switch.protection'].state='unavailable';
  card.hass=hass;
  const html=card.shadowRoot.innerHTML;
  assert.match(html,/safety-value ">—°/);
  assert.match(html,/safety-value ">—</);
  assert.match(html,/aria-label="Ground protection"[^>]*\bdisabled\b/);
});

test('offline safety row is visibly disabled and cannot toggle',async()=>{
  const {card,calls}=setupSafety({online:false});
  assert.match(card.shadowRoot.innerHTML,/class="sl safety off"/);
  await card._toggleLimit('ground_protection');
  assert.equal(calls.length,0);
});

test('every item has its own icon, matched to the entity it represents',()=>{
  const {card}=setupSafety();
  const html=card.shadowRoot.innerHTML;
  for (const icon of ['mdi:ev-station"','mdi:ev-plug-type2"','mdi:current-dc"']) {
    assert.match(html,new RegExp(icon.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')));
  }
  assert.match(html,/class="safety-ground-icon"/);
});

test('Connection quality icon reflects its own Excellent/Good/Fair/Poor/Critical brackets, independent of the good/bad text color',()=>{
  assert.match(setupSafety({conn:98}).card.shadowRoot.innerHTML,/mdi:wifi-strength-4"/);
  assert.match(setupSafety({conn:90}).card.shadowRoot.innerHTML,/mdi:wifi-strength-3"/);
  assert.match(setupSafety({conn:70}).card.shadowRoot.innerHTML,/mdi:wifi-strength-2"/);
  assert.match(setupSafety({conn:50}).card.shadowRoot.innerHTML,/mdi:wifi-strength-1"/);
  assert.match(setupSafety({conn:10}).card.shadowRoot.innerHTML,/mdi:wifi-strength-alert-outline"/);
});

test('tapping a read-only item fires hass-more-info for its own entity; tapping Ground still toggles',()=>{
  const {card}=setupSafety();
  const events=[];
  card.addEventListener('hass-more-info',(e)=>events.push(e.detail.entityId));
  card._moreInfo('box_temperature');
  card._moreInfo('leakage_current');
  card._moreInfo('connection_quality');
  assert.deepEqual(events,['sensor.box','sensor.leak','sensor.conn']);
  card._moreInfo('not_a_real_key');
  assert.equal(events.length,3);
});

test('Connection quality renders alongside the other readings, colored by its own metric, and never affects the card-wide alert',()=>{
  const strong=setupSafety({conn:98});
  assert.match(strong.card.shadowRoot.innerHTML,/safety-value good">98<small>%/);
  const weak=setupSafety({conn:40});
  assert.match(weak.card.shadowRoot.innerHTML,/safety-value bad">40<small>%/);
  assert.equal(weak.card._safetyStatus(),'good');
});

test('safety row tint: good when everything is fine, bad the moment one reading trips, none when offline — scoped to the row, not the whole card',()=>{
  const good=setupSafety();
  assert.match(good.card.shadowRoot.innerHTML,/class="sl safety safety-good"/);
  assert.doesNotMatch(good.card.shadowRoot.innerHTML,/<ha-card class=/);
  const bad=setupSafety({box:85});
  assert.match(bad.card.shadowRoot.innerHTML,/class="sl safety safety-bad"/);
  assert.doesNotMatch(bad.card.shadowRoot.innerHTML,/<ha-card class=/);
  const offline=setupSafety({online:false});
  assert.doesNotMatch(offline.card.shadowRoot.innerHTML,/class="[^"]*safety-(good|bad)/);
});

// ---------- Status, Actions and the card-level alert strip ----------
function setupStatus({state='Charging',reason='Charging',substate='No Limits',stop='off',one='off',ocpp='off',sections=['status','actions'],changed}={}) {
  const registry = {}, timers = new Map(); let timer = 0;
  class HTMLElement extends EventTarget {
    attachShadow() { return this.shadowRoot = {innerHTML:'', addEventListener(){}, querySelector(){return null;}}; }
  }
  const sandbox = {HTMLElement, CustomEvent, console, Date, setTimeout(fn){timers.set(++timer, fn);return timer;}, clearTimeout(id){timers.delete(id);},
    customElements:{define:(k,v)=>registry[k]=v}, window:{customCards:[]}};
  vm.runInNewContext(source, sandbox);
  const card = new registry['eveus-card'](); card.setConfig({sections});
  const st = (s,attributes={})=>({state:String(s),attributes,last_changed:changed});
  const states = {'sensor.state':st(state),'sensor.reason':st(reason),'sensor.sub':st(substate),
    'switch.stop':st(stop),'switch.one':st(one),'switch.ocpp':st(ocpp)};
  const entities = {state:'sensor.state',not_charging_reason:'sensor.reason',substate:'sensor.sub',
    stop_charging:'switch.stop',one_charge:'switch.one',connect_to_ocpp:'switch.ocpp'};
  const calls=[];
  const hass={states,callWS:async()=>({entities}),callService:async(...args)=>{calls.push(args);}};
  card._ids=entities;card._resolved=true;card.hass=hass;
  const html=()=>card.shadowRoot.innerHTML.replace(/<style>[\s\S]*?<\/style>/,'');
  return {card,states,calls,hass,timers,html};
}

test('status is one full-width row with state and note; OCPP/One/Stop live in their own Actions section',()=>{
  const s=setupStatus({sections:['status']}).html();
  assert.match(s,/class="sl status/);
  assert.match(s,/>Charging</);
  assert.doesNotMatch(s,/data-status-toggle/);
  const a=setupStatus({sections:['actions']}).html();
  assert.match(a,/class="panel actions/);
  for(const k of ['connect_to_ocpp','one_charge','stop_charging']) assert.match(a,new RegExp(`data-status-toggle="${k}"`));
  assert.match(a,/>OCPP<[^]*>One charge<[^]*>Stop</);
  assert.doesNotMatch(s+a,/limit_disable_all|Unlimited|No limit/);
});

test('actions say their state in words; a stopped charger offers resume',()=>{
  const a=setupStatus({sections:['actions'],state:'Standby',stop:'on',one:'on'}).html();
  assert.match(a,/act-btn status-one_charge on[^]*>On</);
  assert.match(a,/act-btn status-stop_charging on[^]*>Stopped<[^]*Tap to resume/);
  assert.match(a,/status-connect_to_ocpp[^]*>Off</);
  assert.match(setupStatus({sections:['actions']}).html(),/status-stop_charging[^]*>Stop<[^]*Tap to stop/);
});

test('state colour class: charging good, stopped paused, error bad',()=>{
  assert.match(setupStatus().html(),/status-state charging/);
  const stopped=setupStatus({state:'Standby',stop:'on',reason:'Stopped by User'}).html();
  assert.match(stopped,/status-state paused/);assert.match(stopped,/>Stopped</);
  assert.match(setupStatus({state:'Error',substate:'Grounding Error'}).html(),/status-state fault/);
});

test('not-charging reason is shown when it adds something, hidden when it repeats the state',()=>{
  assert.match(setupStatus({state:'Connected',reason:'Waiting for Car'}).html(),/status-note[^>]*>Waiting for Car</);
  assert.doesNotMatch(setupStatus({state:'Charge Complete',reason:'Charge Complete'}).html(),/status-note/);
  assert.doesNotMatch(setupStatus().html(),/status-note/);
});

test('error shows its fault as the state text, falling back to Error',()=>{
  assert.match(setupStatus({state:'Error',substate:'Grounding Error'}).html(),/status-state fault">Grounding Error</);
  assert.match(setupStatus({state:'Error',substate:'unknown'}).html(),/status-state fault">Error</);
});

test('OCPP and One toggle their own switches immediately',async()=>{
  const {card,calls}=setupStatus();
  await card._statusToggle('connect_to_ocpp');
  await card._statusToggle('one_charge');
  assert.deepEqual(calls.map(c=>[c[1],c[2].entity_id]),[['turn_on','switch.ocpp'],['turn_on','switch.one']]);
});

test('Stop during charging asks first, confirms on second tap, expires after 4 s',async()=>{
  const {card,calls,timers,html}=setupStatus();
  await card._statusToggle('stop_charging');
  assert.equal(calls.length,0);assert.match(html(),/Sure\?/);
  await card._statusToggle('stop_charging');
  assert.deepEqual(calls.map(c=>[c[1],c[2].entity_id]),[['turn_on','switch.stop']]);
  const x=setupStatus();await x.card._statusToggle('stop_charging');
  for(const fn of [...x.timers.values()]) fn();
  assert.doesNotMatch(x.html(),/Sure\?/);
  await x.card._statusToggle('stop_charging');assert.equal(x.calls.length,0);
});

test('Stop when not charging, and resuming, send without asking',async()=>{
  const idle=setupStatus({state:'Connected'});await idle.card._statusToggle('stop_charging');
  assert.deepEqual(idle.calls.map(c=>c[1]),['turn_on']);
  const stopped=setupStatus({state:'Standby',stop:'on'});await stopped.card._statusToggle('stop_charging');
  assert.deepEqual(stopped.calls.map(c=>c[1]),['turn_off']);
});

test('offline status: readable, buttons disabled, nothing sent',async()=>{
  const {card,calls,html}=setupStatus({state:'unavailable'});
  assert.match(html(),/>Offline</);
  assert.match(html(),/data-status-toggle="stop_charging"[^>]*disabled/);
  await card._statusToggle('stop_charging');assert.equal(calls.length,0);
});

test('alert strip appears at the top when status is hidden and the charger is offline or in error',()=>{
  const ago=new Date(Date.now()-5*60000).toISOString();
  const off=setupStatus({state:'unavailable',sections:['current'],changed:ago}).html();
  assert.match(off,/<ha-card[^>]*><div class="alert-strip offline"/);
  assert.match(off,/Offline · 5m ago/);
  const err=setupStatus({state:'Error',substate:'Box Overheat',sections:['safety']}).html();
  assert.match(err,/<ha-card[^>]*><div class="alert-strip fault"[^]*Box Overheat/);
});

test('no alert strip when healthy, or when the status row already shows the alert',()=>{
  assert.doesNotMatch(setupStatus({sections:['current']}).html(),/alert-strip/);
  assert.doesNotMatch(setupStatus({state:'unavailable',sections:['status','current']}).html(),/alert-strip/);
  assert.doesNotMatch(setupStatus({state:'Error',sections:['current','status']}).html(),/alert-strip/);
});

// ---------- Meter, Battery SOC, SOC settings, Adaptive charging ----------
function setupAll({sections, mode, over={}, charging=true, locale, language, hide, fold}={}) {
  const registry = {}, timers = new Map(); let timer = 0;
  class HTMLElement extends EventTarget {
    attachShadow() { return this.shadowRoot = {innerHTML:'', addEventListener(){}, querySelector(){return null;}, querySelectorAll(){return [];}}; }
  }
  const sandbox = {HTMLElement, CustomEvent, console, Date, setTimeout(fn){timers.set(++timer, fn);return timer;}, clearTimeout(id){timers.delete(id);},
    customElements:{define:(k,v)=>registry[k]=v}, window:{customCards:[]}};
  vm.runInNewContext(source, sandbox);
  const card = new registry['eveus-card'](); card.setConfig({sections, ...(mode ? {mode} : {}), ...(language ? {language} : {}), ...(hide ? {hide} : {}), ...(fold === undefined ? {} : {fold})});
  const st = (s,attributes={})=>({state:String(s),attributes});
  const base = {
    state:['sensor.state', charging ? 'Charging' : 'Connected'],
    power:['sensor.power',3620,{unit_of_measurement:'W'}], current:['sensor.cur',15.9,{unit_of_measurement:'A'}], voltage:['sensor.voltage',227,{unit_of_measurement:'V'}],
    session_energy:['sensor.se',15.58,{unit_of_measurement:'kWh'}], session_cost:['sensor.sc',67.3,{unit_of_measurement:'UAH'}],
    session_time:['sensor.stime','5h 30m'],
    soc_percent:['sensor.soc',44,{unit_of_measurement:'%'}], time_to_target_soc:['sensor.eta', charging ? '2h 15m' : 'Not charging'],
    energy_to_target_soc:['sensor.etts',26.32,{unit_of_measurement:'kWh'}], cost_to_target_soc:['sensor.ctts',113.72,{unit_of_measurement:'UAH'}],
    charging_finish_time:['sensor.finish', charging ? '2026-09-22T18:40:00+00:00' : 'unavailable'],
    initial_soc:['number.isoc',25,{min:0,max:100,step:1,unit_of_measurement:'%'}],
    target_soc:['number.tsoc',75,{min:0,max:100,step:5,unit_of_measurement:'%'}],
    battery_capacity:['number.cap',75,{min:10,max:160,step:1,unit_of_measurement:'kWh'}],
    soc_correction:['number.corr',10.5,{min:0,max:20,step:.5,unit_of_measurement:'%'}],
    limit_disable_all:['switch.dis','off'], limit_soc_enabled:['switch.socen','on'],
    limit_energy:['number.le',100,{min:0,max:100,step:1,unit_of_measurement:'kWh'}], limit_energy_enabled:['switch.lee','off'],
    limit_time:['number.lt',60,{min:0,max:1440,step:1,unit_of_measurement:'min'}], limit_time_enabled:['switch.lte','off'],
    limit_cost:['number.lc',50,{min:0,max:10000,step:1,unit_of_measurement:'UAH'}], limit_cost_enabled:['switch.lce','off'],
    adaptive_mode:['select.am','Voltage',{options:['Off','Voltage','Auto','Power']}],
    undervoltage_threshold:['number.uv',210,{min:210,max:220,step:1,unit_of_measurement:'V'}],
    adaptive_current_limit:['sensor.acl',7,{unit_of_measurement:'A'}],
    time_drift:['sensor.drift',0,{unit_of_measurement:'s'}],
  };
  const merged={...base,...over};
  const states={}, entities={};
  for(const [k,v] of Object.entries(merged)) { if(!v) continue; entities[k]=v[0]; states[v[0]]=st(v[1],v[2]||{}); }
  const calls=[];
  const hass={states,callWS:async()=>({entities}),callService:async(...args)=>{calls.push(args);},...(locale ? {locale:{language:locale}} : {})};
  card._ids=entities;card._resolved=true;card.hass=hass;
  const html=()=>card.shadowRoot.innerHTML.replace(/<style>[\s\S]*?<\/style>/,'');
  return {card,states,calls,hass,timers,html};
}

test('meter reads like the charger screen: big voltage, power and actual current; no session',()=>{
  const {html}=setupAll({sections:['basic_info'],over:{current:['sensor.cur',15.9,{unit_of_measurement:'A'}]}});
  assert.match(html(),/class="panel meter"/);
  assert.match(html(),/data-more-info="voltage"[^]*>227<small>V[^]*data-more-info="power"[^]*>3\.6<small>kW[^]*data-more-info="current"[^]*>15\.9<small>A/);
  assert.doesNotMatch(html(),/session_energy|soc_percent/);
});
test('meter voltage outside the working band is bad; unknown readings are dashes',()=>{
  assert.match(setupAll({sections:['basic_info'],over:{voltage:['sensor.voltage',200]}}).html(),/meter-value bad"[^>]*>200/);
  assert.doesNotMatch(setupAll({sections:['basic_info']}).html(),/meter-value bad/);
  const x=setupAll({sections:['basic_info'],over:{power:['sensor.power','unavailable']}}).html();
  assert.match(x,/data-more-info="power"[^]*>—</);
});
test('Session is its own thin row: energy, cost (currency after, two decimals) and duration as separate items',()=>{
  const {html}=setupAll({sections:['session']});
  assert.match(html(),/class="sl session"/);
  assert.match(html(),/>Session</);
  assert.match(html(),/data-more-info="session_energy"[^]*>15\.6<small>kWh[^]*data-more-info="session_cost"[^]*>67\.30<small>₴<\/small>[^]*data-more-info="session_time"[^]*>5h 30m</);
  assert.match(setupAll({sections:['session'],over:{session_cost:['sensor.sc',1234.5,{unit_of_measurement:'UAH'}]}}).html(),/>1\u202f235<small>₴/);
});
test('battery SOC: the battery tile leads with the SOC; the target tile with the time left and the finish time',()=>{
  const {html}=setupAll({sections:['advanced_info']});
  assert.match(html(),/class="panel soc soc-mid"/);
  assert.match(html(),/data-more-info="soc_percent"[^]*<b data-fit="26">≈44<small>%<\/small><\/b>[^]*from 25<small>%/);
  assert.match(html(),/data-more-info="time_to_target_soc"[^]*To 75<small>%[^]*class="soc-finish"[^>]*>\d{2}:\d{2}<[^]*<b data-fit="24">2h15m<\/b>/);
  assert.match(html(),/26\.3<small>kWh[^]*114[^]*to go/);
  assert.match(html(),/soc-base" style="width:25%"/);
  assert.match(html(),/soc-fill" style="left:25%;width:19%"/);
  assert.match(html(),/soc-target" style="left:75%"/);
});
test('battery SOC idle: no time and no finish without a charge — the energy to go leads; reached says so; basic mode renders nothing',()=>{
  const cc={charging_current:['number.cc',12,{min:6,max:32,step:1,unit_of_measurement:'A'}]};
  const idle=setupAll({sections:['advanced_info'],charging:false,over:cc}).html();
  assert.doesNotMatch(idle,/Not charging|soc-finish|≈\d+h|\d{2}:\d{2}/);
  assert.match(idle,/To 75<small>%[^]*<b data-fit="24">26\.3<small>kWh<\/small><\/b>[^]*114<small>₴<\/small> <span class="tile-note">to go/);
  assert.match(setupAll({sections:['advanced_info'],over:{soc_percent:['sensor.soc',80]}}).html(),/Target reached/);
  assert.doesNotMatch(setupAll({sections:['advanced_info'],mode:'basic'}).html(),/class="(sl|panel)/);
  assert.doesNotMatch(setupAll({sections:['advanced_controls'],mode:'basic'}).html(),/limit-tile/);
});
test('SOC settings wear the battery section colour',()=>{
  assert.match(setupAll({sections:['advanced_controls']}).html(),/class="limits controls soc-mid"/);
  assert.match(setupAll({sections:['advanced_controls'],charging:false}).html(),/class="limits controls soc-idle"/);
});
const HISTORY={total_energy:['sensor.tot',5290.16,{unit_of_measurement:'kWh'}],
  counter_a_energy:['sensor.ae',949.11,{unit_of_measurement:'kWh'}],counter_a_cost:['sensor.ac',3032.94,{unit_of_measurement:'UAH'}],
  counter_b_energy:['sensor.be',3694.81,{unit_of_measurement:'kWh'}],counter_b_cost:['sensor.bc',12669.33,{unit_of_measurement:'UAH'}],
  reset_counter_a:['button.ra','unknown'],reset_counter_b:['button.rb','unknown']};
test('History: Total, Counter A and Counter B with energy and cost, each opening More Info',()=>{
  const {html}=setupAll({sections:['history'],over:HISTORY});
  assert.match(html(),/class="panel history"/);
  assert.match(html(),/>Total<[^]*data-more-info="total_energy"[^]*5\u202f290<small>kWh/);
  assert.match(html(),/>Counter A<[^]*data-more-info="counter_a_energy"[^]*949<small>kWh[^]*3\u202f033/);
  assert.match(html(),/>Counter B<[^]*data-more-info="counter_b_energy"[^]*3\u202f695<small>kWh[^]*12\u202f669/);
  assert.match(html(),/data-reset="reset_counter_a"/);
  assert.doesNotMatch(html(),/data-reset="total/);
});
test('Counter reset asks first, presses the button only on confirm, and cancel or timeout sends nothing',async()=>{
  const x=setupAll({sections:['history'],over:HISTORY});
  await x.card._resetCounter('reset_counter_a');
  assert.equal(x.calls.length,0);
  assert.match(x.html(),/Reset Counter A\?[^]*data-reset-cancel[^]*data-reset-confirm="reset_counter_a"/);
  await x.card._resetCounter('reset_counter_a',true);
  assert.equal(JSON.stringify(x.calls),JSON.stringify([['button','press',{entity_id:'button.ra'}]]));
  assert.doesNotMatch(x.html(),/data-reset-confirm/);
  const y=setupAll({sections:['history'],over:HISTORY});
  await y.card._resetCounter('reset_counter_b');y.card._cancelReset();
  await y.card._resetCounter('reset_counter_b',true);
  await y.card._resetCounter('reset_counter_b');for(const fn of [...y.timers.values()]) fn();
  await y.card._resetCounter('reset_counter_b',true);
  assert.equal(y.calls.length,0);
  const z=setupAll({sections:['history'],over:{...HISTORY,state:['sensor.state','unavailable']}});
  assert.match(z.html(),/data-reset="reset_counter_a"[^>]*disabled/);
  await z.card._resetCounter('reset_counter_a');await z.card._resetCounter('reset_counter_a',true);
  assert.equal(z.calls.length,0);
});
test('default order is logical: state and actions, battery and its settings, power and current, adaptive, session and its limits, counters, safety',()=>{
  const x=setupAll({sections:['current']});
  assert.throws(()=>x.card.setConfig({sections:['bogus']}),/status, actions, advanced_info, advanced_controls, basic_info, current, adaptive, session, limits, schedules, time, history, safety/);
});
test('SOC settings: four number tiles with steppers and tap-to-edit, using entity bounds',async()=>{
  const {html,card,calls}=setupAll({sections:['advanced_controls']});
  for(const k of ['initial_soc','target_soc','battery_capacity','soc_correction']) assert.match(html(),new RegExp(`data-limit-step="${k}"`));
  assert.match(html(),/limits-grid four/);
  assert.match(html(),/Loss<small class="tile-unit">%<\/small>[^]*data-fit>10\.5<\/b>/);
  await card._stepLimit('soc_correction',1);
  assert.equal(JSON.stringify(calls.at(-1)),JSON.stringify(['number','set_value',{entity_id:'number.corr',value:11}]));
  await card._stepLimit('target_soc',1);
  assert.equal(calls.at(-1)[2].value,80);
});
test('Limits SOC tile turns read-only "To target" when SOC settings own the Target SOC editor',()=>{
  const both=setupAll({sections:['limits','advanced_controls']}).html();
  const limits=both.slice(0,both.indexOf('aria-label="Advanced controls"'));
  assert.match(limits,/limit-value readonly/);
  assert.doesNotMatch(limits,/data-limit-step="target_soc"/);
  assert.match(limits,/data-limit-toggle="limit_soc_enabled"/);
  const alone=setupAll({sections:['limits']}).html();
  assert.match(alone,/data-limit-step="target_soc"/);
});
test('adaptive: named as adaptive charging, mode picker, threshold only in Voltage mode, read-only adaptive cap',async()=>{
  const v=setupAll({sections:['adaptive']});
  assert.match(v.html(),/class="panel adaptive"/);
  assert.match(v.html(),/>Adaptive charging</);
  assert.match(v.html(),/<select[^>]*data-select="adaptive_mode"/);
  assert.match(v.html(),/<option value="Voltage" selected>Voltage<\/option>/);
  assert.match(v.html(),/Slow down below[^]*data-limit-step="undervoltage_threshold"/);
  assert.match(v.html(),/data-more-info="adaptive_current_limit"[^]*>7<small>A/);
  await v.card._selectOption('adaptive_mode','Auto');
  assert.equal(JSON.stringify(v.calls.at(-1)),JSON.stringify(['select','select_option',{entity_id:'select.am',option:'Auto'}]));
  const off=setupAll({sections:['adaptive'],over:{adaptive_mode:['select.am','Off',{options:['Off','Voltage','Auto','Power']}]}}).html();
  assert.doesNotMatch(off,/undervoltage_threshold|adaptive_current_limit/);
  const power=setupAll({sections:['adaptive'],over:{adaptive_mode:['select.am','Power',{options:['Off','Voltage','Auto','Power']}]}}).html();
  assert.doesNotMatch(power,/undervoltage_threshold/);assert.match(power,/adaptive_current_limit/);
});
test('adaptive offline or unchanged option sends nothing',async()=>{
  const x=setupAll({sections:['adaptive'],over:{state:['sensor.state','unavailable']}});
  await x.card._selectOption('adaptive_mode','Auto');
  const y=setupAll({sections:['adaptive']});await y.card._selectOption('adaptive_mode','Voltage');await y.card._selectOption('adaptive_mode','Bogus');
  assert.equal(x.calls.length+y.calls.length,0);
  assert.match(x.html(),/<select[^>]*disabled/);
});

test('safety row carries its Safety label (hidden only by CSS on narrow cards)',()=>{
  const {card}=setupSafety();
  assert.match(card.shadowRoot.innerHTML,/class="label safety-label">Safety</);
});

test('SOC tiles: long press opens the setting (Battery → Initial SOC, target → Target SOC) and swallows the click',()=>{
  const x=setupAll({sections:['advanced_info']});
  assert.match(x.html(),/data-more-info="soc_percent" data-hold="initial_soc"/);
  assert.match(x.html(),/data-more-info="time_to_target_soc" data-hold="target_soc"/);
  const opened=[];x.card.addEventListener('hass-more-info',(e)=>opened.push(e.detail.entityId));
  const el=(hold,more)=>({closest:(sel)=>sel==='[data-hold]'?{dataset:{hold}}:sel==='[data-more-info]'?{dataset:{moreInfo:more}}:null});
  x.card._holdStart({target:el('initial_soc','soc_percent'),clientX:5,clientY:5});
  for(const fn of [...x.timers.values()]) fn();
  x.card._onClick({target:el('initial_soc','soc_percent')});
  assert.deepEqual([...opened],['number.isoc']);
  // A short tap still opens the reading itself.
  x.card._holdStart({target:el('target_soc','time_to_target_soc'),clientX:5,clientY:5});x.card._holdEnd();
  x.card._onClick({target:el('target_soc','time_to_target_soc')});
  assert.deepEqual([...opened],['number.isoc','sensor.eta']);
});
test('SOC settings are reachable from a keyboard without triggering the tile tap',()=>{
  const x=setupAll({sections:['advanced_info']});
  const opened=[];x.card.addEventListener('hass-more-info',(e)=>opened.push(e.detail.entityId));
  const event=(hold)=>({key:'Enter',altKey:true,target:{closest:(selector)=>selector==='[data-hold]'?{dataset:{hold}}:null},preventDefault(){this.prevented=true;}});
  const initial=event('initial_soc');x.card._onKeyDown(initial);
  const target=event('target_soc');x.card._onKeyDown(target);
  assert.deepEqual([...opened],['number.isoc','number.tsoc']);
  assert.equal(initial.prevented,true);assert.equal(target.prevented,true);
  assert.match(x.html(),/aria-keyshortcuts="Alt\+Enter"/);
});

// ---- Editor (composition pass) ----
function setupEditor(config) {
  const registry = {}, events = [];
  class HTMLElement {
    attachShadow() { return this.shadowRoot = {innerHTML:'', addEventListener(){}, querySelector(){return null;}}; }
    addEventListener() {}
    dispatchEvent(e) { events.push(e); return true; }
  }
  class CustomEvent { constructor(type, init) { this.type = type; Object.assign(this, init); } }
  const cards = [];
  const sandbox = {HTMLElement, CustomEvent, console, setTimeout(){return 0;}, clearTimeout(){},
    customElements:{define:(k,v)=>registry[k]=v, get:(k)=>registry[k]}, window:{customCards:cards}};
  vm.runInNewContext(source, sandbox);
  vm.runInNewContext(source, {...sandbox}); // a second module copy (integration + /local) must not throw or double-list
  const Card = registry['eveus-card'];
  const editor = new registry['eveus-card-editor']();
  editor.setConfig(config);
  const last = () => events.at(-1)?.detail.config;
  return {Card, editor, events, last, cards};
}
test('card is listed once in the picker, stubs the full default card and offers an editor', () => {
  const {Card, cards} = setupEditor({sections:['current']});
  assert.equal(cards.length, 1);
  assert.equal(cards[0].name, 'Eveus EV Charger');
  assert.deepEqual([...Card.getStubConfig().sections], ['status','actions','advanced_info','advanced_controls','basic_info','current','adaptive','session','limits','schedules','time','history','safety']);
  assert.equal(typeof Card.getConfigElement, 'function');
});
test('a card without sections shows the whole default card', () => {
  const {Card} = setupEditor({});
  const card = new Card(); card.setConfig({});
  assert.equal(card._config.sections.length, 13);
});
test('editor lists enabled sections in order, then hidden ones, with readable names', () => {
  const {editor} = setupEditor({sections:['current','status']});
  const html = editor._sectionsHtml();
  assert.ok(html.indexOf('Current slider') < html.indexOf('Status'));
  assert.ok(html.indexOf('Status') < html.indexOf('Safety'));
  assert.match(html, /data-toggle="current" checked/);
  assert.match(html, /data-toggle="safety"(?! checked)/);
  assert.match(html, /data-move="current" data-dir="-1" disabled/); // first cannot go up
  assert.match(html, /data-move="status" data-dir="1" disabled/);   // last enabled cannot go down
});
test('editor hides, shows and reorders sections and reports the new config', () => {
  const {editor, last} = setupEditor({sections:['status','current','session'], device_id:'d1'});
  editor._move('session', -1);
  assert.deepEqual([...last().sections], ['status','session','current']);
  assert.equal(last().device_id, 'd1');
  editor._toggle('status');
  assert.deepEqual([...last().sections], ['session','current']);
  editor._toggle('safety');
  assert.deepEqual([...last().sections], ['session','current','safety']);
});
test('editor keeps at least one section and can restore the default order', () => {
  const {editor, last, events} = setupEditor({sections:['current']});
  editor._toggle('current');
  assert.equal(events.length, 0);
  assert.match(editor._sectionsHtml(), /data-toggle="current" checked disabled/);
  editor._resetOrder();
  assert.equal(last().sections.length, 13);
});
test('basic mode greys out the SOC sections in the editor without dropping them', () => {
  const all = ['status','advanced_info','current','advanced_controls','session'];
  const {editor, last} = setupEditor({sections: all, mode: 'basic'});
  const html = editor._sectionsHtml();
  assert.match(html, /class="row na"><label><input type="checkbox" data-toggle="advanced_info" checked disabled>/);
  assert.doesNotMatch(html, /data-move="advanced_info"/);
  assert.match(html, /advanced mode only/);
  assert.ok(html.indexOf('Battery SOC') > html.indexOf('Safety'), 'greyed rows go last');
  assert.match(html, /data-move="session" data-dir="1" disabled/); // last usable section
  editor._move('current', 1);                                       // skips the greyed SOC settings
  assert.deepEqual([...last().sections], ['status','advanced_info','session','advanced_controls','current']);
  editor._toggle('advanced_info');                                  // cannot be toggled in basic
  assert.deepEqual([...last().sections], ['status','advanced_info','session','advanced_controls','current']);
});
test('editor follows the integration when mode is empty: no SOC entity means basic', async () => {
  const {editor} = setupEditor({sections: ['advanced_info','current']});
  editor.hass = {callWS: async () => ({entities: {state: 'sensor.s'}})};
  await new Promise((r) => setImmediate(r));
  assert.match(editor._sectionsHtml(), /class="row na"/);
  const adv = setupEditor({sections: ['advanced_info','current']}).editor;
  adv.hass = {callWS: async () => ({entities: {soc_percent: 'sensor.soc'}})};
  await new Promise((r) => setImmediate(r));
  assert.doesNotMatch(adv._sectionsHtml(), /class="row na"/);
});
test('basic mode: Limits has no SOC tile (Energy, Time, Cost only)',()=>{
  const basic=setupAll({sections:['limits'],mode:'basic'}).html();
  assert.doesNotMatch(basic,/limit_soc_enabled|target_soc/);
  assert.match(basic,/data-limit-toggle="limit_energy_enabled"|Energy/);
});

const SCHED = {
  schedule_1_enabled:['switch.s1','on'], schedule_1_start:['time.s1a','23:00:00'], schedule_1_stop:['time.s1b','07:00:00'],
  schedule_1_current_limit_enabled:['switch.s1c','on'], schedule_1_current_limit:['number.s1c',16,{min:7,max:16,step:1,unit_of_measurement:'A'}],
  schedule_1_energy_limit_enabled:['switch.s1e','off'], schedule_1_energy_limit:['number.s1e',76.371,{min:0,max:100,step:1,unit_of_measurement:'kWh'}],
  schedule_2_enabled:['switch.s2','off'], schedule_2_start:['time.s2a','09:00:00'], schedule_2_stop:['time.s2b','18:00:00'],
  schedule_2_current_limit_enabled:['switch.s2c','on'], schedule_2_current_limit:['number.s2c',10,{min:7,max:16,step:1,unit_of_measurement:'A'}],
  schedule_2_energy_limit_enabled:['switch.s2e','off'], schedule_2_energy_limit:['number.s2e',0,{min:0,max:100,step:1,unit_of_measurement:'kWh'}],
};
const TIME = {
  time_zone:['select.tz','+3',{options:['-1','0','+1','+2','+3']}], time_drift:['sensor.drift',0,{unit_of_measurement:'s'}], sync_time:['button.sync','unknown'],
};
// ---- Ukrainian ----
const ALL = ['status','actions','advanced_info','advanced_controls','basic_info','current','adaptive','session','limits','schedules','time','history','safety'];
const FULL_OVER = {
  stop_charging:['switch.stop','off'], one_charge:['switch.one','off'], connect_to_ocpp:['switch.ocpp','on'],
  not_charging_reason:['sensor.reason','Charging'], soc_energy:['sensor.soce',33],
  total_energy:['sensor.tot',5290,{unit_of_measurement:'kWh'}],
  counter_a_energy:['sensor.ca',949,{unit_of_measurement:'kWh'}], counter_a_cost:['sensor.cac',3033,{unit_of_measurement:'UAH'}], reset_counter_a:['button.ra','unknown'],
  counter_b_energy:['sensor.cb',3695,{unit_of_measurement:'kWh'}], counter_b_cost:['sensor.cbc',12669,{unit_of_measurement:'UAH'}], reset_counter_b:['button.rb','unknown'],
  ground_protection:['switch.gp','on'], ground:['sensor.ground','Connected'], box_temperature:['sensor.bt',14], plug_temperature:['sensor.pt',6],
  leakage_current:['sensor.lk',0], connection_quality:['sensor.cq',100],
  charging_current:['number.cc',12,{min:6,max:32,step:1}],
  ...SCHED, ...TIME,
};
// Visible text plus the words a screen reader or tooltip speaks.
const spoken = (html) => [html.replace(/<[^>]+>/g, ' '), ...[...html.matchAll(/(?:title|aria-label)="([^"]*)"/g)].map((m) => m[1])].join(' ');
const ENGLISH = /\b(Session|Limits?|Counter|Voltage|Power|Current|Battery|Target|Safety|Stop|Stopped|Sure|Tap|Offline|Loading|Reset|Cancel|Disable|Adaptive|Slow|capped|cap|battery|go|all time|Total|Energy|Time|Cost|Initial|Capacity|Loss|Charging|Connected|On|Off|One|charge|Finish|est|details|Actual|Requested|Decrease|Increase|Set|Box|Plug|Ground|Leakage|Connection|Bad|reached|Hold)\b/;
test('uk: the whole card speaks Ukrainian when Home Assistant does', () => {
  const {html} = setupAll({sections: ALL, locale: 'uk', over: FULL_OVER, fold: false});
  const text = spoken(html());
  assert.doesNotMatch(text, ENGLISH, text.match(ENGLISH)?.[0]);
  const folded = spoken(setupAll({sections: ALL, locale: 'uk', over: FULL_OVER}).html());
  assert.doesNotMatch(folded, ENGLISH, folded.match(ENGLISH)?.[0]);
  for (const word of ['Налаштування SOC', 'Розклади', 'Лічильники']) assert.ok(folded.includes(word), word);
  for (const word of ['Заряджання', 'Один заряд', 'Стоп', 'Батарея', 'Ціль', 'Напруга', 'Потужність', 'Струм',
    'Адаптивне заряджання', 'Сесія', 'Ліміти', 'Без лімітів', 'Енергія', 'Час', 'Вартість', 'Загалом', 'Лічильник A', 'Безпека']) {
    assert.ok(text.includes(word), word);
  }
  assert.match(html(), /2<small>год<\/small>\s*15<small>хв<\/small>/); // time to target
  assert.match(html(), /5<small>год<\/small>\s*30<small>хв<\/small>/); // session time
  assert.match(html(), /<option value="Voltage" selected>Напруга<\/option>/);
});
test('uk: language setting overrides Home Assistant both ways', () => {
  assert.match(setupAll({sections:['session'], locale:'uk', language:'en'}).html(), />Session</);
  assert.match(setupAll({sections:['session'], locale:'en', language:'uk'}).html(), />Сесія</);
  assert.match(setupAll({sections:['session']}).html(), />Session</); // no locale → English
});
test('uk: charger states, faults, reasons and offline age are translated', () => {
  const idle = setupAll({sections:['status'], locale:'uk', charging:false, over:{not_charging_reason:['sensor.r','Cable Not Connected'], state:['sensor.state','Standby']}}).html();
  assert.match(idle, />Очікування</); assert.match(idle, />Кабель не підключено</);
  const fault = setupAll({sections:['status'], locale:'uk', over:{state:['sensor.state','Error'], substate:['sensor.sub','Box Overheat']}}).html();
  assert.match(fault, />Перегрів корпусу</);
  const x = setupAll({sections:['status'], locale:'uk'}); x.states['sensor.state'] = {state:'unavailable', attributes:{}, last_changed:new Date(Date.now()-12*60000).toISOString()}; x.card.hass = x.hass;
  assert.match(x.html(), />Немає зв'язку</); assert.match(x.html(), />12 хв тому</);
  // an unknown value from newer firmware still shows, untranslated
  assert.match(setupAll({sections:['status'], locale:'uk', over:{state:['sensor.state','Warp Drive']}}).html(), />Warp Drive</);
});
test('uk: a unit taken from the entity is translated too (Limit Time reads хв, not min)', () => {
  const x = setupAll({sections:['limits'], locale:'uk', fold:false}).html();
  assert.match(x, /Час<small class="tile-unit">хв<\/small>/);
  assert.doesNotMatch(x, />min</);
  // a unit the card has no word for still shows as the entity gives it
  assert.match(x, /Енергія<small class="tile-unit">kWh<\/small>/);
});
test('uk: a connected ground reads OK, a missing one Немає', () => {
  const card = (ground) => { const x = setupSafety({ground}); x.hass.locale = {language:'uk'}; x.card.hass = x.hass; return x.card.shadowRoot.innerHTML; };
  assert.match(card('Connected'), /safety-value good">OK</);
  assert.match(card('Not Connected'), /safety-value bad">Немає</);
});
test('target tile keeps the % against its number: the goal is one inline run, not two flex items', () => {
  const {html} = setupAll({sections:['advanced_info']});
  assert.match(html(), /<span class="soc-goal">To 75<small>%<\/small><\/span>/);
});
test('uk: counter reset asks in Ukrainian; adaptive keeps sending the real option', async () => {
  const x = setupAll({sections:['history','adaptive'], locale:'uk', over: FULL_OVER});
  x.card._resetCounter('reset_counter_a');
  assert.match(x.html(), /Скинути лічильник A\?/); assert.match(x.html(), />Скасувати</); assert.match(x.html(), /обнулено/);
  await x.card._selectOption('adaptive_mode', 'Auto');
  assert.equal(JSON.stringify(x.calls.at(-1)), JSON.stringify(['select', 'select_option', {entity_id: 'select.am', option: 'Auto'}]));
});
test('uk: editor follows Home Assistant language and offers the card language', () => {
  const {editor} = setupEditor({sections:['current']});
  editor._hass = {locale:{language:'uk'}};
  const html = editor._sectionsHtml();
  assert.match(html, /Повзунок струму/); assert.match(html, /Безпека/); assert.doesNotMatch(html, /Current slider|Safety/);
  const names = editor._schema().map((f) => f.name);
  assert.equal(JSON.stringify(names), JSON.stringify(['device_id', 'mode', 'language', 'fold']));
  assert.equal(JSON.stringify(editor._schema()[2].selector.select.options.map((o) => o.label)), JSON.stringify(['Мова Home Assistant', 'Українська', 'English']));
  assert.equal(editor._labels().head, 'Розділи');
});

// ---- Saved dashboards from the four-layout card (4.24.0 and earlier) ----
const LEGACY = {
  compact: ['status','advanced_info','basic_info','session'],
  status: ['status','advanced_info','basic_info','session','safety'],
  control: ['status','actions','advanced_info','basic_info','current','session'],
  full: ALL,
};
test('a saved layout keeps working: each old layout opens as its matching sections', () => {
  const {Card} = setupEditor({});
  for (const [layout, sections] of Object.entries(LEGACY)) {
    const card = new Card(); card.setConfig({type: 'custom:eveus-card', layout, mode: 'basic', language: 'uk'});
    assert.equal(JSON.stringify(card._config.sections), JSON.stringify(sections), layout);
    assert.equal(card._config.mode, 'basic'); assert.equal(card._config.language, 'uk');
  }
  const card = new Card(); card.setConfig({layout: 'control', sections: ['safety']});
  assert.equal(JSON.stringify(card._config.sections), '["safety"]', 'explicit sections win');
  const odd = new Card(); odd.setConfig({layout: 'mystery'});
  assert.equal(odd._config.sections.length, 13, 'an unknown layout opens the full card');
});
test('the editor opens a saved layout as its sections and replaces it on the first change', () => {
  const {editor, last} = setupEditor({type: 'custom:eveus-card', layout: 'compact'});
  assert.match(editor._sectionsHtml(), /data-toggle="session" checked/);
  assert.match(editor._sectionsHtml(), /data-toggle="actions"(?! checked)/);
  editor._toggle('safety');
  assert.equal(last().layout, undefined);
  assert.equal(JSON.stringify(last().sections), JSON.stringify([...LEGACY.compact, 'safety']));
});

// ---- Schedules and charger time ----
const rowOf = (html, n) => html.split('class="sched-row')[n];
test('schedules: one row per schedule with its window, current cap and energy cap', () => {
  const {html, card, hass} = setupAll({sections:['schedules'], over:SCHED});
  card._nowHM = () => '12:00'; card.hass = hass; // outside both windows
  assert.equal((html().match(/class="sched-row/g) || []).length, 2);
  const one = rowOf(html(), 1), two = rowOf(html(), 2);
  assert.match(one, /^ on"/, 'an enabled schedule is lit');
  assert.doesNotMatch(two, /^ on"/, 'a disabled schedule stays neutral');
  assert.match(one, /data-limit-toggle="schedule_1_enabled" role="switch" aria-checked="true"/);
  assert.match(one, /data-time-edit-trigger="schedule_1_start"[^>]*>23:00</);
  assert.match(one, /data-time-edit-trigger="schedule_1_stop"[^>]*>07:00</);
  assert.match(one, /data-limit-toggle="schedule_1_current_limit_enabled"[^>]*aria-checked="true"/);
  assert.match(one, /data-limit-edit-trigger="schedule_1_current_limit"[\s\S]*?16<small>A<\/small>/);
  assert.match(one, /data-limit-edit-trigger="schedule_1_energy_limit"[\s\S]*?76<small>kWh<\/small>/);
  assert.match(one, /class="sched-lim active"/, 'cap on inside an enabled schedule is active');
  assert.match(two, /class="sched-lim saved"/, 'cap on inside a disabled schedule is only saved');
  assert.match(two, />09:00<[\s\S]*>18:00</);
});
test('schedules: toggles and caps use their own switch and number entities', async () => {
  const {card, calls} = setupAll({sections:['schedules'], over:SCHED});
  await card._toggleLimit('schedule_2_enabled');
  await card._toggleLimit('schedule_1_energy_limit_enabled');
  await card._commitLimitEdit('schedule_1_current_limit', '12');
  assert.equal(JSON.stringify(calls), JSON.stringify([
    ['switch','turn_on',{entity_id:'switch.s2'}], ['switch','turn_on',{entity_id:'switch.s1e'}],
    ['number','set_value',{entity_id:'number.s1c',value:12}]]));
});
test('schedule times: a changed time is sent once as HH:MM:SS after a short pause, or at once on blur', async () => {
  const {card, calls, timers, html} = setupAll({sections:['schedules'], over:SCHED});
  card._startTimeEdit('schedule_1_start');
  assert.match(card.shadowRoot.innerHTML, /<input type="time"[^>]*data-time-edit="schedule_1_start"[^>]*value="23:00"/, 'tap opens a time input');
  card._timeDraft('schedule_1_start', '22:15');
  card._timeDraft('schedule_1_start', '22:30');
  assert.equal(calls.length, 0, 'still typing');
  for (const fn of [...timers.values()]) await fn();
  assert.equal(JSON.stringify(calls), JSON.stringify([['time','set_value',{entity_id:'time.s1a',time:'22:30:00'}]]));
  assert.match(html(), /data-time-edit-trigger="schedule_1_start"[^>]*>22:30</, 'pending value shows until HA confirms');
  card._startTimeEdit('schedule_1_stop'); card._timeDraft('schedule_1_stop', '06:00'); await card._timeBlur('schedule_1_stop');
  assert.equal(JSON.stringify(calls.at(-1)), JSON.stringify(['time','set_value',{entity_id:'time.s1b',time:'06:00:00'}]));
  const n = calls.length;
  card._startTimeEdit('schedule_2_start'); card._timeDraft('schedule_2_start', '09:00'); await card._timeBlur('schedule_2_start');
  card._startTimeEdit('schedule_2_stop'); card._timeDraft('schedule_2_stop', ''); await card._timeBlur('schedule_2_stop');
  assert.equal(calls.length, n, 'unchanged or cleared time sends nothing');
});
test('schedules offline: readable, every control disabled, nothing sent', async () => {
  const {card, calls, html} = setupAll({sections:['schedules'], over:{...SCHED, state:['sensor.state','unavailable']}});
  assert.match(html(), /data-time-edit-trigger="schedule_1_start"[^>]*disabled/);
  assert.match(html(), /data-limit-toggle="schedule_1_enabled"[^>]*disabled/);
  card._startTimeEdit('schedule_1_start'); card._timeDraft('schedule_1_start', '01:00'); await card._timeBlur('schedule_1_start');
  await card._toggleLimit('schedule_1_enabled');
  assert.equal(calls.length, 0);
  assert.match(setupAll({sections:['schedules']}).html(), /No Eveus schedules found/);
});
test('time: zone picker, clock drift coloured by the Repairs threshold, and a sync button', async () => {
  const {card, calls, html} = setupAll({sections:['time'], over:TIME});
  assert.match(html(), /class="sl time/);
  assert.match(html(), /<option value="\+3" selected>UTC\+3<\/option>/);
  assert.match(html(), /<option value="0">UTC<\/option>/);
  assert.match(html(), /data-more-info="time_drift"[\s\S]*?class="time-drift good">0<small>s<\/small>/);
  await card._selectOption('time_zone', '+2');
  await card._syncTime();
  assert.equal(JSON.stringify(calls), JSON.stringify([['select','select_option',{entity_id:'select.tz',option:'+2'}], ['button','press',{entity_id:'button.sync'}]]));
  assert.match(html(), /class="time-sync done"/, 'the tap is acknowledged');
  const drift = (v) => setupAll({sections:['time'], over:{...TIME, time_drift:['sensor.drift',v,{unit_of_measurement:'s'}]}}).html();
  assert.match(drift(90), /class="time-drift good">\+2<small>min<\/small>/);
  assert.match(drift(900), /class="time-drift bad">\+15<small>min<\/small>/);
  assert.match(drift(-3600), /class="time-drift bad">−1<small>h<\/small>/);
  assert.match(drift('unavailable'), /class="time-drift">—/);
});
test('time offline: nothing can be changed or pressed', async () => {
  const {card, calls, html} = setupAll({sections:['time'], over:{...TIME, state:['sensor.state','unavailable']}});
  assert.match(html(), /data-select="time_zone"[^>]*disabled/);
  assert.match(html(), /class="time-sync"[^>]*disabled/);
  await card._syncTime(); await card._selectOption('time_zone', '+2');
  assert.equal(calls.length, 0);
});
test('uk: schedules and time speak Ukrainian', () => {
  const {html} = setupAll({sections:['schedules','time'], over:{...SCHED, ...TIME, time_drift:['sensor.drift',-120,{unit_of_measurement:'s'}]}, locale:'uk'});
  assert.match(html(), /Розклад 1/);
  assert.match(html(), /Часовий пояс/);
  assert.match(html(), /−2<small>хв<\/small>/);
  assert.match(html(), /Синхр/);
});

// ---- Finishing pass: digit groups, active schedule, hidden items ----
test('large readings group their digits with a narrow no-break space, in both languages: 5 290', () => {
  assert.match(setupAll({sections:['history'], over:HISTORY}).html(), /12\u202f669<small>₴/);
  const uk = setupAll({sections:['history','session'], over:{...HISTORY, session_cost:['sensor.sc',1234.5,{unit_of_measurement:'UAH'}]}, locale:'uk'}).html();
  assert.match(uk, /5 290<small>kWh/);
  assert.match(uk, /12 669<small>₴/);
  assert.match(uk, /1 235<small>₴/);
  assert.match(uk, />949<small>kWh/, 'three digits stay as they are');
});
test('a running schedule is marked now, by the charger clock, only while it is on', () => {
  const at = (hm, over = {}) => { const x = setupAll({sections:['schedules'], over:{...SCHED, ...over}}); x.card._nowHM = () => hm; x.card.hass = x.hass; return x.html(); };
  assert.match(rowOf(at('23:30'), 1), /^ on now"/, 'inside an overnight window');
  assert.match(rowOf(at('06:59'), 1), /^ on now"/, 'after midnight, before stop');
  assert.match(rowOf(at('07:00'), 1), /^ on"/, 'stop time is outside');
  assert.match(rowOf(at('12:00', {schedule_2_enabled:['switch.s2','off']}), 2), /^"/, 'a disabled schedule is never now');
  assert.match(rowOf(at('12:00', {schedule_2_enabled:['switch.s2','on']}), 2), /^ on now"/);
  const x = setupAll({sections:['schedules'], over:SCHED}); x.card.hass = {...x.hass, config:{time_zone:'Europe/Kyiv'}};
  assert.match(x.card._nowHM(), /^\d{2}:\d{2}$/);
});
test('schedule predictions follow the charger clock and stop when its drift is unknown or unsafe', () => {
  const x=setupAll({sections:['status','schedules'], charging:false, over:{...SCHED, not_charging_reason:['sensor.reason','Waiting for Schedule'], time_drift:['sensor.drift',120]}});
  const offsets=[];
  x.card._nowHM=(offset=0)=>{offsets.push(offset);return offset===120?'23:30':'23:28';};
  x.card.hass=x.hass;
  assert.match(rowOf(x.html(),1),/^ on now"/);
  assert.ok(offsets.includes(120),'schedule clock uses measured drift');
  x.states['sensor.drift'].state='600';x.card.hass=x.hass;
  assert.doesNotMatch(rowOf(x.html(),1),/^ on now"/);
  assert.doesNotMatch(x.html(),/status-note">Waiting for Schedule · from/);
  x.states['sensor.drift'].state='unavailable';x.card.hass=x.hass;
  assert.doesNotMatch(rowOf(x.html(),1),/^ on now"/);
});
test('masonry size follows rendered height, with a section-based estimate before first paint',()=>{
  const x=setupAll({sections:['status']});
  assert.ok(x.card.getCardSize()>=1);
  x.card._hass=null;
  x.card.setConfig({sections:ALL});
  assert.ok(x.card.getCardSize()>1,'a full card cannot claim 50 px');
  x.card.shadowRoot.querySelector=(q)=>q==='ha-card'?{getBoundingClientRect:()=>({height:511})}:null;
  assert.equal(x.card.getCardSize(),11);
  assert.equal(x.card.getGridOptions().rows,undefined,'sections view follows content height');
});
test('waiting for a schedule says when the next one starts', () => {
  const at = (hm, locale, over = {}) => { const x = setupAll({sections:['status'], charging:false, locale, over:{...SCHED, not_charging_reason:['sensor.reason','Waiting for Schedule'], ...over}}); x.card._nowHM = () => hm; x.card.hass = x.hass; return x.html(); };
  assert.match(at('20:00'), /status-note">Waiting for Schedule · from 23:00</);
  assert.match(at('20:00', 'uk'), /status-note">Очікує розкладу · з 23:00</);
  assert.match(at('08:00', undefined, {schedule_2_enabled:['switch.s2','on']}), /from 09:00</, 'the nearest enabled start wins');
  assert.match(at('20:00', undefined, {schedule_1_enabled:['switch.s1','off']}), /status-note">Waiting for Schedule</, 'no enabled schedule, no time');
});
test('hidden items drop out of their section and the row re-spreads', () => {
  const x = setupAll({sections:['actions','basic_info','history','limits','schedules','time','safety','session','advanced_controls'],
    over:{...FULL_OVER}, fold:false, hide:['actions.ocpp','basic_info.voltage','history.total','limits.cost','schedules.schedule_2','time.sync','safety.connection','session.time','advanced_controls.loss']}).html();
  assert.doesNotMatch(x, /data-status-toggle="connect_to_ocpp"/);
  assert.match(x, /class="panel actions" style="grid-template-columns:repeat\(2,minmax\(0,1fr\)\)"/);
  assert.doesNotMatch(x, /data-more-info="voltage"/);
  assert.match(x, /class="panel meter" style="grid-template-columns:repeat\(2,minmax\(0,1fr\)\)"/);
  assert.doesNotMatch(x, /data-more-info="total_energy"/); assert.match(x, /class="tiles two"/);
  assert.doesNotMatch(x, /data-limit-toggle="limit_cost_enabled"/); assert.match(x, /limits-grid three/);
  assert.equal((x.match(/class="sched-row/g) || []).length, 1);
  assert.doesNotMatch(x, /data-sync/);
  assert.doesNotMatch(x, /data-more-info="connection_quality"/);
  assert.doesNotMatch(x, /data-more-info="session_time"/);
  assert.doesNotMatch(x, /data-limit-step="soc_correction"/);
  const all = setupAll({sections:['actions'], over:{...FULL_OVER}}).html();
  assert.match(all, /class="panel actions" aria-label/, 'three buttons keep the stylesheet grid');
});
test('editor: expand a section to hide or show its items; the last item cannot be hidden', () => {
  const {editor, last} = setupEditor({sections:['time','status']});
  assert.doesNotMatch(editor._sectionsHtml(), /data-item=/, 'items are folded away');
  assert.match(editor._sectionsHtml(), /data-expand="time"/);
  assert.doesNotMatch(editor._sectionsHtml(), /data-expand="status"/, 'a one-item section has nothing to hide');
  editor._expand('time');
  const html = editor._sectionsHtml();
  assert.match(html, /data-item="time.zone" checked/); assert.match(html, /Time zone/);
  editor._toggleItem('time.sync');
  assert.deepEqual([...last().hide], ['time.sync']);
  editor._toggleItem('time.zone');
  assert.deepEqual([...last().hide], ['time.sync', 'time.zone']);
  editor._toggleItem('time.drift');
  assert.deepEqual([...last().hide], ['time.sync', 'time.zone'], 'drift is the last one left');
  assert.match(editor._sectionsHtml(), /data-item="time.drift" checked disabled/);
  editor._toggleItem('time.sync'); editor._toggleItem('time.zone');
  assert.equal(last().hide, undefined, 'nothing hidden, nothing written');
});
test('the card knows a light Home Assistant theme, so low-contrast hues can darken there only', () => {
  const x = setupAll({sections:['basic_info']});
  assert.match(x.html(), /<ha-card data-state="charging">/);
  x.card.hass = {...x.hass, themes:{darkMode:false}};
  assert.match(x.html(), /<ha-card data-state="charging" data-theme="light">/);
});

test('updates keep the same ha-card and replace only what is inside it (a new ha-card is empty for a frame and the page scrolls to the top)', () => {
  const x = setupAll({sections:['session']});
  const attrs = {}, inner = {html: ''};
  const haCard = {setAttribute:(k,v)=>{attrs[k]=v;}, removeAttribute:(k)=>{delete attrs[k];}, set innerHTML(v){inner.html=v;}, get innerHTML(){return inner.html;}};
  x.card.shadowRoot = {innerHTML:'<style>kept</style><ha-card></ha-card>', querySelector:(q)=>q==='ha-card'?haCard:null, querySelectorAll:()=>[], addEventListener(){}};
  x.states['sensor.se'].state = '16'; x.card.hass = x.hass;
  assert.equal(x.card.shadowRoot.innerHTML, '<style>kept</style><ha-card></ha-card>');
  assert.match(inner.html, /class="sl session"/);
  assert.equal(attrs['data-state'], 'charging');
});
test('restoring focus after an update never scrolls the page', () => {
  const x = setupAll({sections:['limits']});
  let opts = 'none';
  const input = {focus:(o)=>{opts=o;}, select(){}};
  x.card._editingLimit = 'limit_energy';
  x.card.shadowRoot = {innerHTML:'', querySelector:(q)=>q.startsWith('[data-limit-edit=')?input:null, querySelectorAll:()=>[], addEventListener(){}};
  x.card._render();
  assert.deepEqual({...opts}, {preventScroll:true});
});
test('an open picker (time zone, adaptive mode) is not re-rendered away by live updates', async () => {
  const x = setupAll({sections:['time','adaptive'], over:TIME});
  x.card._selectFocus('time_zone');
  const before = x.card.shadowRoot.innerHTML;
  x.states['sensor.drift'].state = '30'; x.card.hass = x.hass;
  assert.equal(x.card.shadowRoot.innerHTML, before, 'frozen while the list is open');
  await x.card._selectOption('time_zone', '+2');
  assert.equal(JSON.stringify(x.calls.at(-1)), JSON.stringify(['select','select_option',{entity_id:'select.tz',option:'+2'}]));
  assert.match(x.html(), /\+30<small>s/, 'a choice ends the freeze');
  x.card._selectFocus('adaptive_mode'); x.card._selectBlur();
  x.states['sensor.drift'].state = '60'; x.card.hass = x.hass;
  assert.match(x.html(), /\+1<small>min/, 'closing without a choice ends it too');
  x.card._selectFocus('adaptive_mode');
  for (const fn of [...x.timers.values()]) fn();
  x.states['sensor.drift'].state = '0'; x.card.hass = x.hass;
  assert.match(x.html(), /time-drift good">0<small>s/, 'a forgotten focus times out');
});

test('updates from other entities do not re-render the card; its own entities and the minute do', () => {
  const x = setupAll({sections:['session']});
  x.card.shadowRoot.innerHTML = 'SENTINEL';
  x.hass.states['light.kitchen'] = {state:'on', attributes:{}};
  x.card.hass = {...x.hass};
  assert.equal(x.card.shadowRoot.innerHTML, 'SENTINEL', 'someone else\'s light');
  x.states['sensor.se'] = {state:'16.2', attributes:{unit_of_measurement:'kWh'}}; x.card.hass = x.hass;
  assert.match(x.card.shadowRoot.innerHTML, /16\.2<small>kWh/);
  x.card.shadowRoot.innerHTML = 'SENTINEL';
  x.card._nowHM = () => '23:59'; x.card.hass = x.hass;
  assert.notEqual(x.card.shadowRoot.innerHTML, 'SENTINEL', 'a new minute re-renders (offline age, running schedule)');
});
test('a config change from the editor re-renders even when no state changed', async () => {
  const x = setupAll({sections:['session']});
  x.card.setConfig({sections:['session'], language:'uk'});
  await x.card._resolve();
  assert.match(x.html(), />Сесія</);
});

test('a failed entity lookup is asked again later instead of latching "no charger"', async()=>{
  const registry = {};
  let now = 1000;
  const FakeDate = class extends Date { static now() { return now; } };
  const sandbox = {HTMLElement: class { attachShadow() { return this.shadowRoot = {innerHTML:'', addEventListener(){}, querySelector(){return null;}}; } },
    console, Date: FakeDate, setTimeout(){return 0;}, clearTimeout(){}, customElements:{define:(k,v)=>registry[k]=v}, window:{customCards:[]}};
  vm.runInNewContext(source, sandbox);
  const card = new registry['eveus-card'](); card.setConfig({sections:['current']});
  let asks = 0, failing = true;
  const hass = {states:{'sensor.state':{state:'Charging',attributes:{}}}, callService:async()=>{},
    callWS:async()=>{ asks++; if (failing) throw Error('socket closed'); return {entities:{state:'sensor.state'}}; }};
  card.hass = hass; await new Promise((r)=>setImmediate(r));
  assert.equal(asks, 1); assert.equal(card._ids, null);
  card.hass = hass; await new Promise((r)=>setImmediate(r));
  assert.equal(asks, 1, 'no retry storm on every state change');
  failing = false; now += 60000;
  card.hass = hass; await new Promise((r)=>setImmediate(r));
  assert.equal(asks, 2);
  assert.deepEqual({...card._ids}, {state:'sensor.state'});
});

// ---- 2026-09-24 review: last session, fault guidance, limit state, why slower, schedule plan,
// ---- time-zone fix, month energy, tariff, folding, Stop wording, haptics.
const unplugged = {state:['sensor.state','Standby'], car_connected:['binary_sensor.car','off'],
  last_session_energy:['sensor.lse',15.56,{unit_of_measurement:'kWh',reason:'unplugged',finished_at:'2026-09-20T15:21:42+03:00'}],
  last_session_cost:['sensor.lsc',67.21,{unit_of_measurement:'UAH'}],
  last_session_duration:['sensor.lsd',19895,{unit_of_measurement:'s'}]};
test('unplugged, the Session row is the last session: its energy, cost, duration and when it ended',()=>{
  const x=setupAll({sections:['session'],charging:false,over:unplugged});
  assert.match(x.html(),/>Last session<small>[^<]+<\/small>/);
  assert.match(x.html(),/data-more-info="last_session_energy"[^]*>15\.6<small>kWh[^]*data-more-info="last_session_cost"[^]*>67\.21<small>₴[^]*data-more-info="last_session_duration"[^]*>5h 31m</);
  assert.doesNotMatch(x.html(),/data-more-info="session_energy"/);
  const uk=setupAll({sections:['session'],charging:false,over:unplugged,language:'uk'});
  assert.match(uk.html(),/>Остання сесія<small>/);
  assert.match(uk.html(),/>5<small>год<\/small> 31<small>хв<\/small></);
  // Plugged in, the charger's own session is shown as before.
  const on=setupAll({sections:['session'],charging:false,over:{...unplugged,car_connected:['binary_sensor.car','on'],state:['sensor.state','Connected']}});
  assert.match(on.html(),/>Session</);assert.match(on.html(),/data-more-info="session_energy"/);
});
test('while plugged in, the Session row carries the active tariff price',()=>{
  const x=setupAll({sections:['session'],over:{active_rate_cost:['sensor.rate',2.16,{unit_of_measurement:'₴/kWh',rate_name:'Rate 2'}]}});
  assert.match(x.html(),/>Session<small[^>]*title="Rate 2"[^>]*>2\.16 ₴\/kWh<\/small>/);
  const uk=setupAll({sections:['session'],language:'uk',over:{active_rate_cost:['sensor.rate',2.16,{unit_of_measurement:'₴/kWh',rate_name:'Rate 2'}]}});
  assert.match(uk.html(),/title="Тариф 2"/);
});
test('a fault names the reading behind it and what to do',()=>{
  const hot=setupAll({sections:['status'],over:{state:['sensor.state','Error'],substate:['sensor.sub','Plug Overheat'],plug_temperature:['sensor.plug',86,{unit_of_measurement:'°C'}]}});
  assert.match(hot.html(),/status-state fault">Plug Overheat<[^]*status-note">plug 86° · limit 80° · wait for it to cool</);
  const low=setupAll({sections:['status'],over:{state:['sensor.state','Error'],substate:['sensor.sub','Low Voltage'],voltage:['sensor.voltage',181]}});
  assert.match(low.html(),/status-note">mains 181 V · wait for the mains voltage to return to normal</);
  const relay=setupAll({sections:['status'],language:'uk',over:{state:['sensor.state','Error'],substate:['sensor.sub','Relay Error']}});
  assert.match(relay.html(),/status-note">перезапустіть станцію; якщо повторюється — зверніться до сервісу</);
  // With the status row hidden, the alert strip carries the same detail.
  const strip=setupAll({sections:['session'],over:{state:['sensor.state','Error'],substate:['sensor.sub','Plug Overheat'],plug_temperature:['sensor.plug',86,{unit_of_measurement:'°C'}]}});
  assert.match(strip.html(),/alert-strip fault[^]*Plug Overheat · plug 86°/);
});
test('limits that are off read as off: dim values, a "none on" note, and no Disable all to press',()=>{
  const off=setupAll({sections:['limits'],over:{limit_soc_enabled:['switch.socen','off']}});
  assert.equal((off.html().match(/class="limit-tile inactive/g)||[]).length,4);
  assert.match(off.html(),/class="limits-note">none on</);
  assert.doesNotMatch(off.html(),/Disable all/);
  const one=setupAll({sections:['limits'],over:{limit_soc_enabled:['switch.socen','off'],limit_energy_enabled:['switch.lee','on']}});
  assert.match(one.html(),/Disable all/);assert.doesNotMatch(one.html(),/limits-note/);
  assert.equal((one.html().match(/class="limit-tile inactive/g)||[]).length,3);
  // Suspended keeps the button, so the limits can be switched back.
  const sus=setupAll({sections:['limits'],over:{limit_soc_enabled:['switch.socen','off'],limit_disable_all:['switch.dis','on']}});
  assert.match(sus.html(),/Disable all/);
});
test('charging slower than set: the status says why',()=>{
  const cc={charging_current:['number.cc',12,{min:6,max:16,step:1,unit_of_measurement:'A'}]};
  const adaptive=setupAll({sections:['status'],over:{...cc,current:['sensor.cur',7]}});
  assert.match(adaptive.html(),/status-note">7 of 12 A · adaptive mode</);
  const car=setupAll({sections:['status'],over:{...cc,current:['sensor.cur',9],adaptive_mode:['select.am','Off',{options:['Off','Voltage']}]}});
  assert.match(car.html(),/status-note">9 of 12 A · the car takes less</);
  const uk=setupAll({sections:['status'],language:'uk',over:{...cc,current:['sensor.cur',9],adaptive_mode:['select.am','Off',{options:['Off','Voltage']}]}});
  assert.match(uk.html(),/status-note">9 з 12 A · авто приймає менше</);
  // At the set current the note is the power instead.
  const full=setupAll({sections:['status'],over:{...cc,current:['sensor.cur',11.8],power:['sensor.power',2690]}});
  assert.match(full.html(),/status-note">2\.7 kW/);
});
test('Basic mode or a hidden battery section: the charging status adds the finish time',()=>{
  const cc={charging_current:['number.cc',16,{min:6,max:16,step:1}]};
  const x=setupAll({sections:['status'],over:{...cc,current:['sensor.cur',15.9]}});
  assert.match(x.html(),/status-note">3\.6 kW · until \d{2}:\d{2}</);
  const withBattery=setupAll({sections:['status','advanced_info'],over:{...cc,current:['sensor.cur',15.9]}});
  assert.match(withBattery.html(),/status-note">3\.6 kW</);
});
const plan=(over={})=>({car_connected:['binary_sensor.car','on'],charging_current:['number.cc',12,{min:6,max:16,step:1}],
  schedule_1_enabled:['switch.s1','on'],schedule_1_start:['time.s1a','23:00:00'],schedule_1_stop:['time.s1b','07:00:00'],...over});
test('plugged in and waiting for a schedule, the battery section says how far that schedule gets it (energy, not time)',()=>{
  const x=setupAll({sections:['advanced_info'],charging:false,over:plan()});
  assert.match(x.html(),/class="soc-plan"[^]*Schedule 1: up to ≈21\.8<small>kWh<\/small> → ≈70<small>%/);
  const enough=setupAll({sections:['advanced_info'],charging:false,over:plan({charging_current:['number.cc',16,{min:6,max:16,step:1}]})});
  assert.match(enough.html(),/Schedule 1: enough for 75<small>%/);
  // The schedule's own current limit wins over the slider.
  const capped=setupAll({sections:['advanced_info'],charging:false,over:plan({schedule_1_current_limit:['number.s1c',10,{min:7,max:16,step:1}],schedule_1_current_limit_enabled:['switch.s1ce','on']})});
  assert.match(capped.html(),/up to ≈18\.2<small>kWh/);
  assert.doesNotMatch(setupAll({sections:['advanced_info'],over:plan()}).html(),/soc-plan/,'not while charging');
  assert.doesNotMatch(setupAll({sections:['advanced_info'],charging:false,over:plan({car_connected:['binary_sensor.car','off']})}).html(),/soc-plan/,'not unplugged');
  assert.doesNotMatch(setupAll({sections:['advanced_info'],charging:false,over:plan({schedule_1_enabled:['switch.s1','off']})}).html(),/soc-plan/,'no schedule on');
  assert.match(setupAll({sections:['advanced_info'],charging:false,language:'uk',over:plan()}).html(),/Розклад 1: до ≈21\.8<small>kWh/);
  // Only while the car waits: a fault or a finished charge has nothing to plan.
  for (const state of ['Error','Charge Complete','Paused']) {
    assert.doesNotMatch(setupAll({sections:['advanced_info'],charging:false,over:plan({state:['sensor.state',state]})}).html(),/soc-plan/,state);
  }
});
test('a clock off by whole hours offers the time zone that fixes it instead of Sync',async()=>{
  const tz={time_zone:['select.tz','+3',{options:['+1','+2','+3','+4']}],sync_time:['button.sync','unknown']};
  const x=setupAll({sections:['time'],over:{...tz,time_drift:['sensor.drift',3600]}});
  assert.match(x.html(),/data-zone-fix="\+2"[^]*>Set UTC\+2</);
  assert.doesNotMatch(x.html(),/data-sync/);
  x.card._onClick({target:{closest:(s)=>s==='[data-zone-fix]'?{dataset:{zoneFix:'+2'}}:null}});
  await Promise.resolve();
  assert.deepEqual(JSON.parse(JSON.stringify(x.calls.at(-1))),['select','select_option',{entity_id:'select.tz',option:'+2'}]);
  const minutes=setupAll({sections:['time'],over:{...tz,time_drift:['sensor.drift',700]}});
  assert.match(minutes.html(),/data-sync/);assert.doesNotMatch(minutes.html(),/data-zone-fix/);
});
test('Counters show this month and last month from the energy statistics',async()=>{
  const x=setupAll({sections:['history'],over:{total_energy:['sensor.total',5290.16,{unit_of_measurement:'kWh'}]}});
  const asked=[];
  x.hass.callWS=async(msg)=>{asked.push(msg);return msg.calendar?.offset===-1?{change:240.4}:{change:182.33};};
  await x.card._fetchMonth();
  assert.deepEqual(asked.map((m)=>[m.type,m.statistic_id,m.calendar.period,m.calendar.offset??0,m.types.join()]),
    [['recorder/statistic_during_period','sensor.total','month',0,'change'],['recorder/statistic_during_period','sensor.total','month',-1,'change']]);
  assert.match(x.html(),/class="hist-month"[^]*182<small>kWh[^]*240<small>kWh/);
  // Hidden like any other item.
  const hidden=setupAll({sections:['history'],hide:['history.month'],over:{total_energy:['sensor.total',5290.16]}});
  hidden.hass.callWS=async()=>({change:1});await hidden.card._fetchMonth();
  assert.doesNotMatch(hidden.html(),/hist-month/);
});
test('a long card folds its settings into one-line summaries that open on tap; short cards and fold: false do not',()=>{
  const all=['status','actions','advanced_info','advanced_controls','basic_info','current','adaptive','session','limits','schedules','time','history','safety'];
  const x=setupAll({sections:all,over:plan({total_energy:['sensor.total',5290.16]})});
  const h=x.html();
  assert.doesNotMatch(h,/limits-grid/);assert.match(h,/data-fold="limits"/);
  assert.match(h,/data-fold="advanced_controls"[^]*25<small>%<\/small> → 75<small>%/);
  assert.match(h,/data-fold="schedules"[^]*>1 23:00→07:00</);
  // The fold chevron ends the row, after the head's own controls.
  assert.match(h,/data-select="adaptive_mode"[^]*<\/select><button class="fold-chev-btn" data-fold="adaptive"/);
  assert.match(h,/data-fold="history"[^]*5 290<small>kWh/);
  x.card._onClick({target:{closest:(s)=>s==='[data-fold]'?{dataset:{fold:'limits'}}:null}});
  assert.match(x.html(),/limits-grid/);
  x.card._onClick({target:{closest:(s)=>s==='[data-fold]'?{dataset:{fold:'limits'}}:null}});
  assert.doesNotMatch(x.html(),/limits-grid/);
  const short=setupAll({sections:['limits','history']});
  assert.match(short.html(),/limits-grid/);assert.doesNotMatch(short.html(),/data-fold/);
  const x2=setupAll({sections:all});x2.card.setConfig({sections:all,fold:false});x2.card._ids=x.card._ids;x2.card._resolved=true;x2.card.hass=x2.hass;
  assert.match(x2.html(),/limits-grid/);assert.doesNotMatch(x2.html(),/data-fold/);
});
test('Stop says what a tap does when nothing is charging',()=>{
  const idle=setupStatus({state:'Connected',sections:['actions']});
  assert.match(idle.html(),/status-stop_charging idle[^]*>Tap to block</);
  assert.match(setupStatus({state:'Charging',sections:['actions']}).html(),/>Tap to stop</);
  assert.match(setupStatus({state:'Connected',sections:['actions'],stop:'on'}).html(),/>Tap to resume</);
});
test('taps give haptic feedback in the companion app',async()=>{
  const registry={},events=[];
  class HTMLElement extends EventTarget { attachShadow(){ return this.shadowRoot={innerHTML:'',addEventListener(){},querySelector(){return null;}}; } }
  const sandbox={HTMLElement,CustomEvent,console,Date,setTimeout(){return 0;},clearTimeout(){},customElements:{define:(k,v)=>registry[k]=v},
    window:{customCards:[],dispatchEvent:(e)=>events.push(`${e.type}:${e.detail}`)}};
  vm.runInNewContext(source,sandbox);
  const card=new registry['eveus-card']();card.setConfig({sections:['actions']});
  const entities={state:'sensor.state',one_charge:'switch.one'};
  card._ids=entities;card._resolved=true;
  card.hass={states:{'sensor.state':{state:'Connected',attributes:{}},'switch.one':{state:'off',attributes:{}}},callWS:async()=>({entities}),callService:async()=>{}};
  await card._statusToggle('one_charge');
  assert.deepEqual(events,['haptic:light']);
});
test('the editor offers folding and the month item',()=>{
  const {editor}=setupEditor({sections:['history']});
  assert.ok(editor._schema().some((f)=>f.name==='fold'));
  assert.match(source,/history: \['month', 'total', 'counter_a', 'counter_b'\]/);
});
