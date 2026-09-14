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

function addNowWorker(empId, name, initials, vehicle) {
  var grid = document.getElementById('now-working-grid');
  if (!grid) return;
  var empty = document.getElementById('now-working-empty');
  if (empty) empty.remove();
  var existing = grid.querySelector('.now-worker[data-employee="' + empId + '"]');
  if (existing) {
    var unit = existing.querySelector('.now-worker-unit');
    if (unit) unit.textContent = vehicle;
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
  grid.appendChild(card);
  var count = document.getElementById('now-working-count');
  if (count) {
    count.textContent = grid.querySelectorAll('.now-worker').length + ' on the floor';
  }
}

function startWork(btn) {
  var entryId = btn.getAttribute('data-entry');
  var employeeId = CURRENT_EMPLOYEE;
  if (!employeeId) {
    alert('Please pick your name first.');
    window.location.href = '/select';
    return;
  }
  var body = new FormData();
  body.append('employee_id', employeeId);
  body.append('entry_id', entryId);
  btn.disabled = true;
  fetch('/start-work', {
    method: 'POST',
    body: body
  }).then(function (r) { return r.json(); }).then(function (data) {
    if (!data.ok) {
      btn.disabled = false;
      alert(data.error || 'Could not start work on this vehicle.');
      return;
    }
    var row = btn.closest('.vrow');
    if (row) {
      var ck = row.querySelector('.checklist');
      var startRow = row.querySelector('.row-start');
      if (ck) ck.setAttribute('data-tasks-hidden', 'false');
      if (startRow) startRow.setAttribute('data-tasks-hidden', 'false');
      var badge = row.querySelector('.entry-status');
      if (badge) {
        badge.textContent = 'In Progress';
        badge.className = 'badge entry-status info';
      }
    }
    addNowWorker(employeeId, data.employee, data.initials, data.vehicle);
  }).catch(function () {
    btn.disabled = false;
    alert('Could not start work on this vehicle. Try again.');
  });
}

function completeWork(btn) {
  var entryId = btn.getAttribute('data-entry');
  btn.disabled = true;
  fetch('/entry/' + entryId + '/complete', {
    method: 'POST'
  }).then(function (r) { return r.json(); }).then(function (data) {
    if (!data.ok) {
      btn.disabled = false;
      alert(data.error || 'Could not complete this vehicle.');
      return;
    }
    var row = btn.closest('.vrow');
    if (row) {
      var badge = row.querySelector('.entry-status');
      if (badge) {
        badge.textContent = 'Completed';
        badge.className = 'badge entry-status success';
      }
      var bar = row.querySelector('.progress-fill');
      var label = row.querySelector('.pct');
      if (bar) bar.style.width = (data.pct || 100) + '%';
      if (label) label.textContent = data.done + '/' + data.total + ' — ' + data.pct + '%';
      var startRow = row.querySelector('.row-start');
      if (startRow) startRow.remove();
      btn.remove();
      var replaceBtn = row.querySelector('[data-modal-target^="modal-replace-"]');
      if (replaceBtn) replaceBtn.remove();
      var skipBtn = row.querySelector('[data-modal-target^="modal-skip-"]');
      if (skipBtn) skipBtn.remove();
      row.querySelectorAll('.ck input').forEach(function (chk) { chk.disabled = true; });
    }
  }).catch(function () {
    btn.disabled = false;
    alert('Could not complete this vehicle. Try again.');
  });
}

function markSkipped(row, entryId, reason, unskipUrl) {
  var ck = row.querySelector('.checklist');
  if (ck) ck.remove();
  var startRow = row.querySelector('.row-start');
  if (startRow) startRow.remove();
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
  note.textContent = 'Skipped — counts toward completion.';
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

  document.querySelectorAll('.start-btn').forEach(function (btn) {
    btn.addEventListener('click', function () { startWork(btn); });
  });

  document.querySelectorAll('.done-btn').forEach(function (btn) {
    btn.addEventListener('click', function () { completeWork(btn); });
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
});

function updateStatusIndicators() {
  // optional hook; board status is recomputed server-side on reload
}
