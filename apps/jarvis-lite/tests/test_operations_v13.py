"""Weekly internal schedules, real activity projections and additive upgrades."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
import os
import secrets
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql
import pytest
from sqlalchemy import select

from jarvis_lite import jobs, operations, policy
from jarvis_lite.policy import PolicyError
from jarvis_lite.store import Database, audit_rows, idempotency, records


ADMIN = {"id": "v13-ops-owner", "role": "admin"}
REASON = "Explicitly reviewed internal scheduling and retained authority"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_ENV", "test")
    monkeypatch.setenv("JARVIS_SESSION_SECRET", "synthetic-v13-upgrade-secret-for-tests")
    monkeypatch.delenv("JARVIS_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("KILL_SWITCH", "false")
    database = Database(f"sqlite:///{tmp_path / 'v13-ops.sqlite'}")
    with database.transaction() as tx:
        policy.initialize(tx)
    yield database
    database.close()


def timestamp(value):
    return datetime.fromisoformat(value).timestamp()


def weekly(tx, **changes):
    return operations.configure_schedule(tx, "weekly-report", {"cadence": "weekly", "weekday": 0,
        "daily_at": "08:00", "reason": REASON, **changes}, ADMIN)


def snapshot(tx, table):
    return [dict(row) for row in tx.conn.execute(select(table)).mappings()]


def test_v13_upgrade_adds_only_marker_without_rewriting_legacy_records(db):
    with db.transaction() as tx:
        policy.set_secret(tx, "OPENROUTER_API_KEY", "synthetic-retained-encrypted-value", ADMIN)
        for kind in ("user", "session", "business_mission", "agent_task", "ai_trace", "budget_entry",
                     "internal_schedule", "source_evidence", "custom_future_record"):
            tx.put(kind, "untouched", {"value": "retained", "status": "PAUSED", "enabled": False})
        tx.idempotency_put("old-request", "old-key", {"input": 1}, {"output": 2})
        tx.delete("upgrade", operations.UPGRADE_V13_ID)
        before = snapshot(tx, records)
        before_audit = snapshot(tx, audit_rows)
        before_keys = snapshot(tx, idempotency)
        operations.initialize_v13(tx)
        operations.initialize_v13(tx)
        after = {(row["kind"], row["id"]): row for row in snapshot(tx, records)}
        assert len(after) == len(before) + 1
        assert all(after[(row["kind"], row["id"])] == row for row in before)
        assert snapshot(tx, idempotency) == before_keys
        assert all(row in snapshot(tx, audit_rows) for row in before_audit)
        assert policy.get_secret(tx, "OPENROUTER_API_KEY") == "synthetic-retained-encrypted-value"
        assert tx.get("upgrade", operations.UPGRADE_V13_ID)["external_actions_enabled"] is False
        assert tx.get("business_mission", "untouched")["status"] == "PAUSED"


@pytest.mark.parametrize("start,weekday,at,expected", [
    ("2026-10-23T12:00:00+02:00", 0, "08:00", "2026-10-26T08:00:00+01:00"),
    ("2026-10-26T07:59:00+01:00", 0, "08:00", "2026-10-26T08:00:00+01:00"),
    ("2026-10-26T08:00:00+01:00", 0, "08:00", "2026-11-02T08:00:00+01:00"),
    ("2026-03-27T15:00:00+01:00", 0, "08:00", "2026-03-30T08:00:00+02:00"),
])
def test_weekly_calendar_handles_stockholm_day_and_dst(db, start, weekday, at, expected):
    with db.transaction() as tx:
        tx.now = timestamp(start)
        configured = weekly(tx, weekday=weekday, daily_at=at)
        assert configured["enabled"] is False
        assert configured["next_run_at"] == timestamp(expected)
        assert operations.schedule_due(tx) == []


@pytest.mark.parametrize("changes", [{"weekday": True}, {"weekday": -1}, {"weekday": 7},
    {"max_cost_per_run_minor": 0}, {"max_cost_per_run_minor": 1001}, {"cadence": "hourly"}])
def test_weekly_bounds_fail_closed(db, changes):
    with db.transaction() as tx:
        with pytest.raises(PolicyError) as denied:
            weekly(tx, **changes)
        assert denied.value.status == 422
        assert tx.list("internal_schedule") == []


def test_overdue_weekly_run_does_not_replay_missed_intervals_and_survives_restart(db):
    with db.transaction() as tx:
        configured = weekly(tx, enabled=True)
        configured["next_run_at"] = tx.now - 5 * 7 * 86400
        tx.put("internal_schedule", configured["id"], configured)
        queued = operations.schedule_due(tx)
        assert len(queued) == 1
        assert operations.schedule_due(tx) == []
        assert tx.get("internal_schedule", configured["id"])["next_run_at"] > tx.now
    db.close()
    reopened = Database(db.url)
    try:
        result = jobs.run_once(reopened)
        assert result["status"] == "completed"
        with reopened.transaction() as tx:
            assert operations.schedule_due(tx) == []
            assert len(tx.list("schedule_run")) == len(tx.list("business_report")) == 1
            assert tx.list("ai_trace") == tx.list("budget_entry") == []
    finally:
        reopened.close()


def test_two_schedulers_cannot_enqueue_same_weekly_run(db):
    with db.transaction() as tx:
        configured = weekly(tx, enabled=True)
        configured["next_run_at"] = tx.now - 1
        tx.put("internal_schedule", configured["id"], configured)

    def enqueue(_):
        connection = Database(db.url)
        try:
            with connection.transaction() as tx:
                return len(operations.schedule_due(tx))
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(enqueue, range(4))) == 1
    with db.transaction() as tx:
        assert len(tx.list("schedule_run")) == len(tx.list("job")) == 1


def test_per_run_cost_fence_is_distinct_from_daily_schedule_allowance(db):
    with db.transaction() as tx:
        configured = weekly(tx, enabled=True, max_cost_minor=1000, max_cost_per_run_minor=100)
        configured["next_run_at"] = tx.now - 1
        tx.put("internal_schedule", configured["id"], configured)
        job = operations.schedule_due(tx)[0]
        operations.validate_job(tx, job)
        operations.guard_scheduled(tx, 100)
        run_id = job["payload"]["schedule_run_id"]
        tx.put("budget_entry", "pending-run", {"state": "RESERVED", "schedule_id": configured["id"],
            "schedule_run_id": run_id, "created_at": tx.now - 3 * 86400, "reserved_sek_minor": 100})
        with pytest.raises(PolicyError) as denied:
            operations.guard_scheduled(tx, 1)
        assert denied.value.code == "schedule_run_budget"
        assert operations.schedules(tx)[0]["cost_today_minor"] == 100  # pending never ages out
        operations.guard_scheduled(tx, 0)  # own existing reservation is not double-counted


def test_mission_and_agent_activity_excludes_demo_and_expired_leases(db):
    with db.transaction() as tx:
        tx.put("business_mission", "completed", {"status": "COMPLETED", "created_at": tx.now - 60,
            "completed_at": tx.now, "data_mode": "REAL"})
        tx.put("business_mission", "working", {"status": "RUNNING", "created_at": tx.now, "data_mode": "REAL"})
        tx.put("business_mission", "demo", {"status": "COMPLETED", "created_at": tx.now, "data_mode": "DEMO"})
        tx.put("job", "demo-job", {"kind": "demo_journey", "status": "completed", "created_at": tx.now})
        for role, job_id, lease in (("ceo", "active", tx.now + 90), ("sales", "expired", tx.now - 1)):
            tx.put("job", job_id, {"kind": "internal_agent", "payload": {"mission_id": "working", "role": role},
                "status": "leased", "lease_until": lease, "created_at": tx.now})
            tx.put("agent_task", job_id, {"mission_id": "working", "role": role, "status": "RUNNING"})
        tx.put("agent_task", "done", {"mission_id": "completed", "role": "ceo", "status": "COMPLETED"})
        tx.put("agent_task", "demo-done", {"mission_id": "demo", "role": "ceo", "status": "COMPLETED"})
        metrics = operations.analytics(tx)
        assert metrics["missions"]["completed"] == metrics["missions"]["active"] == 1
        assert metrics["active_agents"] == 1
        assert metrics["completed_jobs"] == 0  # synthetic journey is not business activity
        assert metrics["mission_series"][-1]["completed"] == 1
        ceo = next(row for row in metrics["agent_activity"] if row["role"] == "ceo")
        sales = next(row for row in metrics["agent_activity"] if row["role"] == "sales")
        assert ceo["active_jobs"] == ceo["completed_tasks"] == 1
        assert sales["active_jobs"] == 0


@pytest.mark.skipif(not os.getenv("JARVIS_TEST_DATABASE_URL"), reason="Needs local PostgreSQL maintenance database")
def test_postgres_v13_upgrade_keeps_all_records_audits_keys_and_authority_after_reopen(monkeypatch):
    monkeypatch.setenv("JARVIS_ENV", "test")
    monkeypatch.setenv("JARVIS_SESSION_SECRET", "synthetic-v13-postgres-upgrade-secret")
    monkeypatch.delenv("JARVIS_ENCRYPTION_KEY", raising=False)
    supplied = os.environ["JARVIS_TEST_DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    parsed = urlsplit(supplied)
    name = "jarvis_v13_upgrade_test_" + secrets.token_hex(10)
    with psycopg.connect(supplied, autocommit=True, connect_timeout=10) as maintenance:
        maintenance.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        database = None
        try:
            url = urlunsplit((parsed.scheme, parsed.netloc, "/" + name, parsed.query, parsed.fragment))
            database = Database(url)
            with database.transaction() as tx:
                policy.initialize(tx)
                policy.set_secret(tx, "OPENROUTER_API_KEY", "synthetic-kept-key", ADMIN)
                for kind in ("user", "session", "business_mission", "agent_task", "ai_trace", "payment", "suppression"):
                    tx.put(kind, "retained", {"value": "keep", "status": "PAUSED", "role": "operator"})
                tx.idempotency_put("existing", "existing", {"input": 1}, {"result": 2})
                tx.delete("upgrade", operations.UPGRADE_V13_ID)
                before = snapshot(tx, records)
                audits = snapshot(tx, audit_rows)
                keys = snapshot(tx, idempotency)
                settings = deepcopy(policy.get_settings(tx))
                operations.initialize_v13(tx)
                operations.initialize_v13(tx)
            database.close()
            database = Database(url)
            with database.transaction() as tx:
                after = {(row["kind"], row["id"]): row for row in snapshot(tx, records)}
                assert len(after) == len(before) + 1
                assert all(after[(row["kind"], row["id"])] == row for row in before)
                assert all(row in snapshot(tx, audit_rows) for row in audits)
                assert snapshot(tx, idempotency) == keys
                assert policy.get_settings(tx) == settings
                assert policy.get_secret(tx, "OPENROUTER_API_KEY") == "synthetic-kept-key"
                assert tx.get("business_mission", "retained")["status"] == "PAUSED"
                assert tx.list("internal_schedule") == [] and tx.list("ai_config") == []
        finally:
            if database is not None:
                database.close()
            maintenance.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
