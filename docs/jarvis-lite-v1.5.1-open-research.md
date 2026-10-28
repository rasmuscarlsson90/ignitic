# Jarvis Lite v1.5.1 — Open Public Web Research

## Behavior and policy review

`domain_access_mode` is `allowlist` (default, including persisted v1.5 policies)
or `open_public_web`. The administrator can select it under **Research & Leads →
Autonom research → Granska domäner, kvalitet och sökbudget**.

- In allowlist mode, `allowed_domains` is still required for discovery and reads.
- In open mode, eligible public HTTPS discoveries and reads do not require an
  entry in that list; an empty list is valid. `prohibited_domains` always wins,
  including subdomains, page redirects and robots redirects.
- Both paths use the same URL/domain validator. This validator does not itself
  authorize networking: the existing runtime flags, administrator activation,
  paid-AI permission, mission assignment/deadline and spending guards still apply.
- Open mode requires `open_public_web_reviewed: true`, a documented review in
  `terms_reference` and the normal administrator review reason. It also requires
  a **separate activation** for the saved revision with the exact statement
  `I APPROVE PAID OPEN PUBLIC WEB RESEARCH`. The previous statement
  `I APPROVE PAID WEB RESEARCH` only authorizes allowlist mode.
- Every policy save disables research, fences queued work and requires activation
  again. A changed policy revision invalidates existing scoped source approval.
  Switching into open mode without explicitly supplying its review flag clears
  any inherited open-mode review flag.
- Mutation routes retain stored administrator authorization and CSRF checks;
  agents and viewer/operator accounts cannot change or activate this setting.

## Reading is not permission to reuse

`terms_reviewed` and `terms_reference` describe the administrator's access policy
and the reviewed reuse scope in **listed domains**, not a review of the entire
internet. Public availability, a successful HTTPS fetch, robots permission,
search-result text, HTML license assertions or an AI response do not establish
copyright or contractual permission.

For an unlisted domain in open mode:

1. Safe discovery and bounded public reading are allowed by the separately
   reviewed access policy.
2. Retrieval records `terms_allowed: false`, `license_reviewed: false` and
   `legal_review_status: UNVERIFIED` rather than inventing legal approval.
3. A source that otherwise passes technical/quality checks is recorded as
   `REVIEW_REQUIRED` with `source_legal_review_required`. Actual safety or quality
   failures still produce `REJECTED`.
4. The source is visible with a Swedish legal-review warning and the pending
   source filter. It does not become an approved `research_source` or reusable
   `research_document`, is excluded from claim extraction and verified evidence,
   and cannot supply verified premises to Sales. Research reports gaps when
   sufficient eligible evidence cannot be obtained within the existing limits.

The existing administrator-reviewed `allowed_domains` list continues to define
where reviewed reuse is permitted, even in open mode. The recorded review is an
administrator attestation for internal research, never an assertion of ownership,
permission to republish, outreach permission or semantic truth. All page redirect
origins must be within that reviewed scope; a redirect cannot launder an unreviewed
origin into an approved final source.

**Limitation:** This release does not automatically interpret arbitrary website
terms or verify licenses. New domains can be discovered and read without manual
registration, but their contents cannot automatically become verified reusable
agent evidence. Where reuse is desired, an administrator must actually review the
applicable terms and update the reviewed scope, save/reapprove the policy, and
start a new mission. Existing missions/evidence are not silently reauthorized.
This is deliberately fail-closed; robots permission alone is not legal clearance.
Operators must review whether the contemplated public reading itself is lawful
before approving open access; the software cannot infer all contractual rules.

## Security and financial boundaries

The pinned HTTPS transport, public-only DNS/IP checks (including mixed DNS and
rebinding protection), URL/credential validation, redirect checks, robots rules,
request/size/content-type/deadline/concurrency/rate limits and untrusted-input
handling remain in force. Login/CAPTCHA/access denials are not bypassed. Explicit
`noai`, `nosnippet`, `none` or zero-snippet restrictions in applicable robots meta
or `X-Robots-Tag`, and `TDM-Reservation: 1` headers/meta, additionally stop evidence
retrieval. These conservative checks are not a complete website-terms interpreter.

The 100 SEK Stockholm-calendar-month search cap, daily and mission search caps,
mission authority, global spending caps, cost reservations, trace/audit logs,
server-only credentials and kill switch remain unchanged. Search and AI costs
remain separately recorded; provider billing is not fabricated. No purchases,
accounts, contact, payment or deployment capabilities are added or activated.

## API and deployment notes (manual review only)

The existing endpoints are retained:

- `GET /api/research/autonomy` now includes the effective defaulted policy and
  `activation_statement` for the current saved mode.
- `POST /api/research/autonomy/policy` accepts `domain_access_mode` and
  `open_public_web_reviewed`, alongside existing fields. Saving never enables it.
- `POST /api/research/autonomy/activation` uses the mode-specific statement and
  current `policy_revision` plus a review reason. `DISABLE WEB RESEARCH` works in
  either mode.

No SQL migration, new package or new environment variable is required. Policies
remain generic JSON records. Existing persisted v1.5 policies default to allowlist
without data rewrites or automatic changes to their authorization. The application
and package report version 1.5.1; the `/api/v1.5/overview` URL remains compatible.

A future operator-managed Render rollout uses the existing Docker image/build
and PostgreSQL settings. Preserve the current secrets and data. The existing
`BRAVE_SEARCH_API_KEY`, `JARVIS_ALLOW_AUTONOMOUS_RESEARCH` and
`JARVIS_ALLOW_RESEARCH_NETWORK` remain necessary, as do current reviewed pricing,
FX, provider terms, AI approval and budget availability. Do not change these as
part of code review. After deployment and legal/access review, an administrator
may save the desired policy and separately activate it. No production settings or
records were changed during this implementation.

No subscription, tariff or spending limit is increased. Additional reads can
use existing hosting/network capacity; API search charges stay under all existing
caps and the operator's documented current tariff. No live paid call is needed
for the acceptance tests.

## Verification

Executed locally on 2026-10-24: Tests use
mocked HTTP/search transports and local databases, with external sockets/DNS
blocked. They do not demonstrate a live-provider run or legal clearance.

Reproduce from `apps/jarvis-lite`:

```sh
uv sync --frozen --all-extras
uv run --frozen pytest -q
uv run --frozen ruff check jarvis_lite tests
uv run --frozen mypy jarvis_lite
uv run --with playwright python tests/browser_v15_acceptance.py
uv run --with playwright python tests/browser_v151_acceptance.py
```

For PostgreSQL coverage, supply `JARVIS_TEST_DATABASE_URL` for a local maintenance
connection with CREATE DATABASE permission; each test owns/drops only its random
temporary database. Without it PostgreSQL tests are explicitly skipped. Browser
scripts need `/usr/bin/chromium`, start only a temporary local fixture server,
block external requests and disable served provider/network capabilities.

Focused coverage is in `test_open_research_v151.py` and
`test_web_retrieval_v15.py`: legacy defaults, both modes, current-revision and
mode-specific approval, stored roles/CSRF, public cross-domain redirects,
prohibited domains, DNS/private/mixed addresses, rebinding, robots denials,
content restrictions, explicit reuse restrictions, concurrency/rate limits,
untrusted license assertions, unverified-source quarantine, all budget layers,
revocation, and HTTP/real-AI-adapter rejection before Sales. Existing v1.4/v1.5
agent-chain and recovery regressions remain in the full suite.

| Check | Executed result |
| --- | --- |
| Full suite, including real isolated PostgreSQL | **876 passed**, 74.01 seconds; one upstream Starlette/httpx deprecation warning |
| Focused open-policy and retrieval regressions | **113 passed** |
| Existing v1.5 browser acceptance | **12 checks passed**, no JS errors or external browser requests |
| New v1.5.1 browser acceptance | **6 checks passed**, including actual policy save and mobile overflow check; no JS errors or external requests |
| Ruff | Passed |
| mypy | Passed, 30 source modules |
| Node JavaScript syntax check | Passed |
| actionlint, both workflows | Passed |
| Frozen runtime dependency audit | No known vulnerabilities found |
| Gitleaks source scan | No leaks found |

Full-suite log: `/tmp/v151-full-tests-final.log`; new browser log:
`/tmp/v151-browser.log`, screenshot: `/tmp/v151-browser-mobile.png` (workspace
artifacts, not committed). A fresh local Docker build was not run; the unchanged
GitHub Actions workflow builds the production image and checks non-root startup
with isolated PostgreSQL when this branch is pushed. No Docker/CI success is
claimed here before that run completes.
