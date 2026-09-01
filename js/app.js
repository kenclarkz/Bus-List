'use strict';

/* Detailing Operations Dashboard — static rebuild.
   Runs entirely in the browser with no backend. All data is persisted to
   localStorage under a single key. */

var STORE_KEY = 'detailing_ops_v1';

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function nextId(items) {
  return items.reduce(function (m, i) { return Math.max(m, i.id || 0); }, 0) + 1;
}

function nowIso() {
  return new Date().toISOString();
}

function pad2(n) { return String(n).padStart(2, '0'); }

function todayIso() {
  var d = new Date();
  return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate());
}

function fmtDate(iso) {
  if (!iso) return '\u2014';
  var d = new Date(iso);
  if (isNaN(d.getTime())) return '\u2014';
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

function fmtDateTime(iso) {
  if (!iso) return '\u2014';
  var d = new Date(iso);
  if (isNaN(d.getTime())) return '\u2014';
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ' ' +
         d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

function fmtLongDate(iso) {
  var d = new Date(iso);
  if (isNaN(d.getTime())) return '\u2014';
  return d.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
}

function fmtTime(iso) {
  if (!iso) return '';
  var d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

function daysBetween(iso, now) {
  return (now.getTime() - new Date(iso).getTime()) / 86400000;
}

function normalizeUnit(u) {
  return String(u || '').replace(/[^0-9]/g, '');
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

function newEntry(id, vehicleId, order, checklist) {
  var tasks = (checklist || []).map(function (name) {
    return { name: name, completed: false, completed_at: null, employee_id: null };
  });
  return {
    id: id,
    vehicle_id: vehicleId,
    is_replacement: false,
    replacement_of_vehicle_id: null,
    order_index: order,
    tasks: tasks
  };
}

function seedDemo() {
  var st = defaultState();
  st.employees = [
    { id: 1, name: 'User', active: true },
    { id: 2, name: 'Maria', active: true },
    { id: 3, name: 'Dan', active: true }
  ];
  var now = Date.now();
  function ago(days) { return new Date(now - days * 86400000).toISOString(); }

  st.vehicles = [
    { id: 1, unit_number: '142', vehicle_type: 'Coach', route: 'Route 7', status: 'Active', last_washed: ago(3), last_detailed: ago(6), cleaning_frequency: 7, notes: '', active: true },
    { id: 2, unit_number: '155', vehicle_type: 'Van', route: 'Route 12', status: 'Active', last_washed: ago(1), last_detailed: ago(3), cleaning_frequency: 5, notes: '', active: true },
    { id: 3, unit_number: '187', vehicle_type: 'Coach', route: 'Route 3', status: 'Active', last_washed: ago(9), last_detailed: ago(12), cleaning_frequency: 7, notes: '', active: true },
    { id: 4, unit_number: '201', vehicle_type: 'Bus', route: 'Route 5', status: 'Active', last_washed: null, last_detailed: null, cleaning_frequency: 7, notes: '', active: true },
    { id: 5, unit_number: '210', vehicle_type: 'Mini', route: 'Route 9', status: 'Active', last_washed: ago(1), last_detailed: ago(2), cleaning_frequency: 3, notes: '', active: true }
  ];

  st.schedule = {
    work_date: todayIso(), finalized: false, finalized_at: null, summary: null,
    entries: [
      newEntry(1, 1, 0, st.settings.checklist),
      newEntry(2, 2, 1, st.settings.checklist),
      newEntry(3, 3, 2, st.settings.checklist),
      newEntry(4, 4, 3, st.settings.checklist),
      newEntry(5, 5, 4, st.settings.checklist)
    ]
  };

  completeTask(st.schedule.entries[0], 'Sweep', 2);
  completeTask(st.schedule.entries[0], 'Mop', 2);
  completeTask(st.schedule.entries[1], 'Sweep', 2);
  completeTask(st.schedule.entries[1], 'Mop', 3);
  completeTask(st.schedule.entries[1], 'Windows', 3);
  return st;
}

function defaultState() {
  var checklist = ['Sweep', 'Mop', 'Windows', 'Seats', 'Bathroom', 'Dump', 'Bay Checked', 'Final Inspection'];
  return {
    settings: {
      recent_days: 2,
      due_soon_days: 7,
      location: 'Main Depot',
      checklist: checklist
    },
    employees: [],
    vehicles: [],
    schedule: { work_date: todayIso(), entries: [], finalized: false, finalized_at: null, summary: null },
    notes: [],
    replacements: [],
    days: [],
    current_employee_id: null
  };
}

function load() {
  try {
    var raw = localStorage.getItem(STORE_KEY);
    if (raw) {
      var parsed = JSON.parse(raw);
      if (parsed && parsed.settings) return parsed;
    }
  } catch (e) { /* corrupted or unavailable storage */ }
  var fresh = seedDemo();
  persist(fresh);
  return fresh;
}

function persist(st) {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(st)); }
  catch (e) { /* storage full/unavailable */ }
}

function save() {
  persist(state);
}

var state = load();

// ---------------------------------------------------------------------------
// Lookups / helpers
// ---------------------------------------------------------------------------

function getVehicle(id) {
  return state.vehicles.find(function (v) { return v.id === Number(id); });
}

function getEmployee(id) {
  if (!id) return null;
  return state.employees.find(function (e) { return e.id === Number(id); }) || null;
}

function entryProgress(entry) {
  var tasks = entry.tasks || [];
  if (!tasks.length) return { done: 0, total: 0, pct: 0 };
  var done = tasks.filter(function (t) { return t.completed; }).length;
  return { done: done, total: tasks.length, pct: Math.round(done / tasks.length * 100) };
}

function entryStatus(entry) {
  var p = entryProgress(entry);
  if (p.total && p.done === p.total) return 'completed';
  if (p.done > 0) return 'in_progress';
  return 'pending';
}

function statusIndicator(vehicle) {
  if (!vehicle.last_washed) return { label: 'Never Washed', cls: 'status-unknown' };
  var days = daysBetween(vehicle.last_washed, new Date());
  var recent = parseInt(state.settings.recent_days, 10) || 2;
  var due = parseInt(state.settings.due_soon_days, 10) || 7;
  if (days <= recent) return { label: 'Recently Washed', cls: 'status-recent' };
  if (days <= due) return { label: 'Due Soon', cls: 'status-due' };
  return { label: 'Overdue', cls: 'status-overdue' };
}

function completeTask(entry, name, employeeId) {
  var t = entry.tasks.find(function (x) { return x.name === name; });
  if (t) {
    t.completed = true;
    t.completed_at = nowIso();
    t.employee_id = employeeId || null;
  }
}

function replacementsToday() {
  var t = todayIso();
  return state.replacements.filter(function (r) {
    return (r.replaced_at || '').slice(0, 10) === t;
  });
}

function notesForDate(d) {
  return state.notes.filter(function (n) { return n.work_date === d; });
}

// ---------------------------------------------------------------------------
// Routing
// ---------------------------------------------------------------------------

function parseRoute() {
  var hash = (location.hash || '').replace(/^#/, '') || '/';
  var query = {};
  var qIdx = hash.indexOf('?');
  if (qIdx >= 0) {
    hash.slice(qIdx + 1).split('&').forEach(function (pair) {
      if (!pair) return;
      var kv = pair.split('=');
      query[decodeURIComponent(kv[0])] = decodeURIComponent(kv.slice(1).join('=') || '');
    });
    hash = hash.slice(0, qIdx);
  }
  return { parts: hash.split('/').filter(Boolean), query: query };
}

var filters = { unit: '', type: '', route: '', status: '' };

function render() {
  var route = parseRoute();
  var root = route.parts[0] || 'dashboard';
  markNav(root);
  var app = document.getElementById('app');

  if (root === 'dashboard') { renderDashboard(app); return; }
  if (root === 'vehicles' && route.parts.length === 1) { renderVehicles(app); return; }
  if (root === 'vehicles' && route.parts.length >= 2 && route.parts[2] === 'edit') { renderVehicleForm(app, route.parts[1]); return; }
  if (root === 'vehicles' && route.parts.length >= 2) { renderVehicleDetail(app, route.parts[1]); return; }
  if (root === 'add-vehicle') { renderVehicleForm(app, null); return; }
  if (root === 'end') { renderEnd(app); return; }
  if (root === 'report') { renderReport(app, route.query.date); return; }
  if (root === 'history') { renderHistory(app); return; }
  if (root === 'staff') { renderStaff(app); return; }
  if (root === 'settings') { renderSettings(app); return; }

  app.innerHTML = '<h1>Page not found</h1><div class="card"><p class="muted">The page you requested does not exist.</p><a class="btn" href="#/">Back to Today</a></div>';
}

function markNav(root) {
  var view = root;
  if (root === 'vehicles' || root === 'add-vehicle') view = 'vehicles';
  document.querySelectorAll('#nav a').forEach(function (a) {
    a.classList.toggle('active', a.getAttribute('data-view') === view);
  });
}

// ---------------------------------------------------------------------------
// Dashboard (Today's Board)
// ---------------------------------------------------------------------------

function dashboardRows() {
  return rowsFromEntries(state.schedule.entries);
}

function rowsFromEntries(entries) {
  return (entries || []).slice().sort(function (a, b) {
    return (a.order_index || 0) - (b.order_index || 0);
  }).map(function (entry) {
    var v = getVehicle(entry.vehicle_id);
    return {
      entry: entry,
      vehicle: v,
      prog: entryProgress(entry),
      status: entryStatus(entry),
      indicator: v ? statusIndicator(v) : { label: '', cls: '' }
    };
  }).filter(function (r) { return r.vehicle; });
}

function renderDashboard(app) {
  var rows = dashboardRows();
  var qUnit = filters.unit.trim().toLowerCase();
  var qType = filters.type;
  var qRoute = filters.route.trim().toLowerCase();
  var qStatus = filters.status;

  var frows = rows.filter(function (r) {
    if (qUnit && r.vehicle.unit_number.toLowerCase().indexOf(qUnit) === -1) return false;
    if (qType && r.vehicle.vehicle_type !== qType) return false;
    if (qRoute && (r.vehicle.route || '').toLowerCase().indexOf(qRoute) === -1) return false;
    if (qStatus && r.status !== qStatus) return false;
    return true;
  });

  var total = rows.length;
  var completed = rows.filter(function (r) { return r.status === 'completed'; }).length;
  var inProgress = rows.filter(function (r) { return r.status === 'in_progress'; }).length;
  var remaining = total - completed - inProgress;
  var overdue = rows.filter(function (r) { return r.indicator.label === 'Overdue'; }).length;
  var replCount = replacementsToday().length;
  var doneSum = rows.reduce(function (n, r) { return n + r.prog.done; }, 0);
  var totSum = rows.reduce(function (n, r) { return n + r.prog.total; }, 0);
  var overall = totSum ? Math.round(doneSum / totSum * 100) : 0;

  var types = state.vehicles
    .map(function (v) { return v.vehicle_type; })
    .filter(function (t, i, arr) { return t && arr.indexOf(t) === i; })
    .sort();

  var empOpts = activeEmployeeOptions();

  var html = '';
  html += '<h1>Today\u2019s Detailing Board <span class="muted small">' + esc(fmtLongDate(todayIso())) + '</span></h1>';

  html += '<div class="card flex between">';
  html += '<span class="muted"><strong>I am:</strong></span>';
  html += employeeSelect(empOpts, 'current-employee');
  html += '<span class="muted small right">Checked tasks are attributed to the selected employee</span>';
  html += '</div>';

  html += '<div class="stat-grid"><div class="stats-row">';
  html += stat('Total Vehicles', total, '');
  html += stat('Completed', completed, 'success');
  html += stat('In Progress', inProgress, 'info');
  html += stat('Remaining', remaining, 'warn');
  html += stat('Overdue', overdue, 'danger');
  html += stat('Replacements', replCount, '');
  html += stat('Completion', overall + '%', '');
  html += '</div></div>';

  html += '<div class="card"><div class="filters"><div class="flex" style="width:100%;align-items:flex-end">';
  html += '<label>Unit</label><input type="text" name="unit" value="' + esc(filters.unit) + '" placeholder="Unit #">';
  html += '<label>Type</label><select name="type">' + typeOptions(types, filters.type) + '</select>';
  html += '<label>Route</label><input type="text" name="route" value="' + esc(filters.route) + '" placeholder="Route">';
  html += '<label>Status</label><select name="status">' + statusOptions(filters.status) + '</select>';
  html += '<span class="muted small" style="margin:0 0 8px">Showing ' + frows.length + ' of ' + rows.length + '</span>';
  html += '</div></div></div>';

  if (!rows.length) {
    html += '<div class="card"><h2>No vehicles on today\u2019s board</h2>';
    html += '<p class="muted">Add vehicles to build today\u2019s work list. Data is stored in this browser.</p>';
    html += '<a class="btn" href="#/add-vehicle">Add Vehicle</a></div>';
  } else if (!frows.length) {
    html += '<div class="card"><p class="muted">No vehicles match the current filters.</p>';
    html += '<a class="btn small secondary" href="#/" onclick="clearFilters()">Clear</a></div>';
  }

  html += '<div class="board">';
  frows.forEach(function (r) {
    html += renderRow(r, empOpts);
  });
  html += '</div>';

  app.innerHTML = html;
}

function stat(lbl, num, cls) {
  return '<div class="stat ' + cls + '"><div class="num">' + esc(num) + '</div><div class="lbl">' + esc(lbl) + '</div></div>';
}

function renderRow(r, empOpts) {
  var v = r.vehicle;
  var entry = r.entry;
  var h = '';
  h += '<div class="vrow ' + (entry.is_replacement ? 'row-replacement' : '') + '">';
  h += '<div class="vrow-header">';
  h += '<span class="vnum">' + esc(v.unit_number) + '</span>';
  if (entry.is_replacement) h += '<span class="badge warn">Replacement</span>';
  h += '<div class="vmeta">' + esc(v.vehicle_type || '\u2014');
  if (v.route) h += ' &middot; Route ' + esc(v.route);
  h += '</div>';
  h += '<span class="badge ' + statusBadgeClass(r.status) + '">' + statusLabel(r.status) + '</span>';
  h += '<span class="spacer"></span>';
  h += '<span class="' + r.indicator.cls + '">' + esc(r.indicator.label) + '</span>';
  h += '<span class="muted small">Last washed ' + fmtDateTime(v.last_washed) + '</span>';
  h += '</div>';

  h += '<div class="checklist">';
  (entry.tasks || []).forEach(function (t) {
    h += '<label class="ck ' + (t.completed ? 'done' : '') + '">';
    h += '<input type="checkbox" data-action="toggle-task" data-entry="' + entry.id + '" data-task="' + esc(t.name) + '"' + (t.completed ? ' checked' : '') + '>';
    h += esc(t.name);
    if (t.completed && t.completed_at) h += ' <span class="small muted">\u00b7 ' + esc(fmtTime(t.completed_at)) + '</span>';
    h += '</label>';
  });
  h += '</div>';

  h += '<div class="progress"><div class="progress-bar"><div class="progress-fill" style="width:' + r.prog.pct + '%"></div></div>';
  h += '<span class="pct">' + r.prog.done + '/' + r.prog.total + ' \u2014 ' + r.prog.pct + '%</span></div>';

  h += '<div class="row-actions">';
  if (r.status !== 'completed') {
    h += '<button class="btn small secondary" data-action="open-replace" data-entry="' + entry.id + '">Replace Vehicle</button>';
  }
  h += '<a class="btn small" href="#/vehicles/' + v.id + '">History</a>';
  h += '</div>';
  h += '</div>';
  return h;
}

function statusBadgeClass(status) {
  if (status === 'completed') return 'success';
  if (status === 'in_progress') return 'info';
  return 'muted';
}

function statusLabel(status) {
  if (status === 'completed') return 'Completed';
  if (status === 'in_progress') return 'In Progress';
  return 'Pending';
}

function activeEmployeeOptions() {
  return state.employees.filter(function (e) { return e.active !== false; });
}

function employeeSelect(emps, id) {
  if (!emps.length) return '<select id="' + id + '"><option value="">\u2014 Nobody \u2014</option></select>';
  var h = '<select id="' + id + '"><option value="">\u2014 Select employee \u2014</option>';
  emps.forEach(function (e) {
    h += '<option value="' + e.id + '"' + (state.current_employee_id === e.id ? ' selected' : '') + '>' + esc(e.name) + '</option>';
  });
  return h + '</select>';
}

function typeOptions(types, selected) {
  var h = '<option value="">All Types</option>';
  types.forEach(function (t) {
    h += '<option value="' + esc(t) + '"' + (selected === t ? ' selected' : '') + '>' + esc(t) + '</option>';
  });
  return h;
}

function statusOptions(selected) {
  var opts = [
    ['', 'All Status'],
    ['pending', 'Pending'],
    ['in_progress', 'In Progress'],
    ['completed', 'Completed']
  ];
  return opts.map(function (o) {
    return '<option value="' + o[0] + '"' + (selected === o[0] ? ' selected' : '') + '>' + o[1] + '</option>';
  }).join('');
}

// ---------------------------------------------------------------------------
// Actions: tasks, replacements, notes, finalize
// ---------------------------------------------------------------------------

function toggleTask(entryId, taskName, checked) {
  var entry = state.schedule.entries.find(function (e) { return e.id === Number(entryId); });
  if (!entry) return;
  var task = (entry.tasks || []).find(function (t) { return t.name === taskName; });
  if (!task) return;
  task.completed = checked;
  task.completed_at = checked ? nowIso() : null;
  task.employee_id = checked ? state.current_employee_id : null;
  var v = getVehicle(entry.vehicle_id);
  if (v) {
    if (checked && taskName.toLowerCase() === 'sweep') v.last_washed = nowIso();
    if (checked && taskName.toLowerCase() === 'final inspection') v.last_detailed = nowIso();
  }
  save();
  render();
}

function openReplaceModal(entryId) {
  var entry = state.schedule.entries.find(function (e) { return e.id === Number(entryId); });
  if (!entry) return;
  var v = getVehicle(entry.vehicle_id);
  var h = '<h3>Replace Vehicle ' + esc(v.unit_number) + '</h3>';
  h += '<form data-form="replace" data-entry="' + entry.id + '">';
  h += '<label>Replacement Unit # (e.g. 155)</label>';
  h += '<input type="text" name="replacement_unit" required autocomplete="off">';
  h += '<label>Reason</label>';
  h += '<input type="text" name="reason" placeholder="e.g. down for service">';
  h += '<label>Employee</label>';
  h += employeeSelect(activeEmployeeOptions(), 'replace-employee');
  h += '<div class="flex" style="margin-top:16px">';
  h += '<button type="submit" class="btn warn">Replace</button>';
  h += '<button type="button" class="btn secondary" data-action="close-modal">Cancel</button>';
  h += '</div></form>';
  openModal(h);
}

function doReplace(entryId, unit, reason, employeeId) {
  var entry = state.schedule.entries.find(function (e) { return e.id === Number(entryId); });
  if (!entry) return;
  var original = getVehicle(entry.vehicle_id);
  if (!original) return;
  var normalized = normalizeUnit(unit);
  if (!normalized) return;

  var repl = state.vehicles.find(function (v) {
    return v.active !== false && normalizeUnit(v.unit_number) === normalized;
  });
  if (!repl) {
    repl = {
      id: nextId(state.vehicles),
      unit_number: String(unit).trim(),
      vehicle_type: original.vehicle_type,
      route: original.route,
      status: 'Active',
      last_washed: null,
      last_detailed: null,
      cleaning_frequency: original.cleaning_frequency,
      notes: '',
      active: true
    };
    state.vehicles.push(repl);
  }

  var replEntry = state.schedule.entries.find(function (e) { return e.vehicle_id === repl.id; });
  if (!replEntry) {
    replEntry = {
      id: nextId(state.schedule.entries),
      vehicle_id: repl.id,
      is_replacement: true,
      replacement_of_vehicle_id: original.id,
      order_index: entry.order_index,
      tasks: entry.tasks.map(function (t) {
        return { name: t.name, completed: t.completed, completed_at: t.completed_at, employee_id: t.employee_id };
      })
    };
    state.schedule.entries.push(replEntry);
  } else {
    replEntry.is_replacement = true;
    replEntry.replacement_of_vehicle_id = original.id;
    entry.tasks.forEach(function (t) {
      if (!t.completed) return;
      var rt = replEntry.tasks.find(function (x) { return x.name === t.name; });
      if (rt && !rt.completed) {
        rt.completed = true;
        rt.completed_at = t.completed_at;
        rt.employee_id = t.employee_id;
      }
    });
  }

  state.schedule.entries = state.schedule.entries.filter(function (e) { return e.id !== entry.id; });
  state.replacements.push({
    id: nextId(state.replacements),
    original_vehicle_id: original.id,
    replacement_vehicle_id: repl.id,
    reason: reason || '',
    replaced_at: nowIso(),
    employee_id: employeeId || null,
    source: 'manual'
  });

  save();
  closeModal();
  location.hash = '#/';
}

function addNote(form) {
  var text = form.querySelector('[name=note]').value.trim();
  if (!text) return;
  state.notes.push({
    id: nextId(state.notes),
    work_date: todayIso(),
    text: text,
    employee_id: state.current_employee_id,
    created_at: nowIso()
  });
  save();
  render();
}

function finalizeDay() {
  var sched = state.schedule;
  var rows = dashboardRows();
  var total = rows.length;
  var completed = rows.filter(function (r) { return r.status === 'completed'; }).length;
  var incomplete = total - completed;
  var doneSum = rows.reduce(function (n, r) { return n + r.prog.done; }, 0);
  var totSum = rows.reduce(function (n, r) { return n + r.prog.total; }, 0);
  var overall = totSum ? Math.round(doneSum / totSum * 100) : 0;
  var summary = { total: total, completed: completed, incomplete: incomplete, overall: overall };

  sched.finalized = true;
  sched.finalized_at = nowIso();
  sched.summary = summary;

  state.days.push({
    id: nextId(state.days),
    work_date: sched.work_date,
    finalized: true,
    finalized_at: sched.finalized_at,
    summary: summary,
    entries: JSON.parse(JSON.stringify(sched.entries)),
    replacements_count: replacementsToday().length,
    notes: JSON.parse(JSON.stringify(notesForDate(sched.work_date)))
  });

  save();
  location.hash = '#/history';
}

// ---------------------------------------------------------------------------
// Vehicles views
// ---------------------------------------------------------------------------

function renderVehicles(app) {
  var q = (filters.unit || '').toLowerCase();
  var list = state.vehicles
    .filter(function (v) { return v.active !== false; })
    .filter(function (v) { return !q || v.unit_number.toLowerCase().indexOf(q) !== -1; })
    .sort(function (a, b) { return Number(normalizeUnit(a.unit_number)) - Number(normalizeUnit(b.unit_number)); });

  var h = '<h1>Vehicles</h1>';
  h += '<div class="flex between" style="margin-bottom:16px">';
  h += '<span class="muted">' + list.length + ' active vehicles</span>';
  h += '<a class="btn" href="#/add-vehicle">+ Add Vehicle</a>';
  h += '</div>';

  h += '<div class="card"><div class="filters">';
  h += '<input type="text" name="unit" value="' + esc(filters.unit) + '" placeholder="Search unit number" style="max-width:280px">';
  h += '</div></div>';

  if (!list.length) {
    h += '<div class="card"><p class="muted">No vehicles yet.</p><a class="btn" href="#/add-vehicle">+ Add Vehicle</a></div>';
  } else {
    h += '<div class="card"><div class="table-wrap"><table>';
    h += '<tr><th>Unit</th><th>Type</th><th>Route</th><th>Status</th><th>Last Washed</th><th>Last Detailed</th></tr>';
    list.forEach(function (v) {
      h += '<tr>';
      h += '<td><a href="#/vehicles/' + v.id + '"><strong>' + esc(v.unit_number) + '</strong></a></td>';
      h += '<td>' + esc(v.vehicle_type || '\u2014') + '</td>';
      h += '<td>' + esc(v.route || '\u2014') + '</td>';
      h += '<td>' + esc(v.status || '\u2014') + '</td>';
      h += '<td>' + fmtDateTime(v.last_washed) + '</td>';
      h += '<td>' + fmtDateTime(v.last_detailed) + '</td>';
      h += '</tr>';
    });
    h += '</table></div></div>';
  }
  app.innerHTML = h;
}

function renderVehicleDetail(app, id) {
  var v = getVehicle(id);
  if (!v) {
    app.innerHTML = '<h1>Vehicle not found</h1><p class="muted"><a href="#/vehicles">Back to vehicles</a></p>';
    return;
  }
  var ind = statusIndicator(v);
  var rels = state.replacements.filter(function (r) {
    return r.original_vehicle_id === v.id || r.replacement_vehicle_id === v.id;
  });

  var h = '<div class="flex between" style="margin-bottom:16px">';
  h += '<h1 style="margin:0">Vehicle ' + esc(v.unit_number) + '</h1>';
  h += '<a class="btn secondary" href="#/vehicles/' + v.id + '/edit">Edit</a>';
  h += '</div>';

  h += '<div class="grid" style="grid-template-columns: repeat(auto-fit, minmax(160px,1fr))">';
  h += '<div class="stat"><div class="num" style="font-size:1rem">' + esc(v.vehicle_type || '\u2014') + '</div><div class="lbl">Type</div></div>';
  h += '<div class="stat"><div class="num" style="font-size:1rem">' + esc(v.route || '\u2014') + '</div><div class="lbl">Route</div></div>';
  h += '<div class="stat"><div class="num" style="font-size:1rem">' + esc(v.status || '\u2014') + '</div><div class="lbl">Status</div></div>';
  h += '<div class="stat"><div class="num" style="font-size:1rem">' + esc(v.cleaning_frequency || '\u2014') + 'd</div><div class="lbl">Frequency</div></div>';
  h += '<div class="stat"><div class="num" style="font-size:1rem"><span class="' + ind.cls + '">' + esc(ind.label) + '</span></div><div class="lbl">Wash Status</div></div>';
  h += '</div>';

  h += '<div class="card"><h2>Details</h2>';
  h += '<p><strong>Unit:</strong> ' + esc(v.unit_number) + '</p>';
  h += '<p><strong>Last Washed:</strong> ' + fmtDateTime(v.last_washed) + '</p>';
  h += '<p><strong>Last Detailed:</strong> ' + fmtDateTime(v.last_detailed) + '</p>';
  h += '<p><strong>Active:</strong> ' + (v.active === false ? 'No' : 'Yes') + '</p>';
  if (v.notes) h += '<p><strong>Notes:</strong> ' + esc(v.notes) + '</p>';
  h += '<button class="btn small danger" data-action="toggle-active" data-vehicle="' + v.id + '">' + (v.active === false ? 'Reactivate' : 'Deactivate') + '</button>';
  h += '</div>';

  h += '<div class="card"><h2>Service History</h2>';
  var events = [];
  if (v.last_washed) events.push({ at: v.last_washed, type: 'wash', note: 'Wash completed via checklist' });
  if (v.last_detailed) events.push({ at: v.last_detailed, type: 'prep', note: 'Prep completed via checklist' });
  rels.forEach(function (r) {
    if (r.original_vehicle_id === v.id) {
      var rv = getVehicle(r.replacement_vehicle_id);
      events.push({ at: r.replaced_at, type: 'replaced', note: 'Replaced by ' + (rv ? rv.unit_number : 'unknown') });
    }
    if (r.replacement_vehicle_id === v.id) {
      var ov = getVehicle(r.original_vehicle_id);
      events.push({ at: r.replaced_at, type: 'replaced', note: 'Replaced ' + (ov ? ov.unit_number : 'unknown') });
    }
  });
  events.sort(function (a, b) { return new Date(b.at) - new Date(a.at); });

  if (events.length) {
    h += '<div class="table-wrap"><table>';
    h += '<tr><th>Date</th><th>Type</th><th>Detail</th></tr>';
    events.forEach(function (ev) {
      h += '<tr><td>' + fmtDateTime(ev.at) + '</td>';
      h += '<td><span class="badge ' + (ev.type === 'wash' ? 'success' : 'info') + '">' + ev.type + '</span></td>';
      h += '<td>' + esc(ev.note) + '</td></tr>';
    });
    h += '</table></div>';
  } else {
    h += '<p class="muted">No service history yet.</p>';
  }
  h += '</div>';

  h += '<div class="card"><h2>Replacements</h2>';
  if (rels.length) {
    h += '<div class="table-wrap"><table><tr><th>Date</th><th>Direction</th><th>Vehicle</th><th>Reason</th><th>Employee</th></tr>';
    rels.forEach(function (r) {
      if (r.original_vehicle_id === v.id) {
        var rv = getVehicle(r.replacement_vehicle_id);
        h += '<tr><td>' + fmtDateTime(r.replaced_at) + '</td><td><span class="badge warn">replaced by</span></td>';
        h += '<td>' + esc(rv ? rv.unit_number : '\u2014') + '</td><td>' + esc(r.reason || '\u2014') + '</td>';
        h += '<td>' + esc(empName(r.employee_id)) + '</td></tr>';
      } else {
        var ov = getVehicle(r.original_vehicle_id);
        h += '<tr><td>' + fmtDateTime(r.replaced_at) + '</td><td><span class="badge info">replaced</span></td>';
        h += '<td>' + esc(ov ? ov.unit_number : '\u2014') + '</td><td>' + esc(r.reason || '\u2014') + '</td>';
        h += '<td>' + esc(empName(r.employee_id)) + '</td></tr>';
      }
    });
    h += '</table></div>';
  } else {
    h += '<p class="muted">No replacements recorded.</p>';
  }
  h += '</div>';

  app.innerHTML = h;
}

function empName(id) {
  var e = getEmployee(id);
  return e ? e.name : '\u2014';
}

function renderVehicleForm(app, id) {
  var v = id ? getVehicle(id) : null;
  var h = '<h1>' + (v ? 'Edit Vehicle ' + esc(v.unit_number) : 'Add Vehicle') + '</h1>';
  h += '<div class="card"><form data-form="vehicle">';
  if (v) h += '<input type="hidden" name="id" value="' + v.id + '">';

  h += '<div class="form-row">';
  h += '<div><label>Unit / Vehicle Number *</label><input type="text" name="unit_number" required value="' + esc(v ? v.unit_number : '') + '"></div>';
  h += '<div><label>Vehicle Type</label><input type="text" name="vehicle_type" value="' + esc(v ? v.vehicle_type : '') + '"></div>';
  h += '</div>';
  h += '<div class="form-row">';
  h += '<div><label>Route / Assignment</label><input type="text" name="route" value="' + esc(v ? v.route : '') + '"></div>';
  h += '<div><label>Status / Location</label><input type="text" name="status" value="' + esc(v ? (v.status || 'Active') : 'Active') + '"></div>';
  h += '</div>';
  h += '<div class="form-row">';
  h += '<div><label>Cleaning Frequency (days)</label><input type="number" name="cleaning_frequency" value="' + esc(v ? v.cleaning_frequency : 7) + '"></div>';
  h += '<div><label>Active</label><input type="checkbox" name="active"' + (!v || v.active !== false ? ' checked' : '') + ' style="width:22px;height:22px;margin-top:8px"></div>';
  h += '</div>';
  h += '<label>Notes</label><textarea name="notes" rows="3">' + esc(v ? v.notes : '') + '</textarea>';
  h += '<div class="flex" style="margin-top:16px">';
  h += '<button type="submit" class="btn success">Save</button>';
  h += '<a class="btn secondary" href="#/vehicles' + (v ? '/' + v.id : '') + '">Cancel</a>';
  h += '</div></form></div>';
  app.innerHTML = h;
}

function saveVehicle(form) {
  var id = form.querySelector('[name=id]');
  var unit = form.querySelector('[name=unit_number]').value.trim();
  if (!unit) return;
  var vehicleType = form.querySelector('[name=vehicle_type]').value.trim();
  var route = form.querySelector('[name=route]').value.trim();
  var status = form.querySelector('[name=status]').value.trim() || 'Active';
  var frequency = parseInt(form.querySelector('[name=cleaning_frequency]').value, 10) || 7;
  var notes = form.querySelector('[name=notes]').value.trim();
  var active = form.querySelector('[name=active]').checked;

  if (id) {
    var v = getVehicle(id.value);
    if (v) {
      v.unit_number = unit;
      v.vehicle_type = vehicleType || null;
      v.route = route || null;
      v.status = status;
      v.cleaning_frequency = frequency;
      v.notes = notes;
      v.active = active;
    }
    save();
    location.hash = '#/vehicles/' + v.id;
    return;
  }

  var existing = state.vehicles.find(function (x) {
    return normalizeUnit(x.unit_number) === normalizeUnit(unit);
  });
  if (existing) {
    existing.active = true;
    existing.vehicle_type = vehicleType || existing.vehicle_type;
    if (route) existing.route = route;
    existing.status = status;
    existing.notes = notes;
    var veh = existing;
    ensureOnBoard(veh);
    save();
    render();
    location.hash = '#/';
    return;
  }

  var newV = {
    id: nextId(state.vehicles),
    unit_number: unit,
    vehicle_type: vehicleType || null,
    route: route || null,
    status: status,
    last_washed: null,
    last_detailed: null,
    cleaning_frequency: frequency,
    notes: notes,
    active: active
  };
  state.vehicles.push(newV);
  ensureOnBoard(newV);
  save();
  render();
  location.hash = '#/';
}

function ensureOnBoard(vehicle) {
  if (state.schedule.entries.some(function (e) { return e.vehicle_id === vehicle.id; })) return;
  state.schedule.entries.push(newEntry(nextId(state.schedule.entries), vehicle.id,
    state.schedule.entries.length, state.settings.checklist));
  state.schedule.finalized = false;
}

function toggleVehicleActive(id) {
  var v = getVehicle(id);
  if (!v) return;
  v.active = v.active === false ? true : false;
  save();
  render();
}

// ---------------------------------------------------------------------------
// End My Day
// ---------------------------------------------------------------------------

function renderEnd(app) {
  var rows = dashboardRows();
  var total = rows.length;
  var completed = rows.filter(function (r) { return r.status === 'completed'; }).length;
  var incomplete = total - completed;
  var doneSum = rows.reduce(function (n, r) { return n + r.prog.done; }, 0);
  var totSum = rows.reduce(function (n, r) { return n + r.prog.total; }, 0);
  var overall = totSum ? Math.round(doneSum / totSum * 100) : 0;
  var replCount = replacementsToday().length;
  var incompleteRows = rows.filter(function (r) { return r.status !== 'completed'; });
  var notes = notesForDate(state.schedule.work_date);
  var finalized = state.schedule.finalized;

  var h = '<h1>End My Day <span class="muted small">' + esc(fmtLongDate(state.schedule.work_date)) + '</span></h1>';

  if (finalized) {
    h += '<div class="card" style="border-color:#16a34a"><h2 style="color:#16a34a">\u2713 Day finalized</h2>';
    h += '<p class="muted">This day has been finalized and saved to history.</p>';
    h += '<div class="flex"><a class="btn" href="#/report">View / Print Report</a>';
    h += '<a class="btn secondary" href="#/history">History</a></div></div>';
  }

  h += '<div class="grid" style="grid-template-columns: repeat(auto-fit, minmax(150px,1fr))">';
  h += stat('Total', total, '');
  h += stat('Completed', completed, 'success');
  h += stat('Incomplete', incomplete, 'danger');
  h += stat('Replacements', replCount, 'warn');
  h += stat('Completion', overall + '%', '');
  h += '</div>';

  h += '<div class="card"><h2>Unfinished Checklist Items</h2>';
  if (incompleteRows.length) {
    h += '<div class="table-wrap"><table><tr><th>Unit</th><th>Route</th><th>Done</th><th>Pending</th></tr>';
    incompleteRows.forEach(function (r) {
      h += '<tr><td><strong>' + esc(r.vehicle.unit_number) + '</strong></td>';
      h += '<td>' + esc(r.vehicle.route || '\u2014') + '</td>';
      h += '<td>' + r.prog.done + '/' + r.prog.total + '</td><td>';
      (r.entry.tasks || []).forEach(function (t) {
        if (!t.completed) h += '<span class="badge danger">' + esc(t.name) + '</span> ';
      });
      h += '</td></tr>';
    });
    h += '</table></div>';
  } else {
    h += '<p style="color:#16a34a">All vehicles completed.</p>';
  }
  h += '</div>';

  h += '<div class="card"><h2>Replacements Today</h2>';
  h += replCount ? '<p class="muted">' + replCount + ' replacement(s) recorded. See History for details.</p>'
                  : '<p class="muted">No replacements today.</p>';
  h += '</div>';

  h += '<div class="card"><h2>Important Notes</h2>';
  if (notes.length) {
    h += '<ul>';
    notes.forEach(function (n) {
      var emp = empName(n.employee_id);
      h += '<li>' + esc(n.text) + ' <span class="muted small">\u2014 ' + esc(emp !== '\u2014' ? emp : 'Staff') + '</span></li>';
    });
    h += '</ul>';
  } else {
    h += '<p class="muted">No notes added.</p>';
  }
  h += '<form data-form="add-note" class="flex" style="margin-top:12px">';
  h += '<input type="text" name="note" placeholder="Add a note for end-of-day\u2026" style="flex:1">';
  h += '<button type="submit" class="btn secondary small">Add</button></form>';
  h += '</div>';

  h += '<div class="card no-print" style="border-color:#dc2626">';
  h += '<h2>Finalize Day</h2>';
  h += '<p class="muted">Finalizing locks in today\u2019s work, calculates the completion totals shown above, and saves the completed day to history.</p>';
  if (!finalized) {
    h += '<button class="btn danger big" data-action="open-finalize">END MY DAY</button>';
  }
  h += '</div>';

  app.innerHTML = h;
}

function openFinalizeModal() {
  var rows = dashboardRows();
  var total = rows.length;
  var completed = rows.filter(function (r) { return r.status === 'completed'; }).length;
  var incomplete = total - completed;
  var doneSum = rows.reduce(function (n, r) { return n + r.prog.done; }, 0);
  var totSum = rows.reduce(function (n, r) { return n + r.prog.total; }, 0);
  var overall = totSum ? Math.round(doneSum / totSum * 100) : 0;

  var h = '<h3>Confirm Finalize</h3>';
  h += '<p>Are you sure you want to finalize today?<br>';
  h += '<strong>' + total + '</strong> vehicles &middot; <strong>' + completed + '</strong> completed &middot; ';
  h += '<strong>' + incomplete + '</strong> incomplete &middot; <strong>' + overall + '%</strong>.</p>';
  h += '<form data-form="finalize"><div class="flex">';
  h += '<button type="submit" class="btn danger">Yes, Finalize Day</button>';
  h += '<button type="button" class="btn secondary" data-action="close-modal">Cancel</button>';
  h += '</div></form>';
  openModal(h);
}

// ---------------------------------------------------------------------------
// Report
// ---------------------------------------------------------------------------

function renderReport(app, dateStr) {
  var d = dateStr || todayIso();
  var day = state.days.find(function (x) { return x.work_date === d; });
  var isLive = d === state.schedule.work_date;
  var rows, summary, notes, replInfo, finalized;

  if (isLive) {
    rows = dashboardRows();
    var completed = rows.filter(function (r) { return r.status === 'completed'; }).length;
    var doneSum = rows.reduce(function (n, r) { return n + r.prog.done; }, 0);
    var totSum = rows.reduce(function (n, r) { return n + r.prog.total; }, 0);
    summary = {
      total: rows.length,
      completed: completed,
      incomplete: rows.length - completed,
      overall: totSum ? Math.round(doneSum / totSum * 100) : 0
    };
    notes = notesForDate(d);
    replInfo = replacementsToday().length;
    finalized = state.schedule.finalized;
  } else if (day) {
    summary = day.summary || {};
    notes = day.notes || [];
    replInfo = day.replacements_count || 0;
    finalized = true;
    rows = rowsFromEntries(day.entries);
  } else {
    app.innerHTML = '<h1>Report</h1><div class="card"><p class="muted">No data for ' + esc(d) + '.</p><a class="btn" href="#/end">Back to End My Day</a></div>';
    return;
  }

  var h = '<div class="no-print flex between" style="margin-bottom:16px">';
  h += '<a class="btn secondary" href="#/end">\u2190 Back</a>';
  h += '<button class="btn" data-action="print">Print / Save as PDF</button>';
  h += '</div>';

  h += '<div class="card" id="report">';
  h += '<div class="print-header">';
  h += '<h1 style="border:none">Detailing Operations Dashboard \u2014 Daily Summary</h1>';
  h += '<h2 style="border:none">' + esc(fmtLongDate(d)) + '</h2>';
  h += '</div>';

  h += '<div class="flex" style="gap:20px;margin-bottom:16px">';
  h += '<div><strong>Total:</strong> ' + (summary.total || 0) + '</div>';
  h += '<div><strong>Completed:</strong> ' + (summary.completed || 0) + '</div>';
  h += '<div><strong>Incomplete:</strong> ' + (summary.incomplete != null ? summary.incomplete : ((summary.total || 0) - (summary.completed || 0))) + '</div>';
  h += '<div><strong>Completion:</strong> ' + (summary.overall != null ? summary.overall : 0) + '%</div>';
  h += '<div><strong>Replacements:</strong> ' + replInfo + '</div>';
  h += '</div>';

  h += '<h2>Daily Work List</h2>';
  if (rows.length) {
    h += '<table><tr><th>Unit</th><th>Type</th><th>Route</th><th>Status</th><th>Checklist</th></tr>';
    rows.forEach(function (r) {
      h += '<tr>';
      h += '<td><strong>' + esc(r.vehicle.unit_number) + '</strong>' + (r.entry.is_replacement ? ' *' : '') + '</td>';
      h += '<td>' + esc(r.vehicle.vehicle_type || '\u2014') + '</td>';
      h += '<td>' + esc(r.vehicle.route || '\u2014') + '</td>';
      h += '<td>' + statusLabel(r.status) + '</td><td>';
      (r.entry.tasks || []).forEach(function (t) {
        h += (t.completed ? '\u2611' : '\u2610') + ' ' + esc(t.name) + '&nbsp;';
      });
      h += '</td></tr>';
    });
    h += '</table>';
    var anyRep = rows.some(function (r) { return r.entry.is_replacement; });
    if (anyRep) h += '<p style="font-size:12px">* Replacement vehicle</p>';
  } else {
    h += '<p>No vehicles on the board.</p>';
  }

  h += '<h2>Replacements</h2>';
  h += replInfo ? '<p>See History for full substitution records.</p>' : '<p>None.</p>';

  h += '<h2>Important Notes</h2>';
  if (notes.length) {
    h += '<ul>';
    notes.forEach(function (n) { h += '<li>' + esc(n.text) + '</li>'; });
    h += '</ul>';
  } else {
    h += '<p>No notes.</p>';
  }

  h += '<p style="margin-top:40px;font-size:12px">Generated ' + esc(fmtDateTime(nowIso())) + ' \u00b7 Detailing Operations Dashboard</p>';
  h += '</div>';

  app.innerHTML = h;
}

// ---------------------------------------------------------------------------
// History
// ---------------------------------------------------------------------------

function renderHistory(app) {
  var days = state.days.slice().sort(function (a, b) { return b.work_date.localeCompare(a.work_date); });

  var h = '<h1>History</h1>';

  h += '<div class="card"><h2>Previous Days</h2>';
  if (days.length) {
    h += '<div class="table-wrap"><table><tr><th>Date</th><th>Status</th><th>Total</th><th>Completed</th><th>%</th><th>Report</th></tr>';
    days.forEach(function (day) {
      var s = day.summary || {};
      h += '<tr><td>' + fmtDate(day.work_date) + '</td>';
      h += '<td><span class="badge success">Finalized</span></td>';
      h += '<td>' + (s.total != null ? s.total : '\u2014') + '</td>';
      h += '<td>' + (s.completed != null ? s.completed : '\u2014') + '</td>';
      h += '<td>' + (s.overall != null ? s.overall : '\u2014') + '%</td>';
      h += '<td><a href="#/report?date=' + day.work_date + '">View</a></td></tr>';
    });
    h += '</table></div>';
  } else {
    h += '<p class="muted">No saved days yet. Finalize a day from the <a href="#/end">End Day</a> page to save it here.</p>';
  }
  h += '</div>';

  var rels = state.replacements.slice().sort(function (a, b) { return b.replaced_at.localeCompare(a.replaced_at); });
  h += '<div class="card"><h2>Replacements</h2>';
  if (rels.length) {
    h += '<div class="table-wrap"><table><tr><th>Date</th><th>Original</th><th>Replacement</th><th>Reason</th><th>Employee</th><th>Source</th></tr>';
    rels.forEach(function (r) {
      var ov = getVehicle(r.original_vehicle_id);
      var rv = getVehicle(r.replacement_vehicle_id);
      h += '<tr><td>' + fmtDateTime(r.replaced_at) + '</td>';
      h += '<td>' + esc(ov ? ov.unit_number : '\u2014') + '</td>';
      h += '<td>' + esc(rv ? rv.unit_number : '\u2014') + '</td>';
      h += '<td>' + esc(r.reason || '\u2014') + '</td>';
      h += '<td>' + esc(empName(r.employee_id)) + '</td>';
      h += '<td>' + esc(r.source || '\u2014') + '</td></tr>';
    });
    h += '</table></div>';
  } else {
    h += '<p class="muted">No replacements recorded.</p>';
  }
  h += '</div>';

  h += '<div class="card"><h2>Vehicle Cleaning History</h2>';
  h += '<p class="muted">Open any vehicle from the <a href="#/vehicles">Vehicles</a> page to view its full service history.</p>';
  h += '<form data-form="search-vehicle" class="flex">';
  h += '<input type="text" name="unit" placeholder="Search unit number" style="max-width:280px">';
  h += '<button class="btn small" type="submit">Go</button></form>';
  h += '</div>';

  app.innerHTML = h;
}

// ---------------------------------------------------------------------------
// Staff & Settings
// ---------------------------------------------------------------------------

function renderStaff(app) {
  var h = '<h1>Employees</h1>';
  h += '<div class="card"><h2>Add Employee</h2>';
  h += '<form data-form="employee" class="flex">';
  h += '<input type="text" name="name" placeholder="Employee name" required style="flex:1;max-width:320px">';
  h += '<button class="btn" type="submit">Add</button></form></div>';

  h += '<div class="card"><h2>Current Staff</h2>';
  if (state.employees.length) {
    h += '<div class="table-wrap"><table><tr><th>Name</th><th>Active</th></tr>';
    state.employees.forEach(function (e) {
      h += '<tr><td>' + esc(e.name) + '</td><td>' + (e.active !== false ? 'Yes' : 'No') + '</td></tr>';
    });
    h += '</table></div>';
  } else {
    h += '<p class="muted">No employees yet.</p>';
  }
  h += '</div>';
  app.innerHTML = h;
}

function addEmployee(form) {
  var name = form.querySelector('[name=name]').value.trim();
  if (!name) return;
  state.employees.push({ id: nextId(state.employees), name: name, active: true });
  save();
  render();
}

function renderSettings(app) {
  var s = state.settings;
  var h = '<h1>Settings</h1>';
  h += '<div class="card"><form data-form="settings">';
  h += '<div class="form-row">';
  h += '<div><label>Recently Washed threshold (days)</label><input type="number" name="recent_days" value="' + esc(s.recent_days) + '"></div>';
  h += '<div><label>Due Soon threshold (days)</label><input type="number" name="due_soon_days" value="' + esc(s.due_soon_days) + '"></div>';
  h += '</div>';
  h += '<label>Default Location</label><input type="text" name="location" value="' + esc(s.location) + '">';
  h += '<label>Checklist tasks (comma-separated)</label><input type="text" name="checklist" value="' + esc(s.checklist.join(', ')) + '">';
  h += '<div style="margin-top:16px" class="flex"><button type="submit" class="btn success">Save Settings</button></div>';
  h += '</form></div>';

  h += '<div class="card"><h2>Status Legend</h2>';
  h += '<p><span class="status-recent">\u25cf</span> Recently Washed \u2014 within ' + esc(s.recent_days) + ' days</p>';
  h += '<p><span class="status-due">\u25cf</span> Due Soon \u2014 past ' + esc(s.recent_days) + ' days, within ' + esc(s.due_soon_days) + ' days</p>';
  h += '<p><span class="status-overdue">\u25cf</span> Overdue \u2014 more than ' + esc(s.due_soon_days) + ' days</p>';
  h += '</div>';

  h += '<div class="card no-print"><h2>Data</h2>';
  h += '<p class="muted">All data is stored locally in this browser (localStorage). '
     + 'Use Export to download a backup, or Reset to clear it and reload demo data.</p>';
  h += '<div class="flex">';
  h += '<button class="btn small secondary" data-action="export-data">Export Data</button>';
  h += '<button class="btn small danger" data-action="reset-data">Reset Demo Data</button>';
  h += '</div></div>';

  app.innerHTML = h;
}

function saveSettings(form) {
  state.settings.recent_days = parseInt(form.querySelector('[name=recent_days]').value, 10) || 2;
  state.settings.due_soon_days = parseInt(form.querySelector('[name=due_soon_days]').value, 10) || 7;
  state.settings.location = form.querySelector('[name=location]').value.trim() || 'Main Depot';
  var checklist = form.querySelector('[name=checklist]').value
    .split(',').map(function (x) { return x.trim(); }).filter(Boolean);
  if (checklist.length) state.settings.checklist = checklist;
  save();
  render();
}

// ---------------------------------------------------------------------------
// Modal
// ---------------------------------------------------------------------------

function openModal(html) {
  document.getElementById('modal-body').innerHTML = html;
  document.getElementById('modal').classList.add('open');
}

function closeModal() {
  document.getElementById('modal').classList.remove('open');
  document.getElementById('modal-body').innerHTML = '';
}

function clearFilters() {
  filters = { unit: '', type: '', route: '', status: '' };
  render();
}

// ---------------------------------------------------------------------------
// Event handling (delegated)
// ---------------------------------------------------------------------------

document.getElementById('app').addEventListener('click', function (e) {
  var el = e.target.closest('[data-action]');
  if (!el) return;
  var action = el.getAttribute('data-action');

  if (action === 'open-replace') { openReplaceModal(el.getAttribute('data-entry')); }
  else if (action === 'open-finalize') { openFinalizeModal(); }
  else if (action === 'print') { window.print(); }
  else if (action === 'toggle-active') { toggleVehicleActive(el.getAttribute('data-vehicle')); }
  else if (action === 'export-data') { exportData(); }
  else if (action === 'reset-data') { resetData(); }
});

document.getElementById('app').addEventListener('change', function (e) {
  var el = e.target;

  if (el.id === 'current-employee') {
    state.current_employee_id = el.value ? Number(el.value) : null;
    save();
    return;
  }

  var action = el.getAttribute('data-action');
  if (action === 'toggle-task') {
    toggleTask(el.getAttribute('data-entry'), el.getAttribute('data-task'), el.checked);
    return;
  }

  if (el.name === 'unit' || el.name === 'route' || el.name === 'type' || el.name === 'status') {
    filters[el.name] = el.value;
    render();
  }
});

document.getElementById('app').addEventListener('submit', function (e) {
  var form = e.target.closest('form[data-form]');
  if (!form) return;
  e.preventDefault();
  var kind = form.getAttribute('data-form');

  if (kind === 'add-note') { addNote(form); }
  else if (kind === 'vehicle') { saveVehicle(form); }
  else if (kind === 'employee') { addEmployee(form); }
  else if (kind === 'settings') { saveSettings(form); }
  else if (kind === 'search-vehicle') {
    filters.unit = form.querySelector('[name=unit]').value;
    location.hash = '#/vehicles';
  }
});

var modalEl = document.getElementById('modal');
modalEl.addEventListener('click', function (e) {
  if (e.target === modalEl) { closeModal(); return; }
  var btn = e.target.closest('[data-action]');
  if (btn && btn.getAttribute('data-action') === 'close-modal') closeModal();
});
modalEl.addEventListener('submit', function (e) {
  var form = e.target.closest('form[data-form]');
  if (!form) return;
  e.preventDefault();
  var kind = form.getAttribute('data-form');

  if (kind === 'replace') {
    var entryId = form.getAttribute('data-entry');
    var unit = form.querySelector('[name=replacement_unit]').value;
    var reason = form.querySelector('[name=reason]').value;
    var empEl = form.querySelector('[name=employee_id]');
    var emp = empEl ? empEl.value : '';
    doReplace(entryId, unit, reason, emp ? Number(emp) : null);
  } else if (kind === 'finalize') {
    closeModal();
    finalizeDay();
  }
});

// ---------------------------------------------------------------------------
// Data export / reset
// ---------------------------------------------------------------------------

function exportData() {
  var blob = new Blob([JSON.stringify(state, null, 2)], { type: 'application/json' });
  var url = URL.createObjectURL(blob);
  var a = document.createElement('a');
  a.href = url;
  a.download = 'detailing-ops-backup-' + todayIso() + '.json';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function resetData() {
  state = seedDemo();
  save();
  render();
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

window.addEventListener('hashchange', render);

(function init() {
  var empSel = document.getElementById('current-employee');
  if (empSel) {
    var e = getEmployee(state.current_employee_id);
    if (e) empSel.value = e.id;
  }
  if (!location.hash) location.hash = '#/';
  render();
})();