"""Real artifact persistence/QA contracts using synthetic sources and AI only."""
from copy import deepcopy
import json

import httpx
import pytest

from jarvis_lite import ai, budget, engine, factory, policy, quality, research
from jarvis_lite.policy import PolicyError
from test_factory_v12 import (ADMIN, OPERATOR, VIEWER, BRIEF, attributed_lead,
    db, deliverable, order, paid_mock, HTTP_STREAM)


NEW_TYPES = sorted(set(factory.TYPES) - factory.LEGACY_TYPES)


def mission(tx, *, kind="business_plan", evidence=True):
    lead_ids, source_ids = [], []
    if evidence:
        lead_id, source_id = attributed_lead(tx)
        lead_ids, source_ids = [lead_id], [source_id]
    value = tx.put("business_mission", "quality-mission", {"workflow_version": "1.3", "goal": BRIEF,
        "source_ids": source_ids, "lead_ids": lead_ids, "deliverable_kind": kind, "max_revisions": 1})
    tx.agent_context = {"mission_id": value["id"], "role": "delivery", "task_id": "delivery-task"}
    return value


@pytest.mark.parametrize("kind", NEW_TYPES)
def test_new_deliverable_types_are_saved_real_outputs_with_reviewable_metadata(db, monkeypatch, kind):
    calls = paid_mock(monkeypatch)
    with db.transaction() as tx:
        created = order(tx, kind)
        result = factory.generate(tx, created["id"], OPERATOR, "v13-generate")
        assert result["status"] == "NEEDS_REVIEW" and result["workflow_version"] == "1.3"
        assert result["source_snapshot"] and result["uncertainties"] == result["content"]["limitations"]
        assert result["financial_snapshot"]["currency"] == "SEK"
        assert result["auto_revision_number"] == 0 and result["previous_result_id"] is None
        details = factory.detail(tx, result["id"], VIEWER)
        assert details["sources"] == result["source_snapshot"] and details["versions"] == [result]
        assert details["order"]["brief"] == BRIEF
        assert len(calls) == 1 and getattr(tx, "ai_response_contract", None) is None
        assert tx.list("email_message") == tx.list("payment") == []


@pytest.mark.parametrize("kind", ["competitor_analysis", "research_report", "lead_report", "opportunity_report"])
def test_evidence_dependent_new_types_never_invent_data_without_sources(db, monkeypatch, kind):
    calls = paid_mock(monkeypatch)
    with db.transaction() as tx:
        created = order(tx, kind, evidence=False)
        blocked = factory.generate(tx, created["id"], OPERATOR, "missing-source")
        assert blocked["status"] == "BLOCKED" and blocked["code"] == "approved_evidence_required"
        assert calls == [] and tx.list("service_result") == []


def test_general_approved_document_supports_analysis_without_fabricating_leads(db, monkeypatch):
    calls = paid_mock(monkeypatch)
    with db.transaction() as tx:
        source = research.configure_source(tx, "source-document", {"name": "Synthetic approved report", "kind": "upload",
            "url": "https://research.example.com/market", "approved": True, "license_reviewed": True,
            "legal_review_reference": "Synthetic source rights review for tests", "cost_evidence": "Synthetic no-cost upload"}, ADMIN)
        monkeypatch.setattr(research, "document_citations", lambda tx, ids: [{"document_id": "document-one", "source_id": source["id"],
            "source_version": source["version"], "source_url": source["url"], "collected_at": tx.now,
            "evidence": "Synthetic published market report describes adoption barriers; numbers remain uncertain."}], raising=False)
        created = factory.create(tx, {"kind": "competitor_analysis", "brief": BRIEF, "source_ids": [source["id"]]}, OPERATOR, "document-order")
        result = factory.generate(tx, created["id"], OPERATOR, "document-generate")
        assert result["status"] == "NEEDS_REVIEW" and created["companies"] == []
        assert result["source_snapshot"][0]["document_id"] == "document-one"
        assert tx.list("lead") == [] and len(calls) == 1


def test_minor_qa_gap_gets_one_grounded_revision_and_exact_immutable_history(db, monkeypatch):
    count = []
    def short_uncertainty(content):
        count.append(True)
        return {**content, "limitations": ["None."]} if len(count) == 1 else content
    calls = paid_mock(monkeypatch, mutate=short_uncertainty)
    with db.transaction() as tx:
        assigned = mission(tx)
        first = factory.generate_for_mission(tx, assigned, OPERATOR, "initial-generation")
        first_saved = deepcopy(first)
        checked = quality.review_mission(tx, assigned, OPERATOR, "first-quality")
        assert checked["severity"] == "minor" and checked["revision_required"] is True
        assert checked["status"] == "REVIEW_REQUIRED" and checked["provider_calls"] == 0
        with pytest.raises(PolicyError, match="kvalitetskontroller"):
            factory.review(tx, first["id"], "approve", "Synthetic administrator exact review", ADMIN)
        revised = factory.revise_for_mission(tx, assigned, OPERATOR, "one-quality-revision", checked["id"])
        assert revised.get("version") == 2, revised
        assert revised["previous_result_id"] == first["id"]
        assert revised["auto_revision_number"] == 1 and revised["source_snapshot"] == first["source_snapshot"]
        assert tx.get("service_result", first["id"]) == first_saved
        assert "quality_revision" in json.loads(calls[-1]["text"])
        assert factory.revise_for_mission(tx, assigned, OPERATOR, "one-quality-revision", checked["id"])["id"] == revised["id"]
        checked_again = quality.review_mission(tx, assigned, OPERATOR, "second-quality")
        assert checked_again["status"] == "COMPLETED" and checked_again["severity"] == "none"
        assert checked_again["administrator_review_required"] is True
        approved = factory.review(tx, revised["id"], "approve", "Synthetic administrator exact review", ADMIN)
        assert approved["status"] == "APPROVED" and len(calls) == 2
        with pytest.raises(PolicyError):
            factory.revise_for_mission(tx, assigned, OPERATOR, "bypass-limit-new-key", checked["id"])
        assert len(calls) == 2 and tx.list("payment") == tx.list("email_message") == []


def test_major_arithmetic_issue_never_authorizes_automatic_revision(db, monkeypatch):
    def wrong_math(content):
        content["sections"][0]["content"] += " Preliminary scenario: 5000 SEK - 1000 SEK = 3500 SEK."
        return content
    calls = paid_mock(monkeypatch, mutate=wrong_math)
    with db.transaction() as tx:
        assigned = mission(tx)
        result = factory.generate_for_mission(tx, assigned, OPERATOR, "wrong-math")
        checked = quality.review_mission(tx, assigned, OPERATOR, "arithmetic-quality")
        assert checked["severity"] == "major" and checked["revision_required"] is False
        assert any(issue["code"] == "arithmetic_inconsistent" for issue in checked["issues"])
        with pytest.raises(PolicyError, match="mindre kvalitetsbrister"):
            factory.revise_for_mission(tx, assigned, OPERATOR, "must-not-revise", checked["id"])
        with pytest.raises(PolicyError):
            factory.review(tx, result["id"], "approve", "Synthetic administrator exact review", ADMIN)
        assert len(calls) == 1


def test_preflight_budget_rejection_is_blocked_not_falsely_uncertain(db, monkeypatch):
    successful = paid_mock(monkeypatch)
    original = ai.task
    def reject_budget(tx, **kwargs):
        raise PolicyError("agent_daily_budget", "Synthetic preflight cap; no provider contacted")
    monkeypatch.setattr(ai, "task", reject_budget)
    with db.transaction() as tx:
        created = order(tx, "business_plan")
        blocked = factory.generate(tx, created["id"], OPERATOR, "same-original-key")
        assert blocked["status"] == "BLOCKED" and blocked["code"] == "agent_daily_budget"
        assert tx.get("service_order", created["id"])["generation_state"] == "BLOCKED"
        monkeypatch.setattr(ai, "task", original)
        completed = factory.generate(tx, created["id"], OPERATOR, "same-original-key")
        assert completed["status"] == "NEEDS_REVIEW" and len(successful) == 1


def test_unexpected_policy_failure_after_durable_trace_retains_unknown_and_never_repeats(db, monkeypatch):
    paid_mock(monkeypatch)
    calls = []
    def charged_but_unknown(tx, **kwargs):
        calls.append(True)
        tx.create("ai_trace", {"status": "ATTEMPTED", "role": "delivery", "simulated": False})
        raise PolicyError("uncertain_format", "Synthetic durable attempt")
    monkeypatch.setattr(ai, "task", charged_but_unknown)
    with db.transaction() as tx:
        created = order(tx, "business_plan")
        uncertain = factory.generate(tx, created["id"], OPERATOR, "uncertain-generation")
        assert uncertain["status"] == "UNCERTAIN"
        assert factory.generate(tx, created["id"], OPERATOR, "new-key")["status"] == "UNCERTAIN"
        assert len(calls) == 1 and tx.list("service_result") == []


def test_catalogue_and_detail_remain_authenticated_and_do_not_grant_authority(db, monkeypatch):
    paid_mock(monkeypatch)
    with db.transaction() as tx:
        created = order(tx, "action_plan", evidence=False)
        result = factory.generate(tx, created["id"], OPERATOR, "plan-only")
        assert result["evidence_classification"] == "BRIEF_AND_ESTIMATES"
        assert len(factory.catalogue()) == 13
        assert all(item["acceptance_criteria"] for item in factory.catalogue())
        with pytest.raises(PolicyError):
            factory.detail(tx, result["id"], {"role": "anonymous"})
        assert factory.export(tx, result["id"], "markdown", VIEWER)["content"].startswith("Status: NEEDS_REVIEW")
        assert result["external_action"] is False


@pytest.mark.parametrize("text_as_object", [False, True])
def test_actual_ai_schema_normalization_and_factory_cost_pipeline_with_mock_http(db, monkeypatch, text_as_object):
    monkeypatch.setattr(httpx.Client, "stream", HTTP_STREAM)
    monkeypatch.setenv("JARVIS_ALLOW_PAID_AI", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-v13-factory-provider-key")
    requests = []
    def provider(request):
        payload = json.loads(request.content)
        prompt = json.loads(json.loads(payload["messages"][1]["content"])["untrusted_content"])
        requests.append(payload)
        content = deliverable(prompt["type"])
        envelope = {"action": "draft", "text": content if text_as_object else json.dumps(content),
                    "reason": "Synthetic internally generated business plan with attributed evidence"}
        return httpx.Response(200, json={"id": "synthetic-schema-factory-result", "usage": {
            "cost": "0.007", "prompt_tokens": 400, "completion_tokens": 800},
            "choices": [{"message": {"content": json.dumps(envelope)}, "finish_reason": "stop"}]})
    adapter = ai.OpenRouterAdapter("synthetic-v13-factory-provider-key", transport=httpx.MockTransport(provider))
    actual_task = ai.task
    monkeypatch.setattr(ai, "task", lambda tx, **kwargs: actual_task(tx, **kwargs, adapter=adapter))
    with db.transaction() as tx:
        budget.set_fx_rate(tx, currency="USD", sek_per_unit="12", source="Synthetic v1.3 factory test FX evidence",
                           observed_at=tx.now, actor=ADMIN)
        ai.configure(tx, {"enabled": True, "max_output_tokens": 2048,
            "reason": "Synthetic reviewed internal service schema and bounded test spend"}, ADMIN)
        created = order(tx, "business_plan")
        result = factory.generate(tx, created["id"], OPERATOR, "schema-factory-generation")
        assert result["status"] == "NEEDS_REVIEW", result
        assert len(requests) == 1 and requests[0]["response_format"]["type"] in {"json_object", "json_schema"}
        assert result["cost_sek_minor"] == budget.summary(tx)["actual_sek_minor"] == 12
        assert tx.list("ai_trace")[0]["status"] == "COMPLETED"
        assert factory.generate(tx, created["id"], OPERATOR, "schema-factory-generation")["id"] == result["id"]
        assert len(requests) == 1 and tx.list("email_message") == tx.list("payment") == []


def summary_fixture(tx, *, incomplete=False):
    assigned = mission(tx)
    lead = tx.get("lead", assigned["lead_ids"][0])
    report = tx.create("research_report", {"mission_id": assigned["id"], "citations": lead["citations"], "uncertainties": []})
    summary = tx.create("mission_summary", {"mission_id": assigned["id"], "title": "Synthetic final business report",
        "type": "mission_summary", "responsible_agent": "ceo", "content": "<script>not executable</script> A bounded internally generated final analysis.",
        "source_ids": assigned["source_ids"], "uncertainties": "Source claims still require human factual review.",
        "status": "NEEDS_REVIEW", "version": 1, "external_action": False})
    created = factory.create(tx, {"kind": "business_plan", "brief": BRIEF,
        "mission_id": assigned["id"], "source_ids": assigned["source_ids"], "lead_ids": assigned["lead_ids"]}, OPERATOR, "summary-artifact-order")
    artifact = tx.create("service_result", {"order_id": created["id"], "mission_id": assigned["id"], "kind": "business_plan",
        "content": deliverable("business_plan"), "status": "NEEDS_REVIEW", "version": 1,
        "source_snapshot": created["evidence"], "quality": {"structure_passed": True, "issues": []},
        "external_action": False, "simulated": False})
    created.update(generation_state="COMPLETED", latest_result_id=artifact["id"])
    tx.put("service_order", created["id"], created)
    checked = quality.review_mission(tx, assigned, OPERATOR, "summary-fixture-quality")
    assert checked["status"] == "COMPLETED"
    for number in range(7):
        tx.create("agent_task", {"mission_id": assigned["id"], "status": "QUEUED" if incomplete and number == 6 else "COMPLETED"})
    tx.put("agent_action", "mission-review:" + assigned["id"], {"mission_id": assigned["id"], "status": "PENDING"})
    assigned.update(status="REVIEW_REQUIRED", quality_passed=True, revision_count=0,
                    summary_id=summary["id"], research_report_id=report["id"], quality_review_id=checked["id"])
    assigned["quality_snapshot"] = engine._quality_snapshot(tx, assigned)
    tx.put("business_mission", assigned["id"], assigned)
    return assigned, summary


def test_ceo_summary_is_openable_and_exportable_with_sources_and_safe_content(db):
    with db.transaction() as tx:
        assigned, summary = summary_fixture(tx)
        displayed = factory.detail(tx, summary["id"], VIEWER)
        assert displayed["kind"] == "mission_summary" and displayed["content"]["sections"][0]["content"] == summary["content"]
        assert displayed["source_snapshot"][0]["source_id"] == assigned["source_ids"][0]
        assert displayed["uncertainties"] == [summary["uncertainties"]] and displayed["versions"] == [summary]
        markdown = factory.export(tx, summary["id"], "markdown", VIEWER)
        structured = factory.export(tx, summary["id"], "json", VIEWER)
        assert "<script>" not in markdown["content"] + structured["content"]
        assert "Synthetic final business report" in markdown["content"]
        assert json.loads(structured["content"])["mission_id"] == assigned["id"]
        with pytest.raises(PolicyError):
            factory.export(tx, summary["id"], "html", VIEWER)
        with pytest.raises(PolicyError):
            factory.detail(tx, summary["id"], {"role": "anonymous"})


@pytest.mark.parametrize("incomplete", [False, True])
def test_final_summary_review_uses_real_mission_completion_guard(db, incomplete):
    with db.transaction() as tx:
        assigned, summary = summary_fixture(tx, incomplete=incomplete)
        if incomplete:
            with pytest.raises(PolicyError, match="Blocked or uncertain"):
                factory.review(tx, summary["id"], "approve", "Synthetic exact final summary review", ADMIN)
            assert tx.get("mission_summary", summary["id"])["status"] == "NEEDS_REVIEW"
        else:
            approved = factory.review(tx, summary["id"], "approve", "Synthetic exact final summary review", ADMIN)
            assert approved["status"] == "APPROVED"
            assert tx.get("business_mission", assigned["id"])["status"] == "COMPLETED"
            with pytest.raises(PolicyError):
                factory.review(tx, summary["id"], "reject", "Attempt to change immutable summary review", ADMIN)
        assert tx.list("payment") == tx.list("email_message") == []


def test_summary_approval_rechecks_source_versions_after_earlier_qa(db):
    with db.transaction() as tx:
        assigned, summary = summary_fixture(tx)
        source = tx.get("research_source", assigned["source_ids"][0])
        source["version"] += 1
        tx.put("research_source", source["id"], source)
        assert factory.detail(tx, summary["id"], VIEWER)["current_checks"][0]["code"] == "source_not_current"
        with pytest.raises(PolicyError, match="källor måste granskas"):
            factory.review(tx, summary["id"], "approve", "Synthetic source-stale final review", ADMIN)


def test_business_plan_receives_actual_rank_one_opportunity_without_turning_estimates_into_facts(db, monkeypatch):
    calls = paid_mock(monkeypatch)
    with db.transaction() as tx:
        assigned = mission(tx)
        report = tx.create("research_report", {"mission_id": assigned["id"], "analysis": {"summary": "Synthetic approved-source analysis"},
            "uncertainties": ["Demand and willingness to pay require separate validation."], "conflicts": []})
        assigned["research_report_id"] = report["id"]
        tx.put("business_mission", assigned["id"], assigned)
        opportunities = tx.create("opportunity_report", {"mission_id": assigned["id"], "research_report_id": report["id"],
            "status": "COMPLETED", "opportunities": [{"rank": 2, "title": "Lower ranked hypothesis"},
                {"rank": 1, "title": "Synthetic incoming enquiry automation", "target_group": "B2B support teams",
                 "problem": "Repeated incoming questions", "solution": "Reviewed internal response drafts",
                 "price_estimate_minor": 500000, "cost_estimate_minor": 180000, "preliminary_difference_minor": 320000,
                 "assumptions": ["Willingness to pay is unverified"], "risks": ["Insufficient anonymized evaluation data"],
                 "source_ids": assigned["source_ids"]}]})
        generated = factory.generate_for_mission(tx, assigned, OPERATOR, "opportunity-grounded-plan")
        prompt = json.loads(calls[0]["text"])
        chosen = prompt["opportunity_context"]["selected_opportunity"]
        assert chosen["title"] == "Synthetic incoming enquiry automation" and chosen["rank"] == 1
        assert chosen["estimates_are_not_revenue"] is True and chosen["commercial_service_approved"] is False
        assert prompt["research_context"]["report_id"] == report["id"]
        opportunities["opportunities"][1]["title"] = "Changed after the order snapshot"
        tx.put("opportunity_report", opportunities["id"], opportunities)
        factory.generate(tx, generated["order_id"], OPERATOR, "intended-new-version")
        assert json.loads(calls[1]["text"])["opportunity_context"] == prompt["opportunity_context"]
        assert tx.list("payment") == tx.list("email_message") == []


def test_format_repair_artifact_reports_both_actual_costs_tokens_and_trace_ids(db, monkeypatch):
    monkeypatch.setattr(httpx.Client, "stream", HTTP_STREAM)
    monkeypatch.setenv("JARVIS_ALLOW_PAID_AI", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-v13-factory-correction-key")
    requests = []
    def provider(request):
        requests.append(request)
        content = deliverable("business_plan")
        if len(requests) == 1:
            del content["limitations"]  # A repairable schema failure, never a policy bypass.
        return httpx.Response(200, json={"id": "synthetic-correction-" + str(len(requests)), "usage": {
            "cost": "0.007", "prompt_tokens": 400, "completion_tokens": 300},
            "choices": [{"message": {"content": json.dumps({"action": "draft", "text": json.dumps(content),
                "reason": "Synthetic bounded correction fixture"})}, "finish_reason": "stop"}]})
    adapter = ai.OpenRouterAdapter("synthetic-v13-factory-correction-key", transport=httpx.MockTransport(provider))
    actual_task = ai.task
    monkeypatch.setattr(ai, "task", lambda tx, **kwargs: actual_task(tx, **kwargs, adapter=adapter))
    with db.transaction() as tx:
        budget.set_fx_rate(tx, currency="USD", sek_per_unit="12", source="Synthetic correction FX evidence",
            observed_at=tx.now, actor=ADMIN)
        ai.configure(tx, {"enabled": True, "max_output_tokens": 2048,
            "reason": "Synthetic reviewed one-format-correction spend ceiling"}, ADMIN)
        created = order(tx, "business_plan")
        result = factory.generate(tx, created["id"], OPERATOR, "correction-artifact")
        assert result["status"] == "NEEDS_REVIEW", result
        assert len(requests) == len(result["trace_ids"]) == 2
        assert result["cost_sek_minor"] == budget.summary(tx)["actual_sek_minor"] == 24
        assert result["input_tokens"] == 800 and result["output_tokens"] == 600
        assert {item["id"] for item in tx.list("ai_trace")} == set(result["trace_ids"])
        assert factory.generate(tx, created["id"], OPERATOR, "correction-artifact")["id"] == result["id"]
        assert len(requests) == 2
