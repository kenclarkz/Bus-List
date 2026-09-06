'use strict';

var CURRENT_EMPLOYEE = (function () {
  try { return localStorage.getItem('current_employee') || null; } catch (e) { return null; }
})();

function setEmployee(sel) {
  CURRENT_EMPLOYEE = sel.value || null;
  try { localStorage.setItem('current_employee', CURRENT_EMPLOYEE); } catch (e) {}
  var empSel = document.getElementById('current-employee');
  if (empSel) empSel.value = CURRENT_EMPLOYEE || '';
}

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
    var empSel = document.getElementById('current-employee');
    alert('Select your name from the "I am:" dropdown first.');
    if (empSel) empSel.focus();
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

document.addEventListener('DOMContentLoaded', function () {
  var empSel = document.getElementById('current-employee');
  if (empSel && CURRENT_EMPLOYEE) empSel.value = CURRENT_EMPLOYEE;

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
