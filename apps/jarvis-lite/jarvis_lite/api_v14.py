"""Explicit, administrator-reviewed recovery of a single failed internal step."""
from fastapi import Request
from pydantic import Field

from . import engine
from .api_v12 import Key
from .api_v13 import overview as previous_overview


class RetryFailedStep(Key):
    reason: str = Field(min_length=12, max_length=500)
    expected_task_id: str = Field(min_length=1, max_length=160)
    expected_trace_id: str = Field(min_length=1, max_length=160)


def register_routes(app, db, who):
    @app.get("/api/v1.4/overview")
    def workspace(request: Request):
        with db.transaction() as tx:
            who(tx, request)
            data = previous_overview(tx)
            data["version"] = "1.4.0"
            return data

    @app.post("/api/missions/{mission_id}/retry-failed-step")
    def retry_failed_step(mission_id: str, body: RetryFailedStep, request: Request):
        with db.transaction() as tx:
            return engine.retry_failed_step(tx, mission_id,
                actor=who(tx, request, True, True), reason=body.reason,
                idempotency_key=body.idempotency_key,
                expected_task_id=body.expected_task_id,
                expected_trace_id=body.expected_trace_id)
