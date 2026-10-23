# Local stack validation

The source changes were checked on 2026-10-23 using native frontend, backend,
AI Engine and MCP processes against the five Compose infrastructure services.
No Render resources were created. No LLM, Shopify, advertising, payment,
customer email or other external business operation was invoked.

| Component | Observed result |
| --- | --- |
| PostgreSQL 16 | Healthy; backend migrations and database-backed login work |
| Redis 7 | `PING` returns `PONG` |
| RabbitMQ 3.13 | Healthy; both AI consumers have an open channel and consume |
| MongoDB Atlas Local 8 | Healthy; AI Beanie, checkpoint and memory-store initialization works |
| Neo4j 5 | HTTP service is healthy; Graphiti initialization remains disabled in application code |
| Backend | `/health`, failed and successful synthetic login, and AI tool proxy work |
| Frontend | Sign-in form, CSRF, credentials callback and authenticated NextAuth session work |
| AI Engine | Health, authenticated workflow-template reads and actual MCP tool discovery work |
| MCP | Health, workflow-template registration against AI Engine, protocol initialization and tool discovery work |

AI inference, vector retrieval with real embeddings, Graphiti knowledge-graph
memory and live third-party integrations have **not** been validated. Their keys
were left unset. Discovering tool schemas does not prove the corresponding
external integration works.

## Repeat the communication checks

Follow the native development instructions in `CONTRIBUTING.md`; share the same
JWT secret across backend, AI Engine and MCP. Start the databases from the
repository root:

```bash
docker compose up -d --wait postgres redis rabbitmq mongo neo4j
```

Start backend from `apps/backend` so its relative Goose migrations path resolves.
Start AI Engine before MCP so the latter can register available workflow tools.
Use the repository's pinned tools and frozen dependency installs. The validated
toolchains were Go 1.26.10, Node 24.19.0/npm 11.9.0 and Python 3.12.14.

From the repository root, with all four application services running:

```bash
python scripts/smoke-stack.py
```

The script accepts loopback service URLs only and does not execute any MCP tool.
It checks the actual backend → AI Engine → MCP HTTP path as well as each service.
By default it explicitly skips successful frontend → backend login.

For a stronger login check on an **empty local development users table**, activate
the Go toolchain and run:

```bash
python scripts/smoke-stack.py --with-local-account
```

This opt-in mode requires a local Docker Unix socket and the Compose PostgreSQL
service. It creates one synthetic verified `example.com` account directly in the
database, uses a random in-memory password and the backend's bcrypt implementation,
then checks real backend login and the complete NextAuth credentials/session flow.
The exact generated user is deleted in a `finally` block; ordinary login audit
records remain. It refuses to run when any users already exist and never calls
registration or email-verification endpoints. It must not be used on production.

During verification, external HTTP(S) proxies for backend, AI Engine, MCP and
backend tests pointed to the closed local endpoint `http://127.0.0.1:9`, with
`NO_PROXY=localhost,127.0.0.1`. This provided an additional barrier against accidental
external HTTP calls. No provider API credentials were configured.

## Container verification limits

The app Dockerfiles now use the matching Go 1.26 and Node 24 toolchains. The backend
runs as its non-root user under `/app`, where it can access its executable and
migrations. Python images install frozen dependencies at build time and invoke the
virtual environment directly at runtime.

Fresh builds of all four application images have not been completed in this
workspace. Atlas Local and Neo4j images use substantial space under the provided
Docker `vfs` storage driver; about 8 GB remained on the 32 GB workspace after their
startup. Native app validation reuses the already installed dependencies without
exhausting that disk. A clean image build and Render staging validation remain
separate checks before production deployment.
