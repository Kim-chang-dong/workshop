/* =====================================================================
 * Sentinel-X — dashboard de supervision (JS natif)
 * Source de données : broker Mosquitto via MQTT over WebSockets.
 * Aucune donnée simulée : tout ce qui est affiché provient du broker.
 * ===================================================================== */
(function () {
  "use strict";

  const CFG = window.SENTINEL_CONFIG;
  const T = CFG.topics;
  const K = CFG.telemetryKeys;

  const C = {
    blue: "#2260e9", cyan: "#36b6d4", orange: "#e4803e",
    red: "#d94759", yellow: "#facf53", green: "#8fbe54",
    muted: "#93a0c4", grid: "rgba(255,255,255,0.06)"
  };

  const SYS_TOPICS = {
    "$SYS/broker/version": "s-version",
    "$SYS/broker/clients/connected": "s-clients",
    "$SYS/broker/load/messages/received/1min": "s-msgmin",
    "$SYS/broker/load/publish/received/1min": "s-pubmin",
    "$SYS/broker/load/bytes/received/1min": "s-bytesmin",
    "$SYS/broker/messages/received": "s-msgtotal",
    // Les deux noms existent selon la version de Mosquitto (man mosquitto(8))
    "$SYS/broker/mqtt/publish/dropped": "s-dropped",
    "$SYS/broker/publish/messages/dropped": "s-dropped",
    "$SYS/broker/heap/current": "s-heap"
  };

  const $ = (id) => document.getElementById(id);
  let client = null;

  const state = {
    lastTelemetry: 0, msgCount: 0, intervals: [],
    motionCount: 0, prevMotion: null, motionHistory: [],
    prevAlert: null, alertSince: null, nodeOnline: null,
    alertsCount: 0,
    cmd: { buzzer: 0, led: null }
  };

  // ---------------- Utilitaires ----------------
  const pad = (n) => String(n).padStart(2, "0");
  const hhmmss = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  const isNum = (v) => typeof v === "number" && Number.isFinite(v);
  const truthy = (v) => v === true || v === 1 || v === "1";

  function fmtBytes(b) {
    const n = Number(b);
    if (!Number.isFinite(n)) return String(b);
    if (n < 1024) return `${n.toFixed(0)} o`;
    if (n < 1048576) return `${(n / 1024).toFixed(1)} Ko`;
    if (n < 1073741824) return `${(n / 1048576).toFixed(1)} Mo`;
    return `${(n / 1073741824).toFixed(2)} Go`;
  }
  function fmtDuration(s) {
    const n = Number(s);
    if (!Number.isFinite(n)) return String(s);
    const h = Math.floor(n / 3600), m = Math.floor((n % 3600) / 60), sec = Math.floor(n % 60);
    return `${h} h ${pad(m)} min ${pad(sec)} s`;
  }
  function setText(id, txt) { const el = $(id); if (el) el.textContent = txt; }
  function setPill(id, s, text) { const el = $(id); el.dataset.state = s; el.querySelector(".txt").textContent = text; }
  function connMsg(text, isErr) { const el = $("conn-msg"); el.textContent = text; el.classList.toggle("err", !!isErr); }

  // ---------------- Graphiques ----------------
  Chart.defaults.font.family = '"Segoe UI", system-ui, Roboto, Arial, sans-serif';
  Chart.defaults.color = C.muted;
  Chart.defaults.animation = false;

  function gradient(ctx, area, hex) {
    const g = ctx.createLinearGradient(0, area.top, 0, area.bottom);
    g.addColorStop(0, hex + "55");
    g.addColorStop(1, hex + "00");
    return g;
  }
  const fillWith = (hex) => (c) => (c.chart.chartArea ? gradient(c.chart.ctx, c.chart.chartArea, hex) : "transparent");

  function bigOpts(yTitle, y1) {
    const o = {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { position: "top", align: "end", labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true, pointStyle: "circle" } },
        tooltip: { backgroundColor: "#0d1430", borderColor: "rgba(255,255,255,0.15)", borderWidth: 1, padding: 10 }
      },
      scales: {
        x: { ticks: { maxTicksLimit: 8 }, grid: { display: false }, border: { display: false } },
        y: { title: { display: true, text: yTitle }, grid: { color: C.grid }, border: { display: false } }
      }
    };
    if (y1) o.scales.y1 = Object.assign({ position: "right", grid: { drawOnChartArea: false }, border: { display: false } }, y1);
    return o;
  }

  const lineDs = (label, color, axis, extra) => Object.assign({
    label, data: [], yAxisID: axis, borderColor: color, backgroundColor: fillWith(color),
    pointRadius: 0, pointHoverRadius: 4, borderWidth: 2.2, tension: 0.35, fill: true
  }, extra || {});

  const chartEnv = new Chart($("chart-env"), {
    type: "line",
    data: { labels: [], datasets: [
      lineDs("Température (°C)", C.orange, "y"),
      lineDs("Humidité (%)", C.cyan, "y1", { fill: false, borderDash: [5, 4] })
    ] },
    options: bigOpts("°C", { title: { display: true, text: "%" } })
  });

  const chartGas = new Chart($("chart-gas"), {
    type: "line",
    data: { labels: [], datasets: [
      lineDs("Gaz MQ-2 (ADC)", C.yellow, "y"),
      lineDs("Présence PIR", C.red, "y1", { stepped: true, tension: 0, borderWidth: 1.5 })
    ] },
    options: bigOpts("ADC brut", {
      min: 0, max: 1, title: { display: false },
      ticks: { stepSize: 1, callback: (v) => (v === 1 ? "oui" : v === 0 ? "non" : "") }
    })
  });

  function spark(id, color) {
    return new Chart($(id), {
      type: "line",
      data: { labels: [], datasets: [{ data: [], borderColor: color, backgroundColor: fillWith(color), borderWidth: 2, pointRadius: 0, tension: 0.4, fill: true }] },
      options: {
        responsive: true, maintainAspectRatio: false, events: [],
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
        scales: { x: { display: false }, y: { display: false, grace: "10%" } },
        layout: { padding: 0 }
      }
    });
  }
  const spTemp = spark("sp-temp", C.orange);
  const spHum = spark("sp-hum", C.cyan);
  const spGas = spark("sp-gas", C.yellow);
  const SPARK_POINTS = 40;

  function push(chart, label, values, max) {
    chart.data.labels.push(label);
    values.forEach((v, i) => chart.data.datasets[i].data.push(v));
    while (chart.data.labels.length > max) {
      chart.data.labels.shift();
      chart.data.datasets.forEach((ds) => ds.data.shift());
    }
    chart.update("none");
  }

  // Bandeau d'historique du PIR (les 30 derniers messages)
  const MOTION_SLOTS = 30;
  const barsEl = $("motion-bars");
  for (let i = 0; i < MOTION_SLOTS; i++) barsEl.appendChild(document.createElement("i"));
  function renderMotionBars() {
    const bars = barsEl.children, h = state.motionHistory;
    for (let i = 0; i < MOTION_SLOTS; i++) {
      const v = h[h.length - MOTION_SLOTS + i];
      bars[i].classList.toggle("on", v === true);
    }
  }

  // ---------------- Journal d'alertes ----------------
  function addAlert(a) {
    const feed = $("alerts-feed");
    const empty = feed.querySelector(".empty");
    if (empty) empty.remove();

    const ts = a.ts ? new Date(a.ts) : new Date();
    const li = document.createElement("li");
    li.className = "fresh";

    const time = document.createElement("time");
    time.textContent = isNaN(ts.getTime()) ? String(a.ts) : hhmmss(ts);

    const what = document.createElement("div");
    what.className = "what";
    what.textContent = String(a.type ?? "alerte") + " ";
    const src = document.createElement("span");
    src.className = "src";
    src.textContent = "· " + String(a.source ?? "?");
    what.appendChild(src);

    const sevTxt = String(a.severite ?? "info");
    const sev = document.createElement("span");
    sev.className = "sev " + sevTxt.toLowerCase().replace(/[^a-z]/g, "");
    sev.textContent = sevTxt;

    li.append(time, what, sev);
    if (a.details !== undefined) {
      const det = document.createElement("div");
      det.className = "det";
      det.textContent = typeof a.details === "string" ? a.details : JSON.stringify(a.details);
      li.appendChild(det);
    }
    feed.prepend(li);
    while (feed.children.length > 200) feed.lastElementChild.remove();
    state.alertsCount++;
    setText("alerts-count", String(state.alertsCount));
  }

  // ---------------- Télémétrie ----------------
  function onTelemetry(payload) {
    let d;
    try { d = JSON.parse(payload); } catch (e) {
      addAlert({ source: "dashboard", type: "Payload invalide", severite: "info", details: payload.slice(0, 120) });
      return;
    }

    const now = Date.now();
    if (state.lastTelemetry) {
      state.intervals.push((now - state.lastTelemetry) / 1000);
      if (state.intervals.length > 20) state.intervals.shift();
      const avg = state.intervals.reduce((a, b) => a + b, 0) / state.intervals.length;
      setText("n-interval", `${avg.toFixed(1)} s`);
    }
    state.lastTelemetry = now;
    state.msgCount++;
    setText("n-count", String(state.msgCount));
    setText("last-seen", `Mesure ${hhmmss(new Date(now))}`);

    const temp = d[K.temperature], hum = d[K.humidity], gas = d[K.gas];
    const motion = d[K.motion], alert = d[K.alert];

    setText("v-temp", isNum(temp) ? temp.toFixed(1) : "—");
    setText("v-hum", isNum(hum) ? hum.toFixed(0) : "—");
    setText("v-gas", isNum(gas) ? String(Math.round(gas)) : "—");

    // Présence
    if (motion !== undefined) {
      const on = truthy(motion);
      const el = $("v-motion");
      el.textContent = on ? "Détectée" : "RAS";
      el.classList.toggle("is-alert", on);
      el.classList.toggle("is-ok", !on);
      if (on && state.prevMotion === false) {
        state.motionCount++;
        addAlert({ source: "boîtier", type: "Présence PIR", severite: "info" });
      }
      state.prevMotion = on;
      state.motionHistory.push(on);
      if (state.motionHistory.length > MOTION_SLOTS) state.motionHistory.shift();
      renderMotionBars();
      setText("v-motion-count", `${state.motionCount} détection${state.motionCount > 1 ? "s" : ""}`);
    }

    // Alerte boîtier
    if (alert !== undefined) {
      const on = truthy(alert);
      const el = $("v-alert");
      el.textContent = on ? "ACTIVE" : "Aucune";
      el.classList.toggle("is-alert", on);
      el.classList.toggle("is-ok", !on);
      $("tile-alert").classList.toggle("firing", on);
      if (on && state.prevAlert !== true) {
        state.alertSince = new Date(now);
        if (state.prevAlert === false) addAlert({ source: "boîtier", type: "Alerte locale", severite: "haute", details: d });
      }
      if (on && state.alertSince) setText("alert-since", `Active depuis ${hhmmss(state.alertSince)}`);
      else if (!on && state.prevAlert === true) setText("alert-since", `Dernière à ${hhmmss(new Date(now))}`);
      state.prevAlert = on;
    }

    // Champs optionnels
    if (d[K.ip] !== undefined) setText("n-ip", String(d[K.ip]));
    if (d[K.rssi] !== undefined) setText("n-rssi", `${d[K.rssi]} dBm`);
    if (d[K.uptime] !== undefined) setText("n-uptime", fmtDuration(d[K.uptime]));

    const label = hhmmss(new Date(now));
    push(chartEnv, label, [isNum(temp) ? temp : null, isNum(hum) ? hum : null], CFG.maxPoints);
    push(chartGas, label, [isNum(gas) ? gas : null, motion === undefined ? null : truthy(motion) ? 1 : 0], CFG.maxPoints);
    push(spTemp, label, [isNum(temp) ? temp : null], SPARK_POINTS);
    push(spHum, label, [isNum(hum) ? hum : null], SPARK_POINTS);
    push(spGas, label, [isNum(gas) ? gas : null], SPARK_POINTS);

    refreshNodeStatus();
  }

  // ---------------- MCO ----------------
  function setMeter(key, v) {
    if (!isNum(v)) return;
    setText(`h-${key}`, `${v.toFixed(0)} %`);
    const bar = $(`h-${key}-bar`);
    bar.style.width = `${Math.max(0, Math.min(100, v))}%`;
    bar.classList.toggle("mid", v >= 60 && v < 85);
    bar.classList.toggle("high", v >= 85);
  }
  function onHost(payload) {
    let d;
    try { d = JSON.parse(payload); } catch (e) { return; }
    setMeter("cpu", d.cpu);
    setMeter("ram", d.ram);
    setMeter("disk", d.disk);
    if (d.mqtt_log_bytes !== undefined) setText("h-logs", fmtBytes(d.mqtt_log_bytes));
  }
  function onSys(topic, payload) {
    const id = SYS_TOPICS[topic];
    if (!id) return;
    let txt = payload;
    if (id === "s-bytesmin" || id === "s-heap") txt = fmtBytes(payload);
    else if (id === "s-msgmin" || id === "s-pubmin") txt = Number(payload).toFixed(1);
    setText(id, txt);
  }

  function refreshNodeStatus() {
    if (!state.lastTelemetry) return;
    const age = (Date.now() - state.lastTelemetry) / 1000;
    const online = age <= CFG.staleAfterSeconds;
    if (online) setPill("chip-node", "on", "Boîtier en ligne");
    else setPill("chip-node", "off", `Boîtier muet · ${Math.round(age)} s`);
    if (state.nodeOnline !== null && state.nodeOnline !== online) {
      addAlert({
        source: "dashboard", type: online ? "Boîtier rétabli" : "Perte de télémétrie",
        severite: online ? "info" : "haute",
        details: { silence_s: Math.round(age), seuil_s: CFG.staleAfterSeconds }
      });
    }
    state.nodeOnline = online;
  }
  setInterval(refreshNodeStatus, 1000);

  // ---------------- Actionneurs ----------------
  const sw = $("sw-buzzer");
  const ledBtns = document.querySelectorAll(".seg button");

  function renderCmd() {
    sw.setAttribute("aria-checked", String(state.cmd.buzzer === 1));
    ledBtns.forEach((b) => b.setAttribute("aria-checked", String(b.dataset.led === state.cmd.led)));
  }

  function sendCmd() {
    if (!client || !client.connected) return;
    // État complet envoyé à chaque fois : le firmware ne doit pas interpréter
    // une clé absente comme une valeur par défaut.
    const msg = { buzzer: state.cmd.buzzer, led: state.cmd.led ?? "off" };
    client.publish(T.cmd, JSON.stringify(msg), { qos: 1 }, (err) => {
      setText("cmd-last", err
        ? `Échec de publication : ${err.message}`
        : `${hhmmss(new Date())} · ${T.cmd} ← ${JSON.stringify(msg)}`);
    });
    renderCmd();
  }

  sw.addEventListener("click", () => { state.cmd.buzzer = state.cmd.buzzer === 1 ? 0 : 1; sendCmd(); });
  ledBtns.forEach((b) => b.addEventListener("click", () => { state.cmd.led = b.dataset.led; sendCmd(); }));

  function setControls(enabled) {
    sw.disabled = !enabled;
    ledBtns.forEach((b) => (b.disabled = !enabled));
    $("btn-connect").disabled = enabled;
    $("btn-disconnect").disabled = !enabled;
  }

  // ---------------- Connexion MQTT ----------------
  function connect() {
    const url = $("in-url").value.trim();
    if (!/^wss?:\/\//.test(url)) { connMsg("URL invalide : elle doit commencer par ws:// ou wss://.", true); return; }
    if (url.startsWith("ws://")) connMsg("Attention : ws:// n'est pas chiffré. Utiliser wss:// pour la démo.", true);
    if (client) { client.end(true); client = null; }

    connMsg("Connexion en cours…");
    setPill("chip-broker", "warn", "Connexion…");
    client = mqtt.connect(url, {
      username: $("in-user").value || undefined,
      password: $("in-pass").value || undefined,
      clientId: "sentinel-dash-" + Math.random().toString(16).slice(2, 10),
      clean: true, reconnectPeriod: 3000, connectTimeout: 8000, protocolVersion: 4
    });

    client.on("connect", () => {
      setPill("chip-broker", "on", "Broker connecté");
      connMsg(`Connecté à ${url}`);
      setControls(true);
      const subs = [T.telemetry, T.alerts, T.host, T.status].concat(Object.keys(SYS_TOPICS));
      client.subscribe(subs, { qos: 0 }, (err, granted) => {
        if (err) { connMsg(`Abonnement refusé : ${err.message}`, true); return; }
        const refused = (granted || []).filter((g) => g.qos === 128).map((g) => g.topic); // 128 = refusé (ACL)
        if (refused.length) connMsg(`Connecté. Topics refusés par les ACL : ${refused.join(", ")}`, true);
      });
      toggleConn(false);
    });
    client.on("reconnect", () => setPill("chip-broker", "warn", "Reconnexion…"));
    client.on("offline", () => { setPill("chip-broker", "off", "Broker hors ligne"); setControls(false); $("btn-disconnect").disabled = false; });
    client.on("error", (err) => { connMsg(`Erreur MQTT : ${err.message}`, true); toggleConn(true); });

    client.on("message", (topic, buf) => {
      const payload = buf.toString();
      if (topic === T.telemetry) onTelemetry(payload);
      else if (topic === T.alerts) {
        try { addAlert(JSON.parse(payload)); }
        catch (e) { addAlert({ source: "?", type: "Alerte non JSON", severite: "info", details: payload.slice(0, 120) }); }
      }
      else if (topic === T.host) onHost(payload);
      else if (topic === T.status) setText("n-lwt", payload);
      else if (topic.startsWith("$SYS/")) onSys(topic, payload);
    });
  }

  function disconnect() {
    if (client) client.end(true);
    client = null;
    setPill("chip-broker", "off", "Broker déconnecté");
    setControls(false);
    connMsg("Déconnecté.");
  }

  function toggleConn(show) {
    const bar = $("connbar"), btn = $("btn-toggle-conn");
    const visible = show === undefined ? bar.hidden : show;
    bar.hidden = !visible;
    btn.setAttribute("aria-expanded", String(visible));
  }

  $("btn-connect").addEventListener("click", connect);
  $("btn-disconnect").addEventListener("click", disconnect);
  $("btn-toggle-conn").addEventListener("click", () => toggleConn());
  $("in-pass").addEventListener("keydown", (e) => { if (e.key === "Enter") connect(); });

  // ---------------- Initialisation ----------------
  $("in-url").value = CFG.brokerUrl || "";
  $("in-user").value = CFG.username || "";
  renderCmd();

  if (CFG.webcamUrl) {
    const img = $("webcam"), box = $("webcam-box"), tag = $("cam-tag");
    img.addEventListener("load", () => { box.classList.add("live"); tag.textContent = "en direct"; tag.classList.add("on"); });
    img.addEventListener("error", () => {
      box.classList.remove("live"); tag.textContent = "hors ligne"; tag.classList.remove("on");
      setText("webcam-msg", `Flux indisponible : ${CFG.webcamUrl}`);
    });
    setText("webcam-msg", "Connexion au flux…");
    img.src = CFG.webcamUrl;
  }
})();
