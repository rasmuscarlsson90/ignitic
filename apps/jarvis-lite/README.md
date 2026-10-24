# Jarvis Lite v1.5 development · powered by Ignitic

The autonomous research implementation and completed offline acceptance are documented in
[the v1.5 progress/Render guide](../../docs/jarvis-lite-v1.5-progress.md).
Enable real research only after reviewing the PR, provider terms/pricing, keys and explicit spending authorization.

An internal business-operator console in one FastAPI application with PostgreSQL,
a database-backed queue and six logical agent roles. The original Ignitic stack
and its deployment files remain available independently.

v1.4 adds explicit administrator recovery of a known failed internal AI step in
the original mission, preserving completed work and costs. It adds role token
ceilings, renewed activation after configuration changes and Swedish recovery
history. The supported provider timeout remains 20 seconds; 120 is not enabled.
Read the [v1.4 recovery and upgrade guide](../../docs/jarvis-lite-v1.4.md).

v1.3 adds bounded structured-response normalization/correction, durable mission
controls, separate routable work areas with dark/light themes, internal QA,
expanded saved reports, weekly scheduling and AI-specific budget reporting.
Read the [v1.3 review and operating guide](../../docs/jarvis-lite-v1.3.md) for
the exact boundaries, additive upgrade, costs and manual acceptance checklist.
No new required environment variable or infrastructure service is introduced.

**DEMO is the default. No live providers, real customer messages, payment requests,
customer installations, subscriptions or trading actions are enabled by setup.**

Version 1.2 adds persistent internal missions for six bounded agent roles,
source-attributed research/CRM, unsent offers, five versioned service outputs,
explicit AI activation/diagnostics and internal schedules that default off.
**Real customer contact and payment requests remain blocked by the server in v1.4,
including when old LIVE flags or approvals exist.** Paid AI requires its own
server capability, administrator approval, compatibility and budget checks.
An existing Render installation keeps its PostgreSQL data, encrypted
credentials, operators, sessions and deployment environment. Do not run local
`init-demo` against production or recreate a production database/key to upgrade.
The historical [v1.2 review and upgrade guide](../../docs/jarvis-lite-v1.2.md) remains available, including
the manual Render rehearsal and future separately authorized first-AI-test
checklist. This branch does not deploy, activate providers or create resources.

## Start

Python 3.12 and uv are required. From the repository root:

```bash
cd apps/jarvis-lite
export UV_CACHE_DIR="$PWD/.cache/uv"
uv sync --frozen --extra test
uv run --frozen python -m jarvis_lite.cli init-demo
uv run --frozen python -m jarvis_lite.cli serve --host 127.0.0.1 --port 8020
```

The dashboard is served on local port 8020. Sign in as `admin@example.com` using
the generated password in `.local/admin-password`, read privately. `.env`,
credentials and the persistent SQLite demo database are ignored and must not be
committed or copied into a customer environment. Repeating `init-demo` preserves
existing configuration. PostgreSQL is required for production.

Select **Starta demoresa**. CEO, research, compliance, sales, delivery and finance
coordinate a synthetic company through local acceptance tests. The journey stops
at **PAYMENT_APPROVAL_REQUIRED**. Review the exact customer, service, reference,
net amount, VAT and total, then explicitly approve, request and simulate payment
confirmation using the separate dashboard buttons. Payment confirmation is a
verified simulated provider event, not a model decision or bank payout. The
synthetic customer remains labeled DEMO; restarting the journey does not spam it.

For a complete container/PostgreSQL demonstration, Render inputs, backups and
recovery, follow [the deployment guide](../../docs/jarvis-lite-deployment.md).

## API and authority

All business routes require an opaque HttpOnly session obtained through
`POST /api/auth/login`. `GET /api/auth/me` returns the current user and CSRF token.
Mutations require the session and `X-CSRF-Token`. Viewer, operator and admin roles
are checked against the stored user on each request. There is no public signup,
arbitrary model tool runner or endpoint for directly setting sales/payment state.
Signed provider webhooks are the narrow exception to interactive session auth.

| Function | API |
| --- | --- |
| Dashboard, finance, activity, audit and safety | `GET /api/dashboard` |
| Queue a bounded simulated customer journey | `POST /api/demo/run` |
| Run a queue tick; pause/resume/reconcile explicitly | `POST /api/jobs/run`, `/api/jobs/{id}/pause`, `/resume`, `/reconcile` |
| Global persistent emergency stop | `POST /api/kill-switch` |
| Approved-source lead import and deduplication | `POST /api/leads` |
| Research, qualification, compliance, proposals, acceptance, test delivery | `POST /api/leads/{id}/{action}` |
| Standard contact/proposal dispatch through central policy | `POST /api/leads/{id}/send-contact`, `/send-proposal` |
| Suppression and opt-out | `POST /api/leads/{id}/unsubscribe` |
| Inbound enquiry classification and unsent draft | `POST /api/enquiries` |
| Allowlisted structured agent recommendation; simulation in DEMO | `POST /api/agents/tasks` |
| Finance draft, manual approval, request and simulated receipt | `/api/leads/{id}/payment-draft`, `/api/payments/{id}/approve`, `/request`, `/simulate-paid` |
| Signed payment events and signed inbound email gateway | `POST /api/webhooks/payments/{provider}`, `/api/webhooks/email` |
| Admin-only settings, operators and encrypted provider keys | `PATCH /api/policy`, `POST /api/users`, `POST/DELETE /api/secrets/{name}` |
| Reviewed sender/template configuration | `POST /api/connectors/email/config` |
| Documented FX and already-incurred costs | `POST /api/budget/fx`, `/api/budget/costs` |
| Admin reconciliation of an existing reservation | `POST /api/budget/reservations/{id}/settle` |
| Business Engine readiness, roles, CRM, outputs, schedules and actual metrics | `GET /api/v1.2/overview` |
| Separate administrator AI activation and one explicit connection test | `POST /api/ai/activation`, `/api/ai/test` |
| Bounded internal missions and per-role controls | `POST /api/missions`, `/api/agents/{role}/config` |
| CRM search, scoring and unsent sales preparation | `GET /api/crm/leads`, `POST /api/crm/leads/{id}/score`, `/offer` |
| Versioned internal service outputs and exact-result review | `POST /api/factory/orders`, `/api/factory/orders/{id}/generate`, `/api/factory/results/{id}/review` |
| Explicit administrator opt-in internal recurring work | `POST /api/internal/schedules/{id}` |

Use distinct bounded idempotency keys for intended new actions; reusing a key
with different input fails. A provider timeout leaves a durable uncertain intent
and reserved budget, not permission to resend. Admin reconciliation requires
external evidence and must settle an existing reservation rather than separately
recording the same invoice as a second cost. Fixed cloud invoices without a
reservation use the cost endpoint. Received signed facts survive an emergency
stop; stopped jobs do not resume merely because the stop is cleared.

The optional process-level `KILL_SWITCH=true` also latches the database stop.
Removing that environment flag does not resume work: administrator recovery and
explicit job review remain necessary. Policy changes increment the safety epoch
so pre-change jobs/provider intents cannot silently use old authority.

## Validation

```bash
uv run --frozen pytest -q
uv run --frozen ruff check jarvis_lite tests
uv run --frozen mypy jarvis_lite
node --check jarvis_lite/static/app.js   # optional Node, no frontend build needed
```

Set `JARVIS_TEST_DATABASE_URL` securely to a local PostgreSQL maintenance URL with
CREATE DATABASE permission to include the PostgreSQL concurrency regressions.
Those tests create/drop only their own random databases; without the variable
they explicitly skip. Tests prohibit external network connections; adapter HTTP
tests use mock transports. CI includes actual PostgreSQL tests, hashed runtime
dependency auditing, a non-root Docker build/start and Blueprint privacy checks.

See [validation evidence and scenario coverage](../../docs/jarvis-lite-validation.md).

## Deployment and live limitations

The separate [render-lite.yaml](../../render-lite.yaml) defines one web service
and one private persistent database. It creates paid resources if applied; do not
apply it during review. [Budget assumptions](../../docs/jarvis-lite-budget.md)
estimate the baseline at USD 29 / SEK 348 per month before tax/usage, using an
unverified planning rate of 12 SEK/USD. Current provider pricing, FX and Render
schema/runtime acceptance remain deployment prerequisites, not verified quotes.

The server blocks real customer contact and payment requests for this release.
Existing adapters, signed historical provider events and review controls remain;
neither an old LIVE policy nor a true contact/payment flag bypasses the block.
Synthetic journeys remain labelled DEMO and never create real revenue. Paid AI
can prepare private internal outputs in DEMO only after its independent explicit
authorization. A generated message, quote or deliverable cannot contact a
customer, request payment, publish content or install an integration.

Research uses reviewed uploads and exact approved HTTPS JSON feeds. Internal
scheduled missions use persisted source evidence without automatic fetching.
Source-attributed REAL leads are distinguished from unclassified legacy and
DEMO records; source attribution is not an independent registry identity check.
Service reports cite their evidence, state missing information and require exact
version review before authenticated download. Actual cost charts use the ledger;
real revenue needs matching signed live provider evidence. A model estimate,
synthetic receipt or test payment never counts as genuine income.

Current provider availability, actual production Render settings and a real paid
AI request have not been verified by local mock-transport tests. The
[v1.3 guide](../../docs/jarvis-lite-v1.3.md) documents remaining manual checks,
cost controls and a bounded future AI diagnostic. Customer installation, lawful
live outreach and live billing need a separate future review and implementation;
this release does not authorize or enable them.

The console is for the operator's own organization, retaining visible Ignitic
attribution. Offering customer workspaces as a multi-tenant hosted product needs
a separate review of the existing repository license.
