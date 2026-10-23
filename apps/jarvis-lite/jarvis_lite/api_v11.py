"""Additive authenticated preparation APIs; installation never enables a provider."""
from html import escape
from urllib.parse import parse_qs

from fastapi import Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from . import ai, jobs, outreach, pipeline, research, upgrade
from .policy import PolicyError


class Key(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    idempotency_key: str = Field(min_length=1, max_length=100)


class Import(Key):
    records: list[dict] = Field(max_length=50)


class Reason(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reason: str = Field(min_length=12, max_length=500)


def register_routes(app, db, who):
    @app.get("/api/v1.1/readiness")
    def readiness(request: Request):
        with db.transaction() as tx:
            who(tx, request)
            return {"ai": ai.status(tx), "research_sources": research.sources(tx),
                    "outreach": outreach.status(tx), "pipeline": pipeline.status(tx)}

    @app.post("/api/ai/config")
    def configure_ai(body: dict, request: Request):
        with db.transaction() as tx:
            return ai.configure(tx, body, who(tx, request, True, True))

    @app.post("/api/research/sources/{source_id}")
    def configure_source(source_id: str, body: dict, request: Request):
        with db.transaction() as tx:
            return research.configure_source(tx, source_id, body, who(tx, request, True, True))

    @app.post("/api/research/sources/{source_id}/ingest")
    def ingest(source_id: str, body: Import, request: Request):
        with db.transaction() as tx:
            return research.ingest(tx, source_id, body.records, who(tx, request, True), body.idempotency_key)

    @app.post("/api/research/sources/{source_id}/fetch")
    def fetch_source(source_id: str, body: Key, request: Request):
        with db.transaction() as tx:
            return research.fetch_source(tx, source_id, who(tx, request, True, True), body.idempotency_key)

    @app.post("/api/outreach/config")
    def configure_outreach(body: dict, request: Request):
        with db.transaction() as tx:
            return outreach.configure(tx, body, who(tx, request, True, True))

    @app.post("/api/outreach/checks")
    def test_outreach(request: Request):
        with db.transaction() as tx:
            return outreach.run_checks(tx, who(tx, request, True, True))

    @app.post("/api/outreach/review")
    def review_outreach(body: dict, request: Request):
        with db.transaction() as tx:
            return outreach.record_review(tx, body, who(tx, request, True, True))

    @app.post("/api/pipeline/config")
    def configure_pipeline(body: dict, request: Request):
        with db.transaction() as tx:
            return pipeline.configure(tx, body, actor=who(tx, request, True, True))

    @app.post("/api/pipeline/leads/{lead_id}/advance")
    def advance_pipeline(lead_id: str, body: Key, request: Request):
        with db.transaction() as tx:
            return jobs.enqueue(tx, "pipeline_advance", {"lead_id": lead_id, "manual_preparation": True,
                                "pipeline_revision": pipeline.status(tx)["revision"]},
                                idempotency_key=body.idempotency_key, actor=who(tx, request, True))

    @app.post("/api/tasks/{task_id}/resolve")
    def resolve_task(task_id: str, body: Reason, request: Request):
        with db.transaction() as tx:
            return upgrade.resolve_task(tx, task_id, body.reason, who(tx, request, True))

    @app.post("/api/webhooks/email/delivery")
    async def email_delivery(request: Request):
        raw = await request.body()
        with db.transaction() as tx:
            return outreach.receive_delivery(tx, raw,
                signature=request.headers.get("X-Jarvis-Signature", ""),
                timestamp=request.headers.get("X-Jarvis-Timestamp", "0"),
                event_id=request.headers.get("X-Jarvis-Event-Id"))

    @app.get("/api/contact/unsubscribe")
    def unsubscribe_preview(token: str):
        with db.transaction() as tx:
            outreach.process_unsubscribe(tx, token, confirm=False)
        return HTMLResponse('<!doctype html><html lang="sv"><meta charset="utf-8">'
            '<title>Avregistrera kontakt</title><h1>Avregistrera företagskontakt</h1>'
            '<p>Bekräfta att du vill stoppa fler kontaktmeddelanden.</p>'
            '<form method="post"><input type="hidden" name="token" value="' + escape(token, quote=True) +
            '"><button type="submit">Avregistrera</button></form></html>')

    @app.post("/api/contact/unsubscribe")
    async def unsubscribe_confirm(request: Request):
        raw = await request.body()
        try:
            if request.headers.get("Content-Type", "").startswith("application/json"):
                payload = await request.json()
                token = payload["token"]
            else:
                token = request.query_params.get("token") or parse_qs(raw.decode(), strict_parsing=True)["token"][0]
            if not isinstance(token, str) or len(token) > 4096:
                raise ValueError
        except (KeyError, ValueError, TypeError, UnicodeDecodeError):
            raise PolicyError("unsubscribe_invalid", "A valid signed unsubscribe link is required", 422) from None
        with db.transaction() as tx:
            outreach.process_unsubscribe(tx, token, confirm=True)
        return HTMLResponse('<!doctype html><html lang="sv"><meta charset="utf-8">'
                            '<title>Avregistrerad</title><p>Du är nu avregistrerad från fler kontaktmeddelanden.</p></html>')
