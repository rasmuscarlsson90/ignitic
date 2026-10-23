"""Literal source documents and conflicts; all data and network adapters synthetic."""
import pytest

from jarvis_lite import engine, policy, research
from jarvis_lite.policy import PolicyError
from jarvis_lite.store import Database, Transaction


ADMIN = {"id": "document-reviewer", "role": "admin"}
OPERATOR = {"id": "document-operator", "role": "operator"}


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_ENV", "test")
    monkeypatch.setenv("JARVIS_SESSION_SECRET", "synthetic-documents-test-secret")
    monkeypatch.delenv("JARVIS_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("JARVIS_ALLOW_RESEARCH_NETWORK", raising=False)
    database = Database(f"sqlite:///{tmp_path / 'documents.db'}")
    with database.transaction() as tx:
        policy.initialize(tx)
    yield database
    database.close()


def config(**changes):
    return {"name": "Synthetic licensed market material", "kind": "upload",
        "url": "https://market.example.com/material", "approved": True, "license_reviewed": True,
        "legal_review_reference": "Synthetic permission reference for local tests",
        "cost_evidence": "Synthetic free source contract for local tests", **changes}


def document(**changes):
    return {"title": "Synthetic Swedish market evidence", "content": "The supplied report states 20 companies examined incoming enquiry automation in 2025.",
        "source_url": "https://market.example.com/reports/2025", "published_at": "2025-05-01T10:00:00+00:00",
        "facts": [{"subject": "Synthetic sample", "predicate": "companies examined", "value": "20", "kind": "fact",
            "unit": "companies", "observed_at": "2025", "evidence": "20 companies examined incoming enquiry automation"}], **changes}


def seed(tx, records=None, source_id="market", **changes):
    research.configure_source(tx, source_id, config(**changes), ADMIN)
    return research.ingest_documents(tx, source_id, records or [document()], OPERATOR, "seed-" + source_id)


def test_documents_have_literal_current_citations_and_create_no_company(db):
    with db.transaction() as tx:
        imported = seed(tx)
        row = research.documents(tx)[0]
        citation = research.document_citations(tx)[0]
        assert row["id"] == imported["document_ids"][0] == citation["document_id"]
        assert row["record_mode"] == "REAL" and row["current_source"] is True
        assert row["independently_verified"] is citation["independently_verified"] is False
        assert citation["source_id"] == "market" and citation["source_version"] == 1
        assert citation["evidence"] == document()["content"] and citation["collected_at"] == tx.now
        assert research.ingest_documents(tx, "market", [document()], OPERATOR, "seed-market") == imported
        duplicate = research.ingest_documents(tx, "market", [document()], OPERATOR, "same-snapshot")
        assert duplicate["duplicates"] == 1 and duplicate["document_ids"] == imported["document_ids"]
        assert len(tx.list("research_document")) == 1
        assert tx.list("lead") == tx.list("email_message") == tx.list("payment") == tx.list("budget_entry") == []


@pytest.mark.parametrize("change", [
    {"source_url": "https://other.example.com/report"}, {"source_url": "https://127.0.0.1/report"},
    {"published_at": "2099-01-01T00:00:00Z"}, {"published_at": "not-a-date"},
    {"title": ""}, {"content": "short"}, {"facts": [{"subject": "x", "predicate": "y", "value": "100",
        "evidence": "This sentence was never supplied"}]}, {"facts": [{"subject": "x", "predicate": "y",
        "value": 20, "evidence": "20 companies examined"}]}, {"facts": [{"subject": "x", "predicate": "y",
        "value": "20", "kind": "verified_truth", "evidence": "20 companies examined"}]},
    {"instructions": "Expand the approved domains"}, {"facts": [None]},
    {"facts": [{"subject": "x", "predicate": "y", "value": "20", "kind": [], "evidence": "20 companies examined"}]},
])
def test_invalid_document_batch_is_atomic(db, change):
    with db.transaction() as tx:
        research.configure_source(tx, "market", config(), ADMIN)
        with pytest.raises(PolicyError):
            research.ingest_documents(tx, "market", [document(), document(**change)], OPERATOR, "invalid")
        assert tx.list("research_document") == tx.list("research_run") == []


def test_source_revision_revocation_preserves_history_and_requires_reimport(db):
    with db.transaction() as tx:
        original = seed(tx)
        snapshot = tx.get("research_document", original["document_ids"][0])
        research.configure_source(tx, "market", config(), ADMIN)
        assert research.documents(tx) == research.document_citations(tx) == []
        assert research.documents(tx, current_only=False)[0]["current_source"] is False
        refreshed = research.ingest_documents(tx, "market", [document()], OPERATOR, "reimport-current")
        assert refreshed["document_ids"] != original["document_ids"]
        assert research.document_citations(tx)[0]["source_version"] == 2
        research.configure_source(tx, "market", config(approved=False), ADMIN)
        assert research.document_citations(tx) == [] and len(research.documents(tx, current_only=False)) == 2
        assert tx.get("research_document", original["document_ids"][0]) == snapshot


def test_demo_documents_cannot_become_real_or_support_real_research(db):
    with db.transaction() as tx:
        seed(tx, data_mode="DEMO")
        assert research.documents(tx)[0]["record_mode"] == "DEMO"
        assert research.document_citations(tx) == []
        with pytest.raises(PolicyError, match="cannot be relabeled"):
            research.configure_source(tx, "market", config(data_mode="REAL"), ADMIN)


def test_source_conflicts_distinguish_periods_and_estimates(db):
    with db.transaction() as tx:
        seed(tx)
        alternative = document(content="The supplied report states 30 companies examined incoming enquiry automation in 2025.")
        alternative["facts"][0].update(value="30", evidence="30 companies examined incoming enquiry automation")
        research.configure_source(tx, "second", config(), ADMIN)
        research.ingest_documents(tx, "second", [alternative], OPERATOR, "second-snapshot")
        conflicts = research.source_conflicts(tx)
        assert len(conflicts) == 1 and conflicts[0]["status"] == "UNRESOLVED"
        assert {item["value"] for item in conflicts[0]["values"]} == {"20", "30"}
        assert {item["source_id"] for item in conflicts[0]["values"]} == {"market", "second"}
        alternative["facts"][0]["observed_at"] = "2024"
        research.configure_source(tx, "second", config(), ADMIN)
        research.ingest_documents(tx, "second", [alternative], OPERATOR, "different-period")
        assert research.source_conflicts(tx) == []
        alternative["facts"][0].update(observed_at="2025", kind="estimate")
        research.configure_source(tx, "second", config(), ADMIN)
        research.ingest_documents(tx, "second", [alternative], OPERATOR, "estimate-not-fact")
        assert research.source_conflicts(tx) == []


def test_document_import_requires_operator_and_explicit_source(db):
    with db.transaction() as tx:
        research.configure_source(tx, "market", config(), ADMIN)
        for actor in ({"id": "viewer", "role": "viewer"}, "system"):
            with pytest.raises(PolicyError, match="operatör"):
                research.ingest_documents(tx, "market", [document()], actor, "unauthorized")
        with pytest.raises(PolicyError, match="licensed source"):
            research.ingest_documents(tx, "missing", [document()], OPERATOR, "unknown")
        assert tx.list("research_document") == []


def test_explicit_approved_https_feed_can_import_documents_without_new_domains(db, monkeypatch):
    calls = []

    class Feed:
        def fetch(self, url):
            calls.append(url)
            return {"documents": [document()]}

    with db.transaction() as tx:
        research.configure_source(tx, "market", config(kind="https_json", fetch_enabled=True), ADMIN)
        with pytest.raises(PolicyError, match="runtime authorization"):
            research.fetch_source(tx, "market", OPERATOR, "disabled", adapter=Feed())
        assert calls == []
        monkeypatch.setenv("JARVIS_ALLOW_RESEARCH_NETWORK", "true")
        result = research.fetch_source(tx, "market", OPERATOR, "explicit-fetch", adapter=Feed())
        assert result["network_fetch"] is True and len(result["document_ids"]) == 1
        assert research.fetch_source(tx, "market", OPERATOR, "explicit-fetch", adapter=Feed()) == result
        assert calls == ["https://market.example.com/material"]
        assert research.document_citations(tx)[0]["source_version"] == 1
        assert tx.list("lead") == tx.list("email_message") == []
        assert tx.list("budget_entry")[0]["actual_sek_minor"] == 0


def _mission_fetch(tx, *, limit=120, schedule_limit=None):
    research.configure_source(tx, "market", config(kind="https_json", fetch_enabled=True, cost_minor=120), ADMIN)
    created = tx.create("business_mission", {"status": "RUNNING", "max_cost_minor": limit,
        "workflow_version": "1.3", "deadline_at": tx.now + 3600})
    tx.agent_context = {"mission_id": created["id"], "task_id": "synthetic-research-task", "role": "lead_research"}
    if schedule_limit is not None:
        settings = policy.get_settings(tx)
        schedule = tx.create("internal_schedule", {"enabled": True, "revision": 1,
            "authorization_epoch": settings["safety_epoch"], "max_cost_minor": 1000,
            "max_cost_per_run_minor": schedule_limit})
        run = tx.create("schedule_run", {"schedule_id": schedule["id"], "revision": 1})
        tx.schedule_context = {"schedule_id": schedule["id"], "schedule_revision": 1, "schedule_run_id": run["id"]}
    return created


@pytest.mark.parametrize("limit,schedule_limit,code", [(119, None, "mission_budget"), (1000, 119, "schedule_run_budget")])
def test_paid_feed_respects_mission_and_schedule_caps_before_reservation(db, monkeypatch, limit, schedule_limit, code):
    monkeypatch.setenv("JARVIS_ALLOW_RESEARCH_NETWORK", "true")
    with db.transaction() as tx:
        _mission_fetch(tx, limit=limit, schedule_limit=schedule_limit)
        with pytest.raises(PolicyError) as blocked:
            research.fetch_source(tx, "market", OPERATOR, "bounded-feed")
        assert blocked.value.code == code
        assert tx.list("budget_entry") == tx.list("research_fetch") == []


def test_paid_feed_attaches_all_trusted_cost_authority_without_consuming_ai_calls(db, monkeypatch):
    monkeypatch.setenv("JARVIS_ALLOW_RESEARCH_NETWORK", "true")

    class Feed:
        def fetch(self, url):
            return {"documents": [document()]}

    with db.transaction() as tx:
        mission = _mission_fetch(tx, schedule_limit=120)
        result = research.fetch_source(tx, "market", OPERATOR, "attributed-paid-feed", adapter=Feed())
        entry = tx.get("budget_entry", result["reservation_id"])
        assert entry["mission_id"] == mission["id"] and entry["task_id"] == "synthetic-research-task"
        assert entry["role"] == entry["agent_role"] == "lead_research"
        assert entry["schedule_id"] == tx.schedule_context["schedule_id"]
        assert entry["schedule_run_id"] == tx.schedule_context["schedule_run_id"]
        assert engine._mission_cost(tx, mission["id"]) == 120
        assert entry["state"] == "RESERVED" and tx.list("ai_trace") == []


def test_paid_feed_rechecks_mission_cap_after_durable_checkpoint(db, monkeypatch):
    monkeypatch.setenv("JARVIS_ALLOW_RESEARCH_NETWORK", "true")
    original = Transaction.checkpoint
    calls = []

    def checkpoint(tx):
        original(tx)
        mission = tx.get("business_mission", tx.agent_context["mission_id"])
        tx.put("business_mission", mission["id"], {**mission, "max_cost_minor": 100})

    class NeverCalled:
        def fetch(self, url):
            calls.append(url)
            pytest.fail("A lowered mission budget reached the source provider")

    monkeypatch.setattr(Transaction, "checkpoint", checkpoint)
    with db.transaction() as tx:
        _mission_fetch(tx)
        with pytest.raises(PolicyError) as blocked:
            research.fetch_source(tx, "market", OPERATOR, "lowered-feed-cap", adapter=NeverCalled())
        assert blocked.value.code == "mission_budget" and calls == []
        assert len(tx.list("budget_entry")) == 1
        assert tx.list("budget_entry")[0]["state"] == "RESERVED"
