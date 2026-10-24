"""Administrator-reviewed autonomous research without customer-write authority."""
from fastapi import Request

from . import autonomous_research
from .api_v13 import overview as previous_overview


def register_routes(app, db, who):
    @app.get("/api/v1.5/overview")
    def workspace(request: Request):
        with db.transaction() as tx:
            who(tx, request)
            data = previous_overview(tx)
            data.update(version="1.5.0", autonomous_research=autonomous_research.status(tx))
            return data

    @app.get("/api/research/autonomy")
    def research_status(request: Request):
        with db.transaction() as tx:
            who(tx, request)
            return autonomous_research.status(tx)

    @app.post("/api/research/autonomy/policy")
    def configure_policy(body: dict, request: Request):
        with db.transaction() as tx:
            return autonomous_research.configure(tx, body, who(tx, request, True, True))

    @app.post("/api/research/autonomy/activation")
    def activate_policy(body: dict, request: Request):
        with db.transaction() as tx:
            return autonomous_research.activate(tx, body, who(tx, request, True, True))
