"""Failed-step recovery preserves mission history and uses bounded real adapters offline."""
from copy import deepcopy
import json

import httpx
import pytest

from jarvis_lite import ai, budget, engine, jobs, policy, research
from jarvis_lite.policy import PolicyError
from jarvis_lite.store import Database, Transaction


ADMIN = {"id": "v14-recovery-admin", "role": "admin"}
OPERATOR = {"id": "v14-recovery-operator", "role": "operator"}
REASON = "Reviewed current model limits and bounded internal mission recovery"
SOURCE = "recovery-evidence"


def approve(tx):
    ai.activate(tx, {"statement": ai.ACTIVATE, "config_revision": ai._config(tx)["revision"],
                     "reason": REASON}, ADMIN)


@pytest.fixture
def failed(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_ENV", "test")
    monkeypatch.setenv("KILL_SWITCH", "false")
    monkeypatch.setenv("JARVIS_ALLOW_PAID_AI", "true")
    monkeypatch.setenv("JARVIS_ALLOW_RESEARCH_NETWORK", "false")
    monkeypatch.setenv("JARVIS_ALLOW_LIVE_CONTACT", "false")
    monkeypatch.setenv("JARVIS_ALLOW_LIVE_PAYMENTS", "false")
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-v14-recovery-openrouter-key")
    monkeypatch.setenv("JARVIS_SESSION_SECRET", "synthetic-v14-recovery-session")
    database = Database("sqlite:///" + str(tmp_path / "recovery-v14.sqlite"))
    state = {"truncated": True, "requests": []}

    def provider(request):
        body = json.loads(request.content)
        state["requests"].append(body)
        content = '{"action":"recommend","text":' if state["truncated"] else json.dumps({
            "action": "recommend", "text": "Actual recorded costs remain costs; drafts are not revenue. Unknown labour and hosting costs prevent a verified margin.",
            "reason": "Review of supplied ledger only; no external authority"})
        return httpx.Response(200, json={"id": "offline-finance-" + str(len(state["requests"])),
            "usage": {"prompt_tokens": 80, "completion_tokens": 512 if state["truncated"] else 60,
                      "cost": "0.001"}, "choices": [{"finish_reason": "length" if state["truncated"] else "stop",
                "message": {"content": content}}]})

    original_adapter = ai.OpenRouterAdapter

    class OfflineAdapter(original_adapter):
        def __init__(self, key):
            super().__init__(key, transport=httpx.MockTransport(provider))

    monkeypatch.setattr(ai, "OpenRouterAdapter", OfflineAdapter)
    with database.transaction() as tx:
        policy.initialize(tx)
        research.configure_source(tx, SOURCE, {"name": "Permitted recovery evidence", "kind": "upload",
            "url": "https://example.com/recovery-evidence", "approved": True, "license_reviewed": True,
            "legal_review_reference": REASON, "cost_evidence": "Operator supplied licensed source has no fetch fee"}, ADMIN)
        budget.set_fx_rate(tx, currency="USD", sek_per_unit="12", source=REASON, observed_at=tx.now, actor=ADMIN)
        ai.configure(tx, {"enabled": True, "max_output_tokens": 512, "max_retries": 0, "reason": REASON}, ADMIN)
        approve(tx)
        mission = engine.create_mission(tx, {"goal": "Analyze supplied source evidence and saved internal business results",
            "source_ids": [SOURCE], "deliverable_kind": "business_plan", "max_cost_minor": 2000},
            OPERATOR, "recovery-fixture-mission")
        plan = tx.get("agent_task", engine._task_id(mission["id"], "plan"))
        sequence = [("ceo", "plan"), ("lead_research", "lead_research"), ("sales", "sales"),
                    ("delivery", "delivery"), ("finance", "finance"), ("compliance", "compliance"), ("ceo", "summary")]
        previous = None
        completed = []
        for number, (role, step) in enumerate(sequence):
            task = plan if number == 0 else engine._new_task(tx, mission, role, step,
                "Prepare the reviewed internal " + step + " evidence", [previous])
            if number < 4:
                if not task.get("job_id"):
                    task = engine._enqueue(tx, mission, task)
                job = tx.get("job", task["job_id"])
                tx.put("job", job["id"], {**job, "status": "completed", "result": {"status": "COMPLETED"}})
                task.update(status="COMPLETED", output={"status": "COMPLETED", "report_id": "saved-" + step,
                    "trace_id": "saved-trace-" + step}, finished_at=tx.now)
                tx.put("agent_task", task["id"], task)
                completed.append(deepcopy(task))
            elif role == "finance":
                task = engine._enqueue(tx, mission, task)
                failed_id = task["id"]
            previous = task["id"]
        mission["status"] = "RUNNING"
        tx.put("business_mission", mission["id"], mission)
    result = jobs.run_once(database)
    assert result["status"] == "paused"
    with database.transaction() as tx:
        stopped = tx.get("agent_task", failed_id)
        trace_id = stopped["output"]["trace_id"]
        assert stopped["error_code"] == "ai_output_truncated"
        assert tx.get("ai_trace", trace_id)["status"] == "REVIEW_REQUIRED"
        prior = {"job": tx.get("job", stopped["job_id"]), "task": deepcopy(stopped),
                 "trace": tx.get("ai_trace", trace_id), "results": tx.list("agent_result"),
                 "entries": tx.list("budget_entry")}
        ai.configure(tx, {"max_output_tokens": 4096, "reason": REASON}, ADMIN)
        approve(tx)
    yield {"db": database, "mission_id": mission["id"], "task_id": failed_id, "trace_id": trace_id,
           "state": state, "completed": completed, "prior": prior}
    database.close()


def retry(tx, fixture, key="reviewed-recovery", actor=ADMIN):
    return engine.retry_failed_step(tx, fixture["mission_id"], actor, REASON, key,
        expected_task_id=fixture["task_id"], expected_trace_id=fixture["trace_id"])


def assert_history(tx, fixture):
    for task in fixture["completed"]:
        assert tx.get("agent_task", task["id"]) == task
    prior = fixture["prior"]
    assert tx.get("job", prior["job"]["id"]) == prior["job"]
    assert tx.get("ai_trace", fixture["trace_id"]) == prior["trace"]
    for result in prior["results"]:
        assert tx.get("agent_result", result["id"]) == result
    for entry in prior["entries"]:
        assert tx.get("budget_entry", entry["id"]) == entry


def test_retry_uses_4096_and_continues_only_unfinished_steps(failed, monkeypatch):
    database = failed["db"]
    failed["state"]["truncated"] = False
    with database.transaction() as tx:
        recovered = retry(tx, failed)
        assert recovered["id"] == failed["mission_id"]
        assert recovered["status"] in {"QUEUED", "RUNNING"}
        assert_history(tx, failed)
        assert len(tx.list("mission_retry")) == 1
    assert jobs.run_once(database)["status"] == "completed"
    with database.transaction() as tx:
        assert tx.get("agent_task", failed["task_id"])["status"] == "COMPLETED"
        assert_history(tx, failed)
        traces = tx.list("ai_trace")
        assert len(traces) == 2 and traces[0]["id"] != traces[1]["id"]
        assert len(tx.list("budget_entry")) == 2
        assert engine._mission_cost(tx, failed["mission_id"]) == 24
        assert tx.list("mission_retry")[0]["prior_output"] == failed["prior"]["task"]["output"]
    assert [request["max_tokens"] for request in failed["state"]["requests"]] == [512, 4096]
    downstream = []
    original_handler = engine._handler

    def complete_remaining(tx, mission, task):
        downstream.append(task["step"])
        assert task["role"] in {"compliance", "ceo"}
        return {"status": "COMPLETED", "report_id": "saved-after-retry-" + task["step"]}

    monkeypatch.setattr(engine, "_handler", complete_remaining)
    assert jobs.run_once(database)["status"] == "completed"
    assert jobs.run_once(database)["status"] == "completed"
    assert jobs.run_once(database) is None
    assert downstream == ["compliance", "summary"]
    monkeypatch.setattr(engine, "_handler", original_handler)
    with database.transaction() as tx:
        assert_history(tx, failed)
        tasks = [item for item in tx.list("agent_task") if item["mission_id"] == failed["mission_id"]]
        assert len(tasks) == 7 and all(item["status"] == "COMPLETED" for item in tasks)
        assert tx.get("business_mission", failed["mission_id"])["status"] == "REVIEW_REQUIRED"
        assert tx.list("payment") == tx.list("email_message") == []


def test_persistent_truncation_remains_review_and_manual_retry_bound(failed):
    database = failed["db"]
    for number in range(2):
        with database.transaction() as tx:
            retry(tx, failed, key="reviewed-recovery-" + str(number))
        assert jobs.run_once(database)["status"] == "paused"
        with database.transaction() as tx:
            task = tx.get("agent_task", failed["task_id"])
            assert task["status"] == "REVIEW_REQUIRED" and task["error_code"] == "ai_output_truncated"
            assert tx.get("business_mission", failed["mission_id"])["status"] == "REVIEW_REQUIRED"
            failed["trace_id"] = task["output"]["trace_id"]
            assert len(tx.list("ai_trace")) == number + 2
    with database.transaction() as tx:
        with pytest.raises(PolicyError) as raised:
            retry(tx, failed, key="exceeds-two-retries")
        assert "retry" in raised.value.code or "recovery" in raised.value.code
        assert len(tx.list("mission_retry")) == 2
        assert sum(item["status"] == "COMPLETED" for item in tx.list("agent_task")) == 4
    assert len(failed["state"]["requests"]) == 3


def test_duplicate_retry_returns_original_intent_and_stale_new_key_cannot_dispatch(failed):
    database = failed["db"]
    with database.transaction() as tx:
        first = retry(tx, failed)
        again = retry(tx, failed)
        assert again["id"] == first["id"]
        assert len(tx.list("mission_retry")) == 1
        assert len([j for j in tx.list("job") if j["status"] == "queued"]) == 1
        with pytest.raises(PolicyError):
            retry(tx, failed, key="another-click-same-old-trace")
        assert len(tx.list("mission_retry")) == 1
    assert len(failed["state"]["requests"]) == 1


def test_duplicate_retry_after_step_completion_cannot_repeat_paid_work(failed):
    failed["state"]["truncated"] = False
    with failed["db"].transaction() as tx:
        first = retry(tx, failed)
    assert jobs.run_once(failed["db"])["status"] == "completed"
    with failed["db"].transaction() as tx:
        repeated = retry(tx, failed)
        assert repeated["retry_id"] == first["retry_id"] and repeated["idempotent"]
        assert len(tx.list("mission_retry")) == 1
        assert len(tx.list("ai_trace")) == 2
    assert len(failed["state"]["requests"]) == 2


def test_paused_mission_retry_restores_only_unstarted_downstream_tasks(failed):
    with failed["db"].transaction() as tx:
        engine.pause_mission(tx, failed["mission_id"], ADMIN, REASON)
        assert tx.get("business_mission", failed["mission_id"])["status"] == "PAUSED"
        retry(tx, failed)
        tasks = [item for item in tx.list("agent_task") if item["mission_id"] == failed["mission_id"]]
        assert sum(item["status"] == "COMPLETED" for item in tasks) == 4
        assert all(item["status"] == "QUEUED" for item in tasks if item["status"] != "COMPLETED")
        assert_history(tx, failed)


def test_preexisting_paused_downstream_jobs_resume_after_dependencies_with_same_ids(failed, monkeypatch):
    database = failed["db"]
    failed["state"]["truncated"] = False
    downstream_jobs = {}
    with database.transaction() as tx:
        mission = tx.get("business_mission", failed["mission_id"])
        for step in ("compliance", "summary"):
            task = tx.get("agent_task", engine._task_id(mission["id"], step))
            task = engine._enqueue(tx, mission, task)
            downstream_jobs[step] = task["job_id"]
            jobs.pause(tx, task["job_id"], actor=ADMIN, reason=REASON)
        retry(tx, failed)
        assert all(tx.get("job", job_id)["status"] == "paused" for job_id in downstream_jobs.values())
    assert jobs.run_once(database)["status"] == "completed"
    with database.transaction() as tx:
        assert tx.get("job", downstream_jobs["compliance"])["status"] == "queued"
        assert tx.get("job", downstream_jobs["summary"])["status"] == "paused"
    executed = []

    def complete_downstream(tx, mission, task):
        executed.append(task["step"])
        return {"status": "COMPLETED", "report_id": "saved-" + task["step"]}

    monkeypatch.setattr(engine, "_handler", complete_downstream)
    assert jobs.run_once(database)["id"] == downstream_jobs["compliance"]
    with database.transaction() as tx:
        assert tx.get("job", downstream_jobs["summary"])["status"] == "queued"
    assert jobs.run_once(database)["id"] == downstream_jobs["summary"]
    assert jobs.run_once(database) is None
    assert executed == ["compliance", "summary"]
    with database.transaction() as tx:
        assert_history(tx, failed)
        assert all(tx.get("job", job_id)["status"] == "completed" for job_id in downstream_jobs.values())


@pytest.mark.parametrize("role", ["operator", "viewer"])
def test_retry_requires_administrator(failed, role):
    with failed["db"].transaction() as tx:
        with pytest.raises(PolicyError) as raised:
            retry(tx, failed, actor={"id": "unauthorized-retry", "role": role})
        assert raised.value.status == 403
        assert tx.list("mission_retry") == []


def test_new_token_configuration_needs_separate_renewed_paid_approval(failed):
    with failed["db"].transaction() as tx:
        ai.configure(tx, {"max_output_tokens": 2048, "reason": REASON}, ADMIN)
        assert not ai.status(tx)["effective_paid_enabled"]
        with pytest.raises(PolicyError) as raised:
            retry(tx, failed)
        assert raised.value.code in {"ai_not_ready", "ai_activation_required", "ai_admin_disabled", "ai_approval_required"}
        assert tx.list("mission_retry") == []
    assert len(failed["state"]["requests"]) == 1


def test_legacy_enabled_changed_configuration_requires_current_durable_paid_approval(failed):
    with failed["db"].transaction() as tx:
        # An old v1.3 installation could retain enabled=true while changing the
        # global output ceiling. Upgrade must preserve its settings but must not
        # reinterpret that flag as renewed authority for a replacement call.
        for approval in tx.list("ai_paid_activation"):
            tx.delete("ai_paid_activation", approval["id"])
        config = ai._config(tx)
        assert config["enabled"] and config["max_output_tokens"] == 4096
        assert not config.get("activation_required")
        assert ai.status(tx)["effective_paid_enabled"]
        assert tx.get("ai_trace", failed["trace_id"])["config_revision"] != config["revision"]
        with pytest.raises(PolicyError) as raised:
            retry(tx, failed)
        assert raised.value.code == "ai_activation_required"
        assert tx.list("mission_retry") == []
        assert engine._mission_cost(tx, failed["mission_id"]) == 12
        approve(tx)
        recovered = retry(tx, failed)
        assert recovered["id"] == failed["mission_id"]
        assert len(tx.list("mission_retry")) == 1
        assert tx.list("ai_paid_activation")
        assert_history(tx, failed)
    assert len(failed["state"]["requests"]) == 1


def test_expired_mission_is_not_extended_by_retry(failed):
    with failed["db"].transaction() as tx:
        mission = tx.get("business_mission", failed["mission_id"])
        mission["deadline_at"] = tx.now - 1
        tx.put("business_mission", mission["id"], mission)
        with pytest.raises(PolicyError) as raised:
            retry(tx, failed)
        assert raised.value.code == "mission_deadline"
        assert tx.get("business_mission", mission["id"])["deadline_at"] == mission["deadline_at"]
        assert tx.list("mission_retry") == []


def test_insufficient_remaining_mission_budget_does_not_reset_old_charge(failed):
    with failed["db"].transaction() as tx:
        mission = tx.get("business_mission", failed["mission_id"])
        mission["max_cost_minor"] = engine._mission_cost(tx, mission["id"])
        tx.put("business_mission", mission["id"], mission)
        with pytest.raises(PolicyError) as raised:
            retry(tx, failed)
        assert raised.value.code == "mission_budget"
        assert len(tx.list("budget_entry")) == 1
        assert_history(tx, failed)
        assert tx.list("mission_retry") == []


def test_global_ai_budget_blocks_retry_without_resetting_recorded_cost(failed):
    with failed["db"].transaction() as tx:
        budget.configure_ai_limits(tx, {"daily_limit_minor": 0, "monthly_limit_minor": 0,
                                      "reason": REASON}, ADMIN)
        with pytest.raises(PolicyError) as raised:
            retry(tx, failed)
        assert "budget" in raised.value.code
        assert engine._mission_cost(tx, failed["mission_id"]) == 12
        assert tx.list("mission_retry") == []
        assert_history(tx, failed)


def test_revoked_source_blocks_retry_without_deleting_prior_outputs(failed):
    with failed["db"].transaction() as tx:
        source = tx.get("research_source", SOURCE)
        source["approved"] = False
        tx.put("research_source", SOURCE, source)
        with pytest.raises(PolicyError) as raised:
            retry(tx, failed)
        assert "source" in raised.value.code.lower()
        assert_history(tx, failed)
        assert tx.list("mission_retry") == []


def test_source_version_change_in_completed_report_prevents_reusing_stale_evidence(failed):
    with failed["db"].transaction() as tx:
        source = tx.get("research_source", SOURCE)
        tx.create("research_report", {"mission_id": failed["mission_id"], "status": "COMPLETED",
            "citations": [{"source_id": SOURCE, "source_version": source["version"],
                           "source_url": source["url"], "evidence": "Previously reviewed literal source text"}]})
        source["version"] += 1
        tx.put("research_source", SOURCE, source)
        with pytest.raises(PolicyError) as raised:
            retry(tx, failed)
        assert "source" in raised.value.code
        assert_history(tx, failed)
        assert tx.list("mission_retry") == []


def test_policy_failure_and_unknown_charge_cannot_obtain_new_spend_identity(failed):
    with failed["db"].transaction() as tx:
        trace = tx.get("ai_trace", failed["trace_id"])
        trace.update(status="UNKNOWN", error_code="ai_provider_outcome_unknown")
        tx.put("ai_trace", trace["id"], trace)
        with pytest.raises(PolicyError):
            retry(tx, failed)
        assert tx.list("mission_retry") == []

        trace.update(status="REVIEW_REQUIRED", error_code="ai_policy_violation")
        tx.put("ai_trace", trace["id"], trace)
        task = tx.get("agent_task", failed["task_id"])
        task.update(error_code="ai_policy_violation", output={**task["output"], "code": "ai_policy_violation"})
        tx.put("agent_task", task["id"], task)
        with pytest.raises(PolicyError):
            retry(tx, failed)
        assert tx.list("mission_retry") == []


def test_queued_retry_fenced_by_config_can_be_reviewed_once_with_new_settings(failed):
    database = failed["db"]
    failed["state"]["truncated"] = False
    with database.transaction() as tx:
        first = retry(tx, failed, key="queued-before-settings-review")
        first_retry = tx.get("mission_retry", first["retry_id"])
        first_job = tx.get("job", first_retry["job_id"])
        ai.configure(tx, {"max_output_tokens": 2048, "reason": REASON}, ADMIN)
        state = engine.mission_detail(tx, failed["mission_id"])["recovery"]
        assert state["attempts_used"] == 1
        assert state["active_retry"]["status"] == "PAUSED"
        assert not ai.status(tx)["effective_paid_enabled"]
        assert len(tx.list("ai_trace")) == 1
        assert engine._mission_cost(tx, failed["mission_id"]) == 12
        approve(tx)
        second = retry(tx, failed, key="reviewed-after-settings-change")
        assert second["retry_id"] != first["retry_id"]
        assert len(tx.list("mission_retry")) == 2
        assert tx.get("job", first_job["id"])["status"] == "paused"
        recovery = engine.mission_detail(tx, failed["mission_id"])["recovery"]
        assert recovery["attempts_used"] == 2
    assert jobs.run_once(database)["status"] == "completed"
    with database.transaction() as tx:
        assert len(tx.list("ai_trace")) == 2
        assert engine._mission_cost(tx, failed["mission_id"]) == 24
        assert_history(tx, failed)
    assert [request["max_tokens"] for request in failed["state"]["requests"]] == [512, 2048]


def test_retry_blocked_at_provider_checkpoint_releases_reserve_and_can_be_reviewed_again(failed, monkeypatch):
    database = failed["db"]
    failed["state"]["truncated"] = False
    with database.transaction() as tx:
        first = retry(tx, failed, key="checkpoint-source-revoked")
    original_checkpoint = Transaction.checkpoint
    revoked = []

    def checkpoint_with_source_revocation(tx):
        original_checkpoint(tx)
        if (getattr(tx, "agent_context", None) or {}).get("retry_id") and not revoked:
            source = tx.get("research_source", SOURCE)
            source["approved"] = False
            tx.put("research_source", SOURCE, source)
            revoked.append(True)

    monkeypatch.setattr(Transaction, "checkpoint", checkpoint_with_source_revocation)
    assert jobs.run_once(database)["status"] == "paused"
    monkeypatch.setattr(Transaction, "checkpoint", original_checkpoint)
    assert revoked
    with database.transaction() as tx:
        blocked_trace = next(trace for trace in tx.list("ai_trace") if trace.get("retry_id") == first["retry_id"])
        assert blocked_trace["status"] == "BLOCKED"
        assert tx.get("budget_entry", blocked_trace["reservation_id"])["state"] == "RELEASED"
        assert engine._mission_cost(tx, failed["mission_id"]) == 12
        assert not any(trace["status"] == "UNKNOWN" for trace in tx.list("ai_trace"))
        source = tx.get("research_source", SOURCE)
        source["approved"] = True
        tx.put("research_source", SOURCE, source)
        second = retry(tx, failed, key="checkpoint-source-restored-review")
        assert second["retry_id"] != first["retry_id"]
        recovery = engine.mission_detail(tx, failed["mission_id"])["recovery"]
        assert recovery["attempts_used"] == 2
        assert recovery["active_retry"]["previous_trace_id"] == failed["trace_id"]
    assert len(failed["state"]["requests"]) == 1
    assert jobs.run_once(database)["status"] == "completed"
    with database.transaction() as tx:
        assert len(tx.list("ai_trace")) == 3
        assert len(tx.list("budget_entry")) == 3
        assert engine._mission_cost(tx, failed["mission_id"]) == 24
        assert_history(tx, failed)
    assert [request["max_tokens"] for request in failed["state"]["requests"]] == [512, 4096]


@pytest.mark.parametrize("change", ["missing_deadline", "nonfinite_deadline", "unsupported_graph"])
def test_retry_fails_closed_without_supported_persisted_deadline(failed, change):
    with failed["db"].transaction() as tx:
        mission = tx.get("business_mission", failed["mission_id"])
        if change == "missing_deadline":
            del mission["deadline_at"]
        elif change == "nonfinite_deadline":
            mission["deadline_at"] = float("nan")
        else:
            mission["workflow_version"] = "1.2"
        tx.put("business_mission", mission["id"], mission)
        with pytest.raises(PolicyError) as raised:
            retry(tx, failed)
        assert raised.value.code == "mission_retry_deadline_required"
        assert tx.list("mission_retry") == []
        assert engine._mission_cost(tx, failed["mission_id"]) == 12
    assert len(failed["state"]["requests"]) == 1
