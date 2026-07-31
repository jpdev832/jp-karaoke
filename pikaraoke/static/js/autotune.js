/**
 * Auto-Tune Control Panel — syncs via REST + Socket.IO across all clients,
 * and the Flask backend forwards changes to autotune_engine.py over ZeroMQ.
 */
(function () {
  "use strict";

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
  const apiBase = (window.pikaraokeConfig && window.pikaraokeConfig.basePath) || "";

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
      enabled: !!els.enabled.checked,
      key: els.key.value,
      scale: els.scale.value,
      correction_speed: Number(els.speed.value),
      wet_dry_mix: Number(els.mix.value),
    };
  }

  function applyState(params) {
    if (!params) return;
    applyingRemote = true;
    try {
      if (typeof params.enabled === "boolean") els.enabled.checked = params.enabled;
      if (params.key) els.key.value = params.key;
      if (params.scale) els.scale.value = params.scale;
      if (typeof params.correction_speed === "number") {
        els.speed.value = String(params.correction_speed);
        els.speedVal.textContent = Number(params.correction_speed).toFixed(2);
      }
      if (typeof params.wet_dry_mix === "number") {
        els.mix.value = String(params.wet_dry_mix);
        els.mixVal.textContent = Number(params.wet_dry_mix).toFixed(2);
      }
    } finally {
      applyingRemote = false;
    }
  }

  function publishLocal(params) {
    if (window.autotuneSocket && window.autotuneSocket.connected) {
      window.autotuneSocket.emit("autotune_set", { params: params });
      return;
    }
    // REST fallback if socket is briefly unavailable
    fetch((apiBase || "") + "/api/autotune/config", {
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
      })
      .catch(function (err) {
        showError(err.message || "Failed to update Auto-Tune");
      });
  }

  function schedulePublish() {
    if (applyingRemote) return;
    els.speedVal.textContent = Number(els.speed.value).toFixed(2);
    els.mixVal.textContent = Number(els.mix.value).toFixed(2);
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(function () {
      publishLocal(readForm());
    }, 40);
  }

  form.addEventListener("input", schedulePublish);
  form.addEventListener("change", schedulePublish);

  // Seed from server-rendered boot state
  if (window.AUTOTUNE_BOOT) applyState(window.AUTOTUNE_BOOT);

  if (typeof io === "undefined") {
    setConnection("offline", "Socket.IO unavailable");
    return;
  }

  const socketPath =
    (window.pikaraokeConfig && window.pikaraokeConfig.socketioPath) || "/socket.io";
  const socket = io({ path: socketPath, transports: ["websocket", "polling"] });
  window.autotuneSocket = socket;

  socket.on("connect", function () {
    setConnection("online", "Live");
    showError("");
    socket.emit("autotune_get");
  });

  socket.on("disconnect", function () {
    setConnection("offline", "Reconnecting…");
  });

  socket.on("connect_error", function () {
    setConnection("offline", "Offline");
  });

  socket.on("autotune_update", function (params) {
    showError("");
    applyState(params);
  });

  socket.on("autotune_error", function (payload) {
    showError((payload && payload.error) || "Auto-Tune update rejected");
  });
})();
