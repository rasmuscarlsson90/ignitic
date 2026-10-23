"""AI activation diagnostics and real-only diagnostics with a fake HTTP transport."""
import hashlib
import json

import httpx
import pytest

from jarvis_lite import ai, budget, policy
from jarvis_lite.policy import PolicyError
from jarvis_lite.store import Database


ADMIN = {"id": "v12-ai-admin", "role": "admin"}
OPERATOR = {"id": "v12-ai-operator", "role": "operator"}
KEY = "synthetic-v12-not-a-real-provider-key"
REASON = "Explicitly reviewed private AI drafting and bounded costs"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_ENV", "test")
    monkeypatch.setenv("JARVIS_SESSION_SECRET", "v12-ai-test-session-key-for-tests-only")
    monkeypatch.delenv("JARVIS_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("JARVIS_ALLOW_PAID_AI", "false")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    database = Database("sqlite:///" + str(tmp_path / "v12-ai.db"))
    with database.transaction() as tx:
        policy.initialize(tx)
        budget.set_fx_rate(tx, currency="USD", sek_per_unit="12", source="Synthetic reviewed FX for regression test",
                           observed_at=tx.now, actor=ADMIN)
    yield database
    database.close()


def configure(tx, monkeypatch, *, enabled=False, **changes):
    monkeypatch.setenv("JARVIS_ALLOW_PAID_AI", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    return ai.configure(tx, {"enabled": enabled, "reason": REASON, **changes}, ADMIN)


def activation(state, *, statement=ai.ACTIVATE, **changes):
    return {"statement": statement, "config_revision": state["config"]["revision"], "reason": REASON, **changes}


def codes(state):
    return {blocker["code"] for blocker in state["blockers"]}


def provider_success():
    return {"id": "mock-v12-test", "usage": {"cost": "0.007", "prompt_tokens": 12, "completion_tokens": 9},
        "choices": [{"message": {"content": json.dumps({"action": "recommend", "text": "AI-anslutningen fungerar.",
            "reason": "Synthetic regression provider response."})}}]}


def metadata(*, model="openai/gpt-4o-mini", parameters=None, context=128000, output=16384, prompt="0.00000015"):
    return {"id": model, "context_length": context, "top_provider": {"max_completion_tokens": output},
        "supported_parameters": parameters if parameters is not None else ["response_format", "max_tokens", "temperature"],
        "pricing": {"prompt": prompt, "completion": "0.0000006"}}


def models_adapter(rows):
    return ai.ModelsAdapter(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": rows})))


def test_saved_false_is_the_precise_missing_gate_despite_runtime_key_and_model(db, monkeypatch):
    with db.transaction() as tx:
        state = configure(tx, monkeypatch)
        assert codes(state) == {"ai_admin_disabled"}
        assert state["runtime_allowed"] and state["key_configured"]
        assert not state["config"]["enabled"] and not state["effective_paid_enabled"]
        assert state["readiness"] == {"runtime": True, "administrator": False, "key": True,
            "model": True, "fx": True, "budget": True, "policy": True}
        assert "Administratören" in state["reason"]
        assert KEY not in json.dumps(state, ensure_ascii=False)
        assert tx.list("budget_entry") == tx.list("ai_trace") == []


def test_activation_is_exact_admin_review_preserving_existing_data_and_runtime_gate(db, monkeypatch):
    with db.transaction() as tx:
        state = configure(tx, monkeypatch)
        policy.set_secret(tx, "OPENROUTER_API_KEY", KEY, ADMIN)
        secret = tx.get("secret", "OPENROUTER_API_KEY")
        settings = policy.get_settings(tx)
        tx.put("lead", "existing-customer", {"name": "Existing customer data", "data_mode": "REAL"})
        monkeypatch.setenv("JARVIS_ALLOW_PAID_AI", "false")
        approved = ai.activate(tx, activation(state), ADMIN)
        assert approved["config"]["enabled"] is True
        assert codes(approved) == {"ai_runtime_disabled"}
        assert not approved["effective_paid_enabled"]
        assert tx.get("secret", "OPENROUTER_API_KEY") == secret
        assert tx.get("lead", "existing-customer")["name"] == "Existing customer data"
        current = policy.get_settings(tx)
        assert current["safety_epoch"] == settings["safety_epoch"] + 1
        for key in ("mode", "live_approved", "total_budget_minor", "rolling_tools_limit_minor", "kill_switch"):
            assert current[key] == settings[key]
        assert any(event["action"] == "ai.activation_reviewed" for event in tx.audits())
        monkeypatch.setenv("JARVIS_ALLOW_PAID_AI", "true")
        assert ai.status(tx)["effective_paid_enabled"]
        disabled = ai.activate(tx, activation(approved, statement=ai.DISABLE), ADMIN)
        assert codes(disabled) == {"ai_admin_disabled"}


@pytest.mark.parametrize("change,actor,code", [
    ({"statement": "enable"}, ADMIN, "ai_activation"),
    ({"statement": []}, ADMIN, "ai_activation"),
    ({"config_revision": "old-revision"}, ADMIN, "ai_config_changed"),
    ({"reason": "short"}, ADMIN, "ai_review_required"),
    ({"extra": True}, ADMIN, "ai_activation"),
    ({}, OPERATOR, "admin_required"),
])
def test_activation_rejects_ambiguous_stale_or_unauthorized_reviews(db, monkeypatch, change, actor, code):
    with db.transaction() as tx:
        state = configure(tx, monkeypatch)
        with pytest.raises(PolicyError) as error:
            ai.activate(tx, activation(state, **change), actor)
        assert error.value.code == code
        assert not ai.status(tx)["config"]["enabled"]
        assert tx.list("budget_entry") == []


def test_diagnostics_distinguish_fx_budget_kill_key_and_model_blockers(db, monkeypatch):
    with db.transaction() as tx:
        configure(tx, monkeypatch, enabled=True)
        rate = tx.get("fx_rate", "USD")
        tx.put("fx_rate", "USD", {**rate, "observed_at": tx.now - 31 * 86400})
        assert "fx_rate_stale" in codes(ai.status(tx))
        tx.put("fx_rate", "USD", rate)
        ai.configure(tx, {"per_call_limit_sek_minor": 20, "reason": REASON}, ADMIN)
        assert "ai_call_budget" in codes(ai.status(tx))
        ai.configure(tx, {"per_call_limit_sek_minor": 200, "reason": REASON}, ADMIN)
        settings = policy.get_settings(tx)
        tx.put("settings", "global", {**settings, "rolling_tools_limit_minor": 0, "kill_switch": True})
        monkeypatch.delenv("OPENROUTER_API_KEY")
        assert {"kill_switch", "ai_key_missing", "ai_budget_tools_30d"} <= codes(ai.status(tx))
        assert not ai.status(tx)["effective_paid_enabled"]


def test_unreadable_encrypted_key_has_no_exception_or_secret_leak(db, monkeypatch):
    with db.transaction() as tx:
        configure(tx, monkeypatch, enabled=True)
        policy.set_secret(tx, "OPENROUTER_API_KEY", KEY, ADMIN)
        def broken_key(*args):
            raise ValueError("private encryption internals " + KEY)
        monkeypatch.setattr(policy, "get_secret", broken_key)
        state = ai.status(tx)
        assert "ai_key_unreadable" in codes(state)
        assert KEY not in json.dumps(state) and "encryption internals" not in json.dumps(state)


def test_paid_diagnostic_requires_effective_activation_and_never_uses_demo(db, monkeypatch):
    with db.transaction() as tx:
        configure(tx, monkeypatch)
        with pytest.raises(PolicyError) as error:
            ai.test_connection(tx, idempotency_key="no-demo-test", actor=ADMIN,
                adapter=ai.OpenRouterAdapter(KEY, transport=httpx.MockTransport(lambda _: pytest.fail("blocked test made HTTP"))))
        assert error.value.code == "ai_not_ready"
        with pytest.raises(PolicyError, match="Administrator"):
            ai.test_connection(tx, idempotency_key="no-permission", actor=OPERATOR)
        assert tx.list("agent_result") == tx.list("budget_entry") == []


def test_paid_diagnostic_runs_real_pipeline_once_and_records_actual_usage(db, monkeypatch):
    calls = []
    def provider(request):
        calls.append(request)
        assert request.method == "POST" and str(request.url) == ai.ENDPOINT
        return httpx.Response(200, json=provider_success())
    adapter = ai.OpenRouterAdapter(KEY, transport=httpx.MockTransport(provider))
    with db.transaction() as tx:
        state = configure(tx, monkeypatch)
        ai.activate(tx, activation(state), ADMIN)
        result = ai.test_connection(tx, idempotency_key="reviewed-test", actor=ADMIN, adapter=adapter)
        assert result["diagnostic"] and result["status"] == "COMPLETED" and result["simulated"] is False
        assert ai.test_connection(tx, idempotency_key="reviewed-test", actor=ADMIN, adapter=adapter) == result
        assert len(calls) == 1
        entry, trace = tx.list("budget_entry")[0], tx.list("ai_trace")[0]
        assert entry["state"] == "SETTLED" and entry["actual_sek_minor"] == 12
        assert entry["input_tokens"] == 12 and trace["output_tokens"] == 9
        assert trace["role"] == entry["role"] == "ceo"
        assert trace["workflow_id"] == "administrator-ai-test"
        assert policy.get_settings(tx)["live_approved"] is False
        assert tx.list("email_message") == tx.list("payment") == []


def test_private_ai_works_in_live_without_customer_contact_or_payment_permissions(db, monkeypatch):
    monkeypatch.setenv("JARVIS_ALLOW_LIVE", "false")
    monkeypatch.setenv("JARVIS_ALLOW_LIVE_CONTACT", "false")
    monkeypatch.setenv("JARVIS_ALLOW_LIVE_PAYMENTS", "false")
    with db.transaction() as tx:
        configure(tx, monkeypatch, enabled=True)
        settings = policy.get_settings(tx)
        tx.put("settings", "global", {**settings, "mode": "LIVE", "live_approved": False, "legal_reviewed": False})
        assert ai.status(tx)["effective_paid_enabled"]
        result = ai.test_connection(tx, idempotency_key="private-live-test", actor=ADMIN,
            adapter=ai.OpenRouterAdapter(KEY, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=provider_success()))))
        assert result["status"] == "COMPLETED" and result["data_mode"] == "REAL"
        assert policy.get_settings(tx)["live_approved"] is False
        assert tx.list("email_message") == tx.list("payment") == []


def test_diagnostic_recovers_uncertain_durable_intent_without_a_second_provider_call(db, monkeypatch):
    class Crash:
        def complete(self, **kwargs):
            raise SystemExit("Synthetic process death after the durable dispatch intent")
    with db.transaction() as tx:
        configure(tx, monkeypatch, enabled=True)
    with pytest.raises(SystemExit), db.transaction() as tx:
        ai.test_connection(tx, idempotency_key="crashed-diagnostic", actor=ADMIN, adapter=Crash())
    with db.transaction() as tx:
        result = ai.test_connection(tx, idempotency_key="crashed-diagnostic", actor=ADMIN,
            adapter=ai.OpenRouterAdapter(KEY, transport=httpx.MockTransport(lambda request: pytest.fail("Uncertain diagnostic was sent twice"))))
        assert result["status"] == "UNKNOWN" and result["simulated"] is False
        assert result["trace_id"] == tx.list("ai_trace")[0]["id"]
        assert result["manual_reconciliation_required"]
        assert tx.list("budget_entry")[0]["state"] == "RESERVED"


@pytest.mark.parametrize("response,expected,state", [
    (httpx.Response(401, json={"error": {"code": 401, "message": "private provider error"}}), "REJECTED", "RELEASED"),
    (httpx.Response(500, json={"error": {"code": 500}}), "UNKNOWN", "RESERVED"),
    (httpx.Response(200, json={"id": "provider-accepted", "usage": {"cost": "0.01", "prompt_tokens": 1,
        "completion_tokens": 1}, "choices": [{"message": {"content": "not structured JSON"}}]}), "REVIEW_REQUIRED", "SETTLED"),
])
def test_paid_diagnostic_explains_provider_errors_without_synthetic_success(db, monkeypatch, response, expected, state):
    with db.transaction() as tx:
        configure(tx, monkeypatch, enabled=True)
        result = ai.test_connection(tx, idempotency_key="provider-error-test", actor=ADMIN,
            adapter=ai.OpenRouterAdapter(KEY, transport=httpx.MockTransport(lambda request: response)))
        assert result["status"] == expected and result["simulated"] is False
        assert result["message"] and "private provider error" not in json.dumps(result)
        assert tx.list("budget_entry")[0]["state"] == state


def test_model_refresh_is_fixed_public_get_without_key_proxy_or_generation(db, monkeypatch):
    calls = []
    def provider(request):
        calls.append(request)
        assert request.method == "GET" and str(request.url) == ai.MODELS_ENDPOINT
        assert "authorization" not in request.headers and not request.content
        return httpx.Response(200, json={"data": [metadata(), metadata(model="unapproved/model")]})
    monkeypatch.setenv("HTTPS_PROXY", "http://invalid-proxy.example:9")
    with db.transaction() as tx:
        state = configure(tx, monkeypatch)
        report = ai.refresh_models(tx, ADMIN, ai.ModelsAdapter(transport=httpx.MockTransport(provider)))
        assert len(calls) == 1
        assert report["model_refresh"]["refreshed_models"] == ["openai/gpt-4o-mini"]
        assert report["model_refresh"]["unsupported_models"] == []
        assert report["config"] == state["config"] and not report["config"]["enabled"]
        assert report["model_profile"]["profile"]["availability_verified"] is False
        assert tx.list("budget_entry") == tx.list("external_intent") == tx.list("ai_trace") == []
        assert KEY not in json.dumps(tx.audits())


def test_unknown_model_requires_reviewed_metadata_and_unsupported_format_refuses_dispatch(db, monkeypatch):
    with db.transaction() as tx:
        state = configure(tx, monkeypatch, model="vendor/reviewed", approved_models=["vendor/reviewed"])
        assert "ai_model_metadata_required" in codes(state)
        with pytest.raises(PolicyError) as error:
            ai.activate(tx, activation(state), ADMIN)
        assert error.value.code == "ai_model_incompatible"
        state = ai.refresh_models(tx, ADMIN, models_adapter([metadata(model="vendor/reviewed", parameters=["max_tokens", "temperature"])]))
        assert "ai_model_format_unsupported" in codes(state)
        ai.configure(tx, {"enabled": True, "reason": REASON}, ADMIN)
        with pytest.raises(PolicyError) as error:
            ai.task(tx, role="sales", text="A private draft", workflow_id="format-test", idempotency_key="format-test", actor=ADMIN)
        assert error.value.code == "ai_model_incompatible"
        assert tx.list("budget_entry") == []
        state = ai.refresh_models(tx, ADMIN, models_adapter([metadata(model="vendor/reviewed")]))
        assert state["effective_paid_enabled"]


@pytest.mark.parametrize("row,expected", [
    (metadata(parameters=["response_format", "max_tokens"]), "ai_model_format_unsupported"),
    (metadata(context=4096), "ai_model_context_limit"),
    (metadata(output=128), "ai_model_output_limit"),
    (metadata(prompt="0.0001"), "ai_model_price_above_cap"),
])
def test_model_metadata_checks_exact_request_parameters_context_output_and_price(db, monkeypatch, row, expected):
    with db.transaction() as tx:
        configure(tx, monkeypatch, enabled=True)
        state = ai.refresh_models(tx, ADMIN, models_adapter([row]))
        assert expected in codes(state)
        assert not state["effective_paid_enabled"]


def test_absent_or_stale_provider_model_does_not_retain_a_compatible_profile(db, monkeypatch):
    with db.transaction() as tx:
        configure(tx, monkeypatch, enabled=True)
        ai.refresh_models(tx, ADMIN, models_adapter([metadata()]))
        model_id = hashlib.sha256(b"openai/gpt-4o-mini").hexdigest()
        profile = tx.get("ai_model", model_id)
        tx.put("ai_model", model_id, {**profile, "observed_at": tx.now - 8 * 86400})
        assert "ai_model_metadata_stale" in codes(ai.status(tx))
        state = ai.refresh_models(tx, ADMIN, models_adapter([]))
        assert "ai_model_format_unsupported" in codes(state)
        assert state["model_profile"]["profile"]["verification"] == "not_in_provider_metadata"


@pytest.mark.parametrize("payload", [{"data": "wrong"}, {"data": [metadata(prompt="NaN")]},
    {"data": [metadata(), metadata()]}, {"data": [{**metadata(), "context_length": True}]}])
def test_malformed_metadata_cannot_modify_model_or_configuration(db, monkeypatch, payload):
    with db.transaction() as tx:
        state = configure(tx, monkeypatch)
        with pytest.raises(PolicyError):
            ai.refresh_models(tx, ADMIN, ai.ModelsAdapter(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))))
        assert tx.list("ai_model") == []
        assert ai.status(tx)["config"] == state["config"]


def test_metadata_refresh_requires_runtime_admin_and_surfaces_safe_origin_errors(db, monkeypatch):
    never = ai.ModelsAdapter(transport=httpx.MockTransport(lambda request: pytest.fail("Denied metadata request contacted provider")))
    with db.transaction() as tx:
        with pytest.raises(PolicyError) as error:
            ai.refresh_models(tx, ADMIN, never)
        assert error.value.code == "ai_runtime_disabled"
        monkeypatch.setenv("JARVIS_ALLOW_PAID_AI", "true")
        with pytest.raises(PolicyError, match="Administrator"):
            ai.refresh_models(tx, OPERATOR, never)
        adapter = ai.ModelsAdapter(transport=httpx.MockTransport(lambda request: httpx.Response(302, headers={"Location": "http://localhost/private"})))
        with pytest.raises(PolicyError) as error:
            ai.refresh_models(tx, ADMIN, adapter)
        assert error.value.code == "ai_model_metadata_unavailable"
        assert "localhost" not in str(error.value)


def test_assignment_and_schedule_attribution_and_budget_rechecks_before_dispatch(db, monkeypatch):
    guards = []
    def guard(tx, role, attributes, estimate, *, reservation_active=False):
        guards.append((role, dict(attributes), estimate, len(tx.list("budget_entry")), reservation_active))
    monkeypatch.setattr(ai, "_work_budget_guard", guard)
    with db.transaction() as tx:
        configure(tx, monkeypatch, enabled=True)
        tx.agent_context = {"mission_id": "mission-test", "task_id": "task-test", "role": "sales"}
        tx.create("internal_schedule", {"enabled": True, "revision": "reviewed-revision", "max_cost_minor": 200,
            "authorization_epoch": policy.get_settings(tx)["safety_epoch"]}, id="schedule-test")
        tx.create("schedule_run", {"schedule_id": "schedule-test", "revision": "reviewed-revision"}, id="run-test")
        tx.schedule_context = {"schedule_id": "schedule-test", "schedule_revision": "reviewed-revision", "schedule_run_id": "run-test"}
        result = ai.task(tx, role="sales", text="A private attributed draft.", workflow_id="untrusted-free-text",
            idempotency_key="attributed", actor=ADMIN,
            adapter=ai.OpenRouterAdapter(KEY, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=provider_success()))))
        assert result["workflow_id"] == "mission:mission-test:sales"
        for row in (tx.list("budget_entry")[0], tx.list("ai_trace")[0]):
            assert row["mission_id"] == "mission-test" and row["task_id"] == "task-test"
            assert row["schedule_id"] == "schedule-test" and row["schedule_run_id"] == "run-test"
            assert row["role"] == "sales"
        assert guards[0][2:] == (120, 0, False)
        assert all(estimate == 120 and entries == 1 and active for _, _, estimate, entries, active in guards[1:])
        with pytest.raises(PolicyError) as error:
            ai.task(tx, role="finance", text="Mismatched role", workflow_id="mismatch", idempotency_key="mismatch", actor=ADMIN)
        assert error.value.code == "ai_assignment_invalid"


def test_new_budget_denial_after_checkpoint_prevents_http_and_releases_reservation(db, monkeypatch):
    def guard(tx, role, attributes, estimate, *, reservation_active=False):
        if reservation_active:
            raise PolicyError("agent_budget", "Assignment no longer authorized", 409)
    monkeypatch.setattr(ai, "_work_budget_guard", guard)
    with db.transaction() as tx:
        configure(tx, monkeypatch, enabled=True)
        result = ai.task(tx, role="sales", text="No dispatch after revocation", workflow_id="revoked-budget",
            idempotency_key="revoked-budget", actor=ADMIN,
            adapter=ai.OpenRouterAdapter(KEY, transport=httpx.MockTransport(lambda request: pytest.fail("Budget-revoked work dispatched"))))
        assert result["status"] == "BLOCKED" and result["simulated"] is False
        assert tx.list("budget_entry")[0]["state"] == "RELEASED"


def test_direct_ai_rechecks_lowered_agent_per_call_limit_after_checkpoint(db, monkeypatch):
    with db.transaction() as tx:
        configure(tx, monkeypatch, enabled=True)
        checkpoint = tx.checkpoint
        changes = []
        def lower_limit():
            checkpoint()
            if not changes:
                # This models a concurrent administrator update at the durable
                # boundary, including direct requests with no job lease.
                tx.put("agent_config", "sales", {"per_call_limit_minor": 20})
                changes.append(True)
        monkeypatch.setattr(tx, "checkpoint", lower_limit)
        result = ai.task(tx, role="sales", text="Bounded draft before a concurrent cap reduction.",
            workflow_id="lowered-agent-cap", idempotency_key="lowered-agent-cap", actor=ADMIN,
            adapter=ai.OpenRouterAdapter(KEY, transport=httpx.MockTransport(lambda request: pytest.fail("Reduced agent cap allowed HTTP"))))
        assert changes and result["status"] == "BLOCKED"
        assert tx.list("budget_entry")[0]["state"] == "RELEASED"


def test_expanded_output_bound_requires_explicit_review_and_preserves_default(db, monkeypatch):
    with db.transaction() as tx:
        state = configure(tx, monkeypatch)
        assert state["config"]["max_output_tokens"] == 512
        state = ai.configure(tx, {"max_output_tokens": 4096, "reason": REASON}, ADMIN)
        assert state["config"]["max_output_tokens"] == 4096
        assert state["config"]["reservation_usd_minor"] == ai.DEFAULTS["reservation_usd_minor"]
        assert state["config"]["per_call_limit_sek_minor"] == ai.DEFAULTS["per_call_limit_sek_minor"]
        assert state["model_profile"]["compatible"]
        with pytest.raises(PolicyError) as error:
            ai.configure(tx, {"max_output_tokens": 4097, "reason": REASON}, ADMIN)
        assert error.value.code == "ai_bounds_invalid"


@pytest.mark.parametrize("workflow_id", [
    "mission:existing-mission:sales", "mission:nonexistent:sales", "existing-mission",
    "schedule:existing-schedule:sales", "schedule:nonexistent:sales", "existing-schedule",
])
def test_direct_operator_cannot_forge_reserved_mission_or_schedule_attribution(db, monkeypatch, workflow_id):
    requests = []
    def provider(request):
        requests.append(request)
        return httpx.Response(200, json=provider_success())
    with db.transaction() as tx:
        configure(tx, monkeypatch, enabled=True)
        tx.create("business_mission", {"status": "RUNNING", "max_cost_minor": 1,
            "schedule_id": "existing-schedule"}, id="existing-mission")
        tx.create("internal_schedule", {"enabled": True, "max_cost_minor": 1}, id="existing-schedule")
        with pytest.raises(PolicyError) as error:
            ai.task(tx, role="sales", text="Operator tries to attribute a direct call to a mission.",
                workflow_id=workflow_id, idempotency_key="forged-workflow", actor=OPERATOR,
                adapter=ai.OpenRouterAdapter(KEY, transport=httpx.MockTransport(provider)))
        assert error.value.code == "ai_assignment_invalid" and error.value.status == 403
        assert not requests
        assert tx.list("budget_entry") == tx.list("ai_trace") == tx.list("external_intent") == tx.list("agent_result") == []
