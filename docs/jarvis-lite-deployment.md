# Start and operate Jarvis Lite

Jarvis Lite is a separate feature application. The original Ignitic full-stack
configuration is unchanged. Do not deploy either Blueprint or activate live
providers during review. Installation and tests require no customer credentials.

## Start the local demonstration

Prerequisites: Python 3.12 and uv. From the repository root:

```bash
cd apps/jarvis-lite
export UV_CACHE_DIR="$PWD/.cache/uv"
uv sync --frozen --extra test
uv run python -m jarvis_lite.cli init-demo
uv run python -m jarvis_lite.cli serve --host 127.0.0.1 --port 8020
```

`init-demo` creates ignored local configuration; the first server start initializes
the persistent local demo database. It generates separate session, encryption and administrator secrets;
do not commit, paste into chat or log their values. Read the generated
`.local/admin-password` file privately when signing in as `admin@example.com`.
Reuse the ignored `.env`
and database on restart; generating new encryption keys is not a recovery
procedure. The demo is available on local port 8020. The application serves its
own dashboard; no Node build, RabbitMQ, MongoDB, Neo4j or external AI key is
required.

Use the dashboard's demo action to enqueue the simulated journey. The worker
coordinates the logical roles up to the manual payment gate. Review and approve
the simulated payment request as an administrator, then use the demo provider
confirmation action to finish the journey. Do not interpret the confirmation as
real revenue or money on a bank account. The UI labels demo records and mode.

The default worker shares the web process. To run deterministic tests, disable
the background worker and invoke individual queue ticks through the test/API
helpers. A process restart must reuse the same database and keys. Paused jobs
require explicit administrator resume; clearing the kill switch does not restart
them automatically.

`KILL_SWITCH=true` is a process-level emergency latch as well as the persistent
dashboard control. Startup and action guards persist that stop in the database;
an administrator cannot clear it while the environment latch is true. After
removing the environment latch and restarting, the database remains stopped.
An administrator must explicitly clear the stop and review/resume each paused
job. Policy changes also fence older queued authority; they are not permission
for an old job to continue under stale approvals.

## Local Docker and PostgreSQL

Docker Compose v2 is needed for this optional production-shaped test. `init-demo`
provides local secrets in its ignored Lite `.env`; use this file explicitly so
the full-stack root `.env` is not loaded accidentally:

```bash
# From the repository root:
docker compose --env-file apps/jarvis-lite/.env -f docker-compose.lite.yml build
docker compose --env-file apps/jarvis-lite/.env -f docker-compose.lite.yml up -d --wait
```

The web app binds only local port 8012, and PostgreSQL has no host port. The
dedicated named volume survives container recreation. The Compose file is for
local review, not a production cloud deployment. Never run `down -v` against
data you need to preserve.

If the PostgreSQL instance has no administrator yet, bootstrap one using the
same generated password file through stdin, not a command-line password:

```bash
docker compose --env-file apps/jarvis-lite/.env -f docker-compose.lite.yml \
  exec -T web python -m jarvis_lite.cli bootstrap-admin --email admin@example.com \
  < apps/jarvis-lite/.local/admin-password
```

The administrative email must match your intended local administrator identity.
Do not expose this local demonstration on a public interface. The native SQLite
and container PostgreSQL demonstrations are separate databases.

## Local validation recorded 2026-10-23

The final review image was actually built from the frozen lockfile, then run
against a dedicated local PostgreSQL 16 service. Hashes of all 13 application
source/static files and `uv.lock` matched the checkout. The image ran as UID
10001, excluded `.env` and the administrator-password file, and loaded the
Stockholm timezone and patched cryptography 50.0.2.

A fresh disposable PostgreSQL database completed the actual HTTP customer
journey: queue coordination, manual payment-approval gate, simulated request,
signed provider confirmation, duplicate-event rejection/accounting and final
completion. No live keys were configured and no external provider/business
action was invoked. Process-level emergency latching, persisted pause state and
the absence of automatic job resume were checked. A production-profile restart
retained records and secret decryption and returned HSTS/CSP and Secure,
HttpOnly, SameSite session cookies. Only the generated test database/container
were removed; the earlier demo database was preserved.

A separate PostgreSQL dump/restore rehearsal restored the operator, safety
policy, audit history and encrypted demo webhook secret into an isolated
database, where decryption succeeded with the retained key. The restored test
database was then removed. This is a local rehearsal, not configured offsite
production backup storage.

The frozen, hashed runtime dependency audit checked 26 packages and reported
no known vulnerabilities. This is a Python dependency audit, not a complete OS
image scan. The two non-deploying topology/privacy tests and YAML/Compose parsing
checks passed. Provider-side Render schema, pricing, HTTPS provisioning and
runtime validation remain separate gates below; local success does not claim a
Render deployment was tested.

## Review-only Render plan

`render-lite.yaml` declares only one paid web service and one private managed
PostgreSQL 16 database in Frankfurt. It disables automatic deploys/previews and
sets `JARVIS_ALLOW_LIVE=false`. Applying the Blueprint still creates paid
resources. No Render resource was created during this work.

Before a separately authorized deployment:

1. Obtain current Render pricing, plan availability and a provider validation of
   the Blueprint. Check the actual exchange rate and full tax/usage budget.
   Official pricing/schema access was blocked in this workspace; see the budget
   guide for the provisional 348 SEK/month baseline, not a current quote.
2. Review the local test/build results and run the non-deploying provider schema
   validator. The Docker build proves image construction, not Render acceptance.
3. Generate a stable Fernet encryption key on a trusted machine and enter it in
   Render's secret input. The key is URL-safe base64 of exactly 32 random bytes.
   Generic Render `generateValue` is intentionally not used for this key.
4. Enter the initial administrator email and a strong unique bootstrap password
   securely. Render generates a separate session secret. Never put secret values
   in source, a PR, build arguments, logs or this document.
5. Confirm the database's external allowlist remains empty. The app uses
   `fromDatabase.connectionString` over the provider's internal network; no
   datastore, worker or administration service has a public port.
6. After the explicitly authorized deployment, confirm HTTPS, secure session
   cookies, CSRF protection, role checks, health and database readiness. Remove
   bootstrap environment values after first login and retain a tested recovery
   administrator procedure. Keep live mode disabled.
7. Configure provider-side spending limits and backups before considering any
   separately approved live integration. The global kill switch does not cancel
   provider subscriptions or undo accepted requests.

The web process uses one Uvicorn worker and an in-process scheduler backed by
PostgreSQL. Render restarts it automatically. Queue state and policy survive a
restart. Keep one instance until horizontal-worker concurrency and the complete
deployment have been measured. No paid cron job or extra worker service is
required by this initial topology.

## Backup and restoration

Keep application-consistent PostgreSQL backups, encrypted off the application
host, and retain the encryption key separately under restricted access. Managed
backup availability/retention depends on the purchased provider plan; verify it
before provisioning. A database existing on disk is not a tested backup.

For a local PostgreSQL backup, on a trusted machine with a private destination:

```bash
umask 077
docker compose --env-file apps/jarvis-lite/.env -f docker-compose.lite.yml \
  exec -T postgres pg_dump -U jarvis -d jarvis_lite --format=custom \
  > /secure/path/jarvis-lite.dump
pg_restore --list /secure/path/jarvis-lite.dump > /dev/null
```

Check the command exit status, encrypt the dump and transfer it to approved
storage. Protect customer data, suppressions, audit history and secret ciphertext
as sensitive information. Do not copy production backups into a shared demo.

Rehearse recovery into a newly created isolated PostgreSQL database. Restore
with `pg_restore --exit-on-error --no-owner`, point a non-live app at that database
using the original encryption key, disable workers initially, and verify record
counts, policy, jobs, audit history, secret decryption and payment idempotency.
Never overwrite a live database merely to test restoration. Only after verified
recovery and operator review may jobs be explicitly resumed. If an encryption
key is lost, a SQL dump alone cannot decrypt saved credentials.

## Live activation is a separate project gate

Do not set `JARVIS_ALLOW_LIVE=true` as part of this installation. Before any
separate live approval, review the administrator-controlled contact policy,
approved source/channel list and provider adapters; configure a verified sender
with SPF, DKIM and DMARC; test signed inbound events; set spend and volume caps;
and verify unsubscribe/suppression handling.

A qualified reviewer must assess Swedish/EU GDPR and electronic-marketing rules
for the actual audience and templates. Legal persons, sole traders, generic
company inboxes and identifiable employee addresses are not interchangeable.
Public availability of an address does not establish a lawful marketing basis.
Document purpose, minimization, retention, deletion, access requests, processing
agreements and international transfers. The software does not provide a legal
opinion or make every B2B email lawful.

Use provider test accounts first. Real payment links/invoices still require a
specific manual approval of customer, amount, tax, service and reference. Real
customer installation additionally requires an agreement and explicit access
permission. Live delivery, inference, email deliverability, provider fees and
regulatory compliance cannot be proven by the simulated customer journey.

The current release does not install or verify the service in a customer's
production environment. A local `TEST_VERIFIED` report leaves a live deal in
`DELIVERY_IN_PROGRESS` with a manual task and blocks its downstream production
payment gate. A demo report is never promoted into real delivery evidence.
Keep demo business records out of a future live reporting database; use reviewed
data migration/retention procedures rather than treating simulated income as
confirmed business revenue.

Financial dashboard totals select simulated payments in DEMO and live provider
payments in LIVE. Changing mode never relabels a simulated receipt as real
income. A customer payment confirmation is still not proof of a bank payout.
