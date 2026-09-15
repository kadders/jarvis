(() => {
  const shell = document.getElementById("shell");
  const threadEl = document.getElementById("thread");
  const composer = document.getElementById("composer");
  const promptEl = document.getElementById("prompt");
  const sendBtn = document.getElementById("send-btn");
  const clearBtn = document.getElementById("clear-btn");
  const learnRefreshBtn = document.getElementById("learn-refresh-btn");
  const connStatus = document.getElementById("conn-status");
  const tagline = document.getElementById("tagline");
  const tierGroup = document.getElementById("tier-group");
  const tierBtns = document.querySelectorAll(".tier-btn");
  const navBtns = document.querySelectorAll(".nav-btn");
  const viewChat = document.getElementById("view-chat");
  const viewLearning = document.getElementById("view-learning");
  const learnSummary = document.getElementById("learn-summary");
  const learnDays = document.getElementById("learn-days");
  const learnList = document.getElementById("learn-list");
  const learnDetail = document.getElementById("learn-detail");
  const learnSlice = document.getElementById("learn-slice");
  const learnDomain = document.getElementById("learn-domain");
  const learnRating = document.getElementById("learn-rating");
  const learnSource = document.getElementById("learn-source");
  const learnQ = document.getElementById("learn-q");

  /** @type {{ role: "user" | "assistant", content: string, meta?: object, event_id?: string, error?: boolean, feedback?: number }[]} */
  let history = [];
  let modelTier = "default";
  let busy = false;
  let currentView = "chat";
  let selectedFeedbackId = null;
  let learnQTimer = null;

  function setTier(tier) {
    modelTier = tier;
    tierBtns.forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.tier === tier);
    });
  }

  tierBtns.forEach((btn) => {
    btn.addEventListener("click", () => setTier(btn.dataset.tier));
  });

  function setConnState(state, label) {
    connStatus.dataset.state = state;
    connStatus.querySelector(".status-label").textContent = label;
  }

  async function pollHealth() {
    try {
      const res = await fetch("/api/health");
      const data = await res.json();
      if (data.llm === "ok") {
        setConnState("ok", "Connected");
      } else {
        setConnState("down", "LLM down");
      }
    } catch {
      setConnState("down", "UI error");
    }
  }

  function setView(view) {
    currentView = view;
    navBtns.forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.view === view);
    });
    viewChat.classList.toggle("hidden", view !== "chat");
    viewLearning.classList.toggle("hidden", view !== "learning");
    clearBtn.classList.toggle("hidden", view !== "chat");
    learnRefreshBtn.classList.toggle("hidden", view !== "learning");
    tierGroup.classList.toggle("hidden", view !== "chat");
    shell.classList.toggle("is-learning", view === "learning");
    tagline.textContent =
      view === "learning"
        ? "Correctness from MCP / UI feedback"
        : "Talk to your local models";
    if (view === "learning") {
      loadLearning().catch((e) => {
        learnSummary.textContent = String(e.message || e);
      });
    } else {
      promptEl.focus();
    }
    if (location.hash.replace(/^#/, "") !== view) {
      window.history.replaceState(null, "", `#${view}`);
    }
  }

  navBtns.forEach((btn) => {
    btn.addEventListener("click", () => setView(btn.dataset.view));
  });

  async function sendFeedback(turnIndex, rating, correction) {
    const turn = history[turnIndex];
    if (!turn || turn.role !== "assistant" || !turn.event_id) return;
    const body = { event_id: turn.event_id, rating };
    if (correction) body.correction = correction;
    const res = await fetch("/api/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(
        typeof data.detail === "string"
          ? data.detail
          : `Feedback failed (${res.status})`
      );
    }
    turn.feedback = rating;
    renderThread();
  }

  function renderThread() {
    threadEl.innerHTML = "";
    history.forEach((turn, index) => {
      const article = document.createElement("article");
      article.className =
        "msg " + (turn.role === "user" ? "msg-user" : "msg-assistant");
      if (turn.error) article.classList.add("msg-error");

      const role = document.createElement("p");
      role.className = "msg-role";
      role.textContent = turn.role === "user" ? "You" : "Jarvis";

      const body = document.createElement("p");
      body.className = "msg-body";
      body.textContent = turn.content;

      article.appendChild(role);
      article.appendChild(body);

      if (turn.meta) {
        const meta = document.createElement("p");
        meta.className = "msg-meta";
        const parts = [];
        if (turn.meta.tier) parts.push(`tier ${turn.meta.tier}`);
        if (turn.meta.model) parts.push(turn.meta.model);
        if (turn.meta.timing_ms != null) parts.push(`${turn.meta.timing_ms} ms`);
        meta.textContent = parts.join(" · ");
        article.appendChild(meta);
      }

      if (turn.role === "assistant" && turn.event_id && !turn.error) {
        const actions = document.createElement("div");
        actions.className = "msg-actions";

        const up = document.createElement("button");
        up.type = "button";
        up.className = "fb-btn" + (turn.feedback === 1 ? " is-active" : "");
        up.textContent = "Helpful";
        up.title = "Mark response as good";
        up.addEventListener("click", () => {
          sendFeedback(index, 1).catch((err) => alert(String(err.message || err)));
        });

        const down = document.createElement("button");
        down.type = "button";
        down.className = "fb-btn" + (turn.feedback === -1 ? " is-active" : "");
        down.textContent = "Incorrect";
        down.title = "Mark incorrect and optionally provide a correction";
        down.addEventListener("click", () => {
          const correction = window.prompt(
            "What should the answer have been? (optional)",
            ""
          );
          sendFeedback(
            index,
            -1,
            correction && correction.trim() ? correction.trim() : undefined
          ).catch((err) => alert(String(err.message || err)));
        });

        actions.appendChild(up);
        actions.appendChild(down);
        article.appendChild(actions);
      }

      threadEl.appendChild(article);
    });
    threadEl.scrollTop = threadEl.scrollHeight;
  }

  function pct(acc) {
    if (acc == null || Number.isNaN(acc)) return "—";
    return `${Math.round(acc * 100)}%`;
  }

  function fmtTime(ts) {
    if (ts == null) return "—";
    try {
      return new Date(Number(ts) * 1000).toISOString().replace("T", " ").slice(0, 19);
    } catch {
      return String(ts);
    }
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function ratingLabel(rating) {
    if (rating === 1) return "correct";
    if (rating === -1) return "incorrect";
    return "neutral";
  }

  function ratingBadge(rating) {
    if (rating === 1) return "ok";
    if (rating === -1) return "bad";
    return "neutral";
  }

  function renderSummary(data) {
    const slices = data.slices || {};
    const order = [
      ["all", "All"],
      ["ask_local", "Ask-local"],
      ["agent_outcome", "Agent outcomes"],
      ["mcp", "MCP"],
      ["python_jarvis", "Python"],
      ["arduino_wand", "Arduino"],
      ["rrf_nxt", "RRF/nxt"],
    ];
    learnSummary.innerHTML = order
      .map(([key, label]) => {
        const s = slices[key] || {};
        return `<button type="button" class="learn-card" data-slice="${key}">
          <span class="learn-card-label">${label}</span>
          <span class="learn-card-acc">${pct(s.accuracy)}</span>
          <span class="learn-card-meta">${s.judged || 0} judged · +${s.pos || 0} / −${s.neg || 0}</span>
        </button>`;
      })
      .join("");
    learnSummary.querySelectorAll(".learn-card").forEach((btn) => {
      btn.addEventListener("click", () => {
        learnSlice.value = btn.dataset.slice;
        loadLearnList().catch((e) => alert(String(e.message || e)));
      });
    });

    const meta = document.createElement("p");
    meta.className = "learn-meta-line";
    meta.textContent = `${data.count || 0} records · ${data.with_correction || 0} corrections · ${data.exportable_pairs || 0} DPO pairs`;
    learnSummary.appendChild(meta);

    const days = data.by_day || [];
    if (!days.length) {
      learnDays.innerHTML = "";
      return;
    }
    learnDays.innerHTML = `<h2 class="learn-h">By day</h2>
      <div class="learn-day-row">${days
        .map((d) => {
          const acc = d.accuracy != null ? d.accuracy : 0;
          return `<div class="learn-day" title="${d.day}: ${pct(d.accuracy)} (+${d.pos || 0}/−${d.neg || 0})">
            <div class="learn-day-bar" style="--acc:${acc}"></div>
            <span class="learn-day-label">${String(d.day).slice(5)}</span>
            <span class="learn-day-pct">${pct(d.accuracy)}</span>
          </div>`;
        })
        .join("")}</div>`;
  }

  function renderList(data) {
    const items = data.items || [];
    if (!items.length) {
      learnList.innerHTML = `<li class="learn-empty">No matching feedback.</li>`;
      return;
    }
    learnList.innerHTML = "";
    for (const item of items) {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.dataset.id = item.feedback_id;
      btn.className =
        "learn-row" + (item.feedback_id === selectedFeedbackId ? " is-active" : "");
      btn.innerHTML = `<span class="learn-badge" data-r="${ratingBadge(item.rating)}">${ratingLabel(item.rating)}</span>
        <span class="learn-row-prompt">${escapeHtml(item.prompt_preview || "")}</span>
        <span class="learn-row-meta">${fmtTime(item.created_at)} · ${escapeHtml(item.source || "")}${item.has_correction ? " · corr" : ""}</span>`;
      btn.addEventListener("click", () => {
        selectFeedback(item.feedback_id).catch((e) =>
          alert(String(e.message || e))
        );
      });
      li.appendChild(btn);
      learnList.appendChild(li);
    }
  }

  async function selectFeedback(id) {
    selectedFeedbackId = id;
    learnList.querySelectorAll(".learn-row").forEach((n) => {
      n.classList.toggle("is-active", n.dataset.id === id);
    });
    const res = await fetch(`/api/feedback/item/${encodeURIComponent(id)}`);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(
        typeof data.detail === "string" ? data.detail : `Load failed (${res.status})`
      );
    }
    const tags = (data.slice_tags || []).join(", ");
    learnDetail.innerHTML = `
      <header class="learn-detail-head">
        <span class="learn-badge" data-r="${ratingBadge(data.rating)}">${ratingLabel(data.rating)}</span>
        <span class="learn-row-meta">${fmtTime(data.created_at)} · ${escapeHtml(data.source || "")}${tags ? ` · ${escapeHtml(tags)}` : ""}</span>
      </header>
      <h3>Prompt</h3>
      <pre class="learn-block">${escapeHtml(data.prompt || "")}</pre>
      <h3>Response</h3>
      <pre class="learn-block">${escapeHtml(data.response || "")}</pre>
      ${
        data.correction
          ? `<h3>Correction</h3><pre class="learn-block learn-block-ok">${escapeHtml(data.correction)}</pre>`
          : ""
      }
      ${
        data.model
          ? `<p class="learn-row-meta">model ${escapeHtml(data.model)}${data.tier ? ` · tier ${escapeHtml(String(data.tier))}` : ""}</p>`
          : ""
      }
    `;
  }

  async function loadLearnList() {
    const params = new URLSearchParams();
    const slice = learnSlice.value;
    if (slice && slice !== "all") params.set("slice", slice);
    const domain = learnDomain && learnDomain.value;
    if (domain) params.set("domain", domain);
    const rating = learnRating.value;
    if (rating !== "") params.set("rating", rating);
    const source = learnSource.value;
    if (source) params.set("source", source);
    const q = (learnQ.value || "").trim();
    if (q) params.set("q", q);
    params.set("limit", "80");
    params.set("order", "desc");
    const res = await fetch(`/api/feedback/list?${params}`);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(
        typeof data.detail === "string" ? data.detail : `List failed (${res.status})`
      );
    }
    renderList(data);
  }

  async function loadLearning() {
    learnSummary.textContent = "Loading…";
    learnDetail.innerHTML =
      '<p class="learn-detail-empty">Select a response to inspect prompt, answer, and correction.</p>';
    selectedFeedbackId = null;
    const res = await fetch("/api/feedback/summary?day_limit=30");
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(
        typeof data.detail === "string"
          ? data.detail
          : `Summary failed (${res.status})`
      );
    }
    renderSummary(data);
    await loadLearnList();
  }

  function autosize() {
    promptEl.style.height = "auto";
    promptEl.style.height = Math.min(promptEl.scrollHeight, 160) + "px";
  }

  promptEl.addEventListener("input", autosize);

  promptEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      composer.requestSubmit();
    }
  });

  clearBtn.addEventListener("click", () => {
    if (busy) return;
    history = [];
    renderThread();
    promptEl.focus();
  });

  learnRefreshBtn.addEventListener("click", () => {
    loadLearning().catch((e) => alert(String(e.message || e)));
  });

  [learnSlice, learnDomain, learnRating, learnSource].forEach((el) => {
    el.addEventListener("change", () => {
      loadLearnList().catch((e) => alert(String(e.message || e)));
    });
  });
  learnQ.addEventListener("input", () => {
    clearTimeout(learnQTimer);
    learnQTimer = setTimeout(() => {
      loadLearnList().catch((e) => alert(String(e.message || e)));
    }, 250);
  });

  composer.addEventListener("submit", async (e) => {
    e.preventDefault();
    const message = promptEl.value.trim();
    if (!message || busy) return;

    busy = true;
    sendBtn.disabled = true;
    history.push({ role: "user", content: message });
    renderThread();
    promptEl.value = "";
    autosize();

    const prior = history.slice(0, -1).map(({ role, content }) => ({
      role,
      content,
    }));

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message,
          model_tier: modelTier,
          history: prior,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail =
          typeof data.detail === "string"
            ? data.detail
            : data.detail
              ? JSON.stringify(data.detail)
              : `HTTP ${res.status}`;
        history.push({
          role: "assistant",
          content: detail,
          error: true,
        });
      } else {
        history.push({
          role: "assistant",
          content: data.reply,
          meta: data.meta || {},
          event_id: data.event_id,
        });
      }
    } catch (err) {
      history.push({
        role: "assistant",
        content: String(err.message || err),
        error: true,
      });
    } finally {
      busy = false;
      sendBtn.disabled = false;
      renderThread();
      promptEl.focus();
      pollHealth();
    }
  });

  pollHealth();
  setInterval(pollHealth, 10000);

  const hash = (location.hash || "").replace(/^#/, "");
  setView(hash === "learning" ? "learning" : "chat");
  window.addEventListener("hashchange", () => {
    const h = (location.hash || "").replace(/^#/, "");
    if (h === "learning" || h === "chat") setView(h);
  });

  if (currentView === "chat") promptEl.focus();
})();
