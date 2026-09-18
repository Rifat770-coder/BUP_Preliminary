"use strict";

/* ---------- DOM helpers ---------- */
const $ = (id) => document.getElementById(id);
const fmtNum = (n, d = 2) => (n == null || Number.isNaN(n) ? "—" : Number(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }));

/* ---------- Demo scenario (valid input payload, no expected outputs) ---------- */
function buildDemoScenario() {
  const hours = [];
  for (let h = 0; h < 24; h++) {
    // Simple demand curve: higher during the day, lower at night.
    const base = 9 + 4 * Math.sin(((h - 6) / 24) * 2 * Math.PI);
    const demand = Math.max(5, +base.toFixed(2));
    // Solar only between 6 and 18, peaking at noon.
    const solar = h >= 6 && h <= 18 ? +(8 * Math.sin(((h - 6) / 12) * Math.PI)).toFixed(2) : 0;
    // Tariff: peak 18-22, off-peak otherwise.
    const tariff = (h >= 18 && h <= 22) ? 14.0 : 7.5;
    hours.push({ hour: h, demand_kwh: demand, solar_kwh: Math.max(0, solar), tariff_bdt_per_kwh: tariff });
  }
  return {
    scenario_id: "demo-001",
    battery: {
      capacity_kwh: 20,
      initial_energy_kwh: 8,
      minimum_energy_kwh: 2,
      max_charge_kwh_per_hour: 6,
      max_discharge_kwh_per_hour: 6,
    },
    hours,
    operator_notes: [
      "Solar output will be reduced by 80% from 1 PM to 3 PM.",
    ],
  };
}

/* ---------- Notes editor (1–3 notes) ---------- */
const NOTE_PLACEHOLDERS = [
  "Solar output will be reduced by 80% from 1 PM to 3 PM.",
  "Keep battery energy above 120 kWh from 6 PM to 9 PM.",
  "Block battery charging between 7 PM and 9 PM tonight.",
];

function renderNotes(notes) {
  const list = $("notesList");
  list.innerHTML = "";
  notes.forEach((text, i) => {
    const row = document.createElement("div");
    row.className = "note-row";
    const input = document.createElement("input");
    input.type = "text";
    input.value = text;
    input.placeholder = NOTE_PLACEHOLDERS[i] || `Operator note ${i + 1}`;
    input.addEventListener("input", () => {
      state.notes[i] = input.value;
      syncScenarioJson();
      updateOptimizeBtn();
    });
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "btn btn-sm";
    remove.textContent = "Remove";
    remove.disabled = notes.length <= 1;
    remove.addEventListener("click", () => {
      state.notes.splice(i, 1);
      renderNotes(state.notes);
      syncScenarioJson();
      updateOptimizeBtn();
    });
    row.appendChild(input);
    row.appendChild(remove);
    list.appendChild(row);
  });
}

let state = { scenario: null, notes: [""] };

function syncScenarioJson() {
  if (!state.scenario) return;
  state.scenario.operator_notes = state.notes.filter((n) => n.trim().length > 0);
  $("scenarioJson").value = JSON.stringify(state.scenario, null, 2);
}

function loadScenario(scenario) {
  state.scenario = JSON.parse(JSON.stringify(scenario));
  state.notes = (state.scenario.operator_notes && state.scenario.operator_notes.length > 0)
    ? [...state.scenario.operator_notes]
    : [""];
  renderNotes(state.notes);
  syncScenarioJson();
  updateOptimizeBtn();
  // Reset result panels
  $("resultsCard").hidden = true;
  $("summaryCard").hidden = true;
  $("planCard").hidden = true;
  $("chartCard").hidden = true;
  $("errorBox").hidden = true;
}

function updateOptimizeBtn() {
  const hasScenario = !!state.scenario;
  const hasNote = state.notes.some((n) => n.trim().length > 0);
  $("optimizeBtn").disabled = !(hasScenario && hasNote);
  $("addNoteBtn").disabled = !(state.notes.length < 3);
}

/* ---------- API status ---------- */
async function checkHealth() {
  const dot = $("statusDot");
  const label = $("statusLabel");
  const box = $("apiStatus");
  box.classList.remove("ok", "err");
  try {
    const r = await fetch("/health", { cache: "no-store" });
    if (!r.ok) throw new Error("status " + r.status);
    box.classList.add("ok");
    label.textContent = "● API Online";
  } catch (e) {
    box.classList.add("err");
    label.textContent = "● API Offline";
  }
}

/* ---------- Optimize ---------- */
function readScenarioFromTextarea() {
  try {
    const parsed = JSON.parse($("scenarioJson").value);
    state.scenario = parsed;
    state.notes = (parsed.operator_notes && parsed.operator_notes.length > 0)
      ? [...parsed.operator_notes]
      : [""];
    renderNotes(state.notes);
    updateOptimizeBtn();
    return parsed;
  } catch (e) {
    showError("Scenario JSON is not valid: " + e.message);
    return null;
  }
}

async function optimize() {
  hideError();
  // If user edited the textarea, prefer that.
  const ta = $("scenarioJson");
  let scenario;
  if (ta.value.trim().length > 0) {
    try {
      scenario = JSON.parse(ta.value);
    } catch (e) {
      showError("Scenario JSON is not valid: " + e.message);
      return;
    }
  } else {
    scenario = state.scenario;
  }
  if (!scenario) { showError("Load a scenario first."); return; }
  scenario.operator_notes = state.notes.filter((n) => n.trim().length > 0);
  if (scenario.operator_notes.length === 0) {
    showError("Please provide at least one operator note."); return;
  }
  if (scenario.operator_notes.length > 3) {
    showError("A maximum of 3 operator notes is allowed."); return;
  }

  const btn = $("optimizeBtn");
  const hint = $("loadingHint");
  btn.disabled = true;
  hint.hidden = false;

  try {
    const r = await fetch("/optimize-energy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(scenario),
    });
    const text = await r.text();
    let body = null;
    try { body = JSON.parse(text); } catch (_) { /* leave null */ }

    if (!r.ok) {
      const detail = (body && (body.detail || body.message)) || text || `HTTP ${r.status}`;
      const msg = Array.isArray(detail) ? detail.map((d) => d.msg || JSON.stringify(d)).join("; ") : String(detail);
      showError(prettyError(r.status, msg));
      return;
    }
    renderResults(body);
  } catch (e) {
    showError("Network error: " + e.message);
  } finally {
    btn.disabled = false;
    hint.hidden = true;
    updateOptimizeBtn();
  }
}

function prettyError(status, msg) {
  if (status === 0) return "Could not reach the backend.";
  if (status === 400) return "Invalid request: " + msg;
  if (status === 422) return "The directive was accepted but could not be applied: " + msg;
  if (status === 500) return "Optimization or LLM error: " + msg;
  return `Request failed (HTTP ${status}): ${msg}`;
}

function showError(msg) {
  const box = $("errorBox");
  box.textContent = msg;
  box.hidden = false;
}
function hideError() { $("errorBox").hidden = true; }

/* ---------- Render results ---------- */
function renderResults(resp) {
  renderDirectives(resp.directive_interpretation || []);
  renderSummary(resp);
  renderPlan(resp.hourly_plan || []);
  renderChart(resp.hourly_plan || []);
  $("resultsCard").hidden = false;
  $("summaryCard").hidden = false;
  $("planCard").hidden = false;
  $("chartCard").hidden = false;
  // Scroll to results
  $("resultsCard").scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderDirectives(directives) {
  const root = $("directives");
  root.innerHTML = "";
  if (directives.length === 0) {
    root.innerHTML = '<p class="muted small">No directives returned.</p>';
    return;
  }
  directives.forEach((d) => {
    const card = document.createElement("div");
    card.className = "directive";
    const top = document.createElement("div"); top.className = "top";
    const pill = document.createElement("span");
    pill.className = "type-pill" + (d.directive_type === "no_op" ? " noop" : "");
    pill.textContent = d.directive_type || "—";
    const applies = document.createElement("span");
    applies.className = "applies";
    applies.textContent = "Applies: " + (d.applies ? "Yes" : "No");
    top.appendChild(pill); top.appendChild(applies);
    card.appendChild(top);

    // The "original note" is reconstructed from state if index matches.
    const idx = d.note_index;
    if (idx != null && state.notes[idx]) {
      const note = document.createElement("div");
      note.className = "note";
      note.textContent = `“${state.notes[idx]}”`;
      card.appendChild(note);
    }

    if (d.structured_adjustment != null) {
      const adj = document.createElement("div");
      adj.className = "adjustment";
      adj.textContent = JSON.stringify(d.structured_adjustment);
      card.appendChild(adj);
    }

    if (d.explanation) {
      const exp = document.createElement("div");
      exp.className = "explanation";
      exp.textContent = d.explanation;
      card.appendChild(exp);
    }

    root.appendChild(card);
  });
}

function renderSummary(resp) {
  $("metricGrid").textContent = fmtNum(resp.total_grid_kwh);
  $("metricCost").textContent = "৳ " + fmtNum(resp.total_cost_bdt);
  $("metricPeak").textContent = fmtNum(resp.peak_grid_kwh);
  $("metricScenario").textContent = resp.scenario_id || "—";
  $("planSummary").textContent = resp.plan_summary || "";
}

function renderPlan(plan) {
  const tbody = $("planBody");
  tbody.innerHTML = "";
  plan.forEach((row) => {
    const tr = document.createElement("tr");
    const actionClass = row.battery_action === "charge" ? "action-charge"
      : row.battery_action === "discharge" ? "action-discharge"
      : "action-idle";
    tr.innerHTML = `
      <td>${row.hour}</td>
      <td>${fmtNum(row.grid_kwh)}</td>
      <td>${fmtNum(row.solar_used_kwh)}</td>
      <td class="${actionClass}">${row.battery_action || "—"}</td>
      <td>${fmtNum(row.battery_kwh)}</td>
      <td>${fmtNum(row.battery_energy_after_kwh)}</td>
    `;
    tbody.appendChild(tr);
  });
}

/* ---------- Chart ---------- */
let chartInstance = null;
function renderChart(plan) {
  if (typeof Chart === "undefined") return; // CDN not loaded yet
  const labels = plan.map((r) => r.hour);
  const grid = plan.map((r) => r.grid_kwh);
  const solar = plan.map((r) => r.solar_used_kwh);
  const batt = plan.map((r) => r.battery_energy_after_kwh);
  const ctx = document.getElementById("energyChart").getContext("2d");
  if (chartInstance) { chartInstance.destroy(); }
  chartInstance = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [
        { type: "bar", label: "Grid (kWh)", data: grid, backgroundColor: "rgba(79, 140, 255, 0.65)", borderColor: "rgba(79, 140, 255, 1)", borderWidth: 1 },
        { type: "bar", label: "Solar Used (kWh)", data: solar, backgroundColor: "rgba(56, 214, 164, 0.65)", borderColor: "rgba(56, 214, 164, 1)", borderWidth: 1 },
        { type: "line", label: "Battery Energy After (kWh)", data: batt, borderColor: "#f0b341", backgroundColor: "rgba(240, 179, 65, 0.15)", tension: 0.25, yAxisID: "y1", pointRadius: 2 },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { title: { display: true, text: "Hour", color: "#8898b3" }, ticks: { color: "#8898b3" }, grid: { color: "rgba(31, 44, 71, 0.5)" } },
        y: { title: { display: true, text: "Energy (kWh)", color: "#8898b3" }, ticks: { color: "#8898b3" }, grid: { color: "rgba(31, 44, 71, 0.5)" }, beginAtZero: true },
        y1: { position: "right", title: { display: true, text: "Battery (kWh)", color: "#8898b3" }, ticks: { color: "#8898b3" }, grid: { drawOnChartArea: false }, beginAtZero: true },
      },
      plugins: {
        legend: { labels: { color: "#e6edf7" } },
        tooltip: { backgroundColor: "#0e172a", borderColor: "#1f2c47", borderWidth: 1 },
      },
    },
  });
  // Give the canvas an explicit height for responsiveness.
  document.getElementById("energyChart").style.height = "320px";
}

/* ---------- Init ---------- */
function init() {
  $("loadDemoBtn").addEventListener("click", () => {
    loadScenario(buildDemoScenario());
  });
  $("addNoteBtn").addEventListener("click", () => {
    if (state.notes.length >= 3) return;
    state.notes.push("");
    renderNotes(state.notes);
    updateOptimizeBtn();
  });
  $("optimizeBtn").addEventListener("click", optimize);
  $("scenarioJson").addEventListener("blur", () => {
    if ($("scenarioJson").value.trim().length === 0) return;
    const parsed = readScenarioFromTextarea();
    if (parsed) syncScenarioJson();
  });

  // Start with an empty editor; user must click "Load Demo Scenario".
  state.notes = [""];
  renderNotes(state.notes);
  updateOptimizeBtn();

  checkHealth();
  setInterval(checkHealth, 15000);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
