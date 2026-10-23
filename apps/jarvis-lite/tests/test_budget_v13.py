"""AI-specific ceilings and dimensional accounting; no provider I/O."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal

import pytest

from jarvis_lite import budget, jobs, policy
from jarvis_lite.policy import PolicyError
from jarvis_lite.store import Database


ADMIN = {"id": "v13-budget-reviewer", "role": "admin"}
OPERATOR = {"id": "v13-budget-operator", "role": "operator"}
REASON = "Explicit review of internal AI limits and pending liabilities"


@pytest.fixture
def db(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'v13-budget.sqlite'}")
    with database.transaction() as tx:
        policy.initialize(tx)
        budget.set_fx_rate(tx, currency="USD", sek_per_unit="12", source=REASON,
                           observed_at=tx.now, actor=ADMIN)
    yield database
    database.close()


def reserve(tx, key, amount=10, **extra):
    return budget.reserve(tx, vendor="synthetic-model-provider", workflow_id="synthetic-internal-work",
        amount_minor=amount, currency="SEK", category="ai", idempotency_key=key, actor=OPERATOR, **extra)


def test_ai_ceiling_defaults_are_read_only_and_cannot_expand_owner_mandate(db):
    with db.transaction() as tx:
        before = policy.get_settings(tx)
        limits = budget.ai_limits(tx)
        assert limits["daily_limit_minor"] == 10000 and limits["monthly_limit_minor"] == 50000
        assert tx.list("ai_budget_policy") == [] and policy.get_settings(tx) == before
        configured = budget.configure_ai_limits(tx, {"daily_limit_minor": 50000, "monthly_limit_minor": 50000,
                                                    "reason": REASON}, ADMIN)
        assert configured["daily_limit_minor"] == 10000  # existing global cap remains authoritative
        assert configured["monthly_limit_minor"] == 50000
        assert policy.get_settings(tx)["total_budget_minor"] == 200000
        assert policy.get_settings(tx)["rolling_tools_limit_minor"] == 50000


def test_configuring_ai_ceiling_is_admin_reviewed_and_fences_already_queued_work(db):
    with db.transaction() as tx:
        job = jobs.enqueue(tx, "operations_report", {}, idempotency_key="queued-before-limit-review", actor=OPERATOR)
        epoch = policy.get_settings(tx)["safety_epoch"]
        with pytest.raises(PolicyError) as denied:
            budget.configure_ai_limits(tx, {"daily_limit_minor": 10, "monthly_limit_minor": 20, "reason": REASON}, OPERATOR)
        assert denied.value.code == "admin_required"
        configured = budget.configure_ai_limits(tx, {"daily_limit_minor": 10, "monthly_limit_minor": 20, "reason": REASON}, ADMIN)
        assert configured["daily_remaining_minor"] == 10
        assert policy.get_settings(tx)["safety_epoch"] == epoch + 1
        assert tx.get("job", job["id"])["status"] == "paused"
        assert not tx.list("ai_config") and not tx.list("ai_trace")


@pytest.mark.parametrize("changes", [
    {"daily_limit_minor": True}, {"monthly_limit_minor": "500"}, {"daily_limit_minor": -1},
    {"monthly_limit_minor": 50001}, {"daily_limit_minor": 11, "monthly_limit_minor": 10},
    {"reason": "short"}, {"allow_paid_ai": True},
])
def test_ai_limit_input_validation_fails_before_any_activation(db, changes):
    with db.transaction() as tx:
        with pytest.raises(PolicyError) as error:
            budget.configure_ai_limits(tx, {"daily_limit_minor": 100, "monthly_limit_minor": 200,
                                            "reason": REASON, **changes}, ADMIN)
        assert error.value.status == 422
        assert tx.list("ai_budget_policy") == tx.list("ai_trace") == []


def test_pending_ai_liability_never_ages_out_and_settled_cost_uses_rolling_windows(db):
    with db.transaction() as tx:
        budget.configure_ai_limits(tx, {"daily_limit_minor": 20, "monthly_limit_minor": 30, "reason": REASON}, ADMIN)
        held = reserve(tx, "old-unknown", 10)
        held["created_at"] = tx.now - 90 * budget.DAY
        tx.put("budget_entry", held["id"], held)
        current = reserve(tx, "current", 10)
        with pytest.raises(PolicyError) as daily:
            reserve(tx, "not-allowed", 1)
        assert daily.value.code == "ai_daily_budget"
        budget.settle(tx, current["id"], actual_minor=10, actor=ADMIN)
        tx.now += budget.DAY + 1
        assert budget.ai_limits(tx)["daily_used_minor"] == 10
        assert budget.ai_limits(tx)["monthly_used_minor"] == 20
        reserve(tx, "next-day", 10)
        tx.now += budget.DAY + 1
        budget.configure_ai_limits(tx, {"daily_limit_minor": 30, "monthly_limit_minor": 30, "reason": REASON}, ADMIN)
        with pytest.raises(PolicyError) as monthly:
            reserve(tx, "monthly-block", 1)
        assert monthly.value.code == "ai_monthly_budget"
        assert budget.ai_limits(tx)["daily_used_minor"] == 20  # both pending reservations remain


def test_lowered_ai_budget_is_checked_again_with_existing_reservations(db):
    with db.transaction() as tx:
        reserve(tx, "pending", 50)
        budget.configure_ai_limits(tx, {"daily_limit_minor": 40, "monthly_limit_minor": 100, "reason": REASON}, ADMIN)
        with pytest.raises(PolicyError) as blocked:
            budget.guard_ai_limits(tx)
        assert blocked.value.code == "ai_daily_budget"
        assert budget.ai_limits(tx)["daily_remaining_minor"] == 0
        assert tx.list("budget_entry")[0]["state"] == "RESERVED"


def test_concurrent_connections_cannot_both_consume_last_ai_allowance(db):
    with db.transaction() as tx:
        budget.configure_ai_limits(tx, {"daily_limit_minor": 10, "monthly_limit_minor": 10, "reason": REASON}, ADMIN)

    def attempt(number):
        connection = Database(db.url)
        try:
            with connection.transaction() as tx:
                try:
                    reserve(tx, str(number), 10)
                    return "reserved"
                except PolicyError as error:
                    return error.code
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(attempt, range(4)))
    assert results.count("reserved") == 1 and results.count("ai_daily_budget") == 3
    with db.transaction() as tx:
        assert len(tx.list("budget_entry")) == 1


def test_model_role_mission_accounting_keeps_provider_usd_separate_from_booked_sek(db):
    with db.transaction() as tx:
        tx.now = datetime.fromisoformat("2026-10-23T00:30:00+02:00").timestamp()
        budget.set_fx_rate(tx, currency="USD", sek_per_unit="12", source=REASON, observed_at=tx.now, actor=ADMIN)
        entry = budget.reserve(tx, vendor="openrouter", workflow_id="mission:source-backed:ceo", amount_minor=10,
            currency="USD", category="ai", idempotency_key="provider-cost", actor=OPERATOR)
        entry.update(role="ceo", mission_id="source-backed")
        tx.put("budget_entry", entry["id"], entry)
        budget.settle(tx, entry["id"], actual_minor=1, currency="USD", input_tokens=100, output_tokens=20, actor=ADMIN)
        tx.put("ai_trace", "provider-trace", {"reservation_id": entry["id"], "role": "ceo", "mission_id": "source-backed",
            "model": "openai/gpt-4o-mini", "simulated": False})
        tx.put("agent_result", "provider-result", {"trace_id": "provider-trace", "provider": "openrouter", "simulated": False,
            "reported_cost_usd": "0.001"})
        reserve(tx, "unknown-liability", 100)
        budget.record_cost(tx, vendor="synthetic-hosting", workflow_id="hosting", actual_minor=250, source=REASON,
            idempotency_key="synthetic-invoice", category="infrastructure", actor=ADMIN)
        before = tx.list("budget_entry")
        result = budget.analytics(tx)
        assert result["booked_cost_minor"] == 262 and result["reserved_cost_minor"] == 100
        assert result["periods"]["today"]["booked_cost_minor"] == 262
        assert result["periods"]["calendar_month"]["provider_reported_usd"] == "0.001"
        model = next(item for item in result["by_model"] if item["id"] == "openai/gpt-4o-mini")
        assert model["booked_cost_minor"] == 12 and Decimal(model["provider_reported_usd"]) == Decimal("0.001")
        assert model["booked_calls"] == 1 and model["input_tokens"] == 100
        role = next(item for item in result["by_agent"] if item["id"] == "ceo")
        assert role["booked_cost_minor"] == 12
        assert next(item for item in result["by_mission"] if item["id"] == "source-backed")["booked_cost_minor"] == 12
        assert result["estimated_future_revenue_minor"] is None
        assert tx.list("budget_entry") == before  # reporting never books estimates
