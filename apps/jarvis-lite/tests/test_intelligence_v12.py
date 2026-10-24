"""Attributed CRM and internal intelligence; all providers and feeds are local fakes."""
import json

import pytest

from jarvis_lite import ai, intelligence, policy, research, workflow
from jarvis_lite.policy import PolicyError
from jarvis_lite.store import Database


ADMIN = {"id": "intelligence-admin", "role": "admin"}
OPERATOR = {"id": "intelligence-operator", "role": "operator"}


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_ENV", "test")
    monkeypatch.setenv("JARVIS_SESSION_SECRET", "synthetic-intelligence-session-secret")
    monkeypatch.delenv("JARVIS_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("JARVIS_ALLOW_RESEARCH_NETWORK", raising=False)
    database = Database("sqlite:///" + str(tmp_path / "intelligence.db"))
    with database.transaction() as tx:
        policy.initialize(tx)
    yield database
    database.close()


def config(**changes):
    return {"name": "Synthetic approved company directory", "kind": "upload",
            "url": "https://directory.example.com/companies", "approved": True,
            "license_reviewed": True, "legal_review_reference": "Synthetic source review for this test",
            "cost_evidence": "Synthetic no-charge reviewed directory contract", **changes}


def company(number=1, **changes):
    return {"company": f"Synthetic Company {number} AB", "email": f"info@company-{number}.example.com",
            "evidence": "Published company material describes incoming support enquiries and reply handling.",
            "entity_type": "legal_person", "address_type": "generic", **changes}


def seed(tx, records=None, source_id="directory", **source_changes):
    research.configure_source(tx, source_id, config(**source_changes), ADMIN)
    return research.ingest(tx, source_id, records or [company()], OPERATOR, "seed:" + source_id)["lead_ids"]


class Model:
    def __init__(self):
        self.calls = []
        self.content = None
        self.raw = None
        self.status = "COMPLETED"
        self.simulated = False

    def task(self, tx, **kwargs):
        self.calls.append(kwargs)
        request = json.loads(kwargs["text"])
        content = self.content
        if content is None:
            if kwargs["role"] == "sales":
                content = {"service_id": workflow.SERVICE_ID, "package_name": "Inbound enquiry draft assistant",
                    "fit_reason": "The source evidence suggests an enquiry handling need.",
                    "email_subject": "Internal proposal for enquiry handling",
                    "email_body": "A draft based on attributed company material, pending administrator review.",
                    "follow_up_plan": ["Review evidence and permissions before any future contact"]}
            elif "candidates" in request:
                content = {"summary": "Companies were ranked using the supplied evidence.", "candidates": [
                    {"lead_id": row["lead_id"], "score": 80 + index % 10,
                     "reason": "Published material mentions incoming customer enquiries.",
                     "possible_need": "May benefit from reply drafts.", "suggested_service": workflow.SERVICE_ID,
                     "next_action": "Review source evidence internally"}
                    for index, row in enumerate(request["candidates"])]}
            else:
                content = {"score": 88, "reason": "Source material describes an enquiry workload.",
                    "potential_needs": ["Possibly faster draft replies"], "suggested_service": workflow.SERVICE_ID,
                    "next_action": "Prepare an internal offer"}
        return tx.create("ai_result", {"role": kwargs["role"], "status": self.status,
            "provider": "demo" if self.simulated else "openrouter", "simulated": self.simulated,
            "trace_id": "synthetic-attributed-trace", "data_mode": "DEMO" if self.simulated else "REAL",
            "output": {"text": self.raw if self.raw is not None else json.dumps(content)},
            "manual_reconciliation_required": self.status == "UNKNOWN"})


@pytest.fixture
def model(monkeypatch):
    fake = Model()
    monkeypatch.setattr(ai, "status", lambda tx: {"effective_paid_enabled": True})
    monkeypatch.setattr(ai, "task", fake.task)
    return fake


def mission(**changes):
    return {"id": "mission-synthetic", "goal": "Find businesses with an attributed inbound enquiry workload",
            "source_ids": ["directory"], **changes}


def test_crm_metadata_is_imported_with_evidence_and_never_invented(db):
    with db.transaction() as tx:
        ids = seed(tx, [company(1, website="https://company-1.example.com", industry="Retail",
            geography="Sweden", description="Company description provided by the reviewed source."), company(2)])
        rows = intelligence.list_leads(tx, {"sort": "company", "order": "asc"})["items"]
        assert rows[0]["website"] == "https://company-1.example.com"
        assert rows[0]["industry"] == "Retail" and rows[0]["geography"] == "Sweden"
        assert rows[0]["metadata_evidence"]["website"]["source_id"] == "directory"
        assert rows[0]["source_verified"] is True and rows[0]["official_registry_verified"] is False
        assert rows[1]["website"] == rows[1]["industry"] == rows[1]["description"] == ""
        assert rows[1]["domain"] and rows[1]["contact_authorized"] is False
        # Missing metadata on a subsequent import must not erase existing facts.
        research.ingest(tx, "directory", [company(1)], OPERATOR, "metadata-refresh")
        assert tx.get("lead", ids[0])["website"] == rows[0]["website"]


@pytest.mark.parametrize("metadata", [{"website": "http://company.example.com"}, {"website": "https://127.0.0.1"},
    {"industry": None}, {"geography": False}, {"description": "x" * 2001}])
def test_invalid_metadata_is_rejected_before_any_company_is_imported(db, metadata):
    with db.transaction() as tx:
        research.configure_source(tx, "directory", config(), ADMIN)
        with pytest.raises(PolicyError):
            research.ingest(tx, "directory", [company(**metadata)], OPERATOR, "bad-metadata")
        assert tx.list("lead") == []


def test_research_requires_current_approved_sources_and_real_ai(db, model, monkeypatch):
    with db.transaction() as tx:
        for ids in ([], ["missing"]):
            with pytest.raises(PolicyError) as exc:
                intelligence.run_research(tx, mission(source_ids=ids), OPERATOR, "no-source")
            assert exc.value.code == "NO_APPROVED_SOURCES"
        seed(tx)
        monkeypatch.setattr(ai, "status", lambda tx: {"effective_paid_enabled": False})
        monkeypatch.setattr(research, "fetch_source", lambda *a: pytest.fail("AI-unready work fetched a source"))
        with pytest.raises(PolicyError) as exc:
            intelligence.run_research(tx, mission(), OPERATOR, "ai-disabled")
        assert exc.value.code == "AI_NOT_READY"
        assert model.calls == [] and tx.list("research_report") == []


def test_research_report_uses_existing_company_ids_and_source_citations(db, model):
    with db.transaction() as tx:
        ids = seed(tx, [company(1), company(2)])
        result = intelligence.run_research(tx, mission(), OPERATOR, "research-one")
        assert result["status"] == "COMPLETED" and set(result["lead_ids"]) == set(ids)
        assert {row["company"] for row in result["ranked_candidates"]} == {company(1)["company"], company(2)["company"]}
        assert all(row["citations"] and row["assessment_is_hypothesis"] for row in result["ranked_candidates"])
        assert result["external_sent"] is False and result["grants_contact_authority"] is False
        assert intelligence.run_research(tx, mission(), OPERATOR, "research-one") == result
        assert len(model.calls) == 1
        # This module-level call has no trusted engine assignment.
        assert model.calls[0]["workflow_id"] == "research:mission-synthetic:lead_research"
        assert len(tx.list("lead")) == 2 and tx.list("email_message") == tx.list("payment") == []


def test_hallucinated_identity_duplicate_and_unhashable_ids_are_quarantined(db, model):
    with db.transaction() as tx:
        lead_id = seed(tx)[0]
        model.content = {"summary": "Ranked existing records", "candidates": [
            {"lead_id": "made-up-company", "score": 99, "reason": "Invented company"},
            {"lead_id": ["malformed"], "score": 99, "reason": "Invalid shape"},
            {"lead_id": lead_id, "company": "Invented replacement identity", "score": 90, "reason": "Attributed evidence"},
            {"lead_id": lead_id, "score": 91, "reason": "Duplicate"}]}
        report = intelligence.run_research(tx, mission(), OPERATOR, "malformed-identities")
        assert report["status"] == "REVIEW_REQUIRED" and len(report["ranked_candidates"]) == 1
        assert report["ranked_candidates"][0]["company"] == company()["company"]
        assert tx.get("lead", lead_id)["company"] == company()["company"] and len(tx.list("lead")) == 1


def test_research_provider_request_is_bounded_complete_json_with_explicit_coverage(db, model):
    with db.transaction() as tx:
        seed(tx, [company(i, evidence="Source-attributed company enquiries. " * 100,
            source_url="https://directory.example.com/" + "x" * 1600 + str(i)) for i in range(1, 31)])
        report = intelligence.run_research(tx, mission(goal="G" * 3000), OPERATOR, "bounded-research")
        request = json.loads(model.calls[0]["text"])
        assert 0 < len(request["candidates"]) < 30 and len(model.calls[0]["text"]) <= 12000
        assert report["coverage"]["available_records"] == 30
        assert report["coverage"]["analyzed_records"] == len(request["candidates"])
        assert set(report["lead_ids"]) == {row["lead_id"] for row in request["candidates"]}


def test_research_fetches_only_one_feed_per_bounded_job(db, model, monkeypatch):
    fetched = []
    with db.transaction() as tx:
        for source_id in ("directory", "second-feed", "third-feed"):
            research.configure_source(tx, source_id, config(kind="https_json", fetch_enabled=True), ADMIN)

        def fetch(tx, source_id, actor, key):
            fetched.append(source_id)
            return research.ingest(tx, source_id, [company()], actor, key)

        monkeypatch.setattr(research, "fetch_source", fetch)
        report = intelligence.run_research(tx, mission(source_ids=["directory", "second-feed", "third-feed"],
            refresh_sources=True), OPERATOR, "bounded-fetch")
        assert fetched == ["directory"] and len(model.calls) == 1
        assert report["coverage"]["not_refreshed_source_ids"] == ["second-feed", "third-feed"]
        assert report["coverage"]["max_network_fetches_per_job"] == 1


@pytest.mark.parametrize("source_status", ["UNCERTAIN", "COMPLETED"])
def test_uncertain_or_empty_source_preserves_report_without_fabricating_data(db, model, monkeypatch, source_status):
    with db.transaction() as tx:
        research.configure_source(tx, "directory", config(kind="https_json", fetch_enabled=True), ADMIN)
        monkeypatch.setattr(research, "fetch_source", lambda *args: {"status": source_status, "reservation_id": "synthetic-reservation"})
        result = intelligence.run_research(tx, mission(), OPERATOR, "empty-source")
        assert result["status"] == ("UNCERTAIN" if source_status == "UNCERTAIN" else "NO_RESEARCH_DATA")
        assert result["lead_ids"] == result["ranked_candidates"] == []
        assert model.calls == [] and tx.list("lead") == []
        assert result["fetches"][0]["reservation_id"] == "synthetic-reservation"


def test_no_source_records_is_actionable_and_does_not_invent_companies(db, model):
    with db.transaction() as tx:
        research.configure_source(tx, "directory", config(), ADMIN)
        with pytest.raises(PolicyError) as exc:
            intelligence.run_research(tx, mission(), OPERATOR, "no-imports")
        assert exc.value.code == "NO_RESEARCH_DATA" and model.calls == []


@pytest.mark.parametrize("score", [-1, 101, True, "88"])
def test_ai_scores_are_bounded_without_mutating_stored_facts(db, model, score):
    with db.transaction() as tx:
        lead_id = seed(tx)[0]
        before = tx.get("lead", lead_id)
        model.content = {"score": score, "reason": "Source-based assessment"}
        report = intelligence.score_lead(tx, lead_id, OPERATOR, "invalid-score")
        assert report["status"] == "REVIEW_REQUIRED" and report["assessment"] is None
        assert tx.get("lead", lead_id) == before and len(model.calls) == 1


def test_scoring_retains_source_identity_and_marks_catalogue_value_not_revenue(db, model):
    with db.transaction() as tx:
        lead_id = seed(tx)[0]
        result = intelligence.score_lead(tx, lead_id, OPERATOR, "score-one", mission_id="mission-one")
        lead = tx.get("lead", lead_id)
        assert result["status"] == "COMPLETED" and lead["crm"]["score"] == 88
        assert lead["crm"]["estimated_business_value_minor"] == 500000
        assert "not contracted value or revenue" in lead["crm"]["value_basis"]
        assert lead["company"] == company()["company"] and lead["state"] == "DISCOVERED"
        assert intelligence.score_lead(tx, lead_id, OPERATOR, "score-one", mission_id="mission-one") == result
        assert len(model.calls) == 1 and model.calls[0]["workflow_id"] == "mission:mission-one:lead_research"
        assert tx.list("payment") == []


@pytest.mark.parametrize("mode", ["DEMO", "UNCLASSIFIED"])
def test_demo_and_unclassified_leads_cannot_become_real_ai_work(db, model, mode):
    with db.transaction() as tx:
        lead_id = seed(tx)[0]
        lead = tx.get("lead", lead_id)
        lead.update(record_mode=mode, is_test=mode == "DEMO")
        tx.put("lead", lead_id, lead)
        for operation in (intelligence.score_lead, intelligence.prepare_sales):
            with pytest.raises(PolicyError) as exc:
                operation(tx, lead_id, OPERATOR, "reject-" + operation.__name__)
            assert exc.value.code == "SOURCE_EVIDENCE_REQUIRED"
        assert model.calls == []


def test_current_source_version_is_required_and_reimport_refreshes_evidence(db, model):
    with db.transaction() as tx:
        lead_id = seed(tx)[0]
        research.configure_source(tx, "directory", config(), ADMIN)
        assert intelligence.list_leads(tx)["items"][0]["source_verified"] is False
        with pytest.raises(PolicyError, match="current approved citation"):
            intelligence.score_lead(tx, lead_id, OPERATOR, "stale-source")
        research.ingest(tx, "directory", [company()], OPERATOR, "current-version-import")
        row = intelligence.list_leads(tx)["items"][0]
        assert row["source_verified"] is True and len(row["citations"]) == 2
        assert row["verified_citations"][0]["source_version"] == 2
        research.configure_source(tx, "directory", config(approved=False), ADMIN)
        assert intelligence.list_leads(tx, {"verified": True})["total"] == 0
        assert model.calls == []


def test_crm_search_filters_sort_and_pagination_use_actual_records(db, model):
    with db.transaction() as tx:
        first, second = seed(tx, [company(1, industry="Retail", geography="Sweden"),
            company(2, industry="Manufacturing", geography="Norway")])
        intelligence.score_lead(tx, first, OPERATOR, "first-score")
        model.content = {"score": 25, "reason": "Weak evidence"}
        intelligence.score_lead(tx, second, OPERATOR, "second-score")
        assert intelligence.list_leads(tx, {"search": "COMPANY 1", "record_mode": "REAL"})["items"][0]["id"] == first
        high = intelligence.list_leads(tx, {"industry": "ret", "geography": "swe", "min_score": 80,
            "owner_agent": "lead_research", "verified": True})
        assert high["total"] == 1 and high["items"][0]["id"] == first
        page = intelligence.list_leads(tx, {"sort": "ai_score", "order": "desc", "limit": 1, "offset": 1})
        assert page["total"] == 2 and page["items"][0]["id"] == second
        for filters in ({"sort": "external_send"}, {"limit": True}, {"verified": "true"}, {"min_score": 101}):
            with pytest.raises(PolicyError):
                intelligence.list_leads(tx, filters)


@pytest.mark.parametrize("operation", ["research", "score", "sales"])
def test_unknown_or_malformed_paid_ai_results_persist_and_never_retry(db, model, operation):
    with db.transaction() as tx:
        lead_id = seed(tx)[0]
        model.status = "UNKNOWN"
        call = {"research": lambda: intelligence.run_research(tx, mission(), OPERATOR, "uncertain"),
            "score": lambda: intelligence.score_lead(tx, lead_id, OPERATOR, "uncertain"),
            "sales": lambda: intelligence.prepare_sales(tx, lead_id, OPERATOR, "uncertain")}[operation]
        result = call()
        assert result["status"] == "UNCERTAIN" and result["ai_result_id"]
        assert call() == result and len(model.calls) == 1
        assert tx.get("lead", lead_id).get("crm") is None
    with db.transaction() as tx:
        assert len(tx.list("ai_result")) == 1
        assert any(row["status"] == "UNCERTAIN" for kind in ("research_report", "crm_assessment", "sales_offer") for row in tx.list(kind))


@pytest.mark.parametrize("raw", ["not JSON", "[]", '{"candidates":"not a list"}'])
def test_malformed_model_output_requires_review_after_one_recorded_ai_call(db, model, raw):
    with db.transaction() as tx:
        seed(tx)
        model.raw = raw
        result = intelligence.run_research(tx, mission(), OPERATOR, "malformed-text")
        assert result["status"] == "REVIEW_REQUIRED" and result["ranked_candidates"] == []
        assert result["ai_result_id"] and len(model.calls) == 1


def test_synthetic_ai_fallback_cannot_populate_real_report(db, model):
    with db.transaction() as tx:
        seed(tx)
        model.simulated = True
        model.raw = "Synthetic content must not be presented as a real outcome"
        result = intelligence.run_research(tx, mission(), OPERATOR, "forbidden-demo-output")
        assert result["status"] == "BLOCKED" and result["ranked_candidates"] == []
        assert result["analysis"]["text"] == result["analysis"]["summary"] == ""


def test_sales_prices_are_canonical_and_internal_approval_grants_no_external_authority(db, model):
    with db.transaction() as tx:
        lead_id = seed(tx)[0]
        offer = intelligence.prepare_sales(tx, lead_id, OPERATOR, "sales-one", mission_id="mission-one",
            pricing={"discount_bps": 1000})
        assert offer["status"] == "DRAFT" and offer["price"]["list_price_minor"] == 500000
        assert offer["price"]["amount_minor"] == 450000 and offer["price"]["currency"] == "SEK"
        assert offer["service_registry_approved"] is True and offer["administrator_approval"] is None
        with pytest.raises(PolicyError, match="Administrator"):
            intelligence.review_offer(tx, offer["id"], True, "Reviewed internal material", OPERATOR)
        approved = intelligence.review_offer(tx, offer["id"], True, "Reviewed internal proposal and source evidence", ADMIN)
        assert approved["status"] == "APPROVED_INTERNAL"
        assert approved["grants_contact_authority"] is approved["grants_payment_authority"] is False
        assert approved["administrator_approval"]["grants_contact_authority"] is False
        assert tx.get("lead", lead_id)["state"] == "DISCOVERED"
        assert tx.list("email_message") == tx.list("payment") == tx.list("payment_request") == []
        assert len(model.calls) == 1 and model.calls[0]["workflow_id"] == "mission:mission-one:sales"


@pytest.mark.parametrize("pricing", [{"list_price_minor": 499999}, {"discount_bps": 1001}, {"discount_bps": True}])
def test_sales_economic_limits_apply_before_any_ai_call(db, model, pricing):
    with db.transaction() as tx:
        lead_id = seed(tx)[0]
        with pytest.raises(PolicyError):
            intelligence.prepare_sales(tx, lead_id, OPERATOR, "bad-price", pricing=pricing)
        assert model.calls == [] and tx.list("sales_offer") == []


def test_unregistered_service_is_review_material_and_cannot_be_approved(db, model):
    with db.transaction() as tx:
        lead_id = seed(tx)[0]
        model.content = {"service_id": "unreviewed-market-analysis", "package_name": "Market analysis idea",
            "fit_reason": "Possible service hypothesis", "email_subject": "Internal draft",
            "email_body": "Unregistered service idea for internal review", "follow_up_plan": [],
            "price_minor": 1, "customer_accepted": True}
        offer = intelligence.prepare_sales(tx, lead_id, OPERATOR, "unregistered")
        assert offer["status"] == "REVIEW_REQUIRED" and offer["service_registry_approved"] is False
        assert offer["price"]["amount_minor"] == 500000 and "price_minor" not in offer["content"]
        assert offer["customer_accepted"] is False and "customer_accepted" not in offer["content"]
        with pytest.raises(PolicyError, match="registered-service"):
            intelligence.review_offer(tx, offer["id"], True, "Attempting approval cannot override service review", ADMIN)


def test_service_revocation_blocks_previously_prepared_internal_offer_approval(db, model):
    with db.transaction() as tx:
        lead_id = seed(tx)[0]
        offer = intelligence.prepare_sales(tx, lead_id, OPERATOR, "before-revocation")
        tx.put("service", workflow.SERVICE_ID, {**workflow.service_registry(tx)[0], "approved": False})
        with pytest.raises(PolicyError, match="registered-service"):
            intelligence.review_offer(tx, offer["id"], True, "Service was revoked after this draft", ADMIN)


def test_emergency_stop_blocks_all_new_intelligence_before_model_calls(db, model):
    with db.transaction() as tx:
        lead_id = seed(tx)[0]
        policy.set_kill_switch(tx, True, "Synthetic emergency-stop validation", ADMIN)
        for call in (lambda: intelligence.run_research(tx, mission(), OPERATOR, "killed-research"),
            lambda: intelligence.score_lead(tx, lead_id, OPERATOR, "killed-score"),
            lambda: intelligence.prepare_sales(tx, lead_id, OPERATOR, "killed-sales")):
            with pytest.raises(PolicyError) as exc:
                call()
            assert exc.value.code == "kill_switch"
        assert model.calls == []
