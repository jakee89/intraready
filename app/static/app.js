const state = {
  bootstrap: null,
  currentInvoice: null,
  catalogue: [],
  exports: [],
  declaration: null,
  pendingReviews: new Map(),
  mapping: { regions: [], page: 1, pageCount: 1, documentId: null, image: null, start: null, draft: null, pointerId: null },
  page: "dashboard",
};

const titles = {
  dashboard: ["WORKSPACE", "Overview"], upload: ["IMPORT", "Upload invoices"],
  invoices: ["REVIEW", "Invoices"], review: ["INVOICE", "Review invoice"],
  catalogue: ["LIBRARY", "Product memory"], declarations: ["EXPORT", "Declarations"],
  settings: ["CONFIGURATION", "Organisation"], guide: ["HELP", "How it works"],
  admin: ["PLATFORM", "Administration"],
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);
}

function formatMoney(value, currency = "EUR") {
  if (value === "" || value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  try { return new Intl.NumberFormat(undefined, { style: "currency", currency: currency || "EUR" }).format(Number(value)); }
  catch { return `${value} ${currency || ""}`; }
}

function formatDate(value) {
  if (!value) return "Date missing";
  const parsed = new Date(`${value}T12:00:00`);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

function statusLabel(status) {
  return ({ needs_review: "Needs review", draft: "Draft", approved: "Approved", exported: "Exported", submitted: "Submitted" })[status] || status;
}

function statusChip(status) {
  return `<span class="status ${escapeHtml(status)}">${escapeHtml(statusLabel(status))}</span>`;
}

function toast(message, kind = "success") {
  let region = $(".toast-region");
  if (!region) {
    region = document.createElement("div");
    region.className = "toast-region";
    region.setAttribute("aria-live", "polite");
    document.body.append(region);
  }
  const item = document.createElement("div");
  item.className = `toast ${kind === "error" ? "error" : ""}`;
  item.textContent = message;
  region.append(item);
  setTimeout(() => item.remove(), 4200);
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  if (options.method && !["GET", "HEAD"].includes(options.method)) {
    headers.set("X-IntraReady-Request", "1");
    if (state.bootstrap?.auth?.csrf_token) headers.set("X-CSRF-Token", state.bootstrap.auth.csrf_token);
  }
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401) { location.replace("/login"); throw new Error("Sign in required"); }
  const contentType = response.headers.get("content-type") || "";
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    if (contentType.includes("json")) {
      const body = await response.json();
      detail = body.detail?.message || body.detail?.message || body.detail || detail;
      if (typeof detail !== "string") detail = JSON.stringify(detail);
    }
    throw new Error(detail);
  }
  if (contentType.includes("json")) return response.json();
  return response;
}

async function refreshBootstrap() {
  state.bootstrap = await api("/api/bootstrap");
  renderShared();
}

function navigate(page, options = {}) {
  state.page = page;
  $$(".page").forEach(element => element.classList.toggle("active", element.dataset.page === page));
  $$(".nav-item").forEach(element => element.classList.toggle("active", element.dataset.nav === page || (page === "review" && element.dataset.nav === "invoices")));
  $("#eyebrow").textContent = titles[page]?.[0] || "WORKSPACE";
  $("#pageTitle").textContent = titles[page]?.[1] || "IntraReady";
  document.body.classList.remove("menu-open");
  history.replaceState(null, "", `#${page}`);
  if (!options.preserveScroll) window.scrollTo({ top: 0, behavior: "smooth" });
  if (page === "catalogue") loadCatalogue();
  if (page === "settings") renderSettings();
  if (page === "declarations") { renderDeclarationLanding(); loadExports(); }
  if (page === "admin") loadAdmin();
}

function profileMissing(profile) {
  return ["trader_vat", "email", "signatory", "id_card", "telephone", "locality"].filter(field => !profile?.[field]);
}

function renderShared() {
  const data = state.bootstrap;
  if (!data) return;
  const auth = data.auth || {};
  $("#accountName").textContent = auth.name || auth.email || "Account";
  $("#accountOrganisation").textContent = auth.organisation_name || "Organisation";
  $("#accountInitial").textContent = (auth.name || auth.email || "?").trim().slice(0, 1).toUpperCase();
  $("#adminNav").classList.toggle("hidden", !["owner", "support", "auditor"].includes(auth.platform_role));
  const badge = $("#reviewBadge");
  badge.textContent = data.stats.needs_review;
  badge.classList.toggle("hidden", data.stats.needs_review === 0);
  const missing = profileMissing(data.profile);
  $("#setupBanner").className = `setup-banner ${missing.length ? "visible" : ""}`;
  $("#setupBanner").innerHTML = missing.length ? `
    <span class="setup-icon">!</span><div><strong>Finish your organisation setup</strong><p>${missing.length} required ${missing.length === 1 ? "field is" : "fields are"} missing. You can review invoices now, but export waits for these details.</p></div>
    <button class="button secondary" data-nav="settings">Complete setup</button>` : "";
  $("#metrics").innerHTML = [
    ["Invoices", data.stats.total, "All documents", "▤"],
    ["Needs review", data.stats.needs_review, "Missing or unchecked", "!"],
    ["Approved", data.stats.approved, "Ready for a period", "✓"],
    ["Exported", data.stats.exported, "Exported or submitted", "↗"],
  ].map(([label, value, note, icon]) => `<article class="metric"><div class="metric-top"><span class="metric-label">${label}</span><span class="metric-icon">${icon}</span></div><strong>${value}</strong><small>${note}</small></article>`).join("");
  renderRecentInvoices();
  renderInvoiceTable();
  renderSchemaIndicators();
}

async function loadAdmin() {
  const target = $("#adminContent");
  if (!target) return;
  target.innerHTML = `<div class="panel loading"></div>`;
  try {
    const data = await api("/api/admin/overview");
    const active = data.accounts.filter(item => item.status === "active").length;
    target.innerHTML = `
      <div class="metric-grid admin-metrics">
        <article class="metric"><span class="metric-label">Accounts</span><strong>${data.accounts.length}</strong><small>${active} active</small></article>
        <article class="metric"><span class="metric-label">Organisations</span><strong>${data.organisations.length}</strong><small>Strictly separated workspaces</small></article>
        <article class="metric"><span class="metric-label">Registration</span><strong class="metric-word">${escapeHtml((data.settings.registration_mode || "closed").replaceAll("_", " "))}</strong><small>Change below</small></article>
        <article class="metric"><span class="metric-label">Billing</span><strong class="metric-word">Inactive</strong><small>No charges can be created</small></article>
      </div>
      <article class="panel admin-panel">
        <div class="panel-head"><div><p class="eyebrow">ACCESS</p><h2>Accounts</h2></div>${state.bootstrap.auth.platform_role === "owner" ? `<button class="button primary" data-add-account>Add account</button>` : ""}</div>
        <div class="data-table-wrap"><table class="data-table"><thead><tr><th>Name</th><th>Email</th><th>Status</th><th>Platform role</th><th>Last sign-in</th><th>Action</th></tr></thead><tbody>
          ${data.accounts.map(item => `<tr><td><strong>${escapeHtml(item.name)}</strong>${item.requested_organisation ? `<span class="subline">${escapeHtml(item.requested_organisation)}</span>` : ""}</td><td>${escapeHtml(item.email)}</td><td>${escapeHtml(item.status)}</td><td>${escapeHtml(item.platform_role || "Member")}</td><td>${escapeHtml(item.last_login_at || "Never")}</td><td>${item.status === "pending" && state.bootstrap.auth.platform_role === "owner" ? `<button class="mini-button" data-activate-account="${item.id}">Approve</button>` : item.platform_role !== "owner" && state.bootstrap.auth.platform_role === "owner" ? `<button class="mini-button" data-account-status="${item.id}" data-next-status="${item.status === "suspended" ? "active" : "suspended"}">${item.status === "suspended" ? "Restore" : "Suspend"}</button>` : "—"}</td></tr>`).join("")}
        </tbody></table></div>
      </article>
      ${state.bootstrap.auth.platform_role === "owner" ? `<article class="panel admin-panel"><div class="panel-head"><div><p class="eyebrow">REGISTRATION</p><h2>Who can request an account?</h2></div></div><div class="admin-setting"><div><strong>Registration mode</strong><p>Closed allows only accounts you create. Approval required adds a public request form, but nobody can sign in until you approve them.</p></div><select id="registrationMode"><option value="closed">Closed</option><option value="approval_required">Requests require approval</option></select></div></article>` : ""}
      <article class="panel admin-panel"><div class="panel-head"><div><p class="eyebrow">ORGANISATIONS</p><h2>Usage overview</h2></div></div><div class="data-table-wrap"><table class="data-table"><thead><tr><th>Organisation</th><th>Seats</th><th>Invoices</th><th>Created</th></tr></thead><tbody>${data.organisations.map(item => `<tr><td><strong>${escapeHtml(item.name)}</strong></td><td>${item.seats}</td><td>${item.invoices}</td><td>${escapeHtml(item.created_at)}</td></tr>`).join("")}</tbody></table></div></article>`;
    if ($("#registrationMode")) $("#registrationMode").value = data.settings.registration_mode || "closed";
  } catch (error) { target.innerHTML = `<div class="empty-state"><h2>Administration unavailable</h2><p>${escapeHtml(error.message)}</p></div>`; }
}

function renderRecentInvoices() {
  const invoices = state.bootstrap?.invoices?.slice(0, 5) || [];
  $("#recentInvoices").innerHTML = invoices.length ? invoices.map(invoice => `
    <div class="invoice-list-item" data-open-invoice="${invoice.id}" tabindex="0">
      <span class="file-icon">PDF</span><div><strong>${escapeHtml(invoice.supplier_name || "Supplier not identified")}</strong><small>${escapeHtml(invoice.invoice_number || invoice.filename || "Manual draft")}</small></div>
      <div><strong class="money">${formatMoney(invoice.total_value, invoice.currency)}</strong><small>${formatDate(invoice.arrival_date || invoice.invoice_date)}</small></div>
      ${statusChip(invoice.status)}
    </div>`).join("") : `<div class="empty-state"><h3>No invoices yet</h3><p>Add a PDF to start your first review.</p><button class="button secondary" data-nav="upload">Upload an invoice</button></div>`;
}

function renderInvoiceTable() {
  const target = $("#invoiceTable");
  if (!target || !state.bootstrap) return;
  const filter = $("#invoiceFilter")?.value || "all";
  const invoices = state.bootstrap.invoices.filter(item => filter === "all" || item.status === filter || (filter === "needs_review" && item.status === "draft"));
  target.innerHTML = `<div class="data-table-wrap"><table class="data-table"><thead><tr><th>Supplier / reference</th><th>Movement date</th><th>Goods rows</th><th>Total</th><th>Issues</th><th>Status</th><th>Actions</th></tr></thead><tbody>${invoices.length ? invoices.map(invoice => `
    <tr data-open-invoice="${invoice.id}" tabindex="0"><td><strong>${escapeHtml(invoice.s_name || invoice.supplier_name || "Supplier not identified")}</strong><span class="subline">${escapeHtml(invoice.invoice_number || invoice.filename || "Manual draft")}</span></td>
    <td>${escapeHtml(formatDate(invoice.arrival_date))}</td><td>${invoice.goods_count}</td><td class="amount">${formatMoney(invoice.total_value, invoice.currency)}</td>
    <td>${invoice.blocking_count ? `<span class="status needs_review">${invoice.blocking_count} open</span>` : "—"}</td><td>${statusChip(invoice.status)}</td><td>${invoice.status === "submitted" ? "—" : `<button class="mini-button delete" data-delete-invoice="${invoice.id}" aria-label="Delete invoice ${escapeHtml(invoice.invoice_number || "draft")}">Delete</button>`}</td></tr>`).join("") : `<tr><td class="empty-row" colspan="7">No invoices match this filter.</td></tr>`}</tbody></table></div>`;
}

async function openInvoice(invoiceId) {
  state.pendingReviews.clear();
  navigate("review");
  history.replaceState(null, "", `#review/${invoiceId}`);
  $("#reviewContent").innerHTML = `<div class="panel loading"></div>`;
  try {
    state.currentInvoice = await api(`/api/invoices/${invoiceId}`);
    renderReview();
  } catch (error) {
    $("#reviewContent").innerHTML = `<div class="empty-state"><h2>Could not open invoice</h2><p>${escapeHtml(error.message)}</p></div>`;
  }
}

const invoiceFields = [
  ["flow", "Flow", "Choose arrivals for imports into the reporting country or dispatches for exports."],
  ["supplier_name", "Supplier name", "The legal supplier shown on the invoice."],
  ["invoice_number", "Invoice reference", "Kept for checking and audit; excluded from official XML."],
  ["supplier_vat_country", "VAT country", "Two-letter prefix from the supplier VAT number. Greece uses EL."],
  ["supplier_vat_number", "Supplier VAT number", "Enter without its two-letter country prefix."],
  ["invoice_date", "Invoice date", "The date printed on the invoice; it does not decide the reporting month."],
  ["arrival_date", "Actual arrival date", "The date goods physically arrived in Malta. This determines the period."],
  ["currency", "Invoice currency", "The currency printed on the invoice. Declaration values are entered in EUR."],
  ["total_value", "Invoice total", "Used to reconcile all goods, charges and exclusions before approval."],
  ["consignment_country", "Default consignment country", "Optional default copied into goods rows. Change individual rows when orders were shipped from different EU countries."],
  ["mode_transport", "Mode of transport", "Malta code: usually 1 sea, 4 air/courier, or 5 post."],
  ["terms_delivery", "Delivery terms", "Three-letter Incoterm such as DAP, EXW or CIF."],
  ["nature_transaction", "Nature of transaction", "Usually 11 for an outright business purchase. Confirm exceptions."],
];

function invoiceInput(field, label, tip, invoice) {
  if (field === "arrival_date" && invoice.flow === "D") {
    label = "Actual dispatch date";
    tip = "The date goods physically left Malta. This determines the reporting period.";
  }
  let type = "text";
  if (field.endsWith("_date")) type = "date";
  if (field === "flow") return `<label>${label} <span class="tip" data-tip="${escapeHtml(tip)}">?</span><select data-invoice-field="${field}"><option value="A" ${invoice[field]==="A"?"selected":""}>A · Arrivals</option><option value="D" ${invoice[field]==="D"?"selected":""}>D · Dispatches</option></select></label>`;
  if (field === "mode_transport") return `<label>${label} <span class="tip" data-tip="${escapeHtml(tip)}">?</span><select data-invoice-field="${field}">${[["1","1 · Sea"],["2","2 · Rail"],["3","3 · Road"],["4","4 · Air / courier"],["5","5 · Post"],["7","7 · Fixed installation"],["8","8 · Inland waterway"],["9","9 · Own propulsion"]].map(([v,l]) => `<option value="${v}" ${invoice[field]===v?"selected":""}>${l}</option>`).join("")}</select></label>`;
  if (field === "nature_transaction") return `<label>${label} <span class="tip" data-tip="${escapeHtml(tip)}">?</span><input data-invoice-field="${field}" value="${escapeHtml(invoice[field] || "")}" inputmode="numeric" maxlength="2" placeholder="e.g. 11"></label>`;
  return `<label>${label} <span class="tip" data-tip="${escapeHtml(tip)}">?</span><input data-invoice-field="${field}" type="${type}" value="${escapeHtml(invoice[field])}"></label>`;
}

function renderReview() {
  const invoice = state.currentInvoice;
  if (!invoice) return;
  const issues = invoice.readiness.issues;
  const issueLines = new Set(issues.filter(item => item.line_id).map(item => item.line_id));
  const document = invoice.document;
  $("#reviewContent").innerHTML = `
    <div class="review-toolbar"><div><button class="text-button back" data-nav="invoices">← Back to invoices</button><h2>${escapeHtml(invoice.supplier_name || "Supplier not identified")}</h2><p>${escapeHtml(invoice.invoice_number || "New manual draft")} · Revision ${invoice.revision} · ${statusChip(invoice.status)}</p></div>
    <div class="review-actions">${document && invoice.status !== "submitted" ? `<button id="aiStatusButton" class="button quiet" title="Check the configured cloud invoice AI connection without processing this invoice">Check API AI</button><button id="extractAgainButton" class="button quiet" title="Send this PDF to API AI once and save its layout; confirmed rows are preserved">Learn layout with AI</button><button id="mapSupplierButton" class="button quiet" title="View, edit or restore this supplier's saved layouts">Supplier layout</button>` : ""}<button id="prepareInvoiceButton" class="button secondary" title="Classify and link printing, freight and other extracted charges">Prepare invoice</button><button id="rememberSupplierButton" class="button quiet" title="Reuse this supplier's shipment defaults on future invoices">Remember supplier</button><button id="addLineButton" class="button quiet">Add row</button>${invoice.status === "approved" ? `<button id="reopenButton" class="button secondary">Reopen review</button>` : `<button id="approveButton" class="button primary" ${invoice.readiness.ready ? "" : "disabled"}>Approve invoice</button>`}</div></div>
    <div class="review-layout">
      <article class="panel document-panel">${document ? `<div class="document-head"><strong title="${escapeHtml(document.filename)}">${escapeHtml(document.filename)}</strong><a class="text-button" href="/api/documents/${document.id}/file" target="_blank" rel="noopener">Open ↗</a></div><iframe class="pdf-frame" title="Invoice PDF" src="/api/documents/${document.id}/file#toolbar=1"></iframe>` : `<div class="no-document"><div><span class="file-icon">—</span><h3>Manual invoice</h3><p>No PDF is attached to this draft.</p></div></div>`}</article>
      <div class="review-workspace">
        <article class="panel review-section"><div class="section-title"><div><h3>Invoice and shipment</h3><p>Fields save when you leave them.</p></div></div><div class="form-grid">${invoiceFields.map(fields => invoiceInput(...fields, invoice)).join("")}</div>
          <label class="wide notes-label">Notes<textarea data-invoice-field="notes" rows="2">${escapeHtml(invoice.notes)}</textarea></label>
        </article>
        <article class="panel review-section"><div class="section-title"><div><h3>Checks</h3><p>Approval unlocks when all blocking items are resolved.</p></div></div>
          <div class="issue-summary ${invoice.readiness.ready ? "ready" : ""}"><span class="issue-count">${invoice.readiness.ready ? "✓" : invoice.readiness.blocking_count}</span><div><strong>${invoice.readiness.ready ? "Ready for approval" : `${invoice.readiness.blocking_count} checks remaining`}</strong><p>${invoice.readiness.ready ? "All required data is present and every row has been reviewed." : "Work through the highlighted fields and rows below."}</p></div></div>
          ${issues.length ? `<div class="issue-list">${issues.slice(0, 8).map(issue => `<div class="issue"><span>•</span><span>${escapeHtml(issue.message)}</span>${issue.line_id ? `<button data-focus-line="${issue.line_id}">Show row</button>` : ""}</div>`).join("")}${issues.length > 8 ? `<div class="issue"><span>+</span><span>${issues.length - 8} more checks are highlighted in the table.</span></div>` : ""}</div>` : ""}
        </article>
        ${invoice.extraction_runs?.length ? `<article class="panel review-section"><div class="section-title"><div><h3>Extraction activity</h3><p>Shows when API AI was used, its result, time and provider request information.</p></div></div><div class="issue-list">${invoice.extraction_runs.map(run => `<div class="issue"><span>${run.status === "completed" ? "✓" : "!"}</span><span><strong>${escapeHtml(run.method)}</strong> · ${escapeHtml(run.status)} · ${escapeHtml(run.duration_ms)} ms<br><small>${escapeHtml(run.message)}</small></span></div>`).join("")}</div></article>` : ""}
        ${invoice.preparation?.rows?.length ? `<article class="panel review-section"><div class="section-title"><div><h3>Prepared Intrastat rows</h3><p>These are the final values that will be exported after approval.</p></div></div><div class="data-table-wrap"><table class="data-table"><thead><tr><th>Product</th><th>Consignment</th><th>Included charges</th><th>Invoice €</th><th>Stat €</th><th>Net kg</th></tr></thead><tbody>${invoice.preparation.rows.map(row => `<tr><td><strong>${escapeHtml(row.sku || row.description)}</strong><span class="subline">${escapeHtml(row.description)}</span></td><td>${escapeHtml(row.consignment_country || "Missing")}</td><td>${escapeHtml(row.included.join(", ") || "None")}</td><td>${escapeHtml(row.invoice_value)}</td><td>${escapeHtml(row.statistical_value)}</td><td>${escapeHtml(row.net_mass || "Missing")}</td></tr>`).join("")}</tbody></table></div></article>` : ""}
      </div>
        <article class="panel review-section full-width-lines"><div class="section-title"><div><h3>Goods and charges</h3><p>Internal reference details remain here and stay out of XML.</p></div><div class="line-toolbar"><select id="lineFilter"><option value="all">All rows</option><option value="issues">Rows with issues</option><option value="unreviewed">Unreviewed</option><option value="goods">Goods only</option><option value="charges">Charges only</option></select><button id="saveReviewedButton" class="button secondary" ${state.pendingReviews.size ? "" : "disabled"}>Save reviewed rows${state.pendingReviews.size ? ` (${state.pendingReviews.size})` : ""}</button><button id="combineRowsButton" class="button quiet" title="Combine rows only when their Intrastat classification fields match">Combine equivalent</button><button id="addLineSmallButton" class="button secondary">Add</button></div></div>
          <div class="charge-guide"><strong>Charge treatment:</strong> choose “Add to invoice value” for costs that form part of the goods purchase, “Statistical only” for relevant transport/insurance, or exclude a service with a note. Confirm the correct treatment for your case.</div>
          <div class="data-table-wrap"><table class="data-table line-table"><thead><tr><th>Type</th><th>SKU / description</th><th>Qty</th><th>CN code</th><th>Origin</th><th>Consignment</th><th>Invoice €</th><th>Stat €</th><th>Unit kg</th><th>Total kg</th><th>Reviewed</th><th>Actions</th></tr></thead><tbody id="lineTableBody">
          ${invoice.lines.map(line => lineRow(line, invoice.lines, issueLines)).join("") || `<tr><td class="empty-row" colspan="12">No rows were extracted. Add the first goods row manually.</td></tr>`}
          </tbody></table></div>
        </article>
    </div>`;
}

function lineRow(line, allLines, issueLines) {
  const goods = allLines.filter(item => item.line_kind === "goods");
  const rowClass = issueLines.has(line.id) ? "row-issue" : (line.reviewed ? "row-reviewed" : "");
  const charge = line.line_kind.startsWith("charge");
  return `<tr id="line-${line.id}" class="${rowClass}" data-line-row="${line.id}" data-kind="${line.line_kind}" data-reviewed="${line.reviewed}" data-has-issue="${issueLines.has(line.id)}">
    <td><select data-line-id="${line.id}" data-line-field="line_kind" title="How this source row is treated"><option value="goods" ${line.line_kind==="goods"?"selected":""}>Goods</option><option value="charge" ${line.line_kind==="charge"?"selected":""}>Charge · decide</option><option value="charge_invoice" ${line.line_kind==="charge_invoice"?"selected":""}>Add to invoice value</option><option value="charge_stat" ${line.line_kind==="charge_stat"?"selected":""}>Statistical only</option><option value="excluded" ${line.line_kind==="excluded"?"selected":""}>Excluded / service</option></select>${charge ? `<select data-line-id="${line.id}" data-line-field="linked_line_id" title="Goods row receiving this charge"><option value="">Link to goods…</option>${goods.map(g => `<option value="${g.id}" ${line.linked_line_id===g.id?"selected":""}>${escapeHtml(g.sku || g.description.slice(0,18))}</option>`).join("")}</select>` : `<span class="kind-tag ${line.line_kind}">${escapeHtml(line.confidence)}</span>`}</td>
    <td><input class="description-input" data-line-id="${line.id}" data-line-field="description" value="${escapeHtml(line.description)}" title="Original description"><input data-line-id="${line.id}" data-line-field="sku" value="${escapeHtml(line.sku)}" placeholder="SKU" title="Supplier SKU"></td>
    <td><input data-line-id="${line.id}" data-line-field="quantity" value="${escapeHtml(line.quantity)}" inputmode="decimal"><small class="subline">${escapeHtml(line.unit)}</small></td>
    <td><input data-line-id="${line.id}" data-line-field="hs_code" value="${escapeHtml(line.hs_code)}" maxlength="8" inputmode="numeric" ${line.line_kind!=="goods"?"disabled":""}><small class="subline" title="Checked against the official 2026 CN list">${line.cn_reference?.supp_unit ? `Requires ${escapeHtml(line.cn_reference.supp_unit)}` : escapeHtml(line.raw_commodity_code)}</small></td>
    <td><input data-line-id="${line.id}" data-line-field="origin_country" value="${escapeHtml(line.origin_country)}" maxlength="2" ${line.line_kind!=="goods"?"disabled":""}></td>
    <td><input data-line-id="${line.id}" data-line-field="consignment_country" value="${escapeHtml(line.consignment_country || "")}" maxlength="2" placeholder="EU" title="EU country this goods row was shipped from" ${line.line_kind!=="goods"?"disabled":""}></td>
    <td><input data-line-id="${line.id}" data-line-field="invoice_value" value="${escapeHtml(line.invoice_value)}" inputmode="decimal"></td>
    <td><input data-line-id="${line.id}" data-line-field="statistical_value" value="${escapeHtml(line.statistical_value)}" inputmode="decimal" ${line.line_kind!=="goods"?"disabled":""}></td>
    <td><input data-line-id="${line.id}" data-line-field="unit_net_mass" value="${escapeHtml(line.unit_net_mass)}" inputmode="decimal" title="Net weight of one unit; remembered by supplier and SKU" ${line.line_kind!=="goods"?"disabled":""}></td>
    <td><input data-line-id="${line.id}" data-line-field="net_mass" value="${escapeHtml(line.net_mass)}" inputmode="decimal" title="Total row weight used for Intrastat${line.net_mass_overridden ? " · manually overridden" : " · quantity × unit weight"}" ${line.line_kind!=="goods"?"disabled":""}></td>
    <td><input type="checkbox" data-review-line="${line.id}" ${state.pendingReviews.has(line.id) ? (state.pendingReviews.get(line.id) ? "checked" : "") : (line.reviewed ? "checked" : "")} aria-label="Mark row reviewed"></td>
    <td><div class="row-actions"><button class="mini-button" data-edit-line="${line.id}" title="Edit all row fields">Details</button>${line.line_kind==="goods" ? `<button class="mini-button" data-suggest-cn="${line.id}" title="Search official CN descriptions and rank them with API AI">Suggest CN</button><button class="mini-button" data-remember-line="${line.id}" title="Save verified product facts for this supplier and SKU">Remember</button>` : ""}<button class="mini-button delete" data-delete-line="${line.id}">Delete</button></div><small class="subline">Page ${line.source_page || "—"}</small></td>
  </tr>`;
}

async function updateInvoiceField(input) {
  const invoice = state.currentInvoice;
  try {
    const updated = await api(`/api/invoices/${invoice.id}`, { method: "PATCH", body: JSON.stringify({ revision: invoice.revision, [input.dataset.invoiceField]: input.value }) });
    state.currentInvoice = updated;
    await refreshBootstrap();
    renderReview();
    toast("Invoice saved");
  } catch (error) { toast(error.message, "error"); }
}

async function updateLineField(input) {
  const value = input.type === "checkbox" ? input.checked : input.value;
  try {
    state.currentInvoice = await api(`/api/lines/${input.dataset.lineId}`, { method: "PATCH", body: JSON.stringify({ [input.dataset.lineField]: value }) });
    await refreshBootstrap();
    renderReview();
    toast("Row saved");
  } catch (error) { toast(error.message, "error"); }
}

async function loadCatalogue() {
  try {
    state.catalogue = await api("/api/catalogue");
    $("#catalogueTable").innerHTML = `<div class="data-table-wrap"><table class="data-table"><thead><tr><th>Supplier</th><th>Product</th><th>Commodity</th><th>Origin</th><th>Unit net kg</th><th>Evidence</th><th></th></tr></thead><tbody>${state.catalogue.length ? state.catalogue.map(item => `<tr><td>${escapeHtml(item.supplier_name || item.supplier_vat || "Any supplier")}</td><td><strong>${escapeHtml(item.sku)}</strong><span class="subline">${escapeHtml(item.description)}</span></td><td>${escapeHtml(item.hs_code || "—")}</td><td>${escapeHtml(item.origin_country || "—")}</td><td>${escapeHtml(item.unit_net_mass || "—")}</td><td>${escapeHtml(item.evidence || "—")}</td><td><button class="mini-button delete" data-delete-fact="${item.id}">Remove</button></td></tr>`).join("") : `<tr><td class="empty-row" colspan="7">No products remembered yet. Save one from a reviewed invoice row.</td></tr>`}</tbody></table></div>`;
  } catch (error) { toast(error.message, "error"); }
}

function renderSchemaIndicators() {
  const schema = state.bootstrap?.schema;
  if (!schema) return;
  const text = schema.valid ? "XML schema ready" : "XML setup needed";
  $("#schemaPill").textContent = text;
  $("#schemaPill").className = `schema-pill ${schema.valid ? "ready" : ""}`;
  const target = $("#schemaStatus");
  if (target) target.innerHTML = `<div class="schema-card ${schema.valid ? "ready" : ""}"><span class="info-icon">${schema.valid ? "✓" : "!"}</span><div><strong>${text}</strong><p>${escapeHtml(schema.message)}</p>${schema.missing_fields?.length ? `<p>Unmapped: ${escapeHtml(schema.missing_fields.join(", "))}</p>` : ""}</div></div>`;
}

function renderSettings() {
  const profile = state.bootstrap?.profile;
  if (!profile) return;
  for (const [field, value] of Object.entries(profile)) {
    const input = $(`#profileForm [name="${field}"]`);
    if (input) input.value = value ?? "";
  }
  if ($("#securityAccountEmail")) $("#securityAccountEmail").textContent = state.bootstrap.auth?.email || "";
  renderSchemaIndicators();
}

function renderDeclarationLanding() {
  if (!$("#declarationPeriod").value) $("#declarationPeriod").value = state.bootstrap?.current_period || "";
  if (state.bootstrap?.profile?.default_flow) $("#declarationFlow").value = state.bootstrap.profile.default_flow;
  if (!state.declaration) $("#declarationContent").innerHTML = `<div class="empty-state"><h3>Choose a movement month</h3><p>The preview will contain approved invoices only and will show the exact rounded fields.</p></div>`;
  renderSchemaIndicators();
}

async function buildDeclarationPreview() {
  const period = $("#declarationPeriod").value;
  const flow = $("#declarationFlow").value;
  if (!period) return toast("Choose a movement period", "error");
  $("#declarationContent").innerHTML = `<div class="panel loading"></div>`;
  try {
    state.declaration = await api(`/api/declarations/${period}?flow=${encodeURIComponent(flow)}`);
    const data = state.declaration;
    $("#declarationContent").innerHTML = `
      <div class="declaration-summary"><div class="summary-card"><span>Flow</span><strong>${data.flow === "A" ? "Arrivals" : "Dispatches"}</strong></div><div class="summary-card"><span>Approved invoices</span><strong>${data.invoice_count}</strong></div><div class="summary-card"><span>Goods rows</span><strong>${data.row_count}</strong></div><div class="summary-card"><span>Invoice value</span><strong>€${data.totals.invoice_value.toLocaleString()}</strong></div></div>
      <article class="panel table-panel"><div class="preview-table"><table class="data-table"><thead><tr><th>Invoice ref.</th><th>Supplier</th><th>Description</th><th>CN</th><th>Origin</th><th>Consignment</th><th>Invoice €</th><th>Stat €</th><th>Net kg</th></tr></thead><tbody>${data.rows.length ? data.rows.map(row => `<tr><td>${escapeHtml(row._invoice_reference)}</td><td>${escapeHtml(row._supplier)}</td><td>${escapeHtml(row._description)}</td><td>${escapeHtml(row.HS_Code)}</td><td>${escapeHtml(row.COO)}</td><td>${escapeHtml(row.COC)}</td><td class="amount">${escapeHtml(row.INVOICE_VALUE)}</td><td class="amount">${escapeHtml(row.STAT_VALUE)}</td><td class="amount">${escapeHtml(row.NET_MASS)}</td></tr>`).join("") : `<tr><td class="empty-row" colspan="9">No approved goods are available in ${escapeHtml(period)}.</td></tr>`}</tbody></table></div>
      <div class="export-actions"><p>CSV includes internal references for your review. XML contains official fields only and requires the configured XSD.</p><button id="downloadCsv" class="button quiet" ${data.row_count?"":"disabled"}>Download review CSV</button><button id="downloadXml" class="button primary" ${data.row_count && data.schema.valid?"":"disabled"}>Download validated XML</button></div></article>`;
  } catch (error) {
    $("#declarationContent").innerHTML = `<div class="empty-state"><h3>Preview could not be built</h3><p>${escapeHtml(error.message)}</p></div>`;
  }
}

async function downloadExport(format) {
  const period = $("#declarationPeriod").value;
  const flow = $("#declarationFlow").value;
  try {
    const response = await api(`/api/declarations/${period}/${format}?flow=${encodeURIComponent(flow)}`, { method: "POST" });
    const blob = await response.blob();
    const disposition = response.headers.get("content-disposition") || "";
    const filename = disposition.match(/filename="?([^";]+)"?/)?.[1] || `intrastat-${period}.${format}`;
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob); link.download = filename; document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    await refreshBootstrap(); await loadExports();
    toast(`${format.toUpperCase()} export saved`);
  } catch (error) { toast(error.message, "error"); }
}

function openLineDialog(lineId = null) {
  if (!state.currentInvoice) return;
  const form = $("#lineForm");
  form.reset(); form.dataset.lineId = lineId || "";
  const line = lineId ? state.currentInvoice.lines.find(item => item.id === Number(lineId)) : null;
  if (line) {
    for (const [field, value] of Object.entries(line)) {
      const input = form.elements.namedItem(field);
      if (input) input.value = value ?? "";
    }
  }
  $("#lineDialog h2").textContent = line ? "Edit row details" : "Add a row";
  $("#saveLineButton").textContent = line ? "Save row" : "Add row";
  $("#lineDialog").showModal();
}

async function loadExports() {
  try {
    state.exports = await api("/api/exports");
    const target = $("#exportHistory");
    target.innerHTML = state.exports.length ? `<article class="panel export-history"><div class="panel-head"><div><p class="eyebrow">AUDIT TRAIL</p><h2>Export history</h2></div></div><div class="data-table-wrap"><table class="data-table"><thead><tr><th>Created</th><th>Period</th><th>Flow</th><th>Format</th><th>Rows</th><th>Status / receipt</th></tr></thead><tbody>${state.exports.map(item => `<tr><td>${escapeHtml(formatDate(item.created_at.slice(0,10)))}</td><td>${escapeHtml(item.period)}</td><td>${item.flow === "A" ? "Arrivals" : "Dispatches"}</td><td>${escapeHtml(item.format.toUpperCase())}</td><td>${item.row_count}</td><td>${item.status === "submitted" ? `<strong>${escapeHtml(item.declaration_reference)}</strong><span class="subline">Submitted</span>` : `<button class="mini-button" data-mark-submitted="${item.id}">Record portal receipt</button>`}</td></tr>`).join("")}</tbody></table></div></article>` : "";
  } catch (error) { toast(error.message, "error"); }
}

function openCatalogueDialog() {
  $("#catalogueForm").reset();
  $("#catalogueDialog").showModal();
}

function mappingLabels() {
  return Object.fromEntries([...$("#mappingField").options].map(option => [option.value, option.textContent]));
}

function renderMappingBoxes() {
  const mapping = state.mapping;
  const canvas = $("#mappingCanvas");
  const context = canvas.getContext("2d");
  if (!mapping.image || !mapping.image.complete || !mapping.image.naturalWidth) return;
  if (canvas.width !== mapping.image.naturalWidth || canvas.height !== mapping.image.naturalHeight) {
    canvas.width = mapping.image.naturalWidth; canvas.height = mapping.image.naturalHeight;
  }
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.drawImage(mapping.image, 0, 0, canvas.width, canvas.height);
  const labels = mappingLabels();
  const regions = mapping.regions.filter(region => region.page === mapping.page);
  if (mapping.draft) regions.push(mapping.draft);
  regions.forEach(region => {
    const x = region.x * canvas.width, y = region.y * canvas.height;
    const width = region.width * canvas.width, height = region.height * canvas.height;
    context.save();
    context.strokeStyle = "#e31b36"; context.fillStyle = "rgba(227,27,54,.18)";
    context.lineWidth = Math.max(3, canvas.width / 350);
    if (region === mapping.draft) context.setLineDash([12, 8]);
    context.fillRect(x, y, width, height); context.strokeRect(x, y, width, height);
    const label = labels[region.field] || region.field;
    context.font = `bold ${Math.max(15, canvas.width / 65)}px sans-serif`;
    const labelWidth = context.measureText(label).width + 14;
    const labelY = Math.max(0, y - Math.max(22, canvas.width / 50));
    context.fillStyle = "#e31b36"; context.fillRect(x, labelY, labelWidth, Math.max(22, canvas.width / 50));
    context.fillStyle = "white"; context.fillText(label, x + 7, labelY + Math.max(16, canvas.width / 68));
    context.restore();
  });
}

function renderMapping() {
  const mapping = state.mapping;
  const imageUrl = `/api/documents/${mapping.documentId}/pages/${mapping.page}.png`;
  const image = new Image();
  mapping.image = image;
  image.onload = () => { if (state.mapping.image === image) renderMappingBoxes(); };
  image.src = imageUrl;
  $("#mappingPageLabel").textContent = `Page ${mapping.page} of ${mapping.pageCount}`;
  $("#mappingPrevious").disabled = mapping.page <= 1;
  $("#mappingNext").disabled = mapping.page >= mapping.pageCount;
  const labels = mappingLabels();
  renderMappingBoxes();
  $("#mappingRegionList").innerHTML = mapping.regions.map((region, index) => `<button class="mini-button" data-remove-region="${index}">${escapeHtml(labels[region.field] || region.field)} · p${region.page} ×</button>`).join("");
}

async function openSupplierMapping() {
  try {
    const [source, history] = await Promise.all([api(`/api/invoices/${state.currentInvoice.id}/supplier-mapping`), api(`/api/invoices/${state.currentInvoice.id}/supplier-templates`)]);
    state.mapping = { regions: source.regions || [], page: 1, pageCount: source.page_count || 1, documentId: source.document_id, image: null, start: null, draft: null, pointerId: null };
    $("#mappingStatus").textContent = "Drag a box around the value";
    $("#mappingHistory").innerHTML = `<strong>Saved layout versions</strong>${history.length ? history.map(item => `<div class="mapping-history-row"><span>Version ${item.version} · ${escapeHtml(item.source)} · ${escapeHtml(item.created_at.slice(0,10))}${item.active ? " · Active" : ""}</span>${item.active ? "" : `<button class="mini-button" data-activate-template="${item.id}">Restore</button>`}</div>`).join("") : `<p class="subline">No saved versions yet.</p>`}`;
    $("#supplierMapDialog").showModal(); renderMapping();
  } catch (error) { toast(error.message, "error"); }
}

async function processFiles(files) {
  if (!files.length) return;
  const target = $("#uploadResults");
  target.innerHTML = [...files].map(file => `<div class="upload-result"><span class="file-icon">PDF</span><strong>${escapeHtml(file.name)}</strong><span>Processing…</span></div>`).join("");
  const form = new FormData();
  [...files].forEach(file => form.append("files", file));
  try {
    const response = await api("/api/upload", { method: "POST", body: form });
    target.innerHTML = response.results.map(result => `<div class="upload-result ${result.error ? "error" : ""}"><span class="file-icon">PDF</span><strong>${escapeHtml(result.filename)}</strong><span>${result.error ? escapeHtml(result.error) : result.duplicate ? "Already in workspace" : `Draft ready · ${escapeHtml(result.adapter)}`}</span>${result.invoice_id ? `<button class="button secondary" data-open-invoice="${result.invoice_id}">Review</button><button class="mini-button delete" data-delete-invoice="${result.invoice_id}">Delete</button>` : ""}</div>`).join("");
    await refreshBootstrap();
    toast("Invoice processing complete");
  } catch (error) {
    target.innerHTML = `<div class="upload-result error"><strong>Upload failed</strong><span>${escapeHtml(error.message)}</span></div>`;
  }
}

document.addEventListener("click", async event => {
  if (event.target.closest("#logoutButton")) {
    try { await api("/api/auth/logout", { method: "POST" }); } finally { location.replace("/login"); }
    return;
  }
  if (event.target.closest("#changePasswordButton")) { $("#passwordForm").reset(); $("#passwordDialog").showModal(); return; }
  if (event.target.closest("#refreshAdminButton")) { loadAdmin(); return; }
  if (event.target.closest("[data-add-account]")) { $("#accountForm").reset(); $("#accountDialog").showModal(); return; }
  const activation = event.target.closest("[data-activate-account]");
  if (activation) {
    try { await api(`/api/admin/accounts/${activation.dataset.activateAccount}/activate`, { method: "POST" }); await loadAdmin(); toast("Account approved"); }
    catch (error) { toast(error.message, "error"); }
    return;
  }
  const accountStatus = event.target.closest("[data-account-status]");
  if (accountStatus) {
    const action = accountStatus.dataset.nextStatus === "active" ? "restore" : "suspend";
    if (!confirm(`${action[0].toUpperCase() + action.slice(1)} this account?`)) return;
    try { await api(`/api/admin/accounts/${accountStatus.dataset.accountStatus}/status`, { method: "PATCH", body: JSON.stringify({ status: accountStatus.dataset.nextStatus }) }); await loadAdmin(); toast(action === "restore" ? "Account restored" : "Account suspended"); }
    catch (error) { toast(error.message, "error"); }
    return;
  }
  const nav = event.target.closest("[data-nav]");
  if (nav) { event.preventDefault(); navigate(nav.dataset.nav); return; }
  const deleteInvoice = event.target.closest("[data-delete-invoice]");
  if (deleteInvoice) {
    event.preventDefault();
    event.stopPropagation();
    if (!confirm("Delete this invoice and its stored PDF? This cannot be undone.")) return;
    try {
      await api(`/api/invoices/${deleteInvoice.dataset.deleteInvoice}`, { method: "DELETE" });
      deleteInvoice.closest(".upload-result")?.remove();
      await refreshBootstrap();
      renderInvoiceTable();
      toast("Invoice deleted");
    } catch (error) { toast(error.message, "error"); }
    return;
  }
  const opener = event.target.closest("[data-open-invoice]");
  if (opener) { event.preventDefault(); openInvoice(opener.dataset.openInvoice); return; }
  if (event.target.closest("#menuButton")) { document.body.classList.toggle("menu-open"); return; }
  if (event.target.closest("#browseButton")) { $("#fileInput").click(); return; }
  if (event.target.closest("#manualInvoiceButton")) {
    try { const invoice = await api("/api/invoices", { method: "POST" }); await refreshBootstrap(); openInvoice(invoice.id); } catch (error) { toast(error.message, "error"); }
    return;
  }
  if (event.target.closest("#addLineButton") || event.target.closest("#addLineSmallButton")) { openLineDialog(); return; }
  const extractAgain = event.target.closest("#extractAgainButton");
  if (extractAgain) {
    if (!confirm("Send this invoice PDF to OpenAI and use API credits to learn its layout? Uploading alone never sends invoices. Confirmed rows and previous layout versions will be preserved.")) return;
    extractAgain.disabled = true; extractAgain.textContent = "Extracting…";
    try { state.currentInvoice = await api(`/api/invoices/${state.currentInvoice.id}/extract-again?use_ai=true`, { method: "POST" }); await refreshBootstrap(); renderReview(); toast("AI extraction suggestions updated"); }
    catch (error) { extractAgain.disabled = false; extractAgain.textContent = "Learn layout with AI"; toast(error.message, "error"); }
    return;
  }
  if (event.target.closest("#aiStatusButton")) {
    const button = event.target.closest("#aiStatusButton"); button.disabled = true; button.textContent = "Checking…";
    try {
      const status = await api("/api/ai/test", { method: "POST" });
      button.textContent = status.ready ? `API ready · ${status.model}` : (status.error_code || "AI unavailable");
      toast(status.ready ? `OpenAI API connected · ${status.model} · ${status.latency_ms} ms` : `${status.error_code || "AI_ERROR"}: ${status.last_error || "The configured model is not ready"}`, status.ready ? "" : "error");
    } catch (error) { button.textContent = "AI unavailable"; toast(error.message, "error"); }
    button.disabled = false; return;
  }
  if (event.target.closest("#mapSupplierButton")) { openSupplierMapping(); return; }
  const activateTemplate = event.target.closest("[data-activate-template]");
  if (activateTemplate) {
    try { await api(`/api/invoices/${state.currentInvoice.id}/supplier-templates/${activateTemplate.dataset.activateTemplate}/activate`, { method: "POST" }); toast("Earlier supplier layout restored"); await openSupplierMapping(); }
    catch (error) { toast(error.message, "error"); }
    return;
  }
  if (event.target.closest("[data-close-mapping]")) { $("#supplierMapDialog").close(); return; }
  if (event.target.closest("#mappingPrevious")) { state.mapping.page--; state.mapping.start = null; state.mapping.draft = null; renderMapping(); return; }
  if (event.target.closest("#mappingNext")) { state.mapping.page++; state.mapping.start = null; state.mapping.draft = null; renderMapping(); return; }
  if (event.target.closest("#mappingUndo")) { state.mapping.regions.pop(); renderMapping(); return; }
  if (event.target.closest("#mappingClear")) { state.mapping.regions = []; state.mapping.start = null; state.mapping.draft = null; $("#mappingStatus").textContent = "Drag a box around the value"; renderMapping(); return; }
  const removeRegion = event.target.closest("[data-remove-region]");
  if (removeRegion) { state.mapping.regions.splice(Number(removeRegion.dataset.removeRegion), 1); renderMapping(); return; }
  if (event.target.closest("#saveMappingButton")) {
    try {
      await api(`/api/invoices/${state.currentInvoice.id}/supplier-mapping`, { method: "PUT", body: JSON.stringify({ regions: state.mapping.regions }) });
      $("#supplierMapDialog").close(); toast("Supplier map saved — future invoices will use it before AI");
    } catch (error) { toast(error.message, "error"); }
    return;
  }
  if (event.target.closest("#previewMappingButton")) {
    try {
      const preview = await api(`/api/invoices/${state.currentInvoice.id}/supplier-mapping/preview-all`, { method: "POST", body: JSON.stringify({ regions: state.mapping.regions }) });
      const fields = Object.entries(preview.fields).filter(([, value]) => value).map(([key, value]) => `${mappingLabels()[key] || key}: ${value}`).join(" · ");
      const lines = preview.lines.map(line => escapeHtml(`${line.sku || "No SKU"} — ${line.description} — ${line.quantity || "?"} × ${line.invoice_value || "?"}`)).join("<br>");
      $("#mappingPreview").innerHTML = `<strong>Preview:</strong> ${escapeHtml(fields || "No fixed fields read")}${lines ? `<hr>${lines}` : "<br>No complete rows yet. Map description, quantity and line total columns."}`;
    } catch (error) { toast(error.message, "error"); }
    return;
  }
  if (event.target.closest("#combineRowsButton")) {
    if (!confirm("Combine goods rows whose Intrastat classification fields match? Original details will be kept in the row notes.")) return;
    try { const updated = await api(`/api/invoices/${state.currentInvoice.id}/combine-equivalent`, { method: "POST" }); state.currentInvoice = updated; await refreshBootstrap(); renderReview(); toast(updated.combined_groups ? `${updated.combined_groups} equivalent group(s) combined` : "No equivalent rows found"); }
    catch (error) { toast(error.message, "error"); }
    return;
  }
  if (event.target.closest("#rememberSupplierButton")) {
    try { await api(`/api/invoices/${state.currentInvoice.id}/remember-supplier`, { method: "POST" }); toast("Supplier defaults saved for future invoices"); }
    catch (error) { toast(error.message, "error"); }
    return;
  }
  if (event.target.closest("#prepareInvoiceButton")) {
    try { state.currentInvoice = await api(`/api/invoices/${state.currentInvoice.id}/prepare`, { method: "POST" }); await refreshBootstrap(); renderReview(); toast("Invoice prepared — review the proposed Intrastat rows"); }
    catch (error) { toast(error.message, "error"); }
    return;
  }
  if (event.target.closest("#saveReviewedButton")) {
    const reviewed = [...state.pendingReviews].map(([id, value]) => ({ id, reviewed: value }));
    if (!reviewed.length) return;
    try {
      state.currentInvoice = await api(`/api/invoices/${state.currentInvoice.id}/review-lines`, { method: "PATCH", body: JSON.stringify({ reviewed }) });
      state.pendingReviews.clear(); await refreshBootstrap(); renderReview(); toast("Reviewed rows saved");
    } catch (error) { toast(error.message, "error"); }
    return;
  }
  const editLine = event.target.closest("[data-edit-line]");
  if (editLine) { openLineDialog(editLine.dataset.editLine); return; }
  if (event.target.closest("#addCatalogueButton")) { openCatalogueDialog(); return; }
  if (event.target.closest("#approveButton")) {
    try { state.currentInvoice = await api(`/api/invoices/${state.currentInvoice.id}/approve`, { method: "POST" }); await refreshBootstrap(); renderReview(); toast("Invoice approved and ready for its movement month"); }
    catch (error) { toast(error.message, "error"); }
    return;
  }
  if (event.target.closest("#reopenButton")) {
    try { state.currentInvoice = await api(`/api/invoices/${state.currentInvoice.id}/reopen`, { method: "POST" }); await refreshBootstrap(); renderReview(); toast("Invoice reopened"); }
    catch (error) { toast(error.message, "error"); }
    return;
  }
  const remember = event.target.closest("[data-remember-line]");
  if (remember) {
    try { await api(`/api/catalogue/from-line/${remember.dataset.rememberLine}`, { method: "POST" }); toast("Verified product facts remembered"); }
    catch (error) { toast(error.message, "error"); }
    return;
  }
  const suggestCn = event.target.closest("[data-suggest-cn]");
  if (suggestCn) {
    const dialog = $("#cnSuggestionDialog");
    const target = $("#cnSuggestionResults");
    dialog.dataset.lineId = suggestCn.dataset.suggestCn;
    target.innerHTML = `<div class="issue"><span>Searching the official catalogue and asking API AI…</span></div>`;
    dialog.showModal();
    try {
      const result = await api(`/api/lines/${suggestCn.dataset.suggestCn}/cn-suggestions`);
      target.innerHTML = result.suggestions.length ? result.suggestions.map(item => `<div class="cn-suggestion"><div><strong>${escapeHtml(item.code)} · ${escapeHtml(item.description)}</strong><p>${escapeHtml(item.reason || item.source)}${item.supp_unit ? ` · Requires ${escapeHtml(item.supp_unit)}` : ""}</p></div><span>${item.confidence !== undefined ? `${item.confidence}%` : "Candidate"}</span><button class="button secondary" data-choose-cn="${escapeHtml(item.code)}">Use code</button></div>`).join("") : `<div class="issue"><span>No suitable candidates were found. Add material and intended use to the row notes and try again.</span></div>`;
    } catch (error) { target.innerHTML = `<div class="issue"><span>${escapeHtml(error.message)}</span></div>`; }
    return;
  }
  const chooseCn = event.target.closest("[data-choose-cn]");
  if (chooseCn) {
    try {
      state.currentInvoice = await api(`/api/lines/${$("#cnSuggestionDialog").dataset.lineId}`, { method: "PATCH", body: JSON.stringify({ hs_code: chooseCn.dataset.chooseCn }) });
      $("#cnSuggestionDialog").close(); await refreshBootstrap(); renderReview(); toast("CN code applied — confirm it, then Remember the product");
    } catch (error) { toast(error.message, "error"); }
    return;
  }
  if (event.target.closest("[data-close-cn]")) { $("#cnSuggestionDialog").close(); return; }
  const deletion = event.target.closest("[data-delete-line]");
  if (deletion && confirm("Delete this row from the draft? The original PDF is kept.")) {
    try { state.currentInvoice = await api(`/api/lines/${deletion.dataset.deleteLine}`, { method: "DELETE" }); await refreshBootstrap(); renderReview(); toast("Row deleted"); }
    catch (error) { toast(error.message, "error"); }
    return;
  }
  const deleteFact = event.target.closest("[data-delete-fact]");
  if (deleteFact && confirm("Remove this product from memory? Existing invoices are unchanged.")) {
    try { await api(`/api/catalogue/${deleteFact.dataset.deleteFact}`, { method: "DELETE" }); await refreshBootstrap(); loadCatalogue(); toast("Product memory removed"); }
    catch (error) { toast(error.message, "error"); }
    return;
  }
  const focusLine = event.target.closest("[data-focus-line]");
  if (focusLine) { $(`#line-${focusLine.dataset.focusLine}`)?.scrollIntoView({ behavior: "smooth", block: "center" }); return; }
  if (event.target.closest("#previewButton")) { buildDeclarationPreview(); return; }
  if (event.target.closest("#downloadCsv")) { downloadExport("csv"); return; }
  if (event.target.closest("#downloadXml")) { downloadExport("xml"); return; }
  const markSubmitted = event.target.closest("[data-mark-submitted]");
  if (markSubmitted) {
    const reference = prompt("Enter the declaration or receipt reference shown by the portal:");
    if (!reference) return;
    try { await api(`/api/exports/${markSubmitted.dataset.markSubmitted}/submitted`, { method: "POST", body: JSON.stringify({ declaration_reference: reference }) }); await refreshBootstrap(); await loadExports(); toast("Portal receipt recorded"); }
    catch (error) { toast(error.message, "error"); }
    return;
  }
});

document.addEventListener("change", event => {
  if (event.target.matches("#registrationMode")) {
    api("/api/admin/settings", { method: "PATCH", body: JSON.stringify({ registration_mode: event.target.value }) })
      .then(() => toast("Registration setting saved"))
      .catch(error => { toast(error.message, "error"); loadAdmin(); });
    return;
  }
  if (event.target.matches("#mappingField")) { state.mapping.start = null; state.mapping.draft = null; $("#mappingStatus").textContent = "Drag a box around the value"; renderMappingBoxes(); return; }
  if (event.target.matches("[data-review-line]")) {
    const id = Number(event.target.dataset.reviewLine);
    state.pendingReviews.set(id, event.target.checked);
    const button = $("#saveReviewedButton");
    button.disabled = false;
    button.textContent = `Save reviewed rows (${state.pendingReviews.size})`;
    return;
  }
  if (event.target.matches("[data-invoice-field]")) updateInvoiceField(event.target);
  if (event.target.matches("[data-line-field]")) updateLineField(event.target);
  if (event.target.matches("#invoiceFilter")) renderInvoiceTable();
  if (event.target.matches("#fileInput")) processFiles(event.target.files);
  if (event.target.matches("#lineFilter")) {
    const filter = event.target.value;
    $$('[data-line-row]').forEach(row => {
      row.classList.toggle("hidden", filter === "issues" ? row.dataset.hasIssue !== "true" : filter === "unreviewed" ? row.dataset.reviewed === "1" : filter === "goods" ? row.dataset.kind !== "goods" : filter === "charges" ? !row.dataset.kind.startsWith("charge") : false);
    });
  }
});

$("#lineForm").addEventListener("submit", async event => {
  event.preventDefault();
  if (event.submitter?.value === "cancel") { $("#lineDialog").close(); return; }
  const body = Object.fromEntries(new FormData(event.currentTarget));
  const lineId = event.currentTarget.dataset.lineId;
  const endpoint = lineId ? `/api/lines/${lineId}` : `/api/invoices/${state.currentInvoice.id}/lines`;
  const method = lineId ? "PATCH" : "POST";
  try { state.currentInvoice = await api(endpoint, { method, body: JSON.stringify(body) }); $("#lineDialog").close(); await refreshBootstrap(); renderReview(); toast(lineId ? "Row saved" : "Row added"); }
  catch (error) { toast(error.message, "error"); }
});

$("#lineForm").addEventListener("input", event => {
  if (!event.target.matches('[name="quantity"], [name="unit_net_mass"]')) return;
  const quantity = Number(event.currentTarget.elements.quantity.value);
  const unitMass = Number(event.currentTarget.elements.unit_net_mass.value);
  if (Number.isFinite(quantity) && Number.isFinite(unitMass)) event.currentTarget.elements.net_mass.value = String(quantity * unitMass);
});

$("#accountForm").addEventListener("submit", async event => {
  event.preventDefault();
  if (event.submitter?.value === "cancel") { $("#accountDialog").close(); return; }
  try {
    await api("/api/admin/accounts", { method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(event.currentTarget))) });
    $("#accountDialog").close(); await loadAdmin(); toast("Account created");
  } catch (error) { toast(error.message, "error"); }
});

$("#passwordForm").addEventListener("submit", async event => {
  event.preventDefault();
  if (event.submitter?.value === "cancel") { $("#passwordDialog").close(); return; }
  const body = Object.fromEntries(new FormData(event.currentTarget));
  if (body.new_password !== body.password_confirm) { toast("The new passwords do not match", "error"); return; }
  try { await api("/api/auth/change-password", { method: "POST", body: JSON.stringify(body) }); $("#passwordDialog").close(); toast("Password changed; other sessions were signed out"); }
  catch (error) { toast(error.message, "error"); }
});

$("#catalogueForm").addEventListener("submit", async event => {
  event.preventDefault();
  if (event.submitter?.value === "cancel") { $("#catalogueDialog").close(); return; }
  try { await api("/api/catalogue", { method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(event.currentTarget))) }); $("#catalogueDialog").close(); await refreshBootstrap(); loadCatalogue(); toast("Product saved"); }
  catch (error) { toast(error.message, "error"); }
});

$("#profileForm").addEventListener("submit", async event => {
  event.preventDefault();
  try {
    const profile = await api("/api/profile", { method: "PATCH", body: JSON.stringify(Object.fromEntries(new FormData(event.currentTarget))) });
    state.bootstrap.profile = profile; await refreshBootstrap(); renderSettings();
    $("#profileSaved").textContent = "Saved just now"; toast("Organisation details saved");
  } catch (error) { toast(error.message, "error"); }
});

$("#schemaForm").addEventListener("submit", async event => {
  event.preventDefault();
  const form = new FormData(); form.append("file", $("#schemaInput").files[0]);
  try { await api("/api/schema", { method: "POST", body: form }); await refreshBootstrap(); renderSettings(); toast("Official schema verified and saved"); }
  catch (error) { toast(error.message, "error"); }
});

const dropZone = $("#uploadZone");
["dragenter", "dragover"].forEach(name => dropZone.addEventListener(name, event => { event.preventDefault(); dropZone.classList.add("dragging"); }));
["dragleave", "drop"].forEach(name => dropZone.addEventListener(name, event => { event.preventDefault(); dropZone.classList.remove("dragging"); }));
dropZone.addEventListener("drop", event => processFiles(event.dataTransfer.files));

const mappingOverlay = $("#mappingCanvas");
function mappingPoint(event) {
  const box = mappingOverlay.getBoundingClientRect();
  return { x: Math.max(0, Math.min(1, (event.clientX - box.left) / box.width)), y: Math.max(0, Math.min(1, (event.clientY - box.top) / box.height)) };
}
mappingOverlay.addEventListener("pointerdown", event => {
  event.preventDefault();
  mappingOverlay.setPointerCapture(event.pointerId);
  state.mapping.pointerId = event.pointerId;
  state.mapping.start = mappingPoint(event);
  state.mapping.draft = { field: $("#mappingField").value, page: state.mapping.page, x: state.mapping.start.x, y: state.mapping.start.y, width: 0, height: 0 };
  $("#mappingStatus").textContent = "Keep dragging to size the red box";
  renderMappingBoxes();
});
mappingOverlay.addEventListener("pointermove", event => {
  if (state.mapping.pointerId !== event.pointerId || !state.mapping.start) return;
  const point = mappingPoint(event), start = state.mapping.start;
  state.mapping.draft = { field: $("#mappingField").value, page: state.mapping.page,
    x: Math.min(start.x, point.x), y: Math.min(start.y, point.y),
    width: Math.abs(point.x - start.x), height: Math.abs(point.y - start.y) };
  renderMappingBoxes();
});
mappingOverlay.addEventListener("pointerup", async event => {
  if (state.mapping.pointerId !== event.pointerId || !state.mapping.start) return;
  event.preventDefault();
  const region = state.mapping.draft;
  state.mapping.pointerId = null;
  state.mapping.start = null;
  state.mapping.draft = null;
  try { mappingOverlay.releasePointerCapture(event.pointerId); } catch (_) {}
  if (region.width > .005 && region.height > .005) {
    if (!region.field.startsWith("line_")) state.mapping.regions = state.mapping.regions.filter(saved => saved.field !== region.field);
    state.mapping.regions.push(region);
    $("#mappingStatus").textContent = "Box saved — check the preview below";
    try {
      const preview = await api(`/api/invoices/${state.currentInvoice.id}/supplier-mapping/preview`, { method: "POST", body: JSON.stringify({ region }) });
      $("#mappingPreview").innerHTML = `<strong>${escapeHtml(mappingLabels()[region.field] || region.field)}:</strong> ${escapeHtml(preview.text || "No readable text inside this box — redraw it.")}`;
    } catch (error) { $("#mappingPreview").textContent = error.message; }
  } else {
    $("#mappingStatus").textContent = "Box was too small — drag around the complete value";
  }
  renderMapping();
});
mappingOverlay.addEventListener("pointercancel", () => { state.mapping.pointerId = null; state.mapping.start = null; state.mapping.draft = null; renderMappingBoxes(); });

document.addEventListener("keydown", event => {
  const opener = event.target.closest?.("[data-open-invoice]");
  if (opener && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); openInvoice(opener.dataset.openInvoice); }
});

async function start() {
  try {
    await refreshBootstrap();
    const hash = location.hash.slice(1);
    const reviewMatch = hash.match(/^review\/(\d+)$/);
    if (reviewMatch) await openInvoice(reviewMatch[1]);
    else navigate(titles[hash] ? hash : "dashboard", { preserveScroll: true });
  } catch (error) {
    $("#mainContent").innerHTML = `<div class="empty-state"><h2>IntraReady could not start</h2><p>${escapeHtml(error.message)}</p></div>`;
  }
}

start();
