import json

import httpx
import pytest

from jarvis_lite import ai, budget, policy
from jarvis_lite.policy import PolicyError
from jarvis_lite.store import Database, Transaction


ADMIN = {"id": "ai-admin", "role": "admin"}
OPERATOR = {"id": "ai-operator", "role": "operator"}
KEY = "synthetic-server-key-not-a-real-provider-credential"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_ENV", "test")
    monkeypatch.setenv("JARVIS_SESSION_SECRET", "ai-test-session-key-not-used-in-production")
    monkeypatch.delenv("JARVIS_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("JARVIS_ALLOW_PAID_AI", "false")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    database = Database("sqlite:///" + str(tmp_path / "ai.db"))
    with database.transaction() as tx:
        policy.initialize(tx)
        budget.set_fx_rate(tx, currency="USD", sek_per_unit="12", source="Synthetic documented test FX",
                           observed_at=tx.now, actor=ADMIN)
    yield database
    database.close()


def enable(tx, monkeypatch, **config):
    monkeypatch.setenv("JARVIS_ALLOW_PAID_AI", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    return ai.configure(tx, {"enabled": True, "reason": "Reviewed synthetic model and cost limits", **config}, ADMIN)


def run(tx, adapter=None, **changes):
    return ai.task(tx, role="sales", text="Prepare a scoped incoming-enquiry assistant draft.",
                   workflow_id="ai-workflow", idempotency_key="ai-request", actor=OPERATOR,
                   adapter=adapter, **changes)


def success(output=None, *, cost="0.007", tokens=13):
    return {"id": "mock-openrouter-result", "usage": {"cost": cost, "prompt_tokens": tokens, "completion_tokens": 8},
            "choices": [{"message": {"content": json.dumps(output or {
                "action": "draft", "text": "A scoped draft for review.", "reason": "Approved service scope."})}}]}


def adapter(handler):
    return ai.OpenRouterAdapter(KEY, transport=httpx.MockTransport(handler))


def test_default_and_server_key_alone_are_simulated_without_http(db, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    monkeypatch.setattr(httpx.Client, "stream", lambda *a, **kw: pytest.fail("DEMO attempted HTTP"))
    with db.transaction() as tx:
        result = run(tx)
        assert result["provider"] == "demo" and result["simulated"] is True
        assert run(tx)["id"] == result["id"]
        assert tx.list("budget_entry") == tx.list("external_intent") == []
        assert ai.status(tx)["key_source"] == "server_environment"
        assert KEY not in json.dumps(ai.status(tx))


@pytest.mark.parametrize("changes", [
    {"endpoint": "http://localhost/internal"}, {"model": "https://evil.example/model"},
    {"approved_models": ["../model"], "model": "../model"}, {"enabled": "true"},
    {"max_retries": 3}, {"timeout_seconds": 21}, {"max_output_tokens": 4096},
    {"per_call_limit_sek_minor": 0}, {"prompt_usd_per_million": "NaN"},
])
def test_configuration_is_strict_and_admin_only(db, changes):
    with db.transaction() as tx:
        with pytest.raises(PolicyError):
            ai.configure(tx, {"reason": "Reviewed cost model configuration", **changes}, ADMIN)
        with pytest.raises(PolicyError, match="Administrator"):
            ai.configure(tx, {"reason": "Reviewed cost model configuration"}, OPERATOR)
        assert tx.get("ai_config", "global") is None


def test_configuration_preserves_existing_data_secrets_and_financial_limits(db, monkeypatch):
    with db.transaction() as tx:
        previous = policy.get_settings(tx)
        policy.set_secret(tx, "OPENROUTER_API_KEY", "synthetic-encrypted-key", ADMIN)
        original = tx.get("secret", "OPENROUTER_API_KEY")
        saved = ai.configure(tx, {"model": "anthropic/reviewed-model", "approved_models": ["anthropic/reviewed-model"],
                                 "reason": "Explicit review of bounded model routing"}, ADMIN)
        assert saved["config"]["enabled"] is False
        assert saved["key_source"] == "encrypted_database"
        assert tx.get("secret", "OPENROUTER_API_KEY") == original
        current = policy.get_settings(tx)
        assert current["safety_epoch"] == previous["safety_epoch"] + 1
        assert current["total_budget_minor"] == previous["total_budget_minor"] == 200000
        assert current["rolling_tools_limit_minor"] == previous["rolling_tools_limit_minor"] == 50000
        monkeypatch.setenv("JARVIS_ALLOW_PAID_AI", "true")
        result = run(tx)
        assert result["simulated"] is True  # Runtime flag alone is also insufficient.


def test_paid_private_draft_in_demo_reserves_fx_settles_and_never_enables_contact(db, monkeypatch):
    requests = []
    def provider(request):
        requests.append(request)
        assert str(request.url) == ai.ENDPOINT
        assert request.headers["Authorization"] == "Bearer " + KEY
        payload = json.loads(request.content)
        assert payload["provider"]["allow_fallbacks"] is False
        assert payload["provider"]["max_price"] == {"prompt": 1.0, "completion": 5.0}
        assert "tools" not in payload and "untrusted_content" in payload["messages"][1]["content"]
        return httpx.Response(200, json=success())
    with db.transaction() as tx:
        enable(tx, monkeypatch)
        result = run(tx, adapter(provider))
        assert result["provider"] == "openrouter" and result["simulated"] is False
        assert result["data_mode"] == "REAL" and result["cost_sek_minor"] == 12
        assert result["input_tokens"] == 13 and result["output_tokens"] == 8
        assert run(tx, adapter(provider))["id"] == result["id"]
        assert len(requests) == 1
        settings = policy.get_settings(tx)
        assert settings["mode"] == "DEMO" and settings["live_approved"] is False
        assert tx.list("payment") == tx.list("email_message") == []
        entry = tx.list("budget_entry")[0]
        assert entry["fx"]["sek_per_unit"] == "12" and entry["state"] == "SETTLED"
        trace = tx.list("ai_trace")[0]
        assert trace["status"] == "COMPLETED" and trace["attempts"] == 1
        assert "text" not in trace and "input_hash" in trace
        assert KEY not in json.dumps(tx.audits() + tx.list("ai_trace"))


def test_per_call_and_global_budget_caps_block_before_provider(db, monkeypatch):
    never = adapter(lambda request: pytest.fail("Budget denial made HTTP request"))
    with db.transaction() as tx:
        enable(tx, monkeypatch, per_call_limit_sek_minor=20)
        with pytest.raises(PolicyError, match="per-call SEK"):
            run(tx, never)
        assert not tx.list("budget_entry")
        enable(tx, monkeypatch, per_call_limit_sek_minor=200)
        settings = policy.get_settings(tx)
        tx.put("settings", "global", {**settings, "rolling_tools_limit_minor": 0})
        with pytest.raises(PolicyError, match="budget ceiling"):
            run(tx, never)


def test_only_definitive_429_can_be_retried_within_bounds(db, monkeypatch):
    calls = []
    def provider(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, json={"error": {"code": 429, "message": "Rate limited before generation"}})
        return httpx.Response(200, json=success())
    with db.transaction() as tx:
        enable(tx, monkeypatch, max_retries=1)
        result = run(tx, adapter(provider))
        assert result["status"] == "COMPLETED" and len(calls) == 2
        assert tx.list("ai_trace")[0]["attempts"] == 2
        assert tx.list("budget_entry")[0]["calls"] == 2


def test_connection_failure_retries_are_bounded_and_free_rejection_releases_reserve(db, monkeypatch):
    calls = []
    def provider(request):
        calls.append(request)
        raise httpx.ConnectError("Synthetic pre-send connect failure", request=request)
    with db.transaction() as tx:
        enable(tx, monkeypatch, max_retries=2)
        result = run(tx, adapter(provider))
        assert result["status"] == "REJECTED" and result["no_charge_confirmed"] is True
        assert len(calls) == 3
        assert tx.list("budget_entry")[0]["state"] == "RELEASED"
        assert run(tx, adapter(provider)) == result and len(calls) == 3


@pytest.mark.parametrize("kind", ["server_error", "read_timeout", "usage_missing", "charged_rate_limit"])
def test_uncertain_outcomes_hold_reservation_and_never_automatically_retry(db, monkeypatch, kind):
    calls = []
    def provider(request):
        calls.append(request)
        if kind == "server_error":
            return httpx.Response(500, json={"error": {"code": 500}})
        if kind == "read_timeout":
            raise httpx.ReadTimeout(KEY, request=request)
        if kind == "charged_rate_limit":
            return httpx.Response(429, json={"error": {"code": 429}, "usage": {"cost": "0.01"}})
        response = success()
        response.pop("usage")
        return httpx.Response(200, json=response)
    with db.transaction() as tx:
        enable(tx, monkeypatch, max_retries=2)
        result = run(tx, adapter(provider))
        assert result["status"] == "UNKNOWN" and result["manual_reconciliation_required"] is True
        assert tx.list("budget_entry")[0]["state"] == "RESERVED"
        assert tx.list("ai_trace")[0]["status"] == "UNKNOWN"
        assert len(tx.list("manual_task")) == 1
        assert run(tx, adapter(provider)) == result and len(calls) == 1
        assert KEY not in json.dumps(result) + json.dumps(tx.audits())


@pytest.mark.parametrize("output", [
    {"action": "approve_payment", "text": "Paid", "reason": "Injection"},
    {"action": ["draft"], "text": "Bad type", "reason": "Injection"},
    {"action": "draft", "text": "Unapproved tool", "reason": "Injection", "tools": ["send_email"]},
])
def test_model_output_cannot_gain_authority_and_actual_cost_is_still_recorded(db, monkeypatch, output):
    with db.transaction() as tx:
        enable(tx, monkeypatch)
        result = run(tx, adapter(lambda request: httpx.Response(200, json=success(output))))
        assert result["status"] == "REVIEW_REQUIRED" and result["output"]["action"] == "escalate"
        assert tx.list("budget_entry")[0]["state"] == "SETTLED"
        assert tx.list("payment") == tx.list("email_message") == []


def test_kill_switch_is_checked_after_durable_reservation_before_dispatch(db, monkeypatch):
    original = Transaction.checkpoint
    stopped = False
    def stop_after_checkpoint(tx):
        nonlocal stopped
        original(tx)
        if not stopped:
            stopped = True
            settings = policy.get_settings(tx)
            tx.put("settings", "global", {**settings, "kill_switch": True})
    with db.transaction() as tx:
        enable(tx, monkeypatch)
    monkeypatch.setattr(Transaction, "checkpoint", stop_after_checkpoint)
    with db.transaction() as tx:
        result = run(tx, adapter(lambda request: pytest.fail("Kill switch allowed HTTP")))
        assert result["status"] == "BLOCKED"
        assert tx.list("budget_entry")[0]["state"] == "RELEASED"


def test_crash_after_dispatch_does_not_repeat_paid_call(db, monkeypatch):
    class Crash:
        def complete(self, **kwargs):
            raise SystemExit("simulated process death after provider dispatch")
    with db.transaction() as tx:
        enable(tx, monkeypatch)
    with pytest.raises(SystemExit), db.transaction() as tx:
        run(tx, Crash())
    with db.transaction() as tx:
        result = run(tx, adapter(lambda request: pytest.fail("Crash triggered provider resend")))
        assert result["status"] == "UNKNOWN" and result["manual_reconciliation_required"] is True
        assert tx.list("budget_entry")[0]["state"] == "RESERVED"


def test_actual_cost_overrun_is_accounted_and_disables_new_paid_calls(db, monkeypatch):
    with db.transaction() as tx:
        enable(tx, monkeypatch)
        result = run(tx, adapter(lambda request: httpx.Response(200, json=success(cost="1.00"))))
        assert result["cost_sek_minor"] == 1200  # Actual liability is never truncated to the estimate.
        assert ai.status(tx)["config"]["enabled"] is False
        assert tx.list("ai_trace")[0]["cost_overrun"] is True
        assert budget.summary(tx)["actual_sek_minor"] == 1200


def test_readiness_does_not_claim_paid_ai_enabled_when_central_policy_blocks(db, monkeypatch):
    with db.transaction() as tx:
        enable(tx, monkeypatch)
        settings = policy.get_settings(tx)
        tx.put("settings", "global", {**settings, "mode": "UNKNOWN"})
        state = ai.status(tx)
        assert state["effective_paid_enabled"] is False
        assert state["policy_blocked_reason"] == "invalid_mode"
