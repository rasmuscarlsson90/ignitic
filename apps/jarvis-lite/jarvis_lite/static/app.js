"use strict";

(() => {
  const byId = (id) => document.getElementById(id);
  const state = { user: null, csrf: null, data: null, v12: null, crm: null, crmQuery: null, refreshing: false, toastTimer: null };
  const money = new Intl.NumberFormat("sv-SE", { style: "currency", currency: "SEK", minimumFractionDigits: 0, maximumFractionDigits: 2 });
  const integer = new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 0 });
  const dateFormat = new Intl.DateTimeFormat("sv-SE", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  const roleNames = { admin: "Administratör", operator: "Operatör", viewer: "Läsbehörighet" };
  const statusNames = {
    DISCOVERED: "Identifierad", RESEARCHED: "Analyserad", QUALIFIED: "Kvalificerad",
    COMPLIANCE_CHECK: "Kontaktgranskning", READY_TO_CONTACT: "Klar för kontakt", CONTACTED: "Kontaktad",
    REPLIED: "Svar mottaget", NEGOTIATING: "Dialog", PROPOSAL_READY: "Offertutkast", PROPOSAL_SENT: "Offert skickad",
    ACCEPTED: "Accepterad", DELIVERY_IN_PROGRESS: "Leverans pågår", DELIVERY_VERIFIED: "Leverans verifierad",
    PAYMENT_APPROVAL_REQUIRED: "Betalning väntar på beslut", PAYMENT_REQUESTED: "Betalning begärd",
    PAYMENT_CONFIRMED: "Betalning bekräftad", COMPLETED: "Slutförd", REJECTED: "Avvisad",
    UNSUBSCRIBED: "Avregistrerad", NO_RESPONSE: "Inget svar", DELIVERY_FAILED: "Leveransfel",
    CANCELLED: "Avbruten", PAYMENT_FAILED: "Betalningsfel", MANUAL_REVIEW: "Manuell granskning", PAUSED: "Pausad",
    QUEUED: "I kö", LEASED: "Bearbetas", RUNNING: "Pågår", FAILED: "Misslyckad", UNCERTAIN: "Behöver granskas",
    DRAFT: "Utkast", APPROVED: "Godkänd", REQUESTED: "Begäran skapad", INITIATED: "Betalning inledd",
    CONFIRMED: "Betalning bekräftad", SENT: "Skickad", DELIVERED: "Levererad", PENDING: "Väntar", DONE: "Klar",
    RESOLVED: "Granskad", SUPERSEDED: "Sammanförd", BLOCKED: "Spärrad", REVIEW_REQUIRED: "Kräver granskning",
    IDLE: "Ledig", BUSY: "Arbetar", WAITING: "Väntar", READY: "Redo", TEST_ARTIFACT: "Äldre testartefakt",
    APPROVED_INTERNAL: "Internt granskad · oskickad", NEEDS_REVIEW: "Behöver granskas", QUALITY_REVIEW: "Kvalitetsgranskning"
  };
  const jobNames = { demo_journey: "Simulerad kundresa", research: "Leadanalys", qualify: "Kvalificering", follow_up: "Uppföljning", daily_report: "Daglig rapport", pipeline_advance: "Förbered nästa affärssteg", source_research: "Granska tillåten researchkälla" };
  const errorNames = {
    admin_required: "Åtgärden kräver administratörsbehörighet.",
    unauthorized: "Din session har löpt ut. Logga in igen.",
    authentication_failed: "E-postadressen eller lösenordet stämmer inte.",
    invalid_credentials: "E-postadressen eller lösenordet stämmer inte.",
    kill_switch: "Nödstoppet blockerar nya åtgärder.",
    kill_switch_active: "Nödstoppet blockerar nya åtgärder.",
    budget_exhausted: "Budgettaket är nått. Nya kostnadsdrivande uppgifter är blockerade.",
    payment_approval_required: "En administratör måste först godkänna den här betalningsbegäran.",
    test_recipient_required: "Mottagaren måste finnas i serverns lista över godkända testmottagare.",
    recipient_suppressed: "Kontakten är avregistrerad eller finns på spärrlistan.",
    live_disabled: "Live-läge är inte aktiverat. Verkliga affärsåtgärder är blockerade."
  };

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }
  function text(id, value) { byId(id).textContent = value === undefined || value === null ? "—" : String(value); }
  function amount(value) { return Number.isFinite(Number(value)) && value !== null && value !== undefined ? money.format(Number(value) / 100) : "—"; }
  function number(value, fallback = 0) { return Number.isFinite(Number(value)) ? Number(value) : fallback; }
  function first(...values) { return values.find((value) => value !== undefined && value !== null); }
  function list(value) { return Array.isArray(value) ? value : []; }
  function asStatus(record) { return String(first(record.state, record.status, "PENDING")).toUpperCase(); }
  function statusLabel(value) { return statusNames[String(value).toUpperCase()] || String(value || "Väntar").replaceAll("_", " "); }
  function date(value) {
    if (!value) return "—";
    const parsed = typeof value === "number" ? new Date(value * 1000) : new Date(value);
    return Number.isNaN(parsed.getTime()) ? "—" : dateFormat.format(parsed);
  }
  function canOperate() { return ["admin", "operator"].includes(state.user?.role); }
  function isAdmin() { return state.user?.role === "admin"; }
  function isKilled() { return Boolean(first(state.data?.kill_switch, state.data?.policy?.kill_switch, false)); }
  function isDemo() { return String(first(state.data?.mode, state.data?.policy?.mode, "DEMO")).toUpperCase() === "DEMO"; }
  function key(prefix) { return prefix + ":" + crypto.randomUUID(); }
  function toast(message, failed = false) {
    clearTimeout(state.toastTimer);
    const box = byId("toast");
    box.textContent = message;
    box.classList.toggle("is-error", failed);
    box.hidden = false;
    state.toastTimer = setTimeout(() => { box.hidden = true; }, 6000);
  }
  function showError(message) { text("global-error-text", message); byId("global-error").hidden = false; }
  function errorMessage(payload, status) {
    const detail = payload?.detail;
    const code = first(payload?.code, payload?.error, detail?.code);
    if (errorNames[code]) return errorNames[code];
    if (status === 401) return "E-postadressen eller lösenordet stämmer inte, eller så har sessionen löpt ut.";
    if (Array.isArray(detail)) return "Kontrollera de angivna uppgifterna. Servern kunde inte godkänna formuläret.";
    const message = first(payload?.message, detail?.message, typeof detail === "string" ? detail : null);
    if (typeof message === "string") return (code ? code + ": " : "") + message.slice(0, 500);
    if (status === 403) return "Servern tillåter inte den här åtgärden för ditt konto.";
    return code ? String(code).slice(0, 200) : "Åtgärden kunde inte genomföras. Försök igen eller kontrollera revisionsloggen.";
  }
  async function api(path, options = {}) {
    const method = options.method || "GET";
    const headers = { Accept: "application/json", ...options.headers };
    if (options.body !== undefined) headers["Content-Type"] = "application/json";
    if (method !== "GET" && state.csrf) headers["X-CSRF-Token"] = state.csrf;
    let response;
    try { response = await fetch(path, { method, headers, credentials: "same-origin", cache: "no-store", body: options.body === undefined ? undefined : JSON.stringify(options.body) }); }
    catch { throw new Error("Servern kunde inte nås. Kontrollera anslutningen och försök igen."); }
    let result = null;
    try { result = await response.json(); } catch { /* Healthier error messages do not expose HTML or raw response bodies. */ }
    if (!response.ok) {
      if (response.status === 401 && path !== "/api/auth/login") showLogin();
      throw new Error(errorMessage(result, response.status));
    }
    return result;
  }
  function setBusy(button, busy, label) {
    if (!button) return;
    if (busy) {
      button.dataset.previousLabel = button.textContent;
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
      button.textContent = label || "Arbetar…";
    } else {
      if (button.dataset.previousLabel) button.textContent = button.dataset.previousLabel;
      delete button.dataset.previousLabel;
      button.removeAttribute("aria-busy");
      button.disabled = false;
    }
  }
  async function mutate(button, path, body, success) {
    setBusy(button, true);
    try {
      const result = await api(path, { method: "POST", body });
      toast(success);
      await refresh();
      return result;
    } catch (error) { showError(error.message); toast(error.message, true); return null; }
    finally { setBusy(button, false); if (state.data) applyPermissions(); }
  }
  function showLogin() {
    state.user = null; state.csrf = null; state.data = null; state.v12 = null; state.crm = null; state.crmQuery = null;
    byId("app-shell").hidden = true;
    byId("login-screen").hidden = false;
    for (const form of document.querySelectorAll(".config-form")) { form.reset(); delete form.dataset.hydrated; delete form.dataset.dirty; }
    for (const dialog of document.querySelectorAll("dialog[open]")) dialog.close();
  }
  function establishSession(result) {
    state.user = result?.user || result;
    state.csrf = first(result?.csrf_token, result?.csrfToken);
    if (!state.user?.role || !state.csrf) throw new Error("Sessionen saknar nödvändiga behörighetsuppgifter.");
    byId("login-screen").hidden = true;
    byId("app-shell").hidden = false;
    text("user-role", roleNames[state.user.role] || state.user.role);
    text("user-avatar", String(state.user.email || "J").slice(0, 1).toUpperCase());
    byId("user-avatar").title = state.user.email || "Jarvis";
  }
  function badge(status, forcedText) {
    const upper = String(status || "PENDING").toUpperCase();
    let color = "neutral";
    if (["QUALIFIED", "READY_TO_CONTACT", "COMPLETED", "DELIVERY_VERIFIED", "CONFIRMED", "PAYMENT_CONFIRMED", "DONE", "ACCEPTED", "APPROVED"].includes(upper)) color = "success";
    else if (["FAILED", "PAYMENT_FAILED", "DELIVERY_FAILED", "REJECTED", "CANCELLED", "UNSUBSCRIBED"].includes(upper)) color = "danger";
    else if (["MANUAL_REVIEW", "PAUSED", "UNCERTAIN", "PAYMENT_APPROVAL_REQUIRED", "DRAFT"].includes(upper)) color = "warning";
    else if (["RESEARCHED", "CONTACTED", "REPLIED", "PROPOSAL_SENT", "REQUESTED", "INITIATED", "QUEUED", "LEASED", "RUNNING"].includes(upper)) color = "blue";
    return el("span", "badge badge-" + color, forcedText || statusLabel(upper));
  }
  function cell(content, className) {
    const node = el("td", className);
    node.append(content instanceof Node ? content : document.createTextNode(String(content ?? "—")));
    return node;
  }
  function actionButton(label, handler, options = {}) {
    const button = el("button", "button button-small " + (options.primary ? "button-primary" : "button-secondary"), label);
    button.type = "button";
    button.disabled = Boolean(options.disabled);
    button.addEventListener("click", () => handler(button));
    return button;
  }

  function recordMode(record) {
    if (record?.record_mode === "DEMO" || record?.is_test === true || record?.source_id === "demo" || record?.provider === "demo") return "DEMO";
    if (record?.record_mode === "REAL") return "REAL";
    return "UNCLASSIFIED";
  }
  function modeLabel(record) {
    return { REAL: "Verkliga data", DEMO: "Demodata", UNCLASSIFIED: "Äldre / ej klassificerad" }[recordMode(record)];
  }
  function taskNeedsDedicatedAction(task) {
    return task.role === "FINANCE" || task.type === "job_review" || /payment|approve|approval|legal|compliance|resume|reconcile/i.test(String(task.title || ""));
  }
  function realMetrics(data) {
    const metrics = data.v11?.metrics || {};
    const real = list(data.leads).filter((lead) => recordMode(lead) === "REAL");
    const paid = list(data.payments).filter((payment) => payment.livemode === true && payment.provider !== "demo" && asStatus(payment) === "CONFIRMED");
    return { ...metrics,
      real_leads: first(metrics.real_leads, real.length),
      real_confirmed_revenue_minor: first(metrics.real_confirmed_revenue_minor, paid.reduce((sum, payment) => sum + number(payment.amount_minor), 0)),
      real_paying_customers: first(metrics.real_paying_customers, new Set(paid.map((payment) => payment.lead_id)).size)
    };
  }
  function dataVersionReady() { return Boolean(state.data?.v11 || state.v12); }
  function aiStatus() { return state.v12?.ai || state.data?.v11?.ai || {}; }
  function summaryRows(id, entries) {
    const target = byId(id);
    target.replaceChildren();
    for (const [label, value] of entries) target.append(el("dt", "", label), el("dd", "", value));
  }
  function hydrateConfig(formId, values) {
    const form = byId(formId);
    if (form.dataset.dirty || form.contains(document.activeElement)) return;
    for (const [id, value] of Object.entries(values)) {
      if (value === undefined || value === null) continue;
      if (byId(id).type === "checkbox") byId(id).checked = Boolean(value);
      else byId(id).value = String(value);
    }
    form.dataset.hydrated = "true";
  }
  function renderPreparation(data) {
    const v = data.v11 || {};
    const m = realMetrics(data);
    const demoLeads = list(data.leads).filter((lead) => recordMode(lead) === "DEMO").length;
    const legacyLeads = list(data.leads).filter((lead) => recordMode(lead) === "UNCLASSIFIED").length;
    text("real-lead-count", integer.format(number(m.real_leads)));
    text("demo-lead-count", integer.format(first(m.demo_leads, demoLeads)));
    text("legacy-lead-count", integer.format(first(m.unclassified_leads, legacyLeads)));
    text("demo-revenue", amount(first(m.simulated_revenue_minor, data.metrics?.simulated_revenue_minor, 0)));
    text("real-activity-note", "Verkliga kontaktförsök: " + integer.format(number(m.real_contact_attempts)) + " · Leverantörsgodkända utskick: " + integer.format(number(m.real_contacts_sent)) + " · Oskickade utkast: " + integer.format(number(m.pending_drafts)));
    const ai = aiStatus();
    const aiConfig = ai.config || {};
    const aiPaid = ai.effective_paid_enabled === true;
    text("ai-state", aiPaid ? "Betalda anrop separat tillåtna" : "Betalda anrop spärrade");
    byId("ai-state").className = "badge badge-" + (aiPaid ? "warning" : "neutral");
    summaryRows("ai-summary", [
      ["Modell", first(aiConfig.model, "Ej konfigurerad")],
      ["Servernyckel", ai.key_configured ? "Konfigurerad · dold" : "Ej konfigurerad"],
      ["Kostnadstak per anrop", amount(aiConfig.per_call_limit_sek_minor)],
      ["Runtime-tillstånd", ai.readiness?.runtime === true ? "Tillåtet på servern · inget administratörsbeslut" : "Serverns kostnadsspärr gäller"],
      ["Administratörsbeslut", ai.readiness?.administrator === true ? "Aktuellt godkännande registrerat" : "Separat godkännande saknas eller behöver förnyas"],
      ["Modellrevision", first(ai.config_revision, aiConfig.revision, "Ej granskad")],
      ["Spårbarhet", ai.last_trace ? "Senaste anrop registrerat" : "Inga registrerade anrop"]
    ]);
    byId("ai-blockers").replaceChildren(...list(ai.blockers).map((entry) => el("li", "", typeof entry === "string" ? entry : first(entry.message, entry.code, "Kontroll krävs"))));
    let traceBox = byId("ai-last-trace");
    if (!traceBox) { traceBox = el("div"); traceBox.id = "ai-last-trace"; byId("ai-summary").after(traceBox); }
    traceBox.replaceChildren(ai.last_trace ? resultDetails("Senaste registrerade AI-anrop · status, token och kostnad", ai.last_trace) : el("p", "muted small", "Inga AI-anrop registrerade ännu."));
    hydrateConfig("ai-config-form", { "ai-model": first(aiConfig.model, "openai/gpt-4o-mini"), "ai-call-sek": number(first(aiConfig.per_call_limit_sek_minor, 200)) / 100, "ai-max-tokens": first(aiConfig.max_output_tokens, 512), "ai-timeout": first(aiConfig.timeout_seconds, 20), "ai-retries": first(aiConfig.max_retries, 1) });
    const pipeline = v.pipeline || {};
    text("pipeline-state", pipeline.requires_reauthorization ? "Kräver ny granskning" : pipeline.enabled ? "Förberedelser schemalagda" : "Schema avstängt");
    byId("pipeline-state").className = "badge badge-" + (pipeline.requires_reauthorization ? "warning" : pipeline.enabled ? "success" : "neutral");
    summaryRows("pipeline-summary", [
      ["Arbetssätt", "Förberedelse · inget livegodkännande"],
      ["Källor", list(pipeline.source_ids).join(", ") || "Inga valda"],
      ["Intervall", pipeline.interval_seconds ? integer.format(pipeline.interval_seconds / 60) + " minuter" : "Ej konfigurerat"],
      ["Blockerade steg", integer.format(number(pipeline.blocked_count))],
      ["Väntar på granskning", integer.format(number(pipeline.awaiting_review_count))],
      ["Betalningsordning", pipeline.billing_stage === "before_delivery" ? "Före leverans · separat granskade villkor" : "Efter verifierad leverans"],
      ["Senast schemalagt", date(pipeline.last_scheduled_at)]
    ]);
    hydrateConfig("pipeline-config-form", { "pipeline-enabled": Boolean(pipeline.enabled), "pipeline-sources": list(pipeline.source_ids).join(", "), "pipeline-interval": number(first(pipeline.interval_seconds, 3600)) / 60, "pipeline-max-leads": first(pipeline.max_leads_per_tick, 5), "pipeline-followup": number(first(pipeline.follow_up_delay_seconds, 259200)) / 86400 });
    const sources = list(v.research_sources);
    const sourceBox = byId("sources-list");
    sourceBox.replaceChildren();
    text("sources-count", sources.length);
    if (!sources.length) sourceBox.append(el("p", "empty-state", "Inga v1.1-källor har granskats ännu. Befintliga leads bevaras och klassificeras separat."));
    for (const source of sources) {
      const item = el("div", "source-item");
      const heading = el("div", "task-heading");
      heading.append(el("strong", "", first(source.name, source.id)), badge(source.approved ? "APPROVED" : "MANUAL_REVIEW", source.approved ? "Tillåten källa" : "Kräver granskning"));
      item.append(heading, el("p", "muted small", source.id + " · " + (source.kind === "https_json" ? "Granskad HTTPS-källa" : "Godkänd katalogimport") + " · " + (source.data_mode === "DEMO" ? "Demodata" : "Verkliga uppgifter")));
      item.append(el("p", "muted small", "Kvalitetskrav: " + first(source.minimum_quality, "—") + " / 100 · Kostnad per hämtning: " + amount(first(source.cost_minor, 0))));
      if (source.url) item.append(el("p", "source-reference", source.url));
      if (source.legal_review_reference) item.append(el("p", "muted small", "Tillståndsreferens: " + source.legal_review_reference));
      if (source.kind === "upload" && source.approved && source.data_mode !== "DEMO") item.append(actionButton("Importera företagsunderlag", () => {
        byId("import-lead-form").reset();
        byId("import-lead-error").hidden = true;
        byId("import-source-id").value = source.id;
        byId("import-reference").value = source.url || "";
        byId("import-lead-dialog").showModal();
        byId("import-company").focus();
      }, { disabled: !isAdmin() }));
      sourceBox.append(item);
    }
    const outreach = v.outreach || {};
    const checks = outreach.checks || {};
    const contactEnabled = false;
    text("contact-gate-badge", contactEnabled ? "Separat kontaktgodkännande finns" : "Kundkontakt spärrad");
    byId("contact-gate-badge").className = "badge badge-" + (contactEnabled ? "warning" : "neutral");
    text("outreach-state", "Spärrad i v1.2");
    summaryRows("outreach-summary", [
      ["Leverantör", first(outreach.provider, outreach.config?.provider, "Brevo · kräver granskning")],
      ["Domänunderlag", checks.reviewed_domain_evidence === true ? "Operatörsgranskat underlag registrerat" : "Operatörsgranskning krävs"],
      ["Avsändare & juridisk policy", checks.reviewed_sender_and_lawful_policy === true ? "Granskningsreferens registrerad" : "Ej godkänd"],
      ["Testomfattning", outreach.test_scope === "isolated_offline_safety_probe" ? "Isolerad offlinekontroll · inte leveranstest" : "Separat testunderlag krävs"],
      ["Serverns kontaktspärr", "Permanent spärrad i v1.2"],
      ["Betalningsåtgärder", "Permanent spärrade i v1.2 · historik bevaras"]
    ]);
    text("preparation-status", "Godkända källor, verkliga leads och interna AI-utkast kan förberedas. Verkliga kundutskick och betalningar är spärrade i v1.2, även om äldre runtime-inställningar finns.");
    byId("preparation").classList.toggle("version-unavailable", !data.v11);
  }

  function renderMetrics(data) {
    const m = realMetrics(data);
    const leads = list(data.leads);
    const paying = number(m.real_paying_customers);
    text("metric-leads", integer.format(number(m.real_leads)));
    text("nav-lead-count", integer.format(leads.length));
    text("metric-qualified", integer.format(number(m.real_qualified)) + " kvalificerade verkliga leads");
    text("metric-proposals", integer.format(number(m.real_proposals)));
    text("metric-accepted", integer.format(number(m.real_accepted)) + " accepterade verkliga offerter");
    text("metric-paying", integer.format(paying));
    text("metric-revenue", amount(m.real_confirmed_revenue_minor));
    text("funnel-contacts", integer.format(number(m.real_contacts_sent)));
    text("funnel-replies", integer.format(number(m.real_replies)));
    text("funnel-proposals", integer.format(number(m.real_proposals)));
    text("funnel-accepted", integer.format(number(m.real_accepted)));
    text("funnel-paying", integer.format(paying));
    text("finance-outstanding", amount(number(m.real_outstanding_minor)));
    text("finance-margin", amount(number(m.real_confirmed_revenue_minor) - number(data.budget?.actual_sek_minor)));
    const policy = data.policy || {};
    text("finance-price-rule", amount(policy.minimum_list_price_minor) + " / " + (policy.max_discount_bps === undefined ? "—" : number(policy.max_discount_bps) / 100 + " %"));
  }
  function renderBudget(data) {
    const budget = data.budget || {};
    const policy = data.policy || {};
    const total = first(budget.limits_minor?.total, budget.total_limit_minor, policy.total_budget_minor);
    const used = first(budget.usage?.total, budget.used_minor, number(budget.actual_sek_minor) + number(budget.reserved_sek_minor));
    const remaining = first(typeof budget.remaining_minor === "object" ? budget.remaining_minor.total : budget.remaining_minor, total !== undefined ? Math.max(0, total - used) : null);
    const percent = total > 0 ? Math.min(100, Math.max(0, Math.round(used / total * 100))) : 0;
    text("budget-remaining", amount(remaining));
    text("budget-used", amount(used) + " använt / reserverat");
    text("budget-total", amount(total) + " totalt");
    text("finance-reserved", amount(first(budget.reserved_sek_minor, budget.reserved_minor, 0)));
    byId("budget-progress").setAttribute("aria-valuenow", String(percent));
    byId("budget-progress-bar").style.width = percent + "%";
    byId("budget-progress").classList.toggle("is-warning", percent >= 75 && percent < 100);
    byId("budget-progress").classList.toggle("is-danger", percent >= 100);
    const blocked = list(budget.blocked_windows).length > 0 || (total !== undefined && used >= total);
    text("budget-health", blocked ? "Budgetspärr" : percent >= 75 ? "Bevaka kostnaden" : "Inom mandatet");
    byId("budget-health").className = "badge badge-" + (blocked ? "danger" : percent >= 75 ? "warning" : "success");
    const aiCost = first(budget.by_category?.ai?.actual_sek_minor, budget.ai_minor, budget.ai_cost_minor, budget.usage?.ai, 0);
    // Categories must be reported by the server, never guessed from provider names.
    text("budget-ai", amount(aiCost));
    text("budget-tools", amount(first(budget.by_category?.tools?.actual_sek_minor, budget.tools_cost_minor, 0)));
    text("budget-infra", amount(first(budget.by_category?.infrastructure?.actual_sek_minor, budget.infrastructure_minor, 0)));
    byId("budget-ai").title = "Faktisk registrerad AI-kostnad. Pågående reserveringar ingår i totalbudgetens spärr.";
    byId("budget-tools").title = "Faktisk registrerad verktygskostnad. Rullande gräns för AI och verktyg visas separat nedan.";
    byId("budget-infra").title = "Faktisk registrerad infrastrukturkostnad. Externa fakturor måste räknas in även när applikationen stoppas.";
    let windows = byId("budget-windows");
    if (!windows) {
      windows = el("div", "budget-windows"); windows.id = "budget-windows";
      document.querySelector(".mini-budget-grid").after(windows);
    }
    windows.replaceChildren();
    for (const [name, label] of [["daily", "Rullande 24 timmar"], ["monthly", "Rullande 30 dagar"], ["tools_30d", "AI & verktyg · 30 dagar"]]) {
      if (budget.limits_minor?.[name] === undefined) continue;
      const row = el("div");
      row.append(el("span", "", label), el("strong", "", amount(budget.usage?.[name] || 0) + " / " + amount(budget.limits_minor[name])));
      windows.append(row);
    }
    const days = budget.forecast_days_remaining;
    text("budget-forecast", days !== undefined && days !== null ? "Prognos vid nuvarande förbrukning: cirka " + integer.format(Math.max(0, days)) + " dagar kvar. Fasta leverantörskostnader kan fortsätta även om Jarvis stoppas." : "Förbrukningsprognos skapas när kostnader har registrerats. Fasta leverantörskostnader kräver separata leverantörsgränser.");
    const alerts = byId("budget-alerts");
    alerts.replaceChildren();
    for (const alert of list(budget.alerts).slice(0, 3)) {
      const threshold = first(alert.threshold_percent, alert.threshold, alert.percentage);
      const windowNames = { total: "startbudgeten", monthly: "30-dagarsbudgeten", daily: "dygnsbudgeten", tools_30d: "verktygsbudgeten", infrastructure_monthly: "infrastrukturbudgeten" };
      const windowName = windowNames[alert.window] || "budgetgränsen";
      const note = first(alert.message, alert.description, threshold !== undefined ? "Budgetvarning: " + threshold + " % av " + windowName + "." : "En budgetgräns behöver granskas.");
      alerts.append(el("p", "budget-alert", note));
    }
  }
  function company(lead) { return first(lead.company, lead.company_name, lead.name, "Namnlöst företag"); }
  function renderLeads(data) {
    const search = byId("lead-search").value.trim().toLocaleLowerCase("sv-SE");
    const leads = list(data.leads).filter((lead) => [company(lead), lead.email, statusLabel(asStatus(lead))].join(" ").toLocaleLowerCase("sv-SE").includes(search));
    const body = byId("leads-table-body");
    body.replaceChildren();
    for (const lead of leads) {
      const row = el("tr");
      const identity = el("div");
      identity.append(el("span", "cell-title", company(lead)), el("span", "cell-subtitle", lead.email || "E-post saknas"));
      const source = typeof lead.source === "object" ? first(lead.source.name, lead.source.type, "Dokumenterad källa") : first(lead.source_id, lead.source, "Källa saknas");
      const actions = actionButton("Visa", () => showLead(lead));
      actions.setAttribute("aria-label", "Visa " + company(lead));
      row.append(cell(identity), cell(badge(asStatus(lead))), cell(source), cell(badge(recordMode(lead) === "REAL" ? "APPROVED" : "PENDING", modeLabel(lead))), cell(actions));
      body.append(row);
    }
    text("lead-table-count", leads.length + " av " + list(data.leads).length + " leads");
    byId("leads-empty").hidden = leads.length > 0;
    if (!leads.length && search) text("leads-empty", "Inga leads matchar sökningen.");
    else text("leads-empty", "Inga leads ännu. Starta en demoresa för att se hela arbetsflödet.");
  }
  function showLead(lead) {
    text("lead-dialog-title", company(lead));
    const detail = byId("lead-details");
    detail.replaceChildren();
    const compliance = lead.compliance || {};
    const items = [
      ["Kontakt", lead.email], ["Status", statusLabel(asStatus(lead))], ["Datatyp", modeLabel(lead)],
      ["Källa", typeof lead.source === "object" ? first(lead.source.name, lead.source.type) : first(lead.source_id, lead.source)],
      ["Insamlat", date(first(lead.collected_at, lead.discovered_at, lead.created_at))], ["Källreferens", first(lead.source_url, lead.source_reference)],
      ["Källunderlag", first(lead.research_evidence, lead.citations?.map((entry) => entry.evidence).filter(Boolean).join("\n"), lead.provenance?.evidence, lead.provenance?.source_url)],
      ["Kvalitetsbedömning", first(lead.quality?.score, lead.research_quality?.score, lead.quality_score, lead.research?.quality_score)],
      ["Kvalitetsunderlag", first(lead.quality?.factors, lead.research_quality?.reasons, lead.quality_reasons)],
      ["Behov", first(lead.need, lead.needs, lead.research?.need, lead.research?.summary)],
      ["Kontaktpolicy", first(compliance.status, compliance.approved ? "Godkänd granskning" : "Ej godkänd")],
      ["Laglig grund", first(compliance.lawful_basis, compliance.legal_basis, lead.lawful_basis)],
      ["Nästa steg", first(lead.next_action, lead.next_step)]
    ];
    for (const [name, value] of items) {
      if (value === undefined || value === null || value === "") continue;
      detail.append(el("dt", "", name), el("dd", "", typeof value === "object" ? JSON.stringify(value) : value));
    }
    const actions = byId("lead-dialog-actions");
    actions.replaceChildren();
    if (dataVersionReady() && canOperate() && !isKilled() && recordMode(lead) === "REAL" && !["UNSUBSCRIBED", "CANCELLED", "REJECTED"].includes(asStatus(lead))) {
      actions.append(actionButton("Förbered nästa steg", async (button) => {
        const result = await mutate(button, "/api/pipeline/leads/" + encodeURIComponent(lead.id) + "/advance", { idempotency_key: key("prepare-lead") }, "Nästa steg har köats. Separata serverkontroller styr alla externa åtgärder.");
        if (result !== null) byId("lead-dialog").close();
      }));
    }
    if (canOperate() && asStatus(lead) !== "UNSUBSCRIBED") {
      actions.append(actionButton("Avregistrera kontakt", async (button) => {
        if (!window.confirm("Avregistrera kontakten och blockera framtida kontaktförsök?")) return;
        const result = await mutate(button, "/api/leads/" + encodeURIComponent(lead.id) + "/unsubscribe", { reason: "Avregistrerad av operatör i kontrollpanelen" }, "Kontakten är avregistrerad.");
        if (result !== null) byId("lead-dialog").close();
      }));
    }
    byId("lead-dialog").showModal();
  }
  function approved(payment) { return Boolean(payment.approval?.actor_id || payment.approved_by || payment.approved_at || ["APPROVED", "REQUESTED", "INITIATED", "CONFIRMED"].includes(asStatus(payment))); }
  function renderApprovals(data) {
    const payments = list(data.payments);
    const pending = payments.filter((payment) => asStatus(payment) === "DRAFT" || asStatus(payment) === "PAYMENT_APPROVAL_REQUIRED");
    const extra = list(data.approvals).filter((approval) => !payments.some((payment) => payment.id === first(approval.payment_id, approval.id)) && !["APPROVED", "COMPLETED", "DONE"].includes(asStatus(approval)));
    const box = byId("approval-list");
    box.replaceChildren();
    text("approval-count", pending.length + extra.length);
    if (!pending.length && !extra.length) {
      box.append(el("p", "empty-state", "Inga beslut väntar just nu. Jarvis stannar här när en betalningsbegäran behöver ditt godkännande."));
      return;
    }
    for (const payment of pending.slice(0, 4)) {
      const card = el("div", "approval-item");
      const detail = el("div");
      detail.append(el("strong", "", payment.provider === "demo" ? "Godkänn simulerad betalningsbegäran" : "Historiskt betalningsbeslut · spärrat i v1.2"), el("p", "", first(payment.reference, payment.customer_name, payment.id.slice(0, 8)) + " · " + amount(payment.total_minor ?? payment.amount_minor) + " inkl. moms"));
      card.append(detail, paymentButton(payment, "approve"));
      box.append(card);
    }
    for (const approval of extra.slice(0, 3)) {
      const card = el("div", "approval-item");
      const detail = el("div");
      detail.append(el("strong", "", first(approval.title, approval.action, "Manuell granskning")), el("p", "", first(approval.reason, approval.description, "Granska uppgiften innan arbetet fortsätter.")));
      card.append(detail, badge("MANUAL_REVIEW"));
      box.append(card);
    }
  }
  function paymentButton(payment, action) {
    if (payment.provider !== "demo") return el("span", "muted small", "Betalningsåtgärder spärrade i v1.2");
    const options = { disabled: !isAdmin() || isKilled(), primary: action === "approve" };
    if (action === "approve") return actionButton("Godkänn", async (button) => {
      const label = amount(first(payment.total_minor, payment.amount_minor));
      const lead = list(state.data?.leads).find((item) => item.id === payment.lead_id);
      const review = "Kund: " + (lead ? company(lead) + " / " + lead.email : payment.lead_id) + "\nTjänst: " + payment.service_id + "\nReferens: " + payment.reference + "\nExkl. moms: " + amount(payment.amount_minor) + "\nMoms: " + amount(payment.vat_minor) + "\nTotalt: " + label + "\n\n";
      if (!window.confirm(review + "Godkänn just denna betalningsbegäran? Godkännandet skapar inte en betalningslänk förrän nästa steg väljs.")) return;
      await mutate(button, "/api/payments/" + encodeURIComponent(payment.id) + "/approve", {}, "Betalningsbegäran är manuellt godkänd.");
    }, options);
    if (action === "request") return actionButton(isDemo() ? "Skapa demobegäran" : "Skapa begäran", async (button) => {
      if (!isDemo() && !window.confirm("Skapa en verklig, tidigare godkänd betalningsbegäran? Serverns livekontroller måste vara uppfyllda.")) return;
      await mutate(button, "/api/payments/" + encodeURIComponent(payment.id) + "/request", { idempotency_key: key("payment") }, isDemo() ? "Simulerad betalningsbegäran skapad." : "Betalningsbegäran skapad.");
    }, options);
    return actionButton("Simulera betalning", async (button) => {
      await mutate(button, "/api/payments/" + encodeURIComponent(payment.id) + "/simulate-paid", { event_id: key("demo-event") }, "Signerad demohändelse behandlad. Ingen verklig betalning har gjorts.");
    }, options);
  }
  function renderPayments(data) {
    const payments = list(data.payments);
    const body = byId("payments-table-body");
    body.replaceChildren();
    for (const payment of payments) {
      const row = el("tr");
      const title = el("div");
      const paymentLabel = payment.provider === "demo" ? "Simulerad betalning · aldrig verklig intäkt" : payment.livemode === true ? "Verklig leverantörsbetalning" : "Sandbox / ej verifierad som verklig betalning";
      title.append(el("span", "cell-title", first(payment.reference, payment.id.slice(0, 8))), el("span", "cell-subtitle", paymentLabel));
      const actions = el("div", "table-action-group");
      const status = asStatus(payment);
      if (status === "DRAFT" || status === "PAYMENT_APPROVAL_REQUIRED") actions.append(paymentButton(payment, "approve"));
      else if (status === "APPROVED") actions.append(paymentButton(payment, "request"));
      else if (isDemo() && ["REQUESTED", "INITIATED"].includes(status)) actions.append(paymentButton(payment, "simulate-paid"));
      else actions.append(el("span", "muted small", status === "CONFIRMED" ? "Verifierad leverantörshändelse" : "Ingen automatisk åtgärd"));
      row.append(cell(title), cell(amount(first(payment.amount_minor, payment.price_minor))), cell(badge(status)), cell(approved(payment) ? "Manuellt godkänd" : "Krävs"), cell(actions));
      body.append(row);
    }
    byId("payments-empty").hidden = payments.length > 0;
  }
  function renderJobs(data) {
    const jobs = list(data.jobs);
    const body = byId("jobs-table-body");
    body.replaceChildren();
    for (const job of jobs.slice(0, 20)) {
      const row = el("tr");
      const title = el("div");
      title.append(el("span", "cell-title", jobNames[job.kind] || job.kind || job.type || "Arbetsflöde"), el("span", "cell-subtitle", date(first(job.run_at, job.created_at))));
      if (job.result || job.error_code) title.append(resultDetails("Jobbresultat och blockeringsorsak", { id: job.id, result: job.result, error_code: job.error_code, reason: job.reason }));
      const actions = el("div", "table-action-group");
      const status = asStatus(job);
      if (["PAUSED", "FAILED"].includes(status)) {
        actions.append(actionButton("Granska / återuppta", async (button) => {
          if (!window.confirm("Granska jobbets senaste fel och behörigheter innan det återupptas. Fortsätt med ett uttryckligt återupptagningsbeslut?")) return;
          await mutate(button, "/api/jobs/" + encodeURIComponent(job.id) + "/resume", {}, "Jobbet har fått ett uttryckligt återupptagningsbeslut.");
        }, { disabled: !isAdmin() || isKilled() }));
      } else if (["QUEUED", "LEASED", "RUNNING"].includes(status)) {
        actions.append(actionButton("Pausa", async (button) => {
          await mutate(button, "/api/jobs/" + encodeURIComponent(job.id) + "/pause", {}, "Jobbet är pausat.");
        }, { disabled: !isAdmin() }));
      } else actions.append(el("span", "muted small", status === "UNCERTAIN" ? "Avstämning krävs före nytt försök" : first(job.error_code, job.reason, "—")));
      if (["internal_agent", "factory_generate", "factory_delivery"].includes(job.kind) && ["QUEUED", "PAUSED", "FAILED"].includes(status)) actions.append(actionButton("Avbryt internt jobb", async (button) => {
        const reason = window.prompt("Varför ska detta interna jobb avbrytas?", "Avbrutet efter operatörsgranskning");
        if (!reason || reason.trim().length < 12) return;
        await mutate(button, "/api/internal/jobs/" + encodeURIComponent(job.id) + "/cancel", { reason: reason.trim() }, "Det interna jobbet är avbrutet.");
      }, { disabled: !isAdmin() }));
      row.append(cell(title), cell(badge(status)), cell(number(job.attempts) + " / " + first(job.max_attempts, 3)), cell(actions));
      body.append(row);
    }
    text("jobs-count", jobs.length + " registrerade jobb");
    byId("jobs-empty").hidden = jobs.length > 0;
    const resolved = (task) => ["DONE", "COMPLETED", "RESOLVED", "SUPERSEDED", "TEST_ARTIFACT"].includes(asStatus(task));
    const filter = byId("tasks-filter").value;
    const tasks = list(data.tasks).filter((task) => filter === "all" || (filter === "resolved" ? resolved(task) : !resolved(task)));
    const taskBox = byId("tasks-list");
    taskBox.replaceChildren();
    text("tasks-count", list(data.tasks).filter((task) => !resolved(task)).length);
    if (!tasks.length) taskBox.append(el("p", "empty-state", filter === "pending" ? "Inga manuella uppgifter väntar." : "Inga uppgifter i den här gruppen."));
    for (const task of tasks.slice(0, 30)) {
      const item = el("div", "task-item");
      const lead = list(data.leads).find((entry) => entry.id === task.lead_id);
      const context = first(task.company, lead ? company(lead) : null, task.context_label, task.enquiry_id ? "Förfrågan " + task.enquiry_id.slice(0, 8) : task.id.slice(0, 8));
      const header = el("div", "task-heading");
      header.append(el("strong", "", first(task.title, task.kind, "Manuell uppgift")), badge(asStatus(task)));
      item.append(header, el("p", "task-context", context + " · " + modeLabel(task)));
      if (task.test_case !== undefined && task.test_case !== null) item.append(el("p", "", "Acceptanstestfall: " + (typeof task.test_case === "object" ? JSON.stringify(task.test_case) : task.test_case)));
      if (task.enquiry_excerpt) item.append(el("p", "task-excerpt", task.enquiry_excerpt));
      item.append(el("p", "", first(task.description, task.reason, task.message, task.instructions, "Granska revisionsloggen för nästa steg.")));
      if (!resolved(task) && dataVersionReady() && !taskNeedsDedicatedAction(task)) item.append(actionButton("Markera granskad", async (button) => {
        const reason = window.prompt("Beskriv vad du har granskat. Detta godkänner ingen betalning, kontakt eller leverans.", "");
        if (reason === null) return;
        if (reason.trim().length < 12) { toast("Ange en granskningsanteckning med minst 12 tecken.", true); return; }
        await mutate(button, "/api/tasks/" + encodeURIComponent(task.id) + "/resolve", { reason: reason.trim() }, "Uppgiften är registrerad som granskad. Inga externa åtgärder har godkänts.");
      }, { disabled: !canOperate() }));
      else if (!resolved(task) && taskNeedsDedicatedAction(task)) item.append(el("p", "", "Separat granskningssteg krävs. Använd betalningsbeslutet, kontaktgranskningen eller jobbets avstämning; en vanlig kvittens ger ingen behörighet."));
      taskBox.append(item);
    }
    const reports = list(data.reports).sort((a, b) => number(b.created_at) - number(a.created_at));
    const report = reports[0];
    if (report) {
      const content = first(report.summary, report.content, report.message, report.report);
      text("report-content", typeof content === "object" ? Object.entries(content).map(([name, value]) => name + ": " + (typeof value === "object" ? JSON.stringify(value) : value)).join("\n") : content || "Rapporten har registrerats.");
      text("report-time", date(report.created_at));
    } else { text("report-content", "Ingen rapport har skapats ännu."); text("report-time", ""); }
    renderServices(data);
  }
  function renderServices(data) {
    let panel = byId("service-registry");
    if (!panel) {
      panel = el("article", "panel service-registry"); panel.id = "service-registry";
      document.querySelector(".report-panel").before(panel);
    }
    panel.replaceChildren();
    const header = el("div", "panel-heading");
    header.append(el("h3", "", "Godkänt tjänsteregister"), el("span", "badge badge-neutral", "Enbart granskade erbjudanden"));
    panel.append(header);
    const services = list(data.services);
    if (!services.length) { panel.append(el("p", "empty-state", "Inga tjänster finns i registret.")); return; }
    const translations = {
      "Classify incoming enquiries": "Klassificera inkommande förfrågningar",
      "Prepare unsent reply drafts": "Förbereda svar som inte skickas automatiskt",
      "Register leads": "Registrera leads",
      "Suggest follow-ups": "Föreslå uppföljningar",
      "Classifies the agreed test cases": "Klassificerar överenskomna testfall",
      "Produces unsent draft replies": "Skapar oskickade svarsutkast",
      "Records a test lead and proposed follow-up": "Registrerar testlead och föreslagen uppföljning",
      "Emits a delivery test report without external writes": "Skapar leveransrapport utan externa skrivningar"
    };
    for (const service of services) {
      const item = el("div", "service-template");
      const overview = el("div");
      overview.append(el("h3", "", service.id === "inbound-enquiry-assistant" ? "AI-assistent för inkommande kundförfrågningar" : service.name));
      overview.append(el("p", "muted small", "Version " + first(service.version, "—") + " · " + (service.approved ? "Godkänt erbjudande" : "Kräver granskning") + " · Leveranstid " + first(service.delivery_days, "—") + " dagar"));
      const scope = el("ul");
      for (const entry of list(service.scope)) scope.append(el("li", "", translations[entry] || entry));
      overview.append(scope);
      const acceptance = el("div");
      acceptance.append(el("strong", "small", "Acceptanskriterier"));
      const criteria = el("ul");
      for (const entry of list(service.acceptance_criteria)) criteria.append(el("li", "", translations[entry] || entry));
      acceptance.append(criteria);
      item.append(overview, acceptance); panel.append(item);
    }
    panel.append(el("p", "footnote", "Produktionsinstallation kräver avtal, kundens uttryckliga behörighet och verifierad åtkomst. En godkänd lokal testleverans betyder inte att kundens system har installerats."));
  }
  function renderSecurity(data) {
    const killed = isKilled();
    const demo = isDemo();
    for (const id of ["mode-badge", "security-mode"]) {
      text(id, demo ? "DEMO" : "LIVE");
      byId(id).className = "badge " + (demo ? "badge-demo" : "badge-live");
    }
    byId("demo-notice").hidden = !demo;
    byId("kill-notice").hidden = !killed;
    text("kill-status", killed ? "Aktivt stopp" : "Ej aktiverat");
    byId("kill-status").className = "badge " + (killed ? "badge-danger" : "badge-success");
    text("kill-switch-button", killed ? "Häv nödstopp — återuppta inga jobb" : "Aktivera nödstopp");
    byId("kill-switch-button").classList.toggle("button-danger", !killed);
    byId("kill-switch-button").classList.toggle("button-secondary", killed);
    const connectionBox = byId("connections-list");
    connectionBox.replaceChildren();
    const connections = data.connections || {};
    const connectionRows = [["Databas", first(connections.database, "Beständig databas")], ["Jobbkö", first(connections.queue, "Databasbaserad")], ["AI-leverantör", aiStatus().effective_paid_enabled ? "OpenRouter · separat godkända betalda interna anrop" : "Betalda interna anrop spärrade · se AI-kontroller"], ["E-post", "Verkliga utskick spärrade i v1.2"], ["Betalningar", "Verkliga betalningsåtgärder spärrade i v1.2"], ["Din behörighet", roleNames[state.user?.role] || state.user?.role || "—"]];
    for (const [label, value] of connectionRows) {
      const safeValue = typeof value === "boolean" ? value ? "Konfigurerad" : "Ej konfigurerad" : typeof value === "object" ? first(value.status, value.mode, value.configured ? "Konfigurerad" : "Ej konfigurerad") : value;
      connectionBox.append(el("dt", "", label), el("dd", "", safeValue));
    }
    const audits = list(data.audit).slice().sort((a, b) => number(first(b.created_at, b.timestamp)) - number(first(a.created_at, a.timestamp)));
    const body = byId("audit-table-body");
    body.replaceChildren();
    for (const audit of audits.slice(0, 25)) {
      const row = el("tr");
      const actor = typeof audit.actor === "object" ? first(audit.actor.id, audit.actor.role, "System") : first(audit.actor_id, audit.actor, "System");
      const detail = audit.details || audit.detail || {};
      const detailText = typeof detail === "string" ? detail : Object.entries(detail).filter(([name]) => !/password|secret|token|ciphertext|signature|authorization/i.test(name)).slice(0, 4).map(([name, value]) => name + ": " + (typeof value === "object" ? "Registrerat" : String(value))).join(" · ");
      row.append(cell(date(first(audit.created_at, audit.timestamp))), cell(actor), cell(first(audit.action, audit.event, "Registrerad händelse")), cell(detailText || "—", "muted"));
      body.append(row);
    }
    byId("audit-empty").hidden = audits.length > 0;
  }
  const serviceNames = { market_analysis: "Marknadsanalys", seo_analysis: "SEO-analys", content_calendar: "Innehållskalender", marketing_strategy: "Marknadsföringsstrategi", prospecting_report: "Prospekteringsrapport" };
  const agentNames = { ceo: "CEO", lead_research: "Research", research: "Research", sales: "Sales", delivery: "Delivery", finance: "Finance", compliance: "Compliance" };
  function ids(value) { return [...new Set(String(value).split(",").map((entry) => entry.trim()).filter(Boolean))]; }
  function outputText(value) {
    if (value === undefined || value === null) return "Inget resultat registrerat.";
    return typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
  }
  function resultDetails(label, value, open = false) {
    const details = el("details", "output-details"); details.open = open;
    details.append(el("summary", "", label), el("pre", "output-text", outputText(value)));
    return details;
  }
  function chart(id, points, field, label, monetary = false) {
    const target = byId(id); target.replaceChildren();
    if (!points.length) { target.append(el("p", "empty-state", "Ingen registrerad tidsserie ännu. Ingen aktivitet uppskattas eller simuleras.")); return; }
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 560 180"); svg.setAttribute("role", "img"); svg.setAttribute("aria-label", label);
    const title = document.createElementNS(ns, "title"); title.textContent = label; svg.append(title);
    const values = points.map((point) => number(field(point)));
    const max = Math.max(1, ...values);
    const baseline = document.createElementNS(ns, "path"); baseline.setAttribute("d", "M38 145H550"); baseline.setAttribute("class", "chart-axis"); svg.append(baseline);
    const gap = Math.min(18, 230 / points.length), width = (500 - gap * points.length) / points.length;
    points.forEach((point, index) => {
      const rect = document.createElementNS(ns, "rect");
      const height = values[index] / max * 112;
      rect.setAttribute("x", String(43 + index * (width + gap))); rect.setAttribute("y", String(145 - height)); rect.setAttribute("width", String(Math.max(1, width))); rect.setAttribute("height", String(height)); rect.setAttribute("rx", "3"); rect.setAttribute("class", "chart-bar");
      const hint = document.createElementNS(ns, "title"); hint.textContent = first(point.day, point.date, "Dag " + (index + 1)) + ": " + (monetary ? amount(values[index]) : integer.format(values[index])); rect.append(hint); svg.append(rect);
    });
    for (const [x, y, value, anchor] of [[38, 22, monetary ? amount(max) : integer.format(max), "start"], [38, 167, first(points[0].day, points[0].date, "Första dagen"), "start"], [550, 167, first(points.at(-1).day, points.at(-1).date, "Senaste dagen"), "end"]]) {
      const node = document.createElementNS(ns, "text"); node.setAttribute("x", x); node.setAttribute("y", y); node.setAttribute("text-anchor", anchor); node.setAttribute("class", "chart-label"); node.textContent = value; svg.append(node);
    }
    target.append(svg, el("p", "footnote", "Totalt i visad period: " + (monetary ? amount(values.reduce((sum, n) => sum + n, 0)) : integer.format(values.reduce((sum, n) => sum + n, 0)))));
  }
  function renderEngine() {
    const v = state.v12;
    for (const section of document.querySelectorAll(".v12-section")) section.classList.toggle("version-unavailable", !v);
    text("engine-status", v ? "Databasregistrerad aktivitet" : "v1.2-API saknas");
    if (!v) return;
    const metrics = v.metrics || {}, totals = metrics.totals || metrics;
    const metricsBox = byId("engine-metrics"); metricsBox.replaceChildren();
    const items = [
      ["Bokförda AI-anrop", first(totals.ai_calls, totals.total_ai_calls), false],
      ["Indata / utdata, token", integer.format(number(totals.input_tokens)) + " / " + integer.format(number(totals.output_tokens)), "text"],
      ["AI-kostnad idag", first(totals.ai_cost_daily_minor, totals.ai_cost_today_minor), true],
      ["AI-kostnad, månad", first(totals.ai_cost_monthly_minor, totals.ai_cost_month_minor), true],
      ["Aktiva jobb", first(totals.active_jobs, list(state.data.jobs).filter((job) => ["QUEUED", "LEASED", "RUNNING"].includes(asStatus(job))).length), false],
      ["Slutförda jobb", first(totals.completed_jobs, list(state.data.jobs).filter((job) => ["COMPLETED", "DONE"].includes(asStatus(job))).length), false],
      ["Registrerade leverabler", first(totals.service_outputs, totals.deliverables, list(v.services?.results).length), false],
      ["Registrerad total kostnad", first(totals.actual_cost_minor, state.data.budget?.actual_sek_minor), true],
      ["Verifierade leads", totals.verified_leads, false], ["Källgranskningar", totals.research_reports, false],
      ["Interna offertutkast", totals.offers, false], ["Väntande granskningar", totals.pending_approvals, false],
      ["Verifierad verklig intäkt", totals.verified_revenue_minor, true], ["Verkligt nettoutfall", totals.net_outcome_minor, true],
      ["Återstående budget", totals.budget_remaining_minor, true], ["Reserverad kostnad", totals.reserved_cost_minor, true]
    ];
    for (const [label, value, type] of items) { const card = el("article", "small-stat"); card.append(el("span", "", label), el("strong", "", value === undefined ? "—" : type === "text" ? value : type ? amount(value) : integer.format(number(value)))); metricsBox.append(card); }
    const series = list(first(metrics.daily, metrics.series, metrics.daily_series));
    chart("activity-chart", series, (point) => first(point.ai_calls, point.calls, 0), "Registrerade AI-anrop per dag");
    chart("cost-chart", series, (point) => first(point.ai_cost_minor, point.cost_minor, 0), "Registrerad AI-kostnad i SEK per dag", true);
    const schedules = byId("engine-schedules"); schedules.replaceChildren();
    const scheduleItems = list(v.schedules?.items || v.schedules);
    if (!scheduleItems.length) schedules.append(el("p", "empty-state", "Inga interna scheman är registrerade. Förberedelseschemat kan granskas under AI & schemaläggning."));
    for (const schedule of scheduleItems) { const row = el("div", "source-item"); row.append(el("strong", "", first(schedule.name, schedule.kind, schedule.id)), el("p", "muted small", (schedule.enabled ? "Aktivt internt schema" : "Avstängt") + " · nästa körning " + date(first(schedule.next_run_at, schedule.next_at))), el("p", "muted small", first(schedule.blocker, schedule.last_error, "Extern kontakt och betalningar spärrade"))); schedules.append(row); }
    renderCRM(state.crm || v.crm || { items: [] }); renderFactory(v.services || {}); renderSalesOffers(v.services?.offers || []); renderAgents(v.agents || {}); renderReports(v.reports || {});
  }
  function renderCRM(data) {
    const target = byId("crm-results"); target.replaceChildren(); const leads = list(data.items || data.leads || data);
    text("crm-count", first(data.total, leads.length));
    const mode = byId("crm-mode").value;
    const shown = state.crm ? leads : leads.filter((lead) => !mode || recordMode(lead) === mode);
    if (!shown.length) target.append(el("p", "empty-state", "Inga leads matchar urvalet. Lägg först till verifierat underlag från en godkänd källa."));
    for (const lead of shown) {
      const card = el("article", "crm-card"); const header = el("div", "task-heading");
      header.append(el("h3", "", company(lead)), badge(recordMode(lead) === "REAL" ? "APPROVED" : "PENDING", modeLabel(lead)));
      card.append(header, el("p", "muted small", [lead.industry, lead.geography, lead.website || lead.domain].filter(Boolean).join(" · ") || "Bransch, geografi eller webbplats saknar verifierat källunderlag."));
      const assessment = first(lead.crm, lead.assessment, lead.crm_assessment, lead.latest_assessment, {});
      card.append(el("p", "small", "Bedömning: " + first(assessment.score, lead.ai_score, lead.score, lead.quality_score, "Ej bedömd") + " · " + first(assessment.reason, lead.need, lead.description, "Ingen källbelagd behovsanalys ännu.")));
      card.append(el("p", "muted small", "Källunderlag: " + (lead.source_verified === true ? "aktuell tillåten källa verifierad" : "kräver granskning") + " · registreringsbevis hos myndighet: ej verifierat"));
      if (assessment.next_action || lead.suggested_service) card.append(el("p", "small", "Nästa interna steg: " + first(assessment.next_action, "granska rekommendationen") + " · " + first(assessment.suggested_service, lead.suggested_service, "tjänst ej bedömd")));
      if (first(assessment.estimated_business_value_minor, lead.estimated_business_value_minor) !== undefined) card.append(el("p", "muted small", "Uppskattat affärsvärde: " + amount(first(assessment.estimated_business_value_minor, lead.estimated_business_value_minor)) + " · prognos, ingen intäkt"));
      if (list(lead.citations).length) card.append(resultDetails("Källhänvisningar (" + lead.citations.length + ")", lead.citations));
      if (Object.keys(assessment).length) card.append(resultDetails("Kvalificering och rekommendation", assessment));
      const actions = el("div", "button-row");
      actions.append(actionButton("Visa lead", () => showLead(lead)));
      for (const [label, suffix, message] of [["AI-kvalificera", "score", "Kvalificeringen har köats. Resultatet visas efter slutfört jobb."], ["Förbered offertutkast", "offer", "Ett internt offertjobb har köats. Inget har skickats."]]) actions.append(actionButton(label, async (button) => { state.crm = null; const result = await mutate(button, "/api/crm/leads/" + encodeURIComponent(lead.id) + "/" + suffix, { idempotency_key: key("crm-" + suffix) }, message); if (result) { showLead(lead); byId("lead-details").append(el("dt", "", "Registrerat internt jobb"), el("dd", "", outputText(result))); await loadCRM(); } }, { disabled: !canOperate() || isKilled() }));
      card.append(actions); target.append(card);
    }
  }
  function renderFactory(data) {
    const orders = list(data.orders || data.items), results = list(data.results);
    text("factory-count", orders.length); const target = byId("factory-orders"); target.replaceChildren();
    if (!orders.length) target.append(el("p", "empty-state", "Inga beställningar ännu. En beställning skapar ingen extern leverans."));
    for (const order of orders) {
      const card = el("article", "panel factory-order"); const header = el("div", "panel-heading");
      header.append(el("h3", "", serviceNames[first(order.type, order.service_type, order.kind)] || first(order.type, "Digital leverabel")), badge(asStatus(order))); card.append(header, el("p", "", order.brief), el("p", "muted small", order.id + " · " + date(order.created_at)));
      if (order.blocker || order.blocked_reason || order.error_code) card.append(el("p", "inline-feedback", first(order.blocker, order.blocked_reason, order.error_code)));
      const orderResults = results.filter((result) => result.order_id === order.id).concat(list(order.results));
      const actions = el("div", "button-row");
      if (!["COMPLETED", "CANCELLED", "REJECTED", "UNCERTAIN", "GENERATING"].includes(asStatus(order))) actions.append(actionButton("Köa generering", async (button) => { const result = await mutate(button, "/api/factory/orders/" + encodeURIComponent(order.id) + "/generate", { idempotency_key: key("factory-generate") }, "Genereringen har köats. Resultatet visas efter att det interna jobbet har slutförts."); if (result) text("factory-feedback", "Generering registrerad för " + order.id + ". Se jobbkö och resultat."); }, { disabled: !canOperate() || isKilled() }));
      if (asStatus(order) === "UNCERTAIN") card.append(el("p", "inline-feedback", "Osäkert leverantörsutfall kräver avstämning. Ingen ny generering startas från denna vy."));
      card.append(actions);
      for (const result of orderResults) {
        const block = el("div", "factory-result"); block.append(badge(asStatus(result)), resultDetails("Resultat · innehåll, källor och spårning", first(result.document, result.content, result.output, result), true));
        const resultActions = el("div", "button-row");
        for (const format of ["markdown", "json"]) { const link = el("a", "button button-small button-secondary", "Exportera " + (format === "markdown" ? "Markdown" : "JSON")); link.href = "/api/factory/results/" + encodeURIComponent(result.id) + "/export?format=" + format; link.setAttribute("download", ""); resultActions.append(link); }
        if (!["APPROVED", "REJECTED"].includes(asStatus(result))) for (const [label, decision] of [["Godkänn internt resultat", "approve"], ["Begär omarbetning", "reject"]]) resultActions.append(actionButton(label, async (button) => { const reason = window.prompt("Beskriv granskningen av källor, kvalitet och osäkerheter. Ingen kundkontakt godkänns.", ""); if (!reason || reason.trim().length < 12) return; await mutate(button, "/api/factory/results/" + encodeURIComponent(result.id) + "/review", { decision, reason: reason.trim() }, "Resultatgranskningen är registrerad."); }, { disabled: !isAdmin() }));
        block.append(resultActions); card.append(block);
      }
      target.append(card);
    }
    const types = byId("factory-types"); types.replaceChildren();
    for (const [id, name] of Object.entries(serviceNames)) types.append(el("p", "muted small", name + " · " + id));
  }
  function renderSalesOffers(offers) {
    let panel = byId("sales-offers");
    if (!panel) { panel = el("article", "panel agent-history"); panel.id = "sales-offers"; byId("crm").append(panel); }
    panel.replaceChildren(el("h3", "", "Interna offertutkast · inga utskick"));
    if (!list(offers).length) panel.append(el("p", "empty-state", "Inga verkliga AI-offertutkast har skapats. Ett köat jobb räknas inte som ett färdigt utkast."));
    for (const offer of list(offers)) {
      const content = offer.content || {}, row = el("div", "mission-item"), header = el("div", "task-heading");
      const lead = list(state.data?.leads).find((entry) => entry.id === offer.lead_id);
      header.append(el("strong", "", first(content.package_name, lead ? company(lead) : null, offer.id)), badge(asStatus(offer)));
      row.append(header, el("p", "muted small", "Lead " + offer.lead_id + " · spårning " + first(offer.trace_id, "saknas")), el("p", "small", first(content.fit_reason, "Inget giltigt AI-utkast registrerat.")));
      if (content.email_subject || content.email_body) row.append(resultDetails("Personligt meddelande · oskickat utkast", { subject: content.email_subject, body: content.email_body, follow_up_plan: content.follow_up_plan }, true));
      row.append(resultDetails("Offertpris, källor och granskningsunderlag", { price: offer.price, citations: offer.citations, administrator_approval: offer.administrator_approval, service_registry_approved: offer.service_registry_approved, note: offer.note }));
      if (["DRAFT", "REVIEW_REQUIRED"].includes(asStatus(offer)) && !offer.administrator_approval) { const actions = el("div", "button-row"); for (const [label, decision] of [["Godkänn internt offertutkast", "approve"], ["Avvisa offertutkast", "reject"]]) actions.append(actionButton(label, async (button) => { const reason = window.prompt("Granska tjänst, pris, källor och det oskickade meddelandet. Beslutet får inte tillåta kundkontakt eller betalning.", ""); if (!reason || reason.trim().length < 12) return; await mutate(button, "/api/crm/offers/" + encodeURIComponent(offer.id) + "/review", { decision, reason: reason.trim() }, "Det interna offertutkastet är granskat. Inget har skickats."); }, { disabled: !isAdmin() })); row.append(actions); }
      panel.append(row);
    }
  }
  function renderReports(reports) {
    let panel = byId("agent-reports");
    if (!panel) { panel = el("article", "panel agent-history"); panel.id = "agent-reports"; byId("agent-center").append(panel); }
    panel.replaceChildren(el("h3", "", "Faktiska planer och agentrapporter"));
    let count = 0;
    for (const [name, label] of [["plans", "CEO-plan"], ["research", "Källbelagd research"], ["compliance", "Policygranskning"], ["finance", "Ekonomisk granskning"], ["daily", "Verksamhetsrapport"]]) for (const report of list(reports[name])) { panel.append(resultDetails(label + " · " + first(report.id, report.mission_id, "registrerad") + " · " + date(report.created_at), report)); count++; }
    if (!count) panel.append(el("p", "empty-state", "Inga rapporter har registrerats. Blockerat arbete ger inga påhittade rapporter."));
    let research = byId("crm-research-reports");
    if (!research) { research = el("article", "panel agent-history"); research.id = "crm-research-reports"; byId("crm").append(research); }
    research.replaceChildren(el("h3", "", "Researchresultat med källor"));
    for (const report of list(reports.research)) research.append(resultDetails("Researchrapport " + report.id + " · " + date(report.created_at), report));
    if (!list(reports.research).length) research.append(el("p", "empty-state", "Ingen slutförd researchrapport ännu. Importerat underlag och AI-analyser redovisas var för sig."));
  }
  function renderAgents(data) {
    const roles = list(data.roles), missions = list(data.missions), tasks = list(data.tasks);
    const target = byId("agent-role-cards");
    const preserveRoleForms = target.contains(document.activeElement) || target.querySelector("form[data-dirty]");
    if (!preserveRoleForms) {
    const expandedRoles = new Set([...target.querySelectorAll("details.admin-settings[open] form")].map((form) => form.dataset.role));
    target.replaceChildren();
    if (!roles.length) target.append(el("p", "empty-state", "Inga agentstatusar tillgängliga. Ingen pågående aktivitet antas."));
    for (const role of roles) {
      const roleId = first(role.role, role.id, role.name); const card = el("article", "panel agent-role-card");
      const actuallyRunning = list(state.v12?.jobs || state.data?.jobs).some((job) => job.kind === "internal_agent" && job.payload?.role === roleId && ["LEASED", "RUNNING"].includes(asStatus(job)));
      const roleState = first(role.status, role.state, actuallyRunning ? "BUSY" : role.enabled === false || ["BLOCKED", "UNCERTAIN", "FAILED"].includes(asStatus(role.last_task || { status: "IDLE" })) ? "BLOCKED" : "IDLE");
      const header = el("div", "panel-heading"); header.append(el("h3", "", agentNames[roleId] || roleId), badge(roleState)); card.append(header);
      card.append(el("p", "muted small", first(role.description, role.purpose, "Intern agentroll med serverkontrollerade befogenheter.")), el("p", "small", "Uppgifter: " + number(role.tasks) + " · slutförda " + number(role.completed) + " · blockerade " + number(role.blocked)), el("p", "small", "Senast registrerat: " + first(role.last_action, role.last_event, role.last_task?.error_code, role.last_task ? statusLabel(asStatus(role.last_task)) + " · " + date(first(role.last_task.updated_at, role.last_task.created_at)) : "Ingen registrerad åtgärd")));
      if (role.blocker || role.blocked_reason) card.append(el("p", "inline-feedback", first(role.blocker, role.blocked_reason)));
      card.append(resultDetails("Rollens granskade konfiguration och tillåtna verktyg", role.config || { enabled: role.enabled, priority: role.priority, per_call_limit_minor: role.per_call_limit_minor, daily_budget_minor: role.daily_budget_minor, max_calls_per_day: role.max_calls_per_day, tools: role.tools, allowlisted_tools: role.allowlisted_tools }));
      const settings = el("details", "admin-settings"); settings.open = expandedRoles.has(roleId); settings.append(el("summary", "", "Konfigurera intern roll")); const form = el("form", "config-form agent-config-form");
      form.dataset.role = roleId;
      for (const [name, label, minimum, maximum, fallback] of [["priority", "Prioritet · lägre tal först", 0, 100, 50], ["per_call_limit_minor", "Kostnadstak per anrop, öre", 1, 50000, 200], ["daily_budget_minor", "Dygnsbudget, öre", 0, 50000, 10000], ["max_calls_per_day", "Högst AI-anrop per dygn", 1, 100, 30]]) { const caption = el("label", "", label); const input = el("input"); input.name = name; input.type = "number"; input.min = minimum; input.max = maximum; input.required = true; input.value = first(role.config?.[name], role[name], fallback); input.disabled = !isAdmin(); form.append(caption, input); }
      const reasonLabel = el("label", "", "Granskningsanteckning"); const reason = el("input"); reason.name = "reason"; reason.minLength = 12; reason.maxLength = 500; reason.required = true;
      form.append(el("p", "footnote", "Tillåtna interna verktyg · endast serverns rollspecifika lista"));
      for (const tool of list(role.allowlisted_tools)) { const caption = el("label", "checkbox-label"); const input = el("input"); input.type = "checkbox"; input.name = "tools"; input.value = tool; input.checked = list(role.config?.tools || role.tools).includes(tool); input.disabled = !isAdmin(); caption.append(input, document.createTextNode(tool)); form.append(caption); }
      const enabledLabel = el("label", "checkbox-label"); const enabled = el("input"); enabled.type = "checkbox"; enabled.name = "enabled"; enabled.checked = first(role.config?.enabled, role.enabled, true); enabledLabel.append(enabled, document.createTextNode("Tillåt interna uppgifter för rollen"));
      const button = el("button", "button button-secondary", "Spara rollinställning"); button.type = "submit"; for (const node of [reason, enabled, button]) node.disabled = !isAdmin();
      form.append(enabledLabel, reasonLabel, reason, button); form.addEventListener("input", () => { form.dataset.dirty = "true"; }); settings.append(form); card.append(settings); target.append(card);
    }
    }
    const missionBox = byId("missions-list"); missionBox.replaceChildren(); text("missions-count", missions.length + " uppdrag");
    if (!missions.length) missionBox.append(el("p", "empty-state", "Inga uppdrag ännu. Skapa ett avgränsat internt uppdrag i Business Engine."));
    for (const mission of missions) {
      const row = el("div", "mission-item"); const header = el("div", "task-heading"); const status = asStatus(mission);
      header.append(el("strong", "", first(mission.goal, mission.id)), badge(status)); row.append(header, el("p", "muted small", mission.id + " · " + date(mission.created_at)));
      if (mission.blocker || mission.error_code || mission.reason) row.append(el("p", "inline-feedback", first(mission.blocker, mission.error_code, mission.reason)));
      if (mission.result || mission.output || mission.summary) row.append(resultDetails("Uppdragsresultat", first(mission.result, mission.output, mission.summary)));
      const actions = el("div", "button-row");
      const action = ["PAUSED", "BLOCKED", "FAILED"].includes(status) ? "resume" : ["QUEUED", "RUNNING", "ACTIVE"].includes(status) ? "pause" : null;
      if (action) actions.append(actionButton(action === "resume" ? "Granska och återuppta uppdrag" : "Pausa hela uppdraget", async (button) => { const reason = window.prompt(action === "resume" ? "Beskriv granskningen av källor, kostnader och blockeringsorsak. Osäkra leverantörsanrop kräver separat avstämning." : "Ange varför uppdraget och dess väntande agentjobb ska pausas.", ""); if (!reason || reason.trim().length < 12) return; await mutate(button, "/api/missions/" + encodeURIComponent(mission.id) + "/" + action, { reason: reason.trim() }, action === "resume" ? "Återupptagningsbeslutet är registrerat. Servern kontrollerar alla kvarvarande spärrar." : "Hela uppdraget är pausat."); }, { disabled: !isAdmin() || (action === "resume" && isKilled()) }));
      if (status === "UNCERTAIN") row.append(el("p", "inline-feedback", "Osäkert leverantörsutfall: avstämning krävs före återupptagning. Inga AI-anrop upprepas automatiskt."));
      row.append(actions); missionBox.append(row);
    }
    const taskBox = byId("agent-tasks-list"); taskBox.replaceChildren(); text("agent-tasks-count", tasks.length + " registrerade uppgifter");
    if (!tasks.length) taskBox.append(el("p", "empty-state", "Inga agentuppgifter har körts."));
    for (const task of tasks.slice(0, 60)) { const row = el("div", "mission-item"); const header = el("div", "task-heading"); header.append(el("strong", "", (agentNames[task.role] || task.role || "Agent") + " · " + first(task.kind, task.title, task.id)), badge(asStatus(task))); row.append(header, el("p", "muted small", "Uppdrag " + first(task.mission_id, "—") + " · " + date(first(task.updated_at, task.created_at)))); if (task.blocker || task.error_code || task.reason) row.append(el("p", "inline-feedback", first(task.blocker, task.error_code, task.reason))); if (task.output || task.result) row.append(resultDetails("Verifierbart uppgiftsresultat", first(task.output, task.result))); taskBox.append(row); }
    const proposals = byId("agent-proposals"); proposals.replaceChildren();
    for (const proposal of list(data.proposals)) {
      const row = el("div", "mission-item"); row.append(el("strong", "", first(proposal.title, proposal.kind, proposal.id)), badge(asStatus(proposal)), resultDetails("Förslagets underlag", proposal));
      if (["PENDING", "DRAFT", "REVIEW_REQUIRED", "PROPOSED"].includes(asStatus(proposal))) { const actions = el("div", "button-row"); for (const [label, decision] of [["Granska internt förslag", "approve"], ["Avvisa förslag", "reject"]]) actions.append(actionButton(label, async (button) => { const reason = window.prompt("Beskriv granskningen. Detta får inte ge tillstånd till kundkontakt eller betalningar.", ""); if (!reason || reason.trim().length < 12) return; await mutate(button, "/api/agents/actions/" + encodeURIComponent(proposal.id) + "/review", { decision, reason: reason.trim() }, "Granskningen av det interna förslaget är registrerad."); }, { disabled: !isAdmin() })); row.append(actions); }
      proposals.append(row);
    }
    if (!list(data.proposals).length) proposals.append(el("p", "empty-state", "Inga föreslagna åtgärder väntar."));
  }
  function applyPermissions() {
    const disable = (id, blocked) => { const button = byId(id); button.disabled = blocked || button.getAttribute("aria-busy") === "true"; };
    disable("demo-button", !canOperate() || isKilled() || !isDemo());
    disable("run-job-button", !canOperate() || isKilled());
    disable("add-lead-button", !canOperate());
    disable("kill-switch-button", !isAdmin());
    for (const fieldset of document.querySelectorAll("[data-admin-only]")) fieldset.disabled = !isAdmin() || !dataVersionReady();
    disable("import-lead-save", !isAdmin() || !dataVersionReady());
    for (const fieldset of document.querySelectorAll("[data-operator-only]")) fieldset.disabled = !canOperate() || !state.v12 || isKilled();
    disable("ai-test-button", !isAdmin() || !state.v12 || isKilled());
    disable("ai-activate", !isAdmin() || !state.v12 || isKilled() || !aiStatus().config?.revision);
    disable("ai-deactivate", !isAdmin() || !state.v12 || !aiStatus().config?.revision);
    disable("ai-model-refresh", !isAdmin() || !state.v12 || isKilled());
  }
  function render(data) {
    state.data = data;
    state.v12 = data.v12 || state.v12;
    renderMetrics(data); renderBudget(data); renderLeads(data); renderApprovals(data); renderPayments(data); renderJobs(data); renderSecurity(data); renderPreparation(data); renderEngine(); applyPermissions();
    text("last-updated", "Uppdaterad " + new Intl.DateTimeFormat("sv-SE", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date()));
  }
  async function refresh() {
    if (!state.user || state.refreshing) return;
    state.refreshing = true;
    byId("refresh-button").setAttribute("aria-busy", "true");
    try {
      const data = await api("/api/dashboard");
      if (!data.v12) {
        try { state.v12 = await api("/api/v1.2/overview"); }
        catch (error) { if (!state.user) throw error; state.v12 = null; }
      }
      if (state.crmQuery) state.crm = await api("/api/crm/leads?" + state.crmQuery);
      if (state.user) render(data);
    }
    catch (error) { if (state.user) showError(error.message); }
    finally { state.refreshing = false; byId("refresh-button").removeAttribute("aria-busy"); }
  }

  byId("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = byId("login-submit");
    byId("login-error").hidden = true;
    setBusy(button, true, "Loggar in…");
    try {
      const result = await api("/api/auth/login", { method: "POST", body: { email: byId("login-email").value.trim(), password: byId("login-password").value } });
      establishSession(result);
      byId("login-password").value = "";
      await refresh();
      byId("main").focus({ preventScroll: true });
    } catch (error) { text("login-error", error.message); byId("login-error").hidden = false; }
    finally { setBusy(button, false); }
  });
  byId("logout-button").addEventListener("click", async () => {
    try { await api("/api/auth/logout", { method: "POST", body: {} }); showLogin(); byId("login-email").focus(); }
    catch (error) { showError(error.message); }
  });
  byId("refresh-button").addEventListener("click", refresh);
  byId("dismiss-error").addEventListener("click", () => { byId("global-error").hidden = true; });
  byId("demo-button").addEventListener("click", async () => { await mutate(byId("demo-button"), "/api/demo/run", { idempotency_key: key("demo") }, "Demoresan är registrerad. Den stannar för ditt betalningsgodkännande."); });
  byId("run-job-button").addEventListener("click", async () => {
    const result = await mutate(byId("run-job-button"), "/api/jobs/run", {}, "Jobbkön har kontrollerats.");
    if (result) text("job-feedback", result.job === null ? "Inget körbart jobb finns just nu. Kön kan vara tom, vänta på schemat eller ha blockerade uppgifter." : "Servern behandlade " + first(result.job?.kind, result.kind, "ett jobb") + " · " + statusLabel(asStatus(result.job || result)) + ". Se uppdrag och körhistorik.");
  });
  byId("kill-switch-button").addEventListener("click", async () => {
    const enabled = !isKilled();
    const question = enabled ? "Aktivera globalt nödstopp? Nya kostnadsdrivande och externa åtgärder stoppas. Befintliga data bevaras." : "Häva nödstoppet? Pausade jobb återupptas inte automatiskt och live-läge aktiveras inte.";
    if (!window.confirm(question)) return;
    await mutate(byId("kill-switch-button"), "/api/kill-switch", { enabled, reason: enabled ? "Administratören aktiverade nödstopp i kontrollpanelen" : "Administratören hävde nödstopp efter granskning" }, enabled ? "Nödstoppet är aktiverat." : "Nödstoppet är hävt. Pausade jobb kräver separata beslut.");
  });
  byId("lead-search").addEventListener("input", () => { if (state.data) renderLeads(state.data); });
  byId("tasks-filter").addEventListener("change", () => { if (state.data) renderJobs(state.data); });
  byId("add-lead-button").addEventListener("click", () => { byId("new-lead-error").hidden = true; byId("new-lead-dialog").showModal(); byId("new-lead-company").focus(); });
  byId("new-lead-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = byId("new-lead-submit");
    setBusy(button, true);
    byId("new-lead-error").hidden = true;
    try {
      await api("/api/leads", { method: "POST", body: { company: byId("new-lead-company").value.trim(), email: byId("new-lead-email").value.trim(), source: "manual", is_test: true, idempotency_key: key("lead") } });
      byId("new-lead-dialog").close();
      byId("new-lead-form").reset();
      toast("Testleadet är sparat. Ingen kontakt har tagits.");
      await refresh();
    } catch (error) { text("new-lead-error", error.message); byId("new-lead-error").hidden = false; }
    finally { setBusy(button, false); }
  });
  for (const form of document.querySelectorAll(".config-form")) form.addEventListener("input", () => { form.dataset.dirty = "true"; });
  async function saveConfig(formId, buttonId, path, payload, message) {
    const button = byId(buttonId);
    setBusy(button, true);
    try {
      const result = await api(path, { method: "POST", body: payload });
      delete byId(formId).dataset.dirty;
      toast(message);
      await refresh();
      return result;
    } catch (error) { showError(error.message); toast(error.message, true); return false; }
    finally { setBusy(button, false); if (state.data) applyPermissions(); }
  }
  byId("ai-config-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!isAdmin() || !dataVersionReady()) return;
    const existing = aiStatus().config || {};
    const model = byId("ai-model").value.trim();
    await saveConfig("ai-config-form", "ai-config-save", "/api/ai/config", {
      model, approved_models: [model], enabled: existing.enabled === true,
      max_output_tokens: Number(byId("ai-max-tokens").value),
      max_input_chars: first(existing.max_input_chars, 12000),
      timeout_seconds: Number(byId("ai-timeout").value), max_retries: Number(byId("ai-retries").value),
      reservation_usd_minor: first(existing.reservation_usd_minor, 10),
      per_call_limit_sek_minor: Math.round(Number(byId("ai-call-sek").value) * 100),
      prompt_usd_per_million: String(first(existing.prompt_usd_per_million, "1.00")),
      completion_usd_per_million: String(first(existing.completion_usd_per_million, "5.00")),
      reason: byId("ai-reason").value.trim()
    }, "Modellinställningar sparade. Serverns aktuella AI-spärrar och eventuella krav på förnyat godkännande visas ovan.");
  });
  byId("ai-activation-form").addEventListener("submit", async (event) => {
    event.preventDefault(); if (!isAdmin()) return;
    if (!window.confirm("Godkänn betalda interna AI-anrop för den aktuella modellrevisionen inom budgetmandatet? Detta kan medföra verklig AI-kostnad. Kundkontakt och betalningar förblir spärrade.")) return;
    const result = await saveConfig("ai-activation-form", "ai-activate", "/api/ai/activation", { statement: byId("ai-activation-statement").value.trim(), config_revision: aiStatus().config?.revision, reason: byId("ai-activation-reason").value.trim() }, "Det separata AI-beslutet är registrerat. Alla övriga serverkontroller gäller fortfarande.");
    if (result) byId("ai-activation-form").reset();
  });
  byId("ai-deactivate").addEventListener("click", async () => {
    const reason = window.prompt("Ange varför betalda interna AI-anrop ska spärras.", "Administratören spärrade betalda interna AI-anrop");
    if (!reason || reason.trim().length < 12) return;
    await mutate(byId("ai-deactivate"), "/api/ai/activation", { statement: "DISABLE PAID AI CALLS", config_revision: aiStatus().config?.revision, reason: reason.trim() }, "Betalda interna AI-anrop är spärrade.");
  });
  byId("ai-model-refresh").addEventListener("click", async () => { await mutate(byId("ai-model-refresh"), "/api/ai/models/refresh", {}, "Modellmetadata har kontrollerats. Granska kompatibilitet och prisgränser."); });
  byId("fx-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const observed = new Date(byId("fx-observed").value).getTime();
    if (!Number.isFinite(observed)) { toast("Ange en giltig observationstid.", true); return; }
    await saveConfig("fx-form", "fx-submit", "/api/budget/fx", { currency: "USD", sek_per_unit: byId("fx-rate").value, source: byId("fx-source").value.trim(), observed_at: Math.floor(observed / 1000) }, "Den granskade USD/SEK-kursen är registrerad.");
  });
  byId("ai-test-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const button = byId("ai-test-button"), output = byId("ai-test-result");
    setBusy(button, true, "Väntar på verkligt serverresultat…");
    output.replaceChildren(el("p", "muted small", "Anropet behandlas av servern. Endast ett verifierat leverantörsresultat kan markeras som lyckat."));
    try { const result = await api("/api/ai/test", { method: "POST", body: { idempotency_key: key("ai-diagnostic"), text: byId("ai-test-text").value.trim() } }); output.replaceChildren(resultDetails("Faktiskt AI-resultat · status, svar, token, kostnad och spårning", result, true)); await refresh(); }
    catch (error) { output.replaceChildren(el("p", "form-error", error.message)); await refresh(); }
    finally { setBusy(button, false); applyPermissions(); }
  });
  byId("mission-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const result = await mutate(byId("mission-submit"), "/api/missions", { idempotency_key: key("mission"), goal: byId("mission-goal").value.trim(), source_ids: ids(byId("mission-sources").value), lead_ids: ids(byId("mission-leads").value), deliverable_kind: byId("mission-type").value, max_cost_minor: Math.round(Number(byId("mission-cost").value) * 100), priority: byId("mission-priority").value }, "Det interna uppdraget är registrerat. Agenternas steg och eventuella blockeringar visas i kontrollcentret.");
    text("mission-feedback", result ? "Registrerat uppdrag: " + first(result.id, result.mission?.id, "se Agent Control Center") + " · " + statusLabel(asStatus(result.mission || result)) : "Uppdraget kunde inte skapas. Se serverns blockeringsorsak ovan. Ingen pågående aktivitet antas.");
  });
  async function loadCRM() {
    const params = new URLSearchParams({ q: byId("crm-query").value.trim(), industry: byId("crm-industry").value.trim(), geography: byId("crm-geography").value.trim(), mode: byId("crm-mode").value, sort: byId("crm-sort").value });
    state.crmQuery = params.toString(); state.crm = await api("/api/crm/leads?" + state.crmQuery); renderCRM(state.crm);
  }
  byId("crm-filter-form").addEventListener("submit", async (event) => { event.preventDefault(); setBusy(byId("crm-filter-submit"), true); try { await loadCRM(); } catch (error) { showError(error.message); } finally { setBusy(byId("crm-filter-submit"), false); } });
  byId("factory-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const result = await mutate(byId("factory-submit"), "/api/factory/orders", { idempotency_key: key("factory-order"), type: byId("factory-type").value, brief: byId("factory-brief").value.trim(), source_ids: ids(byId("factory-sources").value), lead_ids: ids(byId("factory-leads").value) }, "Den interna beställningen är skapad. Generering kräver ett separat köbeslut.");
    text("factory-feedback", result ? "Beställning " + first(result.id, result.order?.id, "registrerad") + " skapad. Ingen generering eller kundleverans har startats automatiskt." : "Beställningen blockerades. Se serverns felmeddelande ovan.");
  });
  byId("internal-schedule-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const result = await saveConfig("internal-schedule-form", "schedule-submit", "/api/internal/schedules/" + encodeURIComponent(byId("schedule-id").value.trim()), { enabled: byId("schedule-enabled").checked, kind: byId("schedule-kind").value, cadence: byId("schedule-cadence").value, daily_at: byId("schedule-time").value, interval_minutes: Number(byId("schedule-interval").value), max_runs_per_day: Number(byId("schedule-runs").value), max_cost_minor: Math.round(Number(byId("schedule-cost").value) * 100), timezone: "Europe/Stockholm", goal: byId("schedule-goal").value.trim(), source_ids: ids(byId("schedule-sources").value), order_id: byId("schedule-order").value.trim(), lead_id: byId("schedule-lead").value.trim(), reason: byId("schedule-reason").value.trim() }, "Det interna schemat är granskat och sparat.");
    text("schedule-feedback", result ? "Schema " + result.id + " · " + (result.enabled ? "aktivt internt schema" : "avstängt") + " · nästa planerade tid " + date(result.next_run_at) : "Schemat blockerades. Se serverns felmeddelande.");
  });
  byId("agent-role-cards").addEventListener("submit", async (event) => {
    const form = event.target.closest(".agent-config-form"); if (!form) return;
    event.preventDefault(); const fields = new FormData(form), payload = { enabled: fields.get("enabled") === "on", reason: fields.get("reason"), tools: fields.getAll("tools") };
    for (const name of ["priority", "per_call_limit_minor", "daily_budget_minor", "max_calls_per_day"]) payload[name] = Number(fields.get(name));
    const result = await mutate(form.querySelector("button"), "/api/agents/" + encodeURIComponent(form.dataset.role) + "/config", payload, "Rollens interna begränsningar är sparade. Inga externa befogenheter har lagts till.");
    if (result) { delete form.dataset.dirty; document.activeElement.blur(); renderAgents(state.v12?.agents || {}); }
  });
  byId("agents-refresh").addEventListener("click", refresh);
  function selectPage() {
    const agentsPage = location.pathname.replace(/\/$/, "") === "/agents";
    if (agentsPage) for (const brand of document.querySelectorAll("a.brand")) brand.href = "/#overview";
    for (const section of document.querySelectorAll(".page-section")) section.hidden = agentsPage ? section.id !== "agent-center" : section.id === "agent-center";
    for (const link of document.querySelectorAll(".navigation .nav-link")) {
      const href = link.getAttribute("href");
      if (agentsPage && href.startsWith("#")) link.href = "/" + href;
      link.classList.toggle("active", agentsPage ? link.id === "agents-navigation" : href === (location.hash || "#overview"));
      if (link.classList.contains("active")) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
    }
    const heading = document.querySelector(".breadcrumb strong"); if (heading) heading.textContent = agentsPage ? "Agent Control Center" : "Kontrollpanel";
  }
  selectPage();
  byId("pipeline-config-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!isAdmin() || !dataVersionReady()) return;
    await saveConfig("pipeline-config-form", "pipeline-config-save", "/api/pipeline/config", {
      enabled: byId("pipeline-enabled").checked,
      source_ids: byId("pipeline-sources").value.split(",").map((value) => value.trim()).filter(Boolean),
      interval_seconds: Number(byId("pipeline-interval").value) * 60,
      max_leads_per_tick: Number(byId("pipeline-max-leads").value),
      follow_up_delay_seconds: Number(byId("pipeline-followup").value) * 86400,
      billing_stage: first(state.data.v11.pipeline?.billing_stage, "after_delivery"),
      reason: byId("pipeline-reason").value.trim()
    }, "Förberedelseschemat är sparat. Inget livegodkännande har lämnats.");
  });
  byId("source-config-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!isAdmin() || !dataVersionReady() || !byId("source-reviewed").checked) return;
    const sourceId = byId("source-id").value.trim();
    const saved = await saveConfig("source-config-form", "source-config-save", "/api/research/sources/" + encodeURIComponent(sourceId), {
      name: byId("source-name").value.trim(), kind: "upload", url: byId("source-url").value.trim(),
      approved: true, license_reviewed: true, legal_review_reference: byId("source-reference").value.trim(),
      fetch_enabled: false, minimum_quality: 80, minimum_interval_seconds: 3600,
      cost_minor: 0, cost_evidence: byId("source-cost-evidence").value.trim(), data_mode: "REAL"
    }, "Källgranskningen är registrerad för manuell katalogimport. Ingen nätverkshämtning har aktiverats.");
    if (saved) byId("source-config-form").reset();
  });
  byId("import-lead-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!isAdmin() || !dataVersionReady()) return;
    byId("import-lead-error").hidden = true;
    const sourceId = byId("import-source-id").value;
    const saved = await saveConfig("import-lead-form", "import-lead-save", "/api/research/sources/" + encodeURIComponent(sourceId) + "/ingest", {
      idempotency_key: key("source-import"), records: [{ company: byId("import-company").value.trim(), email: byId("import-email").value.trim(),
        domain: byId("import-domain").value.trim(), source_url: byId("import-reference").value.trim(), evidence: byId("import-evidence").value.trim(),
        entity_type: byId("import-entity").value, address_type: byId("import-address").value }]
    }, "Källunderlaget har granskats av servern. Se importresultat och revisionslogg.");
    if (saved?.accepted === 0 && list(saved.rejected).length) {
      const reasons = { quality_below_threshold: "Källunderlaget uppfyller inte kvalitetskravet.", demo_real_identity_collision: "Samma identitet finns redan i en annan datatyp. Demodata får inte omvandlas till verkliga leads." };
      text("import-lead-error", list(saved.rejected).map((entry) => reasons[entry.reason] || "Importen kräver granskning: " + entry.reason).join(" "));
      byId("import-lead-error").hidden = false;
      toast("Ingen ny lead importerades. Granska källunderlaget.", true);
    } else if (saved) {
      byId("import-lead-dialog").close();
      toast("Importerade uppgifter: " + first(saved.accepted, 1) + ". Dubbletter: " + first(saved.duplicates, 0) + ". Ingen kundkontakt har tagits.");
    }
  });
  for (const link of document.querySelectorAll(".nav-link")) link.addEventListener("click", () => {
    for (const other of document.querySelectorAll(".nav-link")) { other.classList.remove("active"); other.removeAttribute("aria-current"); }
    link.classList.add("active"); link.setAttribute("aria-current", "location");
  });
  for (const dialog of document.querySelectorAll("dialog")) dialog.addEventListener("click", (event) => { if (event.target === dialog) { const bounds = dialog.getBoundingClientRect(); if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close(); } });
  setInterval(() => { if (!document.hidden && state.user) refresh(); }, 10000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden && state.user) refresh(); });
  (async () => { try { establishSession(await api("/api/auth/me")); await refresh(); } catch { showLogin(); } })();
})();
