# v1.5 implementation checkpoint — 2026-10-24

Branch: `feat/jarvis-lite-v1.5-autonomous-research`. Based on the existing user brief commit `edf99d43` and merged v1.4 `41166d37`. This is a reviewable implementation milestone, **not a claim that every acceptance criterion is finished or that production activation is approved**.

## Implemented

- Actual Brave Search HTTPS client with server-only key, bounded queries/responses/timeouts, no inherited proxies or redirects, provider-error/unknown-outcome handling, sanitized untrusted discovery results. Only web search is requested; no subscription or additional provider feature is enabled.
- Pinned public HTTPS retrieval for HTML/PDF, DNS/public-address validation on redirects, robots/access restrictions, bounded concurrency/rate/bytes/time, isolated PDF extraction. New runtime dependency: `pypdf==6.21.2` in the frozen lockfile.
- Disabled-by-default, admin/CSRF-protected source policy and separate explicit paid-web-research activation. Domain/category/content/access/quality restrictions apply without per-URL approval. Manual source approval remains available.
- Calendar-month search ceiling of 100 SEK in Europe/Stockholm, configurable downward; daily/mission bounds and the existing shared financial mandate also apply. Reserve before dispatch under the shared database lock. Unknown outcomes retain reservations; stable outbox identities prevent automatic resending. Costs are conservative reviewed-tariff estimates, **not purported provider invoices**; AI costs remain separate.
- Durable PLAN → SEARCH → FETCH → EXTRACT loop, one operation per worker job, bounded continuation and gap-driven follow-up. Automatic source records remain scoped to the originating mission and current policy. Existing tasks/results/costs are retained.
- Immutable evidence packs with canonical URL, publisher, dates, hash and literal snippets. VERIFIED means corroborated literal assertions from independent dated publisher groups, not proven semantic truth. Stale/spoofed/conflicting/unsupported evidence cannot become verified demand.
- Shared evidence handoff to specialists; Sales requires exact VERIFIED premise text and explicit hypothesis classification. Delivery has a corresponding factual-claim guard. Original unsupported-opportunity blockers remain.
- Swedish research policy/discovery/evidence/budget views and autonomous mission selection, retaining the existing design and manual catalogue.

## Verified at this checkpoint

- Full suite with local PostgreSQL: **811 passed**, one upstream Starlette/httpx warning, before the last two Sales cases were added.
- Latest focused suite: **14 passed**, including those two Sales cases. It covers a complete mocked search/fetch/extract/evidence loop, replay without another search charge, source acceptance without manual URLs, stale/spoofed/fabricated claims, unknown-charge retention, policy hard bounds, old pending liabilities and HTTP admin/CSRF checks.
- Existing search-provider and public-retrieval tests are included in the full run. Existing v1.4 tests remain intact.
- Ruff, mypy (30 modules), JavaScript syntax, actionlint for both workflows and whitespace checks passed.
- Chromium on a real isolated server: authentication, v1.5 hydration, disabled Swedish policy screen, expanded admin form and no JavaScript errors passed. Browser used only loopback requests; its server was stopped. This is a smoke test, not full positive autonomous-mission browser acceptance.
- No actual paid search, real LLM request, customer contact, payment, purchase, production mutation, merge or deploy occurred.

## Remaining before declaring v1.5 complete

1. **Next concrete step:** add one full HTTP/worker end-to-end regression using mocked Brave/page retrieval and the actual AI HTTP adapter with MockTransport. Run CEO → research continuations → Sales → Delivery → Finance → Compliance → CEO summary; assert exact shared pack/usage, retained identities, separate search/AI costs and zero fabricated leads/revenue. The isolated research-loop test already passes, but the complete new graph has not yet been verified in a single test.
2. Add dedicated PostgreSQL autonomous-search races, calendar-month rollover, stopped/in-flight work/restart and current-policy revocation tests. Existing general PostgreSQL locking/recovery tests pass; they are not a substitute for every new research scenario.
3. Complete positive mission/provenance/activation and viewer/mobile browser acceptance, including gaps and rejected sources. Current browser smoke does not cover all these cases.
4. Review the new policy/evidence/worker integration independently, particularly downstream factual-vs-hypothesis handling and publisher independence. Mechanical literal matching cannot prove factual truth or copyright/terms compliance.
5. Review final dependency audit and production Docker CI. Do not activate production research until the remaining tests and review pass.

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
