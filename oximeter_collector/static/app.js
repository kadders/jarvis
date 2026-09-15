const API = "";

let selectedDevice = null;
let selectedSession = null;
let liveChart = null;
let sessionChart = null;
let deviceWs = null;
const livePoints = { ts: [], hr: [], spo2: [] };
const MAX_LIVE = 300;

function toast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.remove("hidden");
  setTimeout(() => el.classList.add("hidden"), 3500);
}

function setWsState(state) {
  const pill = document.getElementById("ws-pill");
  pill.dataset.state = state;
  pill.textContent =
    state === "ok" ? "WS live" : state === "off" ? "WS off" : state === "err" ? "WS err" : "WS …";
}

async function api(path, opts = {}) {
  const r = await fetch(API + path, opts);
  if (!r.ok) throw new Error(await r.text());
  const ct = r.headers.get("content-type") || "";
  if (ct.includes("application/json")) return r.json();
  return r.text();
}

function applyConfigToForm(cfg) {
  if (!cfg) return;
  document.getElementById("wifi_ssid").value = cfg.wifi?.ssid || "";
  document.getElementById("wifi_pass").value = cfg.wifi?.pass || "";
  document.getElementById("mqtt_host").value = cfg.mqtt?.host || "";
  document.getElementById("mqtt_port").value = cfg.mqtt?.port ?? 1883;
  document.getElementById("mqtt_prefix").value = cfg.mqtt?.prefix || "";
  document.getElementById("mqtt_enabled").checked = cfg.mqtt?.enabled !== false;
  document.getElementById("ble_name").value = cfg.ble?.name || "";
  const led = cfg.sensor?.led_current;
  document.getElementById("led_current").value =
    typeof led === "number" ? led.toString(16) : led || "24";
}

async function checkHealth() {
  const pill = document.getElementById("health-pill");
  try {
    const h = await api("/health");
    pill.textContent = `OK · ${h.devices} devices`;
    pill.dataset.state = "ok";
  } catch (e) {
    pill.textContent = "offline";
    pill.dataset.state = "err";
  }
}

function renderLiveChart() {
  const target = document.getElementById("chart-live");
  if (!window.uPlot) return;
  const data = [livePoints.ts, livePoints.hr, livePoints.spo2];
  const opts = {
    width: target.clientWidth || 600,
    height: 220,
    series: [{}, { label: "HR", stroke: "#f66" }, { label: "SpO2", stroke: "#6cf" }],
    scales: { x: { time: false } },
  };
  if (liveChart) {
    liveChart.setData(data);
  } else {
    liveChart = new uPlot(opts, data, target);
  }
}

function pushLivePoint(pt) {
  const t = livePoints.ts.length ? livePoints.ts[livePoints.ts.length - 1] + 1 : 0;
  livePoints.ts.push(t);
  livePoints.hr.push(pt.valid && pt.hr >= 0 ? pt.hr : null);
  livePoints.spo2.push(pt.valid && pt.spo2 >= 0 ? pt.spo2 : null);
  if (livePoints.ts.length > MAX_LIVE) {
    livePoints.ts.shift();
    livePoints.hr.shift();
    livePoints.spo2.shift();
  }
  renderLiveChart();
}

function updateLiveStats(device, extra = {}) {
  document.getElementById("live-stats").textContent = JSON.stringify(
    {
      hr: device?.last_hr,
      spo2: device?.last_spo2,
      valid: device?.last_valid,
      ip: device?.ip,
      ack: device?.config?._last_ack,
      ...extra,
    },
    null,
    2
  );
}

function disconnectWs() {
  if (deviceWs) {
    deviceWs.close();
    deviceWs = null;
  }
  setWsState("off");
}

function connectWs(deviceId) {
  disconnectWs();
  if (!deviceId) return;
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const url = `${proto}//${location.host}/api/v1/devices/${encodeURIComponent(deviceId)}/ws`;
  deviceWs = new WebSocket(url);
  setWsState("…");
  deviceWs.onopen = () => setWsState("ok");
  deviceWs.onclose = () => {
    if (selectedDevice === deviceId) setWsState("off");
  };
  deviceWs.onerror = () => setWsState("err");
  deviceWs.onmessage = (ev) => {
    let msg;
    try {
      msg = JSON.parse(ev.data);
    } catch {
      return;
    }
    if (msg.type === "ping") return;
    if (msg.type === "reading" && msg.data) {
      pushLivePoint(msg.data);
      return;
    }
    if (msg.type === "status" && msg.data) {
      updateLiveStats(msg.data);
      return;
    }
    if (msg.type === "config_ack") {
      toast(msg.data?.ok ? "Config ack OK" : "Config ack failed");
      updateLiveStats({ config: { _last_ack: msg.data } });
      return;
    }
    if (msg.type === "session_open" || msg.type === "session_closed") {
      loadSessions();
    }
    if (msg.type === "device" && msg.data) {
      updateLiveStats(msg.data);
    }
  };
}

async function loadDevices() {
  const { devices } = await api("/api/v1/devices");
  const ul = document.getElementById("device-list");
  ul.innerHTML = "";
  devices.forEach((d) => {
    const li = document.createElement("li");
    li.textContent = `${d.device_id} · ${d.online ? "online" : "offline"} · HR ${d.last_hr ?? "—"}`;
    if (d.device_id === selectedDevice) li.classList.add("active");
    li.onclick = () => selectDevice(d.device_id);
    ul.appendChild(li);
  });
}

async function loadSessions() {
  const q = selectedDevice ? `?device_id=${encodeURIComponent(selectedDevice)}` : "";
  const { sessions } = await api(`/api/v1/sessions${q}`);
  const ul = document.getElementById("session-list");
  ul.innerHTML = "";
  sessions.forEach((s) => {
    const li = document.createElement("li");
    li.textContent = `${s.session_id.slice(0, 20)}… · ${s.n_valid}/${s.n_samples} valid`;
    if (s.session_id === selectedSession) li.classList.add("active");
    li.onclick = () => selectSession(s.session_id);
    ul.appendChild(li);
  });
}

async function selectDevice(id) {
  selectedDevice = id;
  livePoints.ts = [];
  livePoints.hr = [];
  livePoints.spo2 = [];
  const d = await api(`/api/v1/devices/${id}`);
  document.getElementById("device-title").textContent = id;
  document.getElementById("device-sub").textContent =
    `IP ${d.ip || "—"} · FW ${d.fw || "—"} · Bat ${d.battery ?? "—"}%`;
  document.getElementById("mqtt_prefix").value = d.mqtt_prefix || `oximeter/${id}`;
  document.getElementById("ble_name").value = d.ble_name || `Oximeter-${id}`;
  document.getElementById("mqtt-stream-link").href =
    `/api/v1/mqtt/events?topic=oximeter/${encodeURIComponent(id)}/readings&limit=50&order=desc`;
  updateLiveStats(d);
  await loadDevices();
  await loadSessions();
  connectWs(id);
}

async function selectSession(sid) {
  selectedSession = sid;
  document.getElementById("session-panel").hidden = false;
  document.getElementById("export-csv").href = `/api/v1/sessions/${encodeURIComponent(sid)}/export.csv`;
  const s = await api(`/api/v1/sessions/${sid}`);
  const series = await api(`/api/v1/sessions/${sid}/series?max_points=1500`);
  let analytics = {};
  try {
    analytics = await api(`/api/v1/sessions/${sid}/analytics`);
  } catch (e) {
    console.warn(e);
  }
  document.getElementById("session-title").textContent = sid;
  document.getElementById("session-summary").textContent = JSON.stringify(s, null, 2);
  document.getElementById("session-analytics").textContent = JSON.stringify(analytics, null, 2);
  const pts = series.points || [];
  const ts = pts.map((_, i) => i);
  const hr = pts.map((p) => (p.valid ? p.hr : null));
  const spo2 = pts.map((p) => (p.valid ? p.spo2 : null));
  const target = document.getElementById("chart-session");
  const data = [ts, hr, spo2];
  const opts = {
    width: target.clientWidth || 600,
    height: 220,
    series: [{}, { label: "HR", stroke: "#f66" }, { label: "SpO2", stroke: "#6cf" }],
  };
  if (sessionChart) sessionChart.destroy();
  sessionChart = new uPlot(opts, data, target);
  await loadSessions();
}

document.getElementById("refresh-devices").onclick = loadDevices;
document.getElementById("refresh-sessions").onclick = loadSessions;

document.getElementById("load-template").onclick = async () => {
  const q = selectedDevice ? `?device_id=${encodeURIComponent(selectedDevice)}` : "";
  try {
    const tpl = await api(`/api/v1/config/template${q}`);
    applyConfigToForm(tpl);
    toast("Template loaded");
  } catch (e) {
    toast(String(e));
  }
};

document.getElementById("fetch-config").onclick = async () => {
  if (!selectedDevice) {
    toast("Select a device first");
    return;
  }
  try {
    const res = await api(`/api/v1/devices/${selectedDevice}/config`);
    document.getElementById("config-result").textContent = JSON.stringify(res, null, 2);
    if (res.ok && res.config) applyConfigToForm(res.config);
    toast(res.ok ? "Fetched device config" : res.error || "Fetch failed");
  } catch (e) {
    toast(String(e));
  }
};

document.getElementById("reboot-device").onclick = async () => {
  if (!selectedDevice) {
    toast("Select a device first");
    return;
  }
  if (!confirm(`Reboot ${selectedDevice}?`)) return;
  try {
    const res = await api(`/api/v1/devices/${selectedDevice}/reboot`, { method: "POST" });
    toast(res.ok ? "Reboot sent" : "Reboot failed");
  } catch (e) {
    toast(String(e));
  }
};

document.getElementById("config-form").onsubmit = async (e) => {
  e.preventDefault();
  if (!selectedDevice) {
    toast("Select a device first");
    return;
  }
  const body = {
    v: 1,
    via: document.getElementById("push_via").value,
    wifi: {
      ssid: document.getElementById("wifi_ssid").value,
      pass: document.getElementById("wifi_pass").value,
    },
    mqtt: {
      enabled: document.getElementById("mqtt_enabled").checked,
      host: document.getElementById("mqtt_host").value,
      port: +document.getElementById("mqtt_port").value,
      prefix: document.getElementById("mqtt_prefix").value,
      user: "",
      pass: "",
    },
    ble: {
      enabled: true,
      name: document.getElementById("ble_name").value,
    },
    sensor: {
      led_current: parseInt(document.getElementById("led_current").value, 16) || 36,
    },
  };
  try {
    const res = await api(`/api/v1/devices/${selectedDevice}/config`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    document.getElementById("config-result").textContent = JSON.stringify(res, null, 2);
    toast(res.ok ? "Config pushed" : "Push failed");
  } catch (err) {
    toast(String(err));
  }
};

checkHealth();
loadDevices();
loadSessions();
setInterval(checkHealth, 15000);
