# Jarvis Lite budget and deployment estimate

## Economic policy

All amounts are stored as integer minor units, with SEK as the reporting
currency. These defaults implement the initial mandate; a model cannot change
them:

| Rule | Initial value |
| --- | ---: |
| Initial total, first 30 days | 2,000 SEK |
| Tools, rolling 30 days | 500 SEK |
| Infrastructure monthly target | 600 SEK |
| Daily spending ceiling | 100 SEK |
| Monthly spending ceiling | 2,000 SEK |
| Minimum ordinary setup price, excluding VAT | 5,000 SEK |
| Maximum discount | 10% |
| Absolute discounted setup floor, excluding VAT | 4,500 SEK |

Daily and monthly limits are additional ceilings, not permission to exceed the
total start budget. The 100 SEK daily default is a conservative implementation
choice because the mandate did not specify a daily amount. Administrators can
review policy changes; updates require role checks and audit entries. The
minimum discounted floor never makes a higher ordinary price eligible for a
discount larger than 10%.

The ledger distinguishes reserved, actual and released amounts, vendor,
workflow, category, timestamp, currency, token usage and request count. Pending
reservations count against limits until settled or definitively released. A
provider timeout is not evidence that no charge occurred. Idempotency keys
prevent duplicate reservations/settlement and provider event replay must not
double-count revenue. Threshold notifications are recorded at 50%, 75%, 90%
and 100%; the dashboard shows usage, remaining funds and a forecast rather than
silently promising future income.

Currency conversions require a rate, source and observation timestamp. Rates
used for actual reservations must be deliberately configured and sufficiently
fresh. Do not treat the planning assumption below as an observed market rate.
Record the provider's actual invoiced amount and applicable tax separately when
reconciling it. A real overrun is still recorded even when it exceeds a cap;
new spending is then blocked.

## Minimal Render plan: provisional, not a current quote

Estimate prepared **2026-10-23**. Direct requests to the official pricing and
Blueprint schema pages were retried during this implementation, but the
environment's egress proxy rejected CONNECT with HTTP 403. Current prices,
capacity, region availability and provider-schema compatibility therefore
remain unverified. No paid resources have been created.

| Resource | Assumed monthly USD | Planning SEK at 12 SEK/USD |
| --- | ---: | ---: |
| One Render web service, `starter` | 7 | 84 |
| Private PostgreSQL, `basic-1gb` | 19 | 228 |
| 10 GB PostgreSQL storage at assumed 0.30 USD/GB | 3 | 36 |
| **Estimated baseline** | **29** | **348** |

The **12 SEK/USD** rate is an explicitly conservative planning assumption dated
2026-10-23, not a retrieved FX quote. At an illustrative 10.50 SEK/USD, the same
assumptions total 304.50 SEK. Use an actual documented exchange rate before
approving spend. There is no currency API call or subscription hidden in this
estimate.

If 512 MB starter memory is insufficient after measurement, an assumed
`standard` web plan at 25 USD changes the total to **47 USD / 564 SEK per month**
at the same conservative planning rate. That leaves little room below 600 SEK
for VAT, FX changes and usage. The target is a base-infrastructure target, not a
guarantee of a tax-inclusive invoice or a hard provider spending cap. Do not
silently upgrade a paid plan; obtain approval first.

Baseline excludes workspace subscriptions, taxes, domains, overage bandwidth,
build overages, off-provider backups and every AI/email/payment/provider charge.
It assumes one web instance, no high availability and the worker sharing that
instance. Real inference and other tools must fit inside the separate rolling
tool allowance and total budget, not be added outside the mandate.

If 25% VAT were charged and not immediately recoverable, the illustrative cash
amounts would be **435 SEK baseline** and **705 SEK with the standard web plan**
at 12 SEK/USD. The latter exceeds a 600 SEK cash ceiling and must not be selected
under that interpretation of the mandate. Actual tax/reverse-charge treatment
depends on the buyer and provider invoice; confirm it before approval and record
the amount the business actually owes, including non-recoverable tax.

Official sources to check before an authorized deployment:

* [Render pricing](https://render.com/pricing)
* [Compute plans](https://render.com/docs/compute-plans)
* [PostgreSQL storage](https://render.com/docs/postgresql-storage)
* [Blueprint specification](https://render.com/docs/blueprint-spec)
* [Private networking](https://render.com/docs/private-network)
* [PostgreSQL backups](https://render.com/docs/postgresql-backups)

Applying `render-lite.yaml` provisions billable resources even though automatic
deployments and previews are disabled. The YAML is a reviewed plan, not a free
dry-run or evidence that Render accepted it.

## What the application can and cannot control

The server can reject a new LLM request, email-provider request, paid tool job or
payment-request operation before dispatch when policy or reserved budget fails.
It can pause workers and require explicit approvals. It cannot impose a hard
cap on the provider's independent account billing, stop a cloud subscription
by stopping its process, reverse an already accepted request, or guarantee an
estimate equals a later invoice.

Before live use, configure provider-side spend limits, email quotas, model/token
caps and alerts where available. Reconcile cloud invoices and record fixed
infrastructure costs even when the app is stopped. If a budget is exhausted,
manually suspend/cancel billable resources in the provider console as needed;
stopping or pausing Jarvis alone does not stop billing. Preserve backups before
removing a database. New subscriptions, advertising, refunds, transfers and
payout-account changes require separate human handling and are not autonomous
features of Lite.

## Lower-cost options if the target cannot be met

Keep the one-app/one-database topology, reduce nonessential external tools and
measure memory before resizing. A reputable EU VPS with PostgreSQL on a private
socket/network can cost less, but moves OS patching, TLS renewal, encrypted
off-machine backups, monitoring and recovery onto the operator. A hosted database
free tier is only appropriate if its current persistence, backup and availability
contract meets the workload; do not substitute ephemeral storage or publicly
expose PostgreSQL to make a cost spreadsheet pass.

An unverified cheap plan is not authorization to deploy it. If a current
provider quote plus tax exceeds the mandate, stop before provisioning and submit
the revised quote and operational tradeoffs for approval.
