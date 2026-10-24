"""Current reviewed AI limits, fresh approval and explicit truncation recovery."""
import json

import httpx
import pytest

from jarvis_lite import ai, budget, engine, policy
from jarvis_lite.policy import PolicyError
from jarvis_lite.store import Database, Transaction, fingerprint


ADMIN = {"id": "v14-ai-admin", "role": "admin"}
KEY = "synthetic-v14-openrouter-key"
REASON = "Synthetic administrator review of internal execution and spending bounds"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_ENV", "test")
    monkeypatch.setenv("KILL_SWITCH", "false")
    monkeypatch.setenv("JARVIS_ALLOW_PAID_AI", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    monkeypatch.setenv("JARVIS_SESSION_SECRET", "synthetic-v14-ai-session")
    monkeypatch.delenv("JARVIS_ENCRYPTION_KEY", raising=False)
    database = Database("sqlite:///" + str(tmp_path / "ai-v14.db"))
    with database.transaction() as tx:
        policy.initialize(tx)
        budget.set_fx_rate(tx, currency="USD", sek_per_unit="12", source=REASON, observed_at=tx.now, actor=ADMIN)
        ai.configure(tx, {"enabled": True, "max_retries": 0, "reason": REASON}, ADMIN)
    yield database
    database.close()


def envelope(**changes):
    return {"action": "recommend", "text": "Internal finance observation with explicit uncertainties.",
            "reason": "No external action or new spending authority.", **changes}


def provider(content=None, *, finish="stop", completion_tokens=30):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"id": "synthetic-v14-result",
            "usage": {"cost": "0.001", "prompt_tokens": 80, "completion_tokens": completion_tokens},
            "choices": [{"finish_reason": finish, "message": {
                "content": json.dumps(envelope() if content is None else content) if not isinstance(content, str) else content}}]})
    return ai.OpenRouterAdapter(KEY, transport=httpx.MockTransport(handler)), calls


def run(tx, adapter, *, key="finance-internal", role="finance", contract="finance_analysis"):
    return ai.task(tx, role=role, text="Prepare an internal, source-based cost analysis.",
        workflow_id="v14-internal-analysis", idempotency_key=key, actor=ADMIN,
        adapter=adapter, response_contract=contract)


def activate(tx):
    state = ai.status(tx)
    return ai.activate(tx, {"statement": ai.ACTIVATE, "config_revision": state["config"]["revision"],
                           "reason": REASON}, ADMIN)


@pytest.mark.parametrize("change", [
    {"max_output_tokens": 4096}, {"max_input_chars": 11000}, {"timeout_seconds": 10}, {"max_retries": 1},
    {"reservation_usd_minor": 11}, {"per_call_limit_sek_minor": 201},
    {"prompt_usd_per_million": "2.00"}, {"completion_usd_per_million": "6.00"},
    {"model": "vendor/reviewed", "approved_models": ["vendor/reviewed"]},
])
def test_execution_changes_revoke_existing_paid_authorization_even_enabled_true(db, change):
    with db.transaction() as tx:
        before = ai.status(tx)
        assert before["effective_paid_enabled"]
        state = ai.configure(tx, {"enabled": True, "reason": REASON, **change}, ADMIN)
        assert state["config"]["enabled"] is False
        assert state["config"]["activation_required"] is True
        assert state["config"]["revision"] != before["config"]["revision"]
        assert "ai_activation_required" in {item["code"] for item in state["blockers"]}
        # Repeating an ordinary config submission cannot approve the new limits.
        assert not ai.configure(tx, {"enabled": True, "reason": REASON}, ADMIN)["config"]["enabled"]
        with pytest.raises(PolicyError) as error:
            ai.retry_preflight(tx, "finance")
        assert error.value.code == "ai_activation_required"
        assert tx.list("ai_trace") == tx.list("budget_entry") == []


def test_current_exact_approval_restores_paid_readiness_without_erasing_costs(db):
    adapter, calls = provider()
    with db.transaction() as tx:
        first = run(tx, adapter)
        old_trace = tx.get("ai_trace", first["trace_id"])
        old_entry = tx.get("budget_entry", first["reservation_id"])
        old_revision = ai.status(tx)["config"]["revision"]
        state = ai.configure(tx, {"max_output_tokens": 4096, "reason": REASON}, ADMIN)
        with pytest.raises(PolicyError) as error:
            ai.activate(tx, {"statement": ai.ACTIVATE, "config_revision": old_revision, "reason": REASON}, ADMIN)
        assert error.value.code == "ai_config_changed"
        restored = activate(tx)
        assert restored["effective_paid_enabled"] and not restored["config"].get("activation_required")
        assert restored["config"]["max_output_tokens"] == 4096
        assert restored["config"]["revision"] != state["config"]["revision"]
        preview = ai.retry_preflight(tx, "finance")
        assert preview["max_output_tokens"] == preview["global_max_output_tokens"] == 4096
        assert preview["reservation_sek_minor"] == 120
        assert tx.get("ai_trace", first["trace_id"]) == old_trace
        assert tx.get("budget_entry", first["reservation_id"]) == old_entry
        assert len(calls) == 1  # No provider call, new reserve or trace for preview/approval.


def test_unchanged_config_and_initial_legacy_admin_review_do_not_force_new_approval(db):
    with db.transaction() as tx:
        state = ai.status(tx)
        assert state["config"]["enabled"] and not state["config"].get("activation_required")
        unchanged = ai.configure(tx, {"reason": REASON}, ADMIN)
        assert unchanged["effective_paid_enabled"] and unchanged["config"]["revision"] == state["config"]["revision"]


@pytest.mark.parametrize("content", [envelope(), '{"action":"recommend","text":', {}, ["partial"]])
def test_any_explicit_length_finish_is_unaccepted_and_has_no_automatic_repair(db, content):
    adapter, calls = provider(content, finish="length", completion_tokens=512)
    with db.transaction() as tx:
        result = run(tx, adapter)
        assert result["status"] == "REVIEW_REQUIRED" and result["code"] == "ai_output_truncated"
        assert result["error_category"] == "truncation" and result["finish_reason"] == "length"
        assert result["output_tokens"] == result["max_output_tokens"] == 512
        trace = tx.get("ai_trace", result["trace_id"])
        assert not trace["output_valid"] and trace["finish_reason"] == "length"
        assert trace["max_output_tokens"] == trace["global_max_output_tokens"] == 512
        assert trace["timeout_seconds"] == 20 and trace["config_revision"] == ai.status(tx)["config"]["revision"]
        assert len(calls) == len(tx.list("budget_entry")) == len(tx.list("ai_trace")) == 1
        assert tx.list("ai_response_repair") == []
        assert tx.list("budget_entry")[0]["state"] == "SETTLED"
        assert run(tx, adapter) == result and len(calls) == 1


@pytest.mark.parametrize("content", [
    envelope(action="approve_payment"),
    '{"action":"recommend","text":"partial","permissions":',
])
def test_truncation_never_relabels_forbidden_authority_as_retryable(db, content):
    adapter, calls = provider(content, finish="length", completion_tokens=512)
    with db.transaction() as tx:
        result = run(tx, adapter)
        assert result["status"] == "REVIEW_REQUIRED" and result["code"] == "ai_policy_violation"
        assert len(calls) == 1 and tx.list("ai_response_repair") == []


def test_reviewed_role_caps_are_used_independently_and_visible_in_trace(db):
    adapter, calls = provider()
    with db.transaction() as tx:
        ai.configure(tx, {"max_output_tokens": 4096, "reason": REASON}, ADMIN)
        activate(tx)
        engine.configure_agent(tx, "finance", {"max_output_tokens": 2048, "reason": REASON}, ADMIN)
        engine.configure_agent(tx, "sales", {"max_output_tokens": 256, "reason": REASON}, ADMIN)
        finance = run(tx, adapter)
        sales = run(tx, adapter, role="sales", contract=None, key="sales-analysis")
        ceo = run(tx, adapter, role="ceo", contract=None, key="ceo-analysis")
        assert [json.loads(request.content)["max_tokens"] for request in calls] == [2048, 256, 4096]
        assert [result["max_output_tokens"] for result in (finance, sales, ceo)] == [2048, 256, 4096]
        assert all(result["global_max_output_tokens"] == 4096 for result in (finance, sales, ceo))
        assert ai.status(tx)["role_output_limits"]["finance"]["max_output_tokens"] == 2048
        assert ai.status(tx)["config"]["per_call_limit_sek_minor"] == ai.DEFAULTS["per_call_limit_sek_minor"]


def test_role_cap_above_new_global_limit_blocks_instead_of_clamping_or_raising_it(db):
    adapter, calls = provider()
    with db.transaction() as tx:
        ai.configure(tx, {"max_output_tokens": 4096, "reason": REASON}, ADMIN)
        activate(tx)
        engine.configure_agent(tx, "finance", {"max_output_tokens": 2048, "reason": REASON}, ADMIN)
        ai.configure(tx, {"max_output_tokens": 512, "reason": REASON}, ADMIN)
        activate(tx)
        with pytest.raises(PolicyError) as error:
            run(tx, adapter)
        assert error.value.code == "ai_role_output_limit"
        assert ai.status(tx)["role_output_limits"]["finance"]["blocked_reason"] == "ai_role_output_limit"
        assert not calls and not tx.list("budget_entry")


def test_effective_role_cap_change_at_checkpoint_prevents_provider_dispatch(db, monkeypatch):
    adapter, calls = provider()
    original = Transaction.checkpoint
    changes = []
    def checkpoint(tx):
        original(tx)
        if not changes:
            changes.append(True)
            engine.configure_agent(tx, "finance", {"max_output_tokens": 256, "reason": REASON}, ADMIN)
    monkeypatch.setattr(Transaction, "checkpoint", checkpoint)
    with db.transaction() as tx:
        result = run(tx, adapter)
        assert result["status"] == "BLOCKED" and calls == []
        assert tx.list("budget_entry")[0]["state"] == "RELEASED"


@pytest.mark.parametrize("timeout", [21, 60, 120])
def test_unverified_long_timeout_is_rejected_with_actual_supported_policy(db, timeout):
    with db.transaction() as tx:
        before = ai.status(tx)
        with pytest.raises(PolicyError) as error:
            ai.configure(tx, {"timeout_seconds": timeout, "reason": REASON}, ADMIN)
        assert error.value.code == "ai_timeout_unsupported"
        assert ai.status(tx)["config"] == before["config"]
        policy_info = before["timeout_policy"]
        assert policy_info["max_supported_seconds"] == 20 and policy_info["worker_lease_seconds"] == 90
        assert not policy_info["hosting_120_seconds_verified"]


def test_supported_timeout_is_enforced_by_the_real_http_client(db):
    adapter, calls = provider()
    with db.transaction() as tx:
        assert run(tx, adapter)["status"] == "COMPLETED"
    assert 0 < calls[0].extensions["timeout"]["read"] <= 20
    assert calls[0].extensions["timeout"]["connect"] <= 5


def test_retry_preview_honors_zero_global_ai_budget_without_mutation(db):
    with db.transaction() as tx:
        budget.configure_ai_limits(tx, {"daily_limit_minor": 0, "monthly_limit_minor": 0, "reason": REASON}, ADMIN)
        with pytest.raises(PolicyError) as error:
            ai.retry_preflight(tx, "finance")
        assert error.value.code == "ai_daily_budget"
        assert tx.list("budget_entry") == tx.list("ai_trace") == []


def test_invalid_persisted_timeout_is_blocked_before_any_provider_or_reserve(db):
    adapter, calls = provider()
    with db.transaction() as tx:
        config = tx.get("ai_config", "global")
        tx.put("ai_config", "global", {**config, "timeout_seconds": 120})
        assert "ai_timeout_unsupported" in {item["code"] for item in ai.status(tx)["blockers"]}
        with pytest.raises(PolicyError) as error:
            run(tx, adapter)
        assert error.value.code == "ai_timeout_unsupported"
        assert not calls and not tx.list("budget_entry")


def test_legacy_already_changed_enabled_config_needs_current_exact_retry_approval(db):
    adapter, calls = provider(finish="length", completion_tokens=512)
    with db.transaction() as tx:
        failed = run(tx, adapter)
        old_revision = tx.get("ai_trace", failed["trace_id"])["config_revision"]
        old_trace = tx.get("ai_trace", failed["trace_id"])
        old_entry = tx.get("budget_entry", failed["reservation_id"])
        # Reproduce v1.3's actual already-deployed configure behavior, rather
        # than exercising only the v1.4 future-change activation_required path.
        old = ai._config(tx)
        legacy = {name: old[name] for name in ai.DEFAULTS}
        legacy.update(max_output_tokens=4096, enabled=True)
        legacy["revision"] = fingerprint(legacy)
        tx.put("ai_config", "global", legacy)
        assert ai.status(tx)["effective_paid_enabled"]
        assert not legacy.get("activation_required")
        assert ai.retry_preflight(tx, "finance")["max_output_tokens"] == 4096
        with pytest.raises(PolicyError) as error:
            ai.require_retry_revision_approval(tx, old_revision)
        assert error.value.code == "ai_activation_required"
        assert tx.list("ai_paid_activation") == []
        activate(tx)
        proof = ai.require_retry_revision_approval(tx, old_revision)
        assert proof["required"] and proof["config_revision"] == ai.status(tx)["config"]["revision"]
        assert tx.get("ai_paid_activation", proof["approval_id"])["statement"] == ai.ACTIVATE
        assert tx.get("ai_trace", failed["trace_id"]) == old_trace
        assert tx.get("budget_entry", failed["reservation_id"]) == old_entry
        assert len(calls) == 1


def test_retry_approval_does_not_mutate_unchanged_legacy_config_or_trust_unknown_revision(db):
    with db.transaction() as tx:
        config = tx.get("ai_config", "global")
        assert ai.require_retry_revision_approval(tx, config["revision"]) == {
            "required": False, "config_revision": config["revision"]}
        for unknown in (None, "", "historical-unknown-revision"):
            with pytest.raises(PolicyError) as error:
                ai.require_retry_revision_approval(tx, unknown)
            assert error.value.code == "ai_activation_required"
        assert tx.get("ai_config", "global") == config
        assert tx.list("ai_paid_activation") == []


def test_only_exact_activation_creates_current_revision_retry_proof(db):
    with db.transaction() as tx:
        old_revision = tx.get("ai_config", "global")["revision"]
        first = activate(tx)
        original_proof = tx.list("ai_paid_activation")
        assert len(original_proof) == 1
        # A new reviewed configuration invalidates the old revision's proof.
        state = ai.configure(tx, {"max_output_tokens": 4096, "reason": REASON}, ADMIN)
        assert state["config"]["revision"] != first["config"]["revision"]
        with pytest.raises(PolicyError) as error:
            ai.require_retry_revision_approval(tx, old_revision)
        assert error.value.code == "ai_activation_required"
        assert tx.list("ai_paid_activation") == original_proof
        activate(tx)
        assert len(tx.list("ai_paid_activation")) == 2
        assert ai.require_retry_revision_approval(tx, old_revision)["required"]
