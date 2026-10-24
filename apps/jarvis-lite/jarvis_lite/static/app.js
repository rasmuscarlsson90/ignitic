"use strict";

(() => {
  const byId = (id) => document.getElementById(id);
  const state = { user: null, csrf: null, data: null, v12: null, crm: null, crmQuery: null, refreshing: false, toastTimer: null, detailCache: {}, detailLoading: null, detailGeneration: 0, missionRetryRequests: {} };
  const money = new Intl.NumberFormat("sv-SE", { style: "currency", currency: "SEK", minimumFractionDigits: 0, maximumFractionDigits: 2 });
  const integer = new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 0 });
  const dateFormat = new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Stockholm", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
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
    IDLE: "Ledig", WORKING: "Arbetar", ERROR: "Fel / granskas", BUSY: "Arbetar", WAITING: "Väntar", READY: "Redo", UNDISPATCHED: "Ej skickat", TEST_ARTIFACT: "Äldre testartefakt",
    APPROVED_INTERNAL: "Internt granskad · oskickad", NEEDS_REVIEW: "Behöver granskas", QUALITY_REVIEW: "Kvalitetsgranskning",
    AUTO_APPROVED: "Automatiskt godkänd", ACCEPTED_SOURCE: "Godkänd av webbpolicy", VERIFIED: "Samstämmiga belägg", ATTRIBUTED: "Källbelagd uppgift", UNVERIFIED: "Inte verifierad", HYPOTHESIS: "Hypotes", CONFLICT: "Motstridiga belägg", INSUFFICIENT_EVIDENCE: "Kunskapsluckor", EVIDENCE_GAP: "Belägg saknas"
  };
  const jobNames = { demo_journey: "Simulerad kundresa", research: "Leadanalys", qualify: "Kvalificering", follow_up: "Uppföljning", daily_report: "Daglig rapport", pipeline_advance: "Förbered nästa affärssteg", source_research: "Granska tillåten researchkälla" };
  const errorNames = {
    ai_runtime_disabled: "Servern tillåter inte betalda AI-anrop. Ett administratörsbeslut kan inte ändra serverns säkerhetsgräns.",
    ai_admin_disabled: "Betalda interna AI-anrop är spärrade av en administratör. Separat godkännande krävs innan arbetet kan fortsätta.",
    ai_activation_required: "Ett nytt administratörsgodkännande krävs för den aktuella modellrevisionen innan ett återförsök kan kosta pengar.",
    mission_retry_authorization_changed: "Modellinställningar eller behörigheter har ändrats sedan återförsöket granskades. Granska aktuella inställningar och godkännanden igen.",
    retry_source_changed: "Källunderlaget eller dess tillstånd har ändrats. Tidigare källgodkännande kan inte användas för detta återförsök.",
    mission_deadline: "Uppdragets tidsgräns har passerat. Återupptagning får inte förlänga den eller starta nya AI-anrop.",
    mission_retry_limit: "Gränsen för granskade återförsök är nådd. Fler försök får inte skapas inom detta uppdrag.",
    mission_reconciliation_required: "Anropsutfallet eller kostnaden kräver manuell avstämning. Ett nytt återförsök får inte kringgå ett okänt utfall.",
    ai_output_truncated: "AI-svaret nådde svarstaket och är ofullständigt. Ingen delvis JSON godkänns. Granska inställningar, budget och nytt AI-godkännande innan samma steg får återförsökas.",
    ai_json_invalid: "AI-svaret gick inte att läsa som fullständig JSON. Det sparade felet kan granskas utan att säkerhetsgränser ändras.",
    ai_schema_invalid: "AI-svaret saknar uppgifter som krävs för detta delsteg. Högst en budgetkontrollerad formatkorrigering tillåts.",
    ai_policy_violation: "AI-svaret föreslog en otillåten handling. Automatisk formatkorrigering kan inte godkänna den.",
    ai_provider_response_invalid: "Leverantören returnerade ett ogiltigt svar. Kontrollera det spårbara anropsutfallet och kostnaden.",
    admin_required: "Åtgärden kräver administratörsbehörighet.",
    unauthorized: "Din session har löpt ut. Logga in igen.",
    authentication_failed: "E-postadressen eller lösenordet stämmer inte.",
    invalid_credentials: "E-postadressen eller lösenordet stämmer inte.",
    kill_switch: "Nödstoppet blockerar nya åtgärder.",
    kill_switch_active: "Nödstoppet blockerar nya åtgärder.",
    budget_exhausted: "Budgettaket är nått. Nya kostnadsdrivande uppgifter är blockerade.",
    autonomous_research_disabled: "Autonom webbresearch är avstängd. Granska policy, sökbudget och separat godkännande.",
    autonomous_runtime_disabled: "Servern tillåter inte autonom webbresearch. En sparad policy ändrar inte serverns säkerhetsgräns.",
    search_budget_exhausted: "Den separata sökbudgeten räcker inte. Inga nya betalda sökningar tillåts.",
    unsupported_or_unbounded_opportunity: "Affärspåståendet saknar tillräckliga specifika belägg. En officiell källa är inte i sig bevis för efterfrågan eller lönsamhet.",
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
    state.missionRetryRequests = {};
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
    else if (["RESEARCHED", "CONTACTED", "REPLIED", "PROPOSAL_SENT", "REQUESTED", "INITIATED", "QUEUED", "LEASED", "RUNNING", "WORKING"].includes(upper)) color = "blue";
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
      ["Globalt svarstak", integer.format(number(aiConfig.max_output_tokens)) + " token · roller kan ha lägre tak"],
      ["Timeout per leverantörsanrop", number(aiConfig.timeout_seconds) + " sekunder"],
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
    const supportedTimeout = Math.min(20, number(ai.timeout_policy?.max_supported_seconds, 20));
    byId("ai-timeout").max = supportedTimeout;
    text("ai-timeout-policy", first(ai.timeout_policy?.message, ai.timeout_policy?.reason, "Stödd timeout är högst " + supportedTimeout + " sekunder per leverantörsanrop. Upp till 120 sekunder är inte verifierat för worker och hosting. Ett eventuellt separat formatkorrigeringsanrop har sin egen begränsade tidsram."));
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
    const sources = list(v.research_sources).filter((source) => source.approval_mode !== "AUTONOMOUS_POLICY");
    const sourceBox = byId("sources-list");
    sourceBox.replaceChildren();
    text("sources-count", sources.length);
    if (!sources.length) sourceBox.append(el("p", "empty-state", "Inga källor har granskats ännu. Befintliga leads bevaras och klassificeras separat."));
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
      if (source.kind === "https_json" && source.approved) item.append(actionButton("Hämta granskat underlag", async (button) => { if (!window.confirm("Hämta endast denna uttryckligen godkända källa inom dess dokumenterade kostnad? Servern kontrollerar nätverksbehörighet, adress och budget.")) return; await mutate(button, "/api/research/sources/" + encodeURIComponent(source.id) + "/fetch", { idempotency_key: key("source-fetch") }, "Hämtningsresultatet är registrerat. Granska källa och kvalitet innan användning."); }, { disabled: !isAdmin() || isKilled() || !source.fetch_enabled }));
      sourceBox.append(item);
    }
    const outreach = v.outreach || {};
    const checks = outreach.checks || {};
    const contactEnabled = false;
    text("contact-gate-badge", contactEnabled ? "Separat kontaktgodkännande finns" : "Kundkontakt spärrad");
    byId("contact-gate-badge").className = "badge badge-" + (contactEnabled ? "warning" : "neutral");
    text("outreach-state", "Spärrad i v1.4");
    summaryRows("outreach-summary", [
      ["Leverantör", first(outreach.provider, outreach.config?.provider, "Brevo · kräver granskning")],
      ["Domänunderlag", checks.reviewed_domain_evidence === true ? "Operatörsgranskat underlag registrerat" : "Operatörsgranskning krävs"],
      ["Avsändare & juridisk policy", checks.reviewed_sender_and_lawful_policy === true ? "Granskningsreferens registrerad" : "Ej godkänd"],
      ["Testomfattning", outreach.test_scope === "isolated_offline_safety_probe" ? "Isolerad offlinekontroll · inte leveranstest" : "Separat testunderlag krävs"],
      ["Serverns kontaktspärr", "Permanent spärrad i v1.4"],
      ["Betalningsåtgärder", "Permanent spärrade i v1.4 · historik bevaras"]
    ]);
    text("preparation-status", "Godkända källor, verkliga leads och interna AI-utkast kan förberedas. Verkliga kundutskick och betalningar är spärrade i v1.4, även om äldre runtime-inställningar finns.");
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
      detail.append(el("strong", "", payment.provider === "demo" ? "Godkänn simulerad betalningsbegäran" : "Historiskt betalningsbeslut · spärrat i v1.4"), el("p", "", first(payment.reference, payment.customer_name, payment.id.slice(0, 8)) + " · " + amount(payment.total_minor ?? payment.amount_minor) + " inkl. moms"));
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
    if (payment.provider !== "demo") return el("span", "muted small", "Betalningsåtgärder spärrade i v1.4");
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
    const connectionRows = [["Databas", first(connections.database, "Beständig databas")], ["Jobbkö", first(connections.queue, "Databasbaserad")], ["AI-leverantör", aiStatus().effective_paid_enabled ? "OpenRouter · separat godkända betalda interna anrop" : "Betalda interna anrop spärrade · se AI-kontroller"], ["E-post", "Verkliga utskick spärrade i v1.4"], ["Betalningar", "Verkliga betalningsåtgärder spärrade i v1.4"], ["Din behörighet", roleNames[state.user?.role] || state.user?.role || "—"]];
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
    text("engine-status", v ? "Databasregistrerad aktivitet" : "v1.4-API saknas");
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
      header.append(el("h3", "", typeName(first(order.type, order.service_type, order.kind))), badge(asStatus(order))); card.append(header, el("p", "", order.brief), el("p", "muted small", order.id + " · " + date(order.created_at)));
      if (order.blocker || order.blocked_reason || order.error_code) card.append(el("p", "inline-feedback", first(order.blocker, order.blocked_reason, order.error_code)));
      const orderResults = results.filter((result) => result.order_id === order.id).concat(list(order.results));
      const actions = el("div", "button-row");
      if (!["COMPLETED", "CANCELLED", "REJECTED", "UNCERTAIN", "GENERATING"].includes(asStatus(order))) actions.append(actionButton("Köa generering", async (button) => { const result = await mutate(button, "/api/factory/orders/" + encodeURIComponent(order.id) + "/generate", { idempotency_key: key("factory-generate") }, "Genereringen har köats. Resultatet visas efter att det interna jobbet har slutförts."); if (result) text("factory-feedback", "Generering registrerad för " + order.id + ". Se jobbkö och resultat."); }, { disabled: !canOperate() || isKilled() }));
      if (asStatus(order) === "UNCERTAIN") card.append(el("p", "inline-feedback", "Osäkert leverantörsutfall kräver avstämning. Ingen ny generering startas från denna vy."));
      card.append(actions);
      for (const result of orderResults) {
        const block = el("div", "factory-result"); block.append(badge(asStatus(result)), resultDetails("Resultat · innehåll, källor och spårning", first(result.document, result.content, result.output, result), true));
        const resultActions = el("div", "button-row");
        for (const format of ["markdown", "json"]) { const link = el("a", "button button-small button-secondary", "Exportera " + (format === "markdown" ? "Markdown" : "JSON")); link.href = "/api/deliverables/" + encodeURIComponent(result.id) + "/export?format=" + format; link.setAttribute("download", ""); resultActions.append(link); }
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
    if (!panel) { panel = el("article", "panel agent-history"); panel.id = "sales-offers"; byId("research-sales-offers").append(panel); }
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
      const globalTokens = number(aiStatus().config?.max_output_tokens, 512), roleTokens = first(role.config?.max_output_tokens, role.max_output_tokens);
      const tokenLabel = el("label", "", "Högst svarstoken för rollen · tomt ärver globalt tak"), tokenInput = el("input");
      tokenInput.name = "max_output_tokens"; tokenInput.type = "number"; tokenInput.min = 1; tokenInput.max = globalTokens; tokenInput.step = 1; tokenInput.placeholder = "Ärv globalt: " + globalTokens; tokenInput.value = roleTokens === undefined ? "" : roleTokens; tokenInput.disabled = !isAdmin();
      form.append(tokenLabel, tokenInput, el("p", "footnote", "Nu effektivt svarstak: " + Math.min(number(roleTokens, globalTokens), globalTokens) + " token. Ett granskat rolltak får aldrig överstiga globalt tak (" + globalTokens + "). Kostnadsgränser och separat godkännande gäller fortfarande."));
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
    const autonomy = state.v12?.autonomous_research;
    for (const id of ["autonomy-policy-form", "autonomy-activation-form"]) byId(id).querySelector("fieldset").disabled = !isAdmin() || !autonomy;
    disable("autonomy-policy-save", !isAdmin() || !autonomy);
    disable("autonomy-activate", !isAdmin() || !autonomy?.policy?.revision || isKilled());
    disable("autonomy-deactivate", !isAdmin() || !autonomy?.policy?.revision);
  }
  function render(data) {
    state.data = data;
    state.v12 = data.v12 || state.v12;
    renderMetrics(data); renderBudget(data); renderLeads(data); renderApprovals(data); renderPayments(data); renderJobs(data); renderSecurity(data); renderPreparation(data); renderEngine(); renderV13(); applyPermissions();
    text("last-updated", "Uppdaterad " + new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Stockholm", hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date()));
  }
  async function refresh() {
    if (!state.user || state.refreshing) return;
    state.refreshing = true;
    byId("refresh-button").setAttribute("aria-busy", "true");
    try {
      const data = await api("/api/dashboard");
      if (!data.v12) {
        try { state.v12 = await api("/api/v1.5/overview"); }
        catch (error) { if (!state.user) throw error; state.v12 = null; }
      }
      if (state.crmQuery) state.crm = await api("/api/crm/leads?" + state.crmQuery);
      if (state.user) { state.detailCache = {}; state.detailLoading = null; state.detailGeneration++; render(data); }
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
    const result = await mutate(byId("mission-submit"), "/api/missions", { idempotency_key: key("mission"), goal: byId("mission-goal").value.trim(), source_ids: ids(byId("mission-sources").value), lead_ids: ids(byId("mission-leads").value), research_mode: byId("mission-research-mode").value, deliverable_kind: byId("mission-type").value, max_cost_minor: Math.round(Number(byId("mission-cost").value) * 100), priority: byId("mission-priority").value, deadline_seconds: Number(byId("mission-deadline").value) * 60, max_steps: Number(byId("mission-steps").value), max_retries: Number(byId("mission-retries").value), max_revisions: Number(byId("mission-revisions").value) }, "Uppdraget är registrerat. Följ agenternas steg, resultat och kostnader i uppdragsvyn.");
    if (result?.id) navigate("/missions/" + encodeURIComponent(result.id));
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
    const result = await saveConfig("internal-schedule-form", "schedule-submit", "/api/internal/schedules/" + encodeURIComponent(byId("schedule-id").value.trim()), { enabled: byId("schedule-enabled").checked, kind: byId("schedule-kind").value, cadence: byId("schedule-cadence").value, weekday: Number(byId("schedule-weekday").value), max_cost_per_run_minor: Math.round(Number(byId("schedule-run-cost").value) * 100), daily_at: byId("schedule-time").value, interval_minutes: Number(byId("schedule-interval").value), max_runs_per_day: Number(byId("schedule-runs").value), max_cost_minor: Math.round(Number(byId("schedule-cost").value) * 100), timezone: "Europe/Stockholm", goal: byId("schedule-goal").value.trim(), source_ids: ids(byId("schedule-sources").value), order_id: byId("schedule-order").value.trim(), lead_id: byId("schedule-lead").value.trim(), reason: byId("schedule-reason").value.trim() }, "Det interna schemat är granskat och sparat.");
    text("schedule-feedback", result ? "Schema " + result.id + " · " + (result.enabled ? "aktivt internt schema" : "avstängt") + " · nästa planerade tid " + date(result.next_run_at) : "Schemat blockerades. Se serverns felmeddelande.");
  });
  byId("agent-role-cards").addEventListener("submit", async (event) => {
    const form = event.target.closest(".agent-config-form"); if (!form) return;
    event.preventDefault(); const fields = new FormData(form), payload = { enabled: fields.get("enabled") === "on", reason: fields.get("reason"), tools: fields.getAll("tools") };
    for (const name of ["priority", "per_call_limit_minor", "daily_budget_minor", "max_calls_per_day"]) payload[name] = Number(fields.get(name));
    payload.max_output_tokens = String(fields.get("max_output_tokens") || "").trim() === "" ? null : Number(fields.get("max_output_tokens"));
    const result = await mutate(form.querySelector("button"), "/api/agents/" + encodeURIComponent(form.dataset.role) + "/config", payload, "Rollens interna begränsningar är sparade. Inga externa befogenheter har lagts till.");
    if (result) { delete form.dataset.dirty; document.activeElement.blur(); renderAgents(state.v12?.agents || {}); renderAgentViews(); }
  });
  byId("agents-refresh").addEventListener("click", refresh);
  // v1.4: one addressable workspace view at a time. All counters use persisted data.
  const navigation = {
    overview: { title: "Översikt", base: "/", tabs: [] },
    agents: { title: "AI-agenter", base: "/agents", tabs: [["overview", "Översikt"], ["workflows", "Arbetsflöden"], ["activity", "Aktivitet"], ["history", "Historik"]] },
    missions: { title: "Uppdrag", base: "/missions", tabs: [["active", "Aktiva"], ["waiting", "Väntande"], ["completed", "Slutförda"], ["archive", "Arkiv"]] },
    research: { title: "Research & Leads", base: "/research", tabs: [["leads", "Leads"], ["autonomy", "Autonom research"], ["sources", "Manuella källor"], ["reports", "Analyser"]] },
    deliverables: { title: "Leverabler", base: "/deliverables", tabs: [["library", "Bibliotek"], ["new", "Ny beställning"]] },
    finance: { title: "Ekonomi", base: "/finance", tabs: [["overview", "Översikt"], ["costs", "Kostnader"], ["budget", "Budget"]] },
    settings: { title: "Inställningar", base: "/settings", tabs: [["ai", "AI & modeller"], ["integrations", "API & integrationer"], ["budget", "Bokföring"], ["security", "Säkerhet"], ["schedules", "Schemaläggning"], ["logs", "Systemloggar"]] }
  };
  const fieldLabels = { output: "AI-svar", provider: "Leverantör", model: "Modell", input_tokens: "Indata, token", output_tokens: "Utdata, token", reported_cost_usd: "Leverantörens rapporterade kostnad, USD", cost_sek_minor: "Bokförd AI-kostnad", amount_minor: "Nettobelopp", total_minor: "Totalt inklusive moms", vat_minor: "Moms", list_price_minor: "Listpris", discount_bps: "Rabatt i hundradels procent", source_ids: "Källhänvisningar", summary: "Sammanfattning", text: "Analys", reason: "Bedömning", title: "Titel", goal: "Affärsmål", objective: "Deluppgift", status: "Status", tasks: "Deluppgifter", role: "Agent", next_actions: "Nästa steg", next_action: "Rekommenderat nästa steg", limitations: "Osäkerheter & begränsningar", uncertainties: "Osäkerheter", risks: "Risker", findings: "Iakttagelser", recommendations: "Rekommendationer", content: "Innehåll", sources: "Källor", citations: "Källhänvisningar", evidence: "Underlag", possible_need: "Möjligt behov · hypotes", score: "Bedömning", fit_reason: "Varför erbjudandet passar", email_subject: "Ämne · oskickat", email_body: "Meddelande · oskickat", follow_up_plan: "Intern uppföljningsplan", price: "Prisunderlag", facts: "Faktaunderlag", estimated_cost_minor: "Uppskattad kostnad", estimated_revenue_minor: "Uppskattad framtida intäkt, öre", quality: "Kvalitetsgranskning", issues: "Granskningspunkter", sections: "Avsnitt", subject: "Ämne", predicate: "Uppgift", value: "Värde", values: "Motstridiga värden", target_group: "Målgrupp", problem: "Kundproblem", price_estimate_minor: "Preliminärt pris · inte intäkt", cost_estimate_minor: "Uppskattad kostnad", next_steps: "Nästa interna steg", company: "Företag", company_ids: "Källbelagda företag", package_name: "Tjänstepaket", analysis: "Analys", needs_revision: "Revision behövs", conclusion: "Slutsats", opportunities: "Affärsmöjligheter", business_idea: "Affärsidé", target_audience: "Målgrupp", customer_problem: "Kundproblem", solution: "Föreslagen lösning", pricing_model: "Preliminär prismodell", competition: "Konkurrensläge", assumptions: "Antaganden" };
  function routeInfo() {
    const parts = location.pathname.split("/").filter(Boolean).map((part) => { try { return decodeURIComponent(part); } catch { return part; } });
    const group = navigation[parts[0]] ? parts[0] : "overview", config = navigation[group];
    const sub = group === "deliverables" && parts[1] === "list" ? "library" : parts[1] || config.tabs[0]?.[0] || "overview";
    const detail = ["missions", "agents", "deliverables"].includes(group) && !config.tabs.some(([id]) => id === sub) && sub !== "new";
    return { group, sub, detail, config, panel: group === "overview" ? "overview" : group + "/" + (detail ? "detail" : sub) };
  }
  function routeLink(label, path, className = "text-link") {
    const link = el("a", className, label); link.href = path; link.dataset.route = path; return link;
  }
  function navigate(path, replace = false) {
    const url = new URL(path, location.origin);
    if (url.origin !== location.origin) return;
    if (replace) history.replaceState({}, "", url.pathname + url.search); else history.pushState({}, "", url.pathname + url.search);
    selectPage(); renderV13(); document.documentElement.scrollTop = 0;
    byId("main").focus({ preventScroll: true });
    byId("app-shell").classList.remove("mobile-nav-open"); byId("mobile-menu").setAttribute("aria-expanded", "false"); syncNavigationLayout();
  }
  function selectPage() {
    const info = routeInfo();
    for (const section of document.querySelectorAll(".route-panel")) section.hidden = section.dataset.panel !== info.panel;
    if (!document.querySelector('.route-panel[data-panel="' + info.panel + '"]')) byId("view-overview").hidden = false;
    text("route-title", info.config.title); document.title = info.config.title + " · Jarvis · Ignitic";
    for (const link of document.querySelectorAll(".navigation .nav-link")) { const active = link.dataset.route === info.config.base; link.classList.toggle("active", active); if (active) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current"); }
    const tabs = byId("view-tabs"); tabs.replaceChildren(); tabs.hidden = !info.config.tabs.length;
    for (const [id, label] of info.config.tabs) { const link = routeLink(label, info.config.base + "/" + id, "view-tab"); if (id === info.sub || info.detail && id === info.config.tabs[0][0]) { link.classList.add("active"); link.setAttribute("aria-current", "page"); } tabs.append(link); }
    for (const card of byId("agent-role-cards").children) card.hidden = !info.detail || card.querySelector("form")?.dataset.role !== info.sub;
  }
  function parsedText(value) {
    if (typeof value !== "string") return value;
    try { const parsed = JSON.parse(value); if (parsed && typeof parsed === "object") return parsed; } catch { /* Plain prose is displayed as text. */ }
    return value;
  }
  function humanContent(value, depth = 0) {
    const box = el("div", "human-content");
    value = parsedText(value);
    if (value === null || value === undefined) { box.append(el("p", "muted", "Inget underlag registrerat.")); return box; }
    if (typeof value !== "object") { box.append(el("p", "prose", String(value))); return box; }
    if (depth > 4) { box.append(el("p", "muted", "Mer underlag finns under tekniska detaljer.")); return box; }
    if (Array.isArray(value)) { const ul = el("ul", "content-list"); for (const item of value.slice(0, 30)) { const li = el("li"); li.append(humanContent(item, depth + 1)); ul.append(li); } if (!value.length) box.append(el("p", "muted", "Inga registrerade uppgifter.")); else box.append(ul); return box; }
    if (value.sections && Array.isArray(value.sections)) {
      if (value.title) box.append(el("h2", "", value.title));
      for (const section of value.sections) { const node = el("section", "report-section"); node.append(el("h3", "", first(section.heading, section.key, "Avsnitt")), humanContent(section.content, depth + 1)); if (section.source_ids?.length) node.append(el("p", "source-note", "Underlag: " + section.source_ids.join(", "))); box.append(node); }
      for (const name of ["next_actions", "limitations", "uncertainties"]) if (value[name]) { box.append(el("h3", "", fieldLabels[name]), humanContent(value[name], depth + 1)); }
      return box;
    }
    const omitted = new Set(["id", "trace_id", "ai_result_id", "reservation_id", "workflow_id", "mission_id", "created_at", "updated_at", "source_version", "input_fingerprint", "evidence_fingerprint", "provider_request_id", "input_hash", "config_revision", "ciphertext", "lease_token"]);
    let shown = 0;
    for (const [name, item] of Object.entries(value)) {
      if (omitted.has(name) || item === null || item === undefined || name.endsWith("_id") || typeof item === "boolean") continue;
      const label = fieldLabels[name];
      if (!label && !["status", "message", "body", "subject", "heading", "name", "source_url", "url", "note", "brief"].includes(name)) continue;
      box.append(el("h4", "", label || ({ body: "Meddelande", subject: "Ämne", message: "Meddelande", source_url: "Källadress", url: "Källadress", note: "Anteckning", brief: "Uppdragsbeskrivning", name: "Namn", heading: "Avsnitt" }[name] || name)), name === "status" ? badge(item) : name.endsWith("_minor") && typeof item === "number" ? el("p", "prose", amount(item)) : humanContent(item, depth + 1)); shown++;
    }
    if (!shown) box.append(el("p", "muted", "Registrerat underlag. Full spårning finns under tekniska detaljer."));
    return box;
  }
  function technicalDetails(value) { const details = el("details", "technical-details"); details.append(el("summary", "", "Tekniska detaljer & spårning"), el("pre", "output-text", outputText(value))); return details; }
  function resultDetails(label, value, open = false) { const details = el("details", "output-details"); details.open = open; details.append(el("summary", "", label), humanContent(value), technicalDetails(value)); return details; }
  function empty(target, message, action, href) { target.replaceChildren(); const node = el("div", "empty-state"); node.append(el("span", "empty-symbol", "◇"), el("p", "", message)); if (action && href) node.append(routeLink(action, href, "button button-secondary")); target.append(node); }
  function statCard(label, value, detail, monetary = false) { const card = el("article", "stat-card"); card.append(el("span", "stat-label", label), el("strong", "stat-value", value === undefined || value === null ? "—" : monetary ? amount(value) : value), el("span", "stat-note", detail || "Från beständiga backenddata")); return card; }
  function latest(records) { return [...list(records)].sort((a, b) => number(first(b.updated_at, b.created_at)) - number(first(a.updated_at, a.created_at))); }
  function missionRecords() { return list(state.v12?.agents?.missions); }
  function orderedMissionTasks(tasks) {
    const remaining = new Map(list(tasks).map((task) => [task.id, task])), ordered = [];
    while (remaining.size) {
      const ready = [...remaining.values()].filter((task) => list(task.dependencies).every((id) => !remaining.has(id)));
      if (!ready.length) { ordered.push(...remaining.values()); break; }
      for (const task of ready) { ordered.push(task); remaining.delete(task.id); }
    }
    return ordered;
  }
  function currentTasks(id) { return orderedMissionTasks(list(state.v12?.agents?.tasks).filter((task) => task.mission_id === id)); }
  function resultRecords() { const rows = [...list(state.v12?.services?.results), ...list(state.v12?.reports?.summaries).map((row) => ({ ...row, kind: row.kind || "mission_summary", responsible_agent: "ceo", version: row.version || 1 })), ...Object.values(state.detailCache || {}).filter((row) => row?.id && row?.content)]; return [...new Map(rows.map((row) => [row.id, row])).values()]; }
  function taskOutput(task) { return first(task.output?.output?.text, task.output?.analysis, task.output?.content, task.output?.output, task.output, task.result); }
  function missionState(mission) { return String(first(mission.status, mission.state, "QUEUED")).toUpperCase(); }
  function agentState(role) {
    const status = String(first(role.status, role.state, "")).toUpperCase().replaceAll(" ", "_");
    if (["WORKING", "WAITING", "IDLE", "REVIEW_REQUIRED", "ERROR"].includes(status)) return status;
    // Old servers do not provide a verified activity state. Show idle rather than invent work.

    return "IDLE";
  }
  function compactMission(mission) { const row = el("div", "compact-row"); const main = el("div", "row-copy"); main.append(routeLink(mission.goal || "Internt uppdrag", "/missions/" + encodeURIComponent(mission.id), "row-title"), el("span", "row-meta", date(mission.created_at) + " · " + amount(mission.actual_and_pending_cost_minor))); row.append(main, badge(missionState(mission))); return row; }
  function renderDashboard() {
    const v = state.v12 || {}, metrics = v.metrics || {}, missions = missionRecords(), roles = list(v.agents?.roles), costs = state.data?.budget || {};
    const active = missions.filter((m) => ["RUNNING", "QUEUED"].includes(missionState(m))), completed = missions.filter((m) => missionState(m) === "COMPLETED");
    const stats = byId("dashboard-stats"); stats.replaceChildren(statCard("Aktiva agenter", roles.filter((role) => agentState(role) === "WORKING").length, "Endast faktiskt pågående jobb"), statCard("Pågående uppdrag", active.length, "Aktiva och köade uppdrag"), statCard("Slutförda uppdrag", completed.length, "Registrerade slutföranden"), statCard("Väntande godkännanden", first(metrics.pending_approvals, list(v.agents?.proposals).filter((p) => asStatus(p) === "PENDING").length), "Kräver ett uttryckligt beslut"), statCard("AI-kostnad idag", metrics.ai_cost_today_minor, "Bokförd kostnad · SEK", true), statCard("Budget kvar", first(metrics.budget_remaining_minor, costs.remaining_minor?.total), "Efter kostnader & reserveringar", true));
    const target = byId("dashboard-missions"); target.replaceChildren(); for (const mission of latest(active).slice(0, 4)) target.append(compactMission(mission)); if (!active.length) empty(target, "Inga pågående uppdrag. Börja med ett tydligt affärsmål.", "Skapa uppdrag", "/missions/new");
    const agents = byId("dashboard-agents"); agents.replaceChildren(); for (const role of roles) { const row = el("div", "compact-row"); const name = el("div", "agent-compact-name"); name.append(el("span", "role-symbol", (agentNames[role.role] || role.role).slice(0, 1)), routeLink(agentNames[role.role] || role.role, "/agents/" + role.role)); row.append(name, badge(agentState(role))); agents.append(row); } if (!roles.length) empty(agents, "Agentstatus har inte hämtats ännu.");
    const results = byId("dashboard-deliverables"); results.replaceChildren(); for (const result of latest(resultRecords()).slice(0, 3)) { const row = el("div", "compact-row"); const main = el("div", "row-copy"); main.append(routeLink(result.title || "Intern leverabel", "/deliverables/" + encodeURIComponent(result.id), "row-title"), el("span", "row-meta", typeName(result.kind) + " · version " + result.version)); row.append(main, badge(asStatus(result))); results.append(row); } if (!resultRecords().length) empty(results, "Inga AI-genererade leverabler ännu. Blockerat arbete skapar inga påhittade resultat.");
    const events = byId("dashboard-events"); events.replaceChildren(); for (const event of latest(state.data?.audit).slice(0, 5)) { const row = el("div", "event-row"); row.append(el("span", "event-dot"), el("div", "row-copy")); row.lastChild.append(el("strong", "small", eventName(first(event.action, event.event, "Registrerad systemhändelse"))), el("span", "row-meta", date(first(event.created_at, event.at)))); events.append(row); } if (!list(state.data?.audit).length) empty(events, "Inga systemhändelser tillgängliga.");
    chart("dashboard-cost-chart", list(metrics.series), (p) => p.ai_cost_minor, "Bokförd AI-kostnad per dag", true);
    chart("dashboard-mission-chart", list(metrics.mission_series), (p) => p.completed, "Slutförda uppdrag per dag");
  }
  function eventName(name) { return { login_succeeded: "Intern inloggning", secret_updated: "Serverhemlighet uppdaterad", "policy.rejected": "Behörighetskontroll registrerad", "upgrade.v11_applied": "Tidigare kompatibel uppgradering", "upgrade.v12_applied": "Tidigare kompatibel uppgradering", "mission.created": "Nytt internt uppdrag", "agent.task_finished": "Agentsteg registrerat", "ai.task_finished": "AI-anrop registrerat", "job.enqueued": "Internt jobb köat", "job.completed": "Jobb slutfört", "mission.paused": "Uppdrag pausat", "mission.resumed": "Uppdrag återupptaget", "agent.action_reviewed": "Internt resultat granskat", "auth.login": "Inloggning registrerad", "upgrade.v13_applied": "Kompatibel versionsuppgradering" }[name] || String(name).replaceAll("_", " ").replaceAll(".", " · "); }
  function typeName(kind) { return first(list(state.v12?.catalogue || state.v12?.services?.types).find((row) => row.id === kind)?.name, serviceNames[kind], { competitor_analysis: "Konkurrentanalys", business_plan: "Affärsplan", research_report: "Researchrapport", lead_report: "Leadrapport", proposal_draft: "Offertutkast", action_plan: "Handlingsplan", financial_analysis: "Ekonomisk analys", opportunity_report: "Affärsmöjligheter" }[kind], kind, "Intern rapport"); }
  function renderMissionLists() {
    const all = missionRecords();
    for (const group of ["active", "waiting", "completed", "archive"]) {
      const target = byId("v13-missions-" + group), query = target.parentElement.querySelector(".mission-search")?.value.trim().toLocaleLowerCase("sv") || "";
      const rows = latest(all).filter((m) => ({ active: ["QUEUED", "RUNNING"], waiting: ["PAUSED", "REVIEW_REQUIRED", "BLOCKED", "UNCERTAIN"], completed: ["COMPLETED"], archive: ["FAILED", "CANCELLED", "COMPLETED"] }[group].includes(missionState(m)))).filter((m) => !query || [m.goal, m.id, ...currentTasks(m.id).map((t) => agentNames[t.role])].join(" ").toLocaleLowerCase("sv").includes(query));
      target.replaceChildren(); if (!rows.length) { empty(target, query ? "Inga uppdrag matchar sökningen." : "Inga uppdrag i den här vyn.", "Skapa internt uppdrag", "/missions/new"); continue; }
      for (const mission of rows) { const row = el("article", "mission-list-row"); const title = el("div", "mission-list-title"); title.append(routeLink(mission.goal, "/missions/" + encodeURIComponent(mission.id), "row-title"), el("span", "row-meta", date(mission.created_at) + " · " + mission.id.slice(0, 8))); const progress = currentTasks(mission.id); const done = progress.filter((task) => asStatus(task) === "COMPLETED").length; const meta = el("div", "mission-list-meta"); meta.append(badge(missionState(mission)), el("span", "muted small", done + " / " + progress.length + " registrerade steg"), el("strong", "small", amount(mission.actual_and_pending_cost_minor))); row.append(title, meta); if (mission.blocker?.message) row.append(el("p", "blocker-note", mission.blocker.message)); target.append(row); }
    }
  }
  function missionControls(mission, recovery) {
    const actions = el("div", "button-row"), status = missionState(mission);
    if (["QUEUED", "RUNNING", "PAUSED", "REVIEW_REQUIRED", "BLOCKED", "FAILED"].includes(status)) {
      const action = ["QUEUED", "RUNNING"].includes(status) ? "pause" : "resume";
      if (status !== "REVIEW_REQUIRED" && !(action === "resume" && (!recovery || recovery.failed_task_id))) actions.append(actionButton(action === "pause" ? "Pausa uppdrag" : "Granska & återuppta", (button) => missionAction(button, mission, action), { disabled: !isAdmin() || mission.uncertain_outcome || action === "resume" && isKilled() }));
      actions.append(actionButton("Avbryt uppdrag", (button) => missionAction(button, mission, "cancel"), { disabled: !isAdmin() || mission.uncertain_outcome }));
    }
    const proposal = list(state.v12?.agents?.proposals).find((p) => p.mission_id === mission.id && asStatus(p) === "PENDING");
    if (proposal) for (const [label, decision] of [["Godkänn internt resultat", "approve"], ["Avvisa resultat", "reject"]]) actions.append(actionButton(label, async (button) => { const reason = window.prompt("Beskriv vilka resultat, källor och kostnader du har granskat. Ingen kundkontakt eller betalning godkänns.", ""); if (!reason || reason.trim().length < 12) return; await mutate(button, "/api/agents/actions/" + encodeURIComponent(proposal.id) + "/review", { decision, reason: reason.trim() }, "Granskningen är registrerad."); }, { disabled: !isAdmin() }));
    return actions;
  }
  async function missionAction(button, mission, action) { const reason = window.prompt(({ pause: "Varför pausar du detta uppdrag?", resume: "Vad har granskats innan samma uppdrag får återupptas?", cancel: "Varför ska uppdraget avbrytas? Redan uppkomna kostnader bevaras." })[action], ""); if (!reason || reason.trim().length < 12) return; await mutate(button, "/api/missions/" + encodeURIComponent(mission.id) + "/" + action, { reason: reason.trim() }, action === "cancel" ? "Uppdraget är avbrutet. Historik och kostnader bevaras." : action === "pause" ? "Uppdraget är pausat." : "Återupptagningen har granskats."); }
  function retryRequest(mission, recovery) {
    if (!recovery?.failed_task_id || !recovery.failed_trace_id) return null;
    const identity = [first(state.user?.id, state.user?.email, "session"), mission.id, recovery.failed_task_id, recovery.failed_trace_id, number(recovery.attempts_used)].join(":");
    if (!state.missionRetryRequests[identity]) {
      let retained = null;
      try { retained = JSON.parse(sessionStorage.getItem("jarvis-mission-retry:" + identity) || "null"); } catch { /* Memory still retains the key when storage is unavailable. */ }
      const matches = retained?.payload?.expected_task_id === recovery.failed_task_id && retained?.payload?.expected_trace_id === recovery.failed_trace_id && typeof retained.payload.idempotency_key === "string" && typeof retained.payload.reason === "string";
      state.missionRetryRequests[identity] = { identity, payload: matches ? retained.payload : null, busy: false, submitted: matches && retained.submitted === true, error: null };
    }
    return state.missionRetryRequests[identity];
  }
  function retainRetryRequest(request) {
    try { sessionStorage.setItem("jarvis-mission-retry:" + request.identity, JSON.stringify({ payload: request.payload, submitted: request.submitted })); } catch { /* Backend idempotency and expected trace remain authoritative. */ }
  }
  async function retryFailedStep(button, mission, recovery) {
    const request = retryRequest(mission, recovery);
    if (!isAdmin() || isKilled() || !recovery.eligible || !request || request.busy || request.submitted) return;
    if (!request.payload) {
      const reason = window.prompt("Granska varför delsteget misslyckades, aktuell modell-/rollgräns, källor, återstående budget och AI-godkännande. Ett nytt anrop kan kosta pengar. Endast detta steg och återstående steg fortsätter i samma uppdrag; färdiga resultat och tidigare kostnader bevaras. Beskriv beslutet med 12–500 tecken.", "");
      if (!reason || reason.trim().length < 12 || reason.trim().length > 500) return;
      request.payload = { reason: reason.trim(), idempotency_key: key("mission-step-retry"), expected_task_id: recovery.failed_task_id, expected_trace_id: recovery.failed_trace_id };
      retainRetryRequest(request);
    }
    request.busy = true; request.error = null;
    setBusy(button, true, "Registrerar återupptagning…");
    try {
      await api("/api/missions/" + encodeURIComponent(mission.id) + "/retry-failed-step", { method: "POST", body: request.payload });
      request.submitted = true; retainRetryRequest(request);
      toast("Återförsöket är registrerat för samma uppdrag. Färdiga steg körs inte om; servern prövar alla kostnads- och behörighetsspärrar före anropet.");
      await refresh();
    } catch (error) {
      request.error = error.message;
      showError(error.message); toast(error.message, true);
      // Retain the exact body/key if the response was lost. A repeat cannot create a second retry.
    } finally {
      request.busy = false;
      setBusy(button, false);
      if (state.user && state.v12) renderV13();
    }
  }
  function missionRecovery(mission, detail) {
    const recovery = detail?.recovery;
    if (!recovery && !["REVIEW_REQUIRED", "PAUSED"].includes(missionState(mission))) return null;
    if (recovery && !recovery.failed_task_id && !list(recovery.retries).length && !["REVIEW_REQUIRED", "PAUSED"].includes(missionState(mission))) return null;
    const panel = el("article", "panel mission-recovery"); panel.dataset.missionRecovery = mission.id;
    const heading = el("div", "panel-heading"); heading.append(el("h2", "", "Säker återupptagning av misslyckat steg"));
    if (recovery) heading.append(badge(first(recovery.active_retry?.status, recovery.eligible ? "READY" : "BLOCKED")));
    panel.append(heading, el("p", "muted small", "Endast det bekräftat misslyckade steget får ett nytt försök. Därefter fortsätter uppdragets ofärdiga steg. Färdiga resultat, källor, tidigare anropsspår och kostnader bevaras."));
    if (!recovery) { panel.append(el("p", "inline-feedback", detail?.error || "Servern kontrollerar om detta delsteg kan återupptas säkert…")); return panel; }
    if (recovery.blocker && (["REVIEW_REQUIRED", "PAUSED"].includes(missionState(mission)) || !list(recovery.retries).length)) panel.append(el("p", "blocker-note", first(recovery.blocker.message, errorNames[recovery.blocker.code], recovery.blocker.code)));
    const request = retryRequest(mission, recovery);
    if (request?.error) panel.append(el("p", "inline-feedback", request.error + " Samma begäransnyckel behålls om beslutet behöver skickas igen."));
    if (request?.submitted && !list(recovery.retries).length) panel.append(el("p", "inline-feedback", "Beslutet har registrerats. Hämtar serverns aktuella återförsöksstatus…"));
    const stats = el("div", "detail-stats recovery-stats");
    stats.append(statCard("Återförsök", number(recovery.attempts_used) + " / " + number(recovery.max_attempts), "Administratörsgranskade försök"), statCard("Tilläggskostnad", recovery.additional_cost_minor, "Bokfört för dessa återförsök · ingår i uppdraget", true), statCard("Token för återförsök", number(recovery.total_input_tokens) + " in / " + number(recovery.total_output_tokens) + " ut", "Rapporterad användning, inte nytt kostnadsmandat"));
    panel.append(stats);
    if (recovery.failed_task_id) panel.append(el("p", "row-meta retry-reference", "Misslyckat delsteg: " + recovery.failed_task_id + " · tidigare anropsspår: " + recovery.failed_trace_id));
    if (isAdmin() && recovery.failed_task_id) {
      const button = actionButton(request?.payload && !request.submitted ? "Skicka samma återupptagningsbeslut" : "Återuppta från misslyckat steg", (node) => retryFailedStep(node, mission, recovery), { disabled: !request || !recovery.eligible || isKilled() || request.busy || request.submitted });
      button.dataset.missionRetry = mission.id;
      if (request?.busy) setBusy(button, true, "Registrerar återupptagning…");
      panel.append(el("div", "button-row")); panel.lastChild.append(button, routeLink("Granska AI-inställningar & godkännande", "/settings/ai", "button button-quiet"));
    } else if (!isAdmin() && recovery.failed_task_id) panel.append(el("p", "footnote", "Endast en administratör kan granska och begära ett återförsök."));
    panel.append(el("p", "footnote", "Aktuellt godkänd modell, rollens svarstak och eventuellt förnyat AI-godkännande krävs. Uppdragets tidsgräns, källrättigheter, nätverksrestriktioner och alla kostnadstak gäller före varje nytt anrop. Ingen ofullständig JSON godkänns."));
    if (list(recovery.retries).length) {
      const history = el("div", "retry-history"); history.append(el("h3", "", "Återförsökshistorik"));
      for (const retry of latest(recovery.retries)) {
        const row = el("div", "mission-item retry-attempt"), title = el("div", "task-heading");
        title.append(el("strong", "small", "Återförsök · " + date(retry.created_at)), badge(retry.status)); row.append(title);
        row.append(el("p", "row-meta", first(retry.model, "Modell granskas före anrop") + " · svarstak " + first(retry.max_output_tokens, "—") + " token · timeout " + first(retry.timeout_seconds, "—") + " s"), el("p", "small", number(retry.input_tokens) + " token in / " + number(retry.output_tokens) + " ut · tillägg bokfört " + amount(retry.additional_cost_minor) + " · reserverat " + amount(retry.reserved_cost_minor)));
        if (retry.error_code || retry.blocker) row.append(el("p", "blocker-note", first(retry.blocker?.message, errorNames[retry.error_code], retry.error_code)));
        row.append(el("p", "row-meta retry-reference", "Tidigare spår: " + first(retry.previous_trace_id, "—") + " · nya spår: " + (list(retry.trace_ids).join(", ") || "Ännu inget anrop registrerat")), technicalDetails(retry)); history.append(row);
      }
      panel.append(history);
    }
    return panel;
  }
  function missionTaskUsage(task, traces) {
    const own = list(traces).filter((trace) => trace.task_id === task.id);
    if (!own.length) return null;
    const box = el("div", "task-ai-usage");
    box.append(el("p", "small", "Registrerade AI-anrop: " + own.length + " · " + own.reduce((sum, trace) => sum + number(trace.input_tokens), 0) + " token in / " + own.reduce((sum, trace) => sum + number(trace.output_tokens), 0) + " ut · bokfört " + amount(own.reduce((sum, trace) => sum + number(trace.actual_sek_minor), 0))));
    for (const trace of latest(own)) box.append(el("p", "row-meta retry-reference", "Anropsspår " + trace.id + " · " + statusLabel(trace.status) + (trace.max_output_tokens ? " · svarstak " + trace.max_output_tokens + " token" : "") + (trace.finish_reason ? " · " + first({ length: "Svarstaket nått", max_tokens: "Svarstaket nått", stop: "Normalt avslut" }[trace.finish_reason], trace.finish_reason) : "")));
    return box;
  }
  function renderMissionDetail() {
    const info = routeInfo(); if (info.group !== "missions" || !info.detail) return;
    const detail = state.detailCache["missions:" + info.sub], target = byId("v13-mission-detail"), mission = first(detail?.mission, missionRecords().find((m) => m.id === info.sub)); target.replaceChildren();
    if (!mission) { empty(target, "Uppdraget finns inte i den tillgängliga historiken.", "Till uppdrag", "/missions"); return; }
    const heading = el("div", "view-heading"); const title = el("div"); title.append(routeLink("← Alla uppdrag", "/missions"), el("h1", "", mission.goal), el("p", "muted", mission.id)); heading.append(title, badge(missionState(mission))); target.append(heading, missionControls(mission, detail?.recovery));
    const tasks = orderedMissionTasks(detail?.tasks || currentTasks(mission.id)), done = tasks.filter((t) => asStatus(t) === "COMPLETED").length;
    const stats = el("div", "detail-stats"); stats.append(statCard("Steg", done + " / " + tasks.length, "Slutförda / registrerade"), statCard("Kostnad & reservation", mission.actual_and_pending_cost_minor, "Uppdragets aktuella belastning", true), statCard("Kostnadstak", mission.max_cost_minor, "Överordnade mandat gäller", true), statCard("Tidsgräns", date(mission.deadline_at), "Skapad " + date(mission.created_at))); target.append(stats);
    if (mission.blocker || mission.uncertain_outcome) target.append(el("div", "notice notice-error", first(mission.blocker?.message, mission.blocker?.code, "Leverantörsutfallet är okänt. Manuell avstämning krävs innan fortsatt arbete.")));
    const recovery = missionRecovery(mission, detail); if (recovery) target.append(recovery);
    const webResearch = missionWebResearch(mission, detail); if (webResearch) target.append(webResearch);
    const timeline = el("article", "panel"); timeline.append(el("h2", "", "Uppdragets steg & tidslinje"));
    for (const task of tasks) { const row = el("div", "timeline-row"); const marker = el("span", "timeline-marker", asStatus(task) === "COMPLETED" ? "✓" : "·"); const body = el("div", "timeline-copy"); const header = el("div", "task-heading"); header.append(el("strong", "", (agentNames[task.role] || task.role) + " · " + first(task.objective, task.step, "Internt delsteg")), badge(asStatus(task))); body.append(header, el("p", "row-meta", "Start " + date(first(task.started_at, task.created_at)) + " · klar " + date(task.finished_at))); if (task.error_code || task.error_message) body.append(el("p", "blocker-note", first(errorNames[task.error_code], task.error_message, task.error_code))); const usage = missionTaskUsage(task, detail?.ai_traces); if (usage) body.append(usage); if (task.output) body.append(resultDetails("Visa registrerat resultat", taskOutput(task))); row.append(marker, body); timeline.append(row); }
    if (!tasks.length) timeline.append(el("p", "empty-state", "Ingen deluppgift har registrerats.")); target.append(timeline);
    const outputs = resultRecords().filter((r) => r.mission_id === mission.id); const box = el("article", "panel"); box.append(el("h2", "", "Leverabler")); for (const result of outputs) box.append(routeLink(result.title + " · version " + result.version, "/deliverables/" + encodeURIComponent(result.id), "result-link")); if (!outputs.length) box.append(el("p", "muted", "Ingen leverabel är sparad ännu.")); target.append(box, technicalDetails({ mission, tasks }));
  }
  function renderAgentViews() {
    const roles = list(state.v12?.agents?.roles), target = byId("v13-agent-grid"); target.replaceChildren();
    const descriptions = { ceo: "Planering, delegation och slutrapport", lead_research: "Godkända källor, fakta och osäkerheter", sales: "Målgrupp, affärsmöjligheter och interna utkast", delivery: "Rapporter, affärsplaner och leverabler", finance: "Budget, kostnader och preliminära kalkyler", compliance: "Behörigheter, risker och intern kvalitet" };
    for (const role of roles) { const card = el("article", "v13-agent-card"), header = el("div", "panel-heading"); header.append(el("span", "role-symbol large", (agentNames[role.role] || role.role).slice(0, 1)), badge(agentState(role))); card.append(header, routeLink(agentNames[role.role] || role.role, "/agents/" + role.role, "agent-card-title"), el("p", "muted small", descriptions[role.role])); const active = first(role.active_mission?.goal, role.current_task?.objective, role.last_task?.objective); card.append(el("p", "agent-assignment", active || "Inget aktivt uppdrag")); const activity = list(state.v12?.metrics?.agent_activity).find((a) => a.role === role.role); const cost = first(activity?.booked_cost_minor, role.budget?.actual_minor, role.actual_cost_minor, 0); card.append(el("div", "agent-card-meta", "Bokfört " + amount(cost) + " · " + number(role.completed) + " slutförda steg"), routeLink("Öppna agent →", "/agents/" + role.role, "text-link")); target.append(card); }
    if (!roles.length) empty(target, "Ingen agentstatus finns tillgänglig. Ingen pågående aktivitet antas.");
    const workflows = byId("v13-workflows"); workflows.replaceChildren(); for (const mission of latest(missionRecords()).slice(0, 12)) { const card = el("article", "panel workflow-card"); card.append(compactMission(mission)); const graph = el("ol", "workflow-graph"); for (const task of currentTasks(mission.id)) { const step = el("li", "workflow-step"); step.append(el("strong", "", agentNames[task.role] || task.role), badge(asStatus(task)), el("small", "muted", first(task.step, task.objective))); graph.append(step); } card.append(graph); workflows.append(card); } if (!missionRecords().length) empty(workflows, "Inga beständiga arbetsflöden har skapats ännu.", "Skapa uppdrag", "/missions/new");
    const info = routeInfo(); if (info.group === "agents" && info.detail) {
      const role = roles.find((r) => r.role === info.sub), detail = byId("v13-agent-detail"); detail.replaceChildren(); if (!role) empty(detail, "Agentrollen finns inte.", "Alla agenter", "/agents"); else { const heading = el("div", "view-heading"), title = el("div"); title.append(routeLink("← Alla agenter", "/agents"), el("h1", "", agentNames[role.role] || role.role), el("p", "muted", descriptions[role.role])); heading.append(title, badge(agentState(role))); detail.append(heading); const tasks = latest(state.v12?.agents?.tasks).filter((t) => t.role === role.role); const activity = list(state.v12?.metrics?.agent_activity).find((a) => a.role === role.role) || {}; const stats = el("div", "detail-stats"); stats.append(statCard("Slutförda steg", number(role.completed)), statCard("Bokförd kostnad", first(activity.booked_cost_minor, role.budget?.actual_minor, 0), "Från kostnadsbokföringen", true), statCard("Reserverat", first(activity.reserved_cost_minor, role.budget?.reserved_minor, 0), "Inklusive osäkra utfall", true)); detail.append(stats); const history = el("article", "panel"); history.append(el("h2", "", "Senaste arbete & resultat")); for (const task of tasks.slice(0, 8)) { const row = el("div", "mission-item"); row.append(routeLink(task.objective || task.step || "Agentsteg", "/missions/" + encodeURIComponent(task.mission_id), "row-title"), badge(asStatus(task)), el("p", "row-meta", date(first(task.updated_at, task.created_at)))); if (task.output) row.append(resultDetails("Registrerat resultat", taskOutput(task))); if (task.error_code) row.append(el("p", "blocker-note", first(errorNames[task.error_code], task.error_code))); history.append(row); } if (!tasks.length) history.append(el("p", "empty-state", "Agenten har inga registrerade uppgifter.")); detail.append(history); }
      for (const card of byId("agent-role-cards").children) card.hidden = card.querySelector("form")?.dataset.role !== info.sub;
    }
  }
  function renderDeliverables() {
    const query = byId("deliverable-search").value.trim().toLocaleLowerCase("sv"), results = latest(resultRecords()), target = byId("v13-deliverable-library"); target.replaceChildren();
    for (const result of results.filter((r) => !query || [r.title, r.kind, r.mission_id].join(" ").toLocaleLowerCase("sv").includes(query))) { const card = el("article", "deliverable-card"); card.append(el("div", "file-symbol", "▤"), badge(asStatus(result)), routeLink(result.title || "Intern leverabel", "/deliverables/" + encodeURIComponent(result.id), "result-title"), el("p", "muted small", typeName(result.kind) + " · version " + result.version), el("p", "row-meta", date(result.created_at) + " · " + (agentNames[result.responsible_agent] || "Delivery")), routeLink("Öppna resultat →", "/deliverables/" + encodeURIComponent(result.id), "text-link")); target.append(card); }
    if (!target.children.length) empty(target, query ? "Inga leverabler matchar sökningen." : "Ditt resultatbibliotek är tomt. Ett blockerat uppdrag skapar ingen leverabel.", "Skapa beställning", "/deliverables/new");
    const info = routeInfo(); if (info.group !== "deliverables" || !info.detail) return;
    const reader = byId("v13-deliverable-reader"), result = results.find((r) => r.id === info.sub); reader.replaceChildren();
    if (!result) { empty(reader, "Leverabeln finns inte i tillgänglig historik.", "Till biblioteket", "/deliverables"); return; }
    const heading = el("div", "view-heading no-print"), title = el("div"); title.append(routeLink("← Alla leverabler", "/deliverables"), el("p", "eyebrow", typeName(result.kind)), el("h1", "", result.title), el("p", "muted", "Version " + result.version + " · " + date(result.created_at) + " · " + (agentNames[result.responsible_agent] || "Delivery"))); heading.append(title, badge(asStatus(result))); reader.append(heading);
    const actions = el("div", "button-row no-print"); for (const [format, label] of [["markdown", "Hämta Markdown"], ["json", "Hämta JSON"]]) { const link = el("a", "button button-secondary", label); link.href = "/api/deliverables/" + encodeURIComponent(result.id) + "/export?format=" + format; link.download = ""; actions.append(link); } actions.append(actionButton("Skriv ut / spara som PDF", () => window.print()));
    if (!["APPROVED", "REJECTED"].includes(asStatus(result))) for (const [label, decision] of [["Godkänn version", "approve"], ["Avvisa version", "reject"]]) actions.append(actionButton(label, async (button) => { const reason = window.prompt("Granska innehåll, källor, osäkerheter och exakt version. Godkännandet tillåter inget externt utskick.", ""); if (!reason || reason.trim().length < 12) return; await mutate(button, "/api/factory/results/" + encodeURIComponent(result.id) + "/review", { decision, reason: reason.trim() }, "Versionsgranskningen är registrerad."); }, { disabled: !isAdmin() })); reader.append(actions);
    if (result.mission_id) reader.append(routeLink("Visa uppdragets steg, kostnader & granskningar →", "/missions/" + encodeURIComponent(result.mission_id), "result-link no-print"));
    const document = el("article", "report-document"); document.append(el("p", "document-watermark", "Jarvis · powered by Ignitic · Intern leverabel · " + statusLabel(asStatus(result))), humanContent(result.content || result.markdown)); reader.append(document);
    const sources = el("article", "panel"); sources.append(el("h2", "", "Underlag & kvalitetskontroll")); for (const source of list(first(result.source_snapshot, result.sources, result.citations))) { const item = el("div", "source-item"); item.append(el("strong", "small", first(source.title, source.source_id, source.id, "Källa")), el("p", "prose small", first(source.evidence, source.content, "Registrerat källunderlag")), el("p", "source-note", first(source.source_url, source.url, "Ingen URL registrerad"))); sources.append(item); } if (!list(first(result.source_snapshot, result.sources, result.citations)).length) sources.append(el("p", "muted", "Ingen extern källhänvisning finns. Underlag från briefen är inte verifierad extern fakta.")); for (const issue of list(result.quality?.issues)) sources.append(el("p", "blocker-note", typeof issue === "string" ? issue : first(issue.message, issue.code, "Kvalitetspunkt kräver granskning"))); sources.append(el("p", "footnote", "Automatisk struktur- och källkontroll bevisar inte saklig riktighet. Granska underlaget innan användning.")); reader.append(sources);
    const versions = el("article", "panel no-print"); versions.append(el("h2", "", "Versionshistorik")); for (const previous of results.filter((r) => r.order_id === result.order_id).sort((a, b) => b.version - a.version)) { const row = el("div", "compact-row"); row.append(routeLink("Version " + previous.version + " · " + date(previous.created_at), "/deliverables/" + encodeURIComponent(previous.id)), badge(asStatus(previous))); versions.append(row); } reader.append(versions, technicalDetails(result));
  }
  function renderFinanceV13() {
    const metrics = state.v12?.metrics || {}, finance = first(state.v12?.finance, metrics.finance, {}), target = byId("v13-cost-breakdown"); target.replaceChildren();
    const totals = finance.totals || finance;
    const cards = el("div", "detail-stats"); cards.append(statCard("Bokförd kostnad", first(totals.booked_cost_minor, totals.booked_sek_minor, totals.actual_sek_minor, metrics.actual_cost_minor), "Faktiska registrerade SEK-kostnader", true), statCard("Reserverad kostnad", first(totals.reserved_cost_minor, totals.reserved_sek_minor, metrics.reserved_cost_minor), "Inte slutlig leverantörsdebitering", true), statCard("Verifierad intäkt", metrics.verified_revenue_minor, "Endast verifierade live-kvitton", true)); target.append(cards);
    for (const [name, label] of [["by_model", "Kostnad per modell"], ["by_agent", "Kostnad per agent"], ["by_mission", "Kostnad per uppdrag"], ["by_workflow", "Kostnad per arbetsflöde"]]) { const panel = el("article", "panel"); panel.append(el("h2", "", label)); const rows = list(finance[name]); if (!rows.length) panel.append(el("p", "empty-state", "Ingen bokförd användning i denna grupp ännu.")); for (const row of rows.slice(0, 30)) { const line = el("div", "compact-row"); line.append(el("span", "row-title", first(row.model, agentNames[row.role], row.agent, agentNames[row.id], row.mission_id, row.workflow_id, row.key, row.name, row.id, "Registrerad grupp")), el("strong", "small", amount(first(row.booked_sek_minor, row.booked_cost_minor, row.actual_sek_minor, row.actual_minor, row.cost_minor)))); panel.append(line); panel.append(el("p", "row-meta", "Reserverat: " + amount(row.reserved_cost_minor) + " · " + (number(row.reported_cost_count) ? "Leverantören rapporterade " + row.provider_reported_usd + " USD" : "Ingen leverantörskostnad rapporterad") + " · " + number(row.input_tokens) + " in / " + number(row.output_tokens) + " ut, token")); } target.append(panel); }
    target.append(el("p", "footnote", "Leverantörens rapporterade belopp, frysta växelkursreserveringar och bokförda SEK-belopp är separata underlag. Uppskattad framtida intäkt räknas aldrig som verklig intäkt."));
    chart("finance-outcome-chart", list(metrics.series), (p) => p.cost_minor, "Faktiskt bokförd kostnad i SEK", true);
    chart("finance-revenue-chart", list(metrics.series), (p) => p.verified_revenue_minor, "Verifierad verklig intäkt per dag", true);
    chart("finance-agent-chart", list(metrics.agent_activity).map((p) => ({ ...p, date: agentNames[p.role] || p.role })), (p) => p.completed_tasks, "Registrerade slutförda steg per agent");
  }
  function renderReportsV13() {
    const target = byId("v13-research-reports"); target.replaceChildren();
    for (const report of latest(state.v12?.reports?.opportunities)) { const card = el("article", "panel"); card.append(el("h2", "", "Interna affärsmöjligheter · hypoteser"), el("p", "footnote", "Prisidéer och framtida utfall är uppskattningar. Inget erbjudande har skickats och ingen intäkt har uppstått."), humanContent(report)); if (report.mission_id) card.append(routeLink("Visa uppdrag →", "/missions/" + encodeURIComponent(report.mission_id))); target.append(card); }
    for (const report of latest(state.v12?.reports?.research)) { const card = el("article", "panel"); card.append(el("div", "panel-heading")); card.lastChild.append(el("h2", "", first(report.title, report.goal, "Källbelagd researchrapport")), badge(asStatus(report))); card.append(el("p", "row-meta", date(report.created_at)), humanContent(report.analysis || report.content || report.summary), resultDetails("Källor & underlag", { citations: report.citations, sources: report.source_plan, limitations: report.issues })); if (report.mission_id) card.append(routeLink("Öppna uppdrag →", "/missions/" + encodeURIComponent(report.mission_id))); target.append(card); } if (!target.children.length) empty(target, "Ingen researchrapport är sparad. Godkända underlag krävs innan faktabaserad analys.", "Granska källor", "/research/sources");
    for (const id of ["v13-system-traces", "agent-history-traces"]) { const box = byId(id); box.replaceChildren(el("h2", "", "AI-anrop & spårning")); const traces = latest(state.v12?.ai_traces); for (const trace of traces.slice(0, 30)) { const row = el("div", "mission-item"); row.append(el("div", "task-heading")); row.lastChild.append(el("strong", "small", (agentNames[trace.role] || trace.role) + " · " + trace.model), badge(asStatus(trace))); row.append(el("p", "row-meta", date(trace.created_at) + " · " + number(trace.input_tokens) + " in / " + number(trace.output_tokens) + " ut · " + amount(trace.actual_sek_minor))); if (trace.error_message || trace.validation_error?.message) row.append(el("p", "blocker-note", first(trace.error_message, trace.validation_error?.message))); row.append(technicalDetails(trace)); box.append(row); } if (!traces.length) box.append(el("p", "empty-state", "Inga verkliga AI-anrop har registrerats.")); }
  }
  function renderCatalogue() {
    const catalogue = list(first(state.v12?.catalogue, state.v12?.services?.types)); if (!catalogue.length) return;
    for (const name of ["mission-type", "factory-type"]) { const select = byId(name); if (select.dataset.dirty || select === document.activeElement) continue; const value = select.value; select.replaceChildren(); for (const type of catalogue) { const option = el("option", "", type.name); option.value = type.id; select.append(option); } if ([...select.options].some((option) => option.value === value)) select.value = value; }
  }

  function loadCurrentDetail() {
    const info = routeInfo(); if (!info.detail || !state.user || !["missions", "deliverables"].includes(info.group)) return;
    const cacheKey = info.group + ":" + info.sub; if (state.detailCache[cacheKey] || state.detailLoading === cacheKey) return;
    state.detailLoading = cacheKey; const generation = state.detailGeneration;
    api("/api/" + info.group + "/" + encodeURIComponent(info.sub)).then((result) => { if (generation !== state.detailGeneration) return; state.detailCache[cacheKey] = result; if (routeInfo().sub === info.sub) renderV13(); }).catch((error) => { if (generation !== state.detailGeneration) return; state.detailCache[cacheKey] = { error: error.message }; if (routeInfo().sub === info.sub) showError(error.message); }).finally(() => { if (generation === state.detailGeneration) state.detailLoading = null; });
  }
  function renderSourceDocuments() {
    const target = byId("v13-source-documents"); target.replaceChildren();
    for (const item of list(state.v12?.documents)) { const row = el("div", "source-item"); row.append(el("strong", "small", item.title || "Godkänt källdokument"), el("p", "row-meta", first(item.source_id, "Källa") + " · " + date(first(item.fetched_at, item.collected_at, item.created_at))), el("p", "prose small", String(first(item.content, item.excerpt, "")).slice(0, 700)), technicalDetails(item)); target.append(row); }
    for (const conflict of list(state.v12?.source_conflicts)) { const item = el("div", "notice notice-error"); item.append(el("div", "", first(conflict.message, "Motstridiga källuppgifter: " + [conflict.subject, conflict.predicate].filter(Boolean).join(" · ") + ". Granska de exakta underlagen.")), technicalDetails(conflict)); target.append(item); }
    if (!target.children.length) empty(target, "Inga godkända dokument eller registrerade källkonflikter ännu.");
    const limits = first(state.v12?.finance?.ai_limits, state.v12?.metrics?.finance?.ai_limits, state.v12?.ai_limits, {});
    hydrateConfig("ai-limits-form", { "ai-limit-day": number(limits.daily_limit_minor) / 100, "ai-limit-month": number(limits.monthly_limit_minor) / 100 });
    text("ai-limit-status", "Tillgängligt i dygnsfönstret: " + amount(limits.daily_remaining_minor) + " · i 30-dagarsfönstret: " + amount(limits.monthly_remaining_minor) + ". Globala mandat gäller samtidigt.");
  }
  byId("document-import-form").addEventListener("submit", async (event) => { event.preventDefault(); const document = { title: byId("document-title").value.trim(), content: byId("document-content").value.trim() }; if (byId("document-url").value.trim()) document.source_url = byId("document-url").value.trim(); const result = await mutate(byId("document-import-submit"), "/api/research/sources/" + encodeURIComponent(byId("document-source").value.trim()) + "/documents", { documents: [document], idempotency_key: key("document") }, "Dokumentunderlaget är sparat med källhänvisning. Ingen extern källa har hämtats."); if (result) { byId("document-content").value = ""; delete event.target.dataset.dirty; } });
  byId("ai-limits-form").addEventListener("submit", async (event) => { event.preventDefault(); await saveConfig("ai-limits-form", "ai-limit-submit", "/api/budget/ai-limits", { daily_limit_minor: Math.round(Number(byId("ai-limit-day").value) * 100), monthly_limit_minor: Math.round(Number(byId("ai-limit-month").value) * 100), reason: byId("ai-limit-reason").value.trim() }, "De granskade AI-tidsgränserna är sparade."); });

  function autonomousState() { return state.v12?.autonomous_research || {}; }
  function sourceState(source) {
    const status = String(first(source.status, source.evaluation?.status, "PENDING")).toUpperCase();
    return ["ACCEPTED", "AUTO_APPROVED", "APPROVED"].includes(status) ? "approved" : ["REJECTED", "BLOCKED", "FAILED"].includes(status) ? "rejected" : "pending";
  }
  function publicSourceLink(label, value) {
    try { const url = new URL(value); if (url.protocol !== "https:" || url.username || url.password) throw new Error(); const link = el("a", "source-reference", label || url.href); link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer nofollow"; return link; }
    catch { return el("span", "muted small", "Ingen giltig offentlig HTTPS-referens registrerad."); }
  }
  function researchReasons(values) {
    return list(values).map((item) => typeof item === "string" ? first(errorNames[item], item.replaceAll("_", " ")) : first(item.message, item.rationale, errorNames[item.code], item.code, "Granskning krävs"));
  }
  function stockholmInput(timestamp) {
    if (!timestamp) return "";
    const parts = Object.fromEntries(new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Stockholm", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(new Date(timestamp * 1000)).map((item) => [item.type, item.value]));
    return parts.year + "-" + parts.month + "-" + parts.day + "T" + parts.hour + ":" + parts.minute;
  }
  function parseStockholmInput(value) {
    const values = String(value).match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/); if (!values) return NaN;
    const wall = Date.UTC(Number(values[1]), Number(values[2]) - 1, Number(values[3]), Number(values[4]), Number(values[5])); let instant = wall;
    for (let i = 0; i < 3; i++) { const local = stockholmInput(instant / 1000).match(/\d+/g).map(Number); const observed = Date.UTC(local[0], local[1] - 1, local[2], local[3], local[4]); instant += wall - observed; }
    return stockholmInput(instant / 1000) === value ? instant / 1000 : NaN;
  }
  function renderAutonomyPolicy(data) {
    const policy = data.policy || {}, readiness = data.readiness || {}, costs = data.costs || {};
    text("autonomy-state", readiness.ready ? "Redo inom granskad policy" : policy.enabled ? "Aktiverad · kontroller återstår" : "Avstängd");
    byId("autonomy-state").className = "badge badge-" + (readiness.ready ? "success" : policy.enabled ? "warning" : "neutral");
    const stats = byId("autonomy-costs"); stats.replaceChildren(statCard("Sökbudget · kalendermånad", costs.monthly_limit_minor, first(costs.calendar_month, "Stockholm · hårt tak 100 SEK"), true), statCard("Sökningar · förbrukat", costs.monthly_used_minor, "Inkluderar bokfört och utestående ansvar", true), statCard("Sökbudget kvar", costs.monthly_remaining_minor, "Globala budgettak gäller samtidigt", true), statCard("AI-kostnad", costs.ai_cost_minor, "Separat från sök-API · se Ekonomi för perioder", true));
    summaryRows("autonomy-summary", [["Sökleverantör", policy.provider === "brave" ? "Brave Search" : first(policy.provider, "Ej vald")], ["Servernyckel", data.key_configured ? "Konfigurerad · dold på servern" : "Saknas på servern"], ["Serverns nätverksförmåga", data.runtime_allowed ? "Tillåten · kräver även granskad policy" : "Avstängd"], ["Tillåtna domäner", list(policy.allowed_domains).map((entry) => typeof entry === "string" ? entry : entry.domain).join(", ") || "Inga granskade domäner"], ["Policyrevision", first(policy.revision, "Ej sparad")], ["Sökgränser per uppdrag", number(policy.max_queries_per_mission) + " sökningar · " + number(policy.max_pages_per_mission) + " sidor · " + number(policy.max_rounds) + " omgångar"], ["Tariffberäknad sökkostnad", amount(first(costs.estimated_cost_minor, costs.booked_cost_minor))], ["Leverantörens fakturerade kostnad", costs.billed_cost_minor === null || costs.billed_cost_minor === undefined ? "Inte verifierad · visas inte som faktiskt fakturerad" : amount(costs.billed_cost_minor)], ["Utestående sökreservationer", amount(costs.monthly_reserved_minor)]]);
    const blockers = byId("autonomy-blockers"); blockers.replaceChildren(...researchReasons(readiness.blockers).map((message) => el("li", "blocker-note", message)));
    if (!state.v12?.autonomous_research) blockers.append(el("li", "blocker-note", "v1.5-serverns researchdata kunde inte hämtas. Befintliga manuella funktioner bevaras; autonom research är spärrad."));
    hydrateConfig("autonomy-policy-form", { "autonomy-provider": first(policy.provider, "brave"), "autonomy-allowed-domains": list(policy.allowed_domains).join("\n"), "autonomy-prohibited-domains": list(policy.prohibited_domains).join("\n"), "autonomy-domain-categories": Object.entries(policy.domain_categories || {}).map(([domain, category]) => domain + "=" + category).join("\n"), "autonomy-html": list(policy.allowed_content_types).includes("text/html"), "autonomy-pdf": list(policy.allowed_content_types).includes("application/pdf"), "autonomy-min-reliability": first(policy.min_reliability, 60), "autonomy-source-age": first(policy.max_source_age_days, 730), "autonomy-max-queries": first(policy.max_queries_per_mission, 3), "autonomy-max-pages": first(policy.max_pages_per_mission, 6), "autonomy-max-rounds": first(policy.max_rounds, 3), "autonomy-domain-interval": first(policy.min_domain_interval_seconds, 2), "autonomy-month-limit": number(first(policy.monthly_limit_minor, 10000)) / 100, "autonomy-day-limit": number(first(policy.daily_limit_minor, 1000)) / 100, "autonomy-mission-limit": number(first(policy.mission_limit_minor, 1000)) / 100, "autonomy-terms-reference": first(policy.terms_reference, ""), "autonomy-terms-reviewed": policy.terms_reviewed === true, "autonomy-query-price": first(policy.query_price_usd, ""), "autonomy-pricing-observed": stockholmInput(policy.pricing_observed_at), "autonomy-pricing-evidence": first(policy.pricing_evidence, ""), "autonomy-provider-terms": first(policy.provider_terms_reference, ""), "autonomy-timeout": first(policy.timeout_seconds, 8), "autonomy-max-bytes": first(policy.max_bytes, 524288), "autonomy-max-redirects": first(policy.max_redirects, 2) });
    const form = byId("autonomy-policy-form"); if (!form.dataset.dirty && !form.contains(document.activeElement)) for (const option of byId("autonomy-categories").options) option.selected = list(first(policy.allowed_categories, ["official", "industry", "company", "public_web"])).includes(option.value);
    const mode = byId("mission-research-mode").value;
    text("mission-research-note", mode === "autonomous" ? "Jarvis får själv hitta sidor inom den aktuella webbpolicyn. Inga käll-ID:n krävs för upptäckt. " + (readiness.ready ? "Sökförmågan är redo; uppdragsbudget, AI och övriga kontroller gäller fortfarande." : "Autonom research är ännu spärrad. Granska policy och godkännande under Research & Leads.") : "Manuellt läge använder dina angivna, godkända källor. Inga nya webbsidor söks automatiskt.");
  }
  function renderResearchRuns(target, runs, compact = false) {
    target.replaceChildren();
    for (const run of latest(runs).slice(0, compact ? 4 : 30)) {
      const card = el("article", "research-run"); const head = el("div", "task-heading"); head.append(el("strong", "small", first(run.goal, "Researchuppdrag " + first(run.mission_id, run.id, ""))), badge(run.status)); card.append(head);
      card.append(el("p", "row-meta", "Omgång " + number(run.round) + " · " + list(run.queries).length + " registrerade frågor · " + (Array.isArray(run.pages) ? run.pages.length : number(run.pages)) + " sidläsningar · " + date(first(run.updated_at, run.created_at))));
      const queries = el("ol", "query-list"); for (const query of list(run.queries)) { const item = el("li"); item.append(el("span", "", typeof query === "string" ? query : first(query.query, query.text, "Registrerad fråga"))); if (typeof query === "object") item.append(el("span", "row-meta", statusLabel(first(query.status, "PENDING")) + (query.cost_minor !== undefined ? " · " + amount(query.cost_minor) : ""))); queries.append(item); } if (queries.children.length) card.append(queries);
      for (const message of researchReasons(run.gaps)) card.append(el("p", "evidence-gap", message));
      if (run.blocker) card.append(el("p", "blocker-note", typeof run.blocker === "string" ? first(errorNames[run.blocker], run.blocker) : first(run.blocker.message, errorNames[run.blocker.code], run.blocker.code)));
      if (run.mission_id && !compact) card.append(routeLink("Öppna uppdrag →", "/missions/" + encodeURIComponent(run.mission_id)));
      target.append(card);
    }
    if (!target.children.length) empty(target, "Inga autonoma sökningar är registrerade. Policyn är avstängd tills du granskat och godkänt den.");
  }
  function renderDiscoveredSources(data) {
    const target = byId("autonomy-sources"), filter = byId("autonomy-source-filter").value; target.replaceChildren();
    for (const source of latest(data.sources).filter((item) => !filter || sourceState(item) === filter).slice(0, 60)) {
      const card = el("article", "discovered-source"); const head = el("div", "task-heading"); const status = sourceState(source); head.append(el("h3", "", first(source.title, source.canonical_url, "Upptäckt offentlig källa")), badge(status === "approved" ? "AUTO_APPROVED" : status === "rejected" ? "REJECTED" : "PENDING")); card.append(head);
      card.append(publicSourceLink(first(source.canonical_url, source.url), first(source.canonical_url, source.url)), el("p", "row-meta", "Utgivare: " + first(source.publisher, source.publisher_group, "Inte verifierad") + " · publicerad " + date(source.published_at) + " · läst " + date(first(source.retrieved_at, source.accessed_at, source.created_at))));
      if (source.reliability !== undefined || source.confidence !== undefined) card.append(el("p", "small", "Källtillförlitlighet: " + first(source.reliability, source.confidence) + " / 100 · inte ett intyg om varje påstående."));
      if (source.snippet || source.excerpt) card.append(el("blockquote", "evidence-quote", String(first(source.snippet, source.excerpt)).slice(0, 800)));
      for (const message of researchReasons(first(source.reasons, source.evaluation?.reasons))) card.append(el("p", status === "rejected" ? "blocker-note" : "muted small", message));
      for (const message of researchReasons(source.uncertainties)) card.append(el("p", "evidence-gap", message));
      if (source.content_hash) card.append(el("p", "row-meta source-hash", "Innehålls-ID: " + source.content_hash));
      card.append(technicalDetails(source)); target.append(card);
    }
    if (!target.children.length) empty(target, filter ? "Inga källor med den valda statusen." : "Inga webbkällor har upptäckts. Manuellt godkända kataloger finns i en egen flik.");
  }
  function evidencePackCard(pack, compact = false) {
    const card = el("article", "evidence-pack"), head = el("div", "task-heading"); head.append(el("h3", "", "Faktaunderlag " + String(pack.id || "").slice(0, 12)), badge(pack.status === "COMPLETE" ? "COMPLETED" : "INSUFFICIENT_EVIDENCE")); card.append(head);
    card.append(el("p", "row-meta", "Uppdrag " + first(pack.mission_id, "—") + " · " + list(pack.sources).length + " källor · " + list(pack.claims).length + " påståenden"));
    const claims = list(pack.claims); for (const claim of claims.slice(0, compact ? 5 : 40)) {
      const row = el("div", "claim-card"), heading = el("div", "task-heading"); heading.append(el("strong", "small", first(claim.text, [claim.subject, claim.predicate, claim.value, claim.unit].filter(Boolean).join(" · "), "Registrerat påstående")), badge(claim.status)); row.append(heading);
      if (claim.rationale) row.append(el("p", "muted small", claim.rationale));
      row.append(el("p", "row-meta", "Oberoende utgivare: " + number(claim.independent_publisher_count) + " · " + (claim.kind === "hypothesis" || claim.status === "HYPOTHESIS" ? "Hypotes, inte verifierat faktum" : claim.status === "VERIFIED" ? "Samstämmigt källstöd; sakgranskning krävs fortfarande" : "Uppgiften får inte bli obestyrkt affärsbevis")));
      for (const citation of list(claim.citations).slice(0, compact ? 2 : 8)) { const quote = el("div", "claim-citation"); quote.append(el("blockquote", "evidence-quote", first(citation.snippet, "Exakt stödutdrag saknas")), publicSourceLink(first(citation.publisher, citation.canonical_url, citation.source_id), citation.canonical_url), el("p", "row-meta", "Källa " + first(citation.source_id, "—") + " · läst " + date(citation.retrieved_at))); row.append(quote); }
      if (!compact) for (const message of researchReasons(claim.reasons)) row.append(el("p", "muted small", message));
      card.append(row);
    }
    for (const message of researchReasons(pack.gaps)) card.append(el("p", "evidence-gap", message));
    for (const conflict of list(pack.conflicts)) card.append(el("p", "blocker-note", "Motstridiga uppgifter: " + [conflict.subject, conflict.predicate, list(conflict.values).join(" / ")].filter(Boolean).join(" · ")));
    const uses = list(first(pack.usage, pack.used_by)).concat(list(autonomousState().usage).filter((use) => first(use.evidence_pack_id, use.pack_id) === pack.id));
    const usage = el("div", "knowledge-usage"); usage.append(el("h4", "", "Användning i agentteamet"));
    if (!uses.length) usage.append(el("p", "muted small", "Gemensamt underlag är sparat. Ingen efterföljande agentanvändning har registrerats ännu."));
    for (const use of uses) usage.append(el("p", "small", (agentNames[first(use.role, use.agent_role)] || first(use.role, use.agent_role, "Agent")) + " · " + list(use.claim_ids).length + " refererade påståenden · " + date(first(use.used_at, use.created_at))));
    card.append(usage, el("p", "footnote", "Beläggens status gäller de citerade uppgifterna. Ingen kontakt, betalning, marknadsefterfrågan eller intäkt följer automatiskt av ett källgodkännande."));
    if (!compact && pack.mission_id) card.append(routeLink("Öppna uppdrag →", "/missions/" + encodeURIComponent(pack.mission_id))); if (!compact) card.append(technicalDetails(pack)); return card;
  }
  function missionWebResearch(mission, detail) {
    const data = autonomousState(), runs = list(first(detail?.autonomous_research?.runs, data.runs)).filter((run) => run.mission_id === mission.id), packs = list(first(detail?.evidence_packs, detail?.autonomous_research?.evidence_packs, data.evidence_packs)).filter((pack) => pack.mission_id === mission.id);
    if (mission.research_mode !== "autonomous" && !runs.length && !packs.length) return null;
    const panel = el("article", "panel mission-web-research"); panel.append(el("div", "panel-heading")); panel.lastChild.append(el("h2", "", "Autonom research & delat faktaunderlag"), routeLink("Sökningar, källor & policy →", "/research/autonomy"));
    const runBox = el("div"); renderResearchRuns(runBox, runs, true); panel.append(runBox); for (const pack of packs) panel.append(evidencePackCard(pack, true));
    if (!packs.length) panel.append(el("p", "footnote", "Inget färdigt faktaunderlag har överlämnats. Efterföljande agenter får inte fylla kunskapsluckorna med påhittade affärspåståenden.")); return panel;
  }
  function renderAutonomousResearch() {
    const data = autonomousState(); renderAutonomyPolicy(data); renderResearchRuns(byId("autonomy-runs"), data.runs); renderDiscoveredSources(data);
    const target = byId("autonomy-evidence"); target.replaceChildren(); for (const pack of latest(data.evidence_packs).slice(0, 30)) target.append(evidencePackCard(pack)); if (!target.children.length) empty(target, "Inga autonoma faktaunderlag är sparade. Brist på belägg ska redovisas, aldrig ersättas med fabricerade fakta.");
  }
  byId("mission-research-mode").addEventListener("change", () => renderAutonomyPolicy(autonomousState()));
  byId("autonomy-source-filter").addEventListener("change", () => renderDiscoveredSources(autonomousState()));
  byId("autonomy-policy-form").addEventListener("submit", async (event) => {
    event.preventDefault(); if (!isAdmin() || !state.v12?.autonomous_research) return;
    const split = (value) => value.split(/[\n,]/).map((entry) => entry.trim()).filter(Boolean), categories = {};
    for (const line of byId("autonomy-domain-categories").value.split(/\n/).filter((entry) => entry.trim())) { const parts = line.split("=").map((entry) => entry.trim()); if (parts.length !== 2 || !parts[0] || !parts[1]) { showError("Ange domänkategori som domän=kategori, en per rad."); return; } categories[parts[0]] = parts[1]; }
    const observed = parseStockholmInput(byId("autonomy-pricing-observed").value); if (!Number.isFinite(observed)) { showError("Ange en giltig observationstid i Stockholm för det verifierade priset."); return; }
    const body = { provider: byId("autonomy-provider").value, allowed_domains: split(byId("autonomy-allowed-domains").value), prohibited_domains: split(byId("autonomy-prohibited-domains").value), domain_categories: categories, allowed_categories: [...byId("autonomy-categories").selectedOptions].map((option) => option.value), allowed_content_types: [["autonomy-html", "text/html"], ["autonomy-pdf", "application/pdf"]].filter(([id]) => byId(id).checked).map(([, type]) => type), terms_reviewed: byId("autonomy-terms-reviewed").checked, terms_reference: byId("autonomy-terms-reference").value.trim(), provider_terms_reference: byId("autonomy-provider-terms").value.trim(), monthly_limit_minor: Math.round(Number(byId("autonomy-month-limit").value) * 100), daily_limit_minor: Math.round(Number(byId("autonomy-day-limit").value) * 100), mission_limit_minor: Math.round(Number(byId("autonomy-mission-limit").value) * 100), query_price_usd: byId("autonomy-query-price").value.trim(), pricing_observed_at: observed, pricing_evidence: byId("autonomy-pricing-evidence").value.trim(), min_reliability: Number(byId("autonomy-min-reliability").value), max_source_age_days: Number(byId("autonomy-source-age").value), max_queries_per_mission: Number(byId("autonomy-max-queries").value), max_pages_per_mission: Number(byId("autonomy-max-pages").value), max_rounds: Number(byId("autonomy-max-rounds").value), max_concurrent_calls: 1, min_domain_interval_seconds: Number(byId("autonomy-domain-interval").value), timeout_seconds: Number(byId("autonomy-timeout").value), max_bytes: Number(byId("autonomy-max-bytes").value), max_redirects: Number(byId("autonomy-max-redirects").value), reason: byId("autonomy-policy-reason").value.trim() };
    await saveConfig("autonomy-policy-form", "autonomy-policy-save", "/api/research/autonomy/policy", body, "Webbpolicyn är sparad. Separat godkännande krävs innan betald research får aktiveras.");
  });
  byId("autonomy-activation-form").addEventListener("submit", async (event) => {
    event.preventDefault(); if (!isAdmin() || !autonomousState().policy?.revision || isKilled()) return;
    if (!window.confirm("Godkänn betald offentlig webbresearch inom aktuell domänpolicy, sökbudget och övriga kostnadstak? Inga kundutskick, köp eller betalningar aktiveras.")) return;
    const saved = await saveConfig("autonomy-activation-form", "autonomy-activate", "/api/research/autonomy/activation", { statement: byId("autonomy-activation-statement").value.trim(), policy_revision: autonomousState().policy.revision, reason: byId("autonomy-activation-reason").value.trim() }, "Det separata researchbeslutet är registrerat. Servernyckel, nätverksförmåga och alla budgetkontroller gäller fortfarande."); if (saved) byId("autonomy-activation-form").reset();
  });
  byId("autonomy-deactivate").addEventListener("click", async () => {
    if (!isAdmin() || !autonomousState().policy?.revision) return; const reason = window.prompt("Varför ska autonom webbresearch pausas? Redan bokförda kostnader och faktaunderlag bevaras.", ""); if (!reason || reason.trim().length < 12) return;
    await mutate(byId("autonomy-deactivate"), "/api/research/autonomy/activation", { statement: "DISABLE WEB RESEARCH", policy_revision: autonomousState().policy.revision, reason: reason.trim() }, "Autonom webbresearch är pausad. Historik och kostnader bevaras.");
  });

  function renderV13() {
    if (!state.user || !state.v12) return;
    loadCurrentDetail(); renderDashboard(); renderMissionLists(); renderMissionDetail(); renderAgentViews(); renderDeliverables(); renderFinanceV13(); renderReportsV13(); renderCatalogue(); renderSourceDocuments(); renderAutonomousResearch();
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
      name: byId("source-name").value.trim(), kind: byId("source-kind").value, url: byId("source-url").value.trim(),
      approved: true, license_reviewed: true, legal_review_reference: byId("source-reference").value.trim(),
      fetch_enabled: byId("source-fetch-enabled").checked, minimum_quality: 80, minimum_interval_seconds: 3600,
      cost_minor: Math.round(Number(byId("source-fetch-cost").value) * 100), cost_evidence: byId("source-cost-evidence").value.trim(), data_mode: "REAL"
    }, "Källgranskningen är registrerad. En hämtning kräver ett separat beslut och serverns nätverksgodkännande.");
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

  for (const dialog of document.querySelectorAll("dialog")) dialog.addEventListener("click", (event) => { if (event.target === dialog) { const bounds = dialog.getBoundingClientRect(); if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close(); } });

  document.addEventListener("click", (event) => { const link = event.target.closest("a[data-route]"); if (!link || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return; event.preventDefault(); navigate(link.dataset.route || link.getAttribute("href")); });
  window.addEventListener("popstate", () => { selectPage(); renderV13(); });
  for (const input of document.querySelectorAll(".mission-search")) input.addEventListener("input", renderMissionLists);
  byId("deliverable-search").addEventListener("input", renderDeliverables);
  for (const select of [byId("mission-type"), byId("factory-type")]) select.addEventListener("change", () => { select.dataset.dirty = "true"; });
  byId("theme-toggle").addEventListener("click", () => { const light = document.documentElement.dataset.theme !== "light"; document.documentElement.dataset.theme = light ? "light" : "dark"; try { localStorage.setItem("jarvis-theme", light ? "light" : "dark"); } catch {} byId("theme-toggle").setAttribute("aria-label", light ? "Byt till mörkt tema" : "Byt till ljust tema"); });
  byId("sidebar-toggle").addEventListener("click", () => { const collapsed = byId("app-shell").classList.toggle("sidebar-collapsed"); byId("sidebar-toggle").setAttribute("aria-expanded", String(!collapsed)); try { localStorage.setItem("jarvis-sidebar", collapsed ? "collapsed" : "expanded"); } catch {} });
  byId("mobile-menu").addEventListener("click", () => { const open = byId("app-shell").classList.toggle("mobile-nav-open"); byId("mobile-menu").setAttribute("aria-expanded", String(open)); syncNavigationLayout(); });
  try { document.documentElement.dataset.theme = localStorage.getItem("jarvis-theme") === "light" ? "light" : "dark"; const collapsed = localStorage.getItem("jarvis-sidebar") === "collapsed"; byId("app-shell").classList.toggle("sidebar-collapsed", collapsed); byId("sidebar-toggle").setAttribute("aria-expanded", String(!collapsed)); byId("theme-toggle").setAttribute("aria-label", document.documentElement.dataset.theme === "light" ? "Byt till mörkt tema" : "Byt till ljust tema"); } catch {}
  function syncNavigationLayout() { const closed = matchMedia("(max-width:680px)").matches && !byId("app-shell").classList.contains("mobile-nav-open"); byId("sidebar").inert = closed; byId("sidebar").setAttribute("aria-hidden", String(closed)); }
  window.addEventListener("resize", syncNavigationLayout); syncNavigationLayout();
  byId("secret-config-form").addEventListener("submit", async (event) => { event.preventDefault(); const input = byId("secret-value"); const result = await mutate(byId("secret-submit"), "/api/secrets/" + encodeURIComponent(byId("secret-name").value), { value: input.value }, "Serverhemligheten har uppdaterats. Ingen leverantör har aktiverats."); input.value = ""; if (result) delete event.target.dataset.dirty; });
  byId("user-create-form").addEventListener("submit", async (event) => { event.preventDefault(); const result = await mutate(byId("user-create-submit"), "/api/users", { email: byId("user-email").value.trim(), password: byId("user-password").value, role: byId("user-new-role").value }, "Det interna användarkontot är skapat."); byId("user-password").value = ""; if (result) event.target.reset(); });
  byId("cost-form").addEventListener("submit", async (event) => { event.preventDefault(); const reference = byId("cost-reference").value.trim(); await mutate(byId("cost-submit"), "/api/budget/costs", { idempotency_key: "invoice:" + reference, vendor: byId("cost-vendor").value.trim(), workflow_id: "invoice:" + reference, actual_minor: Math.round(Number(byId("cost-amount").value) * 100), currency: "SEK", category: byId("cost-category").value, source: reference }, "Den faktiska kostnaden är bokförd. Ingen betalning har utförts."); });

  setInterval(() => { if (!document.hidden && state.user) refresh(); }, 10000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden && state.user) refresh(); });
  (async () => { try { establishSession(await api("/api/auth/me")); await refresh(); } catch { showLogin(); } })();
})();
