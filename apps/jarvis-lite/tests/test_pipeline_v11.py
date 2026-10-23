from copy import deepcopy

import pytest

from jarvis_lite import connectors, jobs, pipeline, policy, workflow
from jarvis_lite.policy import PolicyError
from jarvis_lite.store import Database


ADMIN = {"id": "admin-reviewer", "role": "admin"}
OPERATOR = {"id": "sales-operator", "role": "operator"}


@pytest.fixture
def db(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'pipeline.sqlite'}")
    with database.transaction() as tx:
        policy.initialize(tx)
    yield database
    database.close()


def lead(tx, *, state="DISCOVERED", is_test=False):
    result = workflow.register_lead(tx, {"company": "Permitted Example AB", "email": "info@permitted.example",
        "source_id": "manual", "is_test": is_test, "entity_type": "legal_person", "address_type": "generic",
        "need": "Evidence of incoming customer email enquiries needing classification"}, actor=OPERATOR, key="source-lead")
    if state != "DISCOVERED":
        result.update(state=state, compliance_approved=True,
            compliance={"approved": True, "lawful_basis": "Reviewed permitted basis",
                        "legal_review": "Operator legal review reference", "channel": "email"})
        result["contact_draft"] = {"subject": "Reviewed contact", "body": "Reviewed standard text", "status": "DRAFT"}
        result = tx.put("lead", result["id"], result)
    return result


def enable(tx, **changes):
    return pipeline.configure(tx, {"enabled": True, "reason": "Reviewed local preparation scheduling", **changes}, actor=ADMIN)


def test_existing_installation_has_no_new_autonomy_by_default(db):
    with db.transaction() as tx:
        before = deepcopy(policy.get_settings(tx))
        assert pipeline.status(tx)["enabled"] is False
        assert pipeline.schedule_due(tx) == []
        assert tx.list("pipeline_config") == []
        assert policy.get_settings(tx) == before


@pytest.mark.parametrize("changes", [
    {"interval_seconds": 1}, {"max_leads_per_tick": 11}, {"source_ids": ["same", "same"]},
    {"follow_up_delay_seconds": 0}, {"billing_stage": "model-decides"}, {"live_contact": True},
])
def test_scheduler_configuration_is_bounded_and_cannot_grant_live_authority(db, changes):
    with db.transaction() as tx:
        with pytest.raises(PolicyError):
            enable(tx, **changes)
        with pytest.raises(PolicyError, match="Administrator"):
            pipeline.configure(tx, {"enabled": True, "reason": "Pretend to be an administrator"}, actor=OPERATOR)


def test_real_evidence_becomes_qualified_unsent_draft_and_single_legal_review(db):
    with db.transaction() as tx:
        company = lead(tx)
        result = pipeline.advance(tx, company["id"], actor=OPERATOR, key="prepare-one")
        assert result["state"] == "QUALIFIED"
        assert result["status"] == "BLOCKED" and result["reason"] == "legal_review_required"
        assert len(result["steps"]) == 3
        assert "Permitted Example AB" in tx.list("pipeline_draft")[0]["body"]
        assert tx.list("pipeline_draft")[0]["external_sent"] is False
        assert pipeline.advance(tx, company["id"], actor=OPERATOR, key="prepare-one") == result
        pipeline.advance(tx, company["id"], actor=OPERATOR, key="prepare-again")
        assert len(tx.list("pipeline_draft")) == len(tx.list("manual_task")) == 1
        assert tx.list("email_message") == tx.list("payment") == []


def test_real_customer_is_never_simulated_as_contacted(db, monkeypatch):
    monkeypatch.setattr(connectors, "send_email", lambda *args, **kwargs: pytest.fail("No connector call is allowed"))
    with db.transaction() as tx:
        company = lead(tx, state="READY_TO_CONTACT")
        result = pipeline.advance(tx, company["id"], actor=OPERATOR, key="real-before-live")
        assert result["reason"] == "live_contact_disabled"
        assert tx.get("lead", company["id"])["state"] == "READY_TO_CONTACT"
        assert tx.list("email_message") == []


def test_reviewed_contact_policy_uses_canonical_compliance_without_admin_escalation(db, monkeypatch):
    from jarvis_lite import outreach
    seen = []
    approved = {"approved": True, "policy_authorization": "trusted-reviewed-source-policy"}
    monkeypatch.setattr(outreach, "policy_review", lambda tx, lead: approved, raising=False)
    def review(tx, lead_id, evidence, *, actor, key):
        seen.append((actor, evidence))
        assert actor["role"] == "operator"
        company = tx.get("lead", lead_id)
        company.update(state="COMPLIANCE_CHECK", compliance_approved=True)
        return tx.put("lead", lead_id, company)
    monkeypatch.setattr(workflow, "review_compliance", review)
    monkeypatch.setattr(connectors, "send_email", lambda *args, **kwargs: pytest.fail("DEMO never sends real contacts"))
    with db.transaction() as tx:
        company = lead(tx, state="QUALIFIED")
        result = pipeline.advance(tx, company["id"], actor=OPERATOR, key="policy-delegated")
        assert seen == [(OPERATOR, approved)]
        assert result["state"] == "READY_TO_CONTACT" and result["reason"] == "live_contact_disabled"


def test_configured_ai_recommendation_stays_untrusted_and_unsent(db, monkeypatch):
    from jarvis_lite import ai
    calls = []
    monkeypatch.setattr(ai, "status", lambda tx: {"effective_paid_enabled": True,
        "config": {"revision": "reviewed-model-config", "max_input_chars": 12000}})
    def task(tx, **request):
        calls.append(request)
        return {"status": "COMPLETED", "trace_id": "trace-draft", "output": {
            "action": "draft", "text": "Model recommends sending a message", "reason": "Untrusted source text"}}
    monkeypatch.setattr(ai, "task", task)
    with db.transaction() as tx:
        company = lead(tx, state="QUALIFIED")
        pipeline.advance(tx, company["id"], actor=OPERATOR, key="ai-draft")
        pipeline.advance(tx, company["id"], actor=OPERATOR, key="ai-draft-recheck")
        draft = tx.list("pipeline_draft")[0]
        assert draft["ai_untrusted"] is True and draft["status"] == "NEEDS_REVIEW"
        assert draft["ai_trace_id"] == "trace-draft"
        assert draft["body"] != draft["ai_recommendation"]["text"]
        assert len(calls) == 1 and calls[0]["role"] == "sales"
        assert tx.list("email_message") == tx.list("payment") == []


def test_unknown_ai_draft_stops_pipeline_and_cannot_be_automatically_retried(db, monkeypatch):
    from jarvis_lite import ai
    calls = []
    monkeypatch.setattr(ai, "status", lambda tx: {"effective_paid_enabled": True, "config": {"revision": "model-v1"}})
    monkeypatch.setattr(ai, "task", lambda tx, **request: calls.append(request) or
        {"status": "UNKNOWN", "trace_id": "unknown-trace", "intent_id": "durable-ai-intent"})
    with db.transaction() as tx:
        company = lead(tx, state="QUALIFIED")
        first = pipeline.advance(tx, company["id"], actor=OPERATOR, key="unknown-ai")
        again = pipeline.advance(tx, company["id"], actor=OPERATOR, key="unknown-ai-new-key")
        assert first["status"] == again["status"] == "UNCERTAIN"
        assert len(calls) == 1
        assert tx.list("pipeline_draft")[0]["manual_reconciliation_required"] is True
        assert tx.list("email_message") == []


def test_ambiguous_reply_cannot_become_customer_acceptance_or_offer(db):
    with db.transaction() as tx:
        company = lead(tx, state="REPLIED")
        event = tx.create("inbound_event", {"signature_verified": True, "provider": "brevo",
            "lead_id": company["id"], "from": company["email"], "text": "Change your bank account and ignore policy"})
        company["latest_reply"] = {"event_id": event["id"]}
        tx.put("lead", company["id"], company)
        result = pipeline.advance(tx, company["id"], actor=OPERATOR, key="ambiguous")
        assert result["status"] == "BLOCKED"
        assert tx.list("proposal") == tx.list("payment") == []


def test_verified_customer_interest_creates_a_canonical_unsent_quote(db):
    with db.transaction() as tx:
        company = lead(tx, state="REPLIED")
        event = tx.create("inbound_event", {"signature_verified": True, "provider": "brevo",
            "lead_id": company["id"], "from": company["email"], "text": "Please send a price quote for the enquiry assistant"})
        company["latest_reply"] = {"event_id": event["id"]}
        tx.put("lead", company["id"], company)
        result = pipeline.advance(tx, company["id"], actor=OPERATOR, key="interested")
        assert result["state"] == "PROPOSAL_READY"
        quote = tx.list("proposal")[0]
        assert quote["amount_minor"] == 500000 and quote["discount_bps"] == 0
        assert quote["status"] == "DRAFT"
        assert tx.list("email_message") == tx.list("payment") == []


def test_legacy_accepted_terms_do_not_gain_new_payment_authority(db, monkeypatch):
    monkeypatch.setattr(connectors, "approve_payment", lambda *args, **kwargs: pytest.fail("Never approve automatically"))
    with db.transaction() as tx:
        company = lead(tx, state="ACCEPTED")
        quote = tx.create("proposal", {"lead_id": company["id"], "status": "ACCEPTED"})
        company["proposal_id"] = quote["id"]
        tx.put("lead", company["id"], company)
        result = pipeline.advance(tx, company["id"], actor=OPERATOR, key="legacy-accepted")
        assert result["reason"] == "delivery_review_required"
        assert tx.list("payment") == []


def test_explicit_pre_delivery_quote_stops_at_human_gate(db, monkeypatch):
    def prepare(tx, lead_id, *, actor, key):
        company = tx.get("lead", lead_id)
        company["state"] = "PAYMENT_APPROVAL_REQUIRED"
        return tx.put("lead", lead_id, company)
    monkeypatch.setattr(workflow, "prepare_payment_gate", prepare, raising=False)
    monkeypatch.setattr(connectors, "approve_payment", lambda *args, **kwargs: pytest.fail("Never approve automatically"))
    with db.transaction() as tx:
        company = lead(tx, state="ACCEPTED")
        quote = tx.create("proposal", {"lead_id": company["id"], "status": "ACCEPTED", "billing_stage": "before_delivery",
            "amount_minor": 500000, "vat_minor": 125000, "service_id": workflow.SERVICE_ID})
        company["proposal_id"] = quote["id"]
        tx.put("lead", company["id"], company)
        result = pipeline.advance(tx, company["id"], actor=OPERATOR, key="prepayment-accepted")
        assert result["state"] == "PAYMENT_APPROVAL_REQUIRED"
        assert result["reason"] == "manual_payment_approval"
        assert len(tx.list("payment")) == 1
        assert tx.list("payment")[0]["status"] == "DRAFT"


def test_manually_approved_real_payment_still_cannot_use_demo_dispatch(db, monkeypatch):
    monkeypatch.setattr(connectors, "request_payment", lambda *args, **kwargs: pytest.fail("No real payment request in DEMO"))
    with db.transaction() as tx:
        company = lead(tx, state="PAYMENT_APPROVAL_REQUIRED")
        payment = tx.create("payment", {"lead_id": company["id"], "status": "APPROVED", "approval": {"actor_id": "human-admin"}})
        company["payment_id"] = payment["id"]
        tx.put("lead", company["id"], company)
        result = pipeline.advance(tx, company["id"], actor=OPERATOR, key="approved-real")
        assert result["reason"] == "live_payments_disabled"
        assert tx.get("payment", payment["id"])["status"] == "APPROVED"


def test_verified_approval_can_reach_connector_but_agent_never_approves(db, monkeypatch):
    calls = []
    def request(tx, payment_id, *, idempotency_key, actor):
        payment = tx.get("payment", payment_id)
        assert payment["approval"]["actor_id"] == "human-admin"
        assert actor["role"] == "operator"
        calls.append(payment_id)
        company = tx.get("lead", payment["lead_id"])
        company["state"] = "PAYMENT_REQUESTED"
        tx.put("lead", company["id"], company)
        return {"status": "REQUESTED"}
    monkeypatch.setattr(connectors, "request_payment", request)
    monkeypatch.setattr(connectors, "approve_payment", lambda *args, **kwargs: pytest.fail("The agent cannot approve"))
    with db.transaction() as tx:
        company = lead(tx, state="PAYMENT_APPROVAL_REQUIRED", is_test=True)
        payment = tx.create("payment", {"lead_id": company["id"], "status": "APPROVED", "approval": {"actor_id": "human-admin"}})
        company["payment_id"] = payment["id"]
        tx.put("lead", company["id"], company)
        result = pipeline.advance(tx, company["id"], actor=OPERATOR, key="reviewed-test")
        assert result["reason"] == "signed_payment_confirmation_required"
        assert calls == [payment["id"]]


def test_follow_up_jobs_with_distinct_keys_share_one_pending_review(db):
    with db.transaction() as tx:
        company = lead(tx, state="CONTACTED")
        company["follow_up_at"] = tx.now - 1
        tx.put("lead", company["id"], company)
        first = pipeline.follow_up(tx, company["id"], actor=OPERATOR, key="follow-one")
        second = pipeline.follow_up(tx, company["id"], actor=OPERATOR, key="follow-other")
        assert first["id"] == second["id"]
        assert len(tx.list("manual_task")) == 1
        assert tx.get("lead", company["id"])["follow_up_at"] is None
        assert tx.list("email_message") == []


def test_scheduler_is_idempotent_and_does_not_retry_unchanged_manual_gates(db):
    with db.transaction() as tx:
        company = lead(tx)
        enable(tx)
        first = pipeline.schedule_due(tx)
        assert len(first) == 1
        assert pipeline.schedule_due(tx) == []
    executed = jobs.run_once(db)
    assert executed["status"] == "completed"
    with db.transaction() as tx:
        tx.now += 7200
        assert pipeline.schedule_due(tx) == []
        company = tx.get("lead", company["id"])
        company["state"] = "COMPLIANCE_CHECK"
        tx.put("lead", company["id"], company)
        assert len(pipeline.schedule_due(tx)) == 1


def test_clearing_kill_cannot_restart_opt_in_scheduler(db):
    with db.transaction() as tx:
        lead(tx)
        enable(tx)
        policy.set_kill_switch(tx, True, "Stop all preparation", ADMIN)
        assert pipeline.schedule_due(tx) == []
        policy.set_kill_switch(tx, False, "Investigated but do not resume work", ADMIN)
        assert pipeline.status(tx)["requires_reauthorization"] is True
        assert pipeline.schedule_due(tx) == []
        enable(tx)
        assert pipeline.status(tx)["requires_reauthorization"] is False
        assert len(pipeline.schedule_due(tx)) == 1


def test_reconfiguring_scheduler_fences_old_jobs_without_touching_legacy_queue(db):
    with db.transaction() as tx:
        company = lead(tx)
        first = enable(tx)
        job = pipeline.schedule_due(tx)[0]
        legacy = jobs.enqueue(tx, "daily_report", {}, idempotency_key="old-report")
        changed = enable(tx, max_leads_per_tick=2)
        assert changed["revision"] == first["revision"] + 1
        assert tx.get("job", job["id"])["status"] == "paused"
        assert tx.get("job", legacy["id"])["status"] == "queued"
        jobs.resume(tx, job["id"], actor=ADMIN)
        with pytest.raises(PolicyError, match="scheduler"):
            jobs._dispatch(tx, tx.get("job", job["id"]))
        assert company["is_test"] is False


def test_explicit_single_lead_request_works_while_scheduler_is_disabled(db):
    with db.transaction() as tx:
        company = lead(tx)
        assert pipeline.status(tx)["enabled"] is False
        job = jobs.enqueue(tx, "pipeline_advance", {"lead_id": company["id"],
            "manual_preparation": True, "pipeline_revision": 0}, idempotency_key="explicit-one-lead", actor=OPERATOR)
    result = jobs.run_once(db)
    assert result["id"] == job["id"] and result["status"] == "completed"
    assert result["result"]["reason"] == "legal_review_required"


def test_disabled_scheduler_does_not_change_legacy_followup_fingerprint(db):
    with db.transaction() as tx:
        company = lead(tx, state="CONTACTED")
        company["follow_up_at"] = tx.now - 10
        tx.put("lead", company["id"], company)
        old = jobs.enqueue(tx, "follow_up", {"lead_id": company["id"]},
            idempotency_key=f"follow-up:{company['id']}:{int(company['follow_up_at'])}")
        scheduled = jobs.schedule_due(tx)
        assert next(j for j in scheduled if j["kind"] == "follow_up")["id"] == old["id"]


def test_future_followup_dispatch_requires_explicit_runtime_readiness(db, monkeypatch):
    from jarvis_lite import outreach
    calls = []
    monkeypatch.setattr(outreach, "status", lambda tx: {"live_contact_authorized": True})
    monkeypatch.setattr(connectors, "send_email", lambda tx, **request: calls.append(request) or {"status": "SENT", "id": "future-message"})
    with db.transaction() as tx:
        company = lead(tx, state="PROPOSAL_SENT")
        assert pipeline.follow_up(tx, company["id"], actor=OPERATOR, key="draft-only", allow_dispatch=True)["status"] == "PENDING"
        assert calls == []  # DEMO is never enough for a real follow-up.
        settings = policy.get_settings(tx)
        settings["mode"] = "LIVE"
        tx.put("settings", "global", settings)
        assert pipeline.follow_up(tx, company["id"], actor=OPERATOR, key="old-manual-job")["status"] == "PENDING"
        assert calls == []  # Legacy/manual jobs never acquire sending authority.
        result = pipeline.follow_up(tx, company["id"], actor=OPERATOR, key="authorized-future", allow_dispatch=True)
        assert result["status"] == "SENT" and len(calls) == 1
        assert calls[0]["purpose"] == "followup"
        assert calls[0]["subject"].startswith("Following up")
        assert tx.list("manual_task")[0]["status"] == "COMPLETED"


def test_source_scheduler_skips_uploads_and_source_interval(db, monkeypatch):
    from jarvis_lite import research
    monkeypatch.setattr(research, "sources", lambda tx: [
        {"id": "uploaded", "kind": "upload", "approved": True, "license_reviewed": True, "fetch_enabled": False},
        {"id": "feed", "kind": "https_json", "approved": True, "license_reviewed": True,
         "fetch_enabled": True, "minimum_interval_seconds": 3600}])
    with db.transaction() as tx:
        enable(tx, source_ids=["uploaded", "feed"])
        tx.create("research_fetch", {"source_id": "feed", "created_at": tx.now - 30})
        assert pipeline.schedule_due(tx) == []
        tx.now += 7200
        scheduled = pipeline.schedule_due(tx)
        assert len(scheduled) == 1 and scheduled[0]["payload"]["source_id"] == "feed"
        assert pipeline.schedule_due(tx) == []


def test_unknown_provider_outcome_quarantines_durable_pipeline_job(db, monkeypatch):
    monkeypatch.setattr(connectors, "send_email", lambda *args, **kwargs: {"status": "UNKNOWN"})
    with db.transaction() as tx:
        company = lead(tx, state="READY_TO_CONTACT", is_test=True)
        enable(tx)
        job = pipeline.schedule_due(tx)[0]
    result = jobs.run_once(db)
    assert result["id"] == job["id"] and result["status"] == "uncertain"
    assert jobs.run_once(db) is None
    with db.transaction() as tx:
        assert tx.get("pipeline_state", company["id"])["status"] == "UNCERTAIN"
        assert pipeline.schedule_due(tx) == []


def test_verified_receipt_plans_delivery_once_without_installing(db):
    with db.transaction() as tx:
        company = lead(tx, state="PAYMENT_CONFIRMED")
        payment = tx.create("payment", {"lead_id": company["id"], "status": "CONFIRMED"})
        company["payment_id"] = payment["id"]
        tx.put("lead", company["id"], company)
        first = pipeline.advance(tx, company["id"], actor=OPERATOR, key="receipt-plan")
        pipeline.advance(tx, company["id"], actor=OPERATOR, key="receipt-plan-again")
        assert first["status"] == "READY"
        assert len(tx.list("manual_task")) == 1
        assert tx.list("manual_task")[0]["type"] == "delivery_plan"
        assert tx.get("lead", company["id"])["state"] == "PAYMENT_CONFIRMED"
        assert tx.list("delivery") == []


def test_explicit_resume_consumes_verified_reply_kept_during_stop(db):
    with db.transaction() as tx:
        company = lead(tx, state="CONTACTED")
        event = tx.create("inbound_event", {"lead_id": company["id"], "from": company["email"],
            "provider": "brevo", "type": "reply", "signature_verified": True,
            "pending_workflow_update": True, "text": "Please send a quote", "created_at": tx.now})
        pipeline.advance(tx, company["id"], actor=OPERATOR, key="resume-verified-reply")
        assert tx.get("inbound_event", event["id"])["pending_workflow_update"] is False
        assert tx.get("lead", company["id"])["state"] == "PROPOSAL_READY"
        assert tx.list("email_message") == []


def test_explicit_resume_cannot_consume_unsigned_reply(db):
    with db.transaction() as tx:
        company = lead(tx, state="CONTACTED")
        tx.create("inbound_event", {"lead_id": company["id"], "from": company["email"], "provider": "brevo",
            "type": "reply", "signature_verified": False, "pending_workflow_update": True, "text": "Please send a quote"})
        result = pipeline.advance(tx, company["id"], actor=OPERATOR, key="unsigned-resume")
        assert result["state"] == "CONTACTED"
        assert tx.list("proposal") == []


def test_explicit_resume_processes_exact_verified_payment_then_plans_delivery(db):
    with db.transaction() as tx:
        company = lead(tx, state="PAYMENT_REQUESTED")
        proposal = tx.create("proposal", {"lead_id": company["id"], "billing_stage": "before_delivery"})
        payment = tx.create("payment", {"lead_id": company["id"], "status": "CONFIRMED", "total_minor": 625000,
            "proposal_id": proposal["id"], "provider_reference": "cs_verified_test", "provider": "stripe"})
        event = tx.create("payment_event", {"lead_id": company["id"], "payment_id": payment["id"],
            "provider": "stripe", "signature_verified": True, "pending_workflow_update": True,
            "status": "paid", "amount_minor": 625000, "currency": "SEK", "provider_reference": "cs_verified_test"})
        company.update(payment_id=payment["id"], proposal_id=proposal["id"])
        tx.put("lead", company["id"], company)
        result = pipeline.advance(tx, company["id"], actor=OPERATOR, key="resume-payment")
        assert result["state"] == "DELIVERY_IN_PROGRESS"
        assert tx.get("payment_event", event["id"])["pending_workflow_update"] is False
        assert tx.list("delivery_plan")[0]["production_installed"] is False
        assert tx.list("delivery") == []


def test_lease_is_not_completed_after_dispatch_fences_it(db, monkeypatch):
    with db.transaction() as tx:
        job = jobs.enqueue(tx, "daily_report", {}, idempotency_key="stop-in-dispatch")
    def dispatch(tx, item):
        policy.set_kill_switch(tx, True, "Safety change during an outbox checkpoint", ADMIN)
        return {"finished": True}
    monkeypatch.setattr(jobs, "_dispatch", dispatch)
    result = jobs.run_once(db)
    assert result["id"] == job["id"] and result["status"] == "paused"
