/* ServerPinger - vanilla JS. No build step, no dependencies.
 *
 * All timestamps are stored and rendered as UTC ISO-8601; everything the user
 * sees is converted here, in the browser's own timezone.
 */
var ServerPinger = (function () {
  "use strict";

  function parseUtc(text) {
    if (!text) { return null; }
    var value = new Date(text);
    return isNaN(value.getTime()) ? null : value;
  }

  function formatLocal(date) {
    return date.toLocaleString(undefined, {
      year: "numeric", month: "short", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit"
    });
  }

  function formatRelative(date) {
    var seconds = Math.round((Date.now() - date.getTime()) / 1000);
    if (seconds < 0) { return "just now"; }
    if (seconds < 60) { return seconds + "s ago"; }
    if (seconds < 3600) { return Math.floor(seconds / 60) + "m ago"; }
    if (seconds < 86400) { return Math.floor(seconds / 3600) + "h ago"; }
    return Math.floor(seconds / 86400) + "d ago";
  }

  function localizeTimestamps(root) {
    var nodes = (root || document).querySelectorAll(".ts[data-utc]");
    for (var i = 0; i < nodes.length; i++) {
      var node = nodes[i];
      var date = parseUtc(node.getAttribute("data-utc"));
      if (!date) {
        node.textContent = node.getAttribute("data-utc") ? node.textContent : "never";
        continue;
      }
      if (node.classList.contains("rel")) {
        node.textContent = formatRelative(date);
        node.title = formatLocal(date);
      } else {
        node.textContent = formatLocal(date);
        node.title = node.getAttribute("data-utc") + " (UTC)";
      }
    }
  }

  function setText(row, selector, text) {
    var cell = row.querySelector(selector);
    if (cell) { cell.textContent = text; }
  }

  function applyTarget(target) {
    var row = document.querySelector('tr[data-target-id="' + target.id + '"]');
    if (!row) { return; }

    var statusCell = row.querySelector(".cell-status");
    if (statusCell) {
      statusCell.innerHTML = "";
      var pill = document.createElement("span");
      pill.className = "pill pill-" + target.status;
      pill.textContent = target.status.toUpperCase();
      statusCell.appendChild(pill);
    }

    setText(row, ".cell-latency",
      target.last_latency_ms === null || target.last_latency_ms === undefined
        ? "—" : target.last_latency_ms.toFixed(1) + " ms");
    setText(row, ".cell-uptime",
      target.uptime_24h === null || target.uptime_24h === undefined
        ? "—" : target.uptime_24h.toFixed(2) + "%");

    var checked = row.querySelector(".cell-checked .ts");
    if (checked) {
      checked.setAttribute("data-utc", target.last_checked_at || "");
      var date = parseUtc(target.last_checked_at);
      checked.textContent = date ? formatRelative(date) : "never";
      if (date) { checked.title = formatLocal(date); }
    }

    var errorRow = document.querySelector('tr[data-error-for="' + target.id + '"]');
    if (errorRow) {
      if (target.status === "down" && target.last_error) {
        errorRow.style.display = "";
        setText(errorRow, ".error-text", target.last_error);
      } else {
        errorRow.style.display = "none";
      }
    }
  }

  function applyStatus(data) {
    var counts = data.counts || {};
    ["up", "down", "unknown"].forEach(function (key) {
      var node = document.getElementById("count-" + key);
      if (node) { node.textContent = counts[key] || 0; }
    });
    (data.groups || []).forEach(function (group) {
      (group.targets || []).forEach(applyTarget);
    });
  }

  function refreshStatus() {
    return fetch("/api/status", { headers: { "Accept": "application/json" } })
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (data) { if (data) { applyStatus(data); } })
      .catch(function () { /* a failed poll is not worth shouting about */ });
  }

  function bindCheckNow(afterCheck) {
    document.addEventListener("click", function (event) {
      var button = event.target.closest ? event.target.closest(".check-now") : null;
      if (!button) { return; }
      event.preventDefault();
      var id = button.getAttribute("data-id");
      var original = button.textContent;
      button.disabled = true;
      button.textContent = "Checking…";
      fetch("/api/targets/" + id + "/check", { method: "POST" })
        .then(function (response) { return response.json(); })
        .then(function (data) {
          if (data && data.target) { applyTarget(data.target); }
          if (afterCheck) { afterCheck(data); }
        })
        .catch(function () { /* ignore */ })
        .then(function () {
          button.disabled = false;
          button.textContent = original;
          localizeTimestamps();
        });
    });
  }

  function startDashboardPoll(intervalMs) {
    bindCheckNow(null);
    window.setInterval(function () {
      refreshStatus().then(function () { localizeTimestamps(); });
    }, intervalMs || 15000);
    window.setInterval(function () { localizeTimestamps(); }, 30000);
  }

  function syncScopeRow(scopeSelect) {
    var container = scopeSelect.closest("td") || scopeSelect.closest("form") || document;
    var group = container.querySelector(".scope-group");
    var target = container.querySelector(".scope-target");
    if (group) { group.hidden = scopeSelect.value !== "group"; }
    if (target) { target.hidden = scopeSelect.value !== "target"; }
  }

  function bindScopeSelects() {
    var selects = document.querySelectorAll(".scope-select");
    for (var i = 0; i < selects.length; i++) {
      (function (select) {
        syncScopeRow(select);
        select.addEventListener("change", function () { syncScopeRow(select); });
      })(selects[i]);
    }
  }

  /* Changing the SMTP mode prefills host/port/security, but every field stays
     editable afterwards. */
  var MODE_DEFAULTS = {
    internal_relay: { port: "25", security: "none" },
    smtp_auth: { port: "587", security: "starttls" }
  };

  function bindEmailSettings() {
    var modeSelect = document.getElementById("smtp-mode");
    if (!modeSelect) { return; }
    var form = document.getElementById("email-form");
    var portInput = form.querySelector('input[name="smtp_port"]');
    var securitySelect = document.getElementById("smtp-security");

    function syncVisibility() {
      var mode = modeSelect.value;
      var helps = form.querySelectorAll(".mode-help");
      for (var i = 0; i < helps.length; i++) {
        helps[i].hidden = helps[i].getAttribute("data-mode") !== mode;
      }
      var authRows = form.querySelectorAll(".auth-only");
      for (var j = 0; j < authRows.length; j++) {
        authRows[j].hidden = mode !== "smtp_auth";
      }
    }

    modeSelect.addEventListener("change", function () {
      var defaults = MODE_DEFAULTS[modeSelect.value];
      if (defaults) {
        if (portInput) { portInput.value = defaults.port; }
        if (securitySelect) { securitySelect.value = defaults.security; }
      }
      syncVisibility();
    });

    syncVisibility();
  }

  document.addEventListener("DOMContentLoaded", function () { localizeTimestamps(); });

  return {
    localizeTimestamps: localizeTimestamps,
    refreshStatus: refreshStatus,
    startDashboardPoll: startDashboardPoll,
    bindCheckNow: bindCheckNow,
    bindScopeSelects: bindScopeSelects,
    bindEmailSettings: bindEmailSettings
  };
})();
