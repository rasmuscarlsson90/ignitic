"""Internal deliverables use synthetic provider replies; no network or customers."""
from copy import deepcopy
import json

import httpx
import pytest

from jarvis_lite import ai, budget, factory, policy, research
from jarvis_lite.policy import PolicyError
from jarvis_lite.store import Database


ADMIN = {"id": "factory-admin", "role": "admin"}
OPERATOR = {"id": "factory-operator", "role": "operator"}
VIEWER = {"id": "factory-viewer", "role": "viewer"}
BRIEF = "Prepare a practical incoming-enquiry assistant service plan for a small B2B support team."
HTTP_STREAM = httpx.Client.stream


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_ENV", "test")
    monkeypatch.setenv("JARVIS_SESSION_SECRET", "synthetic-factory-only-test-session")
    monkeypatch.delenv("JARVIS_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("JARVIS_ALLOW_PAID_AI", "false")
    monkeypatch.setattr(httpx.Client, "stream", lambda *a, **kw: pytest.fail("Factory test attempted network"))
    database = Database("sqlite:///" + str(tmp_path / "factory.db"))
    with database.transaction() as tx:
        policy.initialize(tx)
    yield database
    database.close()


def attributed_lead(tx, *, mode="REAL"):
    source = research.configure_source(tx, "factory-directory", {
        "name": "Synthetic approved source", "kind": "upload",
        "url": "https://directory.example.com/companies", "approved": True,
        "license_reviewed": True, "legal_review_reference": "Synthetic license review for isolated tests only",
        "cost_evidence": "Synthetic free source evidence", "data_mode": mode,
    }, ADMIN)
    imported = research.ingest(tx, source["id"], [{
        "company": "Synthetic Factory Prospect AB", "email": "info@factory-prospect.example.com",
        "evidence": "The company receives customer enquiries and asks for faster draft response handling.",
        "entity_type": "legal_person", "address_type": "generic",
    }], OPERATOR, "factory-research")
    return imported["lead_ids"][0], source["id"]


def order(tx, kind="market_analysis", *, evidence=True, key="create", brief=BRIEF):
    payload = {"kind": kind, "brief": brief}
    if evidence:
        lead_id, source_id = attributed_lead(tx)
        payload.update(lead_ids=[lead_id], source_ids=[source_id])
    return factory.create(tx, payload, OPERATOR, key)


def deliverable(kind, source="S1", **changes):
    content = {
        "title": "Scoped customer enquiry assistant plan",
        "sections": [{"key": key, "heading": key.replace("_", " ").title(),
            "content": "Recommendation: map recurring enquiries, draft two reviewed responses and measure response time before rollout.",
            "source_ids": [source]} for key in factory.TYPES[kind]["sections"]],
        "next_actions": ["Ask the operator to confirm the enquiry categories and evaluate a private draft against five anonymised cases."],
        "limitations": ["Recommendations use the supplied brief and evidence; no independent website crawl or revenue validation occurred."],
        "company_ids": [],
    }
    return {**content, **changes}


def paid_mock(monkeypatch, *, mutate=None, outcome=None):
    calls = []
    monkeypatch.setattr(ai, "status", lambda tx: {
        "effective_paid_enabled": True, "config": {"revision": "synthetic-v12", "max_input_chars": 12000}})

    def task(tx, **kwargs):
        calls.append(kwargs)
        if outcome is not None:
            return deepcopy(outcome)
        prompt = json.loads(kwargs["text"])
        generated = deliverable(prompt["type"], "S1" if prompt["source_evidence"] else "BRIEF")
        if mutate:
            generated = mutate(generated)
        return {"id": "synthetic-ai-result-" + str(len(calls)), "status": "COMPLETED", "provider": "openrouter",
                "simulated": False, "data_mode": "REAL", "trace_id": "synthetic-trace-" + str(len(calls)),
                "model": "synthetic/test-model", "input_tokens": 120, "output_tokens": 180,
                "cost_sek_minor": 6, "output": {"action": "draft", "text": json.dumps(generated)}}

    monkeypatch.setattr(ai, "task", task)
    return calls


@pytest.mark.parametrize("kind", list(factory.TYPES))
def test_all_five_types_require_real_ai_and_persist_attributed_version(db, monkeypatch, kind):
    calls = paid_mock(monkeypatch)
    with db.transaction() as tx:
        created = order(tx, kind)
        assert tx.list("service_result") == [] and calls == []
        result = factory.generate(tx, created["id"], OPERATOR, "generate")
        assert result["kind"] == kind and result["version"] == 1 and result["status"] == "NEEDS_REVIEW"
        assert result["simulated"] is False and result["responsible_agent"] == "delivery"
        assert result["quality"]["external_facts_independently_verified"] is False
        assert result["source_snapshot"] == created["evidence"]
        assert result["trace_id"] == "synthetic-trace-1" and result["cost_sek_minor"] == 6
        assert factory.generate(tx, created["id"], OPERATOR, "generate") == result
        assert len(calls) == 1 and calls[0]["role"] == "delivery"
        assert calls[0]["workflow_id"] == "factory:" + created["id"]
        assert len(factory.results(tx)) == len(factory.list_orders(tx)) == 1
        assert tx.list("email_message") == tx.list("payment") == tx.list("delivery") == []


def test_disabled_ai_never_creates_demo_deliverable_or_calls_provider(db, monkeypatch):
    monkeypatch.setattr(ai, "task", lambda *a, **kw: pytest.fail("Disabled AI was called"))
    with db.transaction() as tx:
        created = order(tx, "marketing_strategy", evidence=False)
        result = factory.generate(tx, created["id"], OPERATOR, "disabled")
        assert result["status"] == "BLOCKED" and result["code"] == "ai_activation_required"
        assert result["result_created"] is False and tx.list("service_result") == []


def test_missing_required_evidence_blocks_but_brief_only_calendar_is_labelled(db, monkeypatch):
    calls = paid_mock(monkeypatch)
    with db.transaction() as tx:
        missing = order(tx, evidence=False)
        assert factory.generate(tx, missing["id"], OPERATOR, "missing")["code"] == "approved_evidence_required"
        assert calls == []
        calendar = order(tx, "content_calendar", evidence=False, key="calendar")
        result = factory.generate(tx, calendar["id"], OPERATOR, "calendar")
        assert result["quality"]["brief_only"] is True and result["source_snapshot"] == []
        assert "BRIEF" in result["markdown"] and "inte oberoende faktakontroll" in result["markdown"]


def test_demo_data_and_unreviewed_sources_cannot_be_evidence(db):
    with db.transaction() as tx:
        lead_id, source_id = attributed_lead(tx, mode="DEMO")
        with pytest.raises(PolicyError, match="REAL leads"):
            factory.create(tx, {"kind": "market_analysis", "brief": BRIEF, "lead_ids": [lead_id]}, OPERATOR, "demo")
        with pytest.raises(PolicyError, match="REAL sources"):
            factory.create(tx, {"kind": "market_analysis", "brief": BRIEF, "source_ids": [source_id]}, OPERATOR, "demo-source")
        assert tx.list("service_order") == []


def test_company_without_current_attributed_citation_is_not_passed_as_known_fact(db, monkeypatch):
    calls = paid_mock(monkeypatch)
    with db.transaction() as tx:
        lead_id, source_id = attributed_lead(tx)
        lead = tx.get("lead", lead_id)
        lead["citations"][0]["source_version"] = 0
        tx.put("lead", lead_id, lead)
        created = factory.create(tx, {"kind": "market_analysis", "brief": BRIEF,
            "lead_ids": [lead_id], "source_ids": [source_id]}, OPERATOR, "stale-company")
        assert created["companies"] == created["evidence"] == []
        assert factory.generate(tx, created["id"], OPERATOR, "stale-company")["code"] == "approved_evidence_required"
        assert calls == []


def test_unknown_provider_outcome_cannot_be_bypassed_with_a_new_key(db, monkeypatch):
    calls = paid_mock(monkeypatch, outcome={"status": "UNKNOWN", "trace_id": "unknown-trace",
                                          "simulated": False, "manual_reconciliation_required": True})
    with db.transaction() as tx:
        created = order(tx)
        result = factory.generate(tx, created["id"], OPERATOR, "uncertain-one")
        assert result["status"] == "UNCERTAIN" and result["result_created"] is False
        assert factory.generate(tx, created["id"], OPERATOR, "uncertain-two")["status"] == "UNCERTAIN"
        assert len(calls) == 1 and tx.list("service_result") == []


def test_process_loss_after_ai_checkpoint_cannot_duplicate_provider_call(db, monkeypatch):
    paid_mock(monkeypatch)
    calls = []

    class ProcessLoss(BaseException):
        pass

    def checkpoint_then_die(tx, **kwargs):
        calls.append(kwargs)
        tx.checkpoint()
        raise ProcessLoss

    monkeypatch.setattr(ai, "task", checkpoint_then_die)
    with db.transaction() as tx:
        created = order(tx)
    with pytest.raises(ProcessLoss):
        with db.transaction() as tx:
            factory.generate(tx, created["id"], OPERATOR, "attempt-one")
    with db.transaction() as tx:
        result = factory.generate(tx, created["id"], OPERATOR, "attempt-after-restart")
        assert result["status"] == "UNCERTAIN" and result["manual_reconciliation_required"] is True
        assert len(calls) == 1 and tx.list("service_result") == []


@pytest.mark.parametrize("outcome", [
    {"provider": "demo", "simulated": True, "status": "COMPLETED"},
    {"provider": "openrouter", "simulated": True, "status": "COMPLETED"},
    {"provider": "openrouter", "simulated": False, "status": "REJECTED"},
])
def test_demo_and_rejected_ai_never_turn_into_results(db, monkeypatch, outcome):
    paid_mock(monkeypatch, outcome=outcome)
    with db.transaction() as tx:
        created = order(tx)
        result = factory.generate(tx, created["id"], OPERATOR, "not-real")
        assert result["status"] == "BLOCKED" and tx.list("service_result") == []


@pytest.mark.parametrize("mutate", [
    lambda content: {**content, "sections": []},
    lambda content: {**content, "unexpected_secret": "must not be included"},
    lambda content: {**content, "sections": [{**s, "source_ids": ["invented-source"]} for s in content["sections"]]},
])
def test_invalid_ai_schema_or_invented_citations_produces_no_deliverable(db, monkeypatch, mutate):
    paid_mock(monkeypatch, mutate=mutate)
    with db.transaction() as tx:
        created = order(tx)
        result = factory.generate(tx, created["id"], OPERATOR, "invalid")
        assert result["status"] == "QUALITY_REVIEW" and result["result_created"] is False
        assert tx.list("service_result") == []


def test_unattributed_companies_contacts_and_urls_fail_quality_and_cannot_be_approved(db, monkeypatch):
    def invented(content):
        content["company_ids"] = ["invented-company"]
        content["sections"][0]["content"] += " Try person@invented.example.com and https://invented.example.com/unknown."
        return content
    paid_mock(monkeypatch, mutate=invented)
    with db.transaction() as tx:
        created = order(tx)
        result = factory.generate(tx, created["id"], OPERATOR, "invented")
        assert result["status"] == "QUALITY_REVIEW" and result["content"]["company_ids"] == []
        assert set(result["quality"]["issues"]) == {"unknown_company_reference", "unattributed_external_url", "generated_contact_details_require_review"}
        with pytest.raises(PolicyError, match="cannot be overridden"):
            factory.review(tx, result["id"], "approve", "Reviewed the exact result version", ADMIN)


def test_versions_are_immutable_and_admin_approval_is_exact(db, monkeypatch):
    calls = paid_mock(monkeypatch)
    with db.transaction() as tx:
        created = order(tx)
        first = factory.generate(tx, created["id"], OPERATOR, "version-one")
        with pytest.raises(PolicyError, match="Administrator"):
            factory.review(tx, first["id"], "approve", "Reviewed exact version and evidence", OPERATOR)
        approved = factory.review(tx, first["id"], "approve", "Reviewed exact version and evidence", ADMIN)
        assert approved["status"] == "APPROVED" and approved["approved_by"] == ADMIN["id"]
        second = factory.generate(tx, created["id"], OPERATOR, "version-two")
        assert second["id"] != first["id"] and second["version"] == 2 and second["status"] == "NEEDS_REVIEW"
        assert tx.get("service_result", first["id"]) == approved
        assert len(calls) == 2
        with pytest.raises(PolicyError, match="latest exact result"):
            factory.review(tx, first["id"], "approve", "Reviewed exact version and evidence", ADMIN)
        with pytest.raises(PolicyError, match="immutable"):
            factory.review(tx, first["id"], "reject", "Change prior review decision after new version", ADMIN)
        rejected = factory.review(tx, second["id"], "reject", "Needs a clearer implementation plan", ADMIN)
        assert rejected["status"] == "REJECTED" and tx.get("service_result", first["id"]) == approved


def test_rejecting_older_draft_does_not_change_latest_order_state(db, monkeypatch):
    paid_mock(monkeypatch)
    with db.transaction() as tx:
        created = order(tx)
        first = factory.generate(tx, created["id"], OPERATOR, "first-draft")
        second = factory.generate(tx, created["id"], OPERATOR, "second-draft")
        factory.review(tx, first["id"], "reject", "Superseded by the revised second draft", ADMIN)
        current = tx.get("service_order", created["id"])
        assert current["latest_result_id"] == second["id"] and current["status"] == "NEEDS_REVIEW"


@pytest.mark.parametrize("change", ["revoke", "version"])
def test_source_revocation_or_version_change_blocks_generation_and_approval(db, monkeypatch, change):
    calls = paid_mock(monkeypatch)
    with db.transaction() as tx:
        created = order(tx)
        result = factory.generate(tx, created["id"], OPERATOR, "before-source-change")
        source = tx.get("research_source", "factory-directory")
        source.update(approved=False) if change == "revoke" else source.update(version=source["version"] + 1)
        tx.put("research_source", source["id"], source)
        with pytest.raises(PolicyError):
            factory.review(tx, result["id"], "approve", "Reviewed exact version and evidence", ADMIN)
        blocked = factory.generate(tx, created["id"], OPERATOR, "after-source-change")
        assert blocked["status"] == "BLOCKED" and len(calls) == 1
        assert tx.get("service_result", result["id"])["source_snapshot"] == result["source_snapshot"]


def test_export_is_bounded_inert_and_does_not_include_server_secrets(db, monkeypatch):
    def markup(content):
        content["sections"][0]["content"] = '<script>alert("unsafe")</script> ![link](javascript:run) remains untrusted model text.'
        return content
    paid_mock(monkeypatch, mutate=markup)
    with db.transaction() as tx:
        policy.set_secret(tx, "OPENROUTER_API_KEY", "synthetic-never-export-server-secret", ADMIN)
        created = order(tx)
        result = factory.generate(tx, created["id"], OPERATOR, "markup")
        markdown = factory.export(tx, result["id"], "markdown", VIEWER)
        structured = factory.export(tx, result["id"], "json", VIEWER)
        assert markdown["media_type"].startswith("text/markdown") and markdown["filename"].endswith(".md")
        assert "<script>" not in markdown["content"] and "![link](javascript:" not in markdown["content"]
        assert "Status: NEEDS_REVIEW" in markdown["content"]
        assert structured["media_type"] == "application/json" and "<script>" not in structured["content"]
        assert json.loads(structured["content"])["review_required"] is True
        assert "synthetic-never-export-server-secret" not in structured["content"] + markdown["content"]
        with pytest.raises(PolicyError, match="Only Markdown and JSON"):
            factory.export(tx, result["id"], "html", VIEWER)
        with pytest.raises(PolicyError, match="Authentication"):
            factory.export(tx, result["id"], "json", {"role": "anonymous"})


def test_mission_delivery_reuses_order_snapshot_and_mission_budget_workflow(db, monkeypatch):
    calls = paid_mock(monkeypatch)
    with db.transaction() as tx:
        lead_id, source_id = attributed_lead(tx)
        mission = {"id": "mission-factory", "goal": BRIEF, "lead_ids": [lead_id], "source_ids": [source_id],
                   "deliverable_kind": "market_analysis", "research_report_id": "research-report-id"}
        setattr(tx, "agent_context", {"mission_id": mission["id"], "role": "delivery", "task_id": "delivery-task"})
        first = factory.generate_for_mission(tx, mission, OPERATOR, "mission-task-one")
        second = factory.generate_for_mission(tx, {**mission, "goal": BRIEF + " Mission metadata changed."}, OPERATOR, "mission-task-two")
        assert first["order_id"] == second["order_id"] and second["version"] == 2
        assert len(factory.list_orders(tx)) == 1 and calls[0]["workflow_id"] == "mission:mission-factory:delivery"
        assert tx.get("service_order", first["order_id"])["research_report_id"] == "research-report-id"


def test_strict_inputs_idempotency_permissions_and_emergency_stop(db, monkeypatch):
    calls = paid_mock(monkeypatch)
    with db.transaction() as tx:
        created = order(tx, "marketing_strategy", evidence=False)
        assert factory.create(tx, {"kind": "marketing_strategy", "brief": BRIEF}, OPERATOR, "create")["id"] == created["id"]
        with pytest.raises(PolicyError, match="different input"):
            factory.create(tx, {"kind": "marketing_strategy", "brief": BRIEF + " Changed."}, OPERATOR, "create")
        for invalid in ({"kind": [], "brief": BRIEF}, {"kind": "content_calendar", "brief": "short"},
                        {"kind": "content_calendar", "brief": BRIEF, "enabled": True}):
            with pytest.raises(PolicyError):
                factory.create(tx, invalid, OPERATOR, "invalid")
        with pytest.raises(PolicyError, match="authenticated operator"):
            factory.generate(tx, created["id"], VIEWER, "viewer")
        with pytest.raises(PolicyError, match="trusted delivery-agent"):
            factory.create(tx, {"kind": "content_calendar", "brief": BRIEF, "mission_id": "untrusted-attribution"}, OPERATOR, "mission-forgery")
        policy.set_kill_switch(tx, True, "Synthetic stop before generation", ADMIN)
        with pytest.raises(PolicyError, match="Emergency stop"):
            factory.generate(tx, created["id"], OPERATOR, "stopped")
        assert calls == [] and tx.list("service_result") == []


@pytest.mark.parametrize("unknown", [False, True])
def test_actual_ai_budget_trace_and_outbox_pipeline_with_http_mock_only(db, monkeypatch, unknown):
    """Exercise production ai.task and its checkpoint/FX ledger, never a provider."""
    monkeypatch.setattr(httpx.Client, "stream", HTTP_STREAM)
    monkeypatch.setenv("JARVIS_ALLOW_PAID_AI", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-factory-integration-key")
    calls = []

    def provider(request):
        calls.append(request)
        if unknown:
            raise httpx.ReadTimeout("Synthetic unknown response; no network", request=request)
        payload = json.loads(request.content)
        prompt = json.loads(json.loads(payload["messages"][1]["content"])["untrusted_content"])
        generated = deliverable(prompt["type"])
        output = {"action": "draft", "text": json.dumps(generated), "reason": "Private source-attributed deliverable for manual review."}
        return httpx.Response(200, json={"id": "synthetic-factory-provider-result", "usage": {
            "cost": "0.007", "prompt_tokens": 400, "completion_tokens": 300},
            "choices": [{"message": {"content": json.dumps(output)}}]})

    adapter = ai.OpenRouterAdapter("synthetic-factory-integration-key", transport=httpx.MockTransport(provider))
    real_task = ai.task
    monkeypatch.setattr(ai, "task", lambda tx, **kwargs: real_task(tx, **kwargs, adapter=adapter))
    with db.transaction() as tx:
        budget.set_fx_rate(tx, currency="USD", sek_per_unit="12", source="Synthetic factory test FX evidence",
                           observed_at=tx.now, actor=ADMIN)
        ai.configure(tx, {"enabled": True, "max_output_tokens": 2048,
                          "reason": "Reviewed private deliverable format and bounded AI spend"}, ADMIN)
        created = order(tx)
        result = factory.generate(tx, created["id"], OPERATOR, "integrated-generate")
        assert len(calls) == 1 and policy.get_settings(tx)["live_approved"] is False
        entry, trace = tx.list("budget_entry")[0], tx.list("ai_trace")[0]
        assert trace["role"] == "delivery" and entry["workflow_id"] == "factory:" + created["id"]
        if unknown:
            assert result["status"] == "UNCERTAIN" and entry["state"] == "RESERVED"
            assert factory.generate(tx, created["id"], OPERATOR, "new-key")["status"] == "UNCERTAIN"
            assert len(calls) == 1 and tx.list("service_result") == []
        else:
            assert result["status"] == "NEEDS_REVIEW" and result["trace_id"] == trace["id"]
            assert entry["state"] == "SETTLED" and entry["actual_sek_minor"] == result["cost_sek_minor"] == 12
            assert result["input_tokens"] == 400 and result["output_tokens"] == 300
            assert factory.generate(tx, created["id"], OPERATOR, "integrated-generate")["id"] == result["id"]
            assert len(calls) == 1
        assert tx.list("email_message") == tx.list("payment") == []
