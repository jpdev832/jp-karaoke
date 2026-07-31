/**
 * Auto-Tune Control Panel — REST-only live updates (no Socket.IO).
 */
(function () {
  "use strict";

  function boot() {
    const form = document.getElementById("autotune-form");
    if (!form) return;

    const els = {
      enabled: document.getElementById("at-enabled"),
      key: document.getElementById("at-key"),
      scale: document.getElementById("at-scale"),
      speed: document.getElementById("at-speed"),
      mix: document.getElementById("at-mix"),
      speedVal: document.getElementById("at-speed-val"),
      mixVal: document.getElementById("at-mix-val"),
      error: document.getElementById("autotune-error"),
      connDot: document.getElementById("autotune-conn-dot"),
      connLabel: document.getElementById("autotune-conn-label"),
    };

    let applyingRemote = false;
    let debounceTimer = null;
    let syncTimer = null;
    const apiBase = (window.pikaraokeConfig && window.pikaraokeConfig.basePath) || "";
    const configUrl = (apiBase || "") + "/api/autotune/config";

    function setConnection(state, label) {
      if (els.connDot) els.connDot.dataset.state = state;
      if (els.connLabel) els.connLabel.textContent = label;
    }

    function showError(message) {
      if (!els.error) return;
      if (!message) {
        els.error.hidden = true;
        els.error.textContent = "";
        return;
      }
      els.error.hidden = false;
      els.error.textContent = message;
    }

    function readForm() {
      return {
        enabled: !!(els.enabled && els.enabled.checked),
        key: els.key ? els.key.value : "C",
        scale: els.scale ? els.scale.value : "major",
        correction_speed: Number(els.speed ? els.speed.value : 0.35),
        wet_dry_mix: Number(els.mix ? els.mix.value : 1),
      };
    }

    function applyState(params) {
      if (!params) return;
      applyingRemote = true;
      try {
        if (els.enabled && typeof params.enabled === "boolean") {
          els.enabled.checked = params.enabled;
        }
        if (els.key && params.key) els.key.value = params.key;
        if (els.scale && params.scale) els.scale.value = params.scale;
        if (els.speed && typeof params.correction_speed === "number") {
          els.speed.value = String(params.correction_speed);
          if (els.speedVal) els.speedVal.textContent = Number(params.correction_speed).toFixed(2);
        }
        if (els.mix && typeof params.wet_dry_mix === "number") {
          els.mix.value = String(params.wet_dry_mix);
          if (els.mixVal) els.mixVal.textContent = Number(params.wet_dry_mix).toFixed(2);
        }
      } finally {
        applyingRemote = false;
      }
    }

    function publishLocal(params) {
      setConnection("online", "Saving…");
      fetch(configUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(params),
      })
        .then(function (res) {
          return res.json().then(function (body) {
            if (!res.ok || !body.ok) throw new Error((body && body.error) || res.statusText);
            return body;
          });
        })
        .then(function (body) {
          showError("");
          applyState(body.params);
          setConnection("online", "Ready");
        })
        .catch(function (err) {
          setConnection("offline", "Error");
          showError(err.message || "Failed to update Auto-Tune");
        });
    }

    function schedulePublish() {
      if (applyingRemote) return;
      if (els.speedVal && els.speed) {
        els.speedVal.textContent = Number(els.speed.value).toFixed(2);
      }
      if (els.mixVal && els.mix) {
        els.mixVal.textContent = Number(els.mix.value).toFixed(2);
      }
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(function () {
        publishLocal(readForm());
      }, 120);
    }

    function refreshFromServer() {
      fetch(configUrl, { headers: { Accept: "application/json" } })
        .then(function (res) {
          return res.json();
        })
        .then(function (body) {
          if (body && body.ok && body.params) {
            applyState(body.params);
            setConnection("online", "Ready");
            showError("");
          }
        })
        .catch(function () {
          setConnection("offline", "Unavailable");
        });
    }

    form.addEventListener("input", schedulePublish);
    form.addEventListener("change", schedulePublish);

    if (window.AUTOTUNE_BOOT) applyState(window.AUTOTUNE_BOOT);
    setConnection("online", "Ready");
    refreshFromServer();

    // Light polling so multiple phones stay roughly in sync without sockets.
    syncTimer = setInterval(refreshFromServer, 4000);
    window.addEventListener("beforeunload", function () {
      if (syncTimer) clearInterval(syncTimer);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
