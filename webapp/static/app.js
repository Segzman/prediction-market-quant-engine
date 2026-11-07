const runForm = document.querySelector("#run-form");
const controlMessage = document.querySelector("#control-message");
const heartbeat = document.querySelector("#heartbeat");
const processSummary = document.querySelector("#process-summary");
const runFacts = document.querySelector("#run-facts");
const marketFacts = document.querySelector("#market-facts");
const pathFacts = document.querySelector("#path-facts");
const modelRows = document.querySelector("#model-rows");
const reportRows = document.querySelector("#report-rows");
const reportMarkdown = document.querySelector("#report-markdown");
const feeContext = document.querySelector("#fee-context");
const logOutput = document.querySelector("#log-output");

function formatValue(value) {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  if (typeof value === "number") {
    return Number.isInteger(value) ? String(value) : value.toFixed(4);
  }
  if (Array.isArray(value)) {
    return value.join(", ");
  }
  return String(value);
}

function formatMoney(value) {
  if (value === null || value === undefined) {
    return "-";
  }
  return `$${Number(value).toFixed(2)}`;
}

function formatPercent(value) {
  if (value === null || value === undefined) {
    return "-";
  }
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function setMessage(text, kind = "") {
  controlMessage.textContent = text;
  controlMessage.className = `message ${kind}`.trim();
}

function renderFacts(element, rows) {
  element.innerHTML = rows
    .map(([label, value]) => `<dt>${label}</dt><dd>${formatValue(value)}</dd>`)
    .join("");
}

function renderModels(models) {
  if (!models.length) {
    modelRows.innerHTML = `<tr><td colspan="11">No active model rows yet.</td></tr>`;
    return;
  }
  modelRows.innerHTML = models
    .map(
      (model) => `
      <tr>
        <td>${model.model}</td>
        <td>${formatValue(model.last_event)}</td>
        <td>${formatValue(model.last_regime_candidate)} / ${formatValue(model.last_signal_direction)} / ${formatValue(model.last_strongest_side)} / edge ${formatValue(model.last_signal_edge)} / net ${formatValue(model.last_net_expected_profit_usdc)}</td>
        <td>${formatValue(model.last_order_status)}</td>
        <td>${formatValue(model.last_resolution)} / ${formatValue(model.last_no_trade_reason)} / fee ${formatValue(model.last_estimated_cost_usdc)}</td>
        <td>${formatValue(model.total_trades)}</td>
        <td>${formatValue(model.wins)}/${formatValue(model.losses)}</td>
        <td>${formatMoney(model.daily_pnl)}</td>
        <td>${formatMoney(model.current_bankroll_usdc)}</td>
        <td>${formatMoney(model.available_bankroll_usdc)}</td>
        <td>${formatMoney(model.pending_exposure_usdc)}</td>
      </tr>
    `
    )
    .join("");
}

function renderReport(rows) {
  const entries = Object.entries(rows || {});
  if (!entries.length) {
        reportRows.innerHTML = `<tr><td colspan="9">No reportable primary trades yet.</td></tr>`;
    return;
  }
  reportRows.innerHTML = entries
    .map(
      ([model, row]) => `
      <tr>
        <td>${model}</td>
        <td>${formatValue(row.trades)}</td>
        <td>${formatValue(row.resolved)}</td>
        <td>${formatPercent(row.win_rate)}</td>
        <td>${formatMoney(row.gross_pnl)}</td>
        <td>${formatMoney(row.estimated_fees)}</td>
        <td>${formatMoney(row.net_pnl)}</td>
        <td>${formatValue(row.avg_fill_slippage_bps)}</td>
        <td>${formatValue(row.avg_submit_slippage_bps)}</td>
      </tr>
    `
    )
    .join("");
}

function renderFeeContext(context) {
  if (!context) {
    feeContext.textContent = "";
    feeContext.className = "message";
    return;
  }
  feeContext.textContent =
    `${context.assumption} Formula: ${context.formula}. feeRate=${context.fee_rate}, exponent=${context.exponent}. Source: ${context.source_url}`;
  feeContext.className = "message";
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || `Request failed: ${response.status}`);
  }
  return payload;
}

async function loadStatus() {
  try {
    const payload = await fetchJson("/api/status");
    const runStatus = payload.status || {};
    const market = runStatus.market || {};
    const models = runStatus.models || [];

    heartbeat.textContent = payload.running ? "Running" : "Idle";
    processSummary.textContent = payload.configured
      ? `${payload.config?.mode || "-"} | pid ${payload.pid || "-"}`
      : "No managed run";

    renderFacts(runFacts, [
      ["Running", payload.running ? "Yes" : "No"],
      ["Mode", payload.config?.mode || "-"],
      ["Asset", payload.config?.asset || runStatus.asset || "-"],
      ["Models", payload.config?.models || models.map((item) => item.model)],
      ["Bankroll", payload.config?.bankroll ? formatMoney(payload.config.bankroll) : "-"],
      ["Duration", payload.config?.duration || "-"],
      ["PID", payload.pid || "-"],
      ["Exit Code", payload.exit_code ?? "-"],
    ]);

    renderFacts(marketFacts, [
      ["Slug", market.slug || "-"],
      ["Phase", market.phase || "-"],
      ["Seconds Left", market.seconds_remaining ?? "-"],
      ["Price", runStatus.price?.current ? formatMoney(runStatus.price.current) : "-"],
      ["Price Source", runStatus.price?.source || "-"],
      ["UP Ask", market.up_ask ?? "-"],
      ["DOWN Ask", market.down_ask ?? "-"],
      ["Price Age", runStatus.price?.age_seconds ?? "-"],
      ["Generated", runStatus.generated_ts ? new Date(runStatus.generated_ts * 1000).toLocaleTimeString() : "-"],
    ]);

    renderFacts(pathFacts, [
      ["Ledger", payload.paths?.ledger_file || "-"],
      ["Status", payload.paths?.status_file || "-"],
      ["Log", payload.paths?.log_file || "-"],
      ["Command", payload.command || "-"],
    ]);

    renderModels(models);
    renderReport(payload.report?.rows || {});
    renderFeeContext(payload.report?.fee_context || null);
    reportMarkdown.textContent = payload.report?.markdown || "No performance table yet.";
    logOutput.textContent = (payload.logs || []).join("\n") || "No log output yet.";
  } catch (error) {
    setMessage(error.message, "error");
  }
}

async function startRun() {
  const formData = new FormData(runForm);
  const payload = {
    mode: formData.get("mode"),
    asset: formData.get("asset"),
    models: formData.get("models"),
    bankroll: Number(formData.get("bankroll")),
    duration: formData.get("duration"),
    debug: runForm.querySelector('input[name="debug"]').checked,
  };
  try {
    await fetchJson("/api/start", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    setMessage("Run started.", "success");
    await loadStatus();
  } catch (error) {
    setMessage(error.message, "error");
  }
}

async function stopRun() {
  try {
    const payload = await fetchJson("/api/stop", {
      method: "POST",
      body: JSON.stringify({}),
    });
    setMessage(payload.stopped ? "Run stop requested." : payload.reason || "Run not active.", payload.stopped ? "success" : "");
    await loadStatus();
  } catch (error) {
    setMessage(error.message, "error");
  }
}

document.querySelector("#start-button").addEventListener("click", startRun);
document.querySelector("#stop-button").addEventListener("click", stopRun);
document.querySelector("#refresh-button").addEventListener("click", loadStatus);
document.querySelector("#report-button").addEventListener("click", loadStatus);

loadStatus();
setInterval(loadStatus, 2000);
