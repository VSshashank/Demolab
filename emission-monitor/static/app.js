/* FleetCarbon dashboard.
 *
 * No framework and no build step, so the whole thing is one file of plain DOM
 * work. Two rules it holds to:
 *
 *   1. Nothing is hardcoded. Every metric, threshold and label on screen came
 *      out of an API call. If the backend cannot answer, the UI says so rather
 *      than showing a plausible number.
 *   2. The socket state is always visible. A dashboard that silently stops
 *      updating looks identical to a parked fleet, which is the worst failure
 *      mode this thing has.
 */
"use strict";

const MANGALORE = [12.95, 74.85];
const LIVE_WINDOW_POINTS = 300;     // 5 minutes at 1 Hz
const CO2_AMBER = 3.0;              // g/s, matches the map legend
const CO2_ROSE = 8.0;

const C = {
  good: "#10b981", warn: "#f59e0b", crit: "#f43f5e",
  line: "#1b2637", lineStrong: "#2a3a52",
  text: "#e2e8f0", dim: "#93a3b8", faint: "#5d6b80",
};

/* Categorical series colours. Multi-series charts genuinely need these; the
 * rest of the interface stays on one accent. Tuned for a dark background, and
 * distinguishable without relying on hue alone thanks to the legend order. */
const SERIES = ["#2dd4bf", "#60a5fa", "#a78bfa", "#fbbf24", "#fb7185", "#a3e635", "#f472b6", "#38bdf8"];

const $ = (id) => document.getElementById(id);
const fmt = (v, d = 1) => (v === null || v === undefined || Number.isNaN(v) ? "0" : Number(v).toFixed(d));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const hhmmss = (iso) => (iso ? String(iso).slice(11, 19) : "");

const state = {
  vehicles: new Map(),
  alerts: [],
  routes: [],
  seenAlertIds: new Set(),
  markers: new Map(),
  traces: new Map(),
  selectedTrip: null,
  scenarios: [],
  tilesOk: true,
  charts: {},
  map: null,
  routeLayer: null,
  traceLayer: null,
};

/* ====================================================================== map */
function initMap() {
  const map = L.map("map", { zoomControl: true, attributionControl: true, preferCanvas: true })
    .setView(MANGALORE, 11);
  state.map = map;

  const tiles = L.tileLayer(
    "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
    { maxZoom: 19, subdomains: "abcd", attribution: "Leaflet | CARTO | OpenStreetMap contributors" }
  );

  /* Offline is an expected condition, not an error. One failed tile is enough
   * to know: swap to a graticule so routes and markers stay readable. */
  let failures = 0;
  tiles.on("tileerror", () => {
    if (++failures < 3 || !state.tilesOk) return;
    state.tilesOk = false;
    map.removeLayer(tiles);
    document.getElementById("map").classList.add("tile-fallback");
    const note = L.control({ position: "topright" });
    note.onAdd = () => {
      const d = L.DomUtil.create("div", "map-legend");
      d.innerHTML = "<b>Offline</b>map tiles unavailable, routes and vehicles still live";
      return d;
    };
    note.addTo(map);
  });
  tiles.addTo(map);

  state.routeLayer = L.layerGroup().addTo(map);
  state.traceLayer = L.layerGroup().addTo(map);

  $("btn-fit").addEventListener("click", fitToFleet);
  return map;
}

function drawRoutes(routes) {
  state.routes = routes || [];
  state.routeLayer.clearLayers();
  for (const r of state.routes) {
    L.polyline(r.polyline, { color: "#33507a", weight: 2, opacity: 0.55, interactive: false })
      .addTo(state.routeLayer);
  }
}

function co2Colour(gps) {
  if (gps < CO2_AMBER) return C.good;
  if (gps < CO2_ROSE) return C.warn;
  return C.crit;
}

function upsertMarker(v) {
  const colour = co2Colour(v.co2_gps_pred);
  let m = state.markers.get(v.vehicle_id);
  if (!m) {
    m = L.circleMarker([v.lat, v.lon], {
      radius: 7, weight: 2, color: "#0b1120", fillColor: colour, fillOpacity: 1,
    }).addTo(state.map);
    state.markers.set(v.vehicle_id, m);
  } else {
    m.setLatLng([v.lat, v.lon]);
    m.setStyle({ fillColor: colour });
  }
  m.bindPopup(popupHtml(v), { className: "veh-popup", closeButton: false });

  /* Travelled trace, brighter than the planned route so the two read apart. */
  let trace = state.traces.get(v.vehicle_id);
  if (!trace) {
    trace = L.polyline([], { color: colour, weight: 2.5, opacity: 0.9 }).addTo(state.traceLayer);
    state.traces.set(v.vehicle_id, trace);
  }
  const pts = trace.getLatLngs();
  if (!pts.length || pts[pts.length - 1].lat !== v.lat || pts[pts.length - 1].lng !== v.lon) {
    trace.addLatLng([v.lat, v.lon]);
    if (pts.length > 900) trace.setLatLngs(pts.slice(-900));
  }
  trace.setStyle({ color: colour });
}

function popupHtml(v) {
  const fallback = v.prediction_source === "physics_fallback"
    ? '<div class="pop-sub" style="color:#f59e0b">Model unavailable, showing the road-load estimate</div>' : "";
  return `<div class="pop-id">${esc(v.vehicle_id)}</div>
    <div class="pop-sub">${esc(v.vehicle_type)} on ${esc(v.route_key || "unmatched route")}, driver ${esc(v.driver_id)}</div>
    ${fallback}
    <dl class="pop-grid">
      <dt>Speed</dt><dd>${fmt(v.speed_kmh, 0)} km/h</dd>
      <dt>CO2 rate</dt><dd style="color:${co2Colour(v.co2_gps_pred)}">${fmt(v.co2_gps_pred, 2)} g/s</dd>
      <dt>Cumulative</dt><dd>${fmt(v.co2_cumulative_kg, 2)} kg</dd>
      <dt>Fuel rate</dt><dd>${fmt(v.fuel_rate_lph, 2)} L/h</dd>
      <dt>Payload</dt><dd>${fmt(v.payload_kg, 0)} kg</dd>
      <dt>Trip distance</dt><dd>${fmt(v.trip_distance_km, 2)} km</dd>
      <dt>Intensity</dt><dd>${fmt(v.trip_intensity_g_per_tkm, 0)} g/t.km</dd>
      <dt>Behaviour</dt><dd>${fmt(v.behaviour_score, 0)} / 100</dd>
    </dl>`;
}

function fitToFleet() {
  const pts = [...state.vehicles.values()].map((v) => [v.lat, v.lon]);
  if (pts.length) state.map.fitBounds(L.latLngBounds(pts).pad(0.25));
}

/* =================================================================== charts */
const axisStyle = {
  grid: { color: C.line, drawBorder: false },
  ticks: { color: C.faint, font: { family: "JetBrains Mono, monospace", size: 10 } },
};

function initLiveChart() {
  state.charts.live = new Chart($("chart-live"), {
    type: "line",
    data: { labels: [], datasets: [] },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { mode: "nearest", axis: "x", intersect: false },
      elements: { point: { radius: 0 }, line: { borderWidth: 1.6, tension: 0.25 } },
      scales: {
        x: { ...axisStyle, ticks: { ...axisStyle.ticks, maxTicksLimit: 8, autoSkip: true } },
        y: { ...axisStyle, beginAtZero: true, title: { display: true, text: "g CO2 / s", color: C.faint, font: { size: 10 } } },
      },
      plugins: {
        legend: { labels: { color: C.dim, boxWidth: 9, boxHeight: 9, font: { size: 10.5 }, usePointStyle: true, pointStyle: "line" } },
        tooltip: { backgroundColor: "#0f1729", borderColor: C.lineStrong, borderWidth: 1, titleColor: C.text, bodyColor: C.dim, bodyFont: { family: "JetBrains Mono, monospace", size: 11 } },
      },
    },
  });
}

function pushLivePoint(v) {
  const chart = state.charts.live;
  if (!chart) return;
  const label = hhmmss(v.ts);
  if (chart.data.labels[chart.data.labels.length - 1] !== label) {
    chart.data.labels.push(label);
    if (chart.data.labels.length > LIVE_WINDOW_POINTS) chart.data.labels.shift();
  }
  let ds = chart.data.datasets.find((d) => d.label === v.vehicle_id);
  if (!ds) {
    const colour = SERIES[chart.data.datasets.length % SERIES.length];
    ds = { label: v.vehicle_id, data: [], borderColor: colour, backgroundColor: colour, spanGaps: true };
    chart.data.datasets.push(ds);
  }
  ds.data.push(Number(v.co2_gps_pred.toFixed(3)));
  if (ds.data.length > LIVE_WINDOW_POINTS) ds.data.shift();
  chart.update("none");
}

/* ===================================================================== KPIs */
function renderKpis(fleet) {
  const vehicles = [...state.vehicles.values()];
  $("kpi-vehicles").textContent = vehicles.length;
  const moving = vehicles.filter((v) => v.speed_kmh > 3).length;
  $("kpi-vehicles-note").textContent = vehicles.length
    ? `${moving} moving, ${vehicles.length - moving} stationary` : "awaiting telemetry";

  if (!fleet) return;
  $("kpi-co2").innerHTML = `${fmt(fleet.co2_wtw_kg, 1)}<small>kg</small>`;
  $("kpi-fuel").innerHTML = `${fmt(fleet.fuel_l, 1)}<small>L</small>`;
  $("kpi-fuel-note").textContent = `${fmt(fleet.distance_km, 1)} km covered`;

  const tile = $("kpi-intensity").closest(".kpi");
  if (fleet.tkm > 0.001) {
    $("kpi-intensity").innerHTML = `${fmt(fleet.intensity_g_per_tkm, 0)}<small>g/t.km</small>`;
    tile.removeAttribute("data-tone");
  } else {
    /* Zero tonne-kilometres means the question has no answer yet. Showing 0
     * would claim a perfect score. */
    $("kpi-intensity").innerHTML = `<span style="font-size:15px;color:${C.faint}">no t.km yet</span>`;
  }

  const mins = Math.round((fleet.idle_seconds || 0) / 60);
  $("kpi-idle").innerHTML = `${mins}<small>m</small>`;
  $("kpi-idle-note").textContent = `across ${fleet.trips} trip${fleet.trips === 1 ? "" : "s"}`;
}

function renderAlertCount() {
  const active = state.alerts.filter((a) => !a.acknowledged).length;
  $("kpi-alerts").textContent = active;
  const tile = $("kpi-alerts-tile");
  const high = state.alerts.some((a) => !a.acknowledged && a.severity === "HIGH");
  tile.dataset.tone = active === 0 ? "good" : (high ? "crit" : "warn");
  $("kpi-alerts-note").textContent = active === 0 ? "all clear" : "unacknowledged";
}

/* ============================================================== alert feed */
function alertHtml(a, isNew) {
  return `<div class="alert${isNew ? " is-new" : ""}" data-sev="${esc(a.severity)}" data-ack="${a.acknowledged ? 1 : 0}" data-id="${a.id}">
    <div class="alert-top">
      <span class="alert-type">${esc(a.type.replace(/_/g, " "))}</span>
      <span class="alert-time num">${hhmmss(a.ts)}</span>
    </div>
    <div class="alert-veh">${esc(a.vehicle_id)}</div>
    <div class="alert-msg">${esc(a.message)}</div>
    <div class="alert-rec">${esc(a.recommendation)}</div>
    ${a.acknowledged ? "" : `<div class="alert-actions"><button class="btn" data-ack="${a.id}">Acknowledge</button></div>`}
  </div>`;
}

function renderAlerts(newIds = new Set()) {
  const feed = $("alert-feed");
  if (!state.alerts.length) {
    feed.innerHTML = `<div class="state"><svg class="icon"><use href="vendor/icons.svg#i-seal-check"></use></svg>
      <span class="state-title">No alerts</span>
      <span class="state-hint">Rules are running against every packet. Inject a scenario below to raise one on demand.</span></div>`;
    renderAlertCount();
    return;
  }
  feed.innerHTML = state.alerts.slice(0, 60).map((a) => alertHtml(a, newIds.has(a.id))).join("");
  renderAlertCount();
}

async function ackAlert(id) {
  try {
    await fetch(`/api/alerts/${id}/ack`, { method: "POST" });
    const a = state.alerts.find((x) => x.id === Number(id));
    if (a) a.acknowledged = 1;
    renderAlerts();
  } catch (e) { console.error("ack failed", e); }
}

$("alert-feed").addEventListener("click", (ev) => {
  const btn = ev.target.closest("button[data-ack]");
  if (btn) ackAlert(btn.dataset.ack);
});
$("btn-ack-all").addEventListener("click", async () => {
  const pending = state.alerts.filter((a) => !a.acknowledged).map((a) => a.id);
  await Promise.all(pending.map((id) => fetch(`/api/alerts/${id}/ack`, { method: "POST" })));
  state.alerts.forEach((a) => { a.acknowledged = 1; });
  renderAlerts();
});

/* ================================================================ WebSocket */
let ws = null;
let backoff = 500;

function setConn(stateName, label) {
  $("conn").dataset.state = stateName;
  $("conn-label").textContent = label;
}

function connect() {
  const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/live`;
  setConn("connecting", "Connecting");
  ws = new WebSocket(url);

  ws.onopen = () => {
    backoff = 500;
    setConn("open", "Live");
  };

  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.kind === "snapshot") {
      drawRoutes(msg.routes);
      (msg.vehicles || []).forEach((v) => { state.vehicles.set(v.vehicle_id, v); upsertMarker(v); });
      state.alerts = msg.alerts || [];
      state.alerts.forEach((a) => state.seenAlertIds.add(a.id));
      renderAlerts();
      renderKpis(msg.fleet);
      populateVehicleSelect();
      if (state.vehicles.size) fitToFleet();
      return;
    }
    if (msg.kind !== "telemetry") return;

    const known = state.vehicles.has(msg.vehicle_id);
    state.vehicles.set(msg.vehicle_id, msg);
    upsertMarker(msg);
    pushLivePoint(msg);
    if (!known) populateVehicleSelect();

    if (msg.new_alerts && msg.new_alerts.length) {
      const fresh = new Set();
      for (const a of msg.new_alerts) {
        if (state.seenAlertIds.has(a.id)) continue;
        state.seenAlertIds.add(a.id);
        fresh.add(a.id);
        state.alerts.unshift(a);
      }
      if (fresh.size) renderAlerts(fresh);
    }
    $("hdr-inf").innerHTML = `${fmt(msg.inference_ms, 3)}<small>ms</small>`;
    if (msg.source && msg.source !== "SIMULATOR") {
      $("source-pill-text").textContent = `Data source: ${msg.source}`;
    }
  };

  const retry = () => {
    /* Exponential backoff with a ceiling. The dot goes red first so the state
     * is on screen before the first retry, not after it. */
    setConn("closed", `Reconnecting in ${(backoff / 1000).toFixed(1)}s`);
    setTimeout(connect, backoff);
    backoff = Math.min(backoff * 2, 15000);
  };
  ws.onclose = retry;
  ws.onerror = () => { try { ws.close(); } catch { /* already closing */ } };
}

/* Browsers suspend sockets in background tabs. On return, verify rather than
 * assume, because a stale-but-open socket looks exactly like a quiet fleet. */
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && ws && ws.readyState !== WebSocket.OPEN) connect();
});

/* ================================================================== health */
async function pollHealth() {
  try {
    const r = await fetch("/api/health");
    if (!r.ok) throw new Error(r.status);
    const h = await r.json();
    $("hdr-pps").innerHTML = `${fmt(h.packets_per_second, 1)}<small>pkt/s</small>`;
    const inf = h.inference || {};
    if (inf.inference_ms_mean) $("hdr-inf").innerHTML = `${fmt(inf.inference_ms_mean, 3)}<small>ms</small>`;

    const al = h.alert_latency || {};
    $("hdr-alat").innerHTML = al.mean_ms !== null && al.mean_ms !== undefined
      ? `${fmt(al.mean_ms, 0)}<small>ms mean</small>`
      : `<span style="font-size:11px;color:${C.faint}">no injections yet</span>`;

    if (!inf.model_loaded) {
      $("source-pill-text").textContent = "Data source: simulator (HIL), model not loaded";
      $("source-pill").title = inf.load_error || "Model file failed to load";
    }
    renderKpis(h.fleet);
  } catch { /* the connection dot already reports transport health */ }
}

/* =============================================================== scenarios */
async function loadScenarios() {
  try {
    const r = await fetch("/api/scenarios");
    const { scenarios } = await r.json();
    state.scenarios = scenarios;
    $("scenario-buttons").innerHTML = scenarios.map((s) =>
      `<button class="btn" data-scenario="${esc(s.type)}" title="${esc(s.expect)}">${esc(s.type.replace(/_/g, " "))}</button>`).join("");
  } catch (e) { console.error("scenario catalogue unavailable", e); }
}

function populateVehicleSelect() {
  const sel = $("scenario-vehicle");
  const ids = [...state.vehicles.keys()].sort();
  if (!ids.length) return;
  const current = sel.value;
  /* Speed is in the label because two of the scenarios cannot bite on a parked
     vehicle, and picking one at a loading bay looks like a broken button. */
  sel.innerHTML = ids.map((id) => {
    const v = state.vehicles.get(id);
    const kmh = v ? Math.round(v.speed_kmh) : 0;
    return `<option value="${esc(id)}">${esc(id)}  ${String(kmh).padStart(3)} km/h</option>`;
  }).join("");
  if (ids.includes(current)) sel.value = current;
}

$("scenario-buttons").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("button[data-scenario]");
  if (!btn) return;
  const vehicle_id = $("scenario-vehicle").value;
  const status = $("scenario-status");
  if (!vehicle_id) { status.dataset.tone = "err"; status.textContent = "No vehicle available yet"; return; }

  btn.disabled = true;
  status.dataset.tone = "";
  status.textContent = `Injecting ${btn.dataset.scenario}...`;
  const t0 = performance.now();
  try {
    const r = await fetch("/api/scenario", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ vehicle_id, scenario: btn.dataset.scenario }),
    });
    const body = await r.json();
    if (!r.ok) throw new Error(body.detail || r.statusText);
    status.dataset.tone = body.armed_waiting ? "" : "ok";
    status.textContent = body.armed_waiting
      ? `${btn.dataset.scenario} armed. ${body.note}`
      : `${btn.dataset.scenario} live in ${Math.round(performance.now() - t0)} ms. ${body.expect}`;
  } catch (e) {
    status.dataset.tone = "err";
    status.textContent = `Rejected: ${e.message}`;
  } finally {
    btn.disabled = false;
  }
});

/* ============================================================ models tab */
let modelsLoaded = false;
async function loadModels() {
  if (modelsLoaded) return;
  const wrap = $("metrics-table-wrap");
  try {
    const r = await fetch("/api/metrics");
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail || `metrics unavailable (${r.status})`);
    }
    const m = await r.json();
    modelsLoaded = true;

    const rows = m.models.map((mo) => `<tr data-best="${mo.name === m.best_model}">
      <td>${esc(mo.name)}${mo.note ? ` <span style="color:${C.faint};font-size:11px">${esc(mo.note)}</span>` : ""}</td>
      <td class="n">${fmt(mo.mae, 4)}</td><td class="n">${fmt(mo.rmse, 4)}</td>
      <td class="n">${fmt(mo.mse, 4)}</td><td class="n">${fmt(mo.r2, 4)}</td>
      <td class="n">${fmt(mo.train_seconds, 1)}</td><td class="n">${fmt(mo.inference_ms_per_row, 4)}</td>
    </tr>`).join("");

    wrap.innerHTML = `<table class="data"><thead><tr>
      <th>Model</th><th style="text-align:right">MAE</th><th style="text-align:right">RMSE</th>
      <th style="text-align:right">MSE</th><th style="text-align:right">R2</th>
      <th style="text-align:right">Train s</th><th style="text-align:right">Inference ms/row</th>
    </tr></thead><tbody>${rows}</tbody></table>`;

    const sc = m.split_comparison || {};
    $("split-caption").innerHTML = `<div class="callout">
      <b>${esc(m.split_strategy)}.</b>
      Target is <code>${esc(m.target)}</code> in ${esc(m.target_units || "g/s")}, over
      <span class="num">${m.dataset.rows.toLocaleString()}</span> rows across
      <span class="num">${m.dataset.trips}</span> trips
      (<span class="num">${m.dataset.train_trips}</span> train,
      <span class="num">${m.dataset.val_trips}</span> validation,
      <span class="num">${m.dataset.test_trips}</span> test).
      The same data under a random row split scores
      <span class="num">${fmt(sc.random_row_split_r2, 4)}</span> against the
      <span class="num">${fmt(sc.grouped_r2, 4)}</span> reported here, so a naive split would
      have overstated accuracy by <span class="num">${fmt(sc.inflation, 4)}</span>.
      Road grade, wind and the physics constants are withheld from the feature set.
    </div>`;

    drawImportance(m.feature_importance);
    renderMlNotes(m);
    loadScatter();
  } catch (e) {
    wrap.innerHTML = `<div class="state" data-tone="err">
      <svg class="icon"><use href="vendor/icons.svg#i-warning-octagon"></use></svg>
      <span class="state-title">Metrics not available</span>
      <span class="state-hint">${esc(e.message)}<br>Run <code>python -m ml.train_models</code> to produce ml/metrics.json.</span></div>`;
  }
}

function renderMlNotes(m) {
  const env = m.environment || {};
  const g = m.gate_b || {};
  $("ml-notes").innerHTML = `
    <h2 class="section-title">Methodology notes</h2>
    <div class="callout" style="margin-bottom:12px">
      <b>Gate B, leakage check: ${esc(g.verdict || "not recorded")}.</b>
      Any test R2 above 0.995 is treated as evidence of leakage rather than of a good model,
      and training prints a warning rather than shipping the number quietly.
    </div>
    <div class="callout" style="margin-bottom:12px">
      <b>Withheld from the model:</b> ${(m.withheld_features || []).map(esc).join(", ")}.
      These are the quantities a real dongle cannot observe, so giving them to the model
      would flatter it in a way that would not survive contact with hardware.
    </div>
    <div class="callout">
      Trained ${esc((m.generated_at || "").slice(0, 19).replace("T", " "))} UTC on
      Python ${esc(env.python || "?")}, scikit-learn ${esc(env.sklearn || "?")}, numpy ${esc(env.numpy || "?")}.
      <code>requirements.txt</code> pins these exactly: a joblib unpickle across versions fails loudly.
    </div>`;
}

function drawImportance(items) {
  if (!items || !items.length) return;
  const top = items.slice(0, 14).reverse();
  if (state.charts.importance) state.charts.importance.destroy();
  state.charts.importance = new Chart($("chart-importance"), {
    type: "bar",
    data: {
      labels: top.map((f) => f.feature),
      datasets: [{ data: top.map((f) => f.importance), backgroundColor: C.good, borderRadius: 2, barThickness: 11 }],
    },
    options: {
      indexAxis: "y", responsive: true, maintainAspectRatio: false,
      scales: {
        x: { ...axisStyle, beginAtZero: true },
        y: { grid: { display: false }, ticks: { color: C.dim, font: { family: "JetBrains Mono, monospace", size: 10 } } },
      },
      plugins: { legend: { display: false }, tooltip: { backgroundColor: "#0f1729", borderColor: C.lineStrong, borderWidth: 1 } },
    },
  });
}

async function loadScatter() {
  try {
    const r = await fetch("/api/models/scatter");
    if (!r.ok) return;
    const d = await r.json();
    const pts = d.pairs.map(([a, b]) => ({ x: a, y: b }));
    const max = Math.max(...d.pairs.flat()) * 1.03;
    if (state.charts.scatter) state.charts.scatter.destroy();
    state.charts.scatter = new Chart($("chart-scatter"), {
      type: "scatter",
      data: {
        datasets: [
          { label: `${d.model} predictions`, data: pts, backgroundColor: "rgba(45,212,191,0.32)", pointRadius: 2 },
          { label: "perfect agreement", type: "line", data: [{ x: 0, y: 0 }, { x: max, y: max }], borderColor: C.faint, borderDash: [5, 4], borderWidth: 1, pointRadius: 0 },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        scales: {
          x: { ...axisStyle, title: { display: true, text: "actual fuel rate, g/s", color: C.faint, font: { size: 10 } } },
          y: { ...axisStyle, title: { display: true, text: "predicted, g/s", color: C.faint, font: { size: 10 } } },
        },
        plugins: { legend: { labels: { color: C.dim, boxWidth: 9, font: { size: 10.5 } } } },
      },
    });
  } catch (e) { console.error("scatter unavailable", e); }
}

/* ============================================================== trips tab */
async function loadTrips() {
  const wrap = $("trips-table-wrap");
  try {
    const r = await fetch("/api/trips");
    const { trips } = await r.json();
    $("trips-count").textContent = `${trips.length} operation${trips.length === 1 ? "" : "s"}`;
    if (!trips.length) {
      wrap.innerHTML = `<div class="state"><svg class="icon"><use href="vendor/icons.svg#i-path"></use></svg>
        <span class="state-title">No completed operations yet</span>
        <span class="state-hint">Trips appear as soon as the simulator has produced telemetry. Give it a few seconds.</span></div>`;
      return;
    }
    wrap.innerHTML = `<table class="data"><thead><tr>
      <th>Operation</th><th>Vehicle</th><th>Route</th>
      <th style="text-align:right">km</th><th style="text-align:right">L</th>
      <th style="text-align:right">TTW kg</th><th style="text-align:right">WTW kg</th>
      <th style="text-align:right">t.km</th><th style="text-align:right">g CO2e/t.km</th>
      <th style="text-align:right">Behaviour</th></tr></thead><tbody>
      ${trips.map((t) => `<tr class="clickable" data-trip="${esc(t.trip_id)}" data-selected="${t.trip_id === state.selectedTrip}">
        <td class="id">${esc(t.trip_id)}</td><td class="id">${esc(t.vehicle_id)}</td>
        <td style="color:${C.dim}">${esc(t.route || "unmatched")}</td>
        <td class="n">${fmt(t.distance_km, 2)}</td><td class="n">${fmt(t.fuel_l, 2)}</td>
        <td class="n">${fmt(t.co2_ttw_kg, 2)}</td><td class="n">${fmt(t.co2_wtw_kg, 2)}</td>
        <td class="n">${fmt(t.tkm, 1)}</td>
        <td class="n">${t.tkm > 0.001 ? fmt(t.intensity_g_per_tkm, 0) : "n/a"}</td>
        <td class="n">${fmt(t.behaviour_score, 0)}</td></tr>`).join("")}
      </tbody></table>`;
  } catch (e) {
    wrap.innerHTML = `<div class="state" data-tone="err"><svg class="icon"><use href="vendor/icons.svg#i-warning-octagon"></use></svg>
      <span class="state-title">Could not load operations</span><span class="state-hint">${esc(e.message)}</span></div>`;
  }
}

$("trips-table-wrap").addEventListener("click", (ev) => {
  const row = ev.target.closest("tr[data-trip]");
  if (row) { state.selectedTrip = row.dataset.trip; loadTrips(); loadTripDetail(row.dataset.trip); }
});
$("btn-refresh-trips").addEventListener("click", loadTrips);
$("btn-export-csv").addEventListener("click", () => {
  const q = state.selectedTrip ? `?trip_id=${encodeURIComponent(state.selectedTrip)}&format=csv` : "?format=csv";
  window.location.href = `/api/report/iso14083${q}`;
});
$("btn-export-pdf").addEventListener("click", () => {
  const q = state.selectedTrip ? `?trip_id=${encodeURIComponent(state.selectedTrip)}&format=pdf` : "?format=pdf";
  window.location.href = `/api/report/iso14083${q}`;
});

async function loadTripDetail(tripId) {
  const box = $("trip-detail");
  box.innerHTML = `<div class="sk-rows"><div class="sk sk-row"></div><div class="sk sk-row"></div><div class="sk sk-row"></div></div>`;
  try {
    const r = await fetch(`/api/trip/${encodeURIComponent(tripId)}`);
    if (!r.ok) throw new Error(`no detail for ${tripId}`);
    const d = await r.json();
    const iso = d.iso14083;
    const alerts = d.alerts || [];

    box.innerHTML = `
      <h2 class="section-title">${esc(tripId)}</h2>
      <p class="section-note">${esc(iso.vehicle_category)} ${esc(iso.vehicle_id)}, driver ${esc(iso.driver_id)},
        ${esc(iso.route || "unmatched route")}. ${esc(iso.data_quality)}</p>
      <div class="grid-2">
        <div>
          <div class="chart-box"><canvas id="chart-trip"></canvas></div>
        </div>
        <div>
          <div class="tbl-wrap"><table class="data"><tbody>
            ${[["Distance", `${fmt(iso.distance_km, 2)} km`],
               ["Cargo mass", `${fmt(iso.cargo_mass_t, 2)} t`],
               ["Transport activity", `${fmt(iso.transport_activity_tkm, 1)} t.km`],
               ["Energy consumed", `${fmt(iso.energy_consumed_l, 2)} L ${esc(iso.fuel_type)}`],
               ["CO2e tank to wheel", `${fmt(iso.co2e_ttw_kg, 3)} kg`],
               ["CO2e well to wheel", `${fmt(iso.co2e_wtw_kg, 3)} kg`],
               ["GHG intensity", iso.transport_activity_tkm > 0.001 ? `${fmt(iso.ghg_intensity_g_per_tkm, 1)} g CO2e/t.km` : "not defined at zero t.km"],
               ["Idle time", `${Math.round(iso.idle_seconds / 60)} min`],
               ["Harsh events", String(iso.harsh_events)],
               ["Behaviour score", `${fmt(iso.behaviour_score, 0)} / 100`]]
              .map(([k, v]) => `<tr><td style="color:${C.dim}">${esc(k)}</td><td class="n">${esc(v)}</td></tr>`).join("")}
          </tbody></table></div>
        </div>
      </div>
      <h2 class="section-title" style="margin-top:22px">Alerts during this operation</h2>
      ${alerts.length
        ? `<div class="tbl-wrap" style="max-height:220px"><table class="data"><thead><tr>
             <th>Time</th><th>Type</th><th>Severity</th><th>Detail</th></tr></thead><tbody>
             ${alerts.map((a) => `<tr><td class="id">${hhmmss(a.ts)}</td><td>${esc(a.type.replace(/_/g, " "))}</td>
               <td style="color:${a.severity === "HIGH" ? C.crit : a.severity === "MEDIUM" ? C.warn : C.dim}">${esc(a.severity)}</td>
               <td style="white-space:normal">${esc(a.message)}</td></tr>`).join("")}
           </tbody></table></div>`
        : `<div class="callout">No alerts were raised during this operation.</div>`}`;

    const prof = d.profile || [];
    if (prof.length) {
      new Chart($("chart-trip"), {
        type: "line",
        data: {
          labels: prof.map((p) => hhmmss(p.ts)),
          datasets: [
            { label: "speed, km/h", data: prof.map((p) => p.speed_kmh), borderColor: SERIES[1], yAxisID: "y", pointRadius: 0, borderWidth: 1.5, tension: 0.25 },
            { label: "CO2, g/s", data: prof.map((p) => p.co2_gps), borderColor: C.warn, yAxisID: "y1", pointRadius: 0, borderWidth: 1.5, tension: 0.25 },
          ],
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          scales: {
            x: { ...axisStyle, ticks: { ...axisStyle.ticks, maxTicksLimit: 7 } },
            y: { ...axisStyle, position: "left", beginAtZero: true, title: { display: true, text: "km/h", color: C.faint, font: { size: 10 } } },
            y1: { ...axisStyle, position: "right", beginAtZero: true, grid: { drawOnChartArea: false }, title: { display: true, text: "g CO2/s", color: C.faint, font: { size: 10 } } },
          },
          plugins: { legend: { labels: { color: C.dim, boxWidth: 9, font: { size: 10.5 } } } },
        },
      });
    } else {
      $("chart-trip").closest(".chart-box").innerHTML =
        `<div class="state"><span class="state-hint">No profile retained for this operation. Profiles are kept for trips seen by the running backend.</span></div>`;
    }
  } catch (e) {
    box.innerHTML = `<div class="state" data-tone="err"><svg class="icon"><use href="vendor/icons.svg#i-warning-octagon"></use></svg>
      <span class="state-title">Could not load that operation</span><span class="state-hint">${esc(e.message)}</span></div>`;
  }
}

/* ===================================================================== tabs */
document.querySelectorAll("nav.tabs button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("nav.tabs button").forEach((b) => b.setAttribute("aria-selected", String(b === btn)));
    const target = btn.dataset.tab;
    document.querySelectorAll(".pane").forEach((p) => { p.dataset.active = String(p.id === `pane-${target}`); });
    if (target === "live" && state.map) setTimeout(() => state.map.invalidateSize(), 60);
    if (target === "models") loadModels();
    if (target === "trips") loadTrips();
  });
});

/* ===================================================================== boot */
initMap();
initLiveChart();
loadScenarios();
connect();
pollHealth();
setInterval(pollHealth, 2000);

fetch("/api/routes").then((r) => r.json()).then((d) => drawRoutes(d.routes)).catch(() => { /* snapshot also carries routes */ });
