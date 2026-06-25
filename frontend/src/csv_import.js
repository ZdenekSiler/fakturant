/**
 * csv_import.js — Bulk-load invoice items from a CSV file.
 *
 * Flow:
 *   1. User clicks "Import CSV" → hidden file input opens
 *   2. File is read and parsed (handles quoted fields with embedded newlines)
 *   3. Mapping modal shows detected headers + auto-suggested column assignments
 *   4. User adjusts mapping, sets default VAT / unit / price-type, clicks Import
 *   5. Items are added via addItemToState() → renderItemList() + schedulePreview()
 */

(function () {
  "use strict";

  // ── Column-name hints for auto-detection ──────────────────────────────────

  const AUTO_HINTS = {
    item_date:   ["date", "datum", "day", "den"],
    project:     ["project", "projekt"],
    unit:        ["task", "ukol", "úkol", "ukol"],
    description: ["detail", "desc", "description", "popis", "note", "poznamka", "poznámka"],
    quantity:    ["hours", "hod", "qty", "quantity", "mnozstvi", "množství", "amount", "pocet"],
    unit_price:  ["price", "cena", "rate", "sazba", "unitprice", "unit_price"],
  };

  const FIELD_LABELS = {
    item_date:   "Datum",
    project:     "Projekt",
    unit:        "Úkol / jednotka",
    description: "Popis",
    quantity:    "Množství / hodiny",
    unit_price:  "Cena",
  };

  // ── RFC-4180 CSV parser (handles quoted fields with embedded newlines) ─────

  function parseCsv(text) {
    // Normalise line endings
    const src = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    const records = [];
    let i = 0;

    function parseField() {
      if (src[i] === '"') {
        // Quoted field
        i++;
        let val = "";
        while (i < src.length) {
          if (src[i] === '"') {
            i++;
            if (src[i] === '"') { val += '"'; i++; }   // escaped quote
            else break;                                   // end of field
          } else {
            val += src[i++];
          }
        }
        return val;
      }
      // Unquoted field — read until comma or newline
      let val = "";
      while (i < src.length && src[i] !== "," && src[i] !== "\n") {
        val += src[i++];
      }
      return val.trim();
    }

    while (i < src.length) {
      const record = [];
      while (true) {
        record.push(parseField());
        if (i >= src.length || src[i] === "\n") { i++; break; }
        i++;   // skip comma
      }
      // Skip blank lines (single empty field)
      if (record.length === 1 && record[0] === "") continue;
      records.push(record);
    }

    if (records.length < 2) return { headers: [], rows: [] };

    const headers = records[0];
    const rows = records.slice(1).map(rec => {
      const obj = {};
      headers.forEach((h, idx) => { obj[h] = rec[idx] ?? ""; });
      return obj;
    });
    return { headers, rows };
  }

  // ── Auto-detect mapping ───────────────────────────────────────────────────

  function detectMapping(headers) {
    const lower = headers.map(h => h.toLowerCase().replace(/[^a-záčďéěíňóřšťúůýž]/g, ""));
    const mapping = {};
    Object.entries(AUTO_HINTS).forEach(([field, hints]) => {
      const idx = lower.findIndex(h => hints.some(hint => h.includes(hint)));
      mapping[field] = idx >= 0 ? headers[idx] : "";
    });
    return mapping;
  }

  // ── State ──────────────────────────────────────────────────────────────────

  let _parsed = null;   // { headers, rows }

  // ── Modal helpers ──────────────────────────────────────────────────────────

  function getMapping() {
    const m = {};
    Object.keys(FIELD_LABELS).forEach(field => {
      const el = document.getElementById(`csvMap_${field}`);
      m[field] = el ? el.value : "";
    });
    return m;
  }

  function getDefaultVat() {
    return parseFloat(document.getElementById("csvDefaultVat")?.value) || 0;
  }

  function getDefaultUnit() {
    return document.getElementById("csvDefaultUnit")?.value?.trim() || "hod";
  }

  function priceIsTotal() {
    const el = document.querySelector('input[name="csvPriceType"]:checked');
    return el?.value === "total";
  }

  function round2(n) { return Math.round(n * 100) / 100; }

  function updateImportButton() {
    const btn = document.getElementById("csvModalImport");
    if (btn && _parsed) {
      btn.textContent = `Importovat ${_parsed.rows.length} řádků`;
    }
  }

  // ── Open modal ─────────────────────────────────────────────────────────────

  function openMappingModal(file, parsed) {
    _parsed = parsed;
    const { headers, rows } = parsed;

    // File info line
    const info = document.getElementById("csvFileInfo");
    if (info) info.textContent = `${rows.length} řádků z „${file.name}"`;

    // Build mapping selects
    const grid = document.getElementById("csvMapGrid");
    if (!grid) return;
    grid.innerHTML = "";
    const defaultMap = detectMapping(headers);

    Object.entries(FIELD_LABELS).forEach(([field, label]) => {
      const row = document.createElement("div");
      row.className = "csv-map-row";

      const lbl = document.createElement("span");
      lbl.className = "csv-map-label";
      lbl.textContent = label;

      const sel = document.createElement("select");
      sel.id = `csvMap_${field}`;
      sel.className = "csv-map-select";

      const none = document.createElement("option");
      none.value = "";
      none.textContent = "(žádný)";
      sel.appendChild(none);

      headers.forEach(h => {
        const opt = document.createElement("option");
        opt.value = h;
        opt.textContent = h;
        if (defaultMap[field] === h) opt.selected = true;
        sel.appendChild(opt);
      });

      row.appendChild(lbl);
      row.appendChild(sel);
      grid.appendChild(row);
    });

    updateImportButton();

    // Auto-detect price type: if price column name suggests "total" (not "rate/sazba")
    const priceCol = (defaultMap.unit_price || "").toLowerCase();
    const looksLikeTotal = priceCol.includes("price") || priceCol.includes("cena");
    const totalRadio = document.querySelector('input[name="csvPriceType"][value="total"]');
    if (totalRadio && looksLikeTotal) totalRadio.checked = true;

    document.getElementById("csvMappingOverlay")?.classList.add("open");
  }

  function closeMappingModal() {
    document.getElementById("csvMappingOverlay")?.classList.remove("open");
    // Reset file input so the same file can be re-imported
    const fileInput = document.getElementById("csvFile");
    if (fileInput) fileInput.value = "";
    _parsed = null;
  }

  // ── Execute import ─────────────────────────────────────────────────────────

  function doImport() {
    if (!_parsed) return;
    const { rows } = _parsed;
    const map = getMapping();
    const defaultVat  = getDefaultVat();
    const defaultUnit = getDefaultUnit();
    const totalPrice  = priceIsTotal();

    let count = 0;
    rows.forEach(row => {
      const qty = parseFloat(row[map.quantity]) || 1;
      let unitPrice = parseFloat(row[map.unit_price]) || 0;
      if (totalPrice && qty > 0) unitPrice = round2(unitPrice / qty);

      const description = map.description
        ? (row[map.description] || "").replace(/\n/g, " ").trim()
        : "";
      const unit = map.unit ? (row[map.unit] || "").trim() || defaultUnit : defaultUnit;

      addItemToState({
        item_date:   map.item_date   ? (row[map.item_date]   || "").trim() : "",
        project:     map.project     ? (row[map.project]     || "").trim() : "",
        unit,
        description,
        quantity:    qty,
        unit_price:  unitPrice,
        vat_rate:    defaultVat,
      });
      count++;
    });

    if (typeof renderItemList  === "function") renderItemList();
    if (typeof schedulePreview === "function") schedulePreview(0);
    if (typeof scheduleSave    === "function") scheduleSave();

    closeMappingModal();

    // Show brief success status
    const badge = document.getElementById("itemCountBadge");
    if (badge) {
      const orig = badge.textContent;
      badge.textContent = `✓ ${count} importováno`;
      setTimeout(() => {
        // Reset to accurate count
        if (typeof renderItemList === "function") renderItemList();
      }, 1800);
    }
  }

  // ── Event wiring ───────────────────────────────────────────────────────────

  function init() {
    const importBtn  = document.getElementById("importCsvBtn");
    const fileInput  = document.getElementById("csvFile");
    const closeBtn   = document.getElementById("csvModalClose");
    const cancelBtn  = document.getElementById("csvModalCancel");
    const importExec = document.getElementById("csvModalImport");
    const overlay    = document.getElementById("csvMappingOverlay");

    importBtn?.addEventListener("click", () => fileInput?.click());

    fileInput?.addEventListener("change", () => {
      const file = fileInput.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = e => {
        const parsed = parseCsv(e.target.result);
        if (!parsed.headers.length) {
          alert("Soubor neobsahuje platná CSV data (chybí záhlaví).");
          return;
        }
        openMappingModal(file, parsed);
      };
      reader.readAsText(file, "utf-8");
    });

    closeBtn?.addEventListener("click",  closeMappingModal);
    cancelBtn?.addEventListener("click", closeMappingModal);
    overlay?.addEventListener("click", e => {
      if (e.target === overlay) closeMappingModal();
    });
    importExec?.addEventListener("click", doImport);

    document.addEventListener("keydown", e => {
      if (e.key === "Escape" && overlay?.classList.contains("open")) {
        closeMappingModal();
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
