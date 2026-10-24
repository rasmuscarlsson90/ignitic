"""Delegation/ledger invariants with synthetic provider fixtures and no network."""
import json

import pytest

from jarvis_lite import ai, budget, engine, factory, intelligence, jobs, policy
from jarvis_lite.policy import PolicyError
from jarvis_lite.store import Database


ADMIN = {"id": "engine-admin", "role": "admin"}
OPERATOR = {"id": "engine-operator", "role": "operator"}


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_ENV", "test")
    monkeypatch.setenv("JARVIS_SESSION_SECRET", "engine-test-session-key")
    monkeypatch.setenv("JARVIS_ALLOW_PAID_AI", "false")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    database = Database("sqlite:///" + str(tmp_path / "engine.db"))
    with database.transaction() as tx:
        policy.initialize(tx)
    yield database
    database.close()


def mission(tx, **changes):
    result = engine.create_mission(tx, {"goal": "Prepare cited internal material for an incoming enquiry assistant",
        "source_ids": ["approved_fixture"], "lead_ids": [], "max_cost_minor": 2000, **changes}, OPERATOR, "mission-fixture")
    # Exercise a persisted v1.2 mission, whose existing seven-step graph and
    # historical statuses must remain compatible after the v1.3 upgrade.
    result.pop("workflow_version", None)
    return tx.put("business_mission", result["id"], result)


def real_fixture(tx, *, role, text, workflow_id, idempotency_key, actor):
    previous = tx.get("agent_result", idempotency_key)
    if previous:
        return previous
    content = json.dumps({"tasks": [{"role": name, "objective": "Prepare internally reviewed " + name + " evidence"}
                                   for name in engine.SPECIALISTS]}) if role == "ceo" and '"roles"' in text else "Actual mocked provider analysis of supplied test evidence."
    return tx.create("agent_result", {"provider": "openrouter", "simulated": False, "status": "COMPLETED",
        "role": role, "trace_id": "synthetic-trace-" + role, "output": {"action": "recommend", "text": content,
        "reason": "Synthetic test fixture, not actual provider usage"}}, id=idempotency_key)


def enable_fixtures(monkeypatch):
    monkeypatch.setattr(ai, "status", lambda tx: {"effective_paid_enabled": True})
    monkeypatch.setattr(ai, "task", real_fixture)

    def research(tx, mission, actor, key):
        return tx.create("research_report", {"status": "COMPLETED", "mission_id": mission["id"],
            "lead_ids": ["cited-fixture-lead"], "citations": [{"source_id": "approved_fixture",
            "source_url": "https://example.test/licensed-fixture", "evidence": "Explicit synthetic test evidence"}],
            "analysis": "Synthetic sourced test report", "trace_id": "synthetic-research-trace"})

    def sale(tx, lead_id, actor, key, *, mission_id):
        return tx.create("sales_offer", {"status": "DRAFT", "lead_id": lead_id,
            "mission_id": mission_id, "list_price_minor": 500000, "discount_bps": 0})

    def delivery(tx, mission, actor, key):
        return tx.create("service_result", {"status": "NEEDS_REVIEW", "mission_id": mission["id"],
            "markdown": "Synthetic generated draft with cited fixture evidence", "version": 1})

    monkeypatch.setattr(intelligence, "run_research", research)
    monkeypatch.setattr(intelligence, "prepare_sales", sale)
    monkeypatch.setattr(factory, "generate_for_mission", delivery)


def test_durable_bounded_graph_restart_and_internal_review(db, monkeypatch):
    enable_fixtures(monkeypatch)
    with db.transaction() as tx:
        created = mission(tx)
        assert mission(tx)["id"] == created["id"]
        assert len(tx.list("job")) == 1
    # Each worker tick opens new transactions and loads durable prerequisites.
    first = jobs.run_once(db)
    assert first["status"] == "completed"
    with db.transaction() as tx:
        assert len(tx.list("agent_task")) == 7
        assert len(tx.list("job")) == 2
        assert len(tx.list("business_plan")) == 1
        assert tx.list("business_plan")[0]["version"] == 1
    for _ in range(6):
        assert jobs.run_once(db)["status"] == "completed"
    assert jobs.run_once(db) is None
    with db.transaction() as tx:
        saved = tx.get("business_mission", created["id"])
        assert saved["status"] == "REVIEW_REQUIRED"
        assert saved["research_report_id"]
        assert saved["lead_ids"] == ["cited-fixture-lead"]
        assert {task["role"] for task in tx.list("agent_task")} == set(engine.ROLE_TOOLS)
        assert all(task["status"] == "COMPLETED" for task in tx.list("agent_task"))
        assert len(tx.list("job")) == 7
        action = tx.list("agent_action")[0]
        with pytest.raises(PolicyError, match="Administrator"):
            engine.review_action(tx, action["id"], "approve", "Checked all generated internal evidence", OPERATOR)
        engine.review_action(tx, action["id"], "approve", "Checked all generated internal evidence", ADMIN)
        assert tx.get("business_mission", created["id"])["status"] == "COMPLETED"
        assert tx.list("email_message") == tx.list("payment") == []
        assert saved["contact_authorized"] is saved["payment_authorized"] is False
        state = engine.agent_status(tx)
        assert len(state["roles"]) == 6 and sum(role["completed"] for role in state["roles"]) == 7


def test_disabled_ai_blocks_without_manufacturing_outputs_and_can_resume(db, monkeypatch):
    monkeypatch.setattr(ai, "task", lambda *a, **kw: pytest.fail("Disabled AI invoked an inference"))
    with db.transaction() as tx:
        created = mission(tx)
    assert jobs.run_once(db)["status"] == "paused"
    with db.transaction() as tx:
        assert tx.get("business_mission", created["id"])["status"] == "BLOCKED"
        assert tx.list("business_plan") == tx.list("agent_result") == []
        task = tx.list("agent_task")[0]
        assert task["status"] == "BLOCKED" and task["error_code"] == "ai_not_ready"
        with pytest.raises(PolicyError, match="Blocked or uncertain"):
            engine.review_action(tx, tx.list("agent_action")[0]["id"], "approve", "Attempt to approve missing output", ADMIN)
    enable_fixtures(monkeypatch)
    with db.transaction() as tx:
        engine.resume_mission(tx, created["id"], ADMIN, "Reviewed readiness and resumed the original request")
        assert tx.list("agent_task")[0]["id"] == task["id"]
        assert len(tx.list("job")) == 1
    assert jobs.run_once(db)["status"] == "completed"


def test_unknown_result_is_quarantined_and_cannot_get_a_new_key(db, monkeypatch):
    monkeypatch.setattr(ai, "status", lambda tx: {"effective_paid_enabled": True})
    calls = []

    def unknown(tx, **kwargs):
        calls.append(kwargs["idempotency_key"])
        return {"status": "UNKNOWN", "trace_id": "fixture-unknown", "simulated": False}

    monkeypatch.setattr(ai, "task", unknown)
    with db.transaction() as tx:
        created = mission(tx)
    assert jobs.run_once(db)["status"] == "uncertain"
    assert jobs.run_once(db) is None
    with db.transaction() as tx:
        task = tx.list("agent_task")[0]
        assert task["status"] == "UNCERTAIN"
        with pytest.raises(PolicyError, match="cannot be bypassed"):
            engine.resume_mission(tx, created["id"], ADMIN, "Must never bypass the uncertain provider outcome")
        replay = engine.execute_agent(tx, tx.list("job")[0])
        assert replay["status"] == "UNCERTAIN" and replay["idempotent"]
        assert tx.list("business_plan") == []
    assert len(calls) == 1


def test_unbounded_or_untrusted_model_plan_is_retained_for_review(db, monkeypatch):
    monkeypatch.setattr(ai, "status", lambda tx: {"effective_paid_enabled": True})
    monkeypatch.setattr(ai, "task", lambda *a, **kw: {"id": "fixture-invalid", "status": "COMPLETED",
        "provider": "openrouter", "simulated": False,
        "output": {"action": "recommend", "text": json.dumps({"tasks": [{"role": "shell", "objective": "send email"}]})}})
    with db.transaction() as tx:
        created = mission(tx)
    assert jobs.run_once(db)["status"] == "paused"
    with db.transaction() as tx:
        assert tx.get("business_mission", created["id"])["status"] == "REVIEW_REQUIRED"
        assert len(tx.list("agent_task")) == 1 and not tx.list("business_plan")
        assert tx.list("agent_task")[0]["output"]["code"] == "agent_plan_invalid"
        with pytest.raises(PolicyError, match="cannot be marked"):
            engine.review_action(tx, tx.list("agent_action")[0]["id"], "approve", "Cannot approve malformed model delegation", ADMIN)


def test_agent_budget_pending_liabilities_and_daily_call_caps(db):
    with db.transaction() as tx:
        created = mission(tx, max_cost_minor=200)
        tx.put("business_mission", created["id"], {**created, "status": "RUNNING"})
        entry = tx.create("budget_entry", {"state": "RESERVED", "reserved_sek_minor": 150,
            "workflow_id": "mission:" + created["id"] + ":sales", "role": "sales", "created_at": tx.now - 90000})
        tx.create("ai_trace", {"role": "sales", "reservation_id": entry["id"],
            "status": "UNKNOWN", "simulated": False, "created_at": tx.now - 90000})
        engine.configure_agent(tx, "sales", {"daily_budget_minor": 160, "reason": "Reviewed synthetic pending liability cap"}, ADMIN)
        with pytest.raises(PolicyError, match="daily budget"):
            engine.guard_ai_budget(tx, "sales", 20, missionid=created["id"])
        engine.guard_ai_budget(tx, "sales", 0, missionid=created["id"], reservation_active=True)
        engine.configure_agent(tx, "sales", {"daily_budget_minor": 1000, "reason": "Reviewed synthetic role cap and mission limit"}, ADMIN)
        with pytest.raises(PolicyError, match="mission budget"):
            engine.guard_ai_budget(tx, "sales", 51, missionid=created["id"])
        engine.guard_ai_budget(tx, "sales", 50, missionid=created["id"])
        engine.configure_agent(tx, "sales", {"max_calls_per_day": 1, "reason": "Reviewed one pending call limit for agent"}, ADMIN)
        with pytest.raises(PolicyError, match="call count"):
            engine.guard_ai_budget(tx, "sales", 1, missionid=created["id"])


def test_capabilities_pause_fences_and_preserves_financial_mandate(db):
    with db.transaction() as tx:
        created = mission(tx)
        initial = policy.get_settings(tx)
        for config in ({"tools": ["external_contact"]}, {"max_calls_per_day": 101}, {"enabled": "yes"}):
            with pytest.raises(PolicyError):
                engine.configure_agent(tx, "ceo", {**config, "reason": "Reviewed but forbidden synthetic configuration"}, ADMIN)
        engine.pause_agent(tx, "ceo", ADMIN)
        assert tx.list("job")[0]["status"] == "paused"
        with pytest.raises(PolicyError, match="paused"):
            engine.guard_ai_budget(tx, "ceo", 1)
        current = policy.get_settings(tx)
        assert current["minimum_list_price_minor"] == initial["minimum_list_price_minor"] == 500000
        assert current["rolling_tools_limit_minor"] == 50000 and current["total_budget_minor"] == 200000
        assert tx.get("business_mission", created["id"])["contact_authorized"] is False


def test_missing_sources_are_blocked_without_fake_companies(db, monkeypatch):
    monkeypatch.setattr(ai, "status", lambda tx: {"effective_paid_enabled": True})
    monkeypatch.setattr(ai, "task", real_fixture)
    with db.transaction() as tx:
        created = mission(tx, source_ids=[])
    assert jobs.run_once(db)["status"] == "completed"
    assert jobs.run_once(db)["status"] == "paused"
    with db.transaction() as tx:
        assert tx.get("business_mission", created["id"])["status"] == "BLOCKED"
        research = next(task for task in tx.list("agent_task") if task["role"] == "lead_research")
        assert research["error_code"] == "NO_APPROVED_SOURCES"
        assert tx.list("lead") == tx.list("research_report") == []


def test_schedule_context_propagates_to_every_delegation(db, monkeypatch):
    enable_fixtures(monkeypatch)
    context = {"schedule_id": "test-schedule", "schedule_revision": "fixture-revision", "schedule_run_id": "fixture-run"}
    # Scheduling authorization itself is separately tested in operations tests.
    from jarvis_lite import operations
    monkeypatch.setattr(operations, "validate_job", lambda tx, job: None)
    with db.transaction() as tx:
        created = engine.create_mission(tx, {"goal": "Prepare recurring internal evidence and reviewed drafts"}, OPERATOR,
            "scheduled-fixture", schedule_context=context)
        assert all(tx.list("job")[0]["payload"][name] == value for name, value in context.items())
    assert jobs.run_once(db)["status"] == "completed"
    with db.transaction() as tx:
        assert tx.get("business_mission", created["id"])["schedule_id"] == context["schedule_id"]
        assert len(tx.list("job")) == 2
        assert all(all(job["payload"][name] == value for name, value in context.items()) for job in tx.list("job"))


def test_failure_and_cancel_hooks_preserve_truthful_task_state(db):
    with db.transaction() as tx:
        created = mission(tx)
        job = tx.list("job")[0]
        engine.record_job_failure(tx, {**job, "status": "paused"}, "fixture_interruption")
        assert tx.list("agent_task")[0]["status"] == "BLOCKED"
        engine.record_job_cancel(tx, {**job, "status": "cancelled"})
        assert tx.list("agent_task")[0]["status"] == "FAILED"
        assert tx.get("business_mission", created["id"])["status"] == "CANCELLED"


def test_fresh_dispatch_rechecks_lowered_role_cap_without_double_reserve(db):
    with db.transaction() as tx:
        entry = tx.create("budget_entry", {"state": "RESERVED", "reserved_sek_minor": 150,
            "role": "sales", "workflow_id": "direct-api-fixture", "created_at": tx.now})
        tx.create("ai_trace", {"role": "sales", "reservation_id": entry["id"],
            "status": "ATTEMPTED", "created_at": tx.now, "simulated": False})
        engine.configure_agent(tx, "sales", {"daily_budget_minor": 150,
            "reason": "Reviewed the full active pending reserve before dispatch"}, ADMIN)
        engine.guard_ai_budget(tx, "sales", 150, reservation_active=True)
        engine.configure_agent(tx, "sales", {"per_call_limit_minor": 100,
            "reason": "Administrator lowered this role limit before provider dispatch"}, ADMIN)
        with pytest.raises(PolicyError, match="per-call"):
            engine.guard_ai_budget(tx, "sales", 150, reservation_active=True)
        engine.configure_agent(tx, "sales", {"per_call_limit_minor": 200, "daily_budget_minor": 149,
            "reason": "Administrator lowered daily liability cap before provider dispatch"}, ADMIN)
        with pytest.raises(PolicyError, match="daily budget"):
            engine.guard_ai_budget(tx, "sales", 150, reservation_active=True)


def test_generic_retry_hook_does_not_make_retry_impossible(db):
    with db.transaction() as tx:
        created = mission(tx)
        engine.record_job_failure(tx, {**tx.list("job")[0], "status": "queued"}, "TransientLocalFixture")
        assert tx.list("agent_task")[0]["status"] == "QUEUED"
        assert tx.get("business_mission", created["id"])["status"] == "RUNNING"
        assert tx.list("agent_action") == []
