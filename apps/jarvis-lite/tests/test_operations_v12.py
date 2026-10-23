from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import os
import secrets
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql
import pytest
from sqlalchemy import select

from jarvis_lite import jobs, operations, policy, research
from jarvis_lite.policy import PolicyError
from jarvis_lite.store import Database, records


ADMIN = {"id": "reviewing-owner", "role": "admin"}
OPERATOR = {"id": "operator", "role": "operator"}
REASON = "Reviewed bounded internal work only"


@pytest.fixture
def db(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'operations.sqlite'}")
    with database.transaction() as tx:
        policy.initialize(tx)
        operations.initialize(tx)
    yield database
    database.close()


def now(text):
    return datetime.fromisoformat(text).timestamp()


def enable(tx, id="daily", **config):
    return operations.configure_schedule(tx, id, {"enabled": True, "cadence": "interval",
        "interval_minutes": 15, "reason": REASON, **config}, ADMIN)


def due(tx, id="daily"):
    schedule = tx.get("internal_schedule", id)
    schedule["next_run_at"] = tx.now - 1
    tx.put("internal_schedule", id, schedule)


def finish(tx, job):
    job.update(status="completed", completed_at=tx.now)
    tx.put("job", job["id"], job)


def entry(tx, id, *, state="SETTLED", amount=100, category="ai", created_at=None, **extra):
    return tx.put("budget_entry", id, {"state": state, "category": category, "vendor": "test-provider",
        "workflow_id": "manual-test", "created_at": tx.now if created_at is None else created_at,
        "reserved_sek_minor": amount if state == "RESERVED" else 0,
        "actual_sek_minor": amount if state != "RESERVED" else 0,
        "input_tokens": 11, "output_tokens": 7, "calls": 1, **extra})


def real_sourced_lead(tx):
    research.configure_source(tx, "approved", {"name": "Reviewed fixture directory", "kind": "upload",
        "url": "https://approved.example.com/evidence", "approved": True, "license_reviewed": True,
        "legal_review_reference": REASON, "cost_evidence": "Synthetic no-charge source contract"}, ADMIN)
    imported = research.ingest(tx, "approved", [{"company": "Attributed Example AB", "email": "info@real.example.com",
        "evidence": "Published company material describes incoming support enquiries and reply handling.",
        "entity_type": "legal_person", "address_type": "generic"}], OPERATOR, "seed-real")
    return tx.get("lead", imported["lead_ids"][0])


def test_upgrade_preserves_every_existing_record_and_does_not_activate_anything(db):
    with db.transaction() as tx:
        for kind, id, data in (
            ("user", "owner", {"password_hash": "retained", "role": "admin", "active": True}),
            ("session", "login", {"csrf": "retained", "expires_at": tx.now + 500}),
            ("ai_config", "global", {"enabled": False, "model": "existing/model"}),
            ("secret", "OPENROUTER_API_KEY", {"ciphertext": "unchanged-ciphertext"}),
            ("job", "legacy", {"kind": "daily_report", "status": "paused"}),
            ("payment", "receipt", {"provider": "demo", "status": "CONFIRMED"}),
            ("suppress", "previous", {"reason": "retained opt out"}),
            ("lead", "customer", {"company": "Existing customer"}),
            ("internal_schedule", "off", {**operations.DEFAULTS, "revision": 1}),
        ):
            tx.put(kind, id, data)
        tx.delete("upgrade", operations.UPGRADE_ID)
        before = list(tx.conn.execute(select(records)).mappings())
        operations.initialize(tx)
        operations.initialize(tx)
        after = { (row["kind"], row["id"]): dict(row) for row in tx.conn.execute(select(records)).mappings() }
        assert all(after[(row["kind"], row["id"])] == row for row in before)
        assert len(after) == len(before) + 1
        assert tx.get("upgrade", operations.UPGRADE_ID)["external_actions_enabled"] is False
        assert tx.get("ai_config", "global")["enabled"] is False
        assert operations.schedule_due(tx) == []
        assert len([row for row in tx.audits() if row["action"] == "upgrade.v12_applied"]) == 2  # fixture + one real upgrade


def test_schedules_default_off_require_admin_and_never_allow_external_job_kinds(db):
    with db.transaction() as tx:
        assert operations.schedules(tx) == []
        with pytest.raises(PolicyError) as denied:
            operations.configure_schedule(tx, "reports", {"reason": REASON}, OPERATOR)
        assert denied.value.code == "admin_required"
        config = operations.configure_schedule(tx, "reports", {"reason": REASON}, ADMIN)
        assert config["enabled"] is False
        due(tx, "reports")
        assert operations.schedule_due(tx) == []
        for forbidden in ("send_contact", "payment_request", "arbitrary_tool", "source_fetch"):
            with pytest.raises(PolicyError):
                enable(tx, kind=forbidden)
        assert tx.list("job") == []
        assert tx.list("payment") == tx.list("email_intent") == []


@pytest.mark.parametrize("invalid", [
    {"source_ids": [{}]}, {"source_ids": ["same", "same"]}, {"kind": []}, {"cadence": []},
    {"deliverable_kind": {}}, {"enabled": "true"}, {"interval_minutes": True},
    {"max_runs_per_day": 25}, {"max_cost_minor": 50001}, {"timezone": "UTC"},
    {"daily_at": "25:00"}, {"refresh_sources": True}, {"email": "someone@example.com"},
])
def test_malformed_schedule_inputs_fail_closed_with_validation_error(db, invalid):
    with db.transaction() as tx:
        with pytest.raises(PolicyError) as denied:
            enable(tx, **invalid)
        assert denied.value.status == 422
        assert tx.list("internal_schedule") == []


def test_due_report_is_idempotent_survives_restart_and_executes_only_internal_metrics(db):
    with db.transaction() as tx:
        enable(tx)
        due(tx)
        queued = operations.schedule_due(tx)
        assert len(queued) == 1
        assert queued[0]["kind"] == "operations_report"
        assert operations.schedule_due(tx) == []
        operations.validate_job(tx, queued[0])
        assert tx.schedule_context["schedule_run_id"] == tx.list("schedule_run")[0]["id"]
    db.close()
    reopened = Database(db.url)
    try:
        result = jobs.run_once(reopened)
        assert result["id"] == queued[0]["id"]
        assert result["status"] == "completed"
        with reopened.transaction() as tx:
            assert len(tx.list("business_report")) == 1
            report = tx.list("business_report")[0]
            assert report["ai_generated"] is False
            assert report["source"] == "persisted_records"
            assert tx.list("ai_trace") == tx.list("budget_entry") == tx.list("payment") == []
            assert operations.schedules(tx)[0]["recent_runs"][0]["status"] == "COMPLETED"
            assert operations.daily_report(tx, OPERATOR, "independent-report") == operations.daily_report(tx, OPERATOR, "independent-report")
    finally:
        reopened.close()


def test_concurrent_scheduler_processes_enqueue_one_durable_run(db):
    with db.transaction() as tx:
        enable(tx)
        due(tx)
    connections = [Database(db.url) for _ in range(4)]
    try:
        def tick(database):
            with database.transaction() as tx:
                return len(operations.schedule_due(tx))
        with ThreadPoolExecutor(max_workers=4) as workers:
            assert sum(workers.map(tick, connections)) == 1
        with db.transaction() as tx:
            assert len(tx.list("job")) == len(tx.list("schedule_run")) == 1
    finally:
        for database in connections:
            database.close()


@pytest.mark.parametrize("kind,config,expected", [
    ("factory", {"order_id": "existing-order"}, "factory_generate"),
    ("lead_rescore", {"lead_id": "existing-lead"}, "lead_rescore"),
])
def test_recurring_factory_and_scoring_only_enqueue_bounded_internal_work(db, kind, config, expected):
    with db.transaction() as tx:
        tx.put("service_order", "existing-order", {"status": "DRAFT"})
        tx.put("lead", "existing-lead", {"record_mode": "UNCLASSIFIED"})
        enable(tx, kind=kind, **config)
        due(tx)
        queued = operations.schedule_due(tx)
        assert len(queued) == 1 and queued[0]["kind"] == expected
        assert queued[0]["payload"]["schedule_id"] == "daily"
        assert tx.list("ai_trace") == tx.list("payment") == tx.list("email_intent") == []


def test_daily_limit_uses_stockholm_midnight_not_utc_and_no_missed_run_storm(db):
    with db.transaction() as tx:
        tx.now = now("2026-07-01T21:58:00+00:00")  # 23:58 Stockholm
        enable(tx, max_runs_per_day=1)
        due(tx)
        first = operations.schedule_due(tx)[0]
        finish(tx, first)
        tx.now = now("2026-07-01T21:59:00+00:00")
        due(tx)
        assert operations.schedule_due(tx) == []
        tx.now = now("2026-07-01T22:01:00+00:00")  # next Stockholm calendar day
        assert len(operations.schedule_due(tx)) == 1
        assert operations.schedule_due(tx) == []
        assert len(tx.list("schedule_run")) == 2
        assert tx.get("internal_schedule", "daily")["next_run_at"] == tx.now + 15 * 60


def test_reconfiguration_does_not_reset_daily_run_allowance(db):
    with db.transaction() as tx:
        enable(tx)
        due(tx)
        finish(tx, operations.schedule_due(tx)[0])
        enable(tx, reason="Review the same safe bounded schedule again")
        due(tx)
        assert operations.schedule_due(tx) == []
        assert operations.schedules(tx)[0]["runs_today"] == 1


def test_uncertain_current_run_blocks_the_next_interval_until_explicit_review(db):
    with db.transaction() as tx:
        enable(tx, max_runs_per_day=5)
        due(tx)
        original = operations.schedule_due(tx)[0]
        original["status"] = "uncertain"
        tx.put("job", original["id"], original)
        due(tx)
        assert operations.schedule_due(tx) == []
        assert operations.schedules(tx)[0]["recent_runs"][0]["status"] == "UNCERTAIN"


def test_daily_clock_preserves_local_time_over_dst_transition(db):
    with db.transaction() as tx:
        tx.now = now("2026-10-24T06:30:00+00:00")
        result = enable(tx, cadence="daily", daily_at="08:00")
        assert result["next_run_at"] == now("2026-10-25T07:00:00+00:00")
        assert datetime.fromtimestamp(result["next_run_at"], operations.ZONE).hour == 8


def test_schedule_change_pauses_old_jobs_and_fences_already_loaded_provider_context(db):
    with db.transaction() as tx:
        enable(tx)
        due(tx)
        original = operations.schedule_due(tx)[0]
        operations.validate_job(tx, original)
        operations.guard_scheduled(tx, 100)
        operations.configure_schedule(tx, "daily", {"enabled": False, "reason": REASON}, ADMIN)
        assert tx.get("job", original["id"])["status"] == "paused"
        with pytest.raises(PolicyError) as denied:
            operations.guard_scheduled(tx)
        assert denied.value.code == "schedule_authority_changed"
        with pytest.raises(PolicyError):
            operations.validate_job(tx, original)


def test_kill_clear_cannot_restart_schedule_without_new_explicit_admin_review(db):
    with db.transaction() as tx:
        enable(tx, max_runs_per_day=2)
        due(tx)
        original = operations.schedule_due(tx)[0]
        policy.set_kill_switch(tx, True, "Investigate an emergency condition", ADMIN)
        assert operations.schedule_due(tx) == []
        with pytest.raises(PolicyError):
            enable(tx)
        policy.set_kill_switch(tx, False, "Reviewed emergency; do not resume old work", ADMIN)
        due(tx)
        assert operations.schedules(tx)[0]["requires_reauthorization"] is True
        assert operations.schedule_due(tx) == []
        enable(tx, reason="Explicitly approve a fresh bounded internal schedule")
        due(tx)
        fresh = operations.schedule_due(tx)[0]
        assert fresh["id"] != original["id"]
        assert tx.get("job", original["id"])["status"] == "paused"
        with pytest.raises(PolicyError):
            operations.validate_job(tx, original)


def test_pending_costs_never_age_out_and_pre_dispatch_check_does_not_double_count(db):
    with db.transaction() as tx:
        enable(tx, max_cost_minor=150)
        due(tx)
        queued = operations.schedule_due(tx)[0]
        operations.validate_job(tx, queued)
        entry(tx, "yesterday-pending", state="RESERVED", amount=100,
              schedule_id="daily", created_at=tx.now - 40 * 86400)
        operations.guard_scheduled(tx, 50)
        with pytest.raises(PolicyError) as denied:
            operations.guard_scheduled(tx, 51)
        assert denied.value.code == "schedule_budget"
        entry(tx, "new-pending", state="RESERVED", amount=50, schedule_id="daily")
        operations.guard_scheduled(tx)  # reservation already counted
        assert operations.schedules(tx)[0]["cost_today_minor"] == 150
        entry(tx, "old-actual", amount=10000, schedule_id="daily", created_at=tx.now - 86400)
        assert operations.schedules(tx)[0]["cost_today_minor"] == 150


def test_forged_unknown_run_or_old_revision_never_grants_schedule_authority(db):
    with db.transaction() as tx:
        schedule = enable(tx)
        with pytest.raises(PolicyError) as denied:
            operations.validate_job(tx, {"payload": {"schedule_id": "daily", "schedule_revision": schedule["revision"],
                                                       "schedule_run_id": "untrusted"}})
        assert denied.value.code == "schedule_run_invalid"
        operations.validate_job(tx, {"payload": {}})
        assert tx.schedule_context is None


def test_scheduled_mission_tags_initial_and_persisted_work_before_execution(db):
    with db.transaction() as tx:
        enable(tx, kind="mission", goal="Prepare an internal sourced business opportunity report")
        due(tx)
        queued = operations.schedule_due(tx)
        assert len(queued) == 1 and queued[0]["kind"] == "internal_agent"
        mission = tx.list("business_mission")[0]
        assert mission["schedule_id"] == queued[0]["payload"]["schedule_id"] == "daily"
        assert mission["schedule_run_id"] == queued[0]["payload"]["schedule_run_id"]
        assert mission["refresh_sources"] is False
        assert mission["contact_authorized"] is mission["payment_authorized"] is False
        assert tx.list("ai_trace") == []


def test_policy_blocked_mission_cannot_starve_unrelated_scheduler_work(db):
    from jarvis_lite import engine
    with db.transaction() as tx:
        engine.configure_agent(tx, "ceo", {"enabled": False, "reason": REASON}, ADMIN)
        enable(tx, "a-mission", kind="mission", goal="Prepare an internal sourced business opportunity report")
        enable(tx, "z-report")
        due(tx, "a-mission")
        due(tx, "z-report")
        queued = operations.schedule_due(tx)
        assert len(queued) == 1 and queued[0]["kind"] == "operations_report"
        runs = tx.list("schedule_run")
        assert {run["status"] for run in runs} == {"BLOCKED", "QUEUED"}
        blocked = next(run for run in runs if run["status"] == "BLOCKED")
        assert blocked["error_code"] == "agent_paused"
        assert tx.list("business_mission") == tx.list("agent_task") == []
        assert next(item for item in operations.schedules(tx) if item["id"] == "a-mission")["recent_runs"][0]["status"] == "BLOCKED"
        assert operations.schedule_due(tx) == []


def test_analytics_uses_settled_actual_costs_and_signed_real_receipts_only(db):
    with db.transaction() as tx:
        tx.now = now("2026-10-05T10:00:00+00:00")
        entry(tx, "actual-ai", amount=125)
        entry(tx, "actual-hosting", amount=700, category="infrastructure")
        entry(tx, "not-an-actual-cost", state="RESERVED", amount=10000)
        entry(tx, "last-month", amount=50, created_at=now("2026-09-30T10:00:00+00:00"))
        lead = real_sourced_lead(tx)
        tx.put("lead", "demo", {**lead, "is_test": True})
        tx.put("lead", "unknown", {"company": "Missing provenance"})
        payment = tx.put("payment", "real-receipt", {"lead_id": lead["id"], "provider": "stripe", "livemode": True,
            "status": "CONFIRMED", "amount_minor": 500000, "total_minor": 625000,
            "provider_reference": "cs_verified", "confirmation_event_id": "signed", "confirmed_at": tx.now})
        tx.put("payment", "demo-receipt", {**payment, "lead_id": "demo", "provider": "demo", "amount_minor": 99999999})
        assert operations.analytics(tx)["verified_revenue_minor"] == 0
        tx.put("payment_event", "signed", {"payment_id": "real-receipt", "lead_id": lead["id"], "provider": "stripe",
            "livemode": True, "signature_verified": True, "status": "paid", "currency": "SEK",
            "amount_minor": 625000, "provider_reference": "cs_verified"})
        metrics = operations.analytics(tx)
        assert metrics["actual_cost_minor"] == 875
        assert metrics["reserved_cost_minor"] == 10000
        assert metrics["ai_cost_today_minor"] == metrics["ai_cost_month_minor"] == 125
        assert metrics["ai_cost_30d_minor"] == 175
        assert metrics["ai_calls"] == 2
        assert metrics["input_tokens"] == 22 and metrics["output_tokens"] == 14
        assert metrics["verified_leads"] == 1
        assert metrics["verified_revenue_minor"] == 500000
        assert metrics["net_outcome_minor"] == 499125
        assert len(metrics["series"]) == 30
        assert metrics["series"][-1]["date"] == "2026-10-05"
        assert metrics["series"][-1]["cost_minor"] == 825
        assert metrics["series"][-1]["net_outcome_minor"] == 499175


@pytest.mark.parametrize("revocation", ["approved", "license_reviewed", "version", "allowlist"])
def test_verified_lead_metric_excludes_revoked_unlicensed_stale_or_unlisted_sources(db, revocation):
    with db.transaction() as tx:
        lead = real_sourced_lead(tx)
        assert operations.analytics(tx)["verified_leads"] == 1
        if revocation == "allowlist":
            settings = policy.get_settings(tx)
            settings["approved_sources"] = []
            tx.put("settings", "global", settings)
        else:
            source = tx.get("research_source", "approved")
            source[revocation] = source["version"] + 1 if revocation == "version" else False
            tx.put("research_source", "approved", source)
        assert operations.analytics(tx)["verified_leads"] == 0
        assert tx.get("lead", lead["id"]) == lead  # historical evidence is retained


@pytest.mark.skipif(not os.getenv("JARVIS_TEST_DATABASE_URL"), reason="Requires an isolated local PostgreSQL maintenance server")
def test_postgres_v12_upgrade_preserves_all_rows_and_authority_after_reopen():
    supplied = os.environ["JARVIS_TEST_DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    parsed = urlsplit(supplied)
    name = "jarvis_v12_upgrade_test_" + secrets.token_hex(10)
    with psycopg.connect(supplied, autocommit=True, connect_timeout=10) as maintenance:
        maintenance.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        url = urlunsplit((parsed.scheme, parsed.netloc, "/" + name, parsed.query, parsed.fragment))
        database = None
        try:
            database = Database(url)
            with database.transaction() as tx:
                policy.initialize(tx)
                policy.set_secret(tx, "OPENROUTER_API_KEY", "synthetic-preserved-secret", ADMIN)
                for kind in ("user", "session", "lead", "payment", "suppress", "manual_task", "job", "custom_future_record"):
                    tx.put(kind, "retained", {"value": "unchanged", "role": "operator", "status": "paused"})
                tx.put("ai_config", "global", {"enabled": False, "model": "existing/model"})
                tx.audit("legacy", "retained.history", {"retained": True})
                tx.idempotency_put("legacy", "retained", {"input": 1}, {"output": 2})
                tx.delete("upgrade", operations.UPGRADE_ID)  # persisted v1.1 snapshot before first v1.2 startup
                before = [dict(row) for row in tx.conn.execute(select(records)).mappings()]
                settings = deepcopy(policy.get_settings(tx))
                operations.initialize(tx)
                operations.initialize(tx)
            database.close()
            database = Database(url)
            with database.transaction() as tx:
                after = {(row["kind"], row["id"]): dict(row) for row in tx.conn.execute(select(records)).mappings()}
                assert len(after) == len(before) + 1
                assert all(after[(row["kind"], row["id"])] == row for row in before)
                assert policy.get_settings(tx) == settings
                assert policy.get_secret(tx, "OPENROUTER_API_KEY") == "synthetic-preserved-secret"
                assert tx.idempotency_get("legacy", "retained", {"input": 1}) == {"output": 2}
                assert any(row["action"] == "retained.history" for row in tx.audits())
                assert operations.schedule_due(tx) == []
                assert tx.get("ai_config", "global")["enabled"] is False
                # Exercise the real PostgreSQL savepoint/queue path after
                # proving that startup left all existing rows untouched.
                from jarvis_lite import engine
                engine.configure_agent(tx, "ceo", {"enabled": False, "reason": REASON}, ADMIN)
                enable(tx, "a-mission", kind="mission", goal="Prepare an internal sourced business opportunity report")
                enable(tx, "z-report")
                due(tx, "a-mission")
                due(tx, "z-report")
                queued = operations.schedule_due(tx)
                assert len(queued) == 1 and queued[0]["kind"] == "operations_report"
                assert {run["status"] for run in tx.list("schedule_run")} == {"BLOCKED", "QUEUED"}
                assert tx.list("business_mission") == []
        finally:
            if database is not None:
                database.close()
            maintenance.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
