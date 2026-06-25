/**
 * ares.js — Czech ARES company registry integration
 *
 * Two search modes for both supplier and customer:
 *   1. IČO lookup   — pure digits in #<party>_ico_search → direct lookup
 *   2. Name search  — text in #<party>_ico_search OR #<party>_name_search
 *                     → debounced dropdown / auto-fill
 */

(function () {
  "use strict";

  const PARTIES = ["supplier", "customer"];

  // ── DOM helpers ────────────────────────────────────────────────────────────

  function setV(id, value) {
    const el = document.getElementById(id);
    if (el) el.value = value ?? "";
  }

  function getV(id) {
    const el = document.getElementById(id);
    return el ? el.value.trim() : "";
  }

  function setStatus(party, msg, isError) {
    const el = document.getElementById(`${party}_status`);
    if (!el) return;
    el.textContent = msg;
    el.style.color = isError ? "#e05050" : "#5a9a5a";
  }

  function clearStatus(party) {
    const el = document.getElementById(`${party}_status`);
    if (el) el.textContent = "";
  }

  function openDropdown(party, results) {
    const dd = document.getElementById(`${party}_dropdown`);
    if (!dd) return;
    dd.innerHTML = "";
    if (!results.length) { dd.classList.remove("open"); return; }

    results.forEach(r => {
      const item = document.createElement("div");
      item.className = "ares-dd-item";
      item.innerHTML =
        `<div class="ares-dd-name">${escHtml(r.name)}</div>` +
        `<div class="ares-dd-meta">IČO ${escHtml(r.ico)}${r.address ? " · " + escHtml(r.address) : ""}</div>`;
      item.addEventListener("mousedown", e => {
        e.preventDefault();
        fillParty(party, r);
        setStatus(party, `✓ ${r.name}`, false);
      });
      dd.appendChild(item);
    });
    dd.classList.add("open");
  }

  function closeDropdown(party) {
    const dd = document.getElementById(`${party}_dropdown`);
    if (dd) { dd.innerHTML = ""; dd.classList.remove("open"); }
  }

  function escHtml(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  // ── Fill form ──────────────────────────────────────────────────────────────

  function fillParty(party, result) {
    setV(`${party}_name`,       result.name);
    setV(`${party}_ico`,        result.ico);
    setV(`${party}_dic`,        result.dic);
    setV(`${party}_address`,    result.address);
    setV(`${party}_ico_search`, result.ico);
    setV(`${party}_name_search`, "");
    closeDropdown(party);
    if (typeof schedulePreview === "function") schedulePreview(0);
    if (typeof scheduleSave    === "function") scheduleSave();
    if (party === "customer" && typeof showSaveContactBanner === "function") showSaveContactBanner();
    if (party === "supplier" && typeof hideSaveContactBanner === "function") hideSaveContactBanner();
  }

  // ── Main search (button + Enter) ───────────────────────────────────────────
  // Accepts IČO (digits) or company name — auto-detects

  async function handleSearch(party) {
    const q = getV(`${party}_ico_search`);
    if (!q) { setStatus(party, "Zadejte IČO nebo název firmy", true); return; }

    if (/^\d{1,8}$/.test(q)) {
      await searchByIco(party, q);
    } else {
      await searchByName(party, q, /* autoFill= */ true);
    }
  }

  async function searchByIco(party, ico) {
    setStatus(party, "Hledám…", false);
    try {
      const resp = await fetch(`/api/ares/ico/${encodeURIComponent(ico)}`);
      if (resp.status === 404) { setStatus(party, `IČO ${ico} nenalezeno v ARES`, true); return; }
      if (!resp.ok)            { setStatus(party, "Chyba ARES, zkuste znovu", true); return; }
      const data = await resp.json();
      fillParty(party, data);
      setStatus(party, `✓ ${data.name}`, false);
    } catch {
      setStatus(party, "Nepodařilo se spojit s ARES", true);
    }
  }

  // autoFill=true  → 1 result fills directly; >1 shows dropdown + status hint
  // autoFill=false → always shows dropdown (used by live name-search input)
  async function searchByName(party, q, autoFill) {
    setStatus(party, "Hledám…", false);
    try {
      const resp = await fetch(`/api/ares/search?q=${encodeURIComponent(q)}&n=8`);
      if (!resp.ok) { setStatus(party, "Chyba ARES, zkuste znovu", true); return; }
      const results = await resp.json();

      if (!results.length) {
        closeDropdown(party);
        setStatus(party, `Firma „${q}" nenalezena`, true);
        return;
      }

      if (autoFill && results.length === 1) {
        fillParty(party, results[0]);
        setStatus(party, `✓ ${results[0].name}`, false);
      } else {
        openDropdown(party, results);
        clearStatus(party);
      }
    } catch {
      setStatus(party, "Nepodařilo se spojit s ARES", true);
    }
  }

  // ── Live name-search input (separate text field) ───────────────────────────

  const nameSearchTimers = {};

  function scheduleLiveSearch(party) {
    clearTimeout(nameSearchTimers[party]);
    nameSearchTimers[party] = setTimeout(async () => {
      const q = getV(`${party}_name_search`);
      if (q.length < 2) { closeDropdown(party); clearStatus(party); return; }
      await searchByName(party, q, /* autoFill= */ false);
    }, 380);
  }

  // ── Event binding ──────────────────────────────────────────────────────────

  function init() {
    PARTIES.forEach(party => {

      // "Načíst z ARES" button
      document.getElementById(`${party}_ares_btn`)
        ?.addEventListener("click", () => handleSearch(party));

      // IČO/name combo input — Enter triggers search
      document.getElementById(`${party}_ico_search`)
        ?.addEventListener("keydown", e => {
          if (e.key === "Enter") { e.preventDefault(); handleSearch(party); }
        });

      // Separate live name-search input
      const nameEl = document.getElementById(`${party}_name_search`);
      if (nameEl) {
        nameEl.addEventListener("input",   () => scheduleLiveSearch(party));
        nameEl.addEventListener("blur",    () => setTimeout(() => closeDropdown(party), 150));
        nameEl.addEventListener("keydown", e => {
          if (e.key === "Escape") { closeDropdown(party); nameEl.blur(); }
        });
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
