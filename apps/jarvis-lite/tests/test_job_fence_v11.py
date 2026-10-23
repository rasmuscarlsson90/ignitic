"""A single-job pause must fence durable dispatch without toggling global stop."""
import hashlib

import httpx
import pytest

from jarvis_lite import ai, budget, connectors, jobs, outreach, pipeline, policy, workflow
from jarvis_lite.store import Database, Transaction


ADMIN = {"id": "fence-reviewer", "role": "admin"}
OPERATOR = {"id": "fence-operator", "role": "operator"}
KEY = "synthetic-lease-fence-provider-key"
REFERENCE = "Synthetic operator review used only by the offline lease-fence test"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_ENV", "test")
    monkeypatch.setenv("JARVIS_SESSION_SECRET", "isolated-lease-fence-tests-not-a-production-key")
    monkeypatch.delenv("JARVIS_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("KILL_SWITCH", "false")
    database = Database(f"sqlite:///{tmp_path / 'job-fence.sqlite'}")
    with database.transaction() as tx:
        policy.initialize(tx)
        budget.set_fx_rate(tx, currency="USD", sek_per_unit="12", source="Synthetic test FX reference",
                           observed_at=tx.now, actor=ADMIN)
    yield database
    database.close()


def pause_during_checkpoint(monkeypatch, db):
    """Pause a claimed job at the actual durable commit/re-lock boundary."""
    original_checkpoint = Transaction.checkpoint
    interrupted = []

    def checkpoint(tx):
        if interrupted or tx.execution_fence is None:
            return original_checkpoint(tx)
        original_commit = tx.conn.commit

        def commit_then_pause():
            original_commit()
            job_id, lease_token = tx.execution_fence
            with db.transaction() as control:
                current = control.get("job", job_id)
                assert current["status"] == "leased" and current["lease_token"] == lease_token
                before = policy.get_settings(control)["safety_epoch"]
                jobs.pause(control, job_id, actor=ADMIN, reason="Revoke only this job during its durable handoff")
                assert policy.get_settings(control)["safety_epoch"] == before
            interrupted.append(job_id)

        # The real checkpoint still performs its normal commit and re-lock.
        # Only the competing administrator action is injected between them.
        with monkeypatch.context() as race:
            race.setattr(tx.conn, "commit", commit_then_pause)
            return original_checkpoint(tx)

    monkeypatch.setattr(Transaction, "checkpoint", checkpoint)
    return interrupted


def queue_manual_preparation(tx, lead_id):
    return jobs.enqueue(tx, "pipeline_advance", {"lead_id": lead_id, "manual_preparation": True,
        "pipeline_revision": pipeline.status(tx)["revision"]}, idempotency_key="single-job-fence-request", actor=OPERATOR)


def prepare_live_contact(tx, monkeypatch):
    monkeypatch.setenv("JARVIS_ALLOW_LIVE", "true")
    monkeypatch.setenv("JARVIS_ALLOW_LIVE_CONTACT", "true")
    settings = policy.get_settings(tx)
    settings.update(mode="LIVE", live_approved=True, legal_reviewed=True,
                    approved_senders=["sales@example.com"], public_origin="https://jarvis.example.com")
    tx.put("settings", "global", settings)
    lead = tx.create("lead", {"company": "Synthetic Fence Customer AB", "email": "contact@example.org",
        "domain": "example.org", "is_test": False, "record_mode": "REAL", "state": "READY_TO_CONTACT",
        "entity_type": "legal_person", "address_type": "generic",
        "provenance": {"approved": True, "source_id": "reviewed-synthetic-source"},
        "compliance": {"approved": True, "lawful_basis": REFERENCE, "legal_review": REFERENCE,
                       "channel": "email", "approval_mode": "LIVE"}})
    message = workflow.contact_message(lead)
    lead["contact_draft"] = message
    tx.put("lead", lead["id"], lead)
    tx.put("connector_config", "email", {"sender": "sales@example.com", "approved": True,
        "spf": True, "dkim": True, "dmarc": True, "legal_review": True,
        "approved_templates": [hashlib.sha256((message["subject"] + "\n" + message["body"]).encode()).hexdigest()]})
    for name in ("BREVO_API_KEY", "EMAIL_WEBHOOK_SECRET", "EMAIL_DELIVERY_WEBHOOK_SECRET"):
        policy.set_secret(tx, name, KEY, ADMIN)
    outreach.configure(tx, {"provider": "brevo", "enabled": True, "sender": "sales@example.com",
        **dict.fromkeys(outreach.REFERENCE_FIELDS, REFERENCE)}, ADMIN)
    checks = outreach.run_checks(tx, ADMIN)
    assert checks["passed"] is True
    outreach.record_review(tx, {"statement": outreach.APPROVE, "test_id": checks["id"],
        "legal_review_reference": REFERENCE, "reason": REFERENCE}, ADMIN)
    assert outreach.status(tx)["live_contact_authorized"] is True
    return lead


def test_pausing_leased_pipeline_job_blocks_email_after_durable_checkpoint(db, monkeypatch):
    requests = []
    def provider(request):
        requests.append(request)
        pytest.fail("The revoked job reached the email provider")
    class MockBrevoAdapter(connectors.BrevoAdapter):
        def __init__(self, api_key):
            super().__init__(api_key, transport=httpx.MockTransport(provider))
    monkeypatch.setattr(connectors, "BrevoAdapter", MockBrevoAdapter)
    with db.transaction() as tx:
        lead = prepare_live_contact(tx, monkeypatch)
        job = queue_manual_preparation(tx, lead["id"])
        epoch = policy.get_settings(tx)["safety_epoch"]
    interrupted = pause_during_checkpoint(monkeypatch, db)
    original_failure = jobs._failure
    def fail_if_checkpoint_not_reached(database, claimed, error):
        if not interrupted:
            raise error
        return original_failure(database, claimed, error)
    monkeypatch.setattr(jobs, "_failure", fail_if_checkpoint_not_reached)
    result = jobs.run_once(db)
    assert interrupted == [job["id"]], result
    assert result["id"] == job["id"] and result["status"] == "paused"
    assert requests == [] and jobs.run_once(db) is None
    with db.transaction() as tx:
        assert policy.get_settings(tx)["safety_epoch"] == epoch
        assert policy.get_settings(tx)["kill_switch"] is False
        assert tx.get("lead", lead["id"])["state"] == "READY_TO_CONTACT"
        assert tx.list("external_intent")[0]["status"] == "ATTEMPTED"
        assert tx.list("email_message")[0]["status"] == "UNKNOWN"
        assert tx.list("budget_entry")[0]["state"] == "RESERVED"
        assert tx.get("job", job["id"])["lease_token"] is None


def test_pausing_leased_pipeline_job_blocks_ai_after_durable_checkpoint(db, monkeypatch):
    monkeypatch.setenv("JARVIS_ALLOW_PAID_AI", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    requests = []
    def provider(request):
        requests.append(request)
        pytest.fail("The revoked job reached the model provider")
    adapter = ai.OpenRouterAdapter(KEY, transport=httpx.MockTransport(provider))
    monkeypatch.setattr(ai, "OpenRouterAdapter", lambda *args, **kwargs: adapter)
    with db.transaction() as tx:
        ai.configure(tx, {"enabled": True, "reason": REFERENCE}, ADMIN)
        lead = tx.create("lead", {"company": "Synthetic Qualified Customer AB", "email": "info@example.org",
            "domain": "example.org", "is_test": False, "record_mode": "REAL", "state": "QUALIFIED",
            "need": "Cited evidence of incoming customer enquiries needing classification"})
        job = queue_manual_preparation(tx, lead["id"])
        epoch = policy.get_settings(tx)["safety_epoch"]
    interrupted = pause_during_checkpoint(monkeypatch, db)
    result = jobs.run_once(db)
    assert interrupted == [job["id"]]
    assert result["id"] == job["id"] and result["status"] == "paused"
    assert requests == [] and jobs.run_once(db) is None
    with db.transaction() as tx:
        assert policy.get_settings(tx)["safety_epoch"] == epoch
        assert tx.get("job", job["id"])["lease_token"] is None
        # AI proves that dispatch never began and can release its reservation;
        # email's conservative attempted intent remains held in the test above.
        assert tx.list("budget_entry")[0]["state"] == "RELEASED"
        assert tx.list("ai_trace")[0]["status"] == "BLOCKED"
        assert tx.list("email_message") == []
