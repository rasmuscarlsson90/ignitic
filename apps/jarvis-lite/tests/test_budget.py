from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from decimal import Decimal

import pytest

from jarvis_lite import budget
from jarvis_lite.policy import PolicyError, get_settings
from jarvis_lite.store import Database


ADMIN = {"id": "budget-admin", "role": "admin"}
OPERATOR = {"id": "budget-operator", "role": "operator"}


@pytest.fixture
def database(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'budget.sqlite3'}")
    yield db
    db.close()


def reserve(tx, amount=1_000, key="action-1", **overrides):
    return budget.reserve(tx, vendor="fixture-provider", workflow_id="fixture-flow",
                          amount_minor=amount, idempotency_key=key, actor=ADMIN, **overrides)


def limits(tx, **overrides):
    settings = get_settings(tx)
    settings.update(overrides)
    tx.put("settings", "global", settings)


@contextmanager
def error_code(code):
    with pytest.raises(PolicyError) as result:
        yield result
    assert result.value.code == code


def test_reservation_and_settlement_are_idempotent_without_double_count(database):
    with database.transaction() as tx:
        first = reserve(tx)
        assert reserve(tx)["id"] == first["id"]
        with error_code("idempotency_conflict"):
            reserve(tx, amount=1_001)
        result = budget.summary(tx)
        assert result["reserved_sek_minor"] == 1_000
        assert result["actual_sek_minor"] == 0
        assert len(tx.list("budget_entry")) == 1
        budget.settle(tx, first["id"], actual_minor=750, input_tokens=120,
                      output_tokens=35, calls=1, actor=ADMIN)
        budget.settle(tx, first["id"], actual_minor=750, input_tokens=120,
                      output_tokens=35, calls=1, actor=ADMIN)
        result = budget.summary(tx)
        assert result["reserved_sek_minor"] == 0
        assert result["actual_sek_minor"] == 750
        assert result["calls"] == 1
        assert result["input_tokens"] == 120
        assert result["output_tokens"] == 35
        assert result["by_vendor"]["fixture-provider"]["actual_sek_minor"] == 750
        assert result["by_workflow"]["fixture-flow"]["calls"] == 1
        assert result["by_category"]["ai"]["actual_sek_minor"] == 750
        with error_code("settlement_conflict"):
            budget.settle(tx, first["id"], actual_minor=751, actor=ADMIN)


@pytest.mark.parametrize("limit_name,category", [
    ("total_budget_minor", "ai"), ("monthly_budget_minor", "ai"),
    ("daily_budget_minor", "ai"), ("rolling_tools_limit_minor", "tools"),
    ("rolling_tools_limit_minor", "ai"), ("monthly_infrastructure_limit_minor", "infrastructure"),
])
def test_each_separate_ceiling_includes_pending_reservations(database, limit_name, category):
    with database.transaction() as tx:
        limits(tx, **{limit_name: 1_500})
        reserve(tx, amount=1_000, category=category)
        reserve(tx, amount=500, key="action-2", category=category)
        with error_code("budget_exhausted"):
            reserve(tx, amount=1, key="action-3", category=category)
        assert budget.summary(tx)["reserved_sek_minor"] == 1_500


def test_ai_and_other_paid_tools_share_the_rolling_tools_ceiling(database):
    with database.transaction() as tx:
        limits(tx, rolling_tools_limit_minor=1_500)
        reserve(tx, amount=1_000, category="ai")
        with error_code("budget_exhausted"):
            reserve(tx, amount=501, category="tools", key="paid-tool")
        reserve(tx, amount=500, category="tools", key="paid-tool")


def test_cost_overrun_is_recorded_and_blocks_new_costs(database):
    with database.transaction() as tx:
        limits(tx, daily_budget_minor=1_000)
        entry = reserve(tx, amount=800)
        budget.settle(tx, entry["id"], actual_minor=1_200, actor=ADMIN)
        result = budget.summary(tx)
        assert result["actual_sek_minor"] == 1_200
        assert result["remaining_minor"]["daily"] == 0
        assert "daily" in result["blocked_windows"]
        with error_code("budget_exhausted"):
            reserve(tx, amount=1, key="blocked-after-overrun")


def test_only_definitively_uncharged_work_releases_reserved_money(database):
    with database.transaction() as tx:
        entry = reserve(tx)
        with error_code("charge_outcome_unknown"):
            budget.release(tx, entry["id"], reason="Provider timed out", actor=ADMIN)
        assert budget.summary(tx)["reserved_sek_minor"] == 1_000
        budget.release(tx, entry["id"], reason="Provider confirmed cancellation before execution",
                       definitive_no_charge=True, actor=ADMIN)
        budget.release(tx, entry["id"], reason="Repeat confirmed cancellation",
                       definitive_no_charge=True, actor=ADMIN)
        assert budget.summary(tx)["reserved_sek_minor"] == 0
        with error_code("reservation_released"):
            reserve(tx)
        with error_code("reservation_released"):
            budget.settle(tx, entry["id"], actual_minor=1_000, actor=ADMIN)


def test_kill_switch_blocks_new_costs_but_preserves_actual_liabilities(database):
    with database.transaction() as tx:
        entry = reserve(tx)
        limits(tx, kill_switch=True)
        with error_code("kill_switch"):
            reserve(tx, key="after-kill")
        budget.settle(tx, entry["id"], actual_minor=900, actor=ADMIN)
        assert budget.summary(tx)["actual_sek_minor"] == 900
        with error_code("charge_already_settled"):
            budget.release(tx, entry["id"], reason="Cannot hide an actual charge",
                           definitive_no_charge=True, actor=ADMIN)


def test_documented_fx_is_conservatively_rounded_and_frozen_per_action(database):
    with database.transaction() as tx:
        budget.set_fx_rate(tx, currency="USD", sek_per_unit=Decimal("10.51"),
                           source="Explicit test quotation; not a live rate", observed_at=tx.now, actor=ADMIN)
        entry = reserve(tx, amount=7, currency="USD")
        assert entry["reserved_sek_minor"] == 74
        assert entry["fx"]["source"] == "Explicit test quotation; not a live rate"
        budget.set_fx_rate(tx, currency="USD", sek_per_unit="20", source="New test quotation",
                           observed_at=tx.now, actor=ADMIN)
        budget.settle(tx, entry["id"], actual_minor=8, currency="USD", actor=ADMIN)
        assert budget.summary(tx)["actual_sek_minor"] == 85
        with error_code("settlement_currency_mismatch"):
            budget.settle(tx, entry["id"], actual_minor=8, currency="EUR", actor=ADMIN)


def test_missing_stale_or_invalid_fx_does_not_create_a_reservation(database):
    with database.transaction() as tx:
        with error_code("fx_rate_required"):
            reserve(tx, currency="EUR")
        with error_code("admin_required"):
            budget.set_fx_rate(tx, currency="EUR", sek_per_unit="11", source="Not authorized",
                               observed_at=tx.now, actor=OPERATOR)
        for invalid in ("NaN", "Infinity", "0", "-1", "10001"):
            with error_code("invalid_fx_rate"):
                budget.set_fx_rate(tx, currency="EUR", sek_per_unit=invalid, source="Invalid test rate",
                                   observed_at=tx.now, actor=ADMIN)
        budget.set_fx_rate(tx, currency="EUR", sek_per_unit="11", source="Old test quotation",
                           observed_at=tx.now - budget.MONTH - 1, actor=ADMIN)
        with error_code("fx_rate_stale"):
            reserve(tx, currency="EUR")
        with error_code("invalid_fx_rate"):
            budget.set_fx_rate(tx, currency="SEK", sek_per_unit="0.5", source="Not allowed",
                               observed_at=tx.now, actor=ADMIN)
        assert tx.list("budget_entry") == []


@pytest.mark.parametrize("amount", [-1, 1.1, "100", True, float("nan")])
def test_noninteger_or_negative_money_is_rejected(database, amount):
    with database.transaction() as tx:
        with error_code("invalid_amount"):
            reserve(tx, amount=amount)
        assert tx.list("budget_entry") == []


def test_threshold_notifications_are_persistent_and_not_duplicated_on_settlement(database):
    with database.transaction() as tx:
        for index, amount in enumerate((5_000, 2_500, 1_500, 1_000)):
            entry = reserve(tx, amount=amount, key=f"step-{index}")
            budget.settle(tx, entry["id"], actual_minor=amount, actor=ADMIN)
        alerts = [item for item in tx.list("budget_alert") if item["window"] == "daily"]
        assert sorted(item["threshold_percent"] for item in alerts) == [50, 75, 90, 100]
        assert len(alerts) == 4
    with database.transaction() as tx:
        assert len([item for item in budget.summary(tx)["alerts"] if item["window"] == "daily"]) == 4


def test_reservations_do_not_age_out_until_settled(database):
    with database.transaction() as tx:
        limits(tx, total_budget_minor=1_000_000, monthly_budget_minor=1_000_000,
               daily_budget_minor=1_000_000)
        original_now = tx.now
        tx.now -= 40 * budget.DAY
        old_pending = reserve(tx, amount=20_000, key="old-pending")
        old_settled = reserve(tx, amount=20_000, key="old-settled")
        budget.settle(tx, old_settled["id"], actual_minor=20_000, actor=ADMIN)
        tx.now = original_now
        result = budget.summary(tx)
        assert result["usage"]["tools_30d"] == 20_000
        assert result["usage"]["daily"] == 20_000
        assert result["usage"]["total"] == 40_000
        assert old_pending["state"] == "RESERVED"
        with error_code("budget_exhausted"):
            reserve(tx, amount=30_001, key="new-paid-tool")


def test_external_cloud_invoice_is_recorded_even_after_kill_or_budget_ceiling(database):
    with database.transaction() as tx:
        limits(tx, kill_switch=True)
        data = dict(vendor="cloud-fixture", workflow_id="cloud-billing", actual_minor=250_000,
                    source="Already billed test invoice", idempotency_key="invoice-1", actor=ADMIN)
        invoice = budget.record_cost(tx, **data)
        assert budget.record_cost(tx, **data)["id"] == invoice["id"]
        with error_code("idempotency_conflict"):
            budget.record_cost(tx, **{**data, "workflow_id": "same-invoice-new-workflow"})
        with error_code("admin_required"):
            budget.record_cost(tx, **{**data, "actor": OPERATOR, "idempotency_key": "invoice-2"})
        result = budget.summary(tx)
        assert result["actual_sek_minor"] == 250_000
        assert result["reserved_sek_minor"] == 0
        assert result["remaining_minor"]["total"] == 0
        assert result["forecast_days_remaining"] == 0


def test_multi_connection_race_cannot_oversubscribe_daily_budget(database):
    # Independent engines/connections prevent a process-local mutex from making
    # this pass accidentally: SQLite's write transaction must serialize them.
    connections = [Database(database.url) for _ in range(20)]
    try:
        def attempt(item):
            index, connection = item
            try:
                with connection.transaction() as tx:
                    reserve(tx, amount=1_000, key=f"concurrent-{index}")
                return "reserved"
            except PolicyError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=20) as executor:
            outcomes = list(executor.map(attempt, enumerate(connections)))
        assert outcomes.count("reserved") == 10
        assert outcomes.count("budget_exhausted") == 10
        with database.transaction() as tx:
            assert budget.summary(tx)["usage"]["daily"] == 10_000
            assert len(tx.list("budget_entry")) == 10
    finally:
        for connection in connections:
            connection.close()


def test_restart_retains_unsettled_cost_and_does_not_release_emergency_stop(tmp_path):
    url = f"sqlite:///{tmp_path / 'restart.sqlite3'}"
    first = Database(url)
    with first.transaction() as tx:
        reserve(tx, amount=2_000)
        limits(tx, kill_switch=True)
    first.close()
    restarted = Database(url)
    try:
        with restarted.transaction() as tx:
            assert budget.summary(tx)["reserved_sek_minor"] == 2_000
            with error_code("kill_switch"):
                reserve(tx, key="restart-must-not-resume")
    finally:
        restarted.close()
