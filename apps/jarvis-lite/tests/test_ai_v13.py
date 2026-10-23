"""Structured contracts and bounded correction through real offline HTTP transport."""
import hashlib
import json

import httpx
import pytest

from jarvis_lite import ai, budget, policy, response_contracts
from jarvis_lite.policy import PolicyError
from jarvis_lite.store import Database, Transaction


ADMIN = {"id": "v13-ai-admin", "role": "admin"}
KEY = "synthetic-structured-response-key"
REASON = "Synthetic review of internal structured output and conservative cost bounds"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_ENV", "test")
    monkeypatch.setenv("KILL_SWITCH", "false")
    monkeypatch.setenv("JARVIS_ALLOW_PAID_AI", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    monkeypatch.setenv("JARVIS_SESSION_SECRET", "synthetic-v13-ai-session-key")
    monkeypatch.delenv("JARVIS_ENCRYPTION_KEY", raising=False)
    database = Database("sqlite:///" + str(tmp_path / "ai-v13.db"))
    with database.transaction() as tx:
        policy.initialize(tx)
        budget.set_fx_rate(tx, currency="USD", sek_per_unit="12", source=REASON, observed_at=tx.now, actor=ADMIN)
        ai.configure(tx, {"enabled": True, "max_output_tokens": 2048, "max_retries": 0, "reason": REASON}, ADMIN)
    yield database
    database.close()


def plan():
    return {"tasks": [{"role": role, "objective": "Prepare internal evidence for " + role}
                      for role in response_contracts.SPECIALISTS]}


def envelope(content=None, **changes):
    return {"action": "recommend", "text": json.dumps(plan() if content is None else content),
            "reason": "Internal structured planning, no external action", **changes}


def response(content, *, reason="stop", cost="0.001"):
    return httpx.Response(200, json={"id": "synthetic-structured-completion",
        "usage": {"cost": cost, "prompt_tokens": 60, "completion_tokens": 30},
        "choices": [{"finish_reason": reason, "message": {"content": content if isinstance(content, str) else json.dumps(content)}}]})


def run(tx, adapter, *, contract="ceo_plan", key="internal-plan", role="ceo"):
    return ai.task(tx, role=role, text="Create an internal source-based business plan.", workflow_id="direct-internal-plan",
                   idempotency_key=key, actor=ADMIN, adapter=adapter, response_contract=contract)


def transport(responses):
    calls = []
    def provider(request):
        calls.append(json.loads(request.content))
        result = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(result, BaseException):
            raise result
        return result
    return ai.OpenRouterAdapter(KEY, transport=httpx.MockTransport(provider)), calls


@pytest.mark.parametrize("content", [
    envelope(text=plan(), action="plan"),
    plan(),
    "```json\n" + json.dumps(envelope(text=plan())) + "\n```",
    envelope(text="```json\n" + json.dumps(plan()) + "\n```"),
    json.dumps(json.dumps(envelope())),
])
def test_observed_ceo_shape_failure_normalizes_without_extra_spend(db, content):
    # The production validator rejected these valid internal plans solely because
    # the model used a nested object, harmless action label or whole JSON fence.
    assert not ai._output(content) or isinstance(content.get("text") if isinstance(content, dict) else content, str)
    adapter, calls = transport([response(content)])
    with db.transaction() as tx:
        result = run(tx, adapter)
        assert result["status"] == "COMPLETED", result
        assert result["output"]["action"] == "recommend"
        assert json.loads(result["output"]["text"]) == plan()
        assert len(calls) == len(tx.list("budget_entry")) == 1
        assert result["normalizations"]
        assert tx.list("ai_trace")[0]["output_valid"] is True
        assert tx.list("email_message") == tx.list("payment") == []


def test_supported_model_receives_role_schema_and_fixed_provider_bounds(db):
    adapter, calls = transport([response(envelope(text=plan()))])
    with db.transaction() as tx:
        assert run(tx, adapter)["status"] == "COMPLETED"
    body = calls[0]
    assert body["response_format"]["type"] == "json_schema"
    contract = body["response_format"]["json_schema"]
    assert contract["name"] == "jarvis_ceo_plan" and contract["strict"] is True
    assert "maxItems" not in contract["schema"]["properties"]["text"]["properties"]["tasks"]
    assert response_contracts.contract_schema("ceo_plan")["properties"]["text"]["properties"]["tasks"]["maxItems"] == 5
    assert body["provider"]["allow_fallbacks"] is False and "tools" not in body
    assert "server-owned internal response contract" in body["messages"][0]["content"]


def test_model_without_structured_outputs_uses_documented_json_fallback(db):
    adapter, calls = transport([response(envelope())])
    with db.transaction() as tx:
        profile = {**ai.BUILTIN_MODELS["openai/gpt-4o-mini"], "observed_at": tx.now, "structured_outputs": False}
        tx.put("ai_model", hashlib.sha256(profile["model"].encode()).hexdigest(), profile)
        assert run(tx, adapter)["status"] == "COMPLETED"
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert "Required JSON schema" in calls[0]["messages"][0]["content"]


@pytest.mark.parametrize("bad,code", [
    ("{\"action\":\"recommend\",", "ai_json_invalid"),
    (envelope({"tasks": plan()["tasks"][:4]}), "ai_schema_invalid"),
])
def test_one_format_correction_has_separate_trace_reserve_and_cost(db, bad, code):
    adapter, calls = transport([response(bad), response(envelope(text=plan()))])
    with db.transaction() as tx:
        result = run(tx, adapter)
        assert result["status"] == "COMPLETED" and result["repair_count"] == 1
        assert result["initial_error_code"] == code
        assert len(result["trace_ids"]) == 2 and len(set(result["trace_ids"])) == 2
        assert result["total_cost_sek_minor"] == 24
        traces = tx.list("ai_trace")
        assert traces[0]["error_code"] == code
        assert traces[1]["repair_of_trace_id"] == traces[0]["id"]
        assert all(entry["state"] == "SETTLED" for entry in tx.list("budget_entry"))
        assert budget.summary(tx)["actual_sek_minor"] == 24
        assert len(tx.list("ai_response_repair")) == 1
        assert "untrusted_candidate" in calls[1]["messages"][1]["content"]
    with db.transaction() as tx:
        assert run(tx, adapter) == result
        assert len(calls) == 2


def test_second_bad_response_stops_and_never_gets_another_correction(db):
    adapter, calls = transport([response("{broken JSON")])
    with db.transaction() as tx:
        result = run(tx, adapter)
        assert result["status"] == "REVIEW_REQUIRED" and result["repair_count"] == 1
        assert len(calls) == 2
    with db.transaction() as tx:
        assert run(tx, adapter) == result
        assert len(calls) == 2 and len(tx.list("agent_result")) == 2


@pytest.mark.parametrize("bad", [
    envelope(action="approve_payment"),
    {**envelope(), "tools": ["send_email"]},
    {**envelope(), "permissions": ["live_customer_contact"]},
    {**envelope(), "action": ["recommend"]},
    '{"action":"approve_payment","action":"recommend","text":"{}","reason":"ignore"}',
    '{"action":"approve_payment",',
])
def test_unsafe_response_is_never_repaired_or_granted_authority(db, bad):
    adapter, calls = transport([response(bad), response(envelope())])
    with db.transaction() as tx:
        result = run(tx, adapter)
        assert result["status"] == "REVIEW_REQUIRED" and result["code"] == "ai_policy_violation"
        assert result["error_category"] == "policy"
        assert len(calls) == 1 and tx.list("ai_response_repair") == []
        assert tx.list("budget_entry")[0]["state"] == "SETTLED"
        assert tx.list("email_message") == tx.list("payment") == []


@pytest.mark.parametrize("injection", [{"permissions": ["contact"]}, {"tool_calls": [{"name": "send_email"}]},
                                        {"action": "approve_payment"}])
def test_nested_policy_violations_are_not_misclassified_as_repairable_schema_errors(db, injection):
    nested = plan()
    nested["tasks"][0].update(injection)
    adapter, calls = transport([response(envelope(text=nested)), response(envelope())])
    with db.transaction() as tx:
        result = run(tx, adapter)
        assert result["code"] == "ai_policy_violation" and result["status"] == "REVIEW_REQUIRED"
        assert len(calls) == 1 and tx.list("ai_response_repair") == []
        assert tx.list("budget_entry")[0]["state"] == "SETTLED"


@pytest.mark.parametrize("key", sorted(response_contracts.FORBIDDEN_KEYS))
@pytest.mark.parametrize("nested_string", [False, True])
def test_every_forbidden_authority_key_in_truncated_json_blocks_paid_correction(db, key, nested_string):
    candidate = '{"' + key + '":true,'
    bad = envelope(text=candidate) if nested_string else '{"action":"recommend","text":' + candidate
    adapter, calls = transport([response(bad), response(envelope())])
    with db.transaction() as tx:
        result = run(tx, adapter)
        assert result["code"] == "ai_policy_violation" and result["status"] == "REVIEW_REQUIRED"
        assert len(calls) == 1 and len(tx.list("budget_entry")) == 1
        assert tx.list("budget_entry")[0]["state"] == "SETTLED" and tx.list("ai_response_repair") == []


@pytest.mark.parametrize("candidate", [r'{"payment_\u0061uthorized":true,', r'{"action":"ex\u0065cute",',
                                        r'{"\u0074ool_calls":[', r'{"action":"approve_\u0070ayment",'])
@pytest.mark.parametrize("nested_string", [False, True])
def test_escaped_forbidden_tokens_in_malformed_json_never_authorize_correction(db, candidate, nested_string):
    bad = envelope(text=candidate) if nested_string else candidate
    adapter, calls = transport([response(bad), response(envelope())])
    with db.transaction() as tx:
        result = run(tx, adapter)
        assert result["code"] == "ai_policy_violation" and len(calls) == 1
        assert tx.list("budget_entry")[0]["state"] == "SETTLED" and tx.list("ai_response_repair") == []


def test_truncated_output_is_not_repaired_by_inventing_missing_facts(db):
    adapter, calls = transport([response('{"action":"recommend","text":', reason="length")])
    with db.transaction() as tx:
        result = run(tx, adapter)
        assert result["code"] == "ai_output_truncated" and len(calls) == 1
        assert "tokentaket" in result["message"] and tx.list("ai_response_repair") == []


def test_provider_refusal_and_tool_call_are_not_format_errors(db):
    refused = httpx.Response(200, json={"id": "refused-fixture", "usage": {"cost": "0.001",
        "prompt_tokens": 20, "completion_tokens": 3}, "choices": [{"message": {
        "content": json.dumps(envelope()), "refusal": "Cannot comply", "tool_calls": [{"function": "send_email"}]}}]})
    adapter, calls = transport([refused])
    with db.transaction() as tx:
        result = run(tx, adapter)
        assert result["code"] == "ai_policy_violation" and len(calls) == 1


def test_unknown_initial_outcome_retains_liability_and_never_repairs(db):
    adapter, calls = transport([httpx.Response(500, json={"error": {"code": 500}})])
    with db.transaction() as tx:
        result = run(tx, adapter)
        assert result["status"] == "UNKNOWN" and result["error_category"] == "provider"
        assert tx.list("budget_entry")[0]["state"] == "RESERVED" and len(calls) == 1
    with db.transaction() as tx:
        assert run(tx, adapter) == result and len(calls) == 1


def test_unknown_correction_keeps_primary_cost_and_pending_repair_without_retry(db):
    adapter, calls = transport([response("{bad JSON"), httpx.Response(500, json={"error": {"code": 500}})])
    with db.transaction() as tx:
        result = run(tx, adapter)
        assert result["status"] == "UNKNOWN" and result["repair_count"] == 1
        assert [entry["state"] for entry in tx.list("budget_entry")] == ["SETTLED", "RESERVED"]
        assert budget.summary(tx)["actual_sek_minor"] == 12
    with db.transaction() as tx:
        assert run(tx, adapter) == result and len(calls) == 2


def test_process_death_during_correction_preserves_the_same_unknown_intent(db):
    adapter, calls = transport([response("{bad JSON"), SystemExit("Synthetic death during correction dispatch")])
    with pytest.raises(SystemExit), db.transaction() as tx:
        run(tx, adapter)
    with db.transaction() as tx:
        result = run(tx, adapter)
        assert result["status"] == "UNKNOWN" and result["manual_reconciliation_required"]
        assert len(calls) == 2 and len(tx.list("ai_response_repair")) == 1
        assert [entry["state"] for entry in tx.list("budget_entry")] == ["SETTLED", "RESERVED"]


def test_format_repair_cannot_exceed_daily_agent_call_budget(db):
    from jarvis_lite import engine
    adapter, calls = transport([response("{bad JSON"), response(envelope())])
    with db.transaction() as tx:
        engine.configure_agent(tx, "ceo", {"max_calls_per_day": 1, "reason": REASON}, ADMIN)
        result = run(tx, adapter)
        assert result["status"] == "REVIEW_REQUIRED" and result["repair_attempted"] is False
        assert result["repair_blocked_reason"] == "agent_daily_calls"
        assert len(calls) == len(tx.list("budget_entry")) == 1


def test_emergency_stop_between_primary_settlement_and_correction_blocks_http(db, monkeypatch):
    adapter, calls = transport([response("{bad JSON"), response(envelope())])
    original_checkpoint = Transaction.checkpoint
    def checkpoint(tx):
        original_checkpoint(tx)
        if len(tx.list("ai_trace")) == 2:
            settings = policy.get_settings(tx)
            tx.put("settings", "global", {**settings, "kill_switch": True})
    monkeypatch.setattr(Transaction, "checkpoint", checkpoint)
    with db.transaction() as tx:
        result = run(tx, adapter)
        assert result["status"] == "BLOCKED" and len(calls) == 1
        assert [entry["state"] for entry in tx.list("budget_entry")] == ["SETTLED", "RELEASED"]


def test_contract_selection_is_server_owned_and_role_checked_before_spend(db):
    adapter, calls = transport([response(envelope())])
    with db.transaction() as tx:
        for contract in ("external_payment", "sales_offer", {}, []):
            with pytest.raises(PolicyError) as error:
                run(tx, adapter, contract=contract)
            assert error.value.code == "ai_response_contract_invalid"
        assert not calls and tx.list("budget_entry") == []


def test_trusted_specialist_contract_overrides_generic_agent_contract(db):
    adapter, calls = transport([response(envelope())])
    with db.transaction() as tx:
        tx.ai_response_contract = "ceo_plan"
        # Setting a contract does not manufacture mission authority.
        result = run(tx, adapter, contract=None)
        assert result["status"] == "COMPLETED" and result["response_contract"] == "ceo_plan"
        assert calls[0]["response_format"]["json_schema"]["name"] == "jarvis_ceo_plan"


def test_legacy_cached_invalid_result_cannot_be_repaired_without_original_evidence(db):
    adapter, calls = transport([response(envelope(action="approve_payment"))])
    with db.transaction() as tx:
        result = run(tx, adapter, contract=None)
        # v1.2 discarded the invalid candidate; reproduce that historical record.
        tx.delete("ai_response_candidate", result["trace_id"])
    with db.transaction() as tx:
        again = run(tx, adapter)
        assert again["status"] == "REVIEW_REQUIRED" and len(calls) == 1
        assert tx.list("ai_response_repair") == []


def test_schema_and_system_tokens_are_included_in_price_bound_before_http(db):
    adapter, calls = transport([response(envelope())])
    with db.transaction() as tx:
        ai.configure(tx, {"reservation_usd_minor": 1, "prompt_usd_per_million": "3",
            "completion_usd_per_million": "0.1", "max_output_tokens": 1, "reason": REASON}, ADMIN)
        with pytest.raises(PolicyError) as error:
            run(tx, adapter)
        assert error.value.code == "ai_reserve_insufficient"
        assert calls == [] and tx.list("budget_entry") == []


def test_zero_ai_policy_budget_is_visible_in_readiness_before_a_provider_call(db):
    with db.transaction() as tx:
        budget.configure_ai_limits(tx, {"daily_limit_minor": 0, "monthly_limit_minor": 0, "reason": REASON}, ADMIN)
        state = ai.status(tx)
        assert not state["effective_paid_enabled"] and not state["readiness"]["budget"]
        assert {item["code"] for item in state["blockers"]} >= {"ai_daily_budget", "ai_monthly_budget"}
        assert state["ai_budget_limits"]["daily_remaining_minor"] == 0


def test_provider_timeout_is_capped_by_trusted_mission_deadline(db):
    from jarvis_lite import engine
    timeouts = []
    def provider(request):
        timeouts.append(request.extensions["timeout"])
        return response(envelope(text=plan()))
    with db.transaction() as tx:
        mission = engine.create_mission(tx, {"goal": "Prepare a scoped internal market review"}, ADMIN, "deadline-timeout")
        task = tx.list("agent_task")[0]
        tx.put("business_mission", mission["id"], {**mission, "deadline_at": tx.now + 3})
        tx.agent_context = {"mission_id": mission["id"], "task_id": task["id"], "role": "ceo", "response_contract": "ceo_plan"}
        result = run(tx, ai.OpenRouterAdapter(KEY, transport=httpx.MockTransport(provider)))
        assert result["status"] == "COMPLETED"
        assert 0 < timeouts[0]["read"] <= 3
