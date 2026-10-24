# Jarvis Lite v1.5 — Autonomous Web Research (implementation brief)

## Goal
Allow the Research agent to autonomously discover, fetch, evaluate and register publicly accessible sources under an administrator-approved *policy*, without requiring approval of each individual URL. Preserve the current explicit-approval workflow for sources outside that policy. Do not expand permissions for outreach, payments, or arbitrary network access.

## Starting point
Base branch: feat/jarvis-lite-v1. The existing v1.4 source gating and mission execution, cost controls, audit trails, retry rules and Swedish UI must remain functional. Prior mission failed at Sales with business_opportunity_output_invalid / unsupported_or_unbounded_opportunity because its only approved SCB document described open data access, not actual market demand. Never paper over this policy failure.

## Deliverables
1. Pluggable search provider abstraction and server-side search client (select Brave Search or Tavily after reviewing API terms, pricing, and implementation fit). Key supplied only via Render environment; never expose in browser, code, logs, or sample configs. Mock provider for tests and demo.
2. Safe public-web retrieval for HTML and permitted public PDFs with strict HTTPS, DNS and redirect validation; block localhost, loopback, RFC1918, link-local, metadata endpoints, internal hostnames, non-HTTP schemes, and DNS rebinding. Set per-request timeout, byte limit, content-type allowlist, concurrency cap, and bounded redirects. Honor provider/site terms and robots restrictions. No login bypass, CAPTCHA circumvention, or unrestricted crawling.
3. Admin-configured autonomous source approval *policy*, separate from individual manual approvals. Start disabled by default. Policy includes allowed domains/categories, prohibited domains, allowed content types, source evaluation thresholds, max search queries/pages per mission, per-domain rate limits, and cost ceilings. Only admin can change policy. Agent-generated text or webpages must never modify policy or tools.
4. Source evaluator that extracts claim-level evidence with canonical URL, page title, publisher, access timestamp, publication date when available, exact short supporting snippet, content hash, retrieval status, and confidence/reliability rationale. Distinguish source authenticity from support for a specific claim. Do not treat an official source as proof of unrelated assertions. Important numerical/commercial claims require independent corroboration when possible; otherwise label UNVERIFIED/HYPOTHESIS. No fabricated citations.
5. Research planning loop: generate search queries from mission goal, discover sources, fetch/evaluate them, expand searches if evidence is insufficient, then stop on budgets/deadline/limits. Return structured evidence pack to Sales/Finance/Compliance. If evidence is still insufficient, block unsupported conclusions and provide an actionable evidence-gap report; never silently downgrade validation.
6. Evidence contract updates for downstream agents: every claim linked to source IDs/snippets, assumptions explicitly marked, sales proposals framed as hypotheses until validated, no invented leads or revenue figures. Keep policy blockers for unsupported_or_unbounded_opportunity.
7. Swedish UI in Research & Leads and mission details: toggle and review policy, source discovery ledger, provenance and source status, search/API spend, blocked domains and explanations, and evidence-gap reasons. Separate provider spending from LLM spend. Preserve current manually approved source catalog.
8. Hard search API spending ceiling of 100 SEK per calendar month (admin configurable downward), plus per-mission and per-day ceilings. Fail closed when currency conversion or provider pricing cannot be verified. Account for concurrency atomically and record estimated vs billed cost. Do not silently charge a subscription or enable paid provider without explicit credential/configuration and admin approval.
9. Keep all external customer contact, live payments, purchasing, automated outreach, and unauthorized network research disabled. Public-web research only, when policy explicitly enabled.
10. Migration and deployment notes for Render/PostgreSQL, safe rollout flags, and rollback instructions.

## Tests and acceptance
- Unit and integration tests for policy eligibility, approved-source fallback, claim-level citation validity, source spoofing, conflicting evidence, stale content, unsupported sales claims, cost accounting, provider outage, budget exhaustion, timeouts, SSRF including redirect/DNS rebinding, prompt injection in webpage text, duplicate/concurrent missions, and data retention.
- Browser acceptance checks for Swedish admin policy UI and mission provenance.
- Demonstrate with a mocked search provider that the same Swedish SME AI-automation mission can identify evidence gaps without fabricating market data, and can produce a traceable evidence pack with legitimate fixture sources.
- No live paid search calls until admin configures a provider and explicitly authorizes spending.
- Run tests, report failures honestly, document changed files and required env vars/migrations. Open a draft PR targeting feat/jarvis-lite-v1; do not merge or deploy.

## Implementation quality
Use the repository's existing framework, database models, auth, logging and test conventions. Inspect architecture before coding. Implement end-to-end rather than only adding a planning document. Maintain backward compatibility.