# Render deployment plan and budget

This is a reviewable deployment plan, not an executed deployment. No Render resources have been created, no billable services have been started, and no external commerce, advertising, email, payment or trading actions were performed. Applying `render.yaml` creates paid resources: review the cost and remaining validation below before doing so.

## Topology

All resources use Frankfurt in one Render workspace. Only the Next.js frontend and Go backend have public HTTPS endpoints. AI Engine, MCP, RabbitMQ, MongoDB Atlas Local and Neo4j are private services. Postgres and the Redis-compatible Render Key Value instance use internal connections and an empty external IP allowlist. Render provides the internal network; the Blueprint does not expose database, broker, MCP or AI ports publicly.

| Component | Render resource | Selected plan | Persistence |
| --- | --- | --- | --- |
| Frontend | Web service, app Dockerfile | `standard`, 1 instance | Stateless; NextAuth signing key retained as a generated variable |
| Backend | Web service, app Dockerfile | `starter`, 1 instance | Managed Postgres; stable encryption/JWT keys |
| AI Engine | Private service, app Dockerfile | `pro`, 1 instance | MongoDB memories/checkpoints; Neo4j graph data |
| MCP | Private service, app Dockerfile | `standard`, 1 instance | Integration credentials remain in the backend |
| RabbitMQ | Private image service | `standard`, 1 instance | 10 GB at `/var/lib/rabbitmq`; stable node name and Erlang cookie |
| MongoDB Atlas Local 8 | Private image service | `pro`, 1 instance | 20 GB at `/data`, covering both `db` and `configdb` |
| Neo4j 5 Community | Private Docker service | `pro`, 1 instance | 10 GB at `/data` |
| Redis-compatible cache | Managed Key Value | `starter` | Disposable cache, `allkeys-lru`; application truth stays in Postgres |
| PostgreSQL 16 | Managed Postgres | `basic-1gb` | 20 GB managed database storage |

The chosen legacy plan identifiers remain supported according to Render's official public Blueprint reference. Their **current** price, capacity and availability still need checking in the Dashboard before provisioning. AI Engine, Mongo vector search and Neo4j receive larger initial allocations; these are starting assumptions, not load-test results. There is no autoscaling or high availability. Services with disks are intentionally single-instance. `autoDeployTrigger: off` and disabled preview environments prevent subsequent Git pushes from automatically creating deployments, but do **not** make initial Blueprint creation free or non-deploying.

## Required inputs

New Blueprint creation prompts for these four inputs. Enter them securely in Render; do not commit keys or place values in a PR:

| Service | Variable | Input |
| --- | --- | --- |
| Backend | `FRONTEND_URL` | Exact canonical public HTTPS frontend origin; no trailing slash |
| Backend | `PUBLIC_API_URL` | Exact public HTTPS backend origin; no trailing slash |
| Backend | `ENCRYPTION_KEY` | Standard base64 encoding of exactly 32 random bytes |
| AI Engine | `PASS_ENCRYPTION_FERNET_KEY` | A valid Fernet key, URL-safe base64 encoding of 32 random bytes |

Generate the two keys on a trusted machine and paste their outputs directly into the appropriate secret inputs:

```bash
python -c 'import base64,secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())'
python -c 'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())'
```

Keep encrypted-data keys stable and back them up securely. Rotating them without a data re-encryption procedure makes existing encrypted credentials unreadable. Render's generic `generateValue` is deliberately **not** used for these two format-sensitive keys. A generated JWT secret is shared through one environment group across backend, AI Engine and MCP. NextAuth, MongoDB, RabbitMQ and Neo4j have separate platform-generated secrets.

The frontend's `NEXT_PUBLIC_API_URL` is obtained from the backend's `PUBLIC_API_URL` during the Docker build; it is baked into browser JavaScript. A public API-origin change therefore requires a frontend rebuild. Server-side frontend calls use the backend's private hostname. The canonical `FRONTEND_URL` also feeds NextAuth and backend CORS. No guessed `onrender.com` hostname is hardcoded. Select actual service names/custom domains and confirm the public origins before the frontend build; restart/rebuild after corrections.

Private hostnames and database credentials use `fromService` / `fromDatabase` references. Small shell command overrides add URI schemes and the application's fixed ports at runtime because Blueprint `value` strings do not interpolate other environment variables. The generated broker/database passwords must remain URL-safe if rotated; encode arbitrary replacement passwords before placing them into connection URLs. Reference values refresh on Blueprint sync, not automatically when another service's key rotates.

## External actions remain unconfigured

The Blueprint sets `BACKEND_AUTOMATION_ENABLED=false` and leaves OpenRouter, Brevo, Cloudinary, Google OAuth, Shopify, Apify and n8n credentials empty. No n8n service or live integration smoke runner is deployed. These settings prepare a foundation for Shopify, business analysis and AI agents without authorizing writes to any external system. There are no crypto or trading components.

An empty OpenRouter key means real inference is unavailable. Email delivery, user email verification, uploads and OAuth integrations also need their own reviewed credentials and configuration. Set the same OpenRouter key on backend and AI Engine only when intentionally enabling paid inference. Shopify/marketing credentials are organization-scoped application data and must not be populated automatically by deployment. A configuration flag or empty platform key is not a general approval mechanism: require separate review and explicit authorization before enabling any external write, payment, advertising campaign or customer communication.

## Cost estimate before any deployment

**Planning estimate prepared 2026-10-23: about USD 382/month at the provisional rates below; budget USD 380–500/month before usage and taxes. This is not a current Render quote.** Direct access to `render.com` was blocked by this environment's egress proxy with CONNECT HTTP 403, including the pricing, compute-plan and schema pages. The current official public GitHub reference confirms the Blueprint fields and legacy plan identifiers, but does not publish current dollar prices. The following familiar rates are explicit budgeting assumptions and must be replaced with a Dashboard quote before any spend is authorized.

| Resource | Assumed monthly USD |
| --- | ---: |
| Frontend `standard` | 25 |
| Backend `starter` | 7 |
| AI Engine `pro` | 85 |
| MCP `standard` | 25 |
| RabbitMQ `standard` | 25 |
| MongoDB `pro` | 85 |
| Neo4j `pro` | 85 |
| Key Value `starter` | 10 |
| Postgres `basic-1gb` compute | 19 |
| **Assumed compute subtotal** | **366** |
| 40 GB attached disks, assumed USD 0.25/GB/month | 10 |
| 20 GB Postgres storage, assumed USD 0.30/GB/month | 6 |
| **Provisional total** | **382** |

Excluded: workspace/user subscriptions, outbound bandwidth over included allowances, build usage over allowances, backup exports, domains, external storage, managed Atlas/Aura replacements, LLM tokens and all commerce/marketing providers. Billing depends on actual running time, allocations and Render's current rates; a fixed instance count is not a hard spending cap. Do not silently substitute free plans: free services cannot supply this topology's persistence, networking and availability guarantees. A smaller non-production topology can cost less, but must first be measured and selected deliberately.

Official sources to recheck before deployment:

* [Render pricing](https://render.com/pricing)
* [Compute plans](https://render.com/docs/compute-plans)
* [Persistent disks](https://render.com/docs/disks)
* [Postgres storage](https://render.com/docs/postgresql-storage)
* [Blueprint specification and schema](https://render.com/docs/blueprint-spec)
* [Private networking](https://render.com/docs/private-network)
* [Official Blueprint field reference](https://github.com/render-oss/skills/blob/8675173fe30232384d51c8763d1eea4db0350419/skills/render-blueprints/references/field-reference.md) and [wiring patterns](https://github.com/render-oss/skills/blob/8675173fe30232384d51c8763d1eea4db0350419/skills/render-blueprints/references/wiring-patterns.md), retrieved from GitHub on 2026-10-23

## Validation and deployment gates

Run the local, non-deploying wiring checks from the repository root:

```bash
apps/ai-engine/.venv/bin/python deploy/render/validate_blueprint.py
```

They parse the YAML, resolve all local service/database/group references, inspect Docker contexts and shell syntax, enforce private datastores and verify that external credentials are not embedded. This is **not** provider schema validation and does not prove a Render deployment works. The official JSON schema at `https://render.com/schema/render.yaml.json` and Render's non-deploying Blueprint validator must still be run when access is available. The CLI validator requires an authenticated workspace and performs a validation API request; it is not an offline parser. No Blueprint API request was made and no remote resources were created.

Two important wiring assumptions remain for that provider validation: Render must resolve the mutually referenced backend/AI/MCP hostnames, and must provide the frontend's `NEXT_PUBLIC_API_URL` environment value as its declared Docker build argument. The fetched official references describe the individual service reference fields, but do not independently prove this complete deployment graph or its build-time evaluation. Inspect the final frontend bundle and browser requests to ensure that the configured HTTPS API origin was baked in, rather than the Dockerfile's local-development default. Private services accepting multiple ports and generated values in environment groups are described by the fetched official references.

Before an authorized staging deployment:

1. Review the final migration tests and take a recoverable backup before applying migrations to existing data. Do not copy local development volumes or keys into production. Verify encryption keys are securely retained.
2. Verify the current provider schema, plan availability and itemized quote. Review all paid-resource creation explicitly. Ensure all resources use the same workspace and region; this does not imply per-service isolation from every other service in that workspace.
3. Set the canonical origins and four required inputs. Validate generated secrets and service references without printing values. Inspect MongoDB Atlas Local authentication, vector-search compatibility and persistence of both `/data/db` and `/data/configdb` across a restart on Render's runtime. The local Atlas image has not been validated on Render and is a single-node development-oriented choice; consider managed Atlas for customer data, with a separately quoted and reviewed change.
4. Start datastores, confirm readiness, then backend/AI Engine/MCP/frontend. Render does not provide Compose `depends_on` health ordering here. If a service starts before its datastore is ready, wait for the datastore and retry only that service. Confirm MCP dynamic workflow registration after AI Engine is healthy; a first-start connection failure may require an MCP restart.
5. From the private network, verify backend `/health`, AI Engine `/health`, MCP `/health`, authenticated MCP tool listing, queue connectivity and an end-to-end local agent request with inference and external tools mocked. Verify frontend sign-in rendering, NextAuth provider/session routes and its backend connection. Real provider calls require separate authorization and a budget.
6. Add application-consistent backup/export and restoration procedures for MongoDB, Neo4j, RabbitMQ and encryption keys. Attached disks protect files across deploys but do not provide replication or a tested restore procedure. A disk-backed service can incur deployment/resize downtime. Protect database backups as sensitive data. Generated initial passwords for persisted MongoDB, RabbitMQ and Neo4j data are bootstrap inputs: changing only a Render variable does not safely rotate the database's stored account password. Use each database's supported rotation procedure and resync consumers together.

The Neo4j helper preserves the upstream `tini` init process and entrypoint while converting a generated password into the required `NEO4J_AUTH=neo4j/password` format. Root application images are shared with Compose; no unrelated Render-only source fork is introduced. MongoDB/Neo4j/queue data uses disks; application containers remain stateless. Cloudinary uploads are intentionally unavailable until a separate storage configuration is reviewed.
