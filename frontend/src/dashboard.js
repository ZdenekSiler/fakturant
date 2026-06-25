/**
 * dashboard.js — Fakturant přehled (dashboard view)
 *
 * Depends on app.js being loaded first:
 *   apiFetch(), loadInvoice(), closeDashboard(), escHtml(), fmtNum(), STATUS_LABELS
 *
 * All metrics are computed client-side from GET /api/invoices?limit=500.
 * No new backend endpoints are required.
 */

"use strict";

/* ── Constants ─────────────────────────────────────────────── */

const CS_MONTHS = ["Led","Úno","Bře","Dub","Kvě","Čvn","Čvc","Srp","Zář","Říj","Lis","Pro"];

const DASH_STATUS_COLORS = {
  draft:     "rgba(171,171,188,0.9)",
  issued:    "rgba(33,85,205,0.85)",
  sent:      "rgba(124,58,184,0.85)",
  paid:      "rgba(26,122,74,0.85)",
  overdue:   "rgba(196,43,43,0.85)",
  cancelled: "rgba(216,213,206,0.9)",
};

/* ── Chart instances (destroyed before re-render) ───────────── */

let _revenueChart  = null;
let _statusChart   = null;
let _allInvoices   = [];
let _activeYear    = null;   // null = show all years

/* ── Public API ─────────────────────────────────────────────── */

async function loadDashboard() {
  const loadingEl = document.getElementById("dashLoading");
  const bodyEl    = document.getElementById("dashBody");
  if (loadingEl) { loadingEl.style.display = "flex"; loadingEl.innerHTML = '<span class="spin"></span> Načítám data…'; }
  if (bodyEl)    bodyEl.style.display = "none";

  try {
    const resp = await apiFetch("/api/invoices?limit=500");
    if (!resp) return;
    if (resp.status === 403) {
      const body = await resp.json();
      if (body.detail === "email_not_verified") {
        if (loadingEl) loadingEl.innerHTML =
          `<span style="color:var(--red)">Potvrďte svůj e-mail pro přístup k fakturám.
           <a href="/auth/verify-email-sent" style="color:var(--blue)">Odeslat znovu</a></span>`;
      }
      return;
    }
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      const detail = body.detail;
      const msg = typeof detail === "string" ? detail : `Server error ${resp.status}`;
      throw new Error(msg);
    }
    const data = await resp.json();
    _allInvoices = Array.isArray(data) ? data : [];

    renderYearFilter(_allInvoices);
    _renderAll(_allInvoices, _activeYear);

    if (loadingEl) loadingEl.style.display = "none";
    if (bodyEl)    bodyEl.style.display    = "block";
  } catch (err) {
    console.error("[dashboard]", err);
    if (loadingEl) loadingEl.innerHTML = `<span style="color:var(--red)">Chyba: ${escHtml(err.message)}</span>`;
  }
}

function _filterByYear(invoices, year) {
  if (!year) return invoices;
  return invoices.filter(i => (i.issued_at || i.updated_at || "").startsWith(year));
}

function _renderAll(invoices, year) {
  const filtered = _filterByYear(invoices, year);
  renderKPIs(filtered);
  renderRevenueChart(filtered, year);
  renderStatusChart(filtered);
  renderAging(filtered);
  renderRecent(invoices);  // recent shows all, not filtered
}

function renderYearFilter(invoices) {
  const el = document.getElementById("dashYearFilter");
  if (!el) return;

  const years = [...new Set(
    invoices
      .map(i => (i.issued_at || i.updated_at || "").slice(0, 4))
      .filter(y => /^\d{4}$/.test(y))
  )].sort().reverse();

  if (years.length <= 1) { el.innerHTML = ""; return; }

  el.innerHTML = ["all", ...years].map(y => {
    const label  = y === "all" ? "Vše" : y;
    const active = (y === "all" ? _activeYear === null : _activeYear === y) ? " active" : "";
    return `<button class="dash-yr-btn${active}" data-year="${y}">${label}</button>`;
  }).join("");

  el.querySelectorAll(".dash-yr-btn").forEach(btn =>
    btn.addEventListener("click", () => {
      _activeYear = btn.dataset.year === "all" ? null : btn.dataset.year;
      el.querySelectorAll(".dash-yr-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      _renderAll(_allInvoices, _activeYear);
    })
  );
}

/* ── Internal helpers ───────────────────────────────────────── */

function _issuedMonth(inv) {
  return inv.issued_at ? inv.issued_at.slice(0, 7) : null;
}

function _outstanding(inv) {
  return Math.max(0, (inv.total || 0) - (inv.paid_total || 0));
}

function _last12Months() {
  const now = new Date();
  return Array.from({ length: 12 }, (_, i) => {
    const d = new Date(now.getFullYear(), now.getMonth() - (11 - i), 1);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
  });
}

function _daysPastDue(inv) {
  if (!inv.due_date) return null;
  const today = new Date(); today.setHours(0, 0, 0, 0);
  return Math.floor((today - new Date(inv.due_date)) / 86400000);
}

function _czk(v, compact) {
  if (compact) return new Intl.NumberFormat("cs-CZ", { notation: "compact" }).format(v) + " Kč";
  return fmtNum(v) + " Kč";
}

/* ── KPI cards ─────────────────────────────────────────────── */

function renderKPIs(invoices) {
  const now    = new Date();
  const yyyyMM = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  const yyyy   = String(now.getFullYear());

  const activeStatuses = ["issued", "sent", "overdue"];

  const outstandingTotal = invoices
    .filter(i => activeStatuses.includes(i.status))
    .reduce((s, i) => s + _outstanding(i), 0);

  const overdueTotal = invoices
    .filter(i => i.status === "overdue")
    .reduce((s, i) => s + _outstanding(i), 0);

  const monthInvoiced = invoices
    .filter(i => !["draft", "cancelled"].includes(i.status) && _issuedMonth(i) === yyyyMM)
    .reduce((s, i) => s + (i.total || 0), 0);

  const ytdInvoiced = invoices
    .filter(i => !["draft", "cancelled"].includes(i.status) && (i.issued_at || "").startsWith(yyyy))
    .reduce((s, i) => s + (i.total || 0), 0);

  _setKPI("kpiOutstanding", outstandingTotal);
  _setKPI("kpiOverdue",     overdueTotal, overdueTotal > 0 ? "danger" : "zero");
  _setKPI("kpiMonth",       monthInvoiced);
  _setKPI("kpiYTD",         ytdInvoiced);
}

function _setKPI(id, value, modifier) {
  const card = document.getElementById(id);
  if (!card) return;
  const valEl = card.querySelector(".kpi-value");
  if (!valEl) return;
  valEl.textContent = _czk(value);
  valEl.className   = "kpi-value" + (modifier ? ` kpi-${modifier}` : "");
}

/* ── Monthly revenue chart ──────────────────────────────────── */

function renderRevenueChart(invoices, year) {
  const months = year
    ? Array.from({ length: 12 }, (_, i) => `${year}-${String(i + 1).padStart(2, "0")}`)
    : _last12Months();
  const labels = months.map(m => {
    const [y, mo] = m.split("-");
    return `${CS_MONTHS[parseInt(mo, 10) - 1]} ${y.slice(2)}`;
  });

  const invoiced = months.map(m =>
    invoices
      .filter(i => !["draft","cancelled"].includes(i.status) && _issuedMonth(i) === m)
      .reduce((s, i) => s + (i.total || 0), 0)
  );

  const collected = months.map(m =>
    invoices
      .filter(i => i.status === "paid" && _issuedMonth(i) === m)
      .reduce((s, i) => s + (i.paid_total || 0), 0)
  );

  const canvas = document.getElementById("chartRevenue");
  if (!canvas) return;
  if (_revenueChart) { _revenueChart.destroy(); _revenueChart = null; }

  _revenueChart = new Chart(canvas, {
    type: "bar",
    data: {
      labels,
      datasets: [
        {
          label: "Fakturováno",
          data: invoiced,
          backgroundColor: "rgba(33,85,205,0.15)",
          borderColor: "rgba(33,85,205,0.75)",
          borderWidth: 1.5,
          borderRadius: 3,
          order: 2,
        },
        {
          label: "Uhrazeno",
          data: collected,
          backgroundColor: "rgba(26,122,74,0.15)",
          borderColor: "rgba(26,122,74,0.75)",
          borderWidth: 1.5,
          borderRadius: 3,
          order: 1,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: "top",
          labels: { font: { family: "'DM Sans', sans-serif", size: 11 }, boxWidth: 10, padding: 14 },
        },
        tooltip: {
          callbacks: {
            label: ctx => ` ${ctx.dataset.label}: ${_czk(ctx.parsed.y)}`,
          },
        },
      },
      scales: {
        x: {
          grid: { display: false },
          ticks: { font: { family: "'DM Sans', sans-serif", size: 10 } },
        },
        y: {
          grid: { color: "rgba(0,0,0,0.04)" },
          ticks: {
            font: { family: "'DM Mono', monospace", size: 10 },
            callback: v => _czk(v, true),
          },
          beginAtZero: true,
        },
      },
    },
  });
}

/* ── Status donut chart ─────────────────────────────────────── */

function renderStatusChart(invoices) {
  const actual = invoices.filter(i => i.doc_type !== "credit_note");
  const counts = {}, totals = {};
  for (const inv of actual) {
    counts[inv.status] = (counts[inv.status] || 0) + 1;
    totals[inv.status] = (totals[inv.status] || 0) + (inv.total || 0);
  }

  const statuses = Object.keys(counts);
  if (!statuses.length) return;

  const canvas = document.getElementById("chartStatus");
  if (!canvas) return;
  if (_statusChart) { _statusChart.destroy(); _statusChart = null; }

  _statusChart = new Chart(canvas, {
    type: "doughnut",
    data: {
      labels: statuses.map(s => STATUS_LABELS[s] || s),
      datasets: [{
        data:            statuses.map(s => counts[s]),
        backgroundColor: statuses.map(s => DASH_STATUS_COLORS[s] || "#ccc"),
        borderWidth: 2,
        borderColor: "#FAFAF8",
        hoverOffset: 4,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: "62%",
      plugins: {
        legend: {
          position: "bottom",
          labels: { font: { family: "'DM Sans', sans-serif", size: 11 }, boxWidth: 10, padding: 10 },
        },
        tooltip: {
          callbacks: {
            label: ctx => {
              const s = statuses[ctx.dataIndex];
              return ` ${ctx.label}: ${ctx.parsed} ks · ${_czk(totals[s])}`;
            },
          },
        },
      },
    },
  });
}

/* ── Aging analysis ─────────────────────────────────────────── */

function renderAging(invoices) {
  const outstanding = invoices.filter(i => ["issued", "sent", "overdue"].includes(i.status));

  const buckets = [
    { label: "Ještě nesplatné", cssVar: "--blue",  items: [] },
    { label: "1–30 dní",        cssVar: "--amber", items: [] },
    { label: "31–60 dní",       cssVar: "--red",   items: [] },
    { label: "61–90 dní",       cssVar: "--red",   items: [] },
    { label: "90+ dní",         cssVar: "--red",   items: [] },
  ];

  for (const inv of outstanding) {
    const d = _daysPastDue(inv);
    if (d === null || d <= 0) buckets[0].items.push(inv);
    else if (d <= 30)         buckets[1].items.push(inv);
    else if (d <= 60)         buckets[2].items.push(inv);
    else if (d <= 90)         buckets[3].items.push(inv);
    else                      buckets[4].items.push(inv);
  }

  const amounts = buckets.map(b => b.items.reduce((s, i) => s + _outstanding(i), 0));
  const maxAmt  = Math.max(1, ...amounts);

  const container = document.getElementById("dashAging");
  if (!container) return;

  const rows = buckets
    .map((b, idx) => {
      const amt = amounts[idx];
      const cnt = b.items.length;
      if (cnt === 0) return "";
      const pct = Math.round(amt / maxAmt * 100);
      return `
        <div class="aging-row">
          <div class="aging-label">${escHtml(b.label)}</div>
          <div class="aging-bar-wrap">
            <div class="aging-bar" style="width:${pct}%;background:var(${b.cssVar})"></div>
          </div>
          <div class="aging-meta">
            <span class="aging-count">${cnt}&nbsp;fakt.</span>
            <span class="aging-amount mono">${_czk(amt)}</span>
          </div>
        </div>`;
    })
    .join("");

  container.innerHTML = rows || '<p class="dash-empty">Žádné pohledávky ✓</p>';
}

/* ── Recent invoices table ──────────────────────────────────── */

function renderRecent(invoices) {
  const container = document.getElementById("dashRecent");
  if (!container) return;

  const sorted = [...invoices]
    .sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""))
    .slice(0, 8);

  if (!sorted.length) {
    container.innerHTML = '<p class="dash-empty">Žádné faktury</p>';
    return;
  }

  container.innerHTML = `
    <table class="dash-table">
      <thead><tr>
        <th>Číslo</th>
        <th>Stav</th>
        <th class="ta-r">Celkem</th>
        <th class="ta-r">Zbývá</th>
        <th>Datum</th>
      </tr></thead>
      <tbody>
        ${sorted.map(r => {
          const bal  = Math.max(0, (r.total || 0) - (r.paid_total || 0));
          const date = (r.issued_at || r.updated_at || "").slice(0, 10);
          const balHtml = r.status === "paid"
            ? `<span class="dash-paid-mark">uhrazeno</span>`
            : `<span class="mono">${_czk(bal)}</span>`;
          return `
            <tr class="dash-tr" data-id="${r.id}">
              <td class="mono">${escHtml(r.invoice_number || "—")}</td>
              <td><span class="dr-status ${escHtml(r.status)}">${escHtml(STATUS_LABELS[r.status] || r.status)}</span></td>
              <td class="mono ta-r">${_czk(r.total || 0)}</td>
              <td class="ta-r">${balHtml}</td>
              <td class="dash-date">${escHtml(date)}</td>
            </tr>`;
        }).join("")}
      </tbody>
    </table>`;

  container.querySelectorAll(".dash-tr").forEach(row => {
    row.addEventListener("click", () => {
      closeDashboard();
      loadInvoice(parseInt(row.dataset.id, 10));
    });
  });
}
