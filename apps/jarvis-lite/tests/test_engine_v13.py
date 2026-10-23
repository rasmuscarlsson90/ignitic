"""Durable coordination controls: synthetic internal artifacts, no provider I/O."""
import json
from typing import Any

import pytest

from jarvis_lite import ai, engine, factory, intelligence, jobs, policy
from jarvis_lite.policy import PolicyError
from jarvis_lite.store import Database


ADMIN = {"id": "v13-engine-admin", "role": "admin"}
OPERATOR = {"id": "v13-engine-operator", "role": "operator"}
REASON = "Reviewed isolated internal coordination and cost limits"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_ENV", "test")
    monkeypatch.setenv("JARVIS_SESSION_SECRET", "v13-engine-tests-only-session")
    monkeypatch.setenv("JARVIS_ALLOW_PAID_AI", "false")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    database = Database(f"sqlite:///{tmp_path / 'v13-engine.sqlite'}")
    with database.transaction() as tx:
        policy.initialize(tx)
    yield database
    database.close()


def run_job(db):
    result = jobs.run_once(db)
    assert isinstance(result, dict)
    return result


def create(tx, key="bounded-mission", **changes):
    return engine.create_mission(tx, {"goal": "Prepare an internal business plan from explicitly approved evidence",
        "source_ids": ["approved-fixture"], "deliverable_kind": "content_calendar", **changes}, OPERATOR, key)


def enable(monkeypatch, *, minor=False, major=False):
    order = []
    monkeypatch.setattr(ai, "status", lambda tx: {"effective_paid_enabled": True, "config": {"max_input_chars": 12000}})

    def actual_model_fixture(tx, *, role, text, workflow_id, idempotency_key, actor):
        context = dict(tx.agent_context)
        step = tx.get("agent_task", context["task_id"])["step"]
        order.append((role, step))
        previous = tx.get("agent_result", idempotency_key)
        if previous:
            return previous
        output: Any
        if step == "plan":
            assert context["response_contract"] == "ceo_plan"
            output = {"tasks": [{"role": value, "objective": "Prepare internally reviewed " + value + " material"} for value in engine.SPECIALISTS]}
        elif role == "compliance":
            assert context["response_contract"] == "compliance_qa"
            output = {"verdict": "pass", "findings": [], "summary": "Review the supplied internal evidence and limitations.",
                      "source_ids": ["approved-fixture"]}
        else:
            output = "Analysis of supplied internal facts, including explicit unknown delivery costs."
        return tx.create("agent_result", {"status": "COMPLETED", "provider": "openrouter", "simulated": False,
            "trace_id": "fixture-trace-" + step, "output": {"action": "recommend",
            "text": json.dumps(output) if isinstance(output, dict) else output, "reason": REASON}}, id=idempotency_key)

    def research(tx, mission, actor, key):
        order.append(("lead_research", "lead_research"))
        return tx.create("research_report", {"mission_id": mission["id"], "status": "COMPLETED", "lead_ids": [],
            "citations": [], "source_ids": ["approved-fixture"], "analysis": "Synthetic supplied document research, not a company lead"})

    def strategy(tx, mission, actor, key):
        order.append(("sales", "sales"))
        return tx.create("opportunity_report", {"mission_id": mission["id"], "status": "COMPLETED",
            "opportunities": [], "summary": "No company identity was invented; internal opportunities require review"})

    def make_result(tx, mission, *, revised=False):
        existing = next((value for value in tx.list("service_order") if value.get("mission_id") == mission["id"]), None)
        if existing:
            source = existing
        else:
            source = tx.create("service_order", {"mission_id": mission["id"], "kind": "content_calendar", "workflow_version": "1.3",
                "evidence": [], "generation_state": "COMPLETED", "auto_revision_count": 0})
        result = tx.create("service_result", {"order_id": source["id"], "mission_id": mission["id"], "kind": source["kind"],
            "version": 2 if revised else 1, "status": "NEEDS_REVIEW", "simulated": False, "external_action": False,
            "quality": {"issues": ["unattributed_external_url"] if major else []},
            "content": {"title": "Internal content plan", "sections": [
                {"key": key, "content": "Use the supplied brief to prepare a specific unsent draft and measure its review outcome.",
                 "source_ids": ["BRIEF"]} for key in factory.TYPES[source["kind"]]["sections"]],
                "limitations": ["None"] if minor and not revised else ["This plan uses only the operator brief and does not verify market demand."]}})
        source.update(latest_result_id=result["id"], auto_revision_count=1 if revised else 0)
        tx.put("service_order", source["id"], source)
        return result

    def delivery(tx, mission, actor, key):
        order.append(("delivery", "delivery"))
        return make_result(tx, mission)

    def revise(tx, mission, actor, key, review_id):
        order.append(("delivery", "delivery_revision"))
        assert tx.agent_context["role"] == "delivery"
        assert tx.get("quality_review", review_id)["severity"] == "minor"
        return make_result(tx, mission, revised=True)

    monkeypatch.setattr(ai, "task", actual_model_fixture)
    monkeypatch.setattr(intelligence, "run_research", research)
    monkeypatch.setattr(intelligence, "prepare_strategy", strategy)
    monkeypatch.setattr(factory, "generate_for_mission", delivery)
    monkeypatch.setattr(factory, "revise_for_mission", revise)
    return order


def test_seven_steps_post_delivery_qa_saved_summary_and_exact_internal_review(db, monkeypatch):
    sequence = enable(monkeypatch)
    with db.transaction() as tx:
        mission = create(tx)
        assert create(tx)["id"] == mission["id"]
        assert tx.list("job")[0]["max_attempts"] == 2
        assert mission["workflow_version"] == "1.3"
    for _ in range(7):
        assert run_job(db)["status"] == "completed"
    assert jobs.run_once(db) is None
    assert [role for role, _ in sequence] == ["ceo", "lead_research", "sales", "delivery", "finance", "compliance", "ceo"]
    with db.transaction() as tx:
        saved = tx.get("business_mission", mission["id"])
        assert saved["status"] == "REVIEW_REQUIRED" and saved["quality_passed"]
        assert tx.get("mission_summary", saved["summary_id"])["content"]
        assert len(tx.list("quality_review")) == 1
        action = tx.list("agent_action")[0]
        engine.review_action(tx, action["id"], "approve", REASON, ADMIN)
        assert tx.get("business_mission", mission["id"])["status"] == "COMPLETED"
        assert tx.list("email_message") == tx.list("payment") == []


def test_one_minor_revision_has_nine_stable_tasks_and_rechecks_before_summary(db, monkeypatch):
    sequence = enable(monkeypatch, minor=True)
    with db.transaction() as tx:
        mission = create(tx)
    for _ in range(9):
        assert run_job(db)["status"] == "completed"
    with db.transaction() as tx:
        saved = tx.get("business_mission", mission["id"])
        assert saved["revision_count"] == 1 and saved["quality_passed"]
        assert len(tx.list("agent_task")) == len(tx.list("job")) == 9
        assert len(tx.list("service_result")) == 2
        assert len(tx.list("quality_review")) == 2
        assert sequence[-3:] == [("delivery", "delivery_revision"), ("compliance", "compliance_recheck"), ("ceo", "summary")]
        assert saved["status"] == "REVIEW_REQUIRED"


@pytest.mark.parametrize("changes,major", [({"max_steps": 7}, False), ({"max_revisions": 0}, False), ({}, True)])
def test_major_or_exhausted_quality_cannot_loop_or_be_approved(db, monkeypatch, changes, major):
    enable(monkeypatch, minor=not major, major=major)
    with db.transaction() as tx:
        mission = create(tx, **changes)
    for _ in range(5):
        assert run_job(db)["status"] == "completed"
    blocked = run_job(db)
    assert blocked["status"] == "paused"
    assert jobs.run_once(db) is None
    with db.transaction() as tx:
        saved = tx.get("business_mission", mission["id"])
        assert saved["status"] == "REVIEW_REQUIRED" and saved["revision_count"] == 0
        assert len(tx.list("agent_task")) == 7
        assert not tx.list("mission_summary")
        with pytest.raises(PolicyError, match="cannot be marked"):
            engine.review_action(tx, tx.list("agent_action")[0]["id"], "approve", REASON, ADMIN)


def test_disabled_ai_pauses_without_fabrication_and_resume_keeps_original_ids(db, monkeypatch):
    with db.transaction() as tx:
        mission = create(tx)
    result = run_job(db)
    assert result["status"] == "paused"
    with db.transaction() as tx:
        saved = tx.get("business_mission", mission["id"])
        original_job = tx.list("job")[0]
        assert saved["status"] == "PAUSED" and saved["blocker"]["code"] == "ai_not_ready"
        assert tx.list("agent_result") == []
    enable(monkeypatch)
    with db.transaction() as tx:
        engine.resume_mission(tx, mission["id"], ADMIN, REASON)
        assert tx.list("job")[0]["id"] == original_job["id"]
        assert len(tx.list("job")) == len(tx.list("agent_task")) == 1
    assert run_job(db)["status"] == "completed"


def test_cancel_fences_entire_graph_preserves_outputs_and_is_idempotent(db, monkeypatch):
    enable(monkeypatch)
    with db.transaction() as tx:
        mission = create(tx)
    assert run_job(db)["status"] == "completed"
    with db.transaction() as tx:
        first_plan = tx.list("business_plan")[0]
        cancelled = engine.cancel_mission(tx, mission["id"], ADMIN, REASON)
        assert cancelled["status"] == "CANCELLED"
        assert engine.cancel_mission(tx, mission["id"], ADMIN, REASON) == cancelled
        assert tx.list("business_plan")[0] == first_plan
        assert all(job["status"] in {"completed", "cancelled"} for job in tx.list("job"))
        assert all(task["status"] in {"COMPLETED", "CANCELLED"} for task in tx.list("agent_task"))
    assert jobs.run_once(db) is None


def test_pause_resume_preserves_completed_steps_and_unqueued_dependencies(db, monkeypatch):
    enable(monkeypatch)
    with db.transaction() as tx:
        mission = create(tx)
    assert run_job(db)["status"] == "completed"
    with db.transaction() as tx:
        original = tx.list("agent_task")
        engine.pause_mission(tx, mission["id"], ADMIN, REASON)
        assert sum(task["status"] == "COMPLETED" for task in tx.list("agent_task")) == 1
        assert sum(task["status"] == "PAUSED" for task in tx.list("agent_task")) == 6
        engine.resume_mission(tx, mission["id"], ADMIN, REASON)
        assert {task["id"] for task in tx.list("agent_task")} == {task["id"] for task in original}
    for _ in range(6):
        assert run_job(db)["status"] == "completed"


def test_unknown_provider_outcome_cannot_be_hidden_by_cancel_or_resume(db, monkeypatch):
    monkeypatch.setattr(ai, "status", lambda tx: {"effective_paid_enabled": True})
    monkeypatch.setattr(ai, "task", lambda *args, **kwargs: {"status": "UNKNOWN", "trace_id": "synthetic-unknown", "simulated": False})
    with db.transaction() as tx:
        mission = create(tx)
    assert run_job(db)["status"] == "uncertain"
    with db.transaction() as tx:
        saved = tx.get("business_mission", mission["id"])
        assert saved["status"] == "REVIEW_REQUIRED" and saved["uncertain_outcome"]
        for operation in (engine.cancel_mission, engine.resume_mission, engine.pause_mission):
            with pytest.raises(PolicyError):
                operation(tx, mission["id"], ADMIN, REASON)
        assert len(tx.list("job")) == 1 and len(tx.list("agent_task")) == 1


def test_expired_mission_fails_without_spending_and_cannot_extend_on_resume(db):
    with db.transaction() as tx:
        mission = create(tx, deadline_seconds=60)
        tx.now += 61
        expired = engine.expire_mission(tx, mission["id"])
        assert expired["status"] == "FAILED" and expired["blocker"]["code"] == "mission_deadline"
        assert tx.list("job")[0]["status"] == "cancelled"
        assert tx.list("agent_task")[0]["status"] == "FAILED"
        with pytest.raises(PolicyError):
            engine.resume_mission(tx, mission["id"], ADMIN, REASON)
        assert not tx.list("budget_entry") and not tx.list("ai_trace")


def test_fresh_budget_guard_checks_deadline_and_pending_tool_liability(db):
    with db.transaction() as tx:
        mission = create(tx, max_cost_minor=120, deadline_seconds=60)
        tx.put("budget_entry", "held-tool", {"state": "RESERVED", "reserved_sek_minor": 110,
            "role": "lead_research", "mission_id": mission["id"], "created_at": tx.now - 90000})
        with pytest.raises(PolicyError) as exhausted:
            engine.guard_tool_budget(tx, "lead_research", 11, mission["id"])
        assert exhausted.value.code == "mission_budget"
        engine.guard_tool_budget(tx, "lead_research", 110, mission["id"], reservation_active=True)
        tx.now += 61
        with pytest.raises(PolicyError) as expired:
            engine.guard_ai_budget(tx, "ceo", 1, mission["id"])
        assert expired.value.code == "mission_deadline"


def test_completion_after_deadline_retains_actual_cost_but_cannot_delegate(db, monkeypatch):
    enable(monkeypatch)
    original_model = ai.task

    def slow_model(tx, **kwargs):
        result = original_model(tx, **kwargs)
        mission_id = tx.agent_context["mission_id"]
        tx.create("budget_entry", {"mission_id": mission_id, "role": "ceo", "state": "SETTLED",
            "actual_sek_minor": 7, "created_at": tx.now})
        tx.now = tx.get("business_mission", mission_id)["deadline_at"] + 1
        return result

    monkeypatch.setattr(ai, "task", slow_model)
    with db.transaction() as tx:
        mission = create(tx, deadline_seconds=60)
    assert run_job(db)["status"] == "failed"
    with db.transaction() as tx:
        saved = tx.get("business_mission", mission["id"])
        assert saved["status"] == "FAILED" and saved["blocker"]["code"] == "mission_deadline"
        assert engine._mission_cost(tx, mission["id"]) == 7
        assert len(tx.list("agent_result")) == 1
        assert len(tx.list("agent_task")) == len(tx.list("job")) == 1
        assert tx.list("business_plan") == []


def test_agent_working_requires_valid_lease_epoch_and_mission(db):
    with db.transaction() as tx:
        mission = create(tx)
        task = tx.list("agent_task")[0]
        job = tx.list("job")[0]
        tx.put("business_mission", mission["id"], {**mission, "status": "RUNNING"})
        tx.put("agent_task", task["id"], {**task, "status": "RUNNING"})
        tx.put("job", job["id"], {**job, "status": "leased", "lease_until": tx.now + 30})
        assert engine.agent_detail(tx, "ceo")["agent"]["state"] == "Working"
        tx.now += 31
        assert engine.agent_detail(tx, "ceo")["agent"]["state"] == "Waiting"
        tx.put("job", job["id"], {**job, "status": "leased", "lease_until": tx.now + 30, "safety_epoch": -1})
        assert engine.agent_detail(tx, "ceo")["agent"]["state"] != "Working"


@pytest.mark.parametrize("change", ["revoked", "version", "content", "latest"])
def test_mission_approval_rechecks_exact_current_quality_proof(db, monkeypatch, change):
    enable(monkeypatch)
    with db.transaction() as tx:
        tx.put("research_source", "approved-fixture", {"approved": True, "license_reviewed": True, "version": "original"})
        settings = policy.get_settings(tx)
        tx.put("settings", "global", {**settings, "approved_sources": [*settings["approved_sources"], "approved-fixture"]})
        mission = create(tx)
    for _ in range(7):
        assert run_job(db)["status"] == "completed"
    with db.transaction() as tx:
        result = tx.list("service_result")[0]
        if change in {"revoked", "version"}:
            source = tx.get("research_source", "approved-fixture")
            source["approved" if change == "revoked" else "version"] = False if change == "revoked" else "changed-version"
            tx.put("research_source", source["id"], source)
        elif change == "content":
            tx.put("service_result", result["id"], {**result, "content": {**result["content"], "title": "Changed after QA"}})
        else:
            revised = tx.create("service_result", {**result, "version": 2}, id="later-result")
            order = tx.get("service_order", result["order_id"])
            tx.put("service_order", order["id"], {**order, "latest_result_id": revised["id"]})
        with pytest.raises(PolicyError) as stale:
            engine.review_action(tx, tx.list("agent_action")[0]["id"], "approve", REASON, ADMIN)
        assert stale.value.code == "quality_snapshot_changed"
        assert tx.get("business_mission", mission["id"])["status"] == "REVIEW_REQUIRED"
        assert tx.list("agent_action")[0]["status"] == "PENDING"


def test_expired_attempt_with_unknown_provider_outcome_cannot_be_cancelled(db):
    with db.transaction() as tx:
        mission = create(tx, deadline_seconds=60)
        task = tx.list("agent_task")[0]
        job = tx.list("job")[0]
        tx.put("job", job["id"], {**job, "status": "leased", "lease_until": tx.now - 1, "attempts": 1})
        tx.put("ai_trace", "pending-attempt", {"status": "ATTEMPTED", "mission_id": mission["id"], "task_id": task["id"],
            "role": "ceo", "simulated": False})
        with pytest.raises(PolicyError):
            jobs.cancel(tx, job["id"], actor=ADMIN, reason=REASON)
        with pytest.raises(PolicyError):
            engine.cancel_mission(tx, mission["id"], ADMIN, REASON)
        tx.now += 61
        assert engine.expire_mission(tx, mission["id"])["status"] == "QUEUED"
        assert engine.mission_detail(tx, mission["id"])["mission"]["uncertain_outcome"]


def test_rejecting_internal_review_cannot_be_bypassed_with_resume(db, monkeypatch):
    enable(monkeypatch)
    with db.transaction() as tx:
        mission = create(tx)
    for _ in range(7):
        assert run_job(db)["status"] == "completed"
    with db.transaction() as tx:
        engine.review_action(tx, tx.list("agent_action")[0]["id"], "reject", REASON, ADMIN)
        assert tx.get("business_mission", mission["id"])["status"] == "FAILED"
        with pytest.raises(PolicyError):
            engine.resume_mission(tx, mission["id"], ADMIN, REASON)


def test_legacy_idempotency_and_history_are_unchanged_and_missing_raw_is_explained(db):
    with db.transaction() as tx:
        payload = {"goal": "Historical internal plan retained without rewriting", "source_ids": [], "lead_ids": [],
            "deliverable_kind": "market_analysis", "max_cost_minor": 2000, "priority": 50, "refresh_sources": False}
        old = tx.put("business_mission", "legacy", {**payload, "status": "REVIEW_REQUIRED", "plan_version": 0})
        tx.idempotency_put("engine.mission", "legacy-request", payload, {"mission_id": "legacy"})
        task = tx.put("agent_task", "legacy-task", {"mission_id": "legacy", "role": "ceo", "status": "BLOCKED",
            "output": {"status": "REVIEW_REQUIRED", "output": {"action": "escalate", "text": "AI response requires manual review.",
                "reason": "The structured response failed the action/shape policy."}}})
        assert engine.create_mission(tx, payload, OPERATOR, "legacy-request") == old
        view = engine.mission_detail(tx, "legacy")
        assert view["mission"]["blocker"]["code"] == "legacy_response_unavailable"
        with pytest.raises(PolicyError) as unavailable:
            engine.resume_mission(tx, "legacy", ADMIN, REASON)
        assert unavailable.value.code == "legacy_response_unavailable"
        assert tx.get("business_mission", "legacy") == old and tx.get("agent_task", "legacy-task") == task


@pytest.mark.parametrize("changes", [{"max_steps": 6}, {"max_steps": 10}, {"max_retries": 3},
    {"max_revisions": 2}, {"deadline_seconds": 59}, {"deadline_seconds": 86401}, {"max_steps": True}])
def test_mission_limits_are_explicitly_bounded(db, changes):
    with db.transaction() as tx, pytest.raises(PolicyError):
        create(tx, **changes)
