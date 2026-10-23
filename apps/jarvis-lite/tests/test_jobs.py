from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from threading import Event, Thread
import time

import pytest

from jarvis_lite import jobs, policy
from jarvis_lite.policy import PolicyError
from jarvis_lite.store import Database


ADMIN = {"id": "owner", "role": "admin"}
OPERATOR = {"id": "sales", "role": "operator"}


@pytest.fixture
def db(tmp_path):
    database = Database("sqlite:///" + str(tmp_path / "jobs.sqlite3"))
    with database.transaction() as tx:
        policy.initialize(tx)
    yield database
    database.close()


def queue(db, key="job-test", kind="daily_report", payload=None):
    with db.transaction() as tx:
        return jobs.enqueue(tx, kind, payload or {}, idempotency_key=key, actor=OPERATOR)


def read(db, job_id):
    with db.transaction() as tx:
        return tx.get("job", job_id)


def make_due(db, job_id):
    with db.transaction() as tx:
        job = tx.get("job", job_id)
        job["run_at"] = tx.now - 1
        tx.put("job", job_id, job)


def test_enqueue_is_idempotent_and_rejects_reused_key(db):
    first = queue(db)
    assert queue(db)["id"] == first["id"]
    with db.transaction() as tx:
        assert len(tx.list("job")) == 1
        with pytest.raises(PolicyError, match="different input"):
            jobs.enqueue(tx, "research", {"lead_id": "other"}, idempotency_key="job-test", actor=OPERATOR)
    with db.transaction() as tx:
        with pytest.raises(PolicyError) as rejected:
            jobs.enqueue(tx, "arbitrary_external_tool", {}, idempotency_key="forbidden", actor=OPERATOR)
        assert rejected.value.code == "unknown_job_kind"


def test_concurrent_workers_only_claim_one_job_across_connections(db):
    job = queue(db)
    second = Database(db.url)
    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda n: jobs.claim(db if n % 2 else second), range(8)))
        claims = [item for item in results if item]
        assert len(claims) == 1
        assert claims[0]["id"] == job["id"]
        assert read(db, job["id"])["attempts"] == 1
    finally:
        second.close()


def test_job_survives_database_close_and_restart(db, monkeypatch):
    job = queue(db)
    url = db.url
    db.close()
    restarted = Database(url)
    calls = []
    monkeypatch.setattr(jobs, "_dispatch", lambda tx, item: calls.append(item["id"]) or {"finished": True})
    try:
        result = jobs.run_once(restarted)
        assert result["status"] == "completed"
        assert result["result"] == {"finished": True}
        assert jobs.run_once(restarted) is None
        assert calls == [job["id"]]
    finally:
        restarted.close()


def test_expired_worker_lease_is_fenced_after_restart(db, monkeypatch):
    job = queue(db)
    stale = jobs.claim(db)
    with db.transaction() as tx:
        record = tx.get("job", job["id"])
        record["lease_until"] = tx.now - 10
        tx.put("job", job["id"], record)
    current = jobs.claim(db)
    assert current["lease_token"] != stale["lease_token"]
    assert current["attempts"] == 2
    calls = []
    monkeypatch.setattr(jobs, "_dispatch", lambda tx, item: calls.append(item["id"]) or {"ok": True})
    monkeypatch.setattr(jobs, "claim", lambda database: stale)
    assert jobs.run_once(db)["status"] == "leased"
    assert calls == []
    monkeypatch.setattr(jobs, "claim", lambda database: current)
    assert jobs.run_once(db)["status"] == "completed"
    assert calls == [job["id"]]


def test_kill_pauses_queued_and_leased_jobs_and_requires_admin_resume(db, monkeypatch):
    first = queue(db, "first")
    second = queue(db, "second")
    jobs.claim(db)
    with db.transaction() as tx:
        policy.set_kill_switch(tx, True, "Emergency test stop", ADMIN)
        assert all(item["status"] == "paused" for item in tx.list("job"))
        queued_while_killed = jobs.enqueue(tx, "daily_report", {}, idempotency_key="during-kill")
        assert queued_while_killed["status"] == "paused"
    assert jobs.claim(db) is None
    with db.transaction() as tx:
        policy.set_kill_switch(tx, False, "Investigated; jobs still require explicit review", ADMIN)
    assert jobs.claim(db) is None
    with db.transaction() as tx:
        with pytest.raises(PolicyError) as denied:
            jobs.resume(tx, first["id"], actor=OPERATOR)
        assert denied.value.code == "admin_required"
        jobs.resume(tx, first["id"], actor=ADMIN)
    monkeypatch.setattr(jobs, "_dispatch", lambda tx, item: {"ok": True})
    assert jobs.run_once(db)["id"] == first["id"]
    assert read(db, second["id"])["status"] == "paused"
    assert read(db, queued_while_killed["id"])["status"] == "paused"


def test_persistent_epoch_blocks_even_an_unobserved_kill_toggle(db):
    job = queue(db)
    # Simulate a pause/resume while an older worker process is offline. It has
    # not seen kill=True, but the persistent epoch must still fence its job.
    with db.transaction() as tx:
        settings = policy.get_settings(tx)
        settings["safety_epoch"] += 2
        settings["kill_switch"] = False
        tx.put("settings", "global", settings)
    assert jobs.claim(db) is None
    assert read(db, job["id"])["status"] == "paused"


def test_kill_between_lease_claim_and_execution_fences_the_old_worker(db, monkeypatch):
    queued = queue(db)
    original_claim = jobs.claim

    def claim_then_kill(database):
        claimed = original_claim(database)
        with database.transaction() as tx:
            policy.set_kill_switch(tx, True, "Stop immediately before handler execution", ADMIN)
        return claimed

    monkeypatch.setattr(jobs, "claim", claim_then_kill)
    monkeypatch.setattr(jobs, "_dispatch", lambda tx, item: pytest.fail("Killed job must never execute"))
    result = jobs.run_once(db)
    assert result["id"] == queued["id"]
    assert result["status"] == "paused"


def test_queued_payload_cannot_give_the_agent_admin_authority(db, monkeypatch):
    from jarvis_lite import workflow

    seen = []

    def demo(tx, *, actor, key):
        seen.append(actor)
        with pytest.raises(PolicyError):
            policy.require_admin(actor)
        return {"stopped_at": "PAYMENT_APPROVAL_REQUIRED"}

    monkeypatch.setattr(workflow, "run_demo", demo)
    job = queue(db, kind="demo_journey", payload={"actor": ADMIN, "approve_payments": True})
    assert jobs.run_once(db)["id"] == job["id"]
    assert seen == [{"id": "agent:ceo", "role": "operator"}]


def test_transient_failures_are_bounded_and_secret_free(db, monkeypatch):
    job = queue(db)

    def fail(tx, item):
        tx.create("manual_task", {"type": "must-rollback"})
        raise RuntimeError("private-provider-secret-must-not-be-stored")

    monkeypatch.setattr(jobs, "_dispatch", fail)
    for attempt in range(1, 4):
        result = jobs.run_once(db)
        assert result["attempts"] == attempt
        assert result["status"] == ("failed" if attempt == 3 else "queued")
        make_due(db, job["id"])
    assert jobs.run_once(db) is None
    with db.transaction() as tx:
        assert len(tx.list("manual_task")) == 1  # one review task; failed handler writes rolled back
        serialized = json.dumps(tx.list("job") + tx.list("manual_task") + tx.audits())
        assert "private-provider-secret" not in serialized


def test_uncertain_provider_outcome_never_retries_without_reconciliation(db, monkeypatch):
    job = queue(db)

    def uncertain(tx, item):
        raise PolicyError("provider_uncertain", "Response lost; provider may have committed")

    monkeypatch.setattr(jobs, "_dispatch", uncertain)
    assert jobs.run_once(db)["status"] == "uncertain"
    assert jobs.run_once(db) is None
    with db.transaction() as tx:
        with pytest.raises(PolicyError) as rejected:
            jobs.resume(tx, job["id"], actor=ADMIN)
        assert rejected.value.code == "reconciliation_required"
        with pytest.raises(PolicyError) as bypass:
            jobs.pause(tx, job["id"], actor=ADMIN)
        assert bypass.value.code == "reconciliation_required"
        jobs.reconcile(tx, job["id"], outcome="not_executed",
                       reason="Provider logs confirm there was no execution", actor=ADMIN)
        jobs.resume(tx, job["id"], actor=ADMIN)
    monkeypatch.setattr(jobs, "_dispatch", lambda tx, item: {"reviewed": True})
    assert jobs.run_once(db)["status"] == "completed"


def test_scheduler_is_idempotent_and_followups_only_create_tasks(db):
    with db.transaction() as tx:
        lead = tx.create("lead", {"state": "CONTACTED", "follow_up_at": tx.now - 10})
        first = jobs.schedule_due(tx)
        second = jobs.schedule_due(tx)
        assert {item["id"] for item in first} == {item["id"] for item in second}
        follow_up = next(item for item in first if item["kind"] == "follow_up")
        # Run this handler by itself: it must never create outbound messages.
        result = jobs._dispatch(tx, follow_up)
        assert result["lead_id"] == lead["id"]
        assert result["requires_compliance"] is True
        assert tx.list("message") == []
        assert tx.list("payment") == []
    with db.transaction() as tx:
        policy.set_kill_switch(tx, True, "Pause scheduler during review", ADMIN)
        assert jobs.schedule_due(tx) == []


def test_daily_report_uses_stockholm_calendar_instead_of_host_utc_day(db):
    with db.transaction() as tx:
        # These instants share a UTC date, but cross midnight in Stockholm.
        tx.now = datetime(2026, 10, 23, 21, 59, tzinfo=timezone.utc).timestamp()
        before_midnight = jobs.schedule_due(tx)[0]
        tx.now = datetime(2026, 10, 23, 22, 1, tzinfo=timezone.utc).timestamp()
        after_midnight = jobs.schedule_due(tx)[0]
        assert before_midnight["id"] != after_midnight["id"]
        assert before_midnight["payload"]["report_date"] == "2026-10-23"
        assert after_midnight["payload"]["report_date"] == "2026-10-24"
        assert after_midnight["payload"]["timezone"] == "Europe/Stockholm"
        assert jobs.schedule_due(tx)[0]["id"] == after_midnight["id"]


def test_worker_loop_stops_without_spinning_when_database_is_unavailable():
    class BrokenDatabase:
        def __init__(self):
            self.calls = 0

        def transaction(self):
            self.calls += 1
            raise OSError("Database unavailable; message must not leak")

    database = BrokenDatabase()
    stopped = Event()
    thread = Thread(target=jobs.worker_loop, args=(database, stopped), kwargs={"poll_interval": 0.05})
    thread.start()
    time.sleep(0.12)
    stopped.set()
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert 1 <= database.calls <= 5
