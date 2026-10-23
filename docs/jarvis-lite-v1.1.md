# Jarvis Lite v1.1: review, operation and safe upgrade

This release prepares the existing one-process/PostgreSQL installation for
reviewed B2B acquisition of the approved incoming-enquiry assistant service.
Preparation does not authorize customer contact, payment requests, purchases,
advertising or customer installation. No Render resource or production
configuration is changed by preparing this branch or its pull request.

## Existing Render installation

Keep the current Render web service, database connection, private network,
session secret, encryption key, administrator identities and environment
variables. The code uses the existing `lite_records`, `lite_audit`,
`lite_control_lock` and `lite_idempotency` tables. New configuration and workflow
records are additive JSON records; do not recreate tables, truncate records,
clear suppressions or generate replacement keys. Stored policy values remain
intact; effective limits enforce the current 2,000 SEK total/500 SEK tool mandate
and price/discount floors even if an older configuration was weaker. Accepted
customer contracts are not silently repriced.

The same Dockerfile builds one Python 3.12 application, serves the existing
dashboard and health endpoint, and runs as UID 10001. PostgreSQL remains the
production persistence layer and the in-process worker uses its durable queue.
No Node build, broker, separate worker, new database, paid scheduler or persistent
application disk is introduced. Runtime dependencies are unchanged; only the
application version changes in the frozen lockfile.

`render-lite.yaml` remains a review plan, not an upgrade command for an existing
installation. Do not apply it again to provision duplicate resources. This
change does not enable auto-deployment or change the production branch in Render.
Review and merge the PR before any separately authorized manual deployment.

## Optional environment variables

Existing required variables keep their meaning. In particular, retain the exact
`JARVIS_DATABASE_URL`, `JARVIS_SESSION_SECRET` and `JARVIS_ENCRYPTION_KEY` values.
Every new capability flag below defaults to `false` when omitted, so an existing
Render service can start without adding them.

| Variable | Default | Purpose |
| --- | --- | --- |
| `JARVIS_ALLOW_PAID_AI` | `false` | Runtime capability for actual AI requests; stored administrator configuration and policy/budget checks are also required. |
| `JARVIS_ALLOW_RESEARCH_NETWORK` | `false` | Runtime capability for fetching an explicitly approved HTTPS source; source approval and enablement are also required. |
| `JARVIS_ALLOW_LIVE_CONTACT` | `false` | Independent runtime capability for real email contact; it is insufficient without the current reviewed contact authorization. |
| `JARVIS_ALLOW_LIVE_PAYMENTS` | `false` | Independent runtime capability for live Stripe requests; current LIVE policy, REAL provenance and exact manual payment approval remain mandatory. |
| `OPENROUTER_API_KEY` | empty | Optional server-only key fallback. An administrator's encrypted stored key takes precedence. Never use a `NEXT_PUBLIC_` variable or expose this value to the dashboard. |
| `BREVO_API_KEY` | empty | Optional server-only Brevo key fallback; it does not enable contact. |
| `EMAIL_WEBHOOK_SECRET` | empty | Shared secret for the authenticated incoming-email signing gateway, not a native Brevo webhook secret. |
| `EMAIL_DELIVERY_WEBHOOK_SECRET` | empty | Separate shared secret for signed delivery/bounce/complaint gateway events. |
| `STRIPE_SECRET_KEY` | empty | Optional server-only Stripe API key fallback. Test keys are for review; production keys also require the independent payment capability and manual gate. |
| `STRIPE_WEBHOOK_SECRET` | empty | Separate server-only secret for official Stripe timestamped webhook signatures; configure the correct test/live endpoint. |

The existing `JARVIS_ALLOW_LIVE=false`, persisted live approval and `KILL_SWITCH`
remain separate controls. Do not set any capability flag to `true` during review.
An API key or a model response cannot grant authority. Configuration status
returns whether a key exists, never its value.

## Preparation workflow

Administrators configure the AI model, cost/token/time limits and approved
research source records. Research accepts reviewed uploads and bounded HTTPS
JSON feeds, rather than treating the public web as an unrestricted crawler.
Source approval records the license/legal reference; each imported lead retains
its source, collection time, citations, relevant evidence and quality assessment.
Duplicate input must not create a second prospect or a second contact opportunity.

The pipeline prepares real leads without sending to them in DEMO. It researches
and qualifies the service fit, writes a personal unsent draft, and records the
legal/contact gate. It processes only verified inbound replies for later sales
steps. Scheduling is separately configured, bounded and persistent; there is no
unbounded agent loop. Jobs retain leases, attempt limits, safety epochs and
idempotency keys across restarts. A stopped or uncertain job requires explicit
review instead of silently resuming or sending again.

Real records, synthetic demonstration records and unclassified legacy records
are displayed separately. A simulated provider receipt never becomes real
revenue when mode or configuration changes. Real confirmed revenue requires
matching signed provider evidence; a customer payment is still separate from a
bank payout. Missing customer production access produces a delivery-plan/manual
task, not a completed installation claim.

The default existing contract still bills after verified delivery. A new
administrator-reviewed quote may explicitly choose `billing_stage=before_delivery`.
Those terms must appear in the exact offer the customer accepts through a verified
reply. Only that accepted quote may reach the prepayment gate; an administrator
must still approve the exact payment request. A signed matching paid receipt can
then create the delivery plan. Existing or accepted delivery-first contracts are
not rewritten into prepayment contracts.

The production Stripe adapter remains dormant unless the separate runtime
payment capability, current global LIVE authority, explicitly sourced REAL
customer, exact accepted offer and manual administrator payment approval all
pass. Signed webhook `livemode` must match the original session, reference,
amount and currency. Test-mode receipts never count as real revenue. A real
confirmed prepayment creates a delivery plan rather than marking the customer
installation complete. This implementation was tested with synthetic credentials
and mocked transport; no real checkout request or production payment was made.
For a REAL lead, a successful Stripe sandbox receipt is `TEST_CONFIRMED`: it
does not advance the customer to paid/delivery states or add real revenue.

## Additive API surface

Existing authenticated routes, roles and CSRF requirements remain in effect.
Configuration mutations below require an administrator. Ingest and pipeline
enqueue are operator/admin actions; viewers retain read-only access.

The email signing gateway must verify its provider, approved mailbox routing,
sender authentication and event/message identity before signing canonical
events. It must never sign arbitrary public JSON. HMAC proves the trusted
gateway's attestation; it does not independently prove email sender ownership.

| Purpose | Route and body |
| --- | --- |
| Capability/readiness status without keys | `GET /api/v1.1/readiness` |
| Reviewed model/bounds configuration | `POST /api/ai/config` with a plain configuration object including `reason` |
| Approved upload/feed source configuration | `POST /api/research/sources/{source_id}` with a plain source object |
| Import attributed prospects | `POST /api/research/sources/{source_id}/ingest` with `records` (at most 50) and `idempotency_key` |
| Fetch one approved feed | `POST /api/research/sources/{source_id}/fetch` with `idempotency_key`; disabled by the network capability default |
| Configure separate sender/domain evidence | `POST /api/outreach/config` with the reviewed configuration object |
| Execute isolated offline safety checks | `POST /api/outreach/checks`; generates server-owned evidence, never live deliverability proof |
| Explicit contact review/revocation | `POST /api/outreach/review`; a separate future approval step, not part of setup |
| Configure bounded preparation schedule | `POST /api/pipeline/config` with a plain configuration object |
| Enqueue one prospect's preparation tick | `POST /api/pipeline/leads/{lead_id}/advance` with `idempotency_key` |
| Resolve an ordinary draft-review task | `POST /api/tasks/{task_id}/resolve` with a meaningful `reason`; cannot approve payments or resume/reconcile jobs |
| Signed delivery gateway receipt | `POST /api/webhooks/email/delivery` with raw signed JSON and `X-Jarvis-Timestamp`, `X-Jarvis-Signature`, `X-Jarvis-Event-Id` |
| Signed unsubscribe | `GET /api/contact/unsubscribe?token=...` previews; a subsequent signed `POST` suppresses contact |

Signed public provider/unsubscribe routes are narrow credential-based exceptions
to interactive login, not general unauthenticated business APIs. Model settings
are bounded by the server; selecting another model does not add tools or override
price, budget, compliance or payment authority.

The default model identifier and token-price caps are review defaults, not a
current provider availability/price quote. Confirm the intended model in the
provider account and configure documented prices, FX, a small per-call cap and
provider-side spend limits before any authorized inference. Retries are bounded;
an ambiguous sent request is quarantined with its budget reservation instead of
being retried as though it were free.

## Email and live authorization

The provider adapter is restricted to the approved provider and reviewed sender
domain. SPF, DKIM and DMARC verification evidence, legal-review reference,
contact-template review and automated test evidence are part of the explicit
contact approval. Administrator-entered DNS-review evidence is an auditable
human review, not a claim that the software queried or verified DNS itself.
Changes to the reviewed configuration invalidate old authority.

Creating a contact approval requires server-generated checks from the preceding
24 hours. An existing approval remains current while its safety epoch,
configuration, credential identities and contact-policy version remain unchanged;
the operator does not need to approve the same unchanged policy every day.
Changing a key or reviewed policy fences the former approval. Clearing an
emergency stop does not restore old queued authority or automatically resume it.

The contact policy distinguishes legal persons, sole traders, generic business
inboxes and identifiable employee addresses. Public availability of an address
does not establish a lawful marketing basis. A qualified reviewer must assess
the actual Swedish/EU marketing and data-protection context, retention,
suppression, processing agreements and templates before separate live approval.

Optional delegated compliance uses `auto_review_source_ids` (default `[]`) and
`reviewed_lawful_basis` (default empty) in the outreach configuration. It is off
unless the reviewer explicitly covers the listed source and lawful basis and
current LIVE customer-contact approval also passes. It covers only explicitly
sourced REAL leads with a current licensed/approved source version, citations
whose hostname exactly matches the reviewed source URL,
quality of at least 80, a legal-person entity and a generic company inbox.
Personal addresses, sole traders, stale/revoked sources, demo/legacy records and
other exceptions retain manual review. This delegation is a narrow reviewed
policy, never an assumption that all B2B marketing is lawful.

The exact server-derived compliance decision carries a policy fingerprint that
is checked again before every contact. A model or client cannot fabricate that
decision. Approved canonical template IDs `contact-standard-v1` and
`followup-standard-v1` permit bounded company-name personalization; they are not
approval for arbitrary AI-generated messages. AI recommendations remain unsent
without their applicable review and contact controls.

Unsubscribe and suppression are checked again immediately before dispatch.
Signed delivery/bounce/complaint events must refer to the expected provider and
message. Where an email provider's native event format is not signed, deploy an
explicit authenticated signing gateway before accepting those events; a generic
HMAC contract is not native-provider webhook support. Complaints and hard-bounce
evidence suppress further contact. Signed incoming facts remain recordable while
the kill switch stops new external effects.

## The three identical enquiry-review tasks

The v1.0 local service acceptance test runs three enquiry cases: sales, support
and billing. Each case called the normal enquiry handler, which created the same
generic pending title, `Review enquiry response draft`. These were three test
case drafts, not three customer replies or three outbound sends.

Version 1.1 distinguishes acceptance-case output from a genuine enquiry that
needs human review. Retained historical rows keep their trace to the enquiry,
lead and test case; obsolete test-review tasks can be marked resolved with audit
evidence rather than deleted. Genuine pending tasks retain customer/context
information and an explicit operator resolution. No task is treated as customer
acceptance, live-contact approval or payment approval merely because its draft
has been reviewed.

The upgrade regression suite rehearses a v1.0-shaped PostgreSQL database through
close/reopen and initialization. It checks retention of users, sessions, policy,
encrypted keys, payment amounts and audit rows, conservative legacy
classification and idempotent upgrade markers. It only creates/removes its own
random test database; it never migrates a production database during CI.

## Cost and financial mandate

The initial server-side mandate remains: 5,000 SEK ordinary minimum setup price
excluding VAT, at most 10% discount, 4,500 SEK absolute discounted floor,
500 SEK AI/tools per rolling 30 days and 2,000 SEK total initial budget. Pending
reservations, actual provider use and external costs consume the same mandate;
funds are not reset by a restart, upgrade or mode change.

Actual AI requests reserve bounded estimated costs before dispatch and record
usage and cost against a trace/workflow. Unknown outcomes retain their reservation
until reconciled; they are not evidence that a retry is free. Source and email
subscriptions/invoices must be recorded, including provider costs incurred
outside this process. Set documented FX, actual currency/timestamp and provider
spend limits before authorizing spending. Do not record an invoice twice by both
settling its reservation and adding a second unrelated expense.

An unknown provider outcome is not cleared by a new idempotency key, restart,
mode change or kill-switch reset. It requires proof-based manual reconciliation
and settlement of the existing budget reservation. Never declare the action
unexecuted merely to free budget or send the same customer request again.

For a future authorized live Stripe session, the current reservation allowance
is **5% of the gross customer amount plus 10 SEK**. This is a conservative policy
estimate, not a current Stripe price quote. At a 5,000 SEK net quote plus 25% VAT,
the allowance is 322.50 SEK. It consumes the same tool/total budget and can be
blocked by the existing daily limit; review the daily cash mandate explicitly
before any activation. Keep that reservation until provider fee evidence can
settle the actual liability. Test-mode checkout reservations are released only
as definitively uncharged test activity. Refunds, transfers, payouts and
payout-account changes are not autonomous capabilities.

There are no new required infrastructure resources. The earlier planning
baseline is **29 USD/month, about 348 SEK before tax and usage at an unverified
12 SEK/USD**, or 435 SEK with illustrative 25% VAT. That is not a current quote
or evidence of the existing production invoice. Use the actual Render plan,
storage, tax, exchange rate and invoice as the cost source. AI, email, source
licenses, domains, overages and provider fees are additional but must fit the
same mandate; they are not excluded from the ledger. The 600 SEK infrastructure
target remains an additional ceiling. See [the budget guide](jarvis-lite-budget.md).

The application can block new requests, not stop an independently billed cloud
subscription. Stopping Jarvis does not stop Render or another provider billing.
Subscriptions, plan upgrades and purchases require separate authorization.

## Review and manual upgrade runbook

1. Review the complete PR and frozen dependency/security/test results. Do not
   merge or deploy while any failed or unreviewed safety check remains.
2. Before a later authorized upgrade, verify an application-consistent
   PostgreSQL backup and a recovery rehearsal into a separate database. Keep the
   original encryption/session keys in approved secret storage. Never put a
   production backup or key in the repository, logs or a shared test database.
3. Record the current image/commit, Render branch, environment variable names,
   database connection identity and counts of users, leads, payments, suppressions,
   queue records and audit history. Do not print secret values into the record.
4. Pause the worker and new external effects using existing operator controls
   before the authorized maintenance window. Leave all new capability flags
   absent/false. Pausing work is not permission to delete queued jobs.
5. Build/test the new image and rehearse the upgrade against an isolated restored
   database with the same keys, non-live flags and workers initially disabled.
   Verify retained users/sessions, secret decryption, audit/history, financial
   totals, source provenance and explicit handling of legacy/test tasks.
6. Only after review and separate authorization, deploy the reviewed commit to
   the existing web service. Reuse the existing private database and environment.
   Do not run `init-demo`, bootstrap a replacement administrator, apply a second
   Blueprint or clear records to obtain a green health check.
7. Verify `/health`, HTTPS, authenticated dashboard, roles/CSRF, financial
   separation, configured keys, kill switch and job pause state before explicitly
   resuming reviewed preparation work. This step does not authorize live contact
   or payment requests.
8. If rollback is needed, stop effects and return to the recorded image with the
   existing keys. Prefer a rehearsed application rollback; restoring a database
   snapshot loses post-snapshot facts and is not an automatic rollback strategy.
   Reconcile any provider events or costs before explicitly resuming work.

No production access, deployment or paid-provider activation is required to
review this release. The existing deployment guide retains local Compose and
backup commands; apply them only to the intended isolated local/test resources.
