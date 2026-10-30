'use strict';

document.addEventListener('DOMContentLoaded', function () {
  var root = document.querySelector('[data-tutorial]');
  if (!root) return;

  var steps = Array.prototype.slice.call(
    root.querySelectorAll('[data-tutorial-step]'));
  var total = steps.length;
  var role = root.getAttribute('data-role') || 'employee';
  var key = 'tutorial-progress-' + role;
  var progressLabel = document.getElementById('tutorial-progress-label');
  var stepCount = document.getElementById('tutorial-step-count');
  var progressFill = document.getElementById('tutorial-progress-fill');
  var complete = document.getElementById('tutorial-complete');

  function readProgress() {
    try {
      return parseInt(window.localStorage.getItem(key), 10) || 0;
    } catch (e) {
      return 0;
    }
  }

  function saveProgress(value) {
    try {
      window.localStorage.setItem(key, String(value));
    } catch (e) {}
  }

  var current = readProgress();
  if (current < 0 || current > total) current = 0;

  function render() {
    var finished = current >= total;
    steps.forEach(function (step, index) {
      step.hidden = finished || index !== current;
    });
    if (complete) complete.hidden = !finished;

    var label = finished ? 'Tutorial complete'
      : 'Step ' + (current + 1) + ' of ' + total;
    if (progressLabel) progressLabel.textContent = label;
    if (stepCount) stepCount.textContent = label;
    if (progressFill) {
      progressFill.style.width = (finished ? 100
        : Math.round((current / total) * 100)) + '%';
    }
  }

  function advance() {
    if (current >= total) return;
    current += 1;
    saveProgress(current);
    render();
  }

  function restart() {
    current = 0;
    saveProgress(0);
    render();
  }

  root.querySelectorAll('[data-tutorial-next]').forEach(function (btn) {
    btn.addEventListener('click', advance);
  });

  var restartButton = document.getElementById('tutorial-restart');
  var restartButton2 = document.getElementById('tutorial-restart-2');
  if (restartButton) restartButton.addEventListener('click', restart);
  if (restartButton2) restartButton2.addEventListener('click', restart);

  render();
});
