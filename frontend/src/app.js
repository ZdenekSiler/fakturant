/**
 * app.js — Fakturant v4
 * Sequential numbering · Status lifecycle · Payments · Credit notes
 */

/* ── State ─────────────────────────────────── */
let currentTemplate  = "modern";
let _hasUnsaved      = false;
let logoB64          = null;
let signatureB64     = null;
let previewTimer     = null;
let saveTimer        = null;
let seqCheckTimer    = null;
let items            = [];
let itemIdSeq        = 0;
let editingItemId    = null;
let currentInvoiceId = null;
let isFirstSave      = true;   // true until the invoice is committed to the sequence
let currentTags      = [];     // string[] — tags for the current invoice

/* ── Auth helper ────────────────────────────────── */
// Used for all /api/invoices/* calls (protected). Returns null and redirects on 401.
async function apiFetch(url, options = {}) {
  const resp = await fetch(url, { credentials: "include", ...options });
  if (resp.status === 401) { window.location.href = "/login"; return null; }
  return resp;
}

/* ── Boot ──────────────────────────────────── */
document.addEventListener("DOMContentLoaded", async () => {
  const today = new Date();
  const due   = new Date(today); due.setDate(due.getDate() + 14);
  const fmt   = d => d.toISOString().split("T")[0];

  document.getElementById("issue_date").value      = fmt(today);
  document.getElementById("duzp").value            = fmt(today);
  document.getElementById("due_date").value        = fmt(due);
  // Pre-fill the next sequential number from the server
  await prefillNextNumber();
  document.getElementById("variable_symbol").value = document.getElementById("invoice_number").value.replace(/\D/g, "");

  addItemToState({ description:"Vývoj webové aplikace", project:"Projekt Alpha",
    item_date:fmt(today), quantity:10, unit:"hod", unit_price:2000, vat_rate:21 });
  addItemToState({ description:"UX konzultace", project:"Projekt Alpha",
    item_date:fmt(today), quantity:3,  unit:"hod", unit_price:1800, vat_rate:21 });

  renderItemList();
  bindEvents();
  schedulePreview();

  // Show authenticated-only controls when a valid session exists
  fetch("/auth/me", { credentials: "include" }).then(r => {
    if (r.ok) {
      document.getElementById("accountBtns").style.display  = "flex";
      document.getElementById("dashboardBtn").style.display = "";
      loadProfile();
    }
  });
});

/* ── Sequential number ─────────────────────── */
async function prefillNextNumber(prefix) {
  try {
    const p = prefix || guessPrefix();
    const resp = await fetch(`/api/sequence/next?prefix=${encodeURIComponent(p)}`);
    if (!resp.ok) return;
    const { number } = await resp.json();
    document.getElementById("invoice_number").value = number;
    const chip = document.getElementById("doc_chip_num");
    if (chip) chip.value = number;
    clearSeqWarning();
  } catch (_) {}
}

function guessPrefix() {
  // Infer prefix from the current invoice_number field (e.g. "FA-2025-001" → "FA")
  const val = document.getElementById("invoice_number")?.value || "";
  const m = val.match(/^([A-Z]+)-/);
  return m ? m[1] : "FA";
}

async function checkSequenceGap(number) {
  if (!number) return;
  try {
    const resp = await fetch(`/api/sequence/check?number=${encodeURIComponent(number)}`);
    if (!resp.ok) return;
    const result = await resp.json();
    const warn = document.getElementById("seqWarning");
    if (!warn) return;
    if (result.gap > 0) {
      warn.textContent = `⚠ Číslo přeskočí ${result.gap} mezeru${result.gap > 1 ? "y" : ""}. Očekáváno: ${result.expected}`;
      warn.style.display = "block";
    } else if (result.gap < 0) {
      warn.textContent = `⚠ Toto číslo již bylo použito nebo je v minulosti (očekáváno: ${result.expected})`;
      warn.style.display = "block";
    } else {
      clearSeqWarning();
    }
  } catch (_) {}
}

function clearSeqWarning() {
  const warn = document.getElementById("seqWarning");
  if (warn) warn.style.display = "none";
}

/* ── Events ────────────────────────────────── */
function bindEvents() {
  // Template switcher
  document.getElementById("templateTabs").addEventListener("click", e => {
    const btn = e.target.closest(".tpl-btn");
    if (!btn) return;
    document.querySelectorAll(".tpl-btn").forEach(t => t.classList.remove("active"));
    btn.classList.add("active");
    currentTemplate = btn.dataset.tpl;
    schedulePreview(0);
  });

  document.getElementById("refreshBtn").addEventListener("click", doPreview);
  document.getElementById("pdfBtn").addEventListener("click", downloadPdf);
  document.getElementById("validateBtn").addEventListener("click", validateInvoice);
  document.getElementById("addItemBtn").addEventListener("click", () => openModal(null));

  // Dashboard
  document.getElementById("dashboardBtn").addEventListener("click", openDashboard);
  document.getElementById("dashClose").addEventListener("click", closeDashboard);

  // Profile
  document.getElementById("profileBtn").addEventListener("click", openProfile);
  document.getElementById("profileClose").addEventListener("click", closeProfile);
  document.getElementById("profileSaveBtn").addEventListener("click", saveProfile);

  // Contacts
  document.getElementById("pickContactBtn").addEventListener("click", openContactPicker);
  document.getElementById("contactsModalClose").addEventListener("click", closeContactPicker);
  document.getElementById("contactsModalCancel").addEventListener("click", closeContactPicker);
  document.getElementById("contactsModal").addEventListener("click", e => {
    if (e.target === document.getElementById("contactsModal")) closeContactPicker();
  });
  document.getElementById("addContactBtn").addEventListener("click", addContactManually);
  document.getElementById("contactSearch").addEventListener("input", e => renderContactsList(e.target.value));

  // Drawer
  document.getElementById("invoiceListBtn").addEventListener("click", openDrawer);
  document.getElementById("newInvoiceBtn").addEventListener("click", newInvoice);
  document.getElementById("drawerClose").addEventListener("click", closeDrawer);
  document.getElementById("drawerOverlay").addEventListener("click", e => {
    if (e.target === document.getElementById("drawerOverlay")) closeDrawer();
  });

  // Modal
  document.getElementById("modalClose").addEventListener("click", closeModal);
  document.getElementById("modalCancel").addEventListener("click", closeModal);
  document.getElementById("modalSave").addEventListener("click", saveModalItem);
  document.getElementById("modalOverlay").addEventListener("click", e => {
    if (e.target === document.getElementById("modalOverlay")) closeModal();
  });
  document.addEventListener("keydown", e => { if (e.key === "Escape") { closeDashboard(); closeProfile(); closeModal(); closeDrawer(); closeSidePanel(); closeStatusMenu(); closeContactPicker(); } });
  ["modalQty","modalPrice","modalVat"].forEach(id =>
    document.getElementById(id).addEventListener("input", updateModalPreview));

  // Invoice number — check gap on change
  document.getElementById("invoice_number").addEventListener("input", e => {
    const chip = document.getElementById("doc_chip_num");
    if (chip) chip.value = e.target.value;
    clearTimeout(seqCheckTimer);
    seqCheckTimer = setTimeout(() => checkSequenceGap(e.target.value), 600);
    markUnsaved();
  });

  // Status controls
  document.getElementById("statusPill").addEventListener("click", openStatusMenu);
  document.addEventListener("click", e => {
    if (!e.target.closest(".status-menu") && !e.target.closest("#statusPill")) closeStatusMenu();
  });

  // Payment panel
  document.getElementById("addPaymentBtn")?.addEventListener("click", openPaymentPanel);
  document.getElementById("paymentPanelClose")?.addEventListener("click", closeSidePanel);
  document.getElementById("paymentForm")?.addEventListener("submit", submitPayment);

  // Credit note button
  document.getElementById("creditNoteBtn")?.addEventListener("click", initCreditNote);
  document.getElementById("cloneBtn")?.addEventListener("click", cloneInvoice);

  // Logo
  const logoDrop = document.getElementById("logoDrop");
  const logoFile = document.getElementById("logoFile");
  logoDrop.addEventListener("click", e => { if (e.target.id === "removeLogo") return; logoFile.click(); });
  logoDrop.addEventListener("dragover", e => { e.preventDefault(); logoDrop.classList.add("dragover"); });
  logoDrop.addEventListener("dragleave", () => logoDrop.classList.remove("dragover"));
  logoDrop.addEventListener("drop", e => {
    e.preventDefault(); logoDrop.classList.remove("dragover");
    const f = e.dataTransfer.files[0];
    if (f && f.type.startsWith("image/")) handleLogo(f);
  });
  logoFile.addEventListener("change", () => { if (logoFile.files[0]) handleLogo(logoFile.files[0]); });
  document.getElementById("removeLogo").addEventListener("click", e => {
    e.stopPropagation(); logoB64 = null;
    document.getElementById("logoPreview").hidden = true;
    document.getElementById("logoPlaceholder").hidden = false;
    document.getElementById("removeLogo").hidden = true;
    schedulePreview();
  });

  // Signature
  const signatureDrop = document.getElementById("signatureDrop");
  const signatureFile = document.getElementById("signatureFile");
  signatureDrop.addEventListener("click", e => { if (e.target.id === "removeSignature") return; signatureFile.click(); });
  signatureDrop.addEventListener("dragover", e => { e.preventDefault(); signatureDrop.classList.add("dragover"); });
  signatureDrop.addEventListener("dragleave", () => signatureDrop.classList.remove("dragover"));
  signatureDrop.addEventListener("drop", e => {
    e.preventDefault(); signatureDrop.classList.remove("dragover");
    const f = e.dataTransfer.files[0];
    if (f && f.type.startsWith("image/")) handleSignature(f);
  });
  signatureFile.addEventListener("change", () => { if (signatureFile.files[0]) handleSignature(signatureFile.files[0]); });
  document.getElementById("removeSignature").addEventListener("click", e => {
    e.stopPropagation(); signatureB64 = null;
    document.getElementById("signaturePreview").hidden = true;
    document.getElementById("signaturePlaceholder").hidden = false;
    document.getElementById("removeSignature").hidden = true;
    schedulePreview();
  });

  // Auto-preview on any form input; save is now manual (Uložit button / Ctrl+S)
  document.querySelector(".form-panel").addEventListener("input", () => {
    schedulePreview();
    markUnsaved();
  });

  // Save button + Ctrl+S
  document.getElementById("saveBtn").addEventListener("click", doSave);
  document.addEventListener("keydown", e => {
    if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); doSave(); }
  });

  // Re-render item list when supplier DIC changes (affects VAT display)
  document.getElementById("supplier_dic")?.addEventListener("input", () => {
    renderItemList();
  });
}

/* ── Logo ──────────────────────────────────── */
function handleLogo(file) {
  const reader = new FileReader();
  reader.onload = e => {
    logoB64 = e.target.result;
    document.getElementById("logoPreview").src = logoB64;
    document.getElementById("logoPreview").hidden = false;
    document.getElementById("logoPlaceholder").hidden = true;
    document.getElementById("removeLogo").hidden = false;
    schedulePreview(); markUnsaved();
  };
  reader.readAsDataURL(file);
}

/* ── Signature ─────────────────────────────── */
function handleSignature(file) {
  const reader = new FileReader();
  reader.onload = e => {
    signatureB64 = e.target.result;
    document.getElementById("signaturePreview").src = signatureB64;
    document.getElementById("signaturePreview").hidden = false;
    document.getElementById("signaturePlaceholder").hidden = true;
    document.getElementById("removeSignature").hidden = false;
    schedulePreview(); markUnsaved();
  };
  reader.readAsDataURL(file);
}

/* ── Item state ────────────────────────────── */
function addItemToState(data) {
  items.push({
    id:          ++itemIdSeq,
    description: data.description || "",
    project:     data.project     || "",
    item_date:   data.item_date   || "",
    quantity:    parseFloat(data.quantity)   || 1,
    unit:        data.unit        || "ks",
    unit_price:  parseFloat(data.unit_price) || 0,
    vat_rate:    parseFloat(data.vat_rate)   || 21,
  });
}
function updateItemInState(id, data) {
  const idx = items.findIndex(i => i.id === id);
  if (idx !== -1) items[idx] = { ...items[idx], ...data };
}
function removeItemFromState(id) { items = items.filter(i => i.id !== id); }
function isVatPayer() {
  return !!(document.getElementById("supplier_dic")?.value?.trim());
}

function calcItem(item) {
  const base = round2(item.quantity * item.unit_price);
  const vat  = isVatPayer() ? round2(base * item.vat_rate / 100) : 0;
  return { base, vat, total: round2(base + vat) };
}

/* ── Item list render ──────────────────────── */
function renderItemList() {
  const container = document.getElementById("itemList");
  container.innerHTML = "";
  if (items.length === 0) {
    container.innerHTML = '<div class="items-empty">Žádné položky — klikněte „Přidat položku"</div>';
    return;
  }
  items.forEach(item => {
    const { total } = calcItem(item);
    const row = document.createElement("div");
    row.className = "item-row"; row.dataset.id = item.id;
    let sub = "";
    if (item.project && item.item_date) sub = `<span class="ir-proj">${escHtml(item.project)}</span> · ${item.item_date}`;
    else if (item.project) sub = `<span class="ir-proj">${escHtml(item.project)}</span>`;
    else if (item.item_date) sub = item.item_date;
    const vatChip = isVatPayer()
      ? `<div><span class="ir-vat-chip">${item.vat_rate|0}&nbsp;%</span></div>`
      : `<div></div>`;
    row.innerHTML =
      `<div class="ir-desc"><div class="ir-desc-main">${escHtml(item.description)||'<em style="color:var(--ink4)">bez popisu</em>'}</div>`+
      (sub?`<div class="ir-desc-sub">${sub}</div>`:"") +`</div>`+
      `<div class="ir-num">${fmtNum(item.quantity)}&nbsp;${escHtml(item.unit)}</div>`+
      `<div class="ir-num ir-right">${fmtNum(item.unit_price)}</div>`+
      vatChip+
      `<div class="ir-total">${fmtNum(total)}</div>`+
      `<div class="ir-actions"><button class="btn-ir edit" title="Upravit položku"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg></button><button class="btn-ir del" title="Odstranit položku">×</button></div>`;
    row.querySelector(".btn-ir.edit").addEventListener("click", () => openModal(item.id));
    row.querySelector(".btn-ir.del").addEventListener("click", () => {
      removeItemFromState(item.id); renderItemList(); schedulePreview(); markUnsaved();
    });
    container.appendChild(row);
  });
}

/* ── Modal ─────────────────────────────────── */
function openModal(itemId) {
  editingItemId = itemId;
  document.getElementById("modalTitle").textContent = itemId ? "Upravit položku" : "Přidat položku";
  if (itemId) {
    const item = items.find(i => i.id === itemId); if (!item) return;
    document.getElementById("modalDesc").value    = item.description;
    document.getElementById("modalProject").value = item.project;
    document.getElementById("modalDate").value    = item.item_date;
    document.getElementById("modalQty").value     = item.quantity;
    document.getElementById("modalUnit").value    = item.unit;
    document.getElementById("modalPrice").value   = item.unit_price;
    document.getElementById("modalVat").value     = item.vat_rate;
  } else {
    document.getElementById("modalDesc").value    = "";
    document.getElementById("modalProject").value = "";
    document.getElementById("modalDate").value    = document.getElementById("issue_date").value || "";
    document.getElementById("modalQty").value     = "1";
    document.getElementById("modalUnit").value    = "hod";
    document.getElementById("modalPrice").value   = "";
    document.getElementById("modalVat").value     = "21";
  }
  // Show/hide DPH field and label based on VAT payer status
  const vatField = document.getElementById("vatRateField");
  const vatLine  = document.getElementById("mlpVat");
  const priceLabel = document.getElementById("modalPriceLabel");
  if (isVatPayer()) {
    if (vatField)    vatField.style.display = "";
    if (vatLine)     vatLine.style.display  = "";
    if (priceLabel)  priceLabel.innerHTML   = 'Cena za jednotku bez DPH (Kč) <span class="req">*</span>';
  } else {
    if (vatField)    vatField.style.display = "none";
    if (vatLine)     vatLine.style.display  = "none";
    if (priceLabel)  priceLabel.innerHTML   = 'Cena za jednotku (Kč) <span class="req">*</span>';
  }

  updateModalPreview();
  document.getElementById("modalOverlay").classList.add("open");
  setTimeout(() => document.getElementById("modalDesc").focus(), 80);
}
function closeModal() { document.getElementById("modalOverlay").classList.remove("open"); editingItemId = null; }
function saveModalItem() {
  const data = {
    description: document.getElementById("modalDesc").value.trim(),
    project:     document.getElementById("modalProject").value.trim(),
    item_date:   document.getElementById("modalDate").value,
    quantity:    parseFloat(document.getElementById("modalQty").value)   || 1,
    unit:        document.getElementById("modalUnit").value              || "ks",
    unit_price:  parseFloat(document.getElementById("modalPrice").value) || 0,
    vat_rate:    parseFloat(document.getElementById("modalVat").value)   || 21,
  };
  editingItemId !== null ? updateItemInState(editingItemId, data) : addItemToState(data);
  renderItemList(); schedulePreview(); markUnsaved(); closeModal();
}
function updateModalPreview() {
  const qty   = parseFloat(document.getElementById("modalQty").value)   || 0;
  const price = parseFloat(document.getElementById("modalPrice").value) || 0;
  const base  = round2(qty * price);
  const vatAmt = isVatPayer() ? round2(base * (parseFloat(document.getElementById("modalVat").value) || 0) / 100) : 0;
  const total  = round2(base + vatAmt);
  document.getElementById("mlpBase").textContent  = isVatPayer() ? `Základ: ${fmtNum(base)}` : `Celkem: ${fmtNum(base)}`;
  document.getElementById("mlpVat").textContent   = `DPH: ${fmtNum(vatAmt)}`;
  document.getElementById("mlpTotal").textContent = fmtNum(total);
}

/* ── Payload ────────────────────────────────── */
function buildPayload() {
  const val = id => (document.getElementById(id)?.value ?? "").trim();
  return {
    template:        currentTemplate,
    invoice_number:  val("invoice_number"),
    issue_date:      val("issue_date"),
    duzp:            val("duzp"),
    due_date:        val("due_date"),
    currency:        val("currency"),
    variable_symbol: val("variable_symbol"),
    bank_account:    val("bank_account"),
    iban:            val("iban"),
    swift:           val("swift"),
    supplier: { name:val("supplier_name"), email:val("supplier_email"), phone:val("supplier_phone"), ico:val("supplier_ico"), dic:val("supplier_dic"), address:val("supplier_address"), vat_payer:!!val("supplier_dic") },
    customer: { name:val("customer_name"), email:val("customer_email"), ico:val("customer_ico"), dic:val("customer_dic"), address:val("customer_address"), vat_payer:!!val("customer_dic") },
    items: items.map(i => ({ description:i.description, project:i.project, item_date:i.item_date, quantity:i.quantity, unit:i.unit, unit_price:i.unit_price, vat_rate:i.vat_rate })),
    notes:    val("notes"),
    tags:     [...currentTags],
    logo_b64:       logoB64 || null,
    signature_b64:  signatureB64 || null,
  };
}

/* ── Preview ────────────────────────────────── */
function schedulePreview(delay) { clearTimeout(previewTimer); previewTimer = setTimeout(doPreview, delay===undefined?450:delay); }
async function doPreview() {
  const frame = document.getElementById("previewFrame");
  const pill  = document.getElementById("loadingPill");
  pill.classList.add("visible"); frame.classList.add("loading");
  try {
    const resp = await fetch("/preview", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(buildPayload()) });
    if (!resp.ok) throw new Error(await resp.text());
    const html = await resp.text();
    const doc = frame.contentDocument || frame.contentWindow.document;
    doc.open(); doc.write(html); doc.close();
    setTimeout(fitFrame, 120);
  } catch (err) { console.error("[preview]", err); }
  finally { pill.classList.remove("visible"); frame.classList.remove("loading"); }
}
function fitFrame() {
  const f = document.getElementById("previewFrame");
  try { const h=f.contentDocument.documentElement.scrollHeight; if(h>200) f.style.height=h+"px"; } catch(_){}
}

/* ── Persistence ────────────────────────────── */
function markUnsaved() {
  if (_hasUnsaved) return;
  _hasUnsaved = true;
  document.getElementById("saveBtn")?.classList.add("dirty");
}
function markSaved() {
  _hasUnsaved = false;
  document.getElementById("saveBtn")?.classList.remove("dirty");
}

function scheduleSave(delay) { clearTimeout(saveTimer); saveTimer = setTimeout(doSave, delay===undefined?0:delay); }
async function doSave() {
  setSaveIndicator("saving");
  try {
    const resp = await fetch("/api/invoices/save", {
      credentials: "include",
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({ data:buildPayload(), invoice_id:currentInvoiceId, commit_sequence: isFirstSave && currentInvoiceId===null }),
    });
    if (resp.status === 401) { setSaveIndicator("login"); return; }
    if (resp.status === 409) { const d = await resp.json(); setSaveIndicator("error"); showToast(d.detail); return; }
    if (!resp.ok) throw new Error(await resp.text());
    const row = await resp.json();
    if (currentInvoiceId === null) { currentInvoiceId = row.id; isFirstSave = false; }
    markSaved();
    setSaveIndicator("saved");
    updateStatusPillFromRow(row);
  } catch (err) { console.error("[save]", err); setSaveIndicator("error"); }
}
function setSaveIndicator(state) {
  const el = document.getElementById("saveIndicator"); if (!el) return;
  const map = { saving:{cls:"saving",text:"Ukládám…"}, saved:{cls:"saved",text:"Uloženo"}, error:{cls:"error",text:"Chyba uložení"} };
  if (state === "login") {
    el.className = "save-indicator login";
    el.innerHTML = '🔒 Faktura se neukládá — <a href="/login">přihlaste se</a>';
    return;
  }
  const s = map[state]||map.saved;
  el.className=`save-indicator ${s.cls}`; el.textContent=s.text;
  if (state==="saved") setTimeout(()=>{ el.textContent=""; el.className="save-indicator"; }, 3000);
}

/* ── Status lifecycle ───────────────────────── */
const STATUS_LABELS = { draft:"Koncept", issued:"Vystavena", sent:"Odesláno", paid:"Zaplaceno", overdue:"Po splatnosti", cancelled:"Stornováno" };
const STATUS_NEXT   = { draft:["issued"], issued:["sent","cancelled"], sent:["paid","overdue","cancelled"], overdue:["paid","cancelled"], paid:[], cancelled:[] };

let currentStatus = "draft";

function updateStatusPillFromRow(row) {
  currentStatus = row.status || "draft";
  renderStatusPill(currentStatus);
  renderPaymentBar(row);
}

function renderStatusPill(status) {
  const pill = document.getElementById("statusPill");
  if (!pill) return;
  const cls = { draft:"draft", issued:"issued", sent:"sent", paid:"paid", overdue:"overdue", cancelled:"cancelled" };
  pill.className = `status-pill ${cls[status]||"draft"}`;
  pill.innerHTML = `<span class="status-dot"></span>${STATUS_LABELS[status]||status}` +
    (STATUS_NEXT[status]?.length ? ` <span class="status-chevron">▾</span>` : "");
}

function openStatusMenu() {
  const next = STATUS_NEXT[currentStatus] || [];
  if (!next.length) return;
  // Toggle: clicking the pill again closes the menu
  const existing = document.getElementById("statusMenu");
  if (existing?.classList.contains("open")) { closeStatusMenu(); return; }
  let menu = existing;
  if (!menu) {
    menu = document.createElement("div");
    menu.id = "statusMenu";
    menu.className = "status-menu";
    document.body.appendChild(menu);
  }
  menu.innerHTML = next.map(s =>
    `<button class="status-menu-item ${s}" data-status="${s}">${STATUS_LABELS[s]}</button>`
  ).join("");
  menu.querySelectorAll(".status-menu-item").forEach(btn => {
    btn.addEventListener("click", async () => {
      closeStatusMenu();
      await transitionStatus(btn.dataset.status);
    });
  });
  const pill = document.getElementById("statusPill");
  const rect = pill.getBoundingClientRect();
  menu.style.top  = (rect.bottom + 6) + "px";
  menu.style.left = rect.left + "px";
  menu.classList.add("open");
}

function closeStatusMenu() {
  document.getElementById("statusMenu")?.classList.remove("open");
}

async function transitionStatus(newStatus) {
  if (!currentInvoiceId) {
    // Invoice not saved yet — try to save first, then retry the transition
    await doSave();
    if (!currentInvoiceId) {
      setSaveIndicator("login");   // still null → not logged in
      return;
    }
  }
  const resp = await apiFetch(`/api/invoices/${currentInvoiceId}/status`, {
    method:"PATCH", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({ status: newStatus }),
  });
  if (!resp) return;  // 401 → apiFetch already redirected to /login
  if (!resp.ok) { alert("Chyba přechodu stavu: " + (await resp.text())); return; }
  const row = await resp.json();
  currentStatus = newStatus;
  renderStatusPill(newStatus);
  renderPaymentBar(row);
  setSaveIndicator("saved");
}

/* ── Payment bar ────────────────────────────── */
function renderPaymentBar(row) {
  const bar = document.getElementById("paymentBar");
  if (!bar) return;
  const total     = row.total     || 0;
  const paidTotal = row.paid_total || 0;
  const balance   = round2(total - paidTotal);
  const payments  = row.payments  || [];

  if (!["issued","sent","paid","overdue"].includes(row.status)) { bar.style.display="none"; return; }
  bar.style.display = "flex";

  const pct = total > 0 ? Math.min(100, Math.round(paidTotal / total * 100)) : 0;
  bar.innerHTML =
    `<div class="pbar-left">` +
      `<span class="pbar-title">Platby</span>` +
      `<span class="pbar-balance ${balance<=0?"pbar-settled":""}">` +
        (balance <= 0 ? "Uhrazeno" : `Zbývá: ${fmtNum(balance)} ${document.getElementById("currency")?.value||"CZK"}`) +
      `</span>` +
    `</div>` +
    `<div class="pbar-track"><div class="pbar-fill" style="width:${pct}%"></div></div>` +
    `<button class="pbar-btn" id="addPaymentBtn">+ Zaznamenat platbu</button>`;

  document.getElementById("addPaymentBtn")?.addEventListener("click", () => openPaymentPanel(row));

  // Render existing payments as small chips
  if (payments.length) {
    const chips = document.createElement("div");
    chips.className = "pbar-chips";
    chips.innerHTML = payments.map(p =>
      `<span class="pbar-chip">${p.paid_on} · ${fmtNum(p.amount)}` +
      ` <button class="pbar-chip-del" data-pid="${p.id}" title="Odebrat">×</button></span>`
    ).join("");
    chips.querySelectorAll(".pbar-chip-del").forEach(btn => {
      btn.addEventListener("click", () => removePayment(parseInt(btn.dataset.pid)));
    });
    bar.appendChild(chips);
  }
}

/* ── Payment panel (side sheet) ─────────────── */
function openPaymentPanel(row) {
  const panel = document.getElementById("paymentPanel");
  if (!panel) return;

  // Pre-fill amount = remaining balance
  const balance = round2((row?.total||0) - (row?.paid_total||0));
  const today = new Date().toISOString().split("T")[0];
  document.getElementById("payInput_amount").value  = balance > 0 ? balance : "";
  document.getElementById("payInput_date").value    = today;
  document.getElementById("payInput_note").value    = "";

  panel.classList.add("open");
  document.getElementById("payInput_amount").focus();
}

function closeSidePanel() {
  document.getElementById("paymentPanel")?.classList.remove("open");
}

async function submitPayment(e) {
  e.preventDefault();
  if (!currentInvoiceId) return;
  const amount = parseFloat(document.getElementById("payInput_amount").value);
  const date   = document.getElementById("payInput_date").value;
  const note   = document.getElementById("payInput_note").value.trim();
  if (!amount || !date) return;
  try {
    const resp = await apiFetch(`/api/invoices/${currentInvoiceId}/payments`, {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({ amount, paid_on:date, note }),
    });
    if (!resp.ok) throw new Error(await resp.text());
    const row = await resp.json();
    renderPaymentBar(row);
    renderStatusPill(row.status);
    currentStatus = row.status;
    closeSidePanel();
    setSaveIndicator("saved");
  } catch (err) { alert("Chyba: " + err.message); }
}

async function removePayment(paymentId) {
  if (!currentInvoiceId) return;
  if (!confirm("Odebrat tuto platbu?")) return;
  try {
    const resp = await apiFetch(`/api/invoices/${currentInvoiceId}/payments/${paymentId}`, { method:"DELETE" });
    if (!resp.ok) throw new Error(await resp.text());
    const row = await resp.json();
    renderPaymentBar(row);
    renderStatusPill(row.status);
    currentStatus = row.status;
  } catch (err) { alert("Chyba: " + err.message); }
}

/* ── Credit note ────────────────────────────── */
async function initCreditNote() {
  if (!currentInvoiceId) { alert("Nejprve uložte fakturu."); return; }
  if (!confirm(`Vytvořit dobropis k faktuře ${document.getElementById("invoice_number").value}?`)) return;
  try {
    const resp = await apiFetch(`/api/invoices/${currentInvoiceId}/credit-note`, { method:"POST" });
    if (!resp.ok) throw new Error(await resp.text());
    const cn = await resp.json();
    // Start a new invoice context pre-filled as credit note
    currentInvoiceId = null; isFirstSave = true;
    applyPayload(cn.data, "credit_note");
    showToast(`Dobropis k ${cn.original_number} připraven. Zkontrolujte a uložte.`);
    schedulePreview(0);
  } catch (err) { alert("Chyba: " + err.message); }
}

function showToast(msg) {
  let t = document.getElementById("toastMsg");
  if (!t) { t = document.createElement("div"); t.id="toastMsg"; t.className="toast"; document.body.appendChild(t); }
  t.textContent = msg; t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 4000);
}

/* ── Dashboard ──────────────────────────────── */
function openDashboard() {
  document.getElementById("dashboardView").classList.add("open");
  if (typeof loadDashboard === "function") loadDashboard();
}
function closeDashboard() {
  document.getElementById("dashboardView").classList.remove("open");
}

/* ── Drawer ─────────────────────────────────── */
async function openDrawer() { document.getElementById("drawerOverlay").classList.add("open"); await loadInvoiceList(); }
function closeDrawer() { document.getElementById("drawerOverlay").classList.remove("open"); }

async function loadInvoiceList() {
  const list = document.getElementById("drawerList");
  list.innerHTML = '<div class="drawer-loading"><span class="spin"></span> Načítám…</div>';
  try {
    const resp = await apiFetch("/api/invoices?limit=100");
    renderDrawerList(await resp.json());
  } catch (err) { list.innerHTML = `<div class="drawer-error">Chyba: ${escHtml(err.message)}</div>`; }
}

function renderDrawerList(rows) {
  const list = document.getElementById("drawerList");
  if (!rows.length) { list.innerHTML = '<div class="drawer-empty">Žádné uložené faktury</div>'; return; }

  // Group: overdue first, then by status
  const order = ["overdue","sent","issued","draft","paid","cancelled"];
  rows.sort((a,b) => (order.indexOf(a.status)||99)-(order.indexOf(b.status)||99) || b.updated_at.localeCompare(a.updated_at));

  list.innerHTML = rows.map(r => {
    const active   = r.id === currentInvoiceId ? " active" : "";
    const date     = r.updated_at?.slice(0,10) || "";
    const balance  = r.total > 0 ? round2(r.total - (r.paid_total||0)) : 0;
    const balHtml  = r.status === "paid" ? "" :
      (balance > 0 ? `<span class="dr-balance">${fmtNum(balance)}</span>` : "");
    const cn       = r.doc_type === "credit_note" ? '<span class="dr-cn-badge">dobropis</span>' : "";
    const canDelete = r.status === "draft";
    const tagBadges = _tagBadgesHtml(r.tags || []);
    return `
      <div class="drawer-row${active}" data-id="${r.id}">
        <div class="dr-main">
          <span class="dr-num">${escHtml(r.invoice_number||"—")}</span>
          ${cn}
          <span class="dr-status ${r.status}">${STATUS_LABELS[r.status]||r.status}</span>
          ${balHtml}
        </div>
        <div class="dr-meta">Uloženo: ${date}${tagBadges ? " · "+tagBadges : ""}</div>
        ${canDelete ? `<button class="dr-delete" data-id="${r.id}" title="Smazat">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4h6v2"/></svg>
        </button>` : ""}
      </div>`;
  }).join("");

  list.querySelectorAll(".drawer-row").forEach(row => {
    row.addEventListener("click", e => { if (e.target.closest(".dr-delete")) return; loadInvoice(parseInt(row.dataset.id)); });
  });
  list.querySelectorAll(".dr-delete").forEach(btn => {
    btn.addEventListener("click", async e => {
      e.stopPropagation();
      if (!confirm("Smazat fakturu?")) return;
      const r = await apiFetch(`/api/invoices/${btn.dataset.id}`, { method:"DELETE" });
      if (!r && r !== null) return;   // 401 already redirected; other errors: stop
      if (parseInt(btn.dataset.id) === currentInvoiceId) { currentInvoiceId = null; isFirstSave = true; }
      await loadInvoiceList();
    });
  });
}

async function loadInvoice(id) {
  try {
    const resp = await apiFetch(`/api/invoices/${id}`);
    if (!resp.ok) throw new Error(await resp.text());
    const row = await resp.json();
    applyPayload(row.data, row.doc_type);
    currentInvoiceId = id; isFirstSave = false;
    currentStatus = row.status;
    renderStatusPill(row.status);
    renderPaymentBar(row);
    document.getElementById("cloneBtn").style.display = "";
    closeDrawer(); schedulePreview(0); setSaveIndicator("saved");
  } catch (err) { alert("Chyba: " + err.message); }
}

async function newInvoice() {
  if (!confirm("Zahodit aktuální fakturu a vytvořit novou?")) return;
  currentInvoiceId = null; isFirstSave = true; currentStatus = "draft";
  const today = new Date(), due = new Date(today);
  due.setDate(due.getDate() + 14);
  const fmt = d => d.toISOString().split("T")[0];
  const setV = (id, v) => { const el=document.getElementById(id); if(el) el.value=v||""; };
  setV("issue_date", fmt(today)); setV("duzp", fmt(today)); setV("due_date", fmt(due));
  setV("currency","CZK");
  ["bank_account","iban","swift","supplier_name","supplier_email","supplier_phone","supplier_ico","supplier_dic",
   "supplier_address","customer_name","customer_email","customer_ico","customer_dic","customer_address","notes"]
    .forEach(id => setV(id,""));
  logoB64=null;
  document.getElementById("logoPreview").hidden=true;
  document.getElementById("logoPlaceholder").hidden=false;
  document.getElementById("removeLogo").hidden=true;
  signatureB64=null;
  document.getElementById("signaturePreview").hidden=true;
  document.getElementById("signaturePlaceholder").hidden=false;
  document.getElementById("removeSignature").hidden=true;
  currentTags=[]; renderTagChips();
  items=[]; itemIdSeq=0; renderItemList();
  await prefillNextNumber("FA");
  document.getElementById("variable_symbol").value = document.getElementById("invoice_number").value.replace(/\D/g, "");
  applyProfileToForm();
  document.getElementById("cloneBtn").style.display = "none";
  renderStatusPill("draft");
  const bar = document.getElementById("paymentBar"); if (bar) bar.style.display="none";
  closeDrawer(); schedulePreview(0);
}

function applyPayload(d, docType) {
  const setV = (id, v) => { const el=document.getElementById(id); if(el&&v!=null) el.value=v; };
  setV("invoice_number", d.invoice_number); setV("issue_date",d.issue_date);
  setV("duzp",d.duzp); setV("due_date",d.due_date); setV("currency",d.currency);
  setV("variable_symbol",d.variable_symbol); setV("bank_account",d.bank_account);
  setV("iban",d.iban); setV("swift",d.swift); setV("notes",d.notes);
  currentTags = Array.isArray(d.tags) ? [...d.tags] : [];
  renderTagChips();
  const sup=d.supplier||{};
  setV("supplier_name",sup.name); setV("supplier_email",sup.email); setV("supplier_phone",sup.phone||"");
  setV("supplier_ico",sup.ico); setV("supplier_dic",sup.dic); setV("supplier_address",sup.address);
  const cust=d.customer||{};
  setV("customer_name",cust.name); setV("customer_email",cust.email);
  setV("customer_ico",cust.ico); setV("customer_dic",cust.dic); setV("customer_address",cust.address);
  currentTemplate = d.template||"modern";
  document.querySelectorAll(".tpl-btn").forEach(b=>b.classList.toggle("active",b.dataset.tpl===currentTemplate));
  logoB64=d.logo_b64||null;
  if (logoB64) { document.getElementById("logoPreview").src=logoB64; document.getElementById("logoPreview").hidden=false; document.getElementById("logoPlaceholder").hidden=true; document.getElementById("removeLogo").hidden=false; }
  else { document.getElementById("logoPreview").hidden=true; document.getElementById("logoPlaceholder").hidden=false; document.getElementById("removeLogo").hidden=true; }
  signatureB64=d.signature_b64||null;
  if (signatureB64) { document.getElementById("signaturePreview").src=signatureB64; document.getElementById("signaturePreview").hidden=false; document.getElementById("signaturePlaceholder").hidden=true; document.getElementById("removeSignature").hidden=false; }
  else { document.getElementById("signaturePreview").hidden=true; document.getElementById("signaturePlaceholder").hidden=false; document.getElementById("removeSignature").hidden=true; }
  items=[]; itemIdSeq=0;
  (d.items||[]).forEach(i=>addItemToState(i)); renderItemList();
  const chip=document.getElementById("doc_chip_num"); if(chip) chip.value=d.invoice_number||"";
  // Show credit note badge if applicable
  const cnBadge = document.getElementById("docTypeBadge");
  if (cnBadge) { cnBadge.textContent = docType==="credit_note"?"Dobropis":"Faktura"; cnBadge.className = docType==="credit_note"?"doc-type-badge cn":"doc-type-badge"; }
}

/* ── Validate ───────────────────────────────── */
async function validateInvoice() {
  const bar = document.getElementById("validationBanner");
  bar.classList.remove("show","ok","err");
  try {
    const resp = await fetch("/validate",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(buildPayload())});
    const data = await resp.json();
    if (data.valid) {
      bar.className="validation-bar show ok";
      bar.innerHTML=`<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg><span><strong>Faktura splňuje požadavky</strong> — § 29 zákona č. 235/2004 Sb.</span>`;
    } else {
      bar.className="validation-bar show err";
      bar.innerHTML=`<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="margin-top:1px"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg><div><strong>Faktura obsahuje chyby:</strong><ul>${data.errors.map(e=>`<li>${escHtml(e)}</li>`).join("")}</ul></div>`;
    }
    setTimeout(()=>bar.classList.remove("show"),14000);
  } catch(err){console.error("[validate]",err);}
}

/* ── PDF ────────────────────────────────────── */
async function downloadPdf() {
  const btn=document.getElementById("pdfBtn"); btn.disabled=true;
  btn.innerHTML=`<span class="spin" style="width:12px;height:12px;border-width:2px;border-top-color:#fff;"></span> Generuji…`;
  try {
    const resp=await fetch("/generate-pdf",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(buildPayload())});
    if(!resp.ok){alert("Chyba PDF:\n"+await resp.text());return;}
    const blob=await resp.blob(),url=URL.createObjectURL(blob),a=document.createElement("a");
    a.href=url; a.download=(document.getElementById("invoice_number").value||"faktura")+".pdf"; a.click(); URL.revokeObjectURL(url);
  } catch(err){alert("Chyba: "+err.message);}
  finally{
    btn.disabled=false;
    btn.innerHTML=`<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg> Stáhnout PDF`;
  }
}

/* ── Helpers ────────────────────────────────── */
function round2(v){return Math.round((v+Number.EPSILON)*100)/100;}
function fmtNum(v){return new Intl.NumberFormat("cs-CZ",{minimumFractionDigits:2,maximumFractionDigits:2}).format(v);}
function escHtml(s){return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");}
window.schedulePreview=schedulePreview;

/* ── User Profile ───────────────────────────── */
let cachedProfile = null;

async function loadProfile() {
  try {
    const resp = await fetch("/api/user/profile", { credentials: "include" });
    if (!resp.ok) return;
    cachedProfile = await resp.json();
    fillProfileForm(cachedProfile);
  } catch (_) {}
}

function fillProfileForm(p) {
  if (!p) return;
  const setV = (id, v) => { const el = document.getElementById(id); if (el) el.value = v || ""; };
  setV("prof_name",         p.name);
  setV("prof_ico",          p.ico);
  setV("prof_dic",          p.dic);
  setV("prof_email",        p.email);
  setV("prof_phone",        p.phone);
  setV("prof_address",      p.address);
  setV("prof_bank_account", p.bank_account);
  setV("prof_iban",         p.iban);
  setV("prof_swift",        p.swift);
}

function applyProfileToForm() {
  if (!cachedProfile) return;
  const p = cachedProfile;
  const setV = (id, v) => { const el = document.getElementById(id); if (el && !el.value) el.value = v || ""; };
  setV("supplier_name",    p.name);
  setV("supplier_email",   p.email);
  setV("supplier_phone",   p.phone);
  setV("supplier_ico",     p.ico);
  setV("supplier_dic",     p.dic);
  setV("supplier_address", p.address);
  setV("bank_account",     p.bank_account);
  setV("iban",             p.iban);
  setV("swift",            p.swift);
}

function openProfile() { document.getElementById("profileView").classList.add("open"); }
function closeProfile() { document.getElementById("profileView").classList.remove("open"); }

async function saveProfile() {
  const val = id => (document.getElementById(id)?.value ?? "").trim();
  const profile = {
    name:         val("prof_name"),
    ico:          val("prof_ico"),
    dic:          val("prof_dic"),
    email:        val("prof_email"),
    phone:        val("prof_phone"),
    address:      val("prof_address"),
    bank_account: val("prof_bank_account"),
    iban:         val("prof_iban"),
    swift:        val("prof_swift"),
  };
  const statusEl = document.getElementById("profileSaveStatus");
  try {
    const resp = await fetch("/api/user/profile", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(profile),
    });
    if (!resp.ok) throw new Error(await resp.text());
    cachedProfile = profile;
    if (statusEl) { statusEl.textContent = "Uloženo ✓"; setTimeout(() => { statusEl.textContent = ""; }, 3000); }
  } catch (err) {
    if (statusEl) statusEl.textContent = "Chyba při ukládání";
    console.error("[profile]", err);
  }
}

/* ── Contacts ───────────────────────────────── */
let cachedContacts = [];

async function loadContacts() {
  try {
    const resp = await fetch("/api/contacts", { credentials: "include" });
    if (!resp.ok) return;
    cachedContacts = await resp.json();
  } catch (_) {}
}

function openContactPicker() {
  loadContacts().then(() => renderContactsList(""));
  document.getElementById("contactsModal").classList.add("open");
  document.getElementById("contactSearch").value = "";
}

function closeContactPicker() {
  document.getElementById("contactsModal").classList.remove("open");
}

function renderContactsList(filter) {
  const q = (filter || "").toLowerCase();
  const list = cachedContacts.filter(c =>
    c.name.toLowerCase().includes(q) || c.ico.includes(q)
  );
  const el = document.getElementById("contactsList");
  if (!list.length) {
    el.innerHTML = `<div style="padding:24px;text-align:center;color:var(--ink3);font-size:13px">${q ? "Žádný kontakt nenalezen." : "Zatím žádné kontakty."}</div>`;
    return;
  }
  el.innerHTML = list.map(c => `
    <div class="contact-row" data-id="${c.id}">
      <div class="contact-row-info">
        <div class="contact-row-name">${escHtml(c.name)}</div>
        <div class="contact-row-meta">${[c.ico ? "IČO "+escHtml(c.ico) : "", escHtml(c.email||"")].filter(Boolean).join(" · ")}</div>
      </div>
      <button class="btn-ghost btn-contact-pick" data-id="${c.id}" style="font-size:12px">Vybrat</button>
      <button class="btn-ghost btn-contact-del" data-id="${c.id}" style="font-size:12px;color:var(--red)">✕</button>
    </div>`).join("");

  el.querySelectorAll(".btn-contact-pick").forEach(btn =>
    btn.addEventListener("click", () => {
      const c = cachedContacts.find(x => String(x.id) === btn.dataset.id);
      if (c) selectContact(c);
    })
  );
  el.querySelectorAll(".btn-contact-del").forEach(btn =>
    btn.addEventListener("click", () => removeContact(Number(btn.dataset.id)))
  );
}

function selectContact(c) {
  const setV = (id, v) => { const el = document.getElementById(id); if (el) el.value = v || ""; };
  setV("customer_name",    c.name);
  setV("customer_ico",     c.ico);
  setV("customer_dic",     c.dic);
  setV("customer_address", c.address);
  setV("customer_email",   c.email);
  schedulePreview(0);
  scheduleSave();
  closeContactPicker();
}

async function removeContact(id) {
  try {
    await fetch(`/api/contacts/${id}`, { method: "DELETE", credentials: "include" });
    cachedContacts = cachedContacts.filter(c => c.id !== id);
    renderContactsList(document.getElementById("contactSearch").value);
  } catch (err) { console.error("[contacts]", err); }
}

async function saveCurrentCustomerAsContact() {
  const val = id => (document.getElementById(id)?.value ?? "").trim();
  const data = {
    name:    val("customer_name"),
    ico:     val("customer_ico"),
    dic:     val("customer_dic"),
    address: val("customer_address"),
    email:   val("customer_email"),
    phone:   "",
  };
  if (!data.name) return;
  try {
    const resp = await fetch("/api/contacts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(data),
    });
    if (!resp.ok) throw new Error(await resp.text());
    const saved = await resp.json();
    cachedContacts = [...cachedContacts.filter(c => c.ico !== saved.ico || !saved.ico), saved]
      .sort((a, b) => a.name.localeCompare(b.name, "cs"));
    hideSaveContactBanner();
    showToast("Kontakt uložen");
  } catch (err) { console.error("[contacts]", err); }
}

function showSaveContactBanner() {
  const el = document.getElementById("saveContactBanner");
  if (!el) return;
  el.style.display = "";
  el.innerHTML = `<div class="ares-found" style="cursor:pointer" id="saveContactChip">
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/></svg>
    Uložit do kontaktů
  </div>`;
  document.getElementById("saveContactChip")?.addEventListener("click", saveCurrentCustomerAsContact);
}

function hideSaveContactBanner() {
  const el = document.getElementById("saveContactBanner");
  if (el) el.style.display = "none";
}

function addContactManually() {
  const name = prompt("Název kontaktu:");
  if (!name) return;
  const ico = prompt("IČO (volitelné):", "") || "";
  const email = prompt("E-mail (volitelné):", "") || "";
  fetch("/api/contacts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ name, ico, dic: "", address: "", email, phone: "" }),
  }).then(r => r.json()).then(saved => {
    cachedContacts = [...cachedContacts, saved].sort((a, b) => a.name.localeCompare(b.name, "cs"));
    renderContactsList(document.getElementById("contactSearch").value);
  }).catch(err => console.error("[contacts]", err));
}

function showToast(msg) {
  const el = document.getElementById("toastMsg");
  if (!el) return;
  el.textContent = msg;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 2500);
}

window.showSaveContactBanner = showSaveContactBanner;
window.hideSaveContactBanner = hideSaveContactBanner;

/* ── Tags ───────────────────────────────────── */

const TAG_COLORS = ["tag-c0","tag-c1","tag-c2","tag-c3","tag-c4"];
function _tagColor(tag) {
  let h = 0;
  for (let i = 0; i < tag.length; i++) h = (h * 31 + tag.charCodeAt(i)) >>> 0;
  return TAG_COLORS[h % TAG_COLORS.length];
}

function renderTagChips() {
  const container = document.getElementById("tagsChips");
  if (!container) return;
  container.innerHTML = currentTags.map((tag, i) =>
    `<span class="tag-chip ${_tagColor(tag)}" data-idx="${i}">${escHtml(tag)}<button class="tag-del" title="Odebrat">×</button></span>`
  ).join("");
  container.querySelectorAll(".tag-del").forEach(btn =>
    btn.addEventListener("click", e => {
      e.stopPropagation();
      const idx = parseInt(btn.closest(".tag-chip").dataset.idx);
      currentTags.splice(idx, 1);
      renderTagChips();
      markUnsaved(); schedulePreview();
    })
  );
}

function _initTagInput() {
  const input = document.getElementById("tagInputField");
  if (!input) return;
  const wrap = document.getElementById("tagsInput");
  wrap?.addEventListener("click", () => input.focus());
  input.addEventListener("keydown", e => {
    if ((e.key === "Enter" || e.key === ",") && input.value.trim()) {
      e.preventDefault();
      const tag = input.value.trim().replace(/,+$/, "");
      if (tag && !currentTags.includes(tag)) {
        currentTags.push(tag);
        renderTagChips();
        markUnsaved(); schedulePreview();
      }
      input.value = "";
    } else if (e.key === "Backspace" && !input.value && currentTags.length) {
      currentTags.pop();
      renderTagChips();
      markUnsaved(); schedulePreview();
    }
  });
}

/* Call once on startup */
_initTagInput();

function _tagBadgesHtml(tags) {
  if (!Array.isArray(tags) || !tags.length) return "";
  return tags.slice(0, 3).map(t =>
    `<span class="dr-tag ${_tagColor(t)}">${escHtml(t)}</span>`
  ).join("");
}

/* ── Invoice cloning ─────────────────────────── */

async function cloneInvoice() {
  if (!confirm("Duplikovat tuto fakturu jako nový koncept?")) return;
  const payload = buildPayload();
  currentInvoiceId = null; isFirstSave = true; currentStatus = "draft";

  const today = new Date(), due = new Date(today);
  due.setDate(due.getDate() + 14);
  const fmt = d => d.toISOString().split("T")[0];

  // Carry over all data from the current invoice, reset date/number fields
  applyPayload({
    ...payload,
    issue_date: fmt(today),
    duzp:       fmt(today),
    due_date:   fmt(due),
    invoice_number: "",
  }, "invoice");

  // Get new sequential number using same prefix
  const prefix = (payload.invoice_number || "FA").match(/^([A-Z]+)-/)?.[1] || "FA";
  await prefillNextNumber(prefix);
  document.getElementById("variable_symbol").value =
    document.getElementById("invoice_number").value.replace(/\D/g, "");

  // Copy tags from original
  currentTags = [...(payload.tags || [])];
  renderTagChips();

  document.getElementById("cloneBtn").style.display = "none";
  renderStatusPill("draft");
  const bar = document.getElementById("paymentBar"); if (bar) bar.style.display = "none";
  schedulePreview(0);
  showToast("Duplikováno — zkontrolujte a uložte nový koncept");
}
