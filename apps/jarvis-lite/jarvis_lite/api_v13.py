"""Authenticated business workspace views over durable internal work."""
from fastapi import Query, Request
from fastapi.responses import Response

from . import budget, engine, factory, operations, research
from .api_v12 import Key, Reason, overview as previous_overview
from pydantic import Field


class Documents(Key):
    documents: list[dict] = Field(min_length=1, max_length=20)


def overview(tx):
    data = previous_overview(tx)
    data.update(version="1.3.0", business_actions_enabled=False,
        catalogue=factory.catalogue(), documents=research.documents(tx),
        source_conflicts=research.source_conflicts(tx),
        finance=budget.analytics(tx), ai_limits=budget.ai_limits(tx))
    data["reports"].update(quality=tx.list("quality_review"), opportunities=tx.list("opportunity_report"),
                           summaries=tx.list("mission_summary"))
    return data


def register_routes(app, db, who):
    @app.get("/api/v1.3/overview")
    def workspace(request: Request):
        with db.transaction() as tx:
            who(tx, request)
            return overview(tx)

    @app.get("/api/missions")
    def missions(request: Request, status: str = "", q: str = Query(default="", max_length=200)):
        with db.transaction() as tx:
            who(tx, request)
            return engine.list_missions(tx, {"status": status, "q": q})

    @app.get("/api/missions/{mission_id}")
    def mission_detail(mission_id: str, request: Request):
        with db.transaction() as tx:
            who(tx, request)
            return engine.mission_detail(tx, mission_id)

    @app.post("/api/missions/{mission_id}/cancel")
    def cancel_mission(mission_id: str, body: Reason, request: Request):
        with db.transaction() as tx:
            return engine.cancel_mission(tx, mission_id, who(tx, request, True, True), body.reason)

    @app.get("/api/agents/{role}")
    def agent_detail(role: str, request: Request):
        with db.transaction() as tx:
            who(tx, request)
            return engine.agent_detail(tx, role)

    @app.get("/api/research/documents")
    def documents(request: Request):
        with db.transaction() as tx:
            who(tx, request)
            return {"items": research.documents(tx)}

    @app.get("/api/research/conflicts")
    def source_conflicts(request: Request):
        with db.transaction() as tx:
            who(tx, request)
            return {"items": research.source_conflicts(tx)}

    @app.post("/api/research/sources/{source_id}/documents")
    def import_documents(source_id: str, body: Documents, request: Request):
        with db.transaction() as tx:
            return research.ingest_documents(tx, source_id, body.documents,
                who(tx, request, True), body.idempotency_key)

    @app.get("/api/deliverables")
    def deliverables(request: Request):
        with db.transaction() as tx:
            who(tx, request)
            return {"items": factory.results(tx) + tx.list("mission_summary"),
                    "orders": factory.list_orders(tx), "catalogue": factory.catalogue()}

    @app.get("/api/deliverables/{result_id}")
    def deliverable_detail(result_id: str, request: Request):
        with db.transaction() as tx:
            return factory.detail(tx, result_id, who(tx, request))

    @app.get("/api/deliverables/{result_id}/export")
    def export_deliverable(result_id: str, request: Request, format: str = "markdown"):
        with db.transaction() as tx:
            item = factory.export(tx, result_id, format, who(tx, request))
            return Response(item["content"], media_type=item["media_type"],
                headers={"Content-Disposition": 'attachment; filename="' + item["filename"] + '"'})

    @app.get("/api/finance")
    def finance(request: Request):
        with db.transaction() as tx:
            who(tx, request)
            return {"budget": budget.summary(tx), "analytics": budget.analytics(tx),
                    "ai_limits": budget.ai_limits(tx), "metrics": operations.analytics(tx)}

    @app.post("/api/budget/ai-limits")
    def configure_ai_limits(body: dict, request: Request):
        with db.transaction() as tx:
            return budget.configure_ai_limits(tx, body, who(tx, request, True, True))
