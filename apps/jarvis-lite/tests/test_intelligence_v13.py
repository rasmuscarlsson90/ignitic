"""General business research is real attributed input, never invented contacts."""
import json

import pytest

from jarvis_lite import ai, intelligence, policy, research
from jarvis_lite.policy import PolicyError
from jarvis_lite.store import Database


ADMIN = {"id": "business-research-admin", "role": "admin"}
OPERATOR = {"id": "business-research-operator", "role": "operator"}


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_ENV", "test")
    monkeypatch.setenv("JARVIS_SESSION_SECRET", "synthetic-business-research-secret")
    monkeypatch.delenv("JARVIS_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("JARVIS_ALLOW_RESEARCH_NETWORK", "false")
    database = Database(f"sqlite:///{tmp_path / 'business-research.db'}")
    with database.transaction() as tx:
        policy.initialize(tx)
        research.configure_source(tx, "market", {"name": "Synthetic permitted market report", "kind": "upload",
            "url": "https://market.example.com/report", "approved": True, "license_reviewed": True,
            "legal_review_reference": "Synthetic permission reviewed for test-only source",
            "cost_evidence": "Synthetic zero-charge upload contract"}, ADMIN)
        research.ingest_documents(tx, "market", [{"title": "Synthetic source material",
            "content": "The synthetic survey covers 20 firms. Estimated sample servicing cost is 2000 SEK.",
            "facts": [{"subject": "Synthetic survey", "predicate": "firms", "value": "20", "kind": "fact",
                "evidence": "survey covers 20 firms"}, {"subject": "Synthetic survey", "predicate": "servicing cost",
                "value": "2000", "unit": "SEK", "kind": "estimate", "evidence": "servicing cost is 2000 SEK"}]}],
            OPERATOR, "permitted-material")
    yield database
    database.close()


def opportunity(**changes):
    return {"title": "Internal enquiry automation hypothesis", "target_group": "Businesses in supplied source material",
        "problem": "Potential enquiry workload, requiring customer confirmation",
        "solution": "Prepare a reviewed reply-drafting assistant internally", "price_estimate_minor": 500000,
        "cost_estimate_minor": 200000, "competition": "The supplied source does not establish competitor market shares",
        "risks": ["The actual demand and complete delivery costs remain unverified"], "source_ids": ["market"],
        "next_steps": ["Review cited evidence and delivery assumptions internally"],
        "assumptions": ["Price and cost are assumptions, not a contracted sale"], **changes}


class Model:
    def __init__(self):
        self.calls = []
        self.contracts = []
        self.content = None
        self.status = "COMPLETED"
        self.simulated = False

    def task(self, tx, **kwargs):
        self.calls.append(kwargs)
        self.contracts.append(tx.ai_response_contract)
        content = self.content
        if content is None:
            content = ({"summary": "One bounded internal opportunity based on supplied evidence.",
                "opportunities": [opportunity()]} if kwargs["role"] == "sales" else {
                "summary": "The supplied synthetic document describes a small sample and an uncertain cost estimate.",
                "candidates": [], "findings": [{"claim": "The source covers a synthetic sample of firms.",
                    "source_ids": ["market"], "kind": "fact"}],
                "uncertainties": ["The sample cannot establish national market demand or verified delivery costs."]})
        return tx.create("agent_result", {"provider": "demo" if self.simulated else "openrouter", "simulated": self.simulated,
            "status": self.status, "role": kwargs["role"], "trace_id": "synthetic-general-research-trace",
            "output": {"text": json.dumps(content)}, "manual_reconciliation_required": self.status == "UNKNOWN"})


@pytest.fixture
def model(monkeypatch):
    fake = Model()
    monkeypatch.setattr(ai, "status", lambda tx: {"effective_paid_enabled": True})
    monkeypatch.setattr(ai, "task", fake.task)
    return fake


def mission(**changes):
    return {"id": "general-business-mission", "goal": "Analyze the supplied Swedish business material and prepare opportunities",
        "source_ids": ["market"], "workflow_version": "1.3", **changes}


def completed_research(tx, model):
    created = mission()
    report = intelligence.run_research(tx, created, OPERATOR, "general-research")
    created["research_report_id"] = report["id"]
    return created, report


def test_general_document_goal_completes_without_creating_a_fake_company(db, model):
    with db.transaction() as tx:
        created, report = completed_research(tx, model)
        assert report["status"] == "COMPLETED" and report["lead_ids"] == []
        assert report["document_ids"] and len(report["citations"]) == 1
        assert report["facts"][0]["value"] == "20" and report["estimates"][0]["value"] == "2000"
        assert report["analysis"]["model_claims_unverified"] is True
        assert report["findings"][0]["requires_fact_review"] is True
        assert report["uncertainties"] and report["coverage"]["analyzed_documents"] == 1
        assert intelligence.run_research(tx, mission(), OPERATOR, "general-research") == report
        assert len(model.calls) == 1 and model.contracts == ["research_report"]
        assert tx.ai_response_contract is None
        assert tx.list("lead") == tx.list("email_message") == tx.list("payment") == []
        assert created["research_report_id"] == report["id"]


def test_general_strategy_records_bounded_estimates_not_offers_or_revenue(db, model):
    with db.transaction() as tx:
        created, report = completed_research(tx, model)
        strategy = intelligence.prepare_strategy(tx, created, OPERATOR, "general-strategy")
        assert strategy["status"] == "COMPLETED" and len(strategy["opportunities"]) == 1
        assert strategy["research_report_id"] == report["id"] and strategy["requires_human_review"]
        item = strategy["opportunities"][0]
        assert item["assessment_is_hypothesis"] is item["prices_are_estimates"] is True
        assert item["commercial_service_approved"] is item["grants_contact_authority"] is False
        assert item["verified_revenue_minor"] == strategy["verified_revenue_minor"] == 0
        assert item["preliminary_difference_minor"] == 300000
        assert intelligence.prepare_strategy(tx, created, OPERATOR, "general-strategy") == strategy
        assert model.contracts == ["research_report", "sales_strategy"]
        assert len(tx.list("opportunity_report")) == 1
        assert tx.list("lead") == tx.list("sales_offer") == tx.list("proposal") == tx.list("payment") == []


@pytest.mark.parametrize("content", [
    {"summary": "Invented candidate", "candidates": [{"lead_id": "invented-company", "score": 100}]},
    {"summary": "Unknown source", "candidates": [], "findings": [{"claim": "An unsupported finding", "source_ids": ["unapproved"], "kind": "fact"}]},
    {"summary": "Unsupported truth", "candidates": [], "findings": [{"claim": "A claimed verified truth", "source_ids": ["market"], "kind": "verified"}]},
    {"summary": 25, "candidates": []}, {"summary": "Unknown uncertainty type", "candidates": [], "uncertainties": [None]},
])
def test_document_research_does_not_accept_invented_identity_or_source(db, model, content):
    model.content = content
    with db.transaction() as tx:
        report = intelligence.run_research(tx, mission(), OPERATOR, "untrusted-model")
        assert report["status"] == "REVIEW_REQUIRED"
        assert tx.list("lead") == []
        assert report["grants_contact_authority"] is False


@pytest.mark.parametrize("changes", [{"price_estimate_minor": 1}, {"cost_estimate_minor": -1},
    {"source_ids": ["unapproved"]}, {"risks": []}, {"assumptions": []},
    {"price_estimate_minor": True}, {"contact_permission": True}])
def test_strategy_cannot_relax_catalogue_price_or_source_authority(db, model, changes):
    with db.transaction() as tx:
        created, _ = completed_research(tx, model)
        model.content = {"summary": "Untrusted opportunity proposal", "opportunities": [opportunity(**changes)]}
        result = intelligence.prepare_strategy(tx, created, OPERATOR, "invalid-strategy")
        assert result["status"] == "REVIEW_REQUIRED" and result["opportunities"] == []
        assert result["verified_revenue_minor"] == 0
        assert tx.list("proposal") == tx.list("payment") == tx.list("email_message") == []


def test_model_uncertainty_is_persisted_and_same_request_never_repeats(db, model):
    model.status = "UNKNOWN"
    with db.transaction() as tx:
        result = intelligence.run_research(tx, mission(), OPERATOR, "unknown-research")
        assert result["status"] == "UNCERTAIN"
        assert intelligence.run_research(tx, mission(), OPERATOR, "unknown-research") == result
        assert len(model.calls) == 1
        with pytest.raises(PolicyError, match="researchsteg"):
            intelligence.prepare_strategy(tx, {**mission(), "research_report_id": result["id"]}, OPERATOR, "no-unknown-plan")


def test_document_research_disabled_ai_and_revoked_material_never_invoke_model(db, model, monkeypatch):
    monkeypatch.setattr(ai, "status", lambda tx: {"effective_paid_enabled": False})
    with db.transaction() as tx:
        with pytest.raises(PolicyError, match="Real internal work"):
            intelligence.run_research(tx, mission(), OPERATOR, "ai-off")
        source = tx.get("research_source", "market")
        source["approved"] = False
        tx.put("research_source", "market", source)
        with pytest.raises(PolicyError, match="revoked"):
            intelligence.run_research(tx, mission(), OPERATOR, "source-off")
        assert model.calls == []


def test_strategy_requires_same_mission_and_current_research_citations(db, model):
    with db.transaction() as tx:
        created, report = completed_research(tx, model)
        with pytest.raises(PolicyError, match="researchsteg"):
            intelligence.prepare_strategy(tx, {**created, "id": "another-mission"}, OPERATOR, "different-mission")
        source = tx.get("research_source", "market")
        tx.put("research_source", "market", {**source, "version": 2})
        with pytest.raises(PolicyError, match="aktuella"):
            intelligence.prepare_strategy(tx, created, OPERATOR, "stale-source")
        assert len(model.calls) == 1 and report["citations"]
