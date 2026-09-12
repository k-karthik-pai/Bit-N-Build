"use strict";

const state = {
  source: null,
  context: null,
  buyers: new Map(),
  threads: new Map(),
  offers: [],
  colors: ["#2457a7", "#18794e", "#a15c00", "#6b5ca5", "#b42318"],
  running: false,
};

const el = id => document.getElementById(id);
const hasNumber = value => value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));
const money = value => hasNumber(value)
  ? new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(Number(value))
  : "—";
const number = value => hasNumber(value) ? new Intl.NumberFormat("en-US").format(Number(value)) : "—";

function setConnection(label, kind = "") {
  el("connectionText").textContent = label;
  el("connectionDot").parentElement.className = `connection ${kind}`.trim();
}

function showAlert(message) {
  el("alert").textContent = message;
  el("alert").classList.toggle("hidden", !message);
}

function setRunning(value) {
  state.running = value;
  el("liveButton").disabled = value;
  el("replayButton").disabled = value;
  el("topN").disabled = value;
  el("replaySpeed").disabled = value;
}

function resetDashboard() {
  if (state.source) state.source.close();
  state.source = null;
  state.buyers.clear();
  state.threads.clear();
  state.offers = [];
  el("buyerBoard").className = "buyer-board empty-state";
  el("buyerBoard").textContent = "Waiting for circularity matches…";
  el("threads").className = "threads empty-state";
  el("threads").textContent = "Waiting for negotiation threads…";
  el("threadTabs").replaceChildren();
  el("systemFeed").replaceChildren();
  el("recommendation").classList.add("hidden");
  el("recommendation").replaceChildren();
  el("buyerCount").textContent = "0";
  el("eventCount").textContent = "0 events";
  showAlert("");
  drawChart();
}

function addSystem(title, detail, danger = false) {
  const row = document.createElement("p");
  if (danger) row.className = "danger";
  const strong = document.createElement("strong");
  strong.textContent = `${title} · `;
  row.append(strong, document.createTextNode(detail));
  el("systemFeed").append(row);
  el("systemFeed").scrollTop = el("systemFeed").scrollHeight;
}

function renderMatches(selected) {
  const board = el("buyerBoard");
  board.className = "buyer-board";
  board.replaceChildren();
  selected.forEach((buyer, index) => {
    const card = document.createElement("article");
    card.className = "buyer-card matched";
    const heading = document.createElement("div");
    heading.className = "buyer-name";
    const name = document.createElement("span");
    name.textContent = buyer.buyer_name;
    const score = document.createElement("span");
    score.className = "score";
    score.textContent = `${Math.round(Number(buyer.compatibility_score) * 100)}%`;
    heading.append(name, score);
    const meta = document.createElement("div");
    meta.className = "buyer-meta";
    meta.textContent = `${buyer.application.replaceAll("_", " ")} · ${buyer.port_id}`;
    const chip = document.createElement("span");
    chip.className = "status-chip";
    chip.textContent = "matched";
    card.append(heading, meta, chip);
    board.append(card);
    state.buyers.set(buyer.buyer_id, { ...buyer, card, chip, color: state.colors[index % state.colors.length] });
  });
  el("buyerCount").textContent = String(selected.length);
  drawChart();
}

function setBuyerStatus(buyerId, status) {
  const buyer = state.buyers.get(buyerId);
  if (!buyer) return;
  buyer.card.className = `buyer-card ${status}`;
  buyer.chip.textContent = status;
}

function activateThread(buyerId) {
  state.threads.forEach((thread, id) => {
    thread.node.classList.toggle("active", id === buyerId);
    thread.tab.classList.toggle("active", id === buyerId);
  });
}

function ensureThread(buyerId, buyerName = buyerId) {
  if (state.threads.has(buyerId)) return state.threads.get(buyerId);
  const container = el("threads");
  if (container.classList.contains("empty-state")) {
    container.className = "threads";
    container.replaceChildren();
  }
  const tab = document.createElement("button");
  tab.className = "thread-tab";
  tab.type = "button";
  tab.setAttribute("role", "tab");
  tab.textContent = buyerName;
  tab.addEventListener("click", () => activateThread(buyerId));
  el("threadTabs").append(tab);
  const node = document.createElement("div");
  node.className = "thread";
  node.setAttribute("role", "tabpanel");
  container.append(node);
  const thread = { tab, node, name: buyerName };
  state.threads.set(buyerId, thread);
  if (state.threads.size === 1) activateThread(buyerId);
  return thread;
}

function addMove(event) {
  const p = event.payload;
  const thread = ensureThread(event.deal_id);
  const bubble = document.createElement("article");
  const seller = event.from_agent === "seller_agent";
  bubble.className = `bubble ${seller ? "seller" : "buyer"}${event.delivered === false ? " bounced" : ""}`;
  const head = document.createElement("div");
  head.className = "bubble-head";
  const label = document.createElement("span");
  label.textContent = `${seller ? "Seller" : "Buyer"} · round ${p.round}`;
  const model = document.createElement("span");
  model.className = "model-badge";
  model.textContent = event.model || "deterministic";
  head.append(label, model);
  const message = document.createElement("p");
  message.className = "bubble-message";
  message.textContent = p.message || p.reason || event.type.replaceAll("_", " ");
  bubble.append(head, message);
  if (event.type === "offer") {
    const terms = document.createElement("div");
    terms.className = "terms";
    [`${money(p.price_per_tonne_usd)}/t`, `${number(p.quantity_tonnes)} t`, `${p.contract_months} months`].forEach(text => {
      const term = document.createElement("span");
      term.className = "term";
      term.textContent = text;
      terms.append(term);
    });
    bubble.append(terms);
    if (event.delivered) {
      state.offers.push({ buyerId: event.deal_id, round: Number(p.round), price: Number(p.price_per_tonne_usd) });
      drawChart();
    }
  }
  if (event.validator) {
    const validator = document.createElement("div");
    validator.className = `validator ${event.validator.ok ? "" : "fail"}`;
    validator.textContent = `${event.validator.ok ? "✓ Passed" : "✕ Bounced"} ${event.validator.gate.replace("_", "-")} · ${event.validator.reason}`;
    bubble.append(validator);
  }
  if (p.rationale) {
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = "Private rationale";
    const rationale = document.createElement("p");
    rationale.textContent = p.rationale;
    details.append(summary, rationale);
    bubble.append(details);
  }
  thread.node.append(bubble);
  thread.node.scrollTop = thread.node.scrollHeight;
}

function drawChart() {
  const svg = el("priceChart");
  svg.replaceChildren();
  const NS = "http://www.w3.org/2000/svg";
  const create = (name, attrs = {}) => {
    const node = document.createElementNS(NS, name);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
    return node;
  };
  const width = 420, height = 270, left = 42, right = 16, top = 18, bottom = 34;
  const floor = Number(state.context?.audience_constraints?.seller_floor ?? 20);
  const ceilings = Object.values(state.context?.audience_constraints?.buyer_ceilings || {}).map(Number);
  const prices = state.offers.map(offer => offer.price);
  const low = Math.floor(Math.min(floor, ...prices, 20) - 1);
  const high = Math.ceil(Math.max(...ceilings, ...prices, 30) + 1);
  const maxRound = Math.max(6, ...state.offers.map(offer => offer.round));
  const x = round => left + ((round - 1) / Math.max(1, maxRound - 1)) * (width - left - right);
  const y = price => top + ((high - price) / Math.max(1, high - low)) * (height - top - bottom);

  const bandTop = y(Math.max(...ceilings, high - 1));
  const bandBottom = y(floor);
  svg.append(create("rect", { x: left, y: bandTop, width: width - left - right, height: Math.max(0, bandBottom - bandTop), fill: "rgba(36,87,167,.06)" }));
  for (let price = low; price <= high; price += 2) {
    svg.append(create("line", { x1: left, y1: y(price), x2: width - right, y2: y(price), stroke: "#e5e7eb", "stroke-width": 1 }));
    const label = create("text", { x: left - 8, y: y(price) + 3, "text-anchor": "end", class: "chart-label" });
    label.textContent = `$${price}`;
    svg.append(label);
  }
  for (let round = 1; round <= maxRound; round += 1) {
    const label = create("text", { x: x(round), y: height - 10, "text-anchor": "middle", class: "chart-label" });
    label.textContent = `R${round}`;
    svg.append(label);
  }
  const grouped = new Map();
  state.offers.forEach(offer => {
    if (!grouped.has(offer.buyerId)) grouped.set(offer.buyerId, []);
    grouped.get(offer.buyerId).push(offer);
  });
  grouped.forEach((offers, buyerId) => {
    const buyer = state.buyers.get(buyerId);
    const color = buyer?.color || state.colors[grouped.size % state.colors.length];
    const sorted = offers.slice().sort((a, b) => a.round - b.round);
    const points = sorted.map(offer => `${x(offer.round)},${y(offer.price)}`).join(" ");
    svg.append(create("polyline", { points, fill: "none", stroke: color, "stroke-width": 2.5, "stroke-linecap": "round", "stroke-linejoin": "round" }));
    sorted.forEach(offer => svg.append(create("circle", { cx: x(offer.round), cy: y(offer.price), r: 4, fill: color, stroke: "#ffffff", "stroke-width": 2 })));
  });
  const legend = el("chartLegend");
  legend.replaceChildren();
  state.buyers.forEach(buyer => {
    const item = document.createElement("span");
    const dot = document.createElement("i");
    dot.style.background = buyer.color;
    item.append(dot, document.createTextNode(buyer.buyer_name));
    legend.append(item);
  });
}

function renderRecommendation(p) {
  const section = el("recommendation");
  section.classList.remove("hidden");
  const grid = document.createElement("div");
  grid.className = "rec-grid";
  const winner = document.createElement("div");
  winner.className = "rec-winner";
  const eyebrow = document.createElement("p");
  eyebrow.className = "eyebrow";
  eyebrow.textContent = "Recommended deal";
  const title = document.createElement("h2");
  title.textContent = p.buyer_name;
  winner.append(eyebrow, title);
  grid.append(winner);
  [
    ["Agreed price", `${money(p.price_per_tonne_usd)}/t`],
    ["Quantity", `${number(p.quantity_tonnes)} t`],
    ["Net value", money(p.total_net_value_usd)],
    ["CO₂ avoided (estimate)", `${number(p.co2_avoided_tonnes_estimate)} t`],
  ].forEach(([label, value]) => {
    const metric = document.createElement("div");
    metric.className = "rec-metric";
    const small = document.createElement("small");
    small.textContent = label;
    const strong = document.createElement("strong");
    strong.textContent = value;
    metric.append(small, strong);
    grid.append(metric);
  });
  section.replaceChildren(grid);
}

function handleEvent(event) {
  el("eventCount").textContent = `${event.seq} events`;
  const p = event.payload;
  switch (event.type) {
    case "run_started":
      el("scenarioText").textContent = `${number(p.quantity_tonnes)} tonnes of ${p.material_id.replaceAll("_", " ")} · ${p.objective.replaceAll("_", " ")}`;
      addSystem("Run started", `${p.top_n} buyer threads requested`);
      break;
    case "match":
      renderMatches(p.selected);
      addSystem("Circularity", `${p.matched_count} compatible buyers; top ${p.selected.length} selected`);
      break;
    case "route":
      addSystem("Logistics", `${p.origin_port} → ${p.destination_port}, ${number(p.distance_km)} km, ${money(p.cost_per_tonne_usd)}/t`);
      break;
    case "thread_started":
      ensureThread(p.buyer_id, p.buyer_name);
      setBuyerStatus(p.buyer_id, "negotiating");
      break;
    case "offer": case "accept": case "reject": case "info_request":
      addMove(event);
      break;
    case "info_response":
      addSystem("Agent information", `${p.topic.replaceAll("_", " ")} returned to ${event.to_agent}`);
      break;
    case "fallback":
      addSystem("Visible fallback", `${p.agent}: ${p.cause} — ${p.detail}`, true);
      break;
    case "thread_result":
      setBuyerStatus(event.deal_id, p.status);
      addSystem("Thread result", p.price_per_tonne_usd === null
        ? `${event.deal_id}: ${p.status} — no deal`
        : `${event.deal_id}: ${p.status} at ${money(p.price_per_tonne_usd)}/t`);
      break;
    case "released":
      setBuyerStatus(event.deal_id, "released");
      addSystem("Released", `${event.deal_id}: ${p.reason}`);
      break;
    case "deal_closed":
      setBuyerStatus(p.buyer_id, "accepted");
      addSystem("Deal closed", `${p.buyer_id} · ${money(p.total_net_value_usd)} net value`);
      break;
    case "recommendation":
      renderRecommendation(p);
      break;
    case "run_completed":
      addSystem("Completed", `${p.llm_calls} LLM calls · ${p.validator_bounces} bounces · ${p.fallbacks} fallbacks`);
      setConnection("Completed", "live");
      finishStream();
      break;
    case "run_failed":
      showAlert(p.error);
      addSystem("Run failed", p.error, true);
      setConnection("Failed", "failed");
      finishStream();
      break;
  }
}

function finishStream() {
  if (state.source) state.source.close();
  state.source = null;
  setRunning(false);
}

function connectStream(url) {
  const source = new EventSource(url);
  state.source = source;
  source.onopen = () => setConnection("Streaming", "live");
  source.onmessage = message => {
    try { handleEvent(JSON.parse(message.data)); }
    catch { showAlert("The server sent an unreadable event."); }
  };
  ["run_started", "match", "route", "thread_started", "offer", "accept", "reject", "info_request", "info_response", "fallback", "thread_result", "released", "deal_closed", "recommendation", "run_completed", "run_failed"].forEach(type => {
    source.addEventListener(type, message => {
      try { handleEvent(JSON.parse(message.data)); }
      catch { showAlert("The server sent an unreadable event."); }
    });
  });
  source.onerror = () => {
    if (state.running) setConnection("Reconnecting…");
  };
}

async function startRun(mode) {
  resetDashboard();
  setRunning(true);
  setConnection(mode === "live" ? "Starting live run…" : "Loading replay…");
  try {
    const response = await fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        mode,
        top_n: Number(el("topN").value),
        replay_speed: Number(el("replaySpeed").value),
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Could not start the run");
    connectStream(result.events_url);
  } catch (error) {
    showAlert(error.message || "Could not start the run");
    setConnection("Failed", "failed");
    setRunning(false);
  }
}

async function initialize() {
  el("replayButton").addEventListener("click", () => startRun("replay"));
  el("liveButton").addEventListener("click", () => startRun("live"));
  try {
    const response = await fetch("/api/context");
    if (!response.ok) throw new Error("Context unavailable");
    state.context = await response.json();
    const scenario = state.context.scenario;
    el("scenarioText").textContent = `${number(scenario.quantity_tonnes)} tonnes of ${scenario.material_id.replaceAll("_", " ")} · ${scenario.objective.replaceAll("_", " ")}`;
    el("replaySpeed").value = String(state.context.default_replay_speed);
    if (!el("replaySpeed").value) el("replaySpeed").value = "4";
    drawChart();
    if (state.context.auto_start_replay) startRun("replay");
  } catch {
    showAlert("Dashboard context could not be loaded. Refresh the page or check the server logs.");
  }
}

initialize();
