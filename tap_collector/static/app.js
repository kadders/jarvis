(() => {
  const API = "/api/v1";
  let selectedId = null;
  let selectedGuid = null;
  let seriesPlot = null;
  let spectrumPlot = null;
  let rpmPlot = null;
  let rpmTelPlot = null;
  let libSearchTimer = null;
  let toolsCache = [];
  let draftSegments = [];
  let sessionMeta = null;
  let segmentAnalysis = [];
  let seriesData = null;
  let spectrumData = null;
  let activeSpectrumSeg = "";
  let mqttLiveTimer = null;
  let mqttSelectedId = null;
  let mqttSinceId = null;
  let sessionWs = null;
  let sessionWsBackoff = 1000;
  let sessionListTimer = null;
  let liveFrame = null;
  let motionData = null;
  let simScrubT = 0;
  let simThree = null;
  let simPlaying = false;
  let simSpeed = 1;
  let simRaf = null;
  let simLastTs = 0;
  let simPathSignature = "";
  let simPathDrawCount = -1;
  let simDomLastMs = 0;
  let simCameraDirty = true;

  const SEG_COLORS = [
    "#3dba8a",
    "#7eb8ff",
    "#d4a017",
    "#c47ad4",
    "#e07a5f",
    "#81b29a",
  ];

  function hashStr(s) {
    let h = 0;
    const str = String(s || "");
    for (let i = 0; i < str.length; i++) {
      h = (h * 31 + str.charCodeAt(i)) | 0;
    }
    return Math.abs(h);
  }

  function toolColorKey(seg) {
    if (!seg) return "unknown";
    if (seg.tool_guid) return `g:${seg.tool_guid}`;
    if (seg.tool_number != null) return `n:${seg.tool_number}`;
    if (seg.segment_id) return `s:${seg.segment_id}`;
    return "unknown";
  }

  function toolColor(segOrKey) {
    const key =
      typeof segOrKey === "string" ? segOrKey : toolColorKey(segOrKey);
    return SEG_COLORS[hashStr(key) % SEG_COLORS.length];
  }

  const el = {
    list: document.getElementById("session-list"),
    toolList: document.getElementById("tool-list"),
    libCount: document.getElementById("lib-count"),
    libSearch: document.getElementById("lib-search"),
    empty: document.getElementById("empty-state"),
    panel: document.getElementById("panel"),
    toolPanel: document.getElementById("tool-panel"),
    title: document.getElementById("sess-title"),
    sub: document.getElementById("sess-sub"),
    analysis: document.getElementById("analysis-card"),
    liveStatus: document.getElementById("live-status"),
    recPill: document.getElementById("rec-pill"),
    liveStatusDetail: document.getElementById("live-status-detail"),
    offsetCard: document.getElementById("offset-card"),
    seriesMeta: document.getElementById("series-meta"),
    spectrumMeta: document.getElementById("spectrum-meta"),
    rpmMeta: document.getElementById("rpm-meta"),
    rpmTelMeta: document.getElementById("rpm-tel-meta"),
    chartSeries: document.getElementById("chart-series"),
    chartSpectrum: document.getElementById("chart-spectrum"),
    chartRpm: document.getElementById("chart-rpm"),
    chartRpmTel: document.getElementById("chart-rpm-tel"),
    segOverlay: document.getElementById("seg-overlay"),
    rpmTelOverlay: document.getElementById("rpm-tel-overlay"),
    seriesMarkers: document.getElementById("series-markers"),
    rpmTelMarkers: document.getElementById("rpm-tel-markers"),
    rpmTelLegend: document.getElementById("rpm-tel-legend"),
    toolLegend: document.getElementById("tool-legend"),
    segBody: document.getElementById("seg-body"),
    segDetail: document.getElementById("seg-detail"),
    forceCard: document.getElementById("force-card"),
    forceMaterial: document.getElementById("force-material"),
    spectrumSegSelect: document.getElementById("spectrum-seg-select"),
    rpmLegend: document.getElementById("rpm-legend"),
    toolTitle: document.getElementById("tool-title"),
    toolSub: document.getElementById("tool-sub"),
    toolCard: document.getElementById("tool-card"),
    toolRaw: document.getElementById("tool-raw"),
    health: document.getElementById("health-pill"),
    toast: document.getElementById("toast"),
    file: document.getElementById("file-input"),
    libFile: document.getElementById("lib-input"),
    libReplace: document.getElementById("lib-replace"),
    sessNotes: document.getElementById("sess-notes"),
    saveNotes: document.getElementById("save-notes-btn"),
    stopSess: document.getElementById("stop-sess-btn"),
    deleteSess: document.getElementById("delete-sess-btn"),
    deleteTool: document.getElementById("delete-tool-btn"),
    refresh: document.getElementById("refresh-btn"),
    libRefresh: document.getElementById("lib-refresh-btn"),
    addSeg: document.getElementById("add-seg-btn"),
    saveSeg: document.getElementById("save-seg-btn"),
    segEmptyCta: document.getElementById("seg-empty-cta"),
    mqttPanel: document.getElementById("mqtt-panel"),
    mqttStreamBtn: document.getElementById("mqtt-stream-btn"),
    mqttSub: document.getElementById("mqtt-sub"),
    mqttStats: document.getElementById("mqtt-stats"),
    mqttTopic: document.getElementById("mqtt-topic"),
    mqttWhere: document.getElementById("mqtt-where"),
    mqttQ: document.getElementById("mqtt-q"),
    mqttSearchBtn: document.getElementById("mqtt-search-btn"),
    mqttLiveBtn: document.getElementById("mqtt-live-btn"),
    mqttClearBtn: document.getElementById("mqtt-clear-btn"),
    mqttList: document.getElementById("mqtt-list"),
    mqttDetail: document.getElementById("mqtt-detail"),
    simMeta: document.getElementById("sim-meta"),
    simCanvas: document.getElementById("sim-canvas"),
    simScrubber: document.getElementById("sim-scrubber"),
    simScrubLabel: document.getElementById("sim-scrub-label"),
    simAxisA: document.getElementById("sim-axis-a"),
    simReplayBtn: document.getElementById("sim-replay-btn"),
    simPlayBtn: document.getElementById("sim-play-btn"),
    simSpeed: document.getElementById("sim-speed"),
    simResetViewBtn: document.getElementById("sim-reset-view-btn"),
  };

  function toast(msg, isErr) {
    el.toast.textContent = msg;
    el.toast.classList.toggle("is-err", !!isErr);
    el.toast.classList.remove("hidden");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.toast.classList.add("hidden"), 4000);
  }

  function stopMqttLive() {
    if (mqttLiveTimer) {
      clearInterval(mqttLiveTimer);
      mqttLiveTimer = null;
    }
    if (el.mqttLiveBtn) {
      el.mqttLiveBtn.dataset.live = "0";
      el.mqttLiveBtn.textContent = "Live off";
    }
  }

  function showSessionView() {
    stopMqttLive();
    el.empty.classList.add("hidden");
    el.toolPanel.classList.add("hidden");
    if (el.mqttPanel) el.mqttPanel.classList.add("hidden");
    el.panel.classList.remove("hidden");
  }

  function showToolView() {
    stopMqttLive();
    stopSessionWs();
    el.empty.classList.add("hidden");
    el.panel.classList.add("hidden");
    if (el.mqttPanel) el.mqttPanel.classList.add("hidden");
    el.toolPanel.classList.remove("hidden");
  }

  function showMqttView() {
    // MQTT ring-buffer Live is unrelated to session recording WebSocket.
    selectedId = null;
    selectedGuid = null;
    stopSessionWs();
    el.empty.classList.add("hidden");
    el.panel.classList.add("hidden");
    el.toolPanel.classList.add("hidden");
    if (el.mqttPanel) el.mqttPanel.classList.remove("hidden");
    destroyPlots();
    loadList().catch(() => {});
    loadTools(el.libSearch.value.trim() || undefined).catch(() => {});
    queryMqttEvents({ append: false }).catch((e) => toast(String(e), true));
  }

  async function checkHealth() {
    try {
      const r = await fetch("/health");
      const j = await r.json();
      el.health.dataset.state = r.ok ? "ok" : "err";
      el.health.textContent = r.ok
        ? `ok · ${j.sessions || 0} sess · ${j.tools || 0} tools · ${j.mqtt_events || 0} mqtt`
        : "down";
    } catch {
      el.health.dataset.state = "err";
      el.health.textContent = "unreachable";
    }
  }

  function destroyPlots() {
    if (seriesPlot) {
      seriesPlot.destroy();
      seriesPlot = null;
    }
    if (spectrumPlot) {
      spectrumPlot.destroy();
      spectrumPlot = null;
    }
    if (rpmPlot) {
      rpmPlot.destroy();
      rpmPlot = null;
    }
    if (rpmTelPlot) {
      rpmTelPlot.destroy();
      rpmTelPlot = null;
    }
    el.chartSeries.innerHTML = "";
    el.chartSpectrum.innerHTML = "";
    el.chartRpm.innerHTML = "";
    if (el.chartRpmTel) el.chartRpmTel.innerHTML = "";
    el.segOverlay.innerHTML = "";
    if (el.rpmTelOverlay) el.rpmTelOverlay.innerHTML = "";
    if (el.seriesMarkers) el.seriesMarkers.innerHTML = "";
    if (el.rpmTelMarkers) el.rpmTelMarkers.innerHTML = "";
    if (el.toolLegend) el.toolLegend.innerHTML = "";
    if (el.rpmTelLegend) el.rpmTelLegend.innerHTML = "";
  }

  function timeSpan() {
    const tStart =
      sessionMeta && sessionMeta.t_start != null ? Number(sessionMeta.t_start) : 0;
    let tEnd =
      sessionMeta && sessionMeta.t_end != null ? Number(sessionMeta.t_end) : tStart + 1;
    const samples =
      (liveFrame && liveFrame.rpm_samples) ||
      (sessionMeta && sessionMeta.rpm_samples) ||
      [];
    samples.forEach((s) => {
      if (s && s.t_s != null) tEnd = Math.max(tEnd, Number(s.t_s));
    });
    draftSegments.forEach((seg) => {
      if (seg && seg.t1 != null) tEnd = Math.max(tEnd, Number(seg.t1));
    });
    return { tStart, tEnd, span: Math.max(1e-6, tEnd - tStart) };
  }

  function renderToolLegend() {
    if (!el.toolLegend) return;
    const seen = new Map();
    draftSegments.forEach((seg) => {
      const key = toolColorKey(seg);
      if (seen.has(key)) return;
      const label = (
        (seg.tool_number != null ? `T${seg.tool_number}` : "") +
        (seg.tool_name ? ` ${seg.tool_name}` : "")
      ).trim() || seg.segment_id || key;
      seen.set(key, { label, color: toolColor(key) });
    });
    el.toolLegend.innerHTML = [...seen.values()]
      .map(
        (x) =>
          `<span class="tool-legend-item"><span class="tool-legend-swatch" style="background:${x.color}"></span>${escapeHtml(x.label)}</span>`
      )
      .join("");
  }

  function paintSegmentBands(overlayEl) {
    if (!overlayEl) return;
    overlayEl.innerHTML = "";
    const { tStart, span } = timeSpan();
    const windows =
      (liveFrame && liveFrame.transition_windows) ||
      (sessionMeta && sessionMeta.transition_windows) ||
      [];
    windows.forEach((w) => {
      const left = ((Number(w.t0) - tStart) / span) * 100;
      const width = ((Number(w.t1) - Number(w.t0)) / span) * 100;
      const div = document.createElement("div");
      div.className = "transition-band";
      div.style.left = `${Math.max(0, left)}%`;
      div.style.width = `${Math.max(0.2, width)}%`;
      div.title = `${w.kind || "transition"} ${Number(w.t0).toFixed(2)}–${Number(w.t1).toFixed(2)}s`;
      overlayEl.appendChild(div);
    });
    draftSegments.forEach((seg) => {
      const left = ((Number(seg.t0) - tStart) / span) * 100;
      const width = ((Number(seg.t1) - Number(seg.t0)) / span) * 100;
      const div = document.createElement("div");
      div.className = "seg-band";
      div.style.left = `${Math.max(0, left)}%`;
      div.style.width = `${Math.max(0.5, width)}%`;
      div.style.background = toolColor(seg);
      const label =
        seg.tool_name ||
        (seg.tool_number != null ? `T${seg.tool_number}` : seg.segment_id);
      div.title = `${label}: ${Number(seg.t0).toFixed(2)}–${Number(seg.t1).toFixed(2)} s`;
      div.textContent = label;
      overlayEl.appendChild(div);
    });
  }

  function paintEventMarkers(markerEl) {
    if (!markerEl) return;
    markerEl.innerHTML = "";
    const { tStart, span } = timeSpan();
    const windows =
      (liveFrame && liveFrame.transition_windows) ||
      (sessionMeta && sessionMeta.transition_windows) ||
      [];
    windows.forEach((w) => {
      const te = w.t_event != null ? Number(w.t_event) : (Number(w.t0) + Number(w.t1)) / 2;
      const left = ((te - tStart) / span) * 100;
      if (left < -2 || left > 102) return;
      const div = document.createElement("div");
      div.className = `event-marker kind-${w.kind || "event"}`;
      div.style.left = `${left}%`;
      if (w.kind === "tool_change") {
        div.style.borderLeftColor = toolColor(w);
      }
      let label = w.kind || "event";
      if (w.kind === "tool_change") {
        label = w.tool_number != null ? `T${w.tool_number}` : "tool";
      } else if (w.kind === "spindle_start") {
        label = "spindle on";
      } else if (w.kind === "spindle_stop") {
        label = "spindle off";
      }
      div.textContent = label;
      div.title = `${w.kind} @ ${te.toFixed(2)}s`;
      markerEl.appendChild(div);
    });
  }

  function chartWidth(node) {
    return Math.max(280, node.clientWidth - 16);
  }

  function mergeUplotScales(optScales) {
    const scales = optScales || {};
    // Recording-relative t_s / Hz / RPM must not use uPlot's default Unix-time
    // x scale (that renders small numbers as Dec 31 1969 in US timezones).
    return { ...scales, x: { ...(scales.x || {}), time: false } };
  }

  function fmtAxisNum(vals) {
    return vals.map((v) => {
      if (v == null || !Number.isFinite(Number(v))) return "";
      const n = Number(v);
      const abs = Math.abs(n);
      if (abs >= 1000) return String(Math.round(n));
      if (abs >= 100) return n.toFixed(0);
      if (abs >= 10) return n.toFixed(1);
      return n.toFixed(2);
    });
  }

  function withNumericXAxis(axes) {
    if (!axes || !axes.length) {
      return [
        {
          stroke: "#8aa094",
          grid: { stroke: "rgba(180,210,190,0.1)" },
          ticks: { stroke: "rgba(180,210,190,0.2)" },
          values: (_u, vals) => fmtAxisNum(vals),
        },
        {
          stroke: "#8aa094",
          grid: { stroke: "rgba(180,210,190,0.1)" },
          ticks: { stroke: "rgba(180,210,190,0.2)" },
        },
      ];
    }
    return axes.map((axis, i) => {
      if (i !== 0 || !axis || typeof axis !== "object") return axis;
      if (typeof axis.values === "function") return axis;
      return { ...axis, values: (_u, vals) => fmtAxisNum(vals) };
    });
  }

  function makePlot(target, opts, data) {
    const optsSafe = opts || {};
    const merged = {
      ...optsSafe,
      width: chartWidth(target),
      height: 220,
      scales: mergeUplotScales(optsSafe.scales),
      axes: withNumericXAxis(optsSafe.axes),
    };
    return new uPlot(merged, data, target);
  }

  function newSegId() {
    return `seg-${Math.random().toString(16).slice(2, 12)}`;
  }

  function toolLabel(t) {
    if (!t) return "—";
    const num = t.tool_number != null ? `T${t.tool_number}` : "";
    return [num, t.description || t.product_id || t.guid]
      .filter(Boolean)
      .join(" ");
  }

  function findTool(guid) {
    return toolsCache.find((t) => t.guid === guid) || null;
  }

  async function loadList() {
    const r = await fetch(`${API}/sessions`);
    const j = await r.json();
    const sessions = j.sessions || [];
    el.list.innerHTML = "";
    if (!sessions.length) {
      el.list.innerHTML = `<li><p class="hint" style="margin:0">No sessions yet.</p></li>`;
      return;
    }
    for (const s of sessions) {
      const li = document.createElement("li");
      const row = document.createElement("div");
      row.className = "session-row";
      const btn = document.createElement("button");
      btn.type = "button";
      if (s.session_id === selectedId) btn.classList.add("is-active");
      const state = s.recording_state || (s.stopped ? "stopped" : "idle");
      const badge =
        state === "recording" || state === "stalled"
          ? `<span class="badge rec">${state}</span>`
          : s.mode === "live_spindle" || s.mode === "homing"
            ? `<span class="badge">${s.mode}</span>`
            : s.origin === "import"
              ? `<span class="badge">import</span>`
              : `<span class="badge">${state}</span>`;
      const segN = s.n_segments ? ` · ${s.n_segments} seg` : "";
      const toolN =
        s.active_tool_number != null ? ` · T${s.active_tool_number}` : "";
      btn.innerHTML = `${badge}<span class="id">${s.session_id}</span>
        <div class="meta-line">${s.device_id || "—"} · ${s.n_samples || s.n_batches || 0} pts${segN}${toolN}</div>`;
      btn.addEventListener("click", () => selectSession(s.session_id));
      const del = document.createElement("button");
      del.type = "button";
      del.className = "ghost danger sess-row-del";
      del.title = `Delete ${s.session_id}`;
      del.setAttribute("aria-label", `Delete ${s.session_id}`);
      del.textContent = "×";
      del.addEventListener("click", (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        deleteSessionById(s.session_id, {
          active:
            !s.stopped &&
            s.recording_state !== "stopped" &&
            s.recording_state !== "idle",
        }).catch((e) => toast(String(e), true));
      });
      row.appendChild(btn);
      row.appendChild(del);
      li.appendChild(row);
      el.list.appendChild(li);
    }
  }

  async function loadTools(q) {
    const url = q
      ? `${API}/tools?q=${encodeURIComponent(q)}`
      : `${API}/tools`;
    const r = await fetch(url);
    const j = await r.json();
    const tools = j.tools || [];
    toolsCache = tools;
    const n = (j.summary && j.summary.n_tools) || tools.length;
    el.libCount.textContent = n ? `(${n})` : "";
    el.toolList.innerHTML = "";
    if (!tools.length) {
      el.toolList.innerHTML = `<li><p class="hint" style="margin:0">No tools yet — import a Fusion .tools file.</p></li>`;
      return;
    }
    for (const t of tools) {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.type = "button";
      if (t.guid === selectedGuid) btn.classList.add("is-active");
      const num = t.tool_number != null ? `T${t.tool_number}` : "—";
      const diam =
        t.diameter != null
          ? `${Number(t.diameter).toFixed(t.diameter < 10 ? 3 : 2)}`
          : "—";
      const flutes = t.flutes != null ? `${t.flutes}fl` : "";
      btn.innerHTML = `<span class="badge">${num}</span><span class="id">${t.description || t.product_id || t.guid}</span>
        <div class="meta-line">${[t.vendor, diam !== "—" ? `⌀${diam}` : null, flutes, t.type]
          .filter(Boolean)
          .join(" · ")}</div>`;
      btn.addEventListener("click", () => selectTool(t.guid));
      li.appendChild(btn);
      el.toolList.appendChild(li);
    }
    // Refresh segment dropdowns if editing
    if (selectedId) renderSegmentTable();
  }

  function renderAnalysis(bySource) {
    const a = (bySource && (bySource.jarvis || Object.values(bySource)[0])) || null;
    if (!a) {
      el.analysis.innerHTML = `<span class="label">No analysis yet</span>`;
      return;
    }
    const fn = a.fn_hz != null ? Number(a.fn_hz).toFixed(2) : "—";
    const conf = a.suggestion_confidence || "—";
    const showSuggest = conf === "high" || conf === "medium";
    const amin =
      showSuggest && a.suggested_rpm_min != null ? Math.round(a.suggested_rpm_min) : null;
    const amax =
      showSuggest && a.suggested_rpm_max != null ? Math.round(a.suggested_rpm_max) : null;
    const avoid = Array.isArray(a.avoid_rpm)
      ? a.avoid_rpm
          .slice(0, 4)
          .map((x) => Math.round(x))
          .join(", ")
      : "—";
    const spindle = a.spindle_freq_hz != null ? Number(a.spindle_freq_hz) : 400;
    const nyq = a.nyquist_hz != null ? Number(a.nyquist_hz).toFixed(0) : "—";
    const axis = a.axis_used || "—";
    const nAvoid = a.n_avoid_in_range != null ? a.n_avoid_in_range : "—";
    const warns = Array.isArray(a.warnings) ? a.warnings : [];
    const warnHtml = warns.length
      ? `<div class="warn-list">${warns
          .map((w) => `<span class="pill-warn">${escapeHtml(w)}</span>`)
          .join(" ")}</div>`
      : "";
    const spindleNote = a.fn_rejected_as_spindle
      ? `<div class="risk">Dominant peak discarded as spindle 1× (~${spindle} Hz)</div>`
      : "";
    const shaping = a.input_shaping;
    const shapingHtml = shaping && shaping.m593_gcode
      ? `<div><span class="label">input shaping</span> <code>${escapeHtml(shaping.m593_gcode)}</code>
        <span class="muted"> · ${escapeHtml(shaping.shaper_type || "zvd")} · S ${Number(shaping.damping || 0.1).toFixed(3)}</span></div>`
      : `<div><span class="label">input shaping</span> <span class="muted">— (no confident fₙ)</span></div>`;
    el.analysis.innerHTML = `
      <div><span class="label">fₙ</span> <span class="fn">${fn} Hz</span>
        <span class="muted"> · ${escapeHtml(axis)} · ${escapeHtml(a.analysis_mode || "")}</span></div>
      <div><span class="label">engine</span> ${escapeHtml(a.engine || a.source || "—")}</div>
      <div><span class="label">spindle</span> ${spindle} Hz / ${Math.round(spindle * 60)} RPM
        <span class="muted"> · Nyquist ${nyq} Hz</span></div>
      <div><span class="label">suggest RPM</span> ${
        amin != null && amax != null ? `${amin}–${amax}` : "none"
      }
        <span class="muted"> · conf ${escapeHtml(conf)} · in-range avoids ${nAvoid}</span></div>
      <div><span class="label">avoid</span> ${avoid || "—"}</div>
      ${shapingHtml}
      ${spindleNote}
      ${warnHtml}
      <div class="disclaimer">Predicted risk · not a stability-lobe guarantee</div>
    `;
  }

  function renderSegmentOverlay() {
    paintSegmentBands(el.segOverlay);
    paintEventMarkers(el.seriesMarkers);
    paintSegmentBands(el.rpmTelOverlay);
    paintEventMarkers(el.rpmTelMarkers);
    renderToolLegend();
  }

  function renderSegmentTable() {
    el.segBody.innerHTML = "";
    if (el.segEmptyCta) {
      el.segEmptyCta.classList.toggle("hidden", draftSegments.length > 0);
    }
    if (el.saveSeg) {
      el.saveSeg.disabled = !selectedId;
    }
    if (!draftSegments.length) {
      el.segBody.innerHTML = `<tr><td colspan="8" class="hint">No segments — use Add segment above, then Save &amp; reanalyze.</td></tr>`;
      renderSegmentOverlay();
      renderSegDetail();
      populateSpectrumSelect();
      return;
    }
    draftSegments.forEach((seg, idx) => {
      const tr = document.createElement("tr");
      if (seg.ambiguous_tools && seg.ambiguous_tools.length) {
        tr.classList.add("is-ambiguous");
      }
      const toolOpts = [
        `<option value="">— select —</option>`,
        ...toolsCache.map(
          (t) =>
            `<option value="${t.guid}" ${t.guid === seg.tool_guid ? "selected" : ""}>${escapeHtml(toolLabel(t))}</option>`
        ),
      ].join("");
      const amb =
        seg.ambiguous_tools && seg.ambiguous_tools.length
          ? `<div class="amb-warn">${seg.ambiguous_tools.length} matches for T${seg.tool_number}</div>`
          : "";
      tr.innerHTML = `
        <td><input type="number" step="0.01" data-k="t0" data-i="${idx}" value="${Number(seg.t0).toFixed(3)}" /></td>
        <td><input type="number" step="0.01" data-k="t1" data-i="${idx}" value="${Number(seg.t1).toFixed(3)}" /></td>
        <td><input type="number" step="1" data-k="tool_number" data-i="${idx}" value="${seg.tool_number != null ? seg.tool_number : ""}" /></td>
        <td><select data-k="tool_guid" data-i="${idx}">${toolOpts}</select>${amb}</td>
        <td><input type="number" step="1" data-k="flutes" data-i="${idx}" value="${seg.flutes != null ? seg.flutes : ""}" /></td>
        <td><input type="number" step="100" data-k="reference_rpm" data-i="${idx}" value="${seg.reference_rpm != null ? Math.round(seg.reference_rpm) : ""}" /></td>
        <td class="src">${escapeHtml(seg.reference_rpm_source || "—")}</td>
        <td><button type="button" class="ghost danger" data-del="${idx}">×</button></td>
      `;
      el.segBody.appendChild(tr);
    });
    el.segBody.querySelectorAll("input, select").forEach((node) => {
      node.addEventListener("change", onSegFieldChange);
    });
    el.segBody.querySelectorAll("[data-del]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const i = Number(btn.getAttribute("data-del"));
        draftSegments.splice(i, 1);
        renderSegmentTable();
      });
    });
    renderSegmentOverlay();
    renderSegDetail();
    populateSpectrumSelect();
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function onSegFieldChange(ev) {
    const node = ev.target;
    const i = Number(node.getAttribute("data-i"));
    const k = node.getAttribute("data-k");
    const seg = draftSegments[i];
    if (!seg) return;
    let v = node.value;
    if (k === "tool_guid") {
      seg.tool_guid = v || null;
      const t = findTool(v);
      if (t) {
        if (t.flutes != null) seg.flutes = t.flutes;
        if (t.tool_number != null) seg.tool_number = t.tool_number;
        seg.tool_name = t.description || t.product_id;
        if (t.reference_rpm != null) {
          seg.reference_rpm = t.reference_rpm;
          seg.reference_rpm_source = "fusion_reference";
        }
        seg.ambiguous_tools = [];
      }
    } else if (k === "t0" || k === "t1" || k === "reference_rpm") {
      const n = v === "" ? null : Number(v);
      seg[k] = n;
      if (k === "reference_rpm" && n != null) {
        seg.reference_rpm_source = "manual";
      }
    } else if (k === "tool_number" || k === "flutes") {
      seg[k] = v === "" ? null : Number(v);
    }
    seg.manual = true;
    simPathSignature = "";
    renderSegmentOverlay();
    renderSegDetail();
    if (motionData) renderSimView(simScrubT);
  }

  function renderSegDetail() {
    if (!segmentAnalysis.length) {
      el.segDetail.innerHTML = "";
      renderCuttingForce();
      return;
    }
    const cards = segmentAnalysis
      .map((sa, i) => {
        const a = sa.analysis || {};
        const fn = a.fn_hz != null ? Number(a.fn_hz).toFixed(1) : "—";
        const conf = a.suggestion_confidence || "—";
        const good = (sa.observed_good_rpm || [])
          .slice(0, 2)
          .map((b) => Math.round(b.rpm_center))
          .join(", ");
        const bad = (sa.observed_bad_rpm || [])
          .slice(0, 2)
          .map((b) => Math.round(b.rpm_center))
          .join(", ");
        const risk = sa.reference_rpm_risk;
        const riskTxt = risk
          ? risk.in_avoid_band
            ? `ref ${Math.round(risk.rpm)} in avoid band (${risk.source})`
            : `ref ${Math.round(risk.rpm)} outside avoid (${risk.source})`
          : "";
        const peaks = (a.peaks || [])
          .slice(0, 3)
          .map((p) => {
            const tag = p.label === "spindle_1x_suspect" ? "*" : "";
            return `${Number(p.hz).toFixed(0)}Hz${tag}`;
          })
          .join(", ");
        const warns = (a.warnings || []).slice(0, 2).join(", ");
        const shaping = a.input_shaping;
        const shapingLine =
          shaping && shaping.m593_gcode
            ? `<div><span class="label">M593</span> <code>${escapeHtml(shaping.m593_gcode)}</code></div>`
            : "";
        const color = toolColor(sa);
        return `<div class="seg-card" style="border-color:${color}">
          <div class="seg-card-title">${escapeHtml(sa.tool_guid ? (findTool(sa.tool_guid) || {}).description || sa.tool_guid : sa.segment_id || `#${i + 1}`)}</div>
          <div><span class="label">fₙ</span> ${fn} Hz · ${a.n_teeth || "?"}fl · conf ${escapeHtml(conf)}</div>
          ${shapingLine}
          <div><span class="label">peaks</span> ${peaks || "—"} <span class="muted">(* spindle suspect)</span></div>
          <div><span class="label">Nyquist</span> ${a.nyquist_hz != null ? Number(a.nyquist_hz).toFixed(0) : "—"} Hz
            · spindle ${a.spindle_freq_hz != null ? Number(a.spindle_freq_hz) : 400} Hz</div>
          <div><span class="label">obs good</span> ${good || (sa.has_observed_rpm ? "—" : "n/a")}</div>
          <div><span class="label">obs bad</span> ${bad || (sa.has_observed_rpm ? "—" : "n/a")}</div>
          ${riskTxt ? `<div class="risk">${escapeHtml(riskTxt)}</div>` : ""}
          ${warns ? `<div class="risk">${escapeHtml(warns)}</div>` : ""}
        </div>`;
      })
      .join("");
    el.segDetail.innerHTML = cards;
    renderCuttingForce();
  }

  function renderCuttingForce() {
    if (!el.forceCard) return;
    let sa = null;
    if (activeSpectrumSeg) {
      sa = segmentAnalysis.find((s) => s.segment_id === activeSpectrumSeg);
    }
    if (!sa) sa = segmentAnalysis.find((s) => s.cutting_force) || segmentAnalysis[0] || null;
    if (!sa) {
      el.forceCard.innerHTML = `<span class="label">Select a tool segment and Save &amp; reanalyze to estimate cutting load from Fusion presets.</span>`;
      return;
    }
    const cf = sa.cutting_force;
    const inputs = sa.cutting_force_inputs || {};
    const warns = sa.cutting_force_warnings || [];
    if (!cf) {
      el.forceCard.innerHTML = `
        <div><span class="label">tool</span> ${escapeHtml(inputs.tool_name || sa.tool_guid || "—")}</div>
        <div class="risk">${escapeHtml((warns[0] || "missing_tool_cutting_preset").replace(/_/g, " "))}</div>
        <div class="disclaimer">Feed and chipload come from the Fusion tool library only — nothing is invented from material defaults.</div>
      `;
      return;
    }
    const pts = cf.points || [];
    const rows = pts
      .map((p) => {
        const overload = p.spindle_ok === false ? ' class="overload"' : "";
        return `<tr${overload}>
          <td>${Number(p.woc_mm).toFixed(2)}</td>
          <td>${Number(p.doc_mm).toFixed(1)}</td>
          <td>${Math.round(p.tangential_force_n)}</td>
          <td>${Math.round(p.resultant_force_n)}</td>
          <td>${Number(p.torque_nm).toFixed(2)}</td>
          <td>${Math.round(p.power_w)}</td>
          <td>${Math.round(p.mrr_mm3_per_min)}</td>
        </tr>`;
      })
      .join("");
    const mismatch =
      inputs.chipload_mm != null && pts[0] && pts[0].fz_mismatch_pct != null
        ? `${Number(pts[0].fz_mismatch_pct).toFixed(1)}%`
        : "—";
    const warnHtml = warns.length
      ? `<div class="warn-list">${warns
          .map((w) => `<span class="pill-warn">${escapeHtml(w)}</span>`)
          .join(" ")}</div>`
      : "";
    el.forceCard.innerHTML = `
      <div class="force-meta">
        <div><span class="label">tool</span> ${escapeHtml(inputs.tool_name || sa.tool_guid || "—")}
          · Ø ${inputs.diameter_mm != null ? Number(inputs.diameter_mm).toFixed(2) : "—"} mm
          · ${inputs.flutes || "?"} fl</div>
        <div><span class="label">Fusion feed</span> ${
          inputs.feed_mm_min != null ? Math.round(inputs.feed_mm_min) : "—"
        } mm/min
          · target f<sub>z</sub> ${
            inputs.chipload_mm != null ? Number(inputs.chipload_mm).toFixed(4) : "—"
          } mm
          · eff f<sub>z</sub> ${
            inputs.fz_effective_mm != null ? Number(inputs.fz_effective_mm).toFixed(4) : "—"
          }
          · Δ ${mismatch}</div>
        <div><span class="label">RPM</span> ${
          inputs.rpm != null ? Math.round(inputs.rpm) : "—"
        } <span class="muted">(${escapeHtml(inputs.rpm_source || "—")})</span>
          · material ${escapeHtml(inputs.material || "6061 aluminum")}
          · spindle limit ${cf.spindle_power_limit_w || 1500} W</div>
        ${
          cf.overload
            ? `<div class="risk">Max power ${Math.round(cf.max_power_w)} W exceeds 1.5 kW reference (not a machine safety cert)</div>`
            : `<div class="muted">Max power in envelope ${Math.round(cf.max_power_w || 0)} W</div>`
        }
        ${warnHtml}
      </div>
      <table class="force-table">
        <thead>
          <tr>
            <th>WoC (mm)</th><th>DoC (mm)</th><th>Ft (N)</th><th>|F| (N)</th><th>T (N·m)</th><th>P (W)</th><th>MRR (mm³/min)</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="disclaimer">${escapeHtml(cf.disclaimer || "")}</div>
    `;
  }

  function populateSpectrumSelect() {
    const sel = el.spectrumSegSelect;
    const prev = activeSpectrumSeg;
    sel.innerHTML = `<option value="">Whole session</option>`;
    draftSegments.forEach((seg, i) => {
      const label =
        seg.tool_name ||
        (seg.tool_number != null ? `T${seg.tool_number}` : seg.segment_id) ||
        `Segment ${i + 1}`;
      const opt = document.createElement("option");
      opt.value = seg.segment_id || String(i);
      opt.textContent = label;
      sel.appendChild(opt);
    });
    if (prev && [...sel.options].some((o) => o.value === prev)) {
      sel.value = prev;
    } else {
      sel.value = "";
      activeSpectrumSeg = "";
    }
  }

  function renderSpectrumForSelection() {
    if (spectrumPlot) {
      spectrumPlot.destroy();
      spectrumPlot = null;
    }
    el.chartSpectrum.innerHTML = "";
    const segId = el.spectrumSegSelect.value;
    activeSpectrumSeg = segId;
    let freqs = [];
    let mag = [];
    let fnLabel = "";

    if (segId) {
      const sa = segmentAnalysis.find((s) => s.segment_id === segId);
      const a = sa && sa.analysis;
      if (a && a.peaks && a.peaks.length) {
        // Build a simple stem from peaks + use whole-session spectrum as background if available
        const sp = (spectrumData && spectrumData.spectrum) || {};
        freqs = sp.freqs_hz || [];
        mag = sp.mag || [];
        fnLabel = a.fn_hz != null ? `seg peak ~ ${Number(a.fn_hz).toFixed(1)} Hz` : "";
      } else if (spectrumData && spectrumData.spectrum) {
        freqs = spectrumData.spectrum.freqs_hz || [];
        mag = spectrumData.spectrum.mag || [];
        fnLabel = a && a.fn_hz != null ? `seg fₙ ~ ${Number(a.fn_hz).toFixed(1)} Hz` : "";
      }
    } else if (spectrumData && spectrumData.spectrum) {
      const sp = spectrumData.spectrum;
      freqs = sp.freqs_hz || [];
      mag = sp.mag || [];
      fnLabel = sp.fn_hz != null ? `peak ~ ${Number(sp.fn_hz).toFixed(1)} Hz` : "";
    }

    el.spectrumMeta.textContent = fnLabel;
    if (freqs.length) {
      spectrumPlot = makePlot(
        el.chartSpectrum,
        {
          series: [
            {},
            {
              label: "mag",
              stroke: "#d4a017",
              width: 1.5,
              fill: "rgba(212,160,23,0.12)",
            },
          ],
        },
        [freqs, mag]
      );
    }
  }

  function renderRpmDomain() {
    if (rpmPlot) {
      rpmPlot.destroy();
      rpmPlot = null;
    }
    el.chartRpm.innerHTML = "";
    el.rpmLegend.innerHTML = "";

    // Prefer selected segment analysis, else first with data, else whole-session avoid bands
    let sa = null;
    if (activeSpectrumSeg) {
      sa = segmentAnalysis.find((s) => s.segment_id === activeSpectrumSeg);
    }
    if (!sa) sa = segmentAnalysis[0] || null;
    const a =
      (sa && sa.analysis) ||
      (sessionMeta &&
        sessionMeta.analysis_by_source &&
        (sessionMeta.analysis_by_source.jarvis ||
          Object.values(sessionMeta.analysis_by_source)[0])) ||
      null;

    if (!a) {
      el.rpmMeta.textContent = "no analysis";
      return;
    }

    const bands = a.avoid_rpm_bands || [];
    const avoid = a.avoid_rpm || [];
    const conf = a.suggestion_confidence || "none";
    const showSuggest = conf === "high" || conf === "medium";
    const smin = showSuggest ? a.suggested_rpm_min : null;
    const smax = showSuggest ? a.suggested_rpm_max : null;
    const buckets = (sa && sa.rpm_buckets) || [];
    const good = (sa && sa.observed_good_rpm) || [];
    const bad = (sa && sa.observed_bad_rpm) || [];
    const spindleHz = a.spindle_freq_hz != null ? Number(a.spindle_freq_hz) : 400;
    const spindleRpm = Math.round(spindleHz * 60);

    // Build a 1-D RPM axis chart: score vs RPM for buckets; markers for avoid centers
    const rpmMin = 9000;
    const rpmMax = spindleRpm > 0 ? spindleRpm : 24000;
    const xs = [];
    const ys = [];
    for (let r = rpmMin; r <= rpmMax; r += 250) {
      xs.push(r);
      // Score surface: high near avoid bands
      let score = 0.05;
      for (const b of bands) {
        const c = b.center_rpm;
        const half = (b.rpm_max - b.rpm_min) / 2 || 500;
        const d = Math.abs(r - c) / half;
        score = Math.max(score, Math.exp(-d * d));
      }
      ys.push(score);
    }

    // Overlay observed bucket scores (normalized)
    const obsX = buckets.map((b) => b.rpm_center);
    const maxScore = Math.max(1e-9, ...buckets.map((b) => b.score || 0));
    const obsY = buckets.map((b) => (b.score || 0) / maxScore);

    const series = [
      {},
      {
        label: "predicted risk",
        stroke: "rgba(196,92,92,0.85)",
        width: 1.5,
        fill: "rgba(196,92,92,0.12)",
      },
    ];
    const data = [xs, ys];
    if (obsX.length) {
      // Resample observed onto xs via nearest — simpler: second series with nulls
      const obsAligned = xs.map((x) => {
        let best = null;
        let bestD = Infinity;
        obsX.forEach((ox, i) => {
          const d = Math.abs(ox - x);
          if (d < bestD && d < 300) {
            bestD = d;
            best = obsY[i];
          }
        });
        return best;
      });
      series.push({
        label: "observed vib",
        stroke: "#7eb8ff",
        width: 2,
        spanGaps: false,
        points: { show: true, size: 6 },
      });
      data.push(obsAligned);
    }

    rpmPlot = makePlot(el.chartRpm, { series }, data);

    const parts = [];
    parts.push(
      `<span class="pill-ref">spindle ${spindleHz} Hz / ${spindleRpm} RPM</span>`
    );
    if (a.nyquist_hz != null) {
      parts.push(`<span class="pill-warn">Nyquist ${Math.round(a.nyquist_hz)} Hz</span>`);
    }
    if (smin != null && smax != null) {
      parts.push(
        `<span class="pill-ok">suggested ${Math.round(smin)}–${Math.round(smax)} (${escapeHtml(conf)})</span>`
      );
    } else {
      parts.push(`<span class="pill-warn">no suggested pocket (${escapeHtml(conf)})</span>`);
    }
    avoid.slice(0, 4).forEach((r) => {
      parts.push(`<span class="pill-bad">avoid ~${Math.round(r)}</span>`);
    });
    good.slice(0, 2).forEach((b) => {
      parts.push(
        `<span class="pill-ok">obs good ~${Math.round(b.rpm_center)} (n=${b.n_windows})</span>`
      );
    });
    bad.slice(0, 2).forEach((b) => {
      parts.push(
        `<span class="pill-bad">obs bad ~${Math.round(b.rpm_center)} (n=${b.n_windows})</span>`
      );
    });
    if (sa && sa.reference_rpm_risk) {
      const rr = sa.reference_rpm_risk;
      parts.push(
        `<span class="pill-ref">ref ${Math.round(rr.rpm)} · ${escapeHtml(rr.source)}${
          rr.is_observed_evidence ? "" : " (est.)"
        }</span>`
      );
    }
    (a.warnings || []).slice(0, 3).forEach((w) => {
      parts.push(`<span class="pill-warn">${escapeHtml(w)}</span>`);
    });
    el.rpmLegend.innerHTML = parts.join(" ");
    el.rpmMeta.textContent = sa
      ? sa.has_observed_rpm
        ? "predicted + observed"
        : "predicted only"
      : "session predicted";
    renderCuttingForce();
  }

  function rpmTelemetrySegments() {
    if (draftSegments && draftSegments.length) return draftSegments;
    return (sessionMeta && sessionMeta.segments) || [];
  }

  function fusionRefAtTime(t, segments) {
    const n = Number(t);
    if (!Number.isFinite(n)) return null;
    for (const seg of segments || []) {
      const t0 = Number(seg.t0);
      const t1 = Number(seg.t1);
      if (!Number.isFinite(t0) || !Number.isFinite(t1)) continue;
      if (n < t0 || n > t1) continue;
      const rpm = seg.reference_rpm;
      if (rpm != null && Number(rpm) > 0) return Number(rpm);
      return null;
    }
    return null;
  }

  /** Per-time Fusion/tooling reference RPM stepped by segment [t0, t1]. */
  function fusionRefSeries(ts, segments) {
    return (ts || []).map((t) => fusionRefAtTime(t, segments));
  }

  function segmentsHaveFusionRef(segments) {
    return (segments || []).some(
      (s) => s && s.reference_rpm != null && Number(s.reference_rpm) > 0
    );
  }

  function renderRpmTelemetry() {
    if (!el.chartRpmTel) return;
    if (rpmTelPlot) {
      rpmTelPlot.destroy();
      rpmTelPlot = null;
    }
    el.chartRpmTel.innerHTML = "";
    if (el.rpmTelLegend) el.rpmTelLegend.innerHTML = "";

    const samples =
      (liveFrame && liveFrame.rpm_samples) ||
      (sessionMeta && sessionMeta.rpm_samples) ||
      [];
    const measuredSrc = new Set(["measured_modbus", "arborctl"]);
    const commandedSrc = new Set(["commanded_modbus", "commanded_rrf"]);
    const measured = samples
      .filter((s) => s && measuredSrc.has(s.source) && s.t_s != null)
      .sort((a, b) => Number(a.t_s) - Number(b.t_s));
    const commanded = samples
      .filter((s) => s && commandedSrc.has(s.source) && s.t_s != null)
      .sort((a, b) => Number(a.t_s) - Number(b.t_s));
    const loadSamples =
      (liveFrame && liveFrame.load_samples) ||
      (sessionMeta && sessionMeta.load_samples) ||
      [];
    const segs = rpmTelemetrySegments();
    const hasFusion = segmentsHaveFusionRef(segs);

    const meta = liveFrame || sessionMeta || {};
    const aligned = meta.has_aligned_measured_rpm;
    const delta =
      meta.latest_measured_minus_commanded != null
        ? Number(meta.latest_measured_minus_commanded)
        : null;
    const parts = [];
    if (
      !measured.length &&
      !commanded.length &&
      !loadSamples.length &&
      !hasFusion
    ) {
      if (el.rpmTelMeta) {
        el.rpmTelMeta.textContent = "no aligned measured RPM telemetry";
      }
      if (el.rpmTelLegend) {
        el.rpmTelLegend.innerHTML =
          `<span class="pill-warn">no aligned measured RPM telemetry</span>` +
          (meta.rpm_alignment
            ? ` <span class="pill-ref">alignment ${escapeHtml(String(meta.rpm_alignment))}</span>`
            : "");
      }
      paintSegmentBands(el.rpmTelOverlay);
      paintEventMarkers(el.rpmTelMarkers);
      return;
    }

    // Merge onto a common time axis for uPlot (include segment bounds for steps)
    const tSet = new Set();
    measured.forEach((s) => tSet.add(Number(s.t_s)));
    commanded.forEach((s) => tSet.add(Number(s.t_s)));
    loadSamples.forEach((s) => {
      if (s && s.t_s != null) tSet.add(Number(s.t_s));
    });
    segs.forEach((seg) => {
      if (seg && seg.t0 != null && Number.isFinite(Number(seg.t0))) {
        tSet.add(Number(seg.t0));
      }
      if (seg && seg.t1 != null && Number.isFinite(Number(seg.t1))) {
        tSet.add(Number(seg.t1));
      }
    });
    const ts = [...tSet].sort((a, b) => a - b);
    const mMap = new Map(measured.map((s) => [Number(s.t_s), Number(s.rpm)]));
    const cMap = new Map(commanded.map((s) => [Number(s.t_s), Number(s.rpm)]));
    const lMap = new Map(
      loadSamples
        .filter((s) => s && s.t_s != null && s.load_percent != null)
        .map((s) => [Number(s.t_s), Number(s.load_percent)])
    );
    const mYs = ts.map((t) => (mMap.has(t) ? mMap.get(t) : null));
    const cYs = ts.map((t) => (cMap.has(t) ? cMap.get(t) : null));
    const lYs = ts.map((t) => (lMap.has(t) ? lMap.get(t) : null));
    const fusionYs = fusionRefSeries(ts, segs);
    const fusionVals = fusionYs.filter((v) => v != null && Number(v) > 0);
    const fusionDistinct = [...new Set(fusionVals.map((v) => Math.round(Number(v))))];

    const seriesOpts = [
      {},
      {
        label: "measured",
        stroke: "#3dba8a",
        width: 1.5,
        spanGaps: true,
      },
      {
        label: "commanded",
        stroke: "#7eb8ff",
        width: 1.5,
        spanGaps: true,
        dash: [4, 3],
      },
    ];
    const dataCols = [ts, mYs, cYs];
    const plotOpts = { series: seriesOpts };
    if (fusionVals.length) {
      seriesOpts.push({
        label: "Fusion ref",
        stroke: "#d4a017",
        width: 1.5,
        dash: [2, 4],
        spanGaps: false,
      });
      dataCols.push(fusionYs);
    }
    if (lMap.size) {
      seriesOpts.push({
        label: "load %",
        stroke: "#e07a5f",
        width: 1.5,
        spanGaps: true,
        scale: "load",
      });
      dataCols.push(lYs);
      plotOpts.scales = {
        load: { auto: false, range: [0, 100] },
      };
      plotOpts.axes = [
        {
          stroke: "#8aa094",
          grid: { stroke: "rgba(180,210,190,0.1)" },
          ticks: { stroke: "rgba(180,210,190,0.2)" },
        },
        {
          stroke: "#8aa094",
          grid: { stroke: "rgba(180,210,190,0.1)" },
          ticks: { stroke: "rgba(180,210,190,0.2)" },
        },
        {
          scale: "load",
          stroke: "#e07a5f",
          grid: { show: false },
          ticks: { stroke: "rgba(224,122,95,0.3)" },
          values: (_u, vals) => vals.map((v) => `${Math.round(v)}%`),
        },
      ];
    }

    rpmTelPlot = makePlot(el.chartRpmTel, plotOpts, dataCols);

    if (el.rpmTelMeta) {
      el.rpmTelMeta.textContent = [
        `${measured.length} measured`,
        `${commanded.length} commanded`,
        fusionDistinct.length
          ? `${fusionDistinct.length} Fusion ref step${fusionDistinct.length === 1 ? "" : "s"}`
          : null,
        lMap.size ? `${lMap.size} load` : null,
        meta.rpm_alignment ? `align ${meta.rpm_alignment}` : null,
        aligned === false ? "no aligned measured" : null,
      ]
        .filter(Boolean)
        .join(" · ");
    }
    parts.push(
      `<span class="pill-ok">measured (ArborCTL / ir_0)</span>`,
      `<span class="pill-ref">commanded (RRF / hr_513)</span>`
    );
    if (lMap.size) {
      const latestLoad = meta.latest_load_percent;
      parts.push(
        `<span class="pill-ok">load %${
          latestLoad != null ? ` ${Number(latestLoad).toFixed(1)}` : ""
        }</span>`
      );
    }
    if (delta != null && Number.isFinite(delta)) {
      parts.push(
        `<span class="pill-ref">Δ latest ${delta >= 0 ? "+" : ""}${Math.round(delta)} RPM</span>`
      );
    }
    if (fusionDistinct.length) {
      parts.push(
        `<span class="pill-ref">Fusion/tooling ref (per segment)${
          fusionDistinct.length <= 4
            ? ` · ${fusionDistinct.join(" / ")}`
            : ` · ${fusionDistinct.length} levels`
        }</span>`
      );
    }
    if (aligned === false || (!measured.length && (commanded.length || hasFusion))) {
      parts.push(
        `<span class="pill-warn">no aligned measured RPM telemetry</span>`
      );
    }
    if (el.rpmTelLegend) el.rpmTelLegend.innerHTML = parts.join(" ");
    paintSegmentBands(el.rpmTelOverlay);
    paintEventMarkers(el.rpmTelMarkers);
  }

  function renderTimeSeries() {
    if (seriesPlot) {
      seriesPlot.destroy();
      seriesPlot = null;
    }
    el.chartSeries.innerHTML = "";
    const s = (seriesData && seriesData.series) || {};
    el.seriesMeta.textContent =
      s.n_returned != null ? `${s.n_returned} / ${s.n_total} pts` : "";
    if (s.t_s && s.t_s.length) {
      const mag = s.t_s.map((_, i) =>
        Math.hypot(s.ax_g[i] || 0, s.ay_g[i] || 0, s.az_g[i] || 0)
      );
      seriesPlot = makePlot(
        el.chartSeries,
        {
          series: [
            {},
            { label: "|a|", stroke: "#3dba8a", width: 1.5 },
            { label: "az", stroke: "#7eb8ff", width: 1 },
          ],
        },
        [s.t_s, mag, s.az_g]
      );
    }
    renderSegmentOverlay();
  }

  function stopSessionWs() {
    if (sessionWs) {
      try {
        sessionWs.onclose = null;
        sessionWs.close();
      } catch (_) {
        /* ignore */
      }
      sessionWs = null;
    }
    sessionWsBackoff = 1000;
  }

  function fmtOffsets(offsets) {
    if (!offsets) return "—";
    const axes = offsets.axes || offsets.offsets_mm || offsets;
    if (!axes || typeof axes !== "object" || Array.isArray(axes)) return "—";
    return Object.keys(axes)
      .map((k) => `${k}=${Number(axes[k]).toFixed(3)}`)
      .join(" · ");
  }

  function renderLiveStatus(frame) {
    if (!el.recPill) return;
    const state =
      (frame && (frame.recording_state || (frame.stopped ? "stopped" : null))) ||
      "idle";
    el.recPill.dataset.state = state;
    el.recPill.textContent = state;
    const tn =
      frame && frame.active_tool_number != null
        ? `T${frame.active_tool_number}`
        : "T—";
    const guid =
      frame && frame.active_tool_guid
        ? String(frame.active_tool_guid).slice(0, 8) + "…"
        : "no GUID";
    const name = (frame && frame.active_tool_name) || "—";
    const seg = frame && frame.active_segment;
    const segInfo = seg
      ? `seg ${seg.segment_id || "?"} · ${seg.n_samples || 0} pts · t=${
          seg.t_start != null ? Number(seg.t_start).toFixed(2) : "?"
        }–${seg.t_end != null ? Number(seg.t_end).toFixed(2) : "…"}`
      : frame && frame.active_segment_id
        ? `seg ${frame.active_segment_id}`
        : "no open segment";
    const age =
      frame && frame.sample_age_s != null
        ? ` · last sample ${Number(frame.sample_age_s).toFixed(1)}s ago`
        : "";
    const warn =
      frame && frame.warnings && frame.warnings.length
        ? ` · ⚠ ${frame.warnings[frame.warnings.length - 1]}`
        : "";
    el.liveStatusDetail.textContent = `${tn} · ${name} · Fusion ${guid} · ${segInfo}${age}${warn}`;
    if (el.offsetCard) {
      const off = (frame && frame.active_offsets) || (seg && seg.offsets) || null;
      const hist = (frame && frame.tool_events
        ? frame.tool_events
        : sessionMeta && sessionMeta.tool_events) || [];
      const offsetEv = hist
        .filter(
          (e) =>
            e &&
            (e.event_type === "tool_offset_changed" ||
              e.event_type === "tool_selected" ||
              e.event === "start" ||
              e.event === "change")
        )
        .slice(-6)
        .reverse();
      const probed =
        off && (off.offsets_probed != null || off.probed != null)
          ? off.offsets_probed ?? off.probed
          : null;
      el.offsetCard.innerHTML = `
        <div><span class="label">current</span> ${fmtOffsets(off)}${
          probed != null ? ` · probed=${probed}` : ""
        }</div>
        <ul class="offset-hist">
          ${
            offsetEv.length
              ? offsetEv
                  .map((e) => {
                    const t =
                      e.t_s != null ? Number(e.t_s).toFixed(2) + "s" : "—";
                    const et = e.event_type || e.event || "?";
                    return `<li><span class="mono">${t}</span> ${et} · T${
                      e.tool_number ?? "—"
                    } · ${fmtOffsets(e.offsets || (e.tool_snapshot && e.tool_snapshot.offsets_mm))}</li>`;
                  })
                  .join("")
              : "<li class=\"muted\">No offset/selection events yet</li>"
          }
        </ul>`;
    }
  }

  function applyLiveFrame(frame) {
    if (!frame || frame.error) return;
    liveFrame = frame;
    if (frame.segments) {
      draftSegments = JSON.parse(JSON.stringify(frame.segments));
    }
    if (frame.segment_analysis) {
      segmentAnalysis = frame.segment_analysis;
    }
    if (frame.series && frame.series.t_s) {
      seriesData = {
        session_id: frame.session_id,
        sample_rate_hz: frame.sample_rate_hz,
        series: frame.series,
        segments: frame.segments || draftSegments,
      };
      renderTimeSeries();
    }
    if (frame.spectrum) {
      spectrumData = {
        session_id: frame.session_id,
        sample_rate_hz: frame.sample_rate_hz,
        spectrum: frame.spectrum,
        analysis_by_source: frame.analysis_by_source || {},
        segments: frame.segments || draftSegments,
        segment_analysis: segmentAnalysis,
      };
      renderSpectrumForSelection();
      renderAnalysis(frame.analysis_by_source || {});
    }
    renderLiveStatus(frame);
    renderSegmentTable();
    renderSegmentOverlay();
    if (frame.latest_motion && frame.latest_motion.t_s != null) {
      simScrubT = frame.latest_motion.t_s;
      if (el.simScrubber) el.simScrubber.value = String(simScrubT);
      renderSimView(simScrubT);
    } else if (frame.motion_samples && frame.motion_samples.length) {
      const last = frame.motion_samples[frame.motion_samples.length - 1];
      if (last && last.t_s != null) {
        simScrubT = last.t_s;
        if (el.simScrubber) el.simScrubber.value = String(simScrubT);
        renderSimView(simScrubT);
      }
    }
    if (sessionMeta) {
      sessionMeta.recording_state = frame.recording_state;
      sessionMeta.active_tool_number = frame.active_tool_number;
      sessionMeta.active_tool_name = frame.active_tool_name;
      sessionMeta.active_tool_guid = frame.active_tool_guid;
      sessionMeta.n_samples = frame.n_samples_total || sessionMeta.n_samples;
      sessionMeta.warnings = frame.warnings;
      if (frame.rpm_samples) {
        const existing = sessionMeta.rpm_samples || [];
        const stopped =
          frame.final ||
          frame.stopped ||
          frame.recording_state === "stopped" ||
          frame.recording_state === "idle";
        // Live frames may send a trailing window — never shrink full history.
        if (!stopped || frame.rpm_samples.length >= existing.length) {
          sessionMeta.rpm_samples = frame.rpm_samples;
        }
      }
      if (frame.load_samples) {
        const existing = sessionMeta.load_samples || [];
        const stopped =
          frame.final ||
          frame.stopped ||
          frame.recording_state === "stopped" ||
          frame.recording_state === "idle";
        if (!stopped || frame.load_samples.length >= existing.length) {
          sessionMeta.load_samples = frame.load_samples;
        }
      }
      if (frame.n_load_samples != null) sessionMeta.n_load_samples = frame.n_load_samples;
      if (frame.latest_load_percent != null) {
        sessionMeta.latest_load_percent = frame.latest_load_percent;
      }
      if (frame.latest_watts != null) sessionMeta.latest_watts = frame.latest_watts;
      if (frame.transition_windows) {
        sessionMeta.transition_windows = frame.transition_windows;
      }
      if (frame.n_rpm_samples != null) sessionMeta.n_rpm_samples = frame.n_rpm_samples;
      if (frame.n_modbus_rows != null) sessionMeta.n_modbus_rows = frame.n_modbus_rows;
      if (frame.latest_measured_rpm != null) {
        sessionMeta.latest_measured_rpm = frame.latest_measured_rpm;
      }
      if (frame.latest_commanded_rpm != null) {
        sessionMeta.latest_commanded_rpm = frame.latest_commanded_rpm;
      }
      if (frame.latest_measured_minus_commanded != null) {
        sessionMeta.latest_measured_minus_commanded =
          frame.latest_measured_minus_commanded;
      }
      if (frame.rpm_alignment) sessionMeta.rpm_alignment = frame.rpm_alignment;
      if (frame.has_aligned_measured_rpm != null) {
        sessionMeta.has_aligned_measured_rpm = frame.has_aligned_measured_rpm;
      }
    }
    renderRpmTelemetry();
  }

  function connectSessionWs(id) {
    stopSessionWs();
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}://${location.host}${API}/sessions/${encodeURIComponent(id)}/ws`;
    let ws;
    try {
      ws = new WebSocket(url);
    } catch (e) {
      toast(`WS connect failed: ${e}`, true);
      return;
    }
    sessionWs = ws;
    ws.onopen = () => {
      sessionWsBackoff = 1000;
    };
    ws.onmessage = (ev) => {
      try {
        const frame = JSON.parse(ev.data);
        if (frame.error === "not_found") {
          toast("Session not found on live stream", true);
          stopSessionWs();
          return;
        }
        applyLiveFrame(frame);
        if (frame.final || frame.recording_state === "stopped") {
          // Server closes after final frame; refresh list once.
          loadList().catch(() => {});
        }
      } catch (e) {
        /* ignore bad frames */
      }
    };
    ws.onclose = () => {
      sessionWs = null;
      if (selectedId !== id) return;
      const state =
        (liveFrame && liveFrame.recording_state) ||
        (sessionMeta && sessionMeta.recording_state);
      if (state === "stopped" || state === "idle") return;
      const delay = sessionWsBackoff;
      sessionWsBackoff = Math.min(8000, sessionWsBackoff * 1.6);
      setTimeout(() => {
        if (selectedId === id) connectSessionWs(id);
      }, delay);
    };
    ws.onerror = () => {
      try {
        ws.close();
      } catch (_) {
        /* ignore */
      }
    };
  }

  function motionPointAt(tS) {
    if (!motionData || !motionData.points || !motionData.points.length) return null;
    const pts = motionData.points;
    let lo = 0;
    let hi = pts.length - 1;
    if (tS < pts[0].t_s) return pts[0];
    if (tS >= pts[hi].t_s) return pts[hi];
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (pts[mid].t_s <= tS) lo = mid;
      else hi = mid - 1;
    }
    return pts[lo];
  }

  function motionIndexAt(tS) {
    if (!motionData || !motionData.points || !motionData.points.length) return -1;
    const pts = motionData.points;
    let lo = 0;
    let hi = pts.length - 1;
    if (tS < pts[0].t_s) return 0;
    if (tS >= pts[hi].t_s) return hi;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (pts[mid].t_s <= tS) lo = mid;
      else hi = mid - 1;
    }
    return lo;
  }

  function simSegments() {
    if (draftSegments && draftSegments.length) return draftSegments;
    return (sessionMeta && sessionMeta.segments) || [];
  }

  function segmentForTime(t) {
    const n = Number(t);
    if (!Number.isFinite(n)) return null;
    for (const seg of simSegments()) {
      const t0 = Number(seg.t0);
      const t1 = Number(seg.t1);
      if (!Number.isFinite(t0) || !Number.isFinite(t1)) continue;
      if (n >= t0 && n <= t1) return seg;
    }
    return null;
  }

  function motionToolKey(point) {
    if (!point) return "unknown";
    const seg = segmentForTime(point.t_s);
    if (seg) return toolColorKey(seg);
    if (point.tool_number != null) return `n:${point.tool_number}`;
    return "unknown";
  }

  function hexToThreeColor(hex) {
    const s = String(hex || "#3dba8a").replace("#", "");
    return parseInt(s.length === 6 ? s : "3dba8a", 16);
  }

  function clearSimPathGroup(sim) {
    if (!sim || !sim.pathGroup) return;
    while (sim.pathGroup.children.length) {
      const child = sim.pathGroup.children[0];
      sim.pathGroup.remove(child);
      if (child.geometry) child.geometry.dispose();
      if (child.material) child.material.dispose();
    }
    sim.pathLine = null;
    simPathDrawCount = -1;
  }

  function pathBuildSignature(pts) {
    return `${(pts || []).length}:${pts && pts.length ? pts[0].t_s : ""}:${
      pts && pts.length ? pts[pts.length - 1].t_s : ""
    }:${simSegments()
      .map((s) => `${s.segment_id || ""}:${s.tool_number}:${s.t0}:${s.t1}`)
      .join("|")}`;
  }

  /** Build full colored path once; reveal with setDrawRange during playback. */
  function ensureSimPathBuilt(sim, pts) {
    if (!sim || !pts || !pts.length) {
      clearSimPathGroup(sim);
      simPathSignature = "";
      return;
    }
    const sig = pathBuildSignature(pts);
    if (sig === simPathSignature && sim.pathLine) return;
    simPathSignature = sig;
    clearSimPathGroup(sim);

    const n = pts.length;
    const pos = new Float32Array(n * 3);
    const col = new Float32Array(n * 3);
    const tmp = new THREE.Color();
    for (let i = 0; i < n; i++) {
      const p = pts[i];
      pos[i * 3] = p.x;
      pos[i * 3 + 1] = p.y;
      pos[i * 3 + 2] = p.z;
      tmp.setHex(hexToThreeColor(toolColor(motionToolKey(p))));
      col[i * 3] = tmp.r;
      col[i * 3 + 1] = tmp.g;
      col[i * 3 + 2] = tmp.b;
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
    geo.setDrawRange(0, 0);
    const mat = new THREE.LineBasicMaterial({ vertexColors: true });
    const line = new THREE.Line(geo, mat);
    sim.pathGroup.add(line);
    sim.pathLine = line;
    simPathDrawCount = -1;
  }

  function setSimPathPlayhead(sim, untilT) {
    if (!sim || !sim.pathLine || !motionData || !motionData.points) return;
    const idx = motionIndexAt(untilT);
    const count = idx < 0 ? 0 : idx + 1;
    if (count === simPathDrawCount) return;
    simPathDrawCount = count;
    sim.pathLine.geometry.setDrawRange(0, count);
  }

  function applySimCamera(sim, bbox, force) {
    if (!sim || !bbox) return;
    if (!force && !simCameraDirty) return;
    const cx = (bbox.x_min + bbox.x_max) / 2;
    const cy = (bbox.y_min + bbox.y_max) / 2;
    const cz = (bbox.z_min + bbox.z_max) / 2;
    const span = Math.max(
      bbox.x_max - bbox.x_min,
      bbox.y_max - bbox.y_min,
      bbox.z_max - bbox.z_min,
      20
    );
    sim.target.set(cx, cy, cz);
    sim.radius = span * 2.1;
    sim.theta = Math.PI * 0.25;
    sim.phi = Math.PI * 0.35;
    updateSimCameraFromSpherical(sim);
    sim.bbox = bbox;
    simCameraDirty = false;
  }

  function updateSimCameraFromSpherical(sim) {
    if (!sim) return;
    const r = Math.max(5, sim.radius);
    const phi = Math.min(Math.PI - 0.05, Math.max(0.05, sim.phi));
    sim.phi = phi;
    sim.radius = r;
    const sinPhi = Math.sin(phi);
    sim.camera.position.set(
      sim.target.x + r * sinPhi * Math.cos(sim.theta),
      sim.target.y + r * sinPhi * Math.sin(sim.theta),
      sim.target.z + r * Math.cos(phi)
    );
    sim.camera.lookAt(sim.target);
  }

  function bindSimCameraControls(sim) {
    const canvas = el.simCanvas;
    if (!canvas || sim._controlsBound) return;
    sim._controlsBound = true;
    let dragging = false;
    let lastX = 0;
    let lastY = 0;

    canvas.addEventListener(
      "wheel",
      (ev) => {
        ev.preventDefault();
        const factor = Math.exp(ev.deltaY * 0.0015);
        sim.radius = Math.min(8000, Math.max(8, sim.radius * factor));
        updateSimCameraFromSpherical(sim);
        sim.renderer.render(sim.scene, sim.camera);
      },
      { passive: false }
    );

    canvas.addEventListener("pointerdown", (ev) => {
      if (ev.button !== 0) return;
      dragging = true;
      lastX = ev.clientX;
      lastY = ev.clientY;
      canvas.classList.add("sim-dragging");
      try {
        canvas.setPointerCapture(ev.pointerId);
      } catch (_) {
        /* ignore */
      }
    });
    canvas.addEventListener("pointermove", (ev) => {
      if (!dragging) return;
      const dx = ev.clientX - lastX;
      const dy = ev.clientY - lastY;
      lastX = ev.clientX;
      lastY = ev.clientY;
      sim.theta -= dx * 0.01;
      sim.phi -= dy * 0.01;
      updateSimCameraFromSpherical(sim);
      sim.renderer.render(sim.scene, sim.camera);
    });
    const endDrag = (ev) => {
      if (!dragging) return;
      dragging = false;
      canvas.classList.remove("sim-dragging");
      try {
        canvas.releasePointerCapture(ev.pointerId);
      } catch (_) {
        /* ignore */
      }
    };
    canvas.addEventListener("pointerup", endDrag);
    canvas.addEventListener("pointercancel", endDrag);
  }

  function ensureSimThree() {
    if (simThree || !el.simCanvas || typeof THREE === "undefined") return simThree;
    const canvas = el.simCanvas;
    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    const dpr = Math.min(window.devicePixelRatio || 1, 1.75);
    renderer.setPixelRatio(dpr);
    renderer.setSize(canvas.clientWidth || 640, canvas.clientHeight || 360, false);
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0a0f14);
    const camera = new THREE.PerspectiveCamera(
      45,
      (canvas.clientWidth || 640) / (canvas.clientHeight || 360),
      0.1,
      5000
    );
    camera.position.set(120, -120, 90);
    camera.up.set(0, 0, 1);
    camera.lookAt(0, 0, 0);
    const light = new THREE.DirectionalLight(0xffffff, 0.9);
    light.position.set(1, 1, 2);
    scene.add(light);
    scene.add(new THREE.AmbientLight(0x445566, 0.6));
    const pathGroup = new THREE.Group();
    scene.add(pathGroup);
    const marker = new THREE.Mesh(
      new THREE.SphereGeometry(1.8, 12, 12),
      new THREE.MeshBasicMaterial({ color: 0x7eb8ff })
    );
    scene.add(marker);
    simThree = {
      renderer,
      scene,
      camera,
      pathGroup,
      pathLine: null,
      marker,
      bbox: null,
      target: new THREE.Vector3(0, 0, 0),
      radius: 200,
      theta: Math.PI * 0.25,
      phi: Math.PI * 0.35,
      _controlsBound: false,
    };
    bindSimCameraControls(simThree);
    return simThree;
  }

  function updateSimPlayButton() {
    if (!el.simPlayBtn) return;
    const hasPts = !!(motionData && motionData.points && motionData.points.length);
    el.simPlayBtn.disabled = !hasPts;
    el.simPlayBtn.textContent = simPlaying ? "Pause" : "Play";
    if (el.simSpeed) el.simSpeed.disabled = !hasPts;
  }

  function stopSimPlayback() {
    simPlaying = false;
    simLastTs = 0;
    if (simRaf != null) {
      cancelAnimationFrame(simRaf);
      simRaf = null;
    }
    updateSimPlayButton();
  }

  function simTimeBounds() {
    const t0 = motionData && motionData.t_start != null ? Number(motionData.t_start) : 0;
    const t1 =
      motionData && motionData.t_end != null ? Number(motionData.t_end) : t0 + 1;
    return { t0, t1: Math.max(t1, t0 + 0.01) };
  }

  function updateSimDom(playhead, p, force) {
    const now = performance.now();
    if (!force && now - simDomLastMs < 50) return;
    simDomLastMs = now;
    if (el.simScrubber) el.simScrubber.value = String(playhead);
    if (p && el.simScrubLabel) {
      el.simScrubLabel.textContent = `X ${p.x.toFixed(2)} Y ${p.y.toFixed(2)} Z ${p.z.toFixed(
        2
      )} @ ${p.t_s.toFixed(2)} s`;
    }
    if (p && el.simAxisA) {
      el.simAxisA.textContent =
        p.a != null ? `A ${Number(p.a).toFixed(1)}°` : motionData.has_a ? "A —" : "";
    }
  }

  function simPlaybackTick(now) {
    if (!simPlaying || !motionData || !motionData.points || !motionData.points.length) {
      stopSimPlayback();
      return;
    }
    if (!simLastTs) simLastTs = now;
    const dt = Math.min(0.05, (now - simLastTs) / 1000);
    simLastTs = now;
    const { t1 } = simTimeBounds();
    simScrubT = Math.min(t1, simScrubT + dt * simSpeed);
    renderSimView(simScrubT, { playing: true });
    if (simScrubT >= t1) {
      updateSimDom(simScrubT, motionPointAt(simScrubT), true);
      stopSimPlayback();
      return;
    }
    simRaf = requestAnimationFrame(simPlaybackTick);
  }

  function startSimPlayback() {
    if (!motionData || !motionData.points || !motionData.points.length) return;
    const { t0, t1 } = simTimeBounds();
    if (simScrubT >= t1 - 1e-6) simScrubT = t0;
    simPlaying = true;
    simLastTs = 0;
    renderSimView(simScrubT, { forceDom: true });
    updateSimPlayButton();
    simRaf = requestAnimationFrame(simPlaybackTick);
  }

  function toggleSimPlayback() {
    if (simPlaying) stopSimPlayback();
    else startSimPlayback();
  }

  function resetSimView() {
    simCameraDirty = true;
    if (simThree && motionData && motionData.bbox) {
      applySimCamera(simThree, motionData.bbox, true);
      simThree.renderer.render(simThree.scene, simThree.camera);
    } else if (motionData) {
      renderSimView(simScrubT, { forceDom: true });
    }
  }

  function renderSimView(tS, opts) {
    const options = opts || {};
    const sim = ensureSimThree();
    if (!sim || !motionData || !motionData.points || !motionData.points.length) {
      updateSimPlayButton();
      return;
    }
    const pts = motionData.points;
    const playhead = typeof tS === "number" && Number.isFinite(tS) ? tS : simScrubT;
    ensureSimPathBuilt(sim, pts);
    setSimPathPlayhead(sim, playhead);
    if (motionData.bbox) applySimCamera(sim, motionData.bbox, false);
    const p = motionPointAt(playhead);
    if (p) {
      sim.marker.position.set(p.x, p.y, p.z);
      const key = motionToolKey(p);
      const col = hexToThreeColor(toolColor(key));
      if (sim.marker.material) sim.marker.material.color.setHex(col);
    }
    updateSimDom(playhead, p, !!options.forceDom || !options.playing);
    sim.renderer.render(sim.scene, sim.camera);
    if (!options.playing) updateSimPlayButton();
  }

  function configureSimScrubber() {
    if (!el.simScrubber || !motionData) return;
    const { t0, t1 } = simTimeBounds();
    el.simScrubber.min = String(t0);
    el.simScrubber.max = String(t1);
    el.simScrubber.step = String(Math.max(0.01, (t1 - t0) / 500));
    el.simScrubber.disabled = !motionData.points || !motionData.points.length;
    el.simScrubber.value = String(simScrubT || t0);
    updateSimPlayButton();
  }

  async function loadMotion(id) {
    stopSimPlayback();
    motionData = null;
    simScrubT = 0;
    simPathSignature = "";
    simCameraDirty = true;
    if (el.simMeta) el.simMeta.textContent = "Loading motion…";
    const r = await fetch(`${API}/sessions/${encodeURIComponent(id)}/motion?max_points=4000`);
    if (!r.ok) {
      if (el.simMeta) el.simMeta.textContent = "No motion samples yet (tap/…/motion).";
      configureSimScrubber();
      renderSimView(0);
      return;
    }
    motionData = await r.json();
    if (el.simMeta) {
      el.simMeta.textContent = `${motionData.n_samples || 0} samples · sources: ${
        (motionData.sources || []).join(", ") || "—"
      }`;
    }
    simScrubT = motionData.t_end != null ? Number(motionData.t_end) : motionData.t_start || 0;
    configureSimScrubber();
    renderSimView(simScrubT);
  }

  async function replayFromLog() {
    if (!selectedId) return;
    if (el.simReplayBtn) el.simReplayBtn.disabled = true;
    try {
      const r = await fetch(`${API}/sessions/${encodeURIComponent(selectedId)}/replay`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ include_batches: false }),
      });
      const j = await r.json();
      if (!r.ok) {
        toast(j.detail || j.error || "Replay failed", true);
        return;
      }
      toast(`Replay +${j.motion_samples_added || 0} motion samples`);
      await loadMotion(selectedId);
    } catch (e) {
      toast(String(e), true);
    } finally {
      if (el.simReplayBtn) el.simReplayBtn.disabled = false;
    }
  }

  async function selectSession(id) {
    selectedId = id;
    selectedGuid = null;
    liveFrame = null;
    stopSimPlayback();
    simPathSignature = "";
    simCameraDirty = true;
    stopSessionWs();
    showSessionView();
    destroyPlots();
    await Promise.all([loadList(), loadTools(el.libSearch.value.trim() || undefined)]);

    const [metaR, seriesR, specR, segR, motionR] = await Promise.all([
      fetch(`${API}/sessions/${encodeURIComponent(id)}`),
      fetch(`${API}/sessions/${encodeURIComponent(id)}/series?max_points=2500`),
      fetch(`${API}/sessions/${encodeURIComponent(id)}/spectrum?max_bins=256`),
      fetch(`${API}/sessions/${encodeURIComponent(id)}/segments`),
      fetch(`${API}/sessions/${encodeURIComponent(id)}/motion?max_points=4000`),
    ]);
    if (!metaR.ok) {
      toast("Session not found", true);
      return;
    }
    sessionMeta = await metaR.json();
    seriesData = await seriesR.json();
    spectrumData = await specR.json();
    const segPayload = segR.ok ? await segR.json() : {};
    draftSegments = JSON.parse(
      JSON.stringify(segPayload.segments || sessionMeta.segments || [])
    );
    segmentAnalysis = segPayload.segment_analysis || sessionMeta.segment_analysis || [];
    if (motionR.ok) {
      motionData = await motionR.json();
      simScrubT = motionData.t_end != null ? Number(motionData.t_end) : motionData.t_start || 0;
      if (el.simMeta) {
        el.simMeta.textContent = `${motionData.n_samples || 0} samples · sources: ${
          (motionData.sources || []).join(", ") || "—"
        }`;
      }
      configureSimScrubber();
      renderSimView(simScrubT);
    } else {
      motionData = null;
      if (el.simMeta) el.simMeta.textContent = "No motion samples yet (tap/…/motion).";
      configureSimScrubber();
      updateSimPlayButton();
    }
    if (sessionMeta.t_start == null && segPayload.t_start != null) {
      sessionMeta.t_start = segPayload.t_start;
      sessionMeta.t_end = segPayload.t_end;
    }

    el.title.textContent = sessionMeta.session_id;
    el.sub.textContent = [
      sessionMeta.mode,
      sessionMeta.origin,
      sessionMeta.job_file,
      sessionMeta.import_label,
      sessionMeta.sample_rate_hz ? `${sessionMeta.sample_rate_hz} Hz` : null,
      sessionMeta.n_samples ? `${sessionMeta.n_samples} samples` : null,
      sessionMeta.n_tool_events ? `${sessionMeta.n_tool_events} tool events` : null,
      draftSegments.length ? `${draftSegments.length} segments` : null,
      sessionMeta.n_rpm_samples ? `${sessionMeta.n_rpm_samples} RPM samples` : null,
    ]
      .filter(Boolean)
      .join(" · ");
    if (el.sessNotes) {
      el.sessNotes.value = sessionMeta.notes || "";
    }

    renderLiveStatus(sessionMeta);
    renderAnalysis(sessionMeta.analysis_by_source || spectrumData.analysis_by_source);
    renderSegmentTable();
    renderTimeSeries();
    renderSpectrumForSelection();
    renderRpmDomain();
    renderRpmTelemetry();
    // Live recording stream for MQTT sessions (distinct from MQTT ring-buffer Live).
    connectSessionWs(id);
  }

  async function selectTool(guid) {
    // If a session is open, apply this library tool to a segment (mapping mode)
    // instead of switching away to the tool detail panel.
    if (selectedId) {
      applyToolToSession(guid);
      return;
    }
    selectedGuid = guid;
    selectedId = null;
    showToolView();
    destroyPlots();
    await loadTools(el.libSearch.value.trim() || undefined);
    await loadList();

    const r = await fetch(`${API}/tools/${encodeURIComponent(guid)}`);
    if (!r.ok) {
      toast("Tool not found", true);
      return;
    }
    const t = await r.json();
    el.toolTitle.textContent = t.description || t.product_id || t.guid;
    el.toolSub.textContent = [
      t.tool_number != null ? `T${t.tool_number}` : null,
      t.vendor,
      t.type,
      t.guid,
    ]
      .filter(Boolean)
      .join(" · ");
    const diam = t.diameter != null ? Number(t.diameter).toFixed(4) : "—";
    const ref =
      t.reference_rpm != null ? Math.round(t.reference_rpm) : "—";
    el.toolCard.innerHTML = `
      <div><span class="label">⌀</span> <span class="fn">${diam}</span> ${t.unit || ""}</div>
      <div><span class="label">flutes</span> ${t.flutes ?? "—"}</div>
      <div><span class="label">ref RPM</span> ${ref} <span class="label">(est.)</span></div>
      <div><span class="label">material</span> ${t.bmc || "—"}</div>
      <div><span class="label">holder</span> ${t.holder || "—"}</div>
      <div><span class="label">product</span> ${t.product_id || "—"}</div>
    `;
    el.toolRaw.textContent = JSON.stringify(t.fusion || t, null, 2);
  }

  function applyToolToSession(guid) {
    const t = findTool(guid);
    if (!t) {
      toast("Tool not in library list — refresh tools", true);
      return;
    }
    selectedGuid = guid;
    let seg = draftSegments.find((s) => !s.tool_guid);
    if (!seg) {
      // Create a segment covering the full recording (or remaining span)
      const t0 =
        sessionMeta && sessionMeta.t_start != null ? Number(sessionMeta.t_start) : 0;
      const t1 =
        sessionMeta && sessionMeta.t_end != null
          ? Number(sessionMeta.t_end)
          : t0 + 10;
      let start = t0;
      if (draftSegments.length) {
        start = Math.max(...draftSegments.map((s) => Number(s.t1)));
      }
      const end = start < t1 ? t1 : start + 1;
      if (end <= start) {
        toast("No time left for a new segment — edit existing slice times", true);
        return;
      }
      seg = {
        segment_id: newSegId(),
        t0: start,
        t1: end,
        tool_number: null,
        tool_guid: null,
        flutes: null,
        reference_rpm: null,
        reference_rpm_source: null,
        manual: true,
        ambiguous_tools: [],
      };
      draftSegments.push(seg);
    }
    seg.tool_guid = t.guid;
    seg.tool_number = t.tool_number != null ? t.tool_number : seg.tool_number;
    seg.tool_name = t.description || t.product_id || t.guid;
    if (t.flutes != null) seg.flutes = t.flutes;
    if (t.reference_rpm != null) {
      seg.reference_rpm = t.reference_rpm;
      seg.reference_rpm_source = "fusion_reference";
    }
    seg.ambiguous_tools = [];
    seg.manual = true;
    simPathSignature = "";
    renderSegmentTable();
    if (motionData) renderSimView(simScrubT);
    loadTools(el.libSearch.value.trim() || undefined);
    toast(
      `Mapped ${toolLabel(t)} → segment ${Number(seg.t0).toFixed(2)}–${Number(seg.t1).toFixed(2)} s — click Save & reanalyze`
    );
  }

  async function importFile(file) {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("mode", "live_spindle");
    fd.append("device_id", "import");
    toast(`Importing ${file.name}…`);
    const r = await fetch(`${API}/sessions/import`, { method: "POST", body: fd });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) {
      toast(j.detail || j.error || "Import failed", true);
      return;
    }
    toast(`Imported ${j.session_id}${j.n_segments ? ` · ${j.n_segments} segments` : ""}`);
    await loadList();
    if (j.session_id) await selectSession(j.session_id);
  }

  async function importLibrary(file) {
    const fd = new FormData();
    fd.append("file", file);
    const replace = !!(el.libReplace && el.libReplace.checked);
    if (replace) fd.append("replace", "true");
    toast(`Importing tool library ${file.name}${replace ? " (replace)" : ""}…`);
    const r = await fetch(`${API}/tools/import`, { method: "POST", body: fd });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) {
      toast(j.detail || j.error || "Library import failed", true);
      return;
    }
    const rem =
      j.removed || j.replaced_duplicates
        ? ` · removed ${j.removed || 0} (dupes ${j.replaced_duplicates || 0})`
        : "";
    toast(
      `Library (${j.mode || "upsert"}): +${j.created} new, ${j.updated} updated${rem} (${j.n_tools} total)`
    );
    await loadTools();
    checkHealth();
  }

  async function saveSessionNotes() {
    if (!selectedId) {
      toast("Select a session first", true);
      return;
    }
    const notes = el.sessNotes ? el.sessNotes.value : "";
    const r = await fetch(`${API}/sessions/${encodeURIComponent(selectedId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notes }),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) {
      toast(j.detail || j.error || "Save notes failed", true);
      return;
    }
    sessionMeta = j;
    toast("Notes saved");
  }

  async function forceStopSession() {
    if (!selectedId) return;
    const r = await fetch(
      `${API}/sessions/${encodeURIComponent(selectedId)}/stop`,
      { method: "POST" }
    );
    const j = await r.json().catch(() => ({}));
    if (!r.ok) {
      toast(j.detail || j.error || "Force stop failed", true);
      return;
    }
    toast(j.already_stopped ? "Already stopped" : "Session force-stopped");
    await selectSession(selectedId);
  }

  async function deleteSessionById(id, { active = false } = {}) {
    if (!id) return;
    const msg = active
      ? `Session ${id} looks active. Force-stop and permanently delete JSON/NPZ?`
      : `Permanently delete session ${id} (JSON + NPZ)?`;
    if (!window.confirm(msg)) return;
    const r = await fetch(
      `${API}/sessions/${encodeURIComponent(id)}?force=true`,
      { method: "DELETE" }
    );
    const j = await r.json().catch(() => ({}));
    if (!r.ok) {
      toast(j.detail || j.error || "Delete failed", true);
      return;
    }
    if (selectedId === id) {
      selectedId = null;
      sessionMeta = null;
      liveFrame = null;
      stopSessionWs();
      destroyPlots();
      el.panel.classList.add("hidden");
      el.empty.classList.remove("hidden");
    }
    toast(`Deleted ${id}`);
    await loadList();
    checkHealth();
  }

  async function deleteSession() {
    if (!selectedId) return;
    const rec =
      sessionMeta &&
      !sessionMeta.stopped &&
      sessionMeta.recording_state !== "stopped" &&
      sessionMeta.recording_state !== "idle";
    await deleteSessionById(selectedId, { active: !!rec });
  }

  async function deleteSelectedTool() {
    if (!selectedGuid) return;
    const guid = selectedGuid;
    if (!window.confirm(`Delete tool ${guid} from the library?`)) return;
    const r = await fetch(`${API}/tools/${encodeURIComponent(guid)}`, {
      method: "DELETE",
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) {
      toast(j.detail || j.error || "Delete tool failed", true);
      return;
    }
    selectedGuid = null;
    el.toolPanel.classList.add("hidden");
    el.empty.classList.remove("hidden");
    toast("Tool deleted");
    await loadTools();
  }

  function addSegment() {
    const t0 =
      sessionMeta && sessionMeta.t_start != null ? Number(sessionMeta.t_start) : 0;
    const t1 =
      sessionMeta && sessionMeta.t_end != null
        ? Number(sessionMeta.t_end)
        : t0 + 10;
    let start = t0;
    if (draftSegments.length) {
      start = Math.max(...draftSegments.map((s) => Number(s.t1)));
    }
    const end = Math.min(t1, start + Math.max(1, (t1 - t0) / 4));
    if (end <= start) {
      toast("No room for another segment — adjust times first", true);
      return;
    }
    draftSegments.push({
      segment_id: newSegId(),
      t0: start,
      t1: end,
      tool_number: null,
      tool_guid: null,
      flutes: null,
      reference_rpm: null,
      reference_rpm_source: null,
      manual: true,
      ambiguous_tools: [],
    });
    renderSegmentTable();
  }

  async function saveSegments() {
    if (!selectedId) {
      toast("Select a session first", true);
      return;
    }
    if (!draftSegments.length) {
      toast("Add at least one segment before saving", true);
      return;
    }
    for (const s of draftSegments) {
      if (s.t1 == null || s.t0 == null || Number(s.t1) <= Number(s.t0)) {
        toast("Each segment needs t₁ > t₀", true);
        return;
      }
    }
    const payload = {
      reanalyze: true,
      segments: draftSegments.map((s) => ({
        segment_id: s.segment_id,
        t0: Number(s.t0),
        t1: Number(s.t1),
        tool_number: s.tool_number,
        tool_guid: s.tool_guid || null,
        tool_name: s.tool_name || null,
        flutes: s.flutes,
        reference_rpm: s.reference_rpm,
        reference_rpm_source: s.reference_rpm_source || (s.reference_rpm != null ? "manual" : null),
        material: s.material || (el.forceMaterial && el.forceMaterial.value) || "6061 aluminum",
        note: s.note || null,
        manual: true,
      })),
    };
    el.saveSeg.disabled = true;
    toast("Saving segments…");
    try {
      const r = await fetch(
        `${API}/sessions/${encodeURIComponent(selectedId)}/segments`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        }
      );
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        toast(j.detail || j.error || "Save failed", true);
        return;
      }
      toast("Segments saved · analytics updated");
      await selectSession(selectedId);
    } finally {
      if (el.saveSeg) el.saveSeg.disabled = !selectedId;
    }
  }

  function isWallClockUnix(n) {
    const v = Number(n);
    return Number.isFinite(v) && v >= 1e9;
  }

  function fmtWallClock(ts) {
    const raw = ts != null && ts.received_at != null ? ts.received_at : ts;
    const n = Number(raw);
    if (!isWallClockUnix(n)) return "—";
    const ms = n >= 1e12 ? n : n * 1000;
    const d = new Date(ms);
    if (Number.isNaN(d.getTime())) return "—";
    return d.toLocaleString(undefined, {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  }

  function fmtRecordingSeconds(v) {
    const n = Number(v);
    if (!Number.isFinite(n)) return null;
    return `${n.toFixed(n >= 100 ? 2 : 3)} s`;
  }

  function mqttPreview(ev) {
    const p = ev.payload;
    if (p == null) return ev.raw_preview || "";
    if (typeof p === "string") return p.slice(0, 160);
    const bits = [];
    for (const k of ["event", "session_id", "tool_number", "mode", "job_file", "source", "fn_hz", "seq"]) {
      if (p[k] != null) bits.push(`${k}=${p[k]}`);
    }
    if (p.t_s != null) bits.push(`t_s=${fmtRecordingSeconds(p.t_s)}`);
    else if (p.t0_s != null) bits.push(`t0_s=${fmtRecordingSeconds(p.t0_s)}`);
    if (!bits.length) {
      try {
        return JSON.stringify(p).slice(0, 160);
      } catch {
        return String(p).slice(0, 160);
      }
    }
    return bits.join(" · ");
  }

  function renderMqttDetail(ev) {
    if (!el.mqttDetail) return;
    const p = ev.payload;
    const received = fmtWallClock(ev.received_at != null ? ev.received_at : ev.ts);
    const rows = [
      ["Received", received],
      ["Topic", ev.topic || "—"],
      ["Id", ev.id != null ? String(ev.id) : "—"],
      ["Kind", ev.payload_kind || "—"],
      ["Bytes", ev.bytes != null ? String(ev.bytes) : "—"],
    ];
    if (p && typeof p === "object" && !Array.isArray(p)) {
      if (p.t_s != null) rows.push(["Recording t_s", fmtRecordingSeconds(p.t_s)]);
      if (p.t0_s != null) rows.push(["Batch t0_s", fmtRecordingSeconds(p.t0_s)]);
      if (p.dt_s != null) rows.push(["Batch dt_s", fmtRecordingSeconds(p.dt_s)]);
      if (isWallClockUnix(p.ts)) rows.push(["Published", fmtWallClock(p.ts)]);
      else if (p.ts != null && !isWallClockUnix(p.ts))
        rows.push(["Payload ts", `${fmtRecordingSeconds(p.ts)} (recording-relative)`]);
    }
    const preview = mqttPreview(ev);
    const dl = rows
      .map(
        ([label, value]) =>
          `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value != null ? String(value) : "—")}</dd>`
      )
      .join("");
    let rawJson = "";
    try {
      rawJson = JSON.stringify(ev, null, 2);
    } catch {
      rawJson = String(ev);
    }
    el.mqttDetail.innerHTML = `
      <dl>${dl}</dl>
      ${preview ? `<p class="hint" style="margin:0 0 0.5rem">${escapeHtml(preview)}</p>` : ""}
      <details>
        <summary>Raw JSON</summary>
        <pre>${escapeHtml(rawJson)}</pre>
      </details>`;
    el.mqttDetail.classList.remove("hidden");
  }

  function renderMqttList(events, { append }) {
    if (!append) el.mqttList.innerHTML = "";
    if (!events.length && !append) {
      el.mqttList.innerHTML = `<p class="hint" style="margin:0">No matching events. Publish MQTT traffic or widen the query.</p>`;
      return;
    }
    const frag = document.createDocumentFragment();
    for (const ev of events) {
      if (append && el.mqttList.querySelector(`[data-id="${ev.id}"]`)) continue;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "mqtt-row";
      btn.dataset.id = String(ev.id);
      if (ev.id === mqttSelectedId) btn.classList.add("is-active");
      const ts = fmtWallClock(ev.received_at != null ? ev.received_at : ev.ts);
      btn.innerHTML = `<div class="mqtt-top"><span class="mqtt-id">#${ev.id}</span><span class="mqtt-topic">${escapeHtml(ev.topic || "")}</span><span class="mqtt-id">${escapeHtml(ts)}</span></div><div class="mqtt-preview">${escapeHtml(mqttPreview(ev))}</div>`;
      btn.addEventListener("click", () => {
        mqttSelectedId = ev.id;
        el.mqttList.querySelectorAll(".mqtt-row").forEach((n) => n.classList.remove("is-active"));
        btn.classList.add("is-active");
        renderMqttDetail(ev);
      });
      if (append) el.mqttList.prepend(btn);
      else frag.appendChild(btn);
    }
    if (!append) el.mqttList.appendChild(frag);
  }

  async function queryMqttEvents({ append = false, sinceId = null } = {}) {
    const params = new URLSearchParams();
    const topic = (el.mqttTopic.value || "").trim();
    const where = (el.mqttWhere.value || "").trim();
    const q = (el.mqttQ.value || "").trim();
    if (topic) params.set("topic", topic);
    if (where) params.set("where_csv", where);
    if (q) params.set("q", q);
    params.set("limit", append ? "50" : "150");
    params.set("order", "desc");
    if (sinceId != null) params.set("since_id", String(sinceId));
    const r = await fetch(`${API}/mqtt/events?${params}`);
    const j = await r.json();
    if (!r.ok) {
      toast(j.detail || j.error || "MQTT query failed", true);
      return;
    }
    el.mqttStats.innerHTML = `
      <div><span class="label">buffer</span> ${j.n_total} / ${j.max_events}</div>
      <div><span class="label">matched</span> ${j.n_returned}</div>
      <div><span class="label">next id</span> ${j.next_id}</div>
    `;
    el.mqttSub.textContent = [
      topic ? `topic~${topic}` : null,
      where || null,
      q ? `q=${q}` : null,
      `${j.n_returned} shown`,
    ]
      .filter(Boolean)
      .join(" · ");
    const events = j.events || [];
    renderMqttList(events, { append });
    if (events.length) {
      mqttSinceId = Math.max(mqttSinceId || 0, ...events.map((e) => e.id));
    } else if (!append) {
      mqttSinceId = j.next_id || mqttSinceId;
    }
    return j;
  }

  function toggleMqttLive() {
    if (el.mqttLiveBtn.dataset.live === "1") {
      stopMqttLive();
      return;
    }
    el.mqttLiveBtn.dataset.live = "1";
    el.mqttLiveBtn.textContent = "Live on";
    mqttLiveTimer = setInterval(() => {
      queryMqttEvents({ append: true, sinceId: mqttSinceId }).catch(() => {});
    }, 1500);
  }

  async function clearMqttBuffer() {
    stopMqttLive();
    if (
      !window.confirm(
        "Clear the MQTT event stream?\nThis deletes the in-memory window and persisted day files (within retention)."
      )
    ) {
      return;
    }
    const r = await fetch(`${API}/mqtt/events`, { method: "DELETE" });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) {
      toast(j.detail || j.error || "Clear failed", true);
      return;
    }
    mqttSinceId = null;
    mqttSelectedId = null;
    if (el.mqttDetail) el.mqttDetail.classList.add("hidden");
    if (el.mqttList) {
      el.mqttList.innerHTML =
        '<p class="hint" style="margin:0">Stream cleared.</p>';
    }
    toast(
      `MQTT stream cleared (next id ${j.next_id ?? "—"}; persisted ${j.n_persisted ?? 0})`
    );
    await queryMqttEvents({ append: false });
    checkHealth();
  }

  el.file.addEventListener("change", () => {
    const f = el.file.files && el.file.files[0];
    el.file.value = "";
    if (f) importFile(f);
  });
  el.libFile.addEventListener("change", () => {
    const f = el.libFile.files && el.libFile.files[0];
    el.libFile.value = "";
    if (f) importLibrary(f);
  });
  el.refresh.addEventListener("click", () => loadList().catch((e) => toast(String(e), true)));
  el.libRefresh.addEventListener("click", () =>
    loadTools(el.libSearch.value.trim() || undefined).catch((e) => toast(String(e), true))
  );
  el.libSearch.addEventListener("input", () => {
    clearTimeout(libSearchTimer);
    libSearchTimer = setTimeout(() => {
      loadTools(el.libSearch.value.trim() || undefined).catch((e) => toast(String(e), true));
    }, 200);
  });
  el.addSeg.addEventListener("click", addSegment);
  el.saveSeg.addEventListener("click", () => saveSegments().catch((e) => toast(String(e), true)));
  if (el.saveNotes) {
    el.saveNotes.addEventListener("click", () =>
      saveSessionNotes().catch((e) => toast(String(e), true))
    );
  }
  if (el.stopSess) {
    el.stopSess.addEventListener("click", () =>
      forceStopSession().catch((e) => toast(String(e), true))
    );
  }
  if (el.deleteSess) {
    el.deleteSess.addEventListener("click", () =>
      deleteSession().catch((e) => toast(String(e), true))
    );
  }
  if (el.deleteTool) {
    el.deleteTool.addEventListener("click", () =>
      deleteSelectedTool().catch((e) => toast(String(e), true))
    );
  }
  el.spectrumSegSelect.addEventListener("change", () => {
    renderSpectrumForSelection();
    renderRpmDomain();
  });
  if (el.forceMaterial) {
    el.forceMaterial.addEventListener("change", () => {
      const mat = el.forceMaterial.value;
      draftSegments.forEach((s) => {
        s.material = mat;
      });
      toast("Material set — Save & reanalyze to refresh cutting load");
    });
  }
  if (el.mqttStreamBtn) {
    el.mqttStreamBtn.addEventListener("click", () => showMqttView());
  }
  if (el.mqttSearchBtn) {
    el.mqttSearchBtn.addEventListener("click", () =>
      queryMqttEvents({ append: false }).catch((e) => toast(String(e), true))
    );
  }
  if (el.mqttLiveBtn) {
    el.mqttLiveBtn.addEventListener("click", toggleMqttLive);
  }
  if (el.mqttClearBtn) {
    el.mqttClearBtn.addEventListener("click", () =>
      clearMqttBuffer().catch((e) => toast(String(e), true))
    );
  }
  ["mqttWhere", "mqttTopic", "mqttQ"].forEach((key) => {
    const node = el[key];
    if (!node) return;
    node.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        queryMqttEvents({ append: false }).catch((e) => toast(String(e), true));
      }
    });
  });

  if (el.simScrubber) {
    el.simScrubber.addEventListener("input", () => {
      stopSimPlayback();
      simScrubT = parseFloat(el.simScrubber.value) || 0;
      renderSimView(simScrubT);
    });
  }
  if (el.simPlayBtn) {
    el.simPlayBtn.addEventListener("click", () => toggleSimPlayback());
  }
  if (el.simSpeed) {
    el.simSpeed.addEventListener("change", () => {
      const v = parseFloat(el.simSpeed.value);
      simSpeed = Number.isFinite(v) && v > 0 ? v : 1;
    });
  }
  if (el.simReplayBtn) {
    el.simReplayBtn.addEventListener("click", () =>
      replayFromLog().catch((e) => toast(String(e), true))
    );
  }
  if (el.simResetViewBtn) {
    el.simResetViewBtn.addEventListener("click", () => resetSimView());
  }

  window.addEventListener("resize", () => {
    if (selectedId) {
      renderTimeSeries();
      renderSpectrumForSelection();
      renderRpmDomain();
      if (simThree && el.simCanvas) {
        const canvas = el.simCanvas;
        const w = canvas.clientWidth || 640;
        const h = Math.max(180, canvas.clientHeight || 360);
        simThree.camera.aspect = w / h;
        simThree.camera.updateProjectionMatrix();
        simThree.renderer.setSize(w, h, false);
        renderSimView(simScrubT);
      }
    }
  });

  checkHealth();
  loadList().catch((e) => toast(String(e), true));
  loadTools().catch((e) => toast(String(e), true));
  setInterval(checkHealth, 15000);
  // Refresh session list so recording badges update without opening a session.
  sessionListTimer = setInterval(() => {
    loadList().catch(() => {});
  }, 5000);
})();
