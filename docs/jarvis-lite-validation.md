# Jarvis Lite validation and remaining gates

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

Stripe currently permits test credentials and test-mode signed events only. It
does not implement autonomous refunds, transfers, payouts or production money
collection. Signed checkout initiation is not a paid event; paid is not bank
payout. Production customer delivery is not installed by local tests: it creates
a manual task and cannot unlock LIVE payment requests. OpenRouter live inference
and Brevo deliverability were not exercised; their adapters were tested with
mocked HTTP and must be reviewed against real sandbox/provider contracts.

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
