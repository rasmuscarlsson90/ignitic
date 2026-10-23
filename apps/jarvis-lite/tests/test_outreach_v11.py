import hashlib
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from jarvis_lite import connectors, outreach, policy, research, workflow
from jarvis_lite.policy import PolicyError
from jarvis_lite.store import Database


ADMIN = {"id": "outreach-test-admin", "role": "admin"}
OPERATOR = {"id": "outreach-test-operator", "role": "operator"}
REFERENCE = "Synthetic operator evidence reference; not an actual DNS verification"
GATEWAY_SECRET = "synthetic-delivery-gateway-secret"


@pytest.fixture
def db(tmp_path, monkeypatch):
    # Offline adapter contract coverage; v1.2's outer release lock is tested separately.
    monkeypatch.setattr(policy, "INTERNAL_ONLY_RELEASE", False)
    monkeypatch.setenv("JARVIS_ENV", "test")
    monkeypatch.setenv("JARVIS_SESSION_SECRET", "outreach-tests-not-a-production-secret")
    monkeypatch.delenv("JARVIS_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("JARVIS_ALLOW_LIVE_CONTACT", raising=False)
    database = Database("sqlite:///" + str(tmp_path / "outreach.db"))
    with database.transaction() as tx:
        policy.initialize(tx)
    yield database
    database.close()


def prepare(tx):
    settings = policy.get_settings(tx)
    settings.update(approved_senders=["sales@example.com"], public_origin="https://jarvis.example.com")
    tx.put("settings", "global", settings)
    tx.put("connector_config", "email", {"sender": "sales@example.com", "approved": True,
        "spf": True, "dkim": True, "dmarc": True, "legal_review": True, "review_reference": REFERENCE})
    for key in ("BREVO_API_KEY", "EMAIL_DELIVERY_WEBHOOK_SECRET", "EMAIL_WEBHOOK_SECRET"):
        policy.set_secret(tx, key, GATEWAY_SECRET if key == "EMAIL_DELIVERY_WEBHOOK_SECRET" else "synthetic-key", ADMIN)
    return outreach.configure(tx, {"provider": "brevo", "sender": "sales@example.com", "enabled": True,
        **dict.fromkeys(outreach.REFERENCE_FIELDS, REFERENCE)}, ADMIN)


def review(tx):
    check = outreach.run_checks(tx, ADMIN)
    assert check["passed"] is True, check
    return outreach.record_review(tx, {"statement": outreach.APPROVE, "test_id": check["id"],
        "legal_review_reference": REFERENCE, "reason": "Synthetic administrator test authorization"}, ADMIN)


def real_customer(tx):
    return tx.create("lead", {"company": "Synthetic Customer AB", "email": "contact@example.org", "domain": "example.org",
        "state": "CONTACTED", "is_test": False, "record_mode": "REAL", "provenance": {"approved": True, "source_id": "approved-directory"},
        "entity_type": "legal_person", "address_type": "generic",
        "created_at": tx.now})


def message(tx):
    lead = real_customer(tx)
    return tx.create("email_message", {"lead_id": lead["id"], "provider": "brevo", "provider_id": "<synthetic-provider-id>",
        "recipient": lead["email"], "status": "SENT", "purpose": "contact", "created_at": tx.now}), lead


def receipt(tx, msg, kind, identity, *, occurred=None, **changes):
    payload = {"event_id": identity, "provider": "brevo", "message_id": msg["id"], "provider_id": msg["provider_id"],
        "recipient": msg["recipient"], "type": kind, "occurred_at": tx.now if occurred is None else occurred, **changes}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    stamp = int(tx.now)
    return raw, connectors.sign_event(raw, GATEWAY_SECRET, stamp), stamp


def test_upgrade_defaults_never_enable_customer_contact(db):
    with db.transaction() as tx:
        result = outreach.status(tx)
        assert result["runtime_contact_enabled"] is False
        assert result["live_contact_authorized"] is False
        assert result["native_brevo_webhook_signature"] is False
        assert result["verification_kind"] == "operator_evidence_review"
        assert tx.list("outreach_config") == []
        with pytest.raises(PolicyError, match="Separate runtime"):
            outreach.guard_contact(tx, {"is_test": False})


def test_server_owned_checks_run_actual_offline_journey_without_providers(db, monkeypatch):
    monkeypatch.setattr(httpx.Client, "post", lambda *a, **kw: pytest.fail("Offline readiness attempted HTTP"))
    with db.transaction() as tx:
        prepare(tx)
        check = outreach.run_checks(tx, ADMIN)
        assert check["passed"] is True, check
        assert check["checks"]["offline_journey_and_manual_payment_stop"] is True
        assert check["live_deliverability_verified"] is False
        assert not tx.list("lead") and not tx.list("payment") and not tx.list("external_intent")
        result = review(tx)
        assert result["approval_current"] is True
        assert result["live_contact_authorized"] is False


def test_caller_cannot_forge_tests_or_skip_separate_approval(db):
    with db.transaction() as tx:
        prepare(tx)
        with pytest.raises(PolicyError, match="client test results"):
            outreach.record_review(tx, {"statement": outreach.APPROVE, "passed": True, "reason": REFERENCE}, ADMIN)
        with pytest.raises(PolicyError, match="Fresh server-run"):
            outreach.record_review(tx, {"statement": outreach.APPROVE, "test_id": "fabricated",
                "reason": REFERENCE, "legal_review_reference": REFERENCE}, ADMIN)
        with pytest.raises(PolicyError, match="Administrator"):
            outreach.run_checks(tx, OPERATOR)
        with pytest.raises(PolicyError, match="Administrator"):
            outreach.configure(tx, {"sender": "sales@example.com"}, OPERATOR)
        with pytest.raises(PolicyError, match="Only the reviewed Brevo"):
            outreach.configure(tx, {"sender": "sales@example.com", "provider": "arbitrary-http"}, ADMIN)


def test_canonical_template_approval_is_allowlisted_and_invalidates_old_review(db):
    with db.transaction() as tx:
        prepare(tx)
        review(tx)
        updated = outreach.configure(tx, {"approved_template_ids": ["contact-standard-v1", "followup-standard-v1"]}, ADMIN)
        assert updated["config"]["approved_template_ids"] == ["contact-standard-v1", "followup-standard-v1"]
        assert updated["approval_current"] is False
        for templates in (["arbitrary-model-output"], ["contact-standard-v1", "contact-standard-v1"], "contact-standard-v1"):
            with pytest.raises(PolicyError, match="reviewed canonical"):
                outreach.configure(tx, {"approved_template_ids": templates}, ADMIN)


def test_unchanged_approval_persists_but_new_review_requires_fresh_versioned_tests(db, monkeypatch):
    with db.transaction() as tx:
        prepare(tx)
        review(tx)
        approval = tx.get("outreach_approval", "global")
        check = tx.get("outreach_test", approval["test_id"])
        assert check["probe_version"] == outreach.CONTACT_POLICY_VERSION == "1.1.0"
        tx.now += 2 * 86400
        assert outreach.status(tx)["approval_current"] is True
        with pytest.raises(PolicyError, match="Fresh server-run"):
            outreach.record_review(tx, {"statement": outreach.APPROVE, "test_id": check["id"],
                "legal_review_reference": REFERENCE, "reason": REFERENCE}, ADMIN)
        monkeypatch.setattr(outreach, "CONTACT_POLICY_VERSION", "future-policy-requiring-new-tests")
        assert outreach.status(tx)["approval_current"] is False


@pytest.mark.parametrize("name", ["BREVO_API_KEY", "EMAIL_WEBHOOK_SECRET", "EMAIL_DELIVERY_WEBHOOK_SECRET"])
def test_server_credential_rotation_invalidates_contact_approval_without_exposing_keys(db, monkeypatch, name):
    with db.transaction() as tx:
        prepare(tx)
        tx.delete("secret", name)
        first = "synthetic-first-server-provider-key"
        second = "synthetic-replacement-server-provider-key"
        monkeypatch.setenv(name, first)
        review(tx)
        assert outreach.status(tx)["approval_current"] is True
        monkeypatch.setenv(name, second)
        result = outreach.status(tx)
        assert result["approval_current"] is False
        assert first not in json.dumps(result) and second not in json.dumps(result)
        assert first not in json.dumps(tx.audits()) and second not in json.dumps(tx.audits())


def test_real_contact_needs_runtime_mode_and_strict_provenance(db, monkeypatch):
    with db.transaction() as tx:
        prepare(tx)
        settings = policy.get_settings(tx)
        settings.update(mode="LIVE", live_approved=True, legal_reviewed=True)
        tx.put("settings", "global", settings)
        review(tx)
        with pytest.raises(PolicyError, match="Separate runtime"):
            outreach.guard_contact(tx, real_customer(tx))
        monkeypatch.setenv("JARVIS_ALLOW_LIVE_CONTACT", "true")
        assert outreach.status(tx)["live_contact_authorized"] is False
        monkeypatch.setenv("JARVIS_ALLOW_LIVE", "true")
        assert outreach.status(tx)["live_contact_authorized"] is True
        with pytest.raises(PolicyError, match="Demo or unclassified"):
            outreach.guard_contact(tx, {"is_test": False})
        with pytest.raises(PolicyError, match="Demo or unclassified"):
            outreach.guard_contact(tx, {"is_test": True, "record_mode": "DEMO"})
        assert outreach.guard_contact(tx, real_customer(tx))["live_contact_authorized"] is True


@pytest.mark.parametrize("mutation", ["epoch", "sender", "origin", "revoke", "key_missing"])
def test_approval_invalidates_after_authority_or_sender_changes(db, mutation):
    with db.transaction() as tx:
        prepare(tx)
        review(tx)
        if mutation == "epoch":
            settings = policy.get_settings(tx)
            settings["safety_epoch"] += 1
            tx.put("settings", "global", settings)
        elif mutation == "sender":
            config = tx.get("connector_config", "email")
            config["sender"] = "changed@example.com"
            tx.put("connector_config", "email", config)
        elif mutation == "origin":
            settings = policy.get_settings(tx)
            settings["public_origin"] = "https://changed.example.com"
            tx.put("settings", "global", settings)
        elif mutation == "key_missing":
            tx.delete("secret", "EMAIL_DELIVERY_WEBHOOK_SECRET")
        else:
            outreach.record_review(tx, {"statement": outreach.REVOKE, "reason": REFERENCE}, ADMIN)
        assert outreach.status(tx)["live_contact_authorized"] is False
        if mutation != "key_missing":
            assert outreach.status(tx)["approval_current"] is False


def test_signed_delivery_is_bound_and_idempotent(db):
    with db.transaction() as tx:
        policy.set_secret(tx, "EMAIL_DELIVERY_WEBHOOK_SECRET", GATEWAY_SECRET, ADMIN)
        msg, _ = message(tx)
        args = receipt(tx, msg, "delivered", "delivery-one")
        result = outreach.receive_delivery(tx, *args, event_id="delivery-one")
        assert result["signature_verified"] is True and result["applied"] is True
        assert tx.get("email_message", msg["id"])["status"] == "DELIVERED"
        assert outreach.receive_delivery(tx, *args)["id"] == result["id"]
        assert len(tx.list("email_delivery_event")) == 1
        with pytest.raises(PolicyError, match="mismatched"):
            outreach.receive_delivery(tx, *args, event_id="changed-header")
        bad = receipt(tx, msg, "delivered", "delivery-other", provider_id="other-provider-id")
        with pytest.raises(PolicyError, match="does not match"):
            outreach.receive_delivery(tx, *bad)
        changed = receipt(tx, msg, "soft_bounce", "delivery-one")
        with pytest.raises(PolicyError, match="different input"):
            outreach.receive_delivery(tx, *changed)


@pytest.mark.parametrize("kind", ["hard_bounce", "complaint"])
def test_permanent_receipts_suppress_even_under_kill_and_cannot_downgrade(db, kind):
    with db.transaction() as tx:
        policy.set_secret(tx, "EMAIL_DELIVERY_WEBHOOK_SECRET", GATEWAY_SECRET, ADMIN)
        msg, lead = message(tx)
        policy.set_kill_switch(tx, True, "Stop customer operations during a safety test", ADMIN)
        outcome = outreach.receive_delivery(tx, *receipt(tx, msg, kind, "permanent-one"))
        assert outcome["received_while_stopped"] is True
        assert tx.get("lead", lead["id"])["state"] == "UNSUBSCRIBED"
        late = outreach.receive_delivery(tx, *receipt(tx, msg, "delivered", "stale-delivery", occurred=tx.now + 1))
        assert late["applied"] is False
        assert tx.get("email_message", msg["id"])["status"] == outreach.DELIVERY_STATES[kind]
        assert len(tx.list("suppression")) == 1
        assert tx.list("payment") == []


def test_gateway_requires_valid_fresh_hmac_and_supported_events(db):
    with db.transaction() as tx:
        policy.set_secret(tx, "EMAIL_DELIVERY_WEBHOOK_SECRET", GATEWAY_SECRET, ADMIN)
        msg, _ = message(tx)
        raw, signature, stamp = receipt(tx, msg, "delivered", "signed-one")
        with pytest.raises(PolicyError, match="Invalid, expired"):
            outreach.receive_delivery(tx, raw, "forged", stamp)
        with pytest.raises(PolicyError, match="Invalid, expired"):
            outreach.receive_delivery(tx, raw, signature, stamp - 301)
        with pytest.raises(PolicyError, match="Unsupported provider"):
            outreach.receive_delivery(tx, *receipt(tx, msg, "unknown", "unsupported"))
        assert not tx.list("email_delivery_event")


def test_signed_unsubscribe_preview_is_nonmutating_and_post_is_private_and_idempotent(db):
    with db.transaction() as tx:
        prepare(tx)
        lead = real_customer(tx)
        headers = outreach.unsubscribe_headers(tx, lead)
        assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
        link = headers["List-Unsubscribe"].strip("<>")
        assert lead["email"] not in link
        token = parse_qs(urlparse(link).query)["token"][0]
        assert outreach.process_unsubscribe(tx, token, confirm=False)["confirmation_required"] is True
        assert not tx.list("suppression")
        policy.set_kill_switch(tx, True, "Stop external actions; retain recipient opt-outs", ADMIN)
        assert outreach.process_unsubscribe(tx, token) == {"unsubscribed": True}
        assert outreach.process_unsubscribe(tx, token) == {"unsubscribed": True}
        assert len(tx.list("suppression")) == 1
        with pytest.raises(PolicyError, match="Invalid unsubscribe"):
            outreach.process_unsubscribe(tx, token + "tampered")
        assert GATEWAY_SECRET not in json.dumps(tx.audits())


def test_unsubscribe_token_keeps_original_recipient_when_lead_identity_changes(db):
    with db.transaction() as tx:
        prepare(tx)
        lead = real_customer(tx)
        link = outreach.unsubscribe_headers(tx, lead)["List-Unsubscribe"].strip("<>")
        token = parse_qs(urlparse(link).query)["token"][0]
        original_recipient = lead["email"]
        lead["email"] = "replacement@example.net"
        tx.put("lead", lead["id"], lead)
        assert outreach.process_unsubscribe(tx, token) == {"unsubscribed": True}
        assert tx.list("suppression")[0]["email"] == original_recipient
        assert tx.get("lead", lead["id"])["state"] == "CONTACTED"


def test_real_adapter_crash_keeps_unsubscribe_secret_and_recipient_grant(db, monkeypatch):
    monkeypatch.setenv("JARVIS_ALLOW_LIVE", "true")
    monkeypatch.setenv("JARVIS_ALLOW_LIVE_CONTACT", "true")
    with db.transaction() as tx:
        prepare(tx)
        settings = policy.get_settings(tx)
        settings.update(mode="LIVE", live_approved=True, legal_reviewed=True)
        tx.put("settings", "global", settings)
        lead = real_customer(tx)
        lead.update(state="READY_TO_CONTACT", compliance={"approved": True, "lawful_basis": REFERENCE,
            "legal_review": REFERENCE, "channel": "email", "approval_mode": "LIVE", "simulation": False})
        tx.put("lead", lead["id"], lead)
        config = tx.get("connector_config", "email")
        config["approved_templates"] = [hashlib.sha256(b"Reviewed\nReviewed body").hexdigest()]
        tx.put("connector_config", "email", config)
        review(tx)
    sent_headers = []
    class ProcessCrash(BaseException):
        pass
    def crash(request):
        sent_headers.append(json.loads(request.content)["headers"])
        raise ProcessCrash()
    adapter = connectors.BrevoAdapter("synthetic-key", transport=httpx.MockTransport(crash))
    with pytest.raises(ProcessCrash):
        with db.transaction() as tx:
            connectors.send_email(tx, lead_id=lead["id"], subject="Reviewed", body="Reviewed body",
                idempotency_key="outreach-durable-link", actor=OPERATOR, adapter=adapter)
    assert len(sent_headers) == 1
    with db.transaction() as tx:
        assert policy.get_secret(tx, "EMAIL_UNSUBSCRIBE_SECRET")
        assert len(tx.list("unsubscribe_grant")) == 1
        token = parse_qs(urlparse(sent_headers[0]["List-Unsubscribe"].strip("<>")).query)["token"][0]
        assert outreach.process_unsubscribe(tx, token, confirm=False)["confirmation_required"] is True
        assert tx.list("external_intent")[0]["status"] == "ATTEMPTED"


def reviewable_customer(tx, monkeypatch):
    prepare(tx)
    source_id = "reviewed-directory"
    research.configure_source(tx, source_id, {"name": "Synthetic licensed directory", "kind": "upload",
        "url": "https://directory.example.com/companies", "approved": True, "license_reviewed": True,
        "legal_review_reference": REFERENCE, "cost_evidence": "Synthetic free-source contract reference",
        "data_mode": "REAL"}, ADMIN)
    imported = research.ingest(tx, source_id, [{"company": "Synthetic Enquiries AB", "email": "enquiries@example.org",
        "source_url": "https://directory.example.com/companies/enquiries",
        "evidence": "This synthetic company needs support classifying incoming customer enquiries.",
        "entity_type": "legal_person", "address_type": "generic"}], ADMIN, "reviewed-directory-import")
    lead = tx.get("lead", imported["lead_ids"][0])
    workflow.research_lead(tx, lead["id"], {"summary": lead["need"]}, actor=OPERATOR, key="review-research")
    lead = workflow.qualify_lead(tx, lead["id"], actor=OPERATOR, key="review-qualify")
    outreach.configure(tx, {"auto_review_source_ids": [source_id],
        "reviewed_lawful_basis": "Explicit synthetic legal review covers this licensed source and generic corporate email channel"}, ADMIN)
    settings = policy.get_settings(tx)
    settings.update(mode="LIVE", live_approved=True, legal_reviewed=True)
    tx.put("settings", "global", settings)
    monkeypatch.setenv("JARVIS_ALLOW_LIVE", "true")
    monkeypatch.setenv("JARVIS_ALLOW_LIVE_CONTACT", "true")
    review(tx)
    return lead, source_id


def test_source_policy_delegation_is_disabled_by_default(db):
    with db.transaction() as tx:
        assert outreach.policy_review(tx, real_customer(tx)) is None
        prepare(tx)
        assert outreach.policy_review(tx, real_customer(tx)) is None
        assert outreach.status(tx)["config"]["auto_review_source_ids"] == []
        assert outreach.status(tx)["config"]["reviewed_lawful_basis"] == ""
        with pytest.raises(PolicyError, match="reviewed lawful basis"):
            outreach.configure(tx, {"auto_review_source_ids": ["directory"]}, ADMIN)
        for sources in ("directory", ["bad/url"], ["directory", "directory"]):
            with pytest.raises(PolicyError, match="reviewed source IDs"):
                outreach.configure(tx, {"auto_review_source_ids": sources}, ADMIN)


def test_delegated_review_requires_exact_current_policy_and_is_rechecked_before_contact(db, monkeypatch):
    with db.transaction() as tx:
        lead, source_id = reviewable_customer(tx, monkeypatch)
        approved = outreach.policy_review(tx, lead)
        assert approved["approved"] is True and approved["simulation"] is False
        assert approved["lawful_basis"] == outreach.status(tx)["config"]["reviewed_lawful_basis"]
        assert approved["policy_authorization"]["source_id"] == source_id
        assert approved["policy_authorization"]["source_version"] == lead["provenance"]["source_version"]
        with pytest.raises(PolicyError, match="Administrator"):
            workflow.review_compliance(tx, lead["id"], {**approved, "lawful_basis": "A caller-forged legal basis"},
                actor=OPERATOR, key="forged-delegated-review")
        reviewed = workflow.review_compliance(tx, lead["id"], approved, actor=OPERATOR, key="valid-delegated-review")
        assert reviewed["state"] == "COMPLIANCE_CHECK"
        assert reviewed["compliance"]["policy_authorization"] == approved["policy_authorization"]
        assert outreach.guard_contact(tx, reviewed)["live_contact_authorized"] is True
        source = tx.get("research_source", source_id)
        source["version"] += 1
        tx.put("research_source", source_id, source)
        with pytest.raises(PolicyError, match="delegated contact review"):
            outreach.guard_contact(tx, reviewed)
        assert not tx.list("email_message") and not tx.list("external_intent")


@pytest.mark.parametrize("mutation", ["runtime", "mode", "demo", "personal", "sole_trader", "quality", "citation", "citation_hostname",
    "source_revoked", "source_unlicensed", "source_version", "stale_provenance", "source_mode", "source_policy", "suppressed"])
def test_policy_delegation_fails_closed_when_contact_or_source_evidence_changes(db, monkeypatch, mutation):
    with db.transaction() as tx:
        lead, source_id = reviewable_customer(tx, monkeypatch)
        assert outreach.policy_review(tx, lead) is not None
        if mutation == "runtime":
            monkeypatch.setenv("JARVIS_ALLOW_LIVE_CONTACT", "false")
        elif mutation == "mode":
            settings = policy.get_settings(tx)
            settings["mode"] = "DEMO"
            tx.put("settings", "global", settings)
        elif mutation == "demo":
            lead.update(is_test=True, record_mode="DEMO")
        elif mutation == "personal":
            lead["address_type"] = "personal"
        elif mutation == "sole_trader":
            lead["entity_type"] = "sole_trader"
        elif mutation == "quality":
            lead["quality"]["score"] = 79
        elif mutation == "citation":
            lead["citations"] = []
        elif mutation == "citation_hostname":
            lead["citations"][0]["source_url"] = "https://unreviewed.example.net/company"
        elif mutation == "stale_provenance":
            lead["provenance"]["source_version"] = 0
        elif mutation == "source_policy":
            settings = policy.get_settings(tx)
            settings["approved_sources"] = []
            tx.put("settings", "global", settings)
        elif mutation == "suppressed":
            connectors.unsubscribe(tx, email=lead["email"], reason="Synthetic recipient suppression", actor=ADMIN)
        else:
            source = tx.get("research_source", source_id)
            if mutation == "source_revoked":
                source["approved"] = False
            elif mutation == "source_unlicensed":
                source["license_reviewed"] = False
            elif mutation == "source_mode":
                source["data_mode"] = "DEMO"
            else:
                source["version"] += 1
            tx.put("research_source", source_id, source)
        assert outreach.policy_review(tx, lead) is None
