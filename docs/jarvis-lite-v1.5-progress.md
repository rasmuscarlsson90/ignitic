# v1.5 implementation checkpoint — 2026-10-24

Branch: `feat/jarvis-lite-v1.5-autonomous-research`. Based on the existing user brief commit `edf99d43` and merged v1.4 `41166d37`. Implementation and offline acceptance are documented below. Production activation remains a separate operator decision.

## Implemented

- Actual Brave Search HTTPS client with server-only key, bounded queries/responses/timeouts, no inherited proxies or redirects, provider-error/unknown-outcome handling, sanitized untrusted discovery results. Only web search is requested; no subscription or additional provider feature is enabled.
- Pinned public HTTPS retrieval for HTML/PDF, DNS/public-address validation on redirects, robots/access restrictions, bounded concurrency/rate/bytes/time, isolated PDF extraction. New runtime dependency: `pypdf==6.21.2` in the frozen lockfile.
- Disabled-by-default, admin/CSRF-protected source policy and separate explicit paid-web-research activation. Domain/category/content/access/quality restrictions apply without per-URL approval. Manual source approval remains available.
- Calendar-month search ceiling of 100 SEK in Europe/Stockholm, configurable downward; daily/mission bounds and the existing shared financial mandate also apply. Reserve before dispatch under the shared database lock. Unknown outcomes retain reservations; stable outbox identities prevent automatic resending. Costs are conservative reviewed-tariff estimates, **not purported provider invoices**; AI costs remain separate.
- Durable PLAN → SEARCH → FETCH → EXTRACT loop, one operation per worker job, bounded continuation and gap-driven follow-up. Automatic source records remain scoped to the originating mission and current policy. Existing tasks/results/costs are retained.
- Immutable evidence packs with canonical URL, publisher, dates, hash and literal snippets. VERIFIED means corroborated literal assertions from independent dated publisher groups, not proven semantic truth. Stale/spoofed/conflicting/unsupported evidence cannot become verified demand.
- Shared evidence handoff to specialists; Sales requires exact VERIFIED premise text and explicit hypothesis classification. Delivery has a corresponding factual-claim guard. Original unsupported-opportunity blockers remain.
- Swedish research policy/discovery/evidence/budget views and autonomous mission selection, retaining the existing design and manual catalogue.

## Final acceptance — PR #7

The original checkpoint is preserved in Git history. Acceptance additions run against the existing v1.5 implementation, not a replacement system.

| Criterion | Evidence and result |
| --- | --- |
| Full HTTP chain and real AI adapter | `test_acceptance_v15.py`: authenticated HTTP mission creation/idempotency and worker ticks; real OpenRouter adapter with `httpx.MockTransport`; real Brave adapter with its own MockTransport; fixture public pages. CEO → bounded Research continuations → Sales → Delivery → Finance → Compliance → CEO summary completes. Nine distinct AI traces, one search, one shared evidence pack recorded for all six roles. |
| Honest costs/data | Search12SEK minor units and AI108minor units are separate; billed search invoice remains unknown. Zero invented CRM leads, email messages, payments or verified revenue. |
| Insufficient evidence | HTTP negative case rejects invented demand, attempts bounded complementary research, records actionable gaps and stops before Sales. Existing strict Sales tests reject substituted unsupported assertions even with valid source IDs. |
| Restart and unknown costs | `test_lifecycle_v15.py`: a new Database instance resumes after the search phase without another paid query; unknown requests remain unknown after reopening with the same retained reservation. |
| Concurrency and calendar budget | Existing eight-independent-connection PostgreSQL search race still applies. New test crosses the exact Europe/Stockholm November boundary: prior settled cost leaves the calendar window; all unknown/pending liabilities remain counted. |
| Revocation and stops | Revoking policy blocks evidence reuse without deleting original packs/sources. Emergency stop between durable intent and HTTP dispatch releases the definitively uncharged reserve and makes zero provider calls. |
| Browser | Reproducible `tests/browser_v15_acceptance.py`: 11 positive controls on a real isolated server populated through the full HTTP/MockTransport acceptance flow. Search queries, approved/rejected source filters and reasons, literal facts/agent usage, separate costs, mission evidence, mobile rendering, viewer restrictions and actual admin policy save/deactivation. No external browser requests or JavaScript errors; server stopped. |
| Backward compatibility | Complete suite includes unchanged v1.4 recovery, authorization, accounting, factory and PostgreSQL tests. |
| Static/security checks | Ruff, mypy30modules, JavaScript syntax, actionlint/whitespace and secret scans. Frozen runtime audit: no known vulnerabilities. |
| Docker | Existing PR #7 workflow is rerun for the final commit: production Dockerfile build, non-root startup, isolated PostgreSQL health/security checks and cleanup. No Render deployment. |

The final local suite passed **822 tests** with PostgreSQL in 63.19seconds. The frozen dependency audit found no known vulnerabilities. The final GitHub Actions conclusion, including Docker checks, is reported in the handoff/PR #7 checks. One upstream Starlette/httpx deprecation warning remains nonblocking.

Two browser-found issues were corrected: rejected search discoveries were omitted from the source list, and several numeric form bounds exceeded the actual server limits. The source list now merges discoveries with evaluations without duplicating evaluated URLs, and UI bounds match server policy.

Browser reproduction (from `apps/jarvis-lite`, after `uv sync --frozen --all-extras`): `uv run --with playwright python tests/browser_v15_acceptance.py`. It uses installed Chromium at `/usr/bin/chromium`, a temporary SQLite database, fake provider keys, blocked external sockets and all served runtime capabilities disabled. The HTTP acceptance fixtures enable only mocked adapters while seeding; they never call a live paid provider.

No implementation acceptance item remains intentionally deferred. Real provider credentials, independently verified current pricing/terms, a production backup/rehearsal and authorized production activation remain operator rollout requirements. Automated corroboration proves literal attributed support, not complete semantic truth, legal permission, or actual market demand beyond the quoted assertion. No paid live API acceptance is claimed.

## Render and configuration

No SQL/DDL migration or new database is required. New policy, activation, run, discovery, evaluation, evidence, outbox and cost records use existing PostgreSQL JSON storage. No startup data reset or automatic source approval occurs.

Required for future authorized real research:

- `BRAVE_SEARCH_API_KEY`: configure privately in Render's environment/secret controls. Never enter it in the browser, repository, sample config or logs.
- `JARVIS_ALLOW_AUTONOMOUS_RESEARCH=true` **and** the existing `JARVIS_ALLOW_RESEARCH_NETWORK=true`: server capabilities only; both default false when absent.
- Existing paid-AI capability, explicit current model activation, compatible settings, FX and budget are required for AI planning/extraction.
- Admin-reviewed allowed domains/categories, applicable website terms, official Brave terms reference, current official per-query pricing evidence and observation date, and a documented current USD/SEK rate.
- Save policy, then explicitly submit `I APPROVE PAID WEB RESEARCH` for its current revision. Saving or changing policy disables it. `DISABLE WEB RESEARCH` pauses the capability. All spending remains bounded.

The official public Brave SDK established the request contract. Direct official pricing/terms pages returned HTTP 403 from this workspace; no current tariff or legal permission is claimed verified. The operator must verify and attest those inputs independently. No price/free-credit assumption is bundled.

Preserve the existing Render service, PostgreSQL URL, session/encryption keys and users. Back up and restore to an isolated database before a separately approved manual upgrade. Do not reapply a Blueprint or run `init-demo` against production. Keep contact/payment capabilities disabled. For rollback, stop workers, return to the recorded earlier image and preserve the database/history; do not blindly resume v1.5 continuation jobs under older code.

CI for checkpoint `0fdfe674` passed: https://github.com/rasmuscarlsson90/ignitic/actions/runs/40078346380 (tests, audit, Docker build/non-root PostgreSQL startup). The PR #7 acceptance update reruns the workflow for the final commit.
