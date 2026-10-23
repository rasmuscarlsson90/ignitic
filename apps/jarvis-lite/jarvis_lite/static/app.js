"use strict";

(() => {
  const byId = (id) => document.getElementById(id);
  const state = { user: null, csrf: null, data: null, refreshing: false, toastTimer: null };
  const money = new Intl.NumberFormat("sv-SE", { style: "currency", currency: "SEK", maximumFractionDigits: 0 });
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
    CONFIRMED: "Betalning bekräftad", SENT: "Skickad", DELIVERED: "Levererad", PENDING: "Väntar", DONE: "Klar"
  };
  const jobNames = { demo_journey: "Simulerad kundresa", research: "Leadanalys", qualify: "Kvalificering", follow_up: "Uppföljning", daily_report: "Daglig rapport" };
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
    if (status === 403) return "Servern tillåter inte den här åtgärden för ditt konto.";
    if (Array.isArray(detail)) return "Kontrollera de angivna uppgifterna. Servern kunde inte godkänna formuläret.";
    const message = first(payload?.message, detail?.message, typeof detail === "string" ? detail : null);
    return typeof message === "string" ? message.slice(0, 300) : "Åtgärden kunde inte genomföras. Försök igen eller kontrollera revisionsloggen.";
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
    state.user = null; state.csrf = null; state.data = null;
    byId("app-shell").hidden = true;
    byId("login-screen").hidden = false;
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

  function renderMetrics(data) {
    const m = data.metrics || {};
    const leads = list(data.leads);
    const payments = list(data.payments);
    const countState = (...states) => leads.filter((lead) => states.includes(asStatus(lead))).length;
    const paying = first(m.paying_customers, payments.filter((payment) => asStatus(payment) === "CONFIRMED").length);
    text("metric-leads", integer.format(first(m.leads, leads.length)));
    text("nav-lead-count", integer.format(first(m.leads, leads.length)));
    text("metric-qualified", integer.format(first(m.qualified, countState("QUALIFIED"))) + " kvalificerade leads");
    text("metric-proposals", integer.format(first(m.proposals_sent, m.proposals, countState("PROPOSAL_SENT"))));
    text("metric-accepted", integer.format(first(m.accepted, countState("ACCEPTED"))) + " accepterade offerter");
    text("metric-paying", integer.format(paying));
    text("metric-revenue", amount(first(m.confirmed_revenue_minor, m.revenue_minor, 0)));
    text("funnel-contacts", integer.format(first(m.contacts, m.contacted, countState("CONTACTED"))));
    text("funnel-replies", integer.format(first(m.replies, countState("REPLIED"))));
    text("funnel-proposals", integer.format(first(m.proposal_requests, m.proposals, countState("PROPOSAL_READY"))));
    text("funnel-accepted", integer.format(first(m.accepted, countState("ACCEPTED"))));
    text("funnel-paying", integer.format(paying));
    text("finance-outstanding", amount(first(m.outstanding_minor, m.outstanding_payments_minor, 0)));
    const margin = first(m.gross_margin_minor, m.estimated_margin_minor);
    text("finance-margin", margin !== undefined ? amount(margin) : "Ingen prognos ännu");
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
    text("budget-tools", amount(first(budget.usage?.tools_30d, budget.tools_30d_minor, 0)));
    text("budget-infra", amount(first(budget.usage?.infrastructure_monthly, budget.infrastructure_minor, 0)));
    byId("budget-ai").title = "Faktisk registrerad AI-kostnad. Pågående reserveringar ingår i totalbudgetens spärr.";
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
      row.append(cell(identity), cell(badge(asStatus(lead))), cell(source), cell(badge(lead.is_test ? "DEMO" : "PENDING", lead.is_test ? "Testdata" : "Kunddata")), cell(actions));
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
      ["Kontakt", lead.email], ["Status", statusLabel(asStatus(lead))], ["Datatyp", lead.is_test ? "Tydligt märkta testdata" : "Kunddata"],
      ["Källa", typeof lead.source === "object" ? first(lead.source.name, lead.source.type) : first(lead.source_id, lead.source)],
      ["Insamlat", date(first(lead.collected_at, lead.discovered_at, lead.created_at))], ["Källreferens", first(lead.source_url, lead.source_reference)],
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
      detail.append(el("strong", "", "Godkänn betalningsbegäran"), el("p", "", first(payment.reference, payment.customer_name, payment.id.slice(0, 8)) + " · " + amount(payment.total_minor ?? payment.amount_minor) + " inkl. moms"));
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
      title.append(el("span", "cell-title", first(payment.reference, payment.id.slice(0, 8))), el("span", "cell-subtitle", payment.is_test || isDemo() ? "Simulerad betalning · testdata" : first(payment.customer_name, "Kundbegäran")));
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
      const actions = el("div", "table-action-group");
      const status = asStatus(job);
      if (["PAUSED", "FAILED", "UNCERTAIN"].includes(status)) {
        actions.append(actionButton("Granska / återuppta", async (button) => {
          if (!window.confirm("Granska jobbets senaste fel och behörigheter innan det återupptas. Fortsätt med ett uttryckligt återupptagningsbeslut?")) return;
          await mutate(button, "/api/jobs/" + encodeURIComponent(job.id) + "/resume", {}, "Jobbet har fått ett uttryckligt återupptagningsbeslut.");
        }, { disabled: !isAdmin() || isKilled() }));
      } else if (["QUEUED", "LEASED", "RUNNING"].includes(status)) {
        actions.append(actionButton("Pausa", async (button) => {
          await mutate(button, "/api/jobs/" + encodeURIComponent(job.id) + "/pause", {}, "Jobbet är pausat.");
        }, { disabled: !isAdmin() }));
      } else actions.append(el("span", "muted small", first(job.error_code, job.reason, "—")));
      row.append(cell(title), cell(badge(status)), cell(number(job.attempts) + " / " + first(job.max_attempts, 3)), cell(actions));
      body.append(row);
    }
    text("jobs-count", jobs.length + " registrerade jobb");
    byId("jobs-empty").hidden = jobs.length > 0;
    const tasks = list(data.tasks).filter((task) => !["DONE", "COMPLETED"].includes(asStatus(task)));
    const taskBox = byId("tasks-list");
    taskBox.replaceChildren();
    text("tasks-count", tasks.length);
    if (!tasks.length) taskBox.append(el("p", "empty-state", "Inga manuella uppgifter väntar."));
    for (const task of tasks.slice(0, 6)) {
      const item = el("div", "task-item");
      item.append(el("strong", "", first(task.title, task.kind, "Manuell uppgift")), el("p", "", first(task.description, task.reason, task.message, "Granska revisionsloggen för nästa steg.")));
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
    const connectionRows = [["Databas", first(connections.database, "Beständig databas")], ["Jobbkö", first(connections.queue, "Databasbaserad")], ["AI-leverantör", demo ? "Simulerad i demo" : first(connections.llm, connections.openrouter, connections.OPENROUTER_API_KEY, "Status saknas")], ["E-post", demo ? "Simulerad i demo" : first(connections.email, connections.BREVO_API_KEY, "Status saknas")], ["Betalningar", demo ? "Simulerade i demo" : first(connections.payments, connections.stripe, connections.STRIPE_SECRET_KEY, "Status saknas")], ["Din behörighet", roleNames[state.user?.role] || state.user?.role || "—"]];
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
  function applyPermissions() {
    byId("demo-button").disabled = !canOperate() || isKilled() || !isDemo();
    byId("run-job-button").disabled = !canOperate() || isKilled();
    byId("add-lead-button").disabled = !canOperate();
    byId("kill-switch-button").disabled = !isAdmin();
  }
  function render(data) {
    state.data = data;
    renderMetrics(data); renderBudget(data); renderLeads(data); renderApprovals(data); renderPayments(data); renderJobs(data); renderSecurity(data); applyPermissions();
    text("last-updated", "Uppdaterad " + new Intl.DateTimeFormat("sv-SE", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date()));
  }
  async function refresh() {
    if (!state.user || state.refreshing) return;
    state.refreshing = true;
    byId("refresh-button").setAttribute("aria-busy", "true");
    try { render(await api("/api/dashboard")); }
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
  byId("run-job-button").addEventListener("click", async () => { await mutate(byId("run-job-button"), "/api/jobs/run", {}, "Jobbkön har behandlats. Se aktuell status nedan."); });
  byId("kill-switch-button").addEventListener("click", async () => {
    const enabled = !isKilled();
    const question = enabled ? "Aktivera globalt nödstopp? Nya kostnadsdrivande och externa åtgärder stoppas. Befintliga data bevaras." : "Häva nödstoppet? Pausade jobb återupptas inte automatiskt och live-läge aktiveras inte.";
    if (!window.confirm(question)) return;
    await mutate(byId("kill-switch-button"), "/api/kill-switch", { enabled, reason: enabled ? "Administratören aktiverade nödstopp i kontrollpanelen" : "Administratören hävde nödstopp efter granskning" }, enabled ? "Nödstoppet är aktiverat." : "Nödstoppet är hävt. Pausade jobb kräver separata beslut.");
  });
  byId("lead-search").addEventListener("input", () => { if (state.data) renderLeads(state.data); });
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
  for (const link of document.querySelectorAll(".nav-link")) link.addEventListener("click", () => {
    for (const other of document.querySelectorAll(".nav-link")) { other.classList.remove("active"); other.removeAttribute("aria-current"); }
    link.classList.add("active"); link.setAttribute("aria-current", "location");
  });
  for (const dialog of document.querySelectorAll("dialog")) dialog.addEventListener("click", (event) => { if (event.target === dialog) { const bounds = dialog.getBoundingClientRect(); if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close(); } });
  setInterval(() => { if (!document.hidden && state.user) refresh(); }, 10000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden && state.user) refresh(); });
  (async () => { try { establishSession(await api("/api/auth/me")); await refresh(); } catch { showLogin(); } })();
})();
