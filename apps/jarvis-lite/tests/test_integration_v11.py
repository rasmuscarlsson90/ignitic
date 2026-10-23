"""Preparation and finance integration: synthetic data and mocked provider HTTP only."""
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from jarvis_lite import auth, budget, connectors, outreach, policy, upgrade, workflow
from jarvis_lite.app import create_app
from jarvis_lite.store import Database


ADMIN = {"id": "v11-integration-admin", "role": "admin"}
PASSWORD = "Only-synthetic-v11-integration-password"


@pytest.fixture
def running(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_ENV", "test")
    for name in ("JARVIS_ALLOW_LIVE", "JARVIS_ALLOW_LIVE_CONTACT", "JARVIS_ALLOW_PAID_AI",
                 "JARVIS_ALLOW_RESEARCH_NETWORK", "JARVIS_ALLOW_LIVE_PAYMENTS"):
        monkeypatch.setenv(name, "false")
    db = Database(f"sqlite:///{tmp_path / 'v11.db'}")
    with db.transaction() as tx:
        auth.create_user(tx, "admin@example.com", PASSWORD, "admin", bootstrap=True)
        auth.create_user(tx, "operator@example.com", PASSWORD, "operator", actor=ADMIN)
    with TestClient(create_app(db, worker_enabled=False)) as client:
        yield client, db
    db.close()


def login(client, role="admin"):
    result = client.post("/api/auth/login", json={"email": role + "@example.com", "password": PASSWORD})
    assert result.status_code == 200
    return {"X-CSRF-Token": result.json()["csrf_token"]}


def test_configured_source_to_real_lead_to_blocked_preparation_has_no_effects(running):
    client, _ = running
    headers = login(client)
    source = {"name": "Synthetic approved directory", "kind": "upload", "url": "https://directory.example.com/approved",
              "approved": True, "license_reviewed": True, "legal_review_reference": "Synthetic licensed-source evidence",
              "cost_evidence": "Synthetic free approved upload", "data_mode": "REAL"}
    assert client.post("/api/research/sources/approved-directory", json=source).status_code == 403
    assert client.post("/api/research/sources/approved-directory", headers=headers, json=source).status_code == 200
    imported = client.post("/api/research/sources/approved-directory/ingest", headers=headers, json={
        "idempotency_key": "approved-upload", "records": [{"company": "Synthetic Incoming AB", "email": "info@synthetic.example",
        "evidence": "The company receives incoming customer support enquiries by email.",
        "entity_type": "legal_person", "address_type": "generic"}]})
    assert imported.status_code == 200, imported.text
    dashboard = client.get("/api/dashboard").json()
    lead = dashboard["leads"][0]
    assert lead["record_mode"] == "REAL" and not lead["is_test"]
    assert dashboard["v11"]["metrics"]["real_leads"] == 1
    queued = client.post(f"/api/pipeline/leads/{lead['id']}/advance", headers=headers,
                         json={"idempotency_key": "prepare-real"})
    assert queued.status_code == 200
    assert client.post("/api/jobs/run", headers=headers).status_code == 200
    dashboard = client.get("/api/dashboard").json()
    assert dashboard["leads"][0]["state"] == "QUALIFIED"
    assert not dashboard["messages"] and not dashboard["payments"]
    assert dashboard["budget"]["actual_sek_minor"] == 0
    assert dashboard["v11"]["outreach"]["live_contact_authorized"] is False
    assert dashboard["v11"]["ai"]["effective_paid_enabled"] is False
    assert dashboard["v11"]["metrics"]["real_confirmed_revenue_minor"] == 0


def test_new_admin_routes_preserve_roles_and_reject_forged_approval(running):
    client, _ = running
    operator = login(client, "operator")
    for path in ("/api/ai/config", "/api/outreach/config", "/api/outreach/review", "/api/pipeline/config"):
        assert client.post(path, headers=operator, json={}).status_code == 403
    admin = login(client)
    result = client.post("/api/outreach/review", headers=admin, json={
        "statement": "I APPROVE LIVE CUSTOMER CONTACT", "test_id": "forged",
        "legal_review_reference": "Synthetic only, never authorizes a real message",
        "reason": "Try an invalid client-provided safety result", "passed": True})
    assert result.status_code == 422
    ready = client.get("/api/v1.1/readiness")
    assert ready.status_code == 200
    assert ready.json()["outreach"]["live_contact_authorized"] is False


def test_acceptance_cases_create_no_identical_pending_review_tasks(running):
    client, _ = running
    headers = login(client)
    client.post("/api/demo/run", headers=headers, json={"idempotency_key": "three-cases"})
    assert client.post("/api/jobs/run", headers=headers).status_code == 200
    data = client.get("/api/dashboard").json()
    assert len(data["deliveries"][0]["tests"]) == 3
    assert not [task for task in data["tasks"] if task["title"] == "Review enquiry response draft" and task["status"] == "PENDING"]
    assert data["metrics"]["confirmed_revenue_minor"] == 0


def test_unsubscribe_link_get_is_readonly_and_one_click_post_works_under_stop(running):
    client, db = running
    with db.transaction() as tx:
        settings = policy.get_settings(tx)
        tx.put("settings", "global", {**settings, "public_origin": "https://operator.example.com"})
        lead = tx.create("lead", {"company": "Synthetic Opt Out AB", "email": "info@optout.example", "state": "CONTACTED",
                                  "is_test": False, "record_mode": "REAL", "provenance": {"approved": True, "source_id": "licensed"}})
        url = outreach.unsubscribe_headers(tx, lead)["List-Unsubscribe"].strip("<>")
        token = parse_qs(urlparse(url).query)["token"][0]
        policy.set_kill_switch(tx, True, "Test receiving opt out under emergency stop", ADMIN)
    path = "/api/contact/unsubscribe?token=" + token
    assert client.get(path).status_code == 200
    with db.transaction() as tx:
        assert not tx.list("suppression")
    assert client.post(path, content="List-Unsubscribe=One-Click", headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code == 200
    with db.transaction() as tx:
        assert tx.get("lead", lead["id"])["state"] == "UNSUBSCRIBED"


@pytest.mark.parametrize("livemode", [True, False])
def test_explicit_prepayment_signed_receipt_plans_delivery_never_claims_installation(running, monkeypatch, livemode):
    _, db = running
    with db.transaction() as tx:
        lead = tx.create("lead", {"company": "Synthetic Prepayment AB", "email": "info@prepayment.example", "state": "REPLIED",
            "is_test": False, "record_mode": "REAL", "provenance": {"approved": True, "source_id": "licensed"}})
        proposal = workflow.prepare_proposal(tx, lead["id"], {"billing_stage": "before_delivery"}, actor=ADMIN, key="new-prepay-offer")
        proposal.update(status="SENT")
        tx.put("proposal", proposal["id"], proposal)
        lead = tx.get("lead", lead["id"])
        lead["state"] = "PROPOSAL_SENT"
        tx.put("lead", lead["id"], lead)
        # Synthetic already-verified inbound gateway evidence, never a real email.
        event = tx.create("inbound_event", {"lead_id": lead["id"], "provider": "brevo", "from": lead["email"],
            "signature_verified": True, "text": "I accept proposal " + proposal["id"]})
        workflow.accept_proposal(tx, lead["id"], {"event_id": event["id"]}, actor=ADMIN, key="customer-accepts")
        settings = policy.get_settings(tx)
        tx.put("settings", "global", {**settings, "mode": "LIVE", "live_approved": True, "legal_reviewed": True,
            "public_origin": "https://operator.example.com", "daily_budget_minor": 50000})
        workflow.prepare_payment_gate(tx, lead["id"], actor=ADMIN, key="review-prepayment")
        payment = connectors.create_payment_draft(tx, lead_id=lead["id"], proposal_id=proposal["id"],
            amount_minor=proposal["amount_minor"], vat_minor=proposal["vat_minor"], service_id=proposal["service_id"],
            reference="PROPOSAL-" + proposal["id"], idempotency_key="exact-draft", actor=ADMIN)
        connectors.approve_payment(tx, payment["id"], actor=ADMIN)
        policy.set_secret(tx, "STRIPE_WEBHOOK_SECRET", "synthetic-signing-secret", ADMIN)
    monkeypatch.setenv("JARVIS_ALLOW_LIVE", "true")
    with db.transaction() as tx, pytest.raises(policy.PolicyError, match="separate deployment capability"):
        connectors.request_payment(tx, payment["id"], idempotency_key="blocked-before-enable", actor=ADMIN)
    monkeypatch.setenv("JARVIS_ALLOW_LIVE_PAYMENTS", "true")
    adapter = connectors.StripeAdapter("sk_" + ("live" if livemode else "test") + "_synthetic_never_a_real_key",
        transport=httpx.MockTransport(lambda req:
        httpx.Response(200, json={"id": "cs_synthetic_live", "url": "https://checkout.stripe.com/c/test", "livemode": livemode})))
    with db.transaction() as tx:
        requested = connectors.request_payment(tx, payment["id"], idempotency_key="mock-only-request", actor=ADMIN, adapter=adapter)
        assert requested.get("cost_reconciliation_required", False) is livemode
        assert budget.summary(tx)["actual_sek_minor"] == 0
        payload = {"id": "evt-synthetic-paid", "type": "checkout.session.completed", "livemode": livemode,
            "data": {"object": {"id": "cs_synthetic_live", "amount_total": proposal["total_minor"], "currency": "sek",
            "payment_status": "paid", "metadata": {"jarvis_payment_id": payment["id"]}}}}
        raw = json.dumps(payload).encode()
        timestamp = int(tx.now)
        signature = f"t={timestamp},v1=" + connectors.sign_event(raw, "synthetic-signing-secret", timestamp)
        connectors.receive_payment_event(tx, provider="stripe", raw_body=raw, signature=signature)
        connectors.receive_payment_event(tx, provider="stripe", raw_body=raw, signature=signature)
        assert tx.get("lead", lead["id"])["state"] == ("DELIVERY_IN_PROGRESS" if livemode else "PAYMENT_REQUESTED")
        plan = tx.get("delivery_plan", "delivery-plan-" + lead["id"])
        assert plan["production_installed"] is False if livemode else plan is None
        assert upgrade.metrics(tx)["real_confirmed_revenue_minor"] == (proposal["amount_minor"] if livemode else 0)
        assert tx.get("payment", payment["id"])["status"] == ("CONFIRMED" if livemode else "TEST_CONFIRMED")
        assert len(tx.list("payment_event")) == 1


def test_weakened_legacy_settings_do_not_weaken_effective_mandate(running):
    _, db = running
    with db.transaction() as tx:
        settings = policy.get_settings(tx)
        tx.put("settings", "global", {**settings, "total_budget_minor": 900000, "rolling_tools_limit_minor": 900000,
            "minimum_list_price_minor": 1, "minimum_discounted_price_minor": 1, "max_discount_bps": 9000})
        with pytest.raises(policy.PolicyError, match="minimum"):
            workflow.price_proposal(tx, {"list_price_minor": 400000})
        with pytest.raises(policy.PolicyError, match="discount"):
            workflow.price_proposal(tx, {"discount_bps": 2000})
        assert budget.summary(tx)["limits_minor"]["total"] == 200000
        assert budget.summary(tx)["limits_minor"]["tools_30d"] == 50000
        assert policy.get_settings(tx)["total_budget_minor"] == 900000
