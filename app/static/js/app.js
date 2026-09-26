'use strict';

var CURRENT_EMPLOYEE = (function () {
  try { return localStorage.getItem('current_employee') || null; } catch (e) { return null; }
})();

// The server knows who signed in; keep the client in sync with it.
(function () {
  var who = document.body.getAttribute('data-employee-id');
  if (who) {
    CURRENT_EMPLOYEE = who;
    try { localStorage.setItem('current_employee', who); } catch (e) {}
  }
})();

function nowWorkerUnit(vehicle, prep) {
  if (!prep || !prep.active) return vehicle;
  return vehicle + ' · ' + prepScopeLabel(prep.scope) + ' prep';
}

function addNowWorker(empId, name, initials, vehicle, prep) {
  var grid = document.getElementById('now-working-grid');
  if (!grid) return;
  var empty = document.getElementById('now-working-empty');
  if (empty) empty.remove();
  var existing = grid.querySelector('.now-worker[data-employee="' + empId + '"]');
  if (existing) {
    var unit = existing.querySelector('.now-worker-unit');
    // The card says which side of the vehicle this person is timing, so a
    // press on the Outside set is not mistaken for the Inside one.
    if (unit) unit.textContent = nowWorkerUnit(vehicle, prep);
    addNowWorkerTimer(existing, empId, prep);
    return;
  }
  var card = document.createElement('div');
  card.className = 'now-worker';
  card.setAttribute('data-employee', empId);
  var avatar = document.createElement('div');
  avatar.className = 'now-worker-avatar';
  avatar.textContent = initials;
  var info = document.createElement('div');
  info.className = 'now-worker-info';
  var nameEl = document.createElement('div');
  nameEl.className = 'now-worker-name';
  nameEl.textContent = name;
  var unitEl = document.createElement('div');
  unitEl.className = 'now-worker-unit';
  unitEl.textContent = nowWorkerUnit(vehicle, prep);
  info.appendChild(nameEl);
  info.appendChild(unitEl);
  card.appendChild(avatar);
  card.appendChild(info);
  addNowWorkerTimer(card, empId, prep);
  grid.appendChild(card);
  var count = document.getElementById('now-working-count');
  if (count) {
    count.textContent = grid.querySelectorAll('.now-worker').length + ' on the floor';
  }
}

// Drop an employee from the "Now Working" cards (their vehicle is done).
function removeNowWorker(empId) {
  var grid = document.getElementById('now-working-grid');
  if (!grid || !empId) return;
  var card = grid.querySelector('.now-worker[data-employee="' + empId + '"]');
  if (!card) return;
  card.remove();
  var count = document.getElementById('now-working-count');
  if (count) {
    count.textContent = grid.querySelectorAll('.now-worker').length + ' on the floor';
  }
  if (!grid.querySelector('.now-worker') && !document.getElementById('now-working-empty')) {
    var empty = document.createElement('p');
    empty.className = 'muted';
    empty.id = 'now-working-empty';
    empty.style.margin = '0';
    empty.innerHTML = 'No vehicles in progress right now. Click <strong>Start</strong> ' +
      'on a vehicle to begin.';
    grid.appendChild(empty);
  }
}

// ---------------------------------------------------------------------------
// Prep timers: Start -> Pause -> Resume -> Done
// ---------------------------------------------------------------------------
// The server owns the clock: every timer element carries the seconds already
// banked plus the moment its current segment began, and the page carries the
// server's time. The browser only adds the two together, so a timer keeps
// counting correctly across a refresh, a reopened tab, a backgrounded tab or a
// device with a skewed clock.

var PREP_ACTIONS = { start: 'start', pause: 'pause', resume: 'resume', done: 'done' };
var serverOffset = 0;  // server clock - browser clock, in ms

function serverNow() {
  return Date.now() + serverOffset;
}

function readServerOffset() {
  var raw = document.body.getAttribute('data-server-epoch');
  var parsed = parseInt(raw, 10);
  if (!isNaN(parsed)) serverOffset = parsed - Date.now();
}

function pad(n) { return (n < 10 ? '0' : '') + n; }

// Seconds -> 1:05:12 (or 05:12 under an hour), matching the server format.
function clockLabel(seconds) {
  var total = Math.max(0, Math.floor(seconds || 0));
  var h = Math.floor(total / 3600);
  var m = Math.floor((total % 3600) / 60);
  var s = total % 60;
  return h > 0 ? h + ':' + pad(m) + ':' + pad(s) : pad(m) + ':' + pad(s);
}

// Seconds -> "1h 05m" / "5m 30s" / "45s" / "0s", matching the server format.
function durationLabel(seconds) {
  var total = Math.floor(seconds || 0);
  if (total <= 0) return '0s';
  var h = Math.floor(total / 3600);
  var m = Math.floor((total % 3600) / 60);
  var s = total % 60;
  if (h) return h + 'h ' + pad(m) + 'm';
  if (m) return m + 'm ' + pad(s) + 's';
  return s + 's';
}

function prepRow(entryId) {
  return document.getElementById('prep-' + entryId);
}

// One clock set of a vehicle: the Inside clocks or the Outside clocks. Every
// vehicle has exactly these two, and they are clocked independently.
function prepSet(entryId, scope) {
  return document.getElementById('prep-' + entryId + '-' + (scope || 'inside'));
}

function prepScopes(row) {
  if (!row) return [];
  return Array.prototype.slice.call(row.querySelectorAll('.prepset'));
}

function prepScopeLabel(scope) {
  if (scope === 'outside') return 'Outside';
  if (scope === 'both') return 'Inside & Outside';
  return 'Inside';
}

// Add (or refresh) the live clock on a "Now Working" card. A card created by a
// Start click gets the same ticking clock a page load would have rendered.
function addNowWorkerTimer(card, empId, state) {
  if (!card || !state || !state.active) {
    var stale = card && card.querySelector('.now-worker-timer');
    if (stale) stale.remove();
    return;
  }
  var timer = card.querySelector('.now-worker-timer');
  if (!timer) {
    timer = document.createElement('div');
    timer.className = 'now-worker-timer prep';
    timer.setAttribute('data-prep-timer', '1');
    card.querySelector('.now-worker-info').appendChild(timer);
  }
  timer.id = 'prep-now-' + empId;
  timer.className = 'now-worker-timer prep prep-' + state.status;
  timer.setAttribute('data-prep-employee', empId);
  applyClock(timer, state);
}

// Seconds a clock element currently shows: a running clock is recomputed from
// the server's base value, a paused/finished one keeps the frozen total it was
// last handed.
function clockSeconds(el) {
  var base = parseInt(el.getAttribute('data-base-seconds'), 10) || 0;
  var segment = parseInt(el.getAttribute('data-segment-epoch'), 10);
  if (el.getAttribute('data-status') !== 'running' || isNaN(segment)) {
    return parseInt(el.getAttribute('data-elapsed'), 10) || 0;
  }
  return base + Math.max(0, Math.floor((serverNow() - segment) / 1000));
}

// Hand a state dict to one live clock element.
function applyClock(el, state) {
  if (!el || !state) return;
  el.setAttribute('data-status', state.status);
  el.setAttribute('data-base-seconds', state.base_seconds);
  if (state.segment_epoch) {
    el.setAttribute('data-segment-epoch', state.segment_epoch);
  } else {
    el.removeAttribute('data-segment-epoch');
  }
  el.setAttribute('data-elapsed', state.elapsed);
  el.setAttribute('data-server-epoch', state.server_epoch);
  el.textContent = clockLabel(state.elapsed);
}

// Recompute every live timer from the server's base values, then each clock
// set's total (the sum of the clocks in that set) and finally the vehicle
// total. A clock recorded before the Inside/Outside split carries no scope and
// counts towards both sets, so it is subtracted once from the vehicle total to
// keep the two sets from double counting it.
function tickPrepTimers() {
  var timers = document.querySelectorAll('[data-prep-timer]');
  var i, el, seconds;
  for (i = 0; i < timers.length; i++) {
    el = timers[i];
    if (el.getAttribute('data-prep-sum') ||
        el.getAttribute('data-prep-sum-scope')) continue;
    seconds = clockSeconds(el);
    el.setAttribute('data-elapsed', seconds);
    el.textContent = clockLabel(seconds);
  }
  var sets = document.querySelectorAll('.prepset');
  for (i = 0; i < sets.length; i++) {
    el = sets[i].querySelector('[data-prep-sum-scope]');
    if (!el) continue;
    seconds = 0;
    var setClocks = sets[i].querySelectorAll(
      '[data-prep-timer]:not([data-prep-sum-scope])');
    for (var j = 0; j < setClocks.length; j++) {
      seconds += parseInt(setClocks[j].getAttribute('data-elapsed'), 10) || 0;
    }
    el.setAttribute('data-elapsed', seconds);
    el.textContent = clockLabel(seconds);
  }
  var totals = document.querySelectorAll('[data-prep-sum]');
  for (i = 0; i < totals.length; i++) {
    el = totals[i];
    var block = el.closest ? el.closest('.prep') : null;
    if (!block) continue;
    seconds = 0;
    var setTotals = block.querySelectorAll('[data-prep-sum-scope]');
    for (var k = 0; k < setTotals.length; k++) {
      seconds += parseInt(setTotals[k].getAttribute('data-elapsed'), 10) || 0;
    }
    var shared = block.querySelectorAll('[data-prep-both]');
    var counted = {};
    for (var m = 0; m < shared.length; m++) {
      // A clock recorded before the Inside/Outside split is listed in both sets,
      // so take it off the vehicle total only once.
      var sharedId = shared[m].getAttribute('data-session') || String(m);
      if (counted[sharedId]) continue;
      counted[sharedId] = true;
      seconds -= parseInt(shared[m].getAttribute('data-elapsed'), 10) || 0;
    }
    el.setAttribute('data-elapsed', seconds);
    el.textContent = clockLabel(seconds);
  }
}

// Hand a vehicle's whole state to its block: the combined total in the head and
// each of the two clock sets below it.
function applyPrepState(row, state, entryCompleted) {
  if (!row || !state) return;
  var total = row.querySelector('[data-prep-sum]');
  if (total) {
    total.setAttribute('data-elapsed', state.elapsed);
    total.textContent = clockLabel(state.elapsed);
  }
  applyPrepBadge(row, state);
  var meta = row.querySelector('.prep-meta');
  if (meta) meta.innerHTML = prepMetaHtml(state);
  prepScopes(row).forEach(function (setEl) {
    var scope = setEl.getAttribute('data-prep-scope');
    var setState = (state.scopes || {})[scope];
    if (!setState) return;
    applyPrepSetState(setEl, state, setState, entryCompleted);
  });
}

// One clock set: its own total, status, employee clocks, buttons and history.
function applyPrepSetState(setEl, state, setState, entryCompleted) {
  var scope = setEl.getAttribute('data-prep-scope');
  var entryId = setEl.getAttribute('data-prep-entry');
  var total = setEl.querySelector('[data-prep-sum-scope]');
  if (total) {
    total.setAttribute('data-elapsed', setState.elapsed);
    total.textContent = clockLabel(setState.elapsed);
  }
  applyPrepBadge(setEl, setState);
  var meta = setEl.querySelector('.prep-meta');
  if (meta) meta.innerHTML = prepSetMetaHtml(setState);
  renderPrepWorkers(setEl, entryId, setState);
  renderPrepActions(setEl, entryId, scope, setState, entryCompleted);
  renderPrepHistory(setEl, setState, scope);
}

function applyPrepBadge(block, state) {
  block.className = block.className.replace(/\bprep-\w+\b/g, '').replace(/\s+/g, ' ') +
    ' prep-' + state.status;
  var badge = block.querySelector('.prep-status');
  if (badge) {
    badge.textContent = state.status_label;
    badge.className = 'badge prep-status ' + prepBadgeClass(state.status);
  }
  var crew = block.querySelector('.prep-crew');
  if (crew) {
    if (state.worker_count > 1) {
      crew.textContent = state.worker_count + ' employees';
      crew.style.display = '';
    } else {
      crew.style.display = 'none';
    }
  }
}

function prepBadgeClass(status) {
  if (status === 'finished') return 'success';
  if (status === 'running') return 'info';
  if (status === 'paused') return 'warn';
  return 'muted';
}

// Every button carries the clock set it acts on, so a press can never move the
// wrong one of a vehicle's two sets.
function prepButton(action, entryId, sessionId, extraClass, scope) {
  var labels = { start: 'Start', pause: 'Pause', resume: 'Resume', done: 'Done' };
  var classes = {
    start: 'btn success start-btn',
    pause: 'btn warn pause-btn',
    resume: 'btn resume-btn',
    done: 'btn success done-btn'
  };
  var btn = document.createElement('button');
  btn.type = 'button';
  btn.className = classes[action] + (extraClass ? ' ' + extraClass : '');
  btn.setAttribute('data-prep-action', action);
  btn.setAttribute('data-entry', entryId);
  if (sessionId) btn.setAttribute('data-session', sessionId);
  if (scope) btn.setAttribute('data-prep-scope', scope);
  btn.textContent = action === 'start'
    ? labels.start + ' ' + prepScopeLabel(scope)
    : labels[action];
  if (prepIsReadOnly()) btn.disabled = true;
  return btn;
}

function prepJoinButton(entryId, scope) {
  var btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'btn success join-btn';
  btn.setAttribute('data-prep-join', entryId);
  btn.setAttribute('data-entry', entryId);
  if (scope) btn.setAttribute('data-prep-scope', scope);
  btn.textContent = '+ Add Me';
  if (prepIsReadOnly()) btn.disabled = true;
  return btn;
}

function prepIsReadOnly() {
  var row = document.querySelector('.prep[data-readonly="1"]');
  return !!row;
}

function escapeHtml(text) {
  return String(text === null || text === undefined ? '' : text)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function prepMetaHtml(state) {
  if (!state.worker_count) return '<span class="muted">No employee recorded</span>';
  var parts = [];
  parts.push(state.worker_count + ' employee' +
    (state.worker_count === 1 ? '' : 's') + ' on this vehicle');
  if (state.started) parts.push('Started ' + state.started);
  if (state.finished) parts.push('Done ' + state.finished);
  parts.push('<strong>Total prep ' + state.total_label + '</strong>');
  var scopes = state.scopes || {};
  if (scopes.inside && scopes.outside) {
    parts.push('(inside ' + scopes.inside.total_label +
      ' · outside ' + scopes.outside.total_label + ')');
  }
  return parts.join(' · ');
}

function prepSetMetaHtml(state) {
  if (!state.worker_count) {
    return '<span class="muted">No ' +
      prepScopeLabel(state.scope).toLowerCase() + ' clock yet</span>';
  }
  var parts = [];
  parts.push(state.worker_count + ' employee' +
    (state.worker_count === 1 ? '' : 's') + ' on the ' +
    prepScopeLabel(state.scope).toLowerCase());
  if (state.started) parts.push('Started ' + state.started);
  if (state.finished) parts.push('Done ' + state.finished);
  parts.push('<strong>' + prepScopeLabel(state.scope) + ' prep ' +
    state.total_label + '</strong>');
  return parts.join(' · ');
}

// Rebuild the per-employee clocks of one clock set. Any number of employees can
// be on a set at once, each with their own clock, total and buttons.
//
// A clock set nobody has started yet is rendered without a list at all, so the
// very first Start on that side has to build it: without this the employee who
// presses Start on the inside of a vehicle somebody is already washing outside
// is told their clock is "in the list above" while there is no list above, and
// no clock appears until the page is reloaded.
function renderPrepWorkers(setEl, entryId, state) {
  var workers = state.workers || [];
  var list = setEl.querySelector('.prep-workers');
  if (!list) {
    if (!workers.length) return;
    list = document.createElement('ul');
    list.className = 'prep-workers';
    // Where the server puts it: after the set's own meta line, before its
    // buttons and its history.
    var anchor = setEl.querySelector('.prep-actions') ||
      setEl.querySelector('.prep-history');
    if (anchor) setEl.insertBefore(list, anchor);
    else setEl.appendChild(list);
  }
  list.innerHTML = '';
  workers.forEach(function (w) {
    var item = document.createElement('li');
    item.className = 'prep-worker prep-worker-' + w.status;
    item.setAttribute('data-session', w.session_id);
    item.setAttribute('data-prep-scope', w.scope);
    item.innerHTML =
      '<span class="badge info prep-worker-initials">' +
        escapeHtml(w.initials || '—') + '</span>' +
      '<span class="prep-worker-name">' + escapeHtml(w.employee || 'Unassigned') +
        '</span>' +
      '<span class="prep-clock prep-timer" data-prep-timer data-employee="' +
        escapeHtml(w.employee_id || '') + '" data-session="' + w.session_id +
        '" data-prep-scope="' + w.scope + '"' +
        (w.scope === 'both' ? ' data-prep-both="1"' : '') +
        ' data-status="' + w.status + '" data-base-seconds="' + w.base_seconds +
        '"' + (w.segment_epoch ? ' data-segment-epoch="' + w.segment_epoch + '"' : '') +
        ' data-elapsed="' + w.elapsed + '" data-server-epoch="' +
        w.server_epoch + '">' + clockLabel(w.elapsed) + '</span>' +
      '<span class="badge prep-worker-status ' + prepBadgeClass(w.status) + '">' +
        w.status_label + '</span>' +
      '<span class="prep-worker-when muted small">' +
        (w.started ? 'Started ' + escapeHtml(w.started) : '') +
        (w.finished ? ' · Done ' + escapeHtml(w.finished) : '') +
        ' · ' + w.total_label + '</span>';
    var actions = document.createElement('span');
    actions.className = 'prep-worker-actions';
    if (w.status === 'running') {
      actions.appendChild(prepButton('pause', entryId, w.session_id, null, w.scope));
      actions.appendChild(prepButton('done', entryId, w.session_id, null, w.scope));
    } else if (w.status === 'paused') {
      actions.appendChild(prepButton('resume', entryId, w.session_id, null, w.scope));
      actions.appendChild(prepButton('done', entryId, w.session_id, null, w.scope));
    }
    item.appendChild(actions);
    list.appendChild(item);
  });
}

function prepHint(state) {
  var label = prepScopeLabel(state.scope);
  var lower = label.toLowerCase();
  if (state.status === 'none') {
    return 'Press Start ' + label + ' to time the ' + lower +
      ' work on this vehicle.';
  }
  if (prepHasMyClock(state)) {
    return 'Your ' + lower + ' clock for this vehicle is in the list above.';
  }
  if (state.status === 'running') {
    return state.running_count + ' clock' + (state.running_count === 1 ? '' : 's') +
      ' running — the ' + lower + ' clock keeps counting if you close the page.';
  }
  if (state.status === 'paused') {
    return 'Paused: paused time is not added to the ' + lower + ' total.';
  }
  return label + ' complete — total active ' + lower + ' prep time ' +
    state.total_label + '.';
}

// Whether the acting employee already holds a clock in this clock set. They get
// their own buttons in the list, not a button that would be refused. The other
// set of the vehicle is a different clock, so it does not count.
function prepHasMyClock(state) {
  var mine = CURRENT_EMPLOYEE;
  if (!mine) return false;
  return (state.workers || []).some(function (w) {
    return String(w.employee_id) === String(mine);
  });
}

// Swap one clock set's buttons over to the ones its new state allows: Start
// when nobody is on that set, otherwise "+ Add Me" so another employee can run
// their own clock on it. The other set keeps its buttons.
function renderPrepActions(setEl, entryId, scope, state, entryCompleted) {
  var actions = setEl.querySelector('.prep-actions');
  if (!actions) return;
  var buttons = actions.querySelectorAll('[data-prep-action], [data-prep-join]');
  for (var i = 0; i < buttons.length; i++) buttons[i].remove();
  var done = entryCompleted === undefined
    ? state.status === 'finished' : !!entryCompleted;
  if (state.status === 'none') {
    actions.insertBefore(prepButton('start', entryId, null, null, scope),
      actions.firstChild);
  } else if (!done && !prepHasMyClock(state)) {
    actions.insertBefore(prepJoinButton(entryId, scope), actions.firstChild);
  }
  var hint = actions.querySelector('.prep-hint');
  if (hint) hint.textContent = prepHint(state);
}

function renderPrepHistory(setEl, state, scope) {
  var existing = setEl.querySelector('.prep-history');
  if (existing) existing.remove();
  if (!state.events || !state.events.length) return;
  var details = document.createElement('details');
  details.className = 'prep-history';
  var summary = document.createElement('summary');
  summary.textContent = prepScopeLabel(scope) +
    ' prep history (' + state.events.length + ')';
  var list = document.createElement('ul');
  list.className = 'prep-events';
  state.events.forEach(function (event) {
    var item = document.createElement('li');
    var type = document.createElement('span');
    type.className = 'prep-event prep-event-' + event.type;
    type.textContent = event.label;
    var when = document.createElement('span');
    when.className = 'prep-event-time';
    when.textContent = event.at;
    item.appendChild(type);
    item.appendChild(when);
    if (event.employee) {
      var who = document.createElement('span');
      who.className = 'muted small';
      who.textContent = event.employee;
      item.appendChild(who);
    }
    var total = document.createElement('span');
    total.className = 'muted small';
    total.textContent = '· running total ' + event.elapsed;
    item.appendChild(total);
    list.appendChild(item);
  });
  details.appendChild(summary);
  details.appendChild(list);
  setEl.appendChild(details);
}

// Bind every timer button in a block, once each. Rows are re-rendered after an
// action, so the buttons JS creates need the same click handling.
function bindPrepButtons(root) {
  root.querySelectorAll('[data-prep-action], [data-prep-join]')
    .forEach(function (btn) {
      if (btn.getAttribute('data-prep-bound')) return;
      btn.setAttribute('data-prep-bound', '1');
      btn.addEventListener('click', function () { runPrepAction(btn); });
    });
}

// Post one action to the server and return the answer as an object, whatever
// came back. Every answer the board gives is JSON, but a login redirect, a
// server error page or a dropped connection is not: those used to throw out of
// r.json() and be reported as a bare "Try again.", which told the employee
// nothing and left them unable to tell whether their clock had started. An
// answer we cannot read comes back as {ok: false, unreadable: true} so the
// caller can re-read the vehicle's real state instead of guessing.
function postBoardAction(url, body) {
  return fetch(url, { method: 'POST', body: body }).then(function (r) {
    return r.text().then(function (text) {
      var data = null;
      try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
      if (data && typeof data === 'object') return data;
      return { ok: false, unreadable: true };
    });
  }, function () {
    return { ok: false, unreadable: true };
  });
}

function runPrepAction(btn) {
  // A "+ Add Me" press is simply a Start on that clock set for whoever is
  // signed in; a Pause / Resume / Done carries the session and the clock set
  // its button lives in.
  var join = btn.getAttribute('data-prep-join');
  var action = btn.getAttribute('data-prep-action') || (join ? 'start' : null);
  var entryId = btn.getAttribute('data-entry') || join;
  var scope = btn.getAttribute('data-prep-scope') || 'inside';
  if (!PREP_ACTIONS[action] || !entryId) return;
  if (!CURRENT_EMPLOYEE) {
    alert('Please pick your name first.');
    window.location.href = '/select';
    return;
  }
  btn.disabled = true;
  var body = new FormData();
  body.append('employee_id', CURRENT_EMPLOYEE);
  body.append('scope', scope);
  if (btn.getAttribute('data-session')) {
    body.append('session_id', btn.getAttribute('data-session'));
  }
  postBoardAction('/entry/' + entryId + '/prep/' + action, body).then(function (data) {
    var row = prepRow(entryId);
    if (!data || !data.ok) {
      btn.disabled = false;
      if (data && data.error) {
        alert(data.error);
        return;
      }
      // The press may or may not have landed, so re-read every clock rather
      // than telling the employee to press again and risk starting a second
      // clock: the re-sync paints the row with whatever is really recorded.
      resyncPrepTimers();
      alert('Could not ' + action + ' this vehicle right now. The board has ' +
        'been re-checked — look at the clock before pressing again.');
      return;
    }
    var state = data.state;
    // A press moves exactly one clock of one clock set. On a shared screen that
    // can be a colleague's, so only the acting employee's own "Now Working"
    // card is ever added or removed here.
    var isMine = !!(data.worker && data.worker.employee_id &&
      String(data.worker.employee_id) === String(CURRENT_EMPLOYEE));
    if (data.action === 'start' && isMine) {
      var ck = row ? row.closest('.vrow').querySelector('.checklist') : null;
      if (ck) ck.setAttribute('data-tasks-hidden', 'false');
      var badge = row ? row.closest('.vrow').querySelector('.entry-status') : null;
      if (badge) {
        badge.textContent = 'In Progress';
        badge.className = 'badge entry-status info';
      }
      addNowWorker(CURRENT_EMPLOYEE, data.employee, data.initials, data.unit,
        data.worker);
    }
    if (row) {
      // Both clock sets come back with every press, so the other one stays in
      // step even though only this one was touched.
      applyPrepState(row, state, data.entry_completed);
      bindPrepButtons(row);
    }
    if (data.action === 'done') {
      // The vehicle is only finished once the last employee working it is
      // done, so only then does the row lock down.
      if (data.entry_completed && data.progress) {
        var vrow = btn.closest('.vrow');
        if (vrow) {
          var bar = vrow.querySelector('.progress-fill');
          var label = vrow.querySelector('.pct');
          if (bar) bar.style.width = (data.progress.pct || 100) + '%';
          if (label) {
            label.textContent = data.progress.done + '/' + data.progress.total +
              ' — ' + data.progress.pct + '%';
          }
          var statusBadge = vrow.querySelector('.entry-status');
          if (statusBadge) {
            statusBadge.textContent = 'Completed';
            statusBadge.className = 'badge entry-status success';
          }
          vrow.querySelectorAll('.ck input').forEach(function (chk) { chk.disabled = true; });
          var replaceBtn = vrow.querySelector('[data-modal-target^="modal-replace-"]');
          if (replaceBtn) replaceBtn.remove();
          var skipBtn = vrow.querySelector('[data-modal-target^="modal-skip-"]');
          if (skipBtn) skipBtn.remove();
        }
      }
      // Finishing a vehicle changes the day totals.
      updateStats(data.counters);
      // This employee is free again, so leave the "Now Working" floor.
      if (isMine) removeNowWorker(CURRENT_EMPLOYEE);
    }
    tickPrepTimers();
  }).catch(function () {
    // Only a fault in this handler itself lands here, since the request can no
    // longer reject. Re-sync so the row matches what is really recorded.
    btn.disabled = false;
    resyncPrepTimers();
  });
}

// Re-sync the clocks from the server after a tab has been in the background,
// so time spent hidden is added to the running timers instead of being lost.
function resyncPrepTimers() {
  var url = '/prep/active';
  var match = /[?&]date=([\d-]+)/.exec(window.location.search);
  if (match) url += '?date=' + match[1];
  fetch(url).then(function (r) { return r.json(); }).then(function (data) {
    if (!data || !data.sessions) return;
    serverOffset = data.now - Date.now();
    Object.keys(data.sessions).forEach(function (entryId) {
      var row = prepRow(entryId);
      // Re-rendering a row replaces its buttons, so the fresh ones are bound
      // again here the same way a press re-binds them.
      if (row) { applyPrepState(row, data.sessions[entryId]); bindPrepButtons(row); }
    });
    // Each "Now Working" card holds its own employee's clock, matched by
    // employee: a vehicle worked by a crew has one card per person.
    document.querySelectorAll('.now-worker-timer[data-prep-employee]')
      .forEach(function (el) {
        var state = (data.workers || {})[el.getAttribute('data-prep-employee')];
        if (state) applyClock(el, state);
      });
    // The card names the side being timed, and a card can be showing a clock
    // for the other set of the same vehicle after a press.
    document.querySelectorAll('.now-worker[data-employee]')
      .forEach(function (card) {
        var state = (data.workers || {})[card.getAttribute('data-employee')];
        var unit = card.querySelector('.now-worker-unit');
        if (state && unit) unit.textContent = nowWorkerUnit(state.vehicle, state);
      });
    tickPrepTimers();
  }).catch(function () { /* keep the last known values */ });
}

function updateStats(counters) {
  if (!counters) return;
  Object.keys(counters).forEach(function (key) {
    var el = document.querySelector('[data-stat="' + key + '"]');
    if (!el) return;
    el.textContent = key === 'overall' ? counters[key] + '%' : counters[key];
  });
}

// Strip one prep block (a vehicle or one of its clock sets) of everything that
// could start or move a clock, and label it skipped.
function markPrepSkipped(block) {
  var actions = block.querySelector('.prep-actions');
  if (actions) actions.remove();
  block.className = block.className.replace(/\bprep-\w+\b/g, '') + ' prep-skipped';
  block.querySelectorAll('[data-prep-timer]').forEach(function (t) {
    t.setAttribute('data-status', 'skipped');
  });
  block.querySelectorAll('.prep-worker-actions').forEach(function (a) {
    a.remove();
  });
  var status = block.querySelector('.prep-status');
  if (status) {
    status.textContent = 'Skipped';
    status.className = 'badge prep-status warn';
  }
}

function markSkipped(row, entryId, reason, unskipUrl) {
  var ck = row.querySelector('.checklist');
  if (ck) ck.remove();
  var prepBlock = row.querySelector('.prep');
  if (prepBlock) {
    // A skipped vehicle never runs a clock, so drop the controls of both clock
    // sets and freeze every clock on the vehicle.
    markPrepSkipped(prepBlock);
    prepScopes(prepBlock).forEach(markPrepSkipped);
  }
  row.querySelectorAll('.done-btn').forEach(function (btn) { btn.remove(); });

  var note = row.querySelector('.skip-note');
  if (!note) {
    note = document.createElement('p');
    note.className = 'muted small skip-note';
    note.style.cssText = 'margin:12px 0 4px';
    var progress = row.querySelector('.progress');
    if (progress) {
      row.insertBefore(note, progress);
    } else {
      row.appendChild(note);
    }
  }
  note.textContent = 'Skipped — does not count toward completion.';
  if (reason) {
    var strong = document.createElement('strong');
    strong.textContent = 'Reason: ' + reason;
    note.appendChild(document.createTextNode(' '));
    note.appendChild(strong);
    note.appendChild(document.createTextNode(' '));
  }
  note.appendChild(document.createTextNode('Un-skip to work this vehicle.'));

  var badge = row.querySelector('.entry-status');
  if (badge) {
    badge.textContent = 'Skipped';
    badge.className = 'badge entry-status warn';
  }
  var fill = row.querySelector('.progress-fill');
  if (fill) fill.classList.add('fill-warn');
  var pct = row.querySelector('.pct');
  if (pct && pct.textContent.indexOf('(skipped)') === -1) {
    pct.textContent += ' (skipped)';
  }

  if (!row.querySelector('.unskip-form')) {
    var skipBtn = row.querySelector('[data-modal-target="modal-skip-' + entryId + '"]');
    if (skipBtn) {
      var unskip = document.createElement('form');
      unskip.className = 'unskip-form';
      unskip.method = 'post';
      unskip.action = unskipUrl || '/entry/' + entryId + '/unskip';
      unskip.style.display = 'inline';
      var ub = document.createElement('button');
      ub.type = 'submit';
      ub.className = 'btn small secondary';
      ub.textContent = 'Un-skip';
      unskip.appendChild(ub);
      skipBtn.parentNode.insertBefore(unskip, skipBtn);
      skipBtn.remove();
    }
  }

  var modal = document.getElementById('modal-skip-' + entryId);
  if (modal) modal.remove();
}

function skipWork(form) {
  var entryId = form.getAttribute('data-skip-entry');
  var unskipUrl = form.getAttribute('data-unskip-url') || '';
  var btn = form.querySelector('[type="submit"]');
  if (btn) btn.disabled = true;
  var body = new FormData(form);
  fetch(form.getAttribute('action'), {
    method: 'POST',
    headers: { 'Accept': 'application/json' },
    body: body
  }).then(function (r) {
    return r.json().then(function (data) {
      if (!data.ok) {
        if (btn) btn.disabled = false;
        alert(data.error || 'Could not skip this vehicle.');
        return;
      }
      var row = document.getElementById('row-' + entryId);
      if (row) {
        var reason = data.reason || body.get('reason') || '';
        markSkipped(row, entryId, reason, unskipUrl);
      }
      // Skipping does not complete a vehicle, so refresh the day totals
      // (completed stays put; remaining and completion % change).
      updateStats(data.counters);
      var modal = document.getElementById('modal-skip-' + entryId);
      if (modal) modal.classList.remove('open');
    });
  }).catch(function () {
    if (btn) btn.disabled = false;
    alert('Could not skip this vehicle. Try again.');
  });
}

document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('.ck input').forEach(function (chk) {
    if (chk.type !== 'checkbox') return;
    chk.addEventListener('change', function () {
      if (chk.disabled) return;
      var entryId = chk.dataset.entry;
      var taskName = chk.dataset.task;
      var checked = chk.checked;
      var employeeId = CURRENT_EMPLOYEE || null;
      var body = new FormData();
      body.append('checked', checked ? 'true' : 'false');
      if (employeeId) body.append('employee_id', employeeId);
      var wrap = chk.parentElement;
      fetch('/task/' + encodeURIComponent(entryId) + '/' + encodeURIComponent(taskName), {
        method: 'POST',
        body: body
      }).then(function (r) { return r.json(); }).then(function (data) {
        if (data.pct !== null && data.pct !== undefined && data.total) {
          var row = chk.closest('.vrow');
          if (row) {
            var bar = row.querySelector('.progress-fill');
            var label = row.querySelector('.pct');
            if (bar) bar.style.width = data.pct + '%';
            if (label) label.textContent = data.done + '/' + data.total + ' — ' + data.pct + '%';
          }
        }
        wrap.classList.toggle('done', checked);
      }).catch(function () {
        chk.checked = !checked;
        wrap.classList.toggle('done', checked);
      });
    });
  });

  // Prep timer workflow: Start -> Pause -> Resume -> Done, per employee, so a
  // crew can work the same vehicle at the same time.
  bindPrepButtons(document);

  // Live clocks. The server supplies the banked seconds and the moment each
  // segment began, so these keep counting across refreshes and hidden tabs.
  readServerOffset();
  tickPrepTimers();
  window.setInterval(tickPrepTimers, 1000);
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) resyncPrepTimers();
  });

  document.querySelectorAll('.skip-form').forEach(function (form) {
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      skipWork(form);
    });
  });

  // modal openers
  document.querySelectorAll('[data-modal-target]').forEach(function (btn) {
    btn.addEventListener('click', function (e) {
      e.preventDefault();
      var id = btn.getAttribute('data-modal-target');
      var modal = document.getElementById(id);
      if (modal) modal.classList.add('open');
    });
  });
  document.querySelectorAll('[data-modal-close]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var id = btn.getAttribute('data-modal-close');
      var modal = document.getElementById(id);
      if (modal) modal.classList.remove('open');
    });
  });
  document.querySelectorAll('.modal-bg').forEach(function (m) {
    m.addEventListener('click', function (e) {
      if (e.target === m) m.classList.remove('open');
    });
  });

  // The transit dropdown is collapsed by default. Open it when the page is
  // loaded with a row anchor inside it (e.g. right after an un-skip).
  if (window.location.hash && window.location.hash.length > 1) {
    var anchored = document.getElementById(window.location.hash.slice(1));
    var section = anchored && anchored.closest ? anchored.closest('details') : null;
    if (section) section.open = true;
  }

  // photo viewer (incident report photos)
  var photos = document.querySelectorAll('.photo-grid .photo-item img[data-photo-viewer]');
  if (photos.length) {
    var viewer = document.getElementById('photo-viewer');
    var viewerImg = document.getElementById('photo-viewer-img');
    var viewerCaption = document.getElementById('photo-viewer-caption');
    var viewerCount = document.getElementById('photo-viewer-count');
    var viewerPrev = document.getElementById('photo-viewer-prev');
    var viewerNext = document.getElementById('photo-viewer-next');
    var currentPhoto = 0;

    function showPhoto(index) {
      currentPhoto = (index + photos.length) % photos.length;
      viewerImg.src = photos[currentPhoto].src;
      viewerImg.alt = photos[currentPhoto].getAttribute('alt') || 'Incident photo';
      viewerCaption.textContent = photos[currentPhoto].getAttribute('data-caption') || '';
      viewerCount.textContent = (currentPhoto + 1) + ' of ' + photos.length;
      viewerPrev.style.display = photos.length > 1 ? '' : 'none';
      viewerNext.style.display = photos.length > 1 ? '' : 'none';
    }

    function openPhoto(index) {
      showPhoto(index);
      viewer.classList.add('open');
      viewer.setAttribute('aria-hidden', 'false');
      document.body.style.overflow = 'hidden';
    }

    function closePhoto() {
      viewer.classList.remove('open');
      viewer.setAttribute('aria-hidden', 'true');
      document.body.style.overflow = '';
    }

    photos.forEach(function (img, i) {
      img.addEventListener('click', function () { openPhoto(i); });
    });

    document.getElementById('photo-viewer-close').addEventListener('click', closePhoto);
    viewer.addEventListener('click', function (e) {
      if (e.target === viewer) closePhoto();
    });
    viewerPrev.addEventListener('click', function () { showPhoto(currentPhoto - 1); });
    viewerNext.addEventListener('click', function () { showPhoto(currentPhoto + 1); });
    document.addEventListener('keydown', function (e) {
      if (!viewer.classList.contains('open')) return;
      if (e.key === 'Escape') closePhoto();
      else if (e.key === 'ArrowLeft') showPhoto(currentPhoto - 1);
      else if (e.key === 'ArrowRight') showPhoto(currentPhoto + 1);
    });
  }
});

function updateStatusIndicators() {
  // optional hook; board status is recomputed server-side on reload
}
