/* Usage dashboard: tokens/cost over time (hand-rolled SVG, accessible table
   alternative) + per-model breakdown from /api/stats. */
"use strict";

import { apiJson } from "../api.js";
import { $ } from "../util.js";
import { openModal } from "../modal.js";

const fmt = (n) => (n == null ? "0" : (+n).toLocaleString(undefined, { maximumFractionDigits: 0 }));
const usd = (n) => n ? `$${(+n).toFixed(n < 1 ? 4 : 2)}` : "—";

export async function usageModal() {
  openModal(`<h2>Usage — last 30 days</h2>
    <div class="card chart-wrap" id="usage-chart"></div>
    <div id="usage-table"></div>`);
  const data = await apiJson("/stats?days=30");
  renderChart(data.days || []);
  renderTable(data.by_model || [], data.totals || {});
}

function renderChart(days) {
  const host = $("#usage-chart");
  if (!days.length) {
    host.innerHTML = `<p class="hint">No usage recorded yet — send a message first.</p>`;
    return;
  }
  const W = 720, H = 180, PAD = 28, BW = Math.max(8, (W - PAD * 2) / days.length - 4);
  const maxTok = Math.max(...days.map((d) => (d.tokens_in || 0) + (d.tokens_out || 0)), 1);
  const parts = [`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Daily token usage bar chart">`];
  parts.push(`<line x1="${PAD}" y1="${H - PAD}" x2="${W - PAD / 2}" y2="${H - PAD}" stroke="var(--border)"/>`);
  parts.push(`<text x="${PAD}" y="12">max ${(maxTok / 1000).toFixed(1)}k tok/day</text>`);
  days.forEach((d, i) => {
    const x = PAD + i * ((W - PAD * 2) / days.length) + 2;
    const hIn = ((d.tokens_in || 0) / maxTok) * (H - PAD * 2);
    const hOut = ((d.tokens_out || 0) / maxTok) * (H - PAD * 2);
    const y = H - PAD;
    parts.push(`<rect class="bar-sub" x="${x}" y="${y - hIn}" width="${BW}" height="${Math.max(hIn, d.tokens_in ? 2 : 0)}" rx="2"><title>${d.date}: ${fmt(d.tokens_in)} in</title></rect>`);
    parts.push(`<rect class="bar" x="${x}" y="${y - hIn - hOut}" width="${BW}" height="${Math.max(hOut, d.tokens_out ? 2 : 0)}" rx="2"><title>${d.date}: ${fmt(d.tokens_out)} out</title></rect>`);
    if (i % Math.ceil(days.length / 8) === 0) {
      parts.push(`<text x="${x}" y="${H - PAD + 14}">${d.date.slice(5)}</text>`);
    }
  });
  parts.push(`<rect class="bar-sub" x="${PAD}" y="${H - 8}" width="9" height="9" rx="2"/><text x="${PAD + 14}" y="${H}">input</text>`);
  parts.push(`<rect class="bar" x="${PAD + 60}" y="${H - 8}" width="9" height="9" rx="2"/><text x="${PAD + 74}" y="${H}">output</text>`);
  parts.push(`</svg>`);
  host.innerHTML = parts.join("");
}

function renderTable(models, totals) {
  const rows = models.map((m) => `<tr>
    <td>${m.provider_id}:${m.model}</td>
    <td class="num">${fmt(m.tokens_in)}</td>
    <td class="num">${fmt(m.tokens_out)}</td>
    <td class="num">${usd(m.cost)}</td>
    <td class="num">${fmt(m.messages)}</td></tr>`).join("");
  $("#usage-table").innerHTML = `
    <table class="stats-table">
      <thead><tr><th>Model</th><th class="num">Tokens in</th>
        <th class="num">Tokens out</th><th class="num">Est. cost</th>
        <th class="num">Replies</th></tr></thead>
      <tbody>${rows}
        <tr><th>Totals</th>
          <td class="num">${fmt(totals.prompt_tokens || 0)}</td>
          <td class="num">${fmt(totals.completion_tokens || 0)}</td>
          <td class="num">${usd(totals.cost)}</td>
          <td class="num">${fmt(totals.messages || 0)}</td></tr>
      </tbody>
    </table>
    <p class="hint">Costs are estimates from your model prices in Settings → General
    (blank when unset). Cached input tokens are included in the in counts.</p>`;
}
