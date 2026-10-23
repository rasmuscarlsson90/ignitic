"""Authenticated internal work, real AI diagnostics and versioned service results."""
from fastapi import Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from . import ai, engine, factory, intelligence, jobs, operations, policy


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Key(Input):
    idempotency_key: str = Field(min_length=1, max_length=100)


class Mission(Key):
    goal: str = Field(min_length=12, max_length=4000)
    source_ids: list[str] = Field(default_factory=list, max_length=10)
    lead_ids: list[str] = Field(default_factory=list, max_length=20)
    deliverable_kind: str = "market_analysis"
    max_cost_minor: int = Field(default=2000, ge=1, le=50000)
    priority: str = "normal"


class Order(Key):
    brief: str = Field(min_length=12, max_length=4000)
    type: str
    title: str | None = Field(default=None, max_length=200)
    lead_ids: list[str] = Field(default_factory=list, max_length=20)
    source_ids: list[str] = Field(default_factory=list, max_length=10)


class Review(Input):
    decision: str
    reason: str = Field(min_length=12, max_length=500)


class Reason(Input):
    reason: str = Field(min_length=12, max_length=500)


class TestAI(Key):
    text: str = Field(default="Return one short, factual description of your role as an internal business assistant.", min_length=1, max_length=4000)


def overview(tx):
    return {"version": "1.2.0", "business_actions_enabled": False,
            "ai": ai.status(tx), "agents": engine.agent_status(tx),
            "crm": intelligence.list_leads(tx, {}),
            "services": {"orders": factory.list_orders(tx), "results": factory.results(tx), "offers": tx.list("sales_offer")},
            "reports": {"plans": tx.list("business_plan"), "research": tx.list("research_report"),
                "compliance": tx.list("compliance_report"), "finance": tx.list("finance_report"), "daily": tx.list("business_report")},
            "metrics": operations.analytics(tx), "schedules": operations.schedules(tx),
            "jobs": jobs.snapshot(tx), "ai_traces": list(reversed(tx.list("ai_trace")))[:100]}


def register_routes(app, db, who):
    @app.get("/api/v1.2/overview")
    def get_overview(request: Request):
        with db.transaction() as tx:
            who(tx, request)
            return overview(tx)

    @app.post("/api/ai/activation")
    def activate_ai(body: dict, request: Request):
        with db.transaction() as tx:
            return ai.activate(tx, body, who(tx, request, True, True))

    @app.post("/api/ai/test")
    def test_ai(body: TestAI, request: Request):
        with db.transaction() as tx:
            return ai.test_connection(tx, **body.model_dump(), actor=who(tx, request, True, True))

    @app.post("/api/ai/models/refresh")
    def refresh_models(request: Request):
        with db.transaction() as tx:
            return ai.refresh_models(tx, who(tx, request, True, True))

    @app.post("/api/missions")
    def create_mission(body: Mission, request: Request):
        with db.transaction() as tx:
            payload = body.model_dump(exclude={"idempotency_key"})
            if body.priority not in {"high", "normal", "low"}:
                raise policy.PolicyError("priority", "Choose high, normal or low priority", 422)
            payload["priority"] = {"high": 10, "normal": 50, "low": 90}[body.priority]
            return engine.create_mission(tx, payload, actor=who(tx, request, True), key=body.idempotency_key)

    @app.post("/api/missions/{mission_id}/resume")
    def resume_mission(mission_id: str, body: Reason, request: Request):
        with db.transaction() as tx:
            return engine.resume_mission(tx, mission_id, actor=who(tx, request, True, True), reason=body.reason)

    @app.post("/api/missions/{mission_id}/pause")
    def pause_mission(mission_id: str, body: Reason, request: Request):
        with db.transaction() as tx:
            return engine.pause_mission(tx, mission_id, actor=who(tx, request, True, True), reason=body.reason)

    @app.post("/api/agents/{role}/config")
    def configure_role(role: str, body: dict, request: Request):
        with db.transaction() as tx:
            return engine.configure_agent(tx, role, body, actor=who(tx, request, True, True))

    @app.post("/api/agents/actions/{action_id}/review")
    def review_action(action_id: str, body: Review, request: Request):
        with db.transaction() as tx:
            return engine.review_action(tx, action_id, body.decision, body.reason, actor=who(tx, request, True, True))

    @app.post("/api/internal/jobs/{job_id}/cancel")
    def cancel_job(job_id: str, body: Reason, request: Request):
        with db.transaction() as tx:
            return jobs.cancel(tx, job_id, actor=who(tx, request, True, True), reason=body.reason)

    @app.get("/api/crm/leads")
    def crm(request: Request, q: str = Query(default="", max_length=200), industry: str = "",
            geography: str = "", mode: str = "REAL", sort: str = "score"):
        with db.transaction() as tx:
            who(tx, request)
            if sort not in {"score", "company", "newest"}:
                raise policy.PolicyError("crm_filter", "Choose score, company or newest", 422)
            return intelligence.list_leads(tx, {"search": q, "industry": industry, "geography": geography,
                "record_mode": mode, "sort": {"score": "ai_score", "company": "company", "newest": "collected_at"}[sort],
                "order": "asc" if sort == "company" else "desc"})

    @app.post("/api/crm/leads/{lead_id}/{action}")
    def crm_action(lead_id: str, action: str, body: Key, request: Request):
        with db.transaction() as tx:
            actor = who(tx, request, True)
            if action not in {"score", "offer"}:
                raise policy.PolicyError("internal_action", "Choose score or offer", 422)
            return jobs.enqueue(tx, "lead_rescore" if action == "score" else "sales_prepare", {"lead_id": lead_id},
                                idempotency_key=body.idempotency_key, actor=actor)

    @app.post("/api/factory/orders")
    def create_order(body: Order, request: Request):
        with db.transaction() as tx:
            payload = body.model_dump(exclude={"idempotency_key"}, exclude_none=True)
            payload["kind"] = payload.pop("type")
            return factory.create(tx, payload, who(tx, request, True), body.idempotency_key)

    @app.post("/api/crm/offers/{offer_id}/review")
    def review_offer(offer_id: str, body: Review, request: Request):
        with db.transaction() as tx:
            actor = who(tx, request, True, True)
            if body.decision not in {"approve", "reject"}:
                raise policy.PolicyError("offer_review", "Choose approve or reject", 422)
            return intelligence.review_offer(tx, offer_id, body.decision == "approve", body.reason, actor)

    @app.post("/api/factory/orders/{order_id}/generate")
    def generate_order(order_id: str, body: Key, request: Request):
        with db.transaction() as tx:
            actor = who(tx, request, True)
            if tx.get("service_order", order_id) is None:
                raise policy.PolicyError("order_missing", "Service order not found", 404)
            return jobs.enqueue(tx, "factory_generate", {"order_id": order_id}, idempotency_key=body.idempotency_key, actor=actor)

    @app.post("/api/factory/results/{result_id}/review")
    def review_result(result_id: str, body: Review, request: Request):
        with db.transaction() as tx:
            return factory.review(tx, result_id, body.decision, body.reason, who(tx, request, True, True))

    @app.get("/api/factory/results/{result_id}/export")
    def export_result(result_id: str, request: Request, format: str = "markdown"):
        with db.transaction() as tx:
            item = factory.export(tx, result_id, format, who(tx, request))
            return Response(item["content"], media_type=item["media_type"],
                            headers={"Content-Disposition": 'attachment; filename="' + item["filename"] + '"'})

    @app.post("/api/internal/schedules/{schedule_id}")
    def configure_schedule(schedule_id: str, body: dict, request: Request):
        with db.transaction() as tx:
            return operations.configure_schedule(tx, schedule_id, body, who(tx, request, True, True))
