# Jarvis Lite validation and remaining gates

## v1.1 review checks (2026-10-23)

The final v1.1 suite passed **249 tests, zero skipped**, including PostgreSQL
concurrency and v1.0-shaped database preservation checks, in 33.92 seconds.
Ruff, mypy for all 16 modules and Actions syntax validation pass. The frozen
runtime dependency audit reports no known vulnerabilities; dependencies are
unchanged except for the project version. One upstream TestClient deprecation
warning remains.

Final review regressions also prove an absolute research deadline across DNS,
TCP, TLS, headers and body, including slow-drip responses. Pausing a leased job
during a real outbox checkpoint prevents both email and AI provider dispatch
without changing the global epoch; uncertain charges remain reserved.

An isolated HTTP/browser rehearsal used actual v1.1 endpoints: approved-source
import, attributed REAL lead, bounded preparation/legal stop, disabled paid AI,
complete DEMO journey with manual payment approval and signed simulated receipt,
strict zero real revenue, task resolution/approval separation and mobile/viewer
behavior. No provider requests were sent. See the
[v1.1 upgrade and operation guide](jarvis-lite-v1.1.md) for current behavior.

The final production Docker image also passed an isolated PostgreSQL rehearsal
seeded with the actual v1.0 image. Upgrading and restarting retained users, an
existing authenticated session, identical encrypted secret ciphertext and
decryption, policy, cost ledger, payment and audit history. Historical acceptance
tasks were classified without deletion; genuine review tasks remained pending.
The simulated journey/manual approval/signed receipt replay and zero real money
checks passed. UID 10001, health 1.1.0, all 19 source/static files and both metadata
files matched the reviewed checkout. Temporary test resources were removed;
the pre-existing local services remained unchanged.

The checks below describe the earlier v1.0 baseline.

This is the recorded **v1.0 validation baseline**. Current v1.1 capabilities,
upgrade checks and independent live-contact/payment gates are described in
[the v1.1 guide](jarvis-lite-v1.1.md); the historical test count below does not
include the new v1.1 regression suites.

Validation date: 2026-10-23. This describes completed local checks, not a Render
deployment or a proof of real customer revenue. No real provider keys, customer
messages, payments, ads, subscriptions, customer installations or trading actions
were used.

## Checks and evidence

The final full suite passed **102 tests, zero skipped**, including both actual
PostgreSQL concurrency tests, in 12.39 seconds. One upstream Starlette TestClient
deprecation warning remains; no failing tests or suppressed dependency advisories
are hidden by that result.

* Unit/domain/API tests exercise the same policy and database code used by the
  server. A socket/DNS guard blocks non-loopback connections during pytest;
  provider adapter tests use HTTPX mock transports, not live services.
* PostgreSQL concurrency tests use independent SQLAlchemy engines/connections.
  They prove one durable claim per job and atomic budget reservations at the cap.
  Only test-created random databases are removed.
* Ruff, mypy and browser JavaScript syntax checks pass. Chromium verified the
  actual session login, dashboard, queued demo journey, explicit payment approval,
  request, signed simulated confirmation, emergency stop, clearing and logout.
  Mobile layout was checked at 390 px with no horizontal page overflow or
  unhandled JavaScript exceptions.
* The Lite Docker image builds and starts as UID 10001 against its own PostgreSQL
  database. Restart preserves policy, safety epoch and audit history. A restore
  rehearsal into an isolated database recovered operators, policy, audit and
  decryptable secret ciphertext using the retained encryption key.
* The frozen runtime dependency audit initially reported cryptography advisories.
  Cryptography was upgraded to 50.0.2; the subsequent audit of 26 pinned, hashed
  production dependencies reports no known vulnerabilities. No advisory was
  suppressed. This is dependency evidence, not a general security certification.
* Render/Compose local topology checks verify one app, one persistent private
  database, no exposed database host port, explicit non-live settings and disabled
  automatic deploys/previews. Provider schema/pricing access returned proxy
  CONNECT 403. No claim is made that Render accepted or ran the Blueprint.

## Requested scenario coverage

| Scenario | Main regression coverage |
| --- | --- |
| Lead discovered, researched and qualified | `test_workflow.py`, full API journey |
| Unauthorized channel/recipient blocked | `test_connectors.py`, `test_workflow.py` |
| Unsubscribed contact blocked, including after reservation | `test_connectors.py` |
| Duplicate messages prevented, including after interrupted dispatch | `test_connectors.py` |
| Signed inbound reply, opt-out and event replay | `test_connectors.py` |
| Price below floor / discount above mandate rejected | `test_workflow.py` |
| Complete approved proposal and stale-policy rejection | `test_workflow.py` |
| Standard local delivery executes acceptance cases | `test_workflow.py` |
| Payment requires exact manual administrator approval | `test_api.py`, `test_connectors.py` |
| Model output cannot confirm payment or customer acceptance | `test_workflow.py`, `test_connectors.py` |
| Duplicate signed events cannot duplicate confirmed payment | `test_api.py`, `test_connectors.py` |
| Budget ceilings include pending costs and concurrent requests | `test_budget.py`, `test_postgres.py` |
| Kill switch blocks new effects / preserves received liabilities | `test_api.py`, `test_budget.py`, `test_connectors.py` |
| Restart, leases, persistent stop and no automatic resume | `test_jobs.py`, `test_budget.py`, container rehearsal |
| Complete simulated customer journey | `test_api.py`, `test_workflow.py`, Chromium and container/PostgreSQL flow |
| No external messages/payments during tests | Shared network guard and mocked adapter transports |
| CSRF, session roles, secret redaction, invalid-mode fail-closed | `test_api.py` |
| Policy changes fence old authority, real/demonstration money separated | `test_api.py`, `test_workflow.py`, `test_connectors.py` |

The mandatory manual payment gate is retained in DEMO. The coordinated agents
reach it autonomously; the test/operator then explicitly performs the human
approval before the journey continues. Automating that approval would contradict
the financial mandate.

## Limits before real operation

There is no unattended live web-prospecting integration. Research operates on
approved imported/customer evidence and synthetic fixtures. Email receiving uses
a canonical-JSON HMAC gateway contract: the signed payload includes `event_id`,
which must equal the event header; it is not an assertion that every provider's
native webhook format has been implemented. Sender SPF/DKIM/DMARC and legal
review evidence must be established by a qualified operator/reviewer before any
separate live enablement.

The v1.0 baseline permitted Stripe test credentials and test-mode signed events
only. v1.1 adds a separately gated production-account adapter, without activating
it. Autonomous refunds, transfers and payouts remain unsupported. Signed checkout
initiation is not a paid event; paid is not bank payout. Production customer
delivery is not installed by local tests. Legacy delivery-first contracts still
require actual delivery proof; new explicit prepayment terms have a separate
manual gate before signed payment evidence can create a delivery plan.
OpenRouter live inference, Stripe live processing and Brevo deliverability were
not exercised; adapters use mocked HTTP and require authorized sandbox/provider
validation before live operation.

Unknown provider outcomes remain quarantined with reserved budget and require
manual reconciliation. Brevo acceptance does not provide the final invoiced
cost; settle its existing reservation against evidence instead of adding the
same invoice as a separate expense. Application budget controls cannot stop a
cloud subscription from billing outside the process.

This is an internal, single-instance review build. Production still needs current
provider prices/schema validation, HTTPS hosting, verified backups/retention,
operator access processes, legal review, spend limits and explicitly authorized
live integration work. Neither the simulated journey nor the Blueprint starts
paid resources or supplies live approval.
