# Jarvis Lite v1.2: internal business engine and review guide

v1.2 extends the existing FastAPI/PostgreSQL installation with bounded internal
missions, source-attributed research, CRM scoring, unsent sales preparation and
five versioned service deliverables. A real configured AI model can prepare
internal work only after separate administrator authorization. This branch does
not activate paid AI, deploy to Render, contact a company, request a payment,
purchase a resource, publish content or install a customer's integration.

**Real customer contact and payment requests are blocked by the v1.2 server even
if old LIVE settings, provider keys or contact/payment environment flags exist.**
Historical signed provider facts, suppression, dashboards and clearly labelled
DEMO journeys remain available. A successful internal job or generated offer is
not a customer, a sale, a paid invoice or verified delivery. There is no crypto,
trading, advertising purchase or autonomous payment authority.

## Why AI stayed disabled

The previous dashboard's AI configuration submit handler unconditionally sent
`enabled: false`. Saving a key/model therefore could not enable paid inference.
The server independently required `JARVIS_ALLOW_PAID_AI=true`; that flag defaults
to false. A provider key alone has never been sufficient authority. The source
review establishes the UI issue; this work has no access to the current Render
environment or OpenRouter account and does not claim their settings or balance
have been inspected.

The new dashboard separates configuration, explicit activation, disable and a
single connection test. Readiness explains the actual server runtime gate,
stored administrator enablement, key presence/decryption, model compatibility,
FX freshness, budget and emergency-stop blockers. It never returns the key.
The exact activation statement is `I APPROVE PAID AI CALLS`, accompanied by the
current configuration revision and a meaningful review reason. Activation does
not change an environment flag or authorize customer actions.

Model metadata checks structured JSON responses, supported request parameters,
context/output capacity and reviewed price caps. The bundled default profile is
format information, not a claim of current provider availability or a completed
paid test. Other models need reviewed provider metadata; there is no silent
fallback. The explicit model-catalog refresh is a separate network action.

## Internal work and authority

An operator submits a concrete mission, selected source/lead identifiers, a
deliverable type and a cost ceiling. CEO asks the actual reviewed AI for a bounded
plan, then persists tasks for lead research, compliance, sales, delivery and
finance. Dependencies, tool allowlists, task counts and executable job types are
server-selected. Model text cannot add executable tools or change policy. CEO
summarizes the completed internal outputs for review. Each role retains its
tasks, results, history, status, cost attribution and review actions across
process restarts. Missing/disabled AI produces a blocked task with a reason,
not a fabricated successful result.

Administrators can pause individual roles, review internal actions and configure
bounded role call/cost limits. Global budgets remain authoritative. Leased jobs,
missions, role configuration and schedules are checked again immediately before
a paid provider attempt. A changed safety epoch, role permission, schedule
revision or emergency stop fences already queued work. Clearing a stop does not
silently resume it. Unknown provider outcomes keep their reservation and stable
request identity for evidence-based manual reconciliation.

Research uses explicitly approved, licensed source records and their imported
evidence. A mission does not treat arbitrary web text as permission to fetch or
scrape. A separately requested fetch still needs the research runtime flag and
the exact approved HTTPS source. Internal recurring missions use existing
source evidence and do not enable automatic source fetching. Company facts,
citations, collection times, source versions and uncertainty remain attached to
the research report. Missing information remains unknown; model recommendations
do not become verified company facts or invented addresses.

CRM supports search/filtering and bounded scoring. Qualification explains its
evidence and missing information. Sales preparation produces a personal unsent
message, proposed service/price and next internal action. It does not create
contact authorization, an accepted contract, a checkout session or a payment.
The financial mandate remains at least 5,000 SEK net, at most 10% discount,
500 SEK tools per rolling 30 days and 2,000 SEK total start budget. AI and other
external costs count against the appropriate shared limits; infrastructure is
also included in the total and its existing separate ceiling. Accepted legacy
contracts are not rewritten.

## Service factory

| Type | Internal output |
| --- | --- |
| `market_analysis` | Evidence-linked market, positioning and opportunity analysis |
| `seo_analysis` | Observations from supplied evidence, content gaps and suggested checks |
| `content_calendar` | Seven days of draft content, channels, objectives and measurement |
| `marketing_strategy` | Proposed audience, message, channels, execution and measurement |
| `prospecting_report` | Selected sourced REAL companies, supported needs and internal next steps |

An order retains its brief, evidence snapshot, responsible delivery agent,
versions and status. Only a real authorized model response can create a result.
Evidence-dependent outputs require current approved sources. Reports distinguish
supplied observations, recommendations and limitations; they do not claim a
website crawl, market measurement or company verification that did not happen.
The result schema and citations are validated before an administrator reviews
the exact version. Markdown/JSON export is an authenticated download for internal
handling. Approval/export does not send, publish, install or prove acceptance by
a customer. A failed, rejected or uncertain generation is retained for review.

## Recurring internal schedules

No new schedule is enabled by installation. An administrator must explicitly
configure a named schedule with a review reason. Supported kinds are `mission`,
`factory`, `lead_rescore` and `report`; email, payment, arbitrary tools and source
fetches are not schedule kinds. Reports calculate persisted actual metrics
without pretending that an AI call occurred.

Schedules use `Europe/Stockholm` calendar days and either a local daily clock or
a bounded interval of 15 minutes to seven days. Each has a per-day run limit,
daily SEK-minor cost ceiling, revision and authorized safety epoch. A delayed
worker enqueues one due run rather than replaying every missed interval. The
same schedule cannot overlap its active or unresolved work. Pending liabilities
never age out of its cost ceiling; actual daily costs reset at Stockholm midnight.
Global and agent budget windows remain their existing rolling 24-hour/30-day
windows and can be stricter than a schedule's calendar allowance.

Changing/pausing a schedule fences its old jobs. After an emergency stop or
authority change, a fresh explicit administrator review is necessary; merely
clearing the stop or restarting the server is insufficient. Re-review preserves
historical run counts and costs. It does not delete or automatically resume the
old paused/uncertain work. Recent run status is derived from its durable jobs.
A policy-blocked due mission records a blocked run without partial mission/job
writes; it consumes its run allowance and does not starve unrelated queued work.

## Dashboard accounting

The Business Engine dashboard shows source-attributed REAL leads, research,
active/completed/failed jobs, internal offers, service results and pending review.
“Verified leads” means explicit REAL provenance plus supplied source citations;
it is not an independent company-registry or mailbox-ownership check. AI trace
identifiers, role, workflow, tokens and actual settled costs support audit.

Cost charts show 30 Stockholm calendar dates. Daily and calendar-month AI costs
are distinct from the rolling 30-day budget view. Actual ledger costs include
infrastructure and externally entered invoices; pending reservations are shown
separately and reduce remaining budget. Recorded costs may be zero when no real
provider use has been recorded. Cloud invoices must be entered once or reconciled
against their original reservation; an absent invoice is not proof of free hosting.
The "Bokförda AI-anrop" counter uses settled provider usage. Attempted or unknown
calls remain visible in AI traces and pending reservations, without inventing
their token use or settled cost.

Real revenue requires a matching signature-verified live payment event, expected
provider/session, amount, currency and a REAL customer. DEMO, sandbox, unclassified
and fabricated receipt records do not qualify. Net outcome is verified recorded
revenue less recorded actual costs. Forecasts, drafts, pending quotes and mock
receipts are never counted as actual income. Existing historical receipts can
remain visible while new live payment requests are blocked.

## API additions

All existing session, role and CSRF checks remain. Writes require an operator or
administrator; activation, configuration, reviews and schedule changes require
an administrator. Viewer access remains read-only.

| Purpose | Route |
| --- | --- |
| Actual readiness, roles, CRM, factory, schedules and metrics | `GET /api/v1.2/overview` |
| Explicit AI activation/disable | `POST /api/ai/activation` |
| One explicit bounded paid AI diagnostic | `POST /api/ai/test` |
| Explicit compatible-model catalog refresh | `POST /api/ai/models/refresh` |
| Create a bounded internal mission | `POST /api/missions` |
| Explicit mission recovery | `POST /api/missions/{id}/resume` |
| Pause a mission and fence its queued work | `POST /api/missions/{id}/pause` |
| Configure/pause a role | `POST /api/agents/{role}/config` |
| Review an exact internal action | `POST /api/agents/actions/{id}/review` |
| Cancel an internal job with review reason | `POST /api/internal/jobs/{id}/cancel` |
| Search/filter source-attributed prospects | `GET /api/crm/leads` |
| Enqueue scoring or an unsent offer | `POST /api/crm/leads/{id}/score`, `/offer` |
| Review an internal offer without contact authority | `POST /api/crm/offers/{id}/review` |
| Create/generate an internal service order | `POST /api/factory/orders`, `/api/factory/orders/{id}/generate` |
| Review/download a result | `POST /api/factory/results/{id}/review`, `GET /api/factory/results/{id}/export` |
| Explicitly configure/enable/pause an internal schedule | `POST /api/internal/schedules/{id}` |

## Environment, infrastructure and costs

There are **no new required environment variables or infrastructure services**.
Keep the current `JARVIS_DATABASE_URL`, `JARVIS_SESSION_SECRET`,
`JARVIS_ENCRYPTION_KEY`, operator identities and encrypted provider credentials.
New record types and the v1.2 upgrade marker use existing JSON tables. Startup
does not reset data, change policy, create recurring work or activate providers.

| Existing variable | v1.2 meaning |
| --- | --- |
| `JARVIS_ALLOW_PAID_AI` | False by default; server capability only. Stored explicit activation, key, compatibility, FX, budget and stop checks also apply. |
| `OPENROUTER_API_KEY` | Optional server-only fallback; existing encrypted administrator key takes precedence. Never expose it in frontend variables. |
| `JARVIS_ALLOW_RESEARCH_NETWORK` | False by default; fetches still require exact approved sources and an explicit action. |
| `JARVIS_ALLOW_LIVE_CONTACT` / `JARVIS_ALLOW_LIVE_PAYMENTS` | Retained for compatibility; v1.2 blocks real contact/payment requests even when true. Leave false. |
| `JARVIS_ALLOW_LIVE` | Does not override the v1.2 business-action block. Leave false during review. |
| `JARVIS_WORKER_ENABLED` | Existing in-process durable worker; set false in an isolated upgrade rehearsal until reviewed. |
| `KILL_SWITCH` | Additional process-level stop; removing it does not resume paused work. |

One existing Render web service and private PostgreSQL are sufficient. The same
non-root Python 3.12 Docker image serves the UI and durable in-process queue.
No Redis, paid cron, external worker, application disk, new database, service,
domain purchase or plan upgrade is required. Do not reapply a Blueprint to the
existing installation or change its production branch/auto-deploy settings.

The change introduces **zero required additional infrastructure resources**;
that is not a quote for the existing service. The earlier 29 USD/month and
12 SEK/USD figures are unverified planning assumptions, not current Render
prices, invoices or FX. Check the actual existing Render plan, storage, taxes,
invoice and exchange rate before any separately approved deployment decision.
AI charges depend on the explicitly selected provider/model and actual tokens;
the configured caps are ceilings, not a provider price quote. Feed licenses,
domains, email fees and other external invoices must also be recorded if incurred.
This work neither obtains subscriptions nor verifies a live account's prices.
Stopping Jarvis does not stop independent Render/provider billing.

## Known practical limits

- Research requires operator-approved uploads or exact licensed HTTPS JSON feeds.
  No web-search subscription, unrestricted crawler or live company directory has
  been acquired or configured. Without attributed input, work blocks explicitly.
- A mission has at most seven tasks and one provider inference per leased task.
  Sales prepares one cited lead per mission; other ranked leads remain available
  for separately reviewed work. This is bounded internal coordination.
- The existing catalogue permits the incoming-enquiry assistant. Suggestions for
  other commercial packages require a separate service-registry review; generating
  a marketing report does not approve selling a new service.
- JSON/schema/citation validation cannot prove semantic correctness. An operator
  must review facts, usefulness, source rights and each exact deliverable version.
- Real provider access, account credit and current model behavior were not tested.
  Unknown outcomes require provider evidence and manual cost reconciliation; they
  cannot be bypassed with a new request key.

## Validation performed before review

- 414 automated tests passed with the local PostgreSQL integration tests enabled.
  External sockets and DNS are blocked by the test fixture; providers use mocks.
- An authenticated end-to-end test executes seven durable jobs across all six
  roles through the actual AI adapter with `httpx.MockTransport`, records costs,
  ranks imported leads, generates a cited version and reviews/exports it.
- Regression tests cover namespace attribution, concurrent budget reductions,
  provider uncertainty, schedule concurrency, source revocation and a competing
  administrative stop during the durable provider handoff.
- Ruff, mypy, JavaScript syntax, both GitHub Actions files and frozen runtime
  dependency audit passed. Secret scans found no leaks in changed product,
  test and documentation directories.
- Isolated Chromium checks exercised actual local API forms, login/roles,
  blocked AI/jobs, CRM, all five service order types and mobile views. Activation
  payloads were intercepted; no paid capability was enabled on the test server.
- The final non-root production Docker image passed an actual v1.1-image to
  v1.2 PostgreSQL upgrade and restart. All preexisting records, audit rows and
  idempotency entries were retained exactly, including an authenticated session,
  encrypted secret, policy, ledger and paused job. Only the additive v1.2 marker
  was added; source/static hashes matched the final checkout. Temporary rehearsal
  resources were removed and the primary local services remained unchanged.

The upstream Starlette TestClient/httpx deprecation warning is informational;
runtime dependencies remain unchanged. These local checks do not attest to a
live Render account, provider balance, semantic report quality or live billing.

## Manual Render upgrade test plan

These steps describe a future reviewed maintenance operation. None is an
instruction to deploy or modify the current production environment now.

1. Review the PR, complete tests/security checks and the existing Render branch,
   image/commit and variable **names**. Record retained users, sessions, leads,
   suppressions, jobs, accepted contracts, receipts, ledger totals and audit
   counts. Never copy secret values into a ticket or log.
2. Verify a consistent PostgreSQL backup and restore rehearsal in an isolated
   database. Retain the exact session/encryption keys in approved secret storage.
   A new encryption key cannot decrypt old credentials. Do not run `init-demo`
   against production or recreate its database or administrator.
3. Run the reviewed image on the isolated restoration with workers initially
   disabled and all external flags false. Check health, login/session continuity,
   roles/CSRF, retained secret decryption, every existing record, audit history,
   budget/revenue separation and no provider activation after repeated startup.
4. Exercise an explicitly enabled internal metrics report, a blocked mission
   with AI disabled, job restart/recovery, exact-version output review and the
   emergency stop. Clearing the stop must leave old work paused and schedules
   needing fresh review. Keep real contact/payment blocked, including a test
   with old LIVE settings/flags true.
5. Only after separate authorization, manually deploy the approved commit to the
   existing web service/database. Preserve environment values, private database,
   domains, dashboard and permissions. Do not provision a second Blueprint or
   turn on auto-deployment as part of this upgrade.
6. Verify `/health`, HTTPS, authenticated overview, history, accounting and stop
   state before reviewing any worker resumption. Roll back the application image
   to the recorded commit if needed; restoring an old database can lose later
   facts and requires its own recovery decision and reconciliation.

## First real AI request checklist

This is a **future separately authorized paid test**, not a test performed by
this change. A real account, key, provider balance, current prices and current
model availability have not been verified here. Automated tests use mocked
transport and prohibit external provider connections.

1. Obtain explicit approval for exactly one small internal inference and its
   spend ceiling. Keep real contact/payment disabled, schedules off, existing
   queued work paused and the worker disabled during this diagnostic.
2. Review the intended model in the provider account, its supported parameters,
   structured response format, current token prices, account limits and any
   required terms. Use documented model metadata; do not assume that a typed
   model name is compatible or that a bundled profile proves availability.
3. Privately retain/configure the server key and existing encryption key. Record
   a fresh documented USD/SEK rate. Include prior actual infrastructure/tool
   invoices and outstanding reservations in the ledger first.
4. Save small explicit input/output/token/timeout limits, zero retries and a
   conservative reservation that covers the reviewed price bound. Set a small
   SEK per-call cap and ensure role, daily, rolling-tool and total remaining
   budgets cover it. An insufficient reserve must fail before any request.
5. In the authorized environment, enable only the paid-AI runtime capability.
   Read every readiness blocker, then use the separate administrator activation
   with the exact statement, current revision and review reason. Saving a model,
   key or contact policy does not perform this activation.
6. Request the single AI connection test with one unique idempotency key and a
   harmless short prompt containing no customer secrets. Inspect the real
   provider/model, trace, tokens, settled charge and FX. A syntactically valid
   response is a successful connection, not proof of a useful customer service.
7. If the outcome is unknown, retain its reservation and reconcile provider
   evidence; do not retry with a fresh key. Disable paid AI after the diagnostic
   and review the recorded actual cost before authorizing any mission, schedule
   or further inference. This never authorizes customer contact or payment.
