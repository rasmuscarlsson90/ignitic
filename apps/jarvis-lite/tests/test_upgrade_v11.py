from copy import deepcopy
import os
import secrets
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql
import pytest

from jarvis_lite import policy, upgrade
from jarvis_lite.policy import PolicyError
from jarvis_lite.store import Database


ADMIN = {"id": "reviewing-admin", "role": "admin"}
OPERATOR = {"id": "reviewing-operator", "role": "operator"}


@pytest.fixture
def db(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'upgrade.sqlite'}")
    yield database
    database.close()


def real_lead(tx, id="real"):
    return tx.put("lead", id, {"company": "Actual Example AB", "email": "hello@actual.example",
        "state": "QUALIFIED", "record_mode": "REAL", "is_test": False,
        "provenance": {"kind": "approved_source", "source_id": "licensed-directory", "approved": True}})


@pytest.mark.parametrize("record,expected", [
    ({"record_mode": "REAL", "is_test": False}, "UNCLASSIFIED"),
    ({"is_test": False, "source_id": "manual"}, "UNCLASSIFIED"),
    ({"record_mode": "REAL", "is_test": True}, "DEMO"),
    ({"record_mode": "REAL", "source_id": "demo"}, "DEMO"),
    ({"record_mode": "REAL", "provider": "demo"}, "DEMO"),
    ({"record_mode": "REAL", "is_test": False, "provenance": {"source_id": "approved", "approved": True}}, "REAL"),
])
def test_real_classification_requires_explicit_provenance_and_demo_always_wins(record, expected):
    assert upgrade.classify_record(record) == expected


def test_additive_upgrade_preserves_existing_policy_keys_users_sessions_costs_and_ids(db):
    with db.transaction() as tx:
        policy.initialize(tx)
        original = deepcopy(policy.get_settings(tx))
        original.update(mode="LIVE", total_budget_minor=177777, rolling_tools_limit_minor=44444)
        tx.put("settings", "global", original)
        tx.put("user", "operator", {"role": "operator", "password_hash": "retained-scrypt-hash"})
        tx.put("session", "retained-session", {"user_id": "operator", "csrf": "retained-csrf", "expires_at": tx.now + 10000})
        policy.set_secret(tx, "OPENROUTER_API_KEY", "private-key-retained", ADMIN)
        tx.put("budget_entry", "external-invoice", {"state": "ACTUAL", "actual_sek_minor": 12345})
        tx.put("lead", "old-real-unknown", {"company": "Unknown legacy record", "is_test": False})
        tx.put("lead", "old-demo", {"is_test": True, "company": "Synthetic"})
        tx.put("payment", "retained-payment", {"provider": "demo", "status": "CONFIRMED", "amount_minor": 500000})
        before = {kind: deepcopy(tx.list(kind)) for kind in ("user", "session", "secret", "budget_entry", "payment")}
        audit_count = len([item for item in tx.audits() if item["action"] == "upgrade.v11_applied"])
        # Simulate the first v1.1 startup after v1.0. Repeated initialization is a no-op.
        tx.delete("upgrade", upgrade.UPGRADE_ID)
        upgrade.initialize(tx)
        upgrade.initialize(tx)
        assert tx.get("settings", "global") == original
        for kind, values in before.items():
            assert tx.list(kind) == values
        assert policy.get_secret(tx, "OPENROUTER_API_KEY") == "private-key-retained"
        assert tx.get("lead", "old-real-unknown")["record_mode"] == "UNCLASSIFIED"
        assert tx.get("lead", "old-demo")["record_mode"] == "DEMO"
        assert len([item for item in tx.audits() if item["action"] == "upgrade.v11_applied"]) == audit_count + 1


def test_three_historical_acceptance_tasks_are_tagged_without_deleting_genuine_tasks(db):
    with db.transaction() as tx:
        tx.put("lead", "demo-lead", {"company": "Demo Customer AB", "is_test": True})
        tests = []
        for index, category in enumerate(("sales", "support", "billing"), 1):
            id = f"case-{index}"
            tx.put("enquiry", id, {"customer_id": "demo-lead", "text": f"A synthetic {category} case"})
            tx.put("manual_task", "task-" + id, {"title": "Review enquiry response draft", "enquiry_id": id, "status": "PENDING", "role": "DELIVERY"})
            tests.append({"enquiry_id": id, "case": index, "expected": category})
        tx.put("delivery", "delivery", {"lead_id": "demo-lead", "mode": "test", "tests": tests})
        tx.put("enquiry", "genuine", {"text": "An actual customer question"})
        genuine = tx.put("manual_task", "genuine-task", {"title": "Review enquiry response draft", "enquiry_id": "genuine", "status": "PENDING"})
        upgrade.initialize(tx)
        tasks = tx.list("manual_task")
        assert len(tasks) == 4
        assert tx.get("manual_task", "genuine-task") == genuine
        assert sum(task["status"] == "TEST_ARTIFACT" for task in tasks) == 3
        hydrated = upgrade.hydrate_tasks(tx)
        case_tasks = [task for task in hydrated if task.get("test_artifact")]
        assert {task["test_case"] for task in case_tasks} == {1, 2, 3}
        assert all(task["record_mode"] == "DEMO" and task["company"] == "Demo Customer AB" for task in case_tasks)
        upgrade.initialize(tx)
        assert len(tx.list("manual_task")) == 4
        assert len([item for item in tx.audits() if item["action"] == "upgrade.test_task_classified"]) == 3


def test_metrics_never_promote_demo_or_test_receipts_or_unclassified_leads_to_real_revenue(db):
    with db.transaction() as tx:
        lead = real_lead(tx)
        tx.put("lead", "demo", {"record_mode": "DEMO", "is_test": True})
        tx.put("lead", "unknown", {"is_test": False})
        tx.put("payment", "demo-money", {"lead_id": lead["id"], "provider": "demo", "livemode": True, "status": "CONFIRMED", "amount_minor": 999999})
        tx.put("payment", "test-money", {"lead_id": lead["id"], "provider": "stripe", "livemode": False, "status": "CONFIRMED", "amount_minor": 888888})
        payment = tx.put("payment", "real-money", {"lead_id": lead["id"], "provider": "stripe", "livemode": True,
             "status": "CONFIRMED", "amount_minor": 500000, "total_minor": 625000,
             "provider_reference": "cs_live_checked", "confirmation_event_id": "receipt"})
        assert upgrade.metrics(tx)["real_confirmed_revenue_minor"] == 0
        tx.put("payment_event", "receipt", {"lead_id": lead["id"], "payment_id": payment["id"],
             "provider": "stripe", "livemode": True, "signature_verified": True, "status": "paid", "currency": "SEK",
             "amount_minor": payment["total_minor"], "provider_reference": payment["provider_reference"]})
        result = upgrade.metrics(tx)
        assert result["real_leads"] == result["demo_leads"] == result["unclassified_leads"] == 1
        assert result["real_confirmed_revenue_minor"] == 500000
        assert result["simulated_revenue_minor"] == 999999
        assert result["real_paying_customers"] == 1
        tx.put("settings", "global", {"mode": "LIVE"})
        assert upgrade.metrics(tx) == result


def test_resolving_draft_task_does_not_authorize_payments_resume_jobs_or_change_workflow(db):
    with db.transaction() as tx:
        lead = real_lead(tx)
        enquiry = tx.put("enquiry", "enquiry", {"customer_id": lead["id"], "text": "Actual enquiry", "draft_status": "UNSENT"})
        tx.put("manual_task", "review", {"title": "Review enquiry response draft", "status": "PENDING", "enquiry_id": enquiry["id"]})
        tx.put("manual_task", "finance", {"title": "Manually approve the payment request", "status": "PENDING", "role": "FINANCE"})
        tx.put("manual_task", "job", {"title": "Review an interrupted agent job", "status": "PENDING", "type": "job_review"})
        with pytest.raises(PolicyError):
            upgrade.resolve_task(tx, "review", "A reviewed response draft", {"id": "viewer", "role": "viewer"})
        resolved = upgrade.resolve_task(tx, "review", "Draft reviewed; no send authorized", OPERATOR)
        assert resolved["status"] == "RESOLVED"
        assert upgrade.resolve_task(tx, "review", "Second review is idempotent", OPERATOR) == resolved
        for task_id in ("finance", "job"):
            with pytest.raises(PolicyError, match="dedicated"):
                upgrade.resolve_task(tx, task_id, "This must not grant financial authority", ADMIN)
        assert tx.get("lead", lead["id"]) == lead
        assert tx.get("enquiry", enquiry["id"])["draft_status"] == "UNSENT"
        assert not tx.list("payment")


@pytest.mark.skipif(not os.getenv("JARVIS_TEST_DATABASE_URL"), reason="PostgreSQL upgrade requires an isolated local test server")
def test_postgres_v10_records_survive_upgrade_and_process_reopen():
    supplied = os.environ["JARVIS_TEST_DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    parsed = urlsplit(supplied)
    name = "jarvis_upgrade_test_" + secrets.token_hex(10)
    with psycopg.connect(supplied, autocommit=True, connect_timeout=10) as maintenance:
        maintenance.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        url = urlunsplit((parsed.scheme, parsed.netloc, "/" + name, parsed.query, parsed.fragment))
        database = None
        try:
            database = Database(url)
            with database.transaction() as tx:
                policy.get_settings(tx)
                policy.set_secret(tx, "OPENROUTER_API_KEY", "synthetic-preserved-key", ADMIN)
                user = tx.put("user", "retained-user", {"role": "operator", "active": True, "password_hash": "retained"})
                session = tx.put("session", "retained-session", {"user_id": user["id"], "csrf": "retained", "expires_at": tx.now + 9999})
                settings = deepcopy(policy.get_settings(tx))
                tx.put("lead", "legacy", {"is_test": False, "company": "Unclassified existing business"})
                tx.put("payment", "legacy-money", {"lead_id": "legacy", "provider": "demo", "status": "CONFIRMED", "amount_minor": 500000})
                tx.audit("legacy", "retained.audit", {"source": "existing-data"})
            database.close()
            database = Database(url)
            with database.transaction() as tx:
                policy.initialize(tx)
                assert tx.get("settings", "global") == settings
                assert tx.get("user", user["id"]) == user
                assert tx.get("session", session["id"]) == session
                assert policy.get_secret(tx, "OPENROUTER_API_KEY") == "synthetic-preserved-key"
                assert tx.get("lead", "legacy")["record_mode"] == "UNCLASSIFIED"
                assert tx.get("payment", "legacy-money")["amount_minor"] == 500000
                assert upgrade.metrics(tx)["real_confirmed_revenue_minor"] == 0
                assert any(item["action"] == "retained.audit" for item in tx.audits())
                marker = tx.get("upgrade", upgrade.UPGRADE_ID)
                policy.initialize(tx)
                assert tx.get("upgrade", upgrade.UPGRADE_ID) == marker
        finally:
            if database:
                database.close()
            maintenance.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
