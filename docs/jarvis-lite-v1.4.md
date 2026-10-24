# Jarvis Lite v1.4 · reliable mission recovery

This release builds on `feat/jarvis-lite-v1` at `3532bb4a`, the merged v1.3
release. It adds explicit recovery of a definitively failed internal AI step in
the original mission. It does not deploy, enable a provider, contact customers,
request payments, buy resources or change production data.

The reported production mission was not inspected or modified in production.
Automated tests reproduce its reported shape: four
completed steps, Finance stopped with `ai_output_truncated` at 512 output tokens,
then a reviewed 4096-token configuration and recovery of only unfinished work.
Those tests use synthetic evidence and mocked provider responses, not the
private production response or a real OpenRouter charge.

## What recovery does

An administrator opens the mission detail and selects **Återuppta från
misslyckat steg**. The server requires an authenticated stored admin role, CSRF,
a meaningful review reason, an explicit idempotency key and the exact failed
task and trace being reviewed. Operators and viewers cannot authorize recovery.

Only a known, definitively settled JSON/schema/truncation failure can authorize a
replacement attempt. A final deliverable awaiting review is not a failed step.
Unknown provider outcomes, pending liabilities, policy violations, cancelled or
completed work and missing provenance cannot be bypassed with a new request key.
The original ordinary resume action retains its original request identities;
it does not turn a cached failed response into a new paid call.

Recovery keeps the mission and task identities, dependencies, original deadline,
cost ceiling and completed steps. It saves an attempt record containing the prior
failure and job reference, creates a distinct durable retry request/job and
retains all old results, budget entries, provider references, audit events and
traces. Each new provider request has a unique trace. The same recovery request
returns the same accepted attempt; a second request targeting an obsolete failure
cannot start another concurrent attempt. The database control-row lock, durable
outbox and worker leases remain authoritative across processes and restarts.

If a configuration/source fence stops a queued attempt before any provider call,
an administrator can review again using the retained original settled failure.
This is permitted only when no new trace exists or every new trace is definitively
blocked with its reservation released. It still consumes one of the two explicit
attempts. An attempted/unknown call or unsettled reservation cannot use this path.

There are at most **two explicit recovery attempts per task**. A truncated result
is never accepted, even when its JSON happens to parse. Full role-schema and
policy validation applies to each new result. No automatic paid continuation is
started to fill missing JSON or facts. Existing bounded format correction for a
non-truncated formatting error remains separate, independently reserved and
charged; forbidden actions and unknown outcomes remain ineligible.

After a successful retry, the original graph schedules its unfinished dependent
steps. Completed steps and their artifacts are not regenerated. Another
truncation stops for review again; reaching the recovery bound requires a new
reviewed plan rather than unlimited replacement keys.

## Current approval, budgets and sources

The retry uses the currently reviewed model settings, not the failed trace's
former 512-token limit. There is no automatic increase to 4096. The approved
global maximum remains at most 4096, and every role can have a narrower reviewed
`max_output_tokens` value. `null` inherits the approved global maximum. A role
limit above the current global maximum blocks execution; lowering the global
maximum never silently clamps or increases a role's authority.

Changing an existing execution/spending configuration disables paid AI and
requires separate activation of the current saved revision. Supplying
`enabled: true` to the configuration endpoint does not bypass a pending renewal.
Use `/api/ai/activation` with the exact statement `I APPROVE PAID AI CALLS`, the
current `config_revision` and a review reason. Unchanged previously reviewed
settings and server-stored credentials survive upgrade. The explicit retry
decision is additional authorization for this bounded new attempt, not a new
global spending allowance.

This also covers settings changed **before** the v1.4 upgrade: when the failed
trace's configuration revision differs from today's revision, recovery requires
a durable current-revision `ai_paid_activation` record created only by the exact
activation action. The upgrade does not manufacture approval from `enabled: true`.
An older activation recorded only in historical audit rows may therefore require
one fresh explicit review; those audit rows are preserved.

Before acceptance and again before dispatch, recovery checks the applicable
stop/role/tool authority, paid-AI readiness, model compatibility, current FX,
remaining mission/agent/shared/AI budget, deadline and approved source evidence.
Past costs and every unsettled reservation remain counted. A changed or revoked
source cannot be reused as current approved evidence from completed work.
Network research retains its separate capability and source/domain restrictions;
recovery neither refreshes sources implicitly nor enables network access.

The mandate stays unchanged: minimum list price 5,000 SEK, at most 10% discount,
500 SEK tools per rolling 30 days and 2,000 SEK total startup budget. Existing
external expenses count. Customer contact and live payment requests remain
server-blocked. A paid internal AI approval grants neither permission.

## Supported timeout

The supported, admin-reviewed provider timeout remains **2–20 seconds**. Values
above 20, including 120, are rejected explicitly. This is an execution limit,
not a missing UI field: transport retries share one absolute per-call deadline;
the primary request plus the one permitted format correction can consume up to
40 seconds. Worker leases are 90 seconds. A 120-second call could outlive its
lease before allowing correction and safe completion.

The deployed hosting/request/shutdown timeouts have not been independently
verified. This release therefore does not advertise 120-second support. Raising
the limit later requires coordinated HTTP, worker lease/fencing, cancellation,
shutdown and Render timeout validation. A timeout with an unknown provider
outcome retains its reservation for evidence-based reconciliation; it does not
authorize another paid attempt.

## API and Swedish UI

`GET /api/v1.4/overview` exposes the current workspace; older overview endpoints
remain available. Health and application version are `1.4.0`.

```http
POST /api/missions/{mission_id}/retry-failed-step
X-CSRF-Token: <current authenticated session token>
Content-Type: application/json

{
  "reason": "Reviewed current settings, costs, deadline and approved source evidence",
  "idempotency_key": "operator-generated-stable-key-for-this-decision",
  "expected_task_id": "<failed task from current mission detail>",
  "expected_trace_id": "<failed trace from current mission detail>"
}
```

The payload cannot set a new model, token ceiling, price, budget, deadline,
permission or replacement mission. `GET /api/missions/{id}` includes recovery
eligibility, concrete blockers, attempt history, token usage and additional
costs. The mission UI shows the original completed steps, old/new traces and
reviewed effective settings. The retry button stays busy during polling and
duplicate clicks; its stable key survives a lost response and page reload.

Role limits use the existing admin endpoint:
`POST /api/agents/{role}/config` with `max_output_tokens` and `reason`.
Global configuration and activation retain their separate existing endpoints.

## Migration and environment requirements

There is **no SQL/DDL migration**, new table, destructive conversion or data
reset. New retry/attempt and explicit activation proof information uses the existing `lite_records` JSON store
and `lite_idempotency`/`lite_audit` infrastructure. Existing missions retain their
graph version and historical values. The application does not automatically
retry old missions on startup.

No new required environment variables, runtime dependencies, queue service,
database or paid plan are introduced. Keep the existing PostgreSQL URL,
`JARVIS_SESSION_SECRET`, `JARVIS_ENCRYPTION_KEY`, users and encrypted provider key.
Do not run `init-demo`, re-bootstrap the administrator, rotate retained keys or
reapply a Blueprint to upgrade an existing installation.

The existing server capability `JARVIS_ALLOW_PAID_AI` remains separate from saved
admin activation. Keep `JARVIS_ALLOW_LIVE_CONTACT`, `JARVIS_ALLOW_LIVE_PAYMENTS`
and `JARVIS_ALLOW_RESEARCH_NETWORK` disabled. No proxy, internal address or
development credential is required in GitHub Actions or Render.

## Review and future manual deployment

These are future maintenance steps, not deployment authorization for this task:

1. Review the PR, exact commit and successful CI. Back up PostgreSQL and verify
   restoration on an isolated database. Record current image, stop state,
   budgets, job states and session/key identifiers without printing secrets.
2. Pause workers/schedules during the isolated upgrade rehearsal. Preserve all
   environment values and use the reviewed image against a restored copy with
   external capabilities disabled. Check users, session, secret decryption,
   mission/task/result identities, costs, traces and audit history after restart.
3. Exercise truncation recovery with a mocked provider: preserve four completed
   steps, reject missing approval/expired deadline/insufficient budget/revoked
   evidence, accept one deliberate retry and verify no duplicate dispatch.
4. Only after separate review and deployment approval, update the **existing**
   Render web service manually. Do not create resources or reapply Blueprint.
   Automatic deployment and previews remain disabled in repository plans.
5. Verify `/health`, authentication, `/api/v1.4/overview`, mission details and
   finance before resuming workers. Review current global and role token limits,
   model, FX and every required AI activation. Nothing is enabled by upgrade.
6. For the reported mission, inspect the live application state after deployment.
   If its deadline, source rights, provenance or budget no longer permit retry,
   stop and address the blocker through a separately reviewed decision. Do not
   edit database JSON, reset costs or extend its deadline to force acceptance.
7. If all checks pass, an admin may explicitly request recovery. Observe only
   Finance and the original unfinished downstream steps, new traces and actual
   additional charges. Reconcile unknown outcomes before any further attempt.

Rollback uses the recorded earlier image with workers stopped. Preserve the
database and new attempt history; do not blindly resume newly created retry jobs
under older code that does not understand them. Restoring an old database can
discard newer facts and requires a separate recovery decision.

## Verification and limits

Automated provider calls use `httpx.MockTransport`; the test suite blocks external
sockets/DNS. Concurrency tests use independent PostgreSQL connections and only
their own randomly named temporary databases. Browser fixtures are synthetic and
never evidence of a real production charge or completed customer transaction.

| Validation | Result |
| --- | --- |
| Complete unit/API/PostgreSQL suite | **679 passed**; one upstream Starlette/httpx deprecation warning |
| New v1.4 regression cases | 55 included above: 29 AI, 21 engine, three HTTP, two PostgreSQL concurrency |
| Independent focused security review | 50 AI/engine tests passed; no remaining concrete blocking finding |
| Ruff / mypy | Passed; 25 application source files type-checked |
| JavaScript syntax / actionlint for both workflows / whitespace | Passed |
| Application and test secret scans | No secrets detected |
| Chromium recovery acceptance | 12 checks passed; real isolated server/auth and offline failed-Finance fixture, intercepted positive retry transport |

Browser checks cover all seven views, admin/viewer access, real model/role
configuration, the 20-second timeout explanation, Swedish blockers/costs/tokens,
mobile layout, busy state through actual polling/double clicks, exact key and
payload reuse after a lost response/reload, and a new reviewed generation after
a definitively undispatched attempt. Its real fixture ledger remained 60 synthetic
minor units, revenue zero; no browser/provider external requests or JavaScript
errors occurred. The test server was stopped. The actual HTTP retry and complete
downstream flow are separately covered without intercepting the retry endpoint.

The existing GitHub workflow retains tests, security auditing, production image
build and non-root image startup against isolated PostgreSQL. The final CI run is
linked from the PR description/handoff; no Render service or production database
is needed to run these checks on a normal GitHub-hosted Ubuntu runner.

## Changed files

| Files | Purpose |
| --- | --- |
| `jarvis_lite/engine.py` | Recovery policy, attempt/provenance records, source/config/budget guards, original-graph continuation and detail API data |
| `jarvis_lite/ai.py`, `response_contracts.py` | Current-revision approval proof, effective role caps, timeout policy, trace ancestry and strict truncation handling |
| `jarvis_lite/api_v14.py`, `app.py` | Authenticated recovery route, current overview and health/version |
| `jarvis_lite/static/app.js`, `index.html`, `styles.css` | Swedish recovery panel, stable click decisions and role/timeout controls |
| `tests/test_ai_v14.py`, `test_engine_v14.py`, `test_integration_v14.py`, `test_postgres_v14.py` | The new regression cases listed above |
| `tests/test_ai_v11.py`, `test_ai_v13.py` | Existing test setups explicitly renew approval after changing execution limits; original assertions retained |
| `pyproject.toml`, `uv.lock` | Project version 1.4.0; runtime dependency graph unchanged |
| `.env.example`, `README.md`, this guide | Supported limits, migration/operating instructions and costs |

Paths in this table are relative to `apps/jarvis-lite` except this guide.

No real OpenRouter request, production-data recovery, legal review, customer
contact, live payment or deployment was performed. Actual provider output quality
and hosting settings still need separately authorized production acceptance.
