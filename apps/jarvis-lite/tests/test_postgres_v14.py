"""Recovery races across independent PostgreSQL connections; only owned random DBs."""
from concurrent.futures import ThreadPoolExecutor
import os

from fastapi.testclient import TestClient
import pytest

from jarvis_lite import auth, engine, jobs
from jarvis_lite.app import create_app
from jarvis_lite.policy import PolicyError
from jarvis_lite.store import Database
from test_integration_v13 import ADMIN, PASSWORD, REASON
from test_integration_v14 import (activate_current, approve_larger_output,
                                  prepare_failed_mission, retry_payload)
from test_postgres import postgres_url


pytestmark = pytest.mark.skipif(not os.getenv("JARVIS_TEST_DATABASE_URL"),
    reason="Independent-connection recovery races require isolated PostgreSQL")


@pytest.fixture
def failed_postgres(postgres_url, monkeypatch):
    monkeypatch.setenv("JARVIS_ENV", "test")
    monkeypatch.setenv("JARVIS_SESSION_SECRET", "v14-postgres-synthetic-session-secret")
    monkeypatch.delenv("JARVIS_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("KILL_SWITCH", "false")
    for name in ("JARVIS_ALLOW_LIVE", "JARVIS_ALLOW_LIVE_CONTACT", "JARVIS_ALLOW_LIVE_PAYMENTS",
                 "JARVIS_ALLOW_PAID_AI", "JARVIS_ALLOW_RESEARCH_NETWORK"):
        monkeypatch.setenv(name, "false")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    database = Database(postgres_url)
    try:
        with database.transaction() as tx:
            for role in ("admin", "operator", "viewer"):
                auth.create_user(tx, role + "@example.com", PASSWORD, role,
                                 actor=ADMIN, bootstrap=role == "admin")
        with TestClient(create_app(database, worker_enabled=False)) as client:
            headers, mission_id, detail, completed, calls = prepare_failed_mission(client, database, monkeypatch)
            activate_current(client, headers, approve_larger_output(client, headers))
            yield database, mission_id, detail, completed, calls
    finally:
        database.close()


@pytest.mark.parametrize("same_key", [True, False], ids=["duplicate-click", "different-keys-same-failed-trace"])
def test_postgres_retry_and_workers_cannot_duplicate_failed_step(failed_postgres, same_key):
    database, mission_id, detail, completed, calls = failed_postgres
    connections = [Database(database.url) for _ in range(8)]
    payload = retry_payload(detail)
    with database.transaction() as tx:
        prior_traces = {item["id"]: item for item in tx.list("ai_trace")}
        prior_entries = {item["id"]: item for item in tx.list("budget_entry")}

    def authorize(index):
        try:
            with connections[index].transaction() as tx:
                result = engine.retry_failed_step(tx, mission_id, actor=ADMIN, reason=REASON,
                    idempotency_key=payload["idempotency_key"] if same_key else "race-click-" + str(index),
                    expected_task_id=payload["expected_task_id"], expected_trace_id=payload["expected_trace_id"])
                return {"retry_id": result["retry_id"], "idempotent": result["idempotent"]}
        except PolicyError as error:
            return {"blocked": error.code}

    try:
        with ThreadPoolExecutor(max_workers=len(connections)) as pool:
            authorizations = list(pool.map(authorize, range(len(connections))))
        successful = [value for value in authorizations if "retry_id" in value]
        assert len(successful) == (8 if same_key else 1), authorizations
        assert len({value["retry_id"] for value in successful}) == 1
        assert sum(value["idempotent"] is False for value in successful) == 1
        retry_id = successful[0]["retry_id"]
        with database.transaction() as tx:
            retries = tx.list("mission_retry")
            queued = [job for job in tx.list("job") if job["status"] == "queued"]
            assert len(retries) == len(queued) == 1
            assert queued[0]["id"] == retries[0]["job_id"]
            assert len(tx.list("ai_trace")) == 5  # Authorization alone never dispatches a provider.
        # Every worker has a different process-local mutex and DB connection;
        # the PostgreSQL control row, durable job lease and outbox do the fencing.
        with ThreadPoolExecutor(max_workers=len(connections)) as pool:
            list(pool.map(jobs.run_once, connections))
        assert sum(call["contract"] == "jarvis_finance_analysis" for call in calls) == 2
        for contract in ("jarvis_ceo_plan", "jarvis_research_report", "jarvis_sales_strategy", "jarvis_service_result"):
            assert sum(call["contract"] == contract for call in calls) == 1
        with database.transaction() as tx:
            assert len(tx.list("mission_retry")) == 1
            retry_traces = [trace for trace in tx.list("ai_trace") if trace.get("retry_id") == retry_id]
            assert len(retry_traces) == 1 and retry_traces[0]["id"] not in prior_traces
            assert retry_traces[0]["status"] == "COMPLETED"
            assert retry_traces[0]["retry_of_trace_id"] == payload["expected_trace_id"]
            assert {key: tx.get("agent_task", key) for key in completed} == completed
            assert {key: tx.get("ai_trace", key) for key in prior_traces} == prior_traces
            assert {key: tx.get("budget_entry", key) for key in prior_entries} == prior_entries
            assert all(entry["state"] == "SETTLED" for entry in tx.list("budget_entry"))
            assert tx.list("email_message") == tx.list("payment") == []
        # A fresh engine after connection disposal sees the same durable retry.
        for connection in connections:
            connection.close()
        restored = Database(database.url)
        try:
            if same_key:
                with restored.transaction() as tx:
                    replay = engine.retry_failed_step(tx, mission_id, actor=ADMIN, reason=REASON,
                        idempotency_key=payload["idempotency_key"], expected_task_id=payload["expected_task_id"],
                        expected_trace_id=payload["expected_trace_id"])
                    assert replay["idempotent"] is True and replay["retry_id"] == retry_id
        finally:
            restored.close()
    finally:
        for connection in connections:
            connection.close()
