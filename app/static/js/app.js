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

function addNowWorker(empId, name, initials, vehicle, prep) {
  var grid = document.getElementById('now-working-grid');
  if (!grid) return;
  var empty = document.getElementById('now-working-empty');
  if (empty) empty.remove();
  var existing = grid.querySelector('.now-worker[data-employee="' + empId + '"]');
  if (existing) {
    var unit = existing.querySelector('.now-worker-unit');
    if (unit) unit.textContent = vehicle;
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
  unitEl.textContent = vehicle;
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
  timer.setAttribute('data-prep-unit', state.vehicle || '');
  timer.setAttribute('data-status', state.status);
  timer.setAttribute('data-base-seconds', state.base_seconds);
  if (state.segment_epoch) {
    timer.setAttribute('data-segment-epoch', state.segment_epoch);
  } else {
    timer.removeAttribute('data-segment-epoch');
  }
  timer.setAttribute('data-server-epoch', state.server_epoch);
  timer.textContent = clockLabel(state.elapsed);
}

// Recompute every live timer from the server's base values.
function tickPrepTimers() {
  var timers = document.querySelectorAll('[data-prep-timer]');
  for (var i = 0; i < timers.length; i++) {
    var el = timers[i];
    if (el.getAttribute('data-status') !== 'running') continue;
    var base = parseInt(el.getAttribute('data-base-seconds'), 10) || 0;
    var segment = parseInt(el.getAttribute('data-segment-epoch'), 10);
    var elapsed = base;
    if (!isNaN(segment)) {
      elapsed = base + Math.max(0, Math.floor((serverNow() - segment) / 1000));
    }
    el.textContent = clockLabel(elapsed);
  }
}

function applyPrepState(row, state) {
  if (!row || !state) return;
  var timer = row.querySelector('[data-prep-timer]');
  if (timer) {
    timer.setAttribute('data-status', state.status);
    timer.setAttribute('data-base-seconds', state.base_seconds);
    if (state.segment_epoch) {
      timer.setAttribute('data-segment-epoch', state.segment_epoch);
    } else {
      timer.removeAttribute('data-segment-epoch');
    }
    timer.setAttribute('data-server-epoch', state.server_epoch);
    timer.textContent = clockLabel(state.elapsed);
  }
  row.className = row.className.replace(/\bprep-\w+\b/g, '').replace(/\s+/g, ' ') +
    ' prep-' + state.status;
  var badge = row.querySelector('.prep-status');
  if (badge) {
    badge.textContent = state.status_label;
    badge.className = 'badge prep-status ' + prepBadgeClass(state.status);
  }
  var meta = row.querySelector('.prep-meta');
  if (meta) {
    var parts = [];
    if (state.employee) parts.push(state.initials + ' ' + state.employee);
    else parts.push('No employee recorded');
    if (state.started) parts.push('Started ' + state.started);
    if (state.finished) parts.push('Done ' + state.finished);
    if (state.status === 'finished') {
      parts.push('Total prep ' + state.total_label);
    } else if (state.status === 'paused') {
      parts.push('timer paused at ' + state.total_label);
    }
    meta.textContent = parts.join(' · ');
  }
}

function prepBadgeClass(status) {
  if (status === 'finished') return 'success';
  if (status === 'running') return 'info';
  if (status === 'paused') return 'warn';
  return 'muted';
}

function prepButton(action, entryId, extraClass) {
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
  btn.textContent = labels[action];
  return btn;
}

function prepHint(state) {
  if (state.status === 'none') {
    return 'Press Start to begin the clock on this vehicle.';
  }
  if (state.status === 'running') {
    return 'Clock is running — the prep time keeps counting if you close the page.';
  }
  if (state.status === 'paused') {
    return 'Paused: paused time is not added to the prep total.';
  }
  return 'Prep complete — total active prep time ' + state.total_label + '.';
}

// Swap the row's buttons over to the ones the new state allows.
function renderPrepActions(row, entryId, state) {
  var actions = row.querySelector('.prep-actions');
  if (!actions) return;
  var buttons = actions.querySelectorAll('[data-prep-action]');
  for (var i = 0; i < buttons.length; i++) buttons[i].remove();
  if (state.status === 'none') {
    actions.insertBefore(prepButton('start', entryId), actions.firstChild);
  } else if (state.status === 'running') {
    actions.insertBefore(prepButton('done', entryId), actions.firstChild);
    actions.insertBefore(prepButton('pause', entryId), actions.firstChild);
  } else if (state.status === 'paused') {
    actions.insertBefore(prepButton('done', entryId), actions.firstChild);
    actions.insertBefore(prepButton('resume', entryId), actions.firstChild);
  }
  var hint = actions.querySelector('.prep-hint');
  if (hint) hint.textContent = prepHint(state);
}

function renderPrepHistory(row, state) {
  var existing = row.querySelector('.prep-history');
  if (existing) existing.remove();
  if (!state.events || !state.events.length) return;
  var details = document.createElement('details');
  details.className = 'prep-history';
  var summary = document.createElement('summary');
  summary.textContent = 'Prep history (' + state.events.length + ')';
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
  row.appendChild(details);
}

function runPrepAction(btn) {
  var action = btn.getAttribute('data-prep-action');
  var entryId = btn.getAttribute('data-entry');
  if (!PREP_ACTIONS[action]) return;
  if (!CURRENT_EMPLOYEE) {
    alert('Please pick your name first.');
    window.location.href = '/select';
    return;
  }
  btn.disabled = true;
  var body = new FormData();
  body.append('employee_id', CURRENT_EMPLOYEE);
  fetch('/entry/' + entryId + '/prep/' + action, {
    method: 'POST',
    body: body
  }).then(function (r) {
    return r.json().then(function (data) { return { ok: r.ok, data: data }; });
  }).then(function (res) {
    var data = res.data || {};
    var row = prepRow(entryId);
    if (!data.ok) {
      btn.disabled = false;
      alert(data.error || 'Could not ' + action + ' this vehicle.');
      return;
    }
    var state = data.state;
    if (data.action === 'start') {
      var ck = row ? row.closest('.vrow').querySelector('.checklist') : null;
      if (ck) ck.setAttribute('data-tasks-hidden', 'false');
      var badge = row ? row.closest('.vrow').querySelector('.entry-status') : null;
      if (badge) {
        badge.textContent = 'In Progress';
        badge.className = 'badge entry-status info';
      }
      addNowWorker(CURRENT_EMPLOYEE, data.employee, data.initials, data.unit,
        data.state);
    }
    if (row) {
      applyPrepState(row, state);
      renderPrepActions(row, entryId, state);
      renderPrepHistory(row, state);
    }
    if (data.action === 'done') {
      if (data.progress) {
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
      // The employee is free again, so leave the "Now Working" floor.
      removeNowWorker(CURRENT_EMPLOYEE);
    }
  }).catch(function () {
    btn.disabled = false;
    alert('Could not ' + action + ' this vehicle. Try again.');
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
    var byUnit = {};
    Object.keys(data.sessions).forEach(function (entryId) {
      var state = data.sessions[entryId];
      byUnit[state.vehicle] = state;
      var row = prepRow(entryId);
      if (row) applyPrepState(row, state);
    });
    document.querySelectorAll('.now-worker-timer[data-prep-unit]')
      .forEach(function (el) {
        var state = byUnit[el.getAttribute('data-prep-unit')];
        if (state) applyPrepState(el, state);
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

function markSkipped(row, entryId, reason, unskipUrl) {
  var ck = row.querySelector('.checklist');
  if (ck) ck.remove();
  var prepBlock = row.querySelector('.prep');
  if (prepBlock) {
    // A skipped vehicle never runs a clock, so drop its controls.
    var actions = prepBlock.querySelector('.prep-actions');
    if (actions) actions.remove();
    prepBlock.className = prepBlock.className.replace(/\bprep-\w+\b/g, '') +
      ' prep-skipped';
    var timer = prepBlock.querySelector('[data-prep-timer]');
    if (timer) timer.setAttribute('data-status', 'skipped');
    var status = prepBlock.querySelector('.prep-status');
    if (status) {
      status.textContent = 'Skipped';
      status.className = 'badge prep-status warn';
    }
  }
  var doneBtn = row.querySelector('.done-btn');
  if (doneBtn) doneBtn.remove();

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

  // Prep timer workflow: Start -> Pause -> Resume -> Done
  document.querySelectorAll('[data-prep-action]').forEach(function (btn) {
    btn.addEventListener('click', function () { runPrepAction(btn); });
  });

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
