"""One web process: dashboard, authenticated REST API and durable queue worker."""
from contextlib import asynccontextmanager
import hashlib
import os
from pathlib import Path
import threading
from typing import Literal
from urllib.parse import urlparse
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from . import ai, auth, budget, connectors, jobs, policy, workflow, upgrade, research, outreach, pipeline
from .policy import PolicyError
from .store import Database


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


class Login(Input):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class Key(Input):
    idempotency_key: str = Field(min_length=1, max_length=100)


class Lead(Key):
    company: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=254)
    source: str = "manual"
    source_url: str = Field(default="", max_length=2000)
    is_test: bool = True
    domain: str | None = None
    entity_type: Literal["legal_person", "sole_trader", "unknown"] = "unknown"
    address_type: Literal["generic", "personal", "unknown"] = "unknown"


class Stop(Input):
    enabled: bool
    reason: str = Field(min_length=1, max_length=500)


class Data(Key):
    data: dict = Field(default_factory=dict)


class Reason(Input):
    reason: str = Field(default="Operator review", min_length=1, max_length=500)


class Event(Input):
    event_id: str = Field(min_length=1, max_length=100)


class User(Input):
    email: str
    password: str = Field(min_length=12, max_length=256)
    role: Literal["admin", "operator", "viewer"] = "operator"


class Secret(Input):
    value: str = Field(min_length=1, max_length=8192)


class FX(Input):
    currency: str
    sek_per_unit: str
    source: str = Field(min_length=1, max_length=300)
    observed_at: float


class Cost(Key):
    vendor: str
    workflow_id: str
    actual_minor: int = Field(ge=0)
    currency: str = "SEK"
    category: Literal["infrastructure", "tools", "ai"] = "infrastructure"
    source: str


class AgentTask(Key):
    role: Literal["ceo", "lead_research", "sales", "delivery", "finance", "compliance"]
    text: str = Field(min_length=1, max_length=12000)
    workflow_id: str = Field(min_length=1, max_length=100)


class LiveApproval(Input):
    statement: Literal["I APPROVE LIVE BUSINESS ACTIONS"]
    legal_review_reference: str = Field(min_length=12, max_length=500)
    reason: str = Field(min_length=12, max_length=500)


class EmailConfig(Input):
    sender: str = Field(min_length=3, max_length=254)
    approved: bool = False
    spf: bool = False
    dkim: bool = False
    dmarc: bool = False
    legal_review: bool = False
    review_reference: str = Field(min_length=12, max_length=500)
    approved_templates: list[str] = Field(default_factory=list, max_length=50)
    reservation_minor: int = Field(default=50, ge=1, le=10000)


class Settlement(Input):
    actual_minor: int = Field(ge=0)
    currency: str | None = None
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    calls: int = Field(default=1, ge=0)
    evidence: str = Field(min_length=12, max_length=500)


class Reconcile(Input):
    outcome: Literal["completed", "not_executed"]
    reason: str = Field(min_length=12, max_length=500)


def create_app(database=None, *, worker_enabled=None):
    production = os.getenv("JARVIS_ENV") == "production"
    database_url = os.getenv("JARVIS_DATABASE_URL", "sqlite:///.local/jarvis.db")
    if production:
        if len(os.getenv("JARVIS_SESSION_SECRET", "")) < 32 or not os.getenv("JARVIS_ENCRYPTION_KEY"):
            raise RuntimeError("Production requires stable session and encryption secrets")
        if not database and not database_url.startswith(("postgres://", "postgresql")):
            raise RuntimeError("Production requires persistent PostgreSQL")
    elif not database and database_url.startswith("sqlite"):
        Path(".local").mkdir(exist_ok=True)
    db = database or Database(database_url)
    stop = threading.Event()
    worker = None

    @asynccontextmanager
    async def lifespan(app):
        nonlocal worker
        with db.transaction() as tx:
            policy.initialize(tx)
            email, password = os.getenv("JARVIS_BOOTSTRAP_EMAIL"), os.getenv("JARVIS_BOOTSTRAP_PASSWORD")
            if email and password and not tx.list("user"):
                auth.create_user(tx, email, password, "admin", bootstrap=True)
        enabled = worker_enabled if worker_enabled is not None else os.getenv("JARVIS_WORKER_ENABLED", "true").lower() == "true"
        if enabled:
            stop.clear()
            worker = threading.Thread(target=jobs.worker_loop, args=(db, stop), daemon=True, name="jarvis-durable-worker")
            worker.start()
        yield
        stop.set()
        if worker:
            worker.join(timeout=5)
        if database is None:
            db.close()

    app = FastAPI(title="Jarvis Lite · powered by Ignitic", version="1.2.0", lifespan=lifespan,
                  docs_url=None if production else "/docs")
    app.state.db = db

    @app.middleware("http")
    async def security(request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            try:
                size = int(request.headers.get("Content-Length", "0"))
            except ValueError:
                return JSONResponse({"error": "invalid_length"}, status_code=400)
            if size > 65536 or request.headers.get("Transfer-Encoding"):
                return JSONResponse({"error": "body_too_large"}, status_code=413)
            origin = request.headers.get("Origin")
            public_url = os.getenv("JARVIS_PUBLIC_URL") or os.getenv("RENDER_EXTERNAL_URL") or str(request.base_url)
            if origin and (urlparse(origin).scheme, urlparse(origin).netloc) != (urlparse(public_url).scheme, urlparse(public_url).netloc):
                return JSONResponse({"error": "origin_rejected"}, status_code=403)
        response = await call_next(request)
        response.headers.update({
            "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer", "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        })
        if production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    @app.exception_handler(PolicyError)
    async def rejected(request, error):
        with db.transaction() as tx:
            tx.audit("request", "policy.rejected", {"code": error.code, "path": request.url.path})
        return JSONResponse({"error": error.code, "message": error.message}, status_code=error.status)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, error):
        return JSONResponse({"error": "invalid_input", "message": "Check the request fields",
                             "fields": [str(item["loc"][-1]) for item in error.errors()]}, status_code=422)

    def who(tx, request, write=False, admin=False):
        return auth.current_actor(tx, request, write=write,
            roles={"admin"} if admin else {"admin", "operator"} if write else None)[0]

    @app.get("/health")
    def health():
        with db.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "healthy", "service": "jarvis-lite", "version": "1.2.0"}

    @app.post("/api/auth/login")
    def login(body: Login, request: Request):
        with db.transaction() as tx:
            result = auth.login(tx, body.email, body.password, request.client.host if request.client else "unknown")
        if "error" in result:
            return JSONResponse({"error": result["error"], "message": "Sign-in failed"}, status_code=result["status"])
        token = result.pop("session_token")
        response = JSONResponse(result)
        response.set_cookie("jarvis_session", token, httponly=True, secure=production, samesite="strict", max_age=28800)
        return response

    @app.get("/api/auth/me")
    def me(request: Request):
        with db.transaction() as tx:
            actor, session = auth.current_actor(tx, request)
            return {"user": actor, "csrf_token": session["csrf"]}

    @app.post("/api/auth/logout")
    def logout(request: Request):
        with db.transaction() as tx:
            auth.current_actor(tx, request, write=True)
            tx.delete("session", hashlib.sha256(request.cookies["jarvis_session"].encode()).hexdigest())
        response = JSONResponse({"ok": True})
        response.delete_cookie("jarvis_session")
        return response

    @app.get("/api/dashboard")
    def dashboard(request: Request):
        with db.transaction() as tx:
            user = who(tx, request)
            settings = policy.get_settings(tx)
            leads, payments = tx.list("lead"), tx.list("payment")
            proposals = tx.list("proposal")
            # Never relabel simulated receipts as real revenue after a mode change.
            financial_payments = ([p for p in payments if p.get("provider") == "demo"]
                                  if settings["mode"] == "DEMO"
                                  else [p for p in payments if p.get("livemode") is True])
            confirmed = [p for p in financial_payments if p["status"] == "CONFIRMED"]
            metrics = {"leads": len(leads), "qualified": sum(bool(lead.get("qualification", {}).get("qualified")) for lead in leads),
                "contacts": sum(m.get("purpose") == "contact" and m.get("status") in {"SENT", "DELIVERED"} for m in tx.list("email_message")),
                "replies": sum(e.get("type") == "reply" for e in tx.list("inbound_event")), "proposals": len(proposals),
                "proposals_sent": sum(p["status"] in {"SENT", "ACCEPTED"} for p in proposals),
                "accepted": sum(p["status"] == "ACCEPTED" for p in proposals), "paying_customers": len({p["lead_id"] for p in confirmed}),
                "confirmed_revenue_minor": sum(p["amount_minor"] for p in confirmed),
                "simulated_revenue_minor": sum(p["amount_minor"] for p in payments if p["status"] == "CONFIRMED" and p.get("provider") == "demo"),
                "outstanding_minor": sum(p["total_minor"] for p in financial_payments if p["status"] in {"REQUESTED", "INITIATED"})}
            financial = budget.summary(tx)
            metrics["gross_margin_minor"] = metrics["confirmed_revenue_minor"] - financial["actual_sek_minor"]
            separated = upgrade.metrics(tx)
            metrics.update(confirmed_revenue_minor=separated["real_confirmed_revenue_minor"],
                           paying_customers=separated["real_paying_customers"],
                           outstanding_minor=separated["real_outstanding_minor"])
            metrics["gross_margin_minor"] = metrics["confirmed_revenue_minor"] - financial["actual_sek_minor"]
            return {"user": user, "mode": settings["mode"], "kill_switch": settings["kill_switch"],
                "policy": settings, "metrics": metrics, "budget": financial,
                "leads": leads, "jobs": tx.list("job"), "payments": payments,
                "approvals": [p for p in payments if p["status"] in {"DRAFT", "APPROVED"}],
                "audit": tx.audits(), "services": workflow.service_registry(tx),
                "tasks": upgrade.hydrate_tasks(tx), "reports": tx.list("report"),
                "v11": {"ai": ai.status(tx), "research_sources": research.sources(tx),
                        "outreach": outreach.status(tx), "pipeline": pipeline.status(tx), "metrics": separated},
                "connections": {name: bool(tx.get("secret", name)) for name in
                    ("BREVO_API_KEY", "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "OPENROUTER_API_KEY")},
                "messages": tx.list("email_message"), "deliveries": tx.list("delivery")}

    @app.post("/api/demo/run")
    def demo(body: Key, request: Request):
        with db.transaction() as tx:
            actor = who(tx, request, True)
            if policy.get_settings(tx)["mode"] != "DEMO":
                raise PolicyError("demo_only", "Only simulated journeys may use this endpoint", 403)
            return jobs.enqueue(tx, "demo_journey", idempotency_key=body.idempotency_key, actor=actor)

    @app.post("/api/jobs/run")
    def run_job(request: Request):
        with db.transaction() as tx:
            who(tx, request, True)
        result = jobs.run_once(db)
        return {"job": result, "queue_empty": result is None,
                "message": "Jobbet har bearbetats" if result else "Jobbkön är tom. Skapa ett internt uppdrag för att börja."}

    @app.post("/api/kill-switch")
    def kill(body: Stop, request: Request):
        with db.transaction() as tx:
            return policy.set_kill_switch(tx, body.enabled, body.reason, who(tx, request, True, True))

    @app.post("/api/jobs/{job_id}/reconcile")
    def reconcile_job(job_id: str, body: Reconcile, request: Request):
        with db.transaction() as tx:
            return jobs.reconcile(tx, job_id, **body.model_dump(), actor=who(tx, request, True, True))

    @app.post("/api/leads")
    def new_lead(body: Lead, request: Request):
        with db.transaction() as tx:
            data = body.model_dump(exclude_none=True)
            data["source_id"] = data.pop("source")
            data.pop("idempotency_key")
            return workflow.register_lead(tx, data, actor=who(tx, request, True), key=body.idempotency_key)

    @app.post("/api/leads/{lead_id}/unsubscribe")
    def unsubscribe(lead_id: str, body: Reason, request: Request):
        with db.transaction() as tx:
            actor = who(tx, request, True)
            lead = tx.get("lead", lead_id)
            if not lead:
                raise PolicyError("not_found", "Lead not found", 404)
            return connectors.unsubscribe(tx, email=lead["email"], reason=body.reason, actor=actor)

    @app.post("/api/payments/{payment_id}/approve")
    def approve(payment_id: str, request: Request):
        with db.transaction() as tx:
            return connectors.approve_payment(tx, payment_id, actor=who(tx, request, True, True))

    @app.post("/api/leads/{lead_id}/send-contact")
    def send_contact(lead_id: str, body: Key, request: Request):
        with db.transaction() as tx:
            actor = who(tx, request, True)
            lead = workflow.prepare_contact(tx, lead_id, actor=actor, key=body.idempotency_key + ":draft")
            draft = lead["contact_draft"]
            return connectors.send_email(tx, lead_id=lead_id, subject=draft["subject"], body=draft["body"],
                idempotency_key=body.idempotency_key, actor=actor)

    @app.post("/api/leads/{lead_id}/send-proposal")
    def send_proposal(lead_id: str, body: Key, request: Request):
        with db.transaction() as tx:
            actor = who(tx, request, True)
            draft = workflow.proposal_message(tx, lead_id)
            return connectors.send_email(tx, lead_id=lead_id, **draft, purpose="proposal",
                idempotency_key=body.idempotency_key, actor=actor)

    @app.post("/api/leads/{lead_id}/payment-draft")
    def payment_draft(lead_id: str, body: Key, request: Request):
        with db.transaction() as tx:
            actor = who(tx, request, True)
            current = tx.get("lead", lead_id, {})
            gate = workflow.prepare_payment_gate if current.get("state") == "ACCEPTED" else workflow.payment_approval_required
            lead = gate(tx, lead_id, actor=actor, key=body.idempotency_key + ":gate")
            proposal = tx.get("proposal", lead["proposal_id"])
            return connectors.create_payment_draft(tx, lead_id=lead_id, proposal_id=proposal["id"],
                amount_minor=proposal["amount_minor"], vat_minor=proposal["vat_minor"],
                service_id=proposal["service_id"], reference="PROPOSAL-" + proposal["id"],
                idempotency_key=body.idempotency_key, actor=actor)

    @app.post("/api/enquiries")
    def enquiry(body: Data, request: Request):
        with db.transaction() as tx:
            return workflow.process_enquiry(tx, body.data, actor=who(tx, request, True), key=body.idempotency_key)

    @app.post("/api/agents/tasks")
    def agent_task(body: AgentTask, request: Request):
        with db.transaction() as tx:
            return ai.task(tx, **body.model_dump(), actor=who(tx, request, True))

    @app.post("/api/payments/{payment_id}/request")
    def request_payment(payment_id: str, body: Key, request: Request):
        with db.transaction() as tx:
            return connectors.request_payment(tx, payment_id, idempotency_key=body.idempotency_key, actor=who(tx, request, True))

    @app.post("/api/payments/{payment_id}/simulate-paid")
    def simulate_paid(payment_id: str, body: Event, request: Request):
        with db.transaction() as tx:
            return connectors.simulate_payment_event(tx, payment_id, event_id=body.event_id, actor=who(tx, request, True, True))

    @app.post("/api/webhooks/payments/{provider}")
    async def payment_webhook(provider: Literal["stripe", "demo"], request: Request):
        raw = await request.body()
        with db.transaction() as tx:
            return connectors.receive_payment_event(tx, provider=provider, raw_body=raw,
                signature=request.headers.get("Stripe-Signature", ""))

    @app.post("/api/webhooks/email")
    async def email_webhook(request: Request):
        raw = await request.body()
        import json
        try:
            payload = json.loads(raw)
            timestamp = int(request.headers.get("X-Jarvis-Timestamp", "0"))
        except (ValueError, TypeError):
            raise PolicyError("invalid_webhook", "Invalid signed email receipt", 400) from None
        with db.transaction() as tx:
            return connectors.receive_email(tx, payload=payload, signature=request.headers.get("X-Jarvis-Signature", ""),
                timestamp=timestamp, event_id=request.headers.get("X-Jarvis-Event-Id", ""))

    @app.post("/api/users")
    def new_user(body: User, request: Request):
        with db.transaction() as tx:
            return auth.create_user(tx, body.email, body.password, body.role, actor=who(tx, request, True, True))

    @app.patch("/api/policy")
    def change_policy(body: dict, request: Request):
        with db.transaction() as tx:
            return policy.update_settings(tx, body, who(tx, request, True, True))

    @app.post("/api/secrets/{name}")
    def change_secret(name: str, body: Secret, request: Request):
        with db.transaction() as tx:
            actor = who(tx, request, True, True)
            policy.set_secret(tx, name, body.value, actor)
            policy.update_settings(tx, {}, actor)
            return {"name": name, "configured": True}

    @app.delete("/api/secrets/{name}")
    def delete_secret(name: str, request: Request):
        with db.transaction() as tx:
            actor = who(tx, request, True, True)
            tx.delete("secret", name)
            policy.update_settings(tx, {}, actor)
            tx.audit(actor, "secret_removed", {"name": name})
            return {"name": name, "configured": False}

    @app.post("/api/connectors/email/config")
    def email_config(body: EmailConfig, request: Request):
        with db.transaction() as tx:
            actor = who(tx, request, True, True)
            if "@" not in body.sender or any(c.isspace() for c in body.sender):
                raise PolicyError("sender", "Use a reviewed sender email address", 422)
            if any(len(value) != 64 or any(c not in "0123456789abcdef" for c in value) for value in body.approved_templates):
                raise PolicyError("template", "Template approvals must be SHA-256 fingerprints", 422)
            result = tx.put("connector_config", "email", body.model_dump())
            settings = policy.get_settings(tx)
            settings["approved_senders"] = [body.sender]
            settings["safety_epoch"] += 1
            tx.put("settings", "global", settings)
            jobs.pause_for_kill(tx, actor=actor)
            tx.audit(actor, "email_configuration_reviewed", {"sender": body.sender, "review_reference": body.review_reference})
            return result

    @app.post("/api/budget/fx")
    def fx(body: FX, request: Request):
        with db.transaction() as tx:
            return budget.set_fx_rate(tx, **body.model_dump(), actor=who(tx, request, True, True))

    @app.post("/api/budget/costs")
    def cost(body: Cost, request: Request):
        with db.transaction() as tx:
            return budget.record_cost(tx, **body.model_dump(), actor=who(tx, request, True, True))

    @app.post("/api/budget/reservations/{reservation_id}/settle")
    def settle_cost(reservation_id: str, body: Settlement, request: Request):
        with db.transaction() as tx:
            actor = who(tx, request, True, True)
            fields = body.model_dump(exclude={"evidence"})
            result = budget.settle(tx, reservation_id, **fields, actor=actor)
            tx.audit(actor, "cost.manually_reconciled", {"reservation_id": reservation_id, "evidence": body.evidence})
            return result

    @app.post("/api/live/approve")
    def live(body: LiveApproval, request: Request):
        with db.transaction() as tx:
            actor = who(tx, request, True, True)
            if policy.INTERNAL_ONLY_RELEASE:
                raise PolicyError("internal_only_release", "External business actions cannot be enabled in v1.2", 403)
            if os.getenv("JARVIS_ALLOW_LIVE", "false").lower() != "true":
                raise PolicyError("live_disabled", "Live mode is disabled by the deployment; separate authorization is required", 403)
            settings = policy.get_settings(tx)
            if not settings.get("approved_senders"):
                raise PolicyError("sender_review", "Review sender identity and SPF/DKIM/DMARC evidence first", 403)
            settings.update(mode="LIVE", live_approved=True, legal_reviewed=True,
                legal_review_reference=body.legal_review_reference, live_approved_by=actor["id"], live_approved_at=tx.now)
            settings["safety_epoch"] += 1
            tx.put("settings", "global", settings)
            jobs.pause_for_kill(tx, actor=actor)
            tx.audit(actor, "live.manually_approved", {"reason": body.reason, "legal_review_reference": body.legal_review_reference})
            return {"mode": "LIVE", "customer_contact_enabled": False,
                    "next_step": "Run current outreach checks and obtain separate legal/customer-contact approval"}

    from .api_v11 import register_routes
    register_routes(app, db, who)
    from .api_v12 import register_routes as register_v12
    register_v12(app, db, who)

    @app.post("/api/leads/{lead_id}/{action}")
    def lead_action(lead_id: str, action: Literal["research", "qualify", "compliance", "proposal", "accept", "deliver"], body: Data, request: Request):
        with db.transaction() as tx:
            actor = who(tx, request, True, action == "compliance")
            functions = {
                "research": lambda: workflow.research_lead(tx, lead_id, body.data, actor=actor, key=body.idempotency_key),
                "qualify": lambda: workflow.qualify_lead(tx, lead_id, actor=actor, key=body.idempotency_key),
                "compliance": lambda: workflow.review_compliance(tx, lead_id, body.data, actor=actor, key=body.idempotency_key),
                "proposal": lambda: workflow.prepare_proposal(tx, lead_id, body.data, actor=actor, key=body.idempotency_key),
                "accept": lambda: workflow.accept_proposal(tx, lead_id, body.data, actor=actor, key=body.idempotency_key),
                "deliver": lambda: workflow.deliver_service(tx, lead_id, body.data, actor=actor, key=body.idempotency_key),
            }
            return functions[action]()

    @app.post("/api/jobs/{job_id}/{action}")
    def job_action(job_id: str, action: Literal["pause", "resume"], body: Reason, request: Request):
        with db.transaction() as tx:
            actor = who(tx, request, True, True)
            job = tx.get("job", job_id)
            if action == "resume" and job and job["kind"] == "internal_agent":
                from . import engine
                engine.resume_mission(tx, job["payload"]["mission_id"], actor, body.reason)
                return tx.get("job", job_id)
            return getattr(jobs, action)(tx, job_id, actor=actor, reason=body.reason)

    static = Path(__file__).parent / "static"
    static.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    @app.get("/agents")
    def agent_center():
        return FileResponse(static / "index.html")

    return app
