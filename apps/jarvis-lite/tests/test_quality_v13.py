"""QA consumes saved source versions and outputs, never provider permissions."""
import pytest

from jarvis_lite import engine, factory, policy, quality
from jarvis_lite.policy import PolicyError
from test_factory_v12 import ADMIN, OPERATOR, db, paid_mock
from test_factory_v13 import mission, summary_fixture


@pytest.mark.parametrize("change", ["revoked", "unlicensed", "version", "unlisted"])
def test_qa_rechecks_current_source_proof_and_never_repairs_missing_authority(db, monkeypatch, change):
    calls = paid_mock(monkeypatch)
    with db.transaction() as tx:
        assigned = mission(tx)
        result = factory.generate_for_mission(tx, assigned, OPERATOR, "source-result")
        approved_review = quality.review_mission(tx, assigned, OPERATOR, "initial-source-check")
        assert approved_review["status"] == "COMPLETED"
        assert quality.review_mission(tx, assigned, OPERATOR, "initial-source-check") == approved_review
        source = tx.get("research_source", assigned["source_ids"][0])
        if change == "revoked":
            source["approved"] = False
        elif change == "unlicensed":
            source["license_reviewed"] = False
        elif change == "version":
            source["version"] += 1
        else:
            settings = policy.get_settings(tx)
            settings["approved_sources"] = []
            tx.put("settings", "global", settings)
        tx.put("research_source", source["id"], source)
        checked = quality.review_mission(tx, assigned, OPERATOR, "changed-source-check")
        assert checked["severity"] == "major" and checked["revision_required"] is False
        assert any(issue["code"] == "source_not_current" for issue in checked["issues"])
        with pytest.raises(PolicyError, match="different input"):
            quality.review_mission(tx, assigned, OPERATOR, "initial-source-check")
        assert tx.get("service_result", result["id"]) == result and len(calls) == 1


@pytest.mark.parametrize("limitation", ["None.", "No limitations", "Inga"])
def test_short_or_empty_claimed_uncertainty_is_minor_without_legal_or_external_authority(db, monkeypatch, limitation):
    paid_mock(monkeypatch, mutate=lambda content: {**content, "limitations": [limitation]})
    with db.transaction() as tx:
        assigned = mission(tx)
        factory.generate_for_mission(tx, assigned, OPERATOR, "uncertainty-result")
        checked = quality.review_mission(tx, assigned, OPERATOR, "uncertainty-check")
        assert checked["severity"] == "minor" and checked["revision_required"] is True
        assert checked["semantic_correctness_verified"] is False
        assert checked["external_contact_authorized"] is checked["payment_authorized"] is False
        assert tx.list("email_message") == tx.list("payment") == []


def test_missing_artifact_unknown_outcome_and_unprivileged_request_are_not_successful_qa(db):
    with db.transaction() as tx:
        assigned = mission(tx, evidence=False)
        checked = quality.review_mission(tx, assigned, OPERATOR, "no-result-check")
        assert checked["status"] == "REVIEW_REQUIRED" and checked["severity"] == "major"
        assert checked["revision_required"] is False and checked["result_ids"] == []
        with pytest.raises(PolicyError):
            quality.review_mission(tx, assigned, {"role": "viewer"}, "viewer")
        policy.set_kill_switch(tx, True, "Synthetic emergency stop before quality control", ADMIN)
        with pytest.raises(PolicyError, match="Emergency stop"):
            quality.review_mission(tx, assigned, OPERATOR, "stopped")


def test_conflicting_research_facts_require_human_review_not_a_format_revision(db, monkeypatch):
    paid_mock(monkeypatch)
    with db.transaction() as tx:
        assigned = mission(tx)
        report = tx.create("research_report", {"mission_id": assigned["id"], "conflicts": [{
            "subject": "Synthetic company", "predicate": "employees", "values": ["10", "20"]}]})
        assigned["research_report_id"] = report["id"]
        factory.generate_for_mission(tx, assigned, OPERATOR, "conflict-result")
        checked = quality.review_mission(tx, assigned, OPERATOR, "conflict-check")
        assert checked["severity"] == "major" and checked["revision_required"] is False
        assert any(item["code"] == "conflicting_sources_require_review" for item in checked["issues"])


def test_revision_cannot_apply_to_a_changed_result_or_relabel_a_policy_failure_as_minor(db, monkeypatch):
    paid_mock(monkeypatch, mutate=lambda content: {**content, "limitations": ["None."]})
    with db.transaction() as tx:
        assigned = mission(tx)
        result = factory.generate_for_mission(tx, assigned, OPERATOR, "review-binding-result")
        checked = quality.review_mission(tx, assigned, OPERATOR, "review-binding-check")
        result["content"]["title"] += " Changed outside the reviewed version"
        tx.put("service_result", result["id"], result)
        with pytest.raises(PolicyError, match="exakt oförändrat"):
            factory.revise_for_mission(tx, assigned, OPERATOR, "changed-review-revision", checked["id"])


def test_rejected_exact_artifact_cannot_complete_a_previously_passed_mission(db):
    with db.transaction() as tx:
        assigned, _ = summary_fixture(tx)
        artifact = tx.list("service_result")[0]
        assert quality.review_mission(tx, assigned, OPERATOR, "before-rejection")["status"] == "COMPLETED"
        factory.review(tx, artifact["id"], "reject", "Administrator rejected this exact deliverable", ADMIN)
        checked = quality.review_mission(tx, assigned, OPERATOR, "after-rejection")
        assert checked["severity"] == "major" and checked["revision_required"] is False
        assert any(item["code"] == "rejected_deliverable" for item in checked["issues"])
        with pytest.raises(PolicyError) as blocked:
            engine.review_action(tx, "mission-review:" + assigned["id"], "approve", "Try to complete a rejected deliverable", ADMIN)
        assert blocked.value.code == "quality_review_required"
        assert tx.get("business_mission", assigned["id"])["status"] == "REVIEW_REQUIRED"
        assert tx.get("agent_action", "mission-review:" + assigned["id"])["status"] == "PENDING"


@pytest.mark.parametrize("expression,valid", [("5000 SEK - 1000 SEK = 4000 SEK", True),
    ("5000 - 1000 = 3500 SEK", False), ("10 / 0 = 0 SEK", False),
    ("2,50 × 4 = 10,00 SEK", True), ("Revenue is a future estimate, not a receipt.", True)])
def test_explicit_arithmetic_is_checked_without_inventing_financial_proof(expression, valid):
    assert quality._arithmetic(expression) is valid
