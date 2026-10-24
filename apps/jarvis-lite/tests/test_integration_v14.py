"""Original mission recovery over real HTTP/AI adapter boundaries, entirely offline."""
import json

import httpx

from jarvis_lite import ai, budget, engine, jobs
from jarvis_lite.store import fingerprint
from test_integration_v13 import ADMIN, EVIDENCE, REASON, SOURCE, login, post, running


def prepare_failed_mission(client, database, monkeypatch):
    """Run four complete specialists and one billed, truncated Finance response."""
    headers = login(client)
    post(client, "/api/research/sources/" + SOURCE, headers, {
        "name": "Godkänt syntetiskt återhämtningsunderlag", "kind": "upload",
        "url": "https://market.example.com/evidence", "approved": True, "license_reviewed": True,
        "legal_review_reference": REASON, "cost_evidence": REASON, "data_mode": "REAL"})
    post(client, "/api/research/sources/" + SOURCE + "/documents", headers, {
        "idempotency_key": "v14-approved-document", "documents": [{"title": "Syntetiskt kundbehov",
        "content": EVIDENCE + " Priser och lönsamhet har inte verifierats.", "facts": [{
            "subject": "Syntetiska småföretag", "predicate": "behov", "value": "snabbare svar",
            "kind": "fact", "evidence": EVIDENCE}]}]})
    monkeypatch.setenv("JARVIS_ALLOW_PAID_AI", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-v14-no-real-provider-key")
    with database.transaction() as tx:
        budget.set_fx_rate(tx, currency="USD", sek_per_unit="12", source=REASON,
                           observed_at=tx.now, actor=ADMIN)
        ai.configure(tx, {"enabled": True, "max_output_tokens": 512, "reason": REASON}, ADMIN)
    calls = []

    def provider(request):
        assert str(request.url) == ai.ENDPOINT
        body = json.loads(request.content)
        prompt = json.loads(json.loads(body["messages"][-1]["content"])["untrusted_content"])
        contract = body.get("response_format", {}).get("json_schema", {}).get("name", "")
        calls.append({"contract": contract, "max_tokens": body["max_tokens"]})
        finance_calls = sum(item["contract"] == "jarvis_finance_analysis" for item in calls)
        if contract == "jarvis_finance_analysis" and finance_calls == 1:
            assert body["max_tokens"] == 512
            return httpx.Response(200, json={"id": "v14-truncated-finance", "usage": {
                "cost": "0.001", "prompt_tokens": 100, "completion_tokens": 512},
                "choices": [{"finish_reason": "length", "message": {"content":
                    '{"action":"recommend","text":"Avkortad finansiell analys'}}]})
        action = "recommend"
        if "roles" in prompt:
            content = {"tasks": [{"role": role, "objective": "Bearbeta godkänt internt " + role + " underlag"}
                                 for role in engine.SPECIALISTS]}
        elif "required_section_keys" in prompt:
            action = "draft"
            content = {"title": "Intern affärsplan med tydliga osäkerheter", "sections": [
                {"key": key, "heading": key,
                 "content": "Källan beskriver behov av snabbare svar. Lösningsförslaget är en intern hypotes som kräver mänsklig validering före kommersiell användning.",
                 "source_ids": [prompt["source_evidence"][0]["id"]]} for key in prompt["required_section_keys"]],
                "next_actions": ["Granska underlag och validera affärsantaganden internt"],
                "limitations": ["Syntetiskt testunderlag verifierar inte marknadsstorlek, verkliga leveranskostnader eller betalningsvilja."],
                "company_ids": []}
        elif contract == "jarvis_compliance_qa":
            action = "classify"
            content = {"verdict": "pass", "findings": [], "summary":
                "Interna leverabler har källor och tydliga osäkerheter; externa handlingar godkänns inte.", "source_ids": [SOURCE]}
        elif contract == "jarvis_sales_strategy":
            content = {"summary": "En intern hypotes från det dokumenterade behovet.", "opportunities": [{
                "title": "Interna svarsutkast", "target_group": "Småföretag med kundförfrågningar",
                "problem": "Svar på vanliga frågor tar tid enligt underlaget", "solution": "AI-utkast med mänsklig granskning",
                "price_estimate_minor": 500000, "cost_estimate_minor": 100000,
                "competition": "Konkurrenternas faktiska erbjudanden har inte verifierats",
                "risks": ["Dataskydd och betalningsvilja kräver granskning"], "source_ids": [SOURCE],
                "next_steps": ["Validera hypotesen internt"], "assumptions": ["Pris och kostnad är estimat, inte intäkter"]}]}
        elif contract == "jarvis_research_report":
            content = {"summary": "Godkänt underlag beskriver ett behov av snabbare svar.", "candidates": [],
                "findings": [{"claim": EVIDENCE, "source_ids": [SOURCE], "kind": "fact"}],
                "uncertainties": ["Betalningsvilja och marknadsstorlek har inte verifierats"]}
        else:
            content = "Analysen gäller faktisk intern bearbetning av syntetiskt godkänt källunderlag. Bokförda kostnader finns i huvudboken. Uppskattade priser är inte intäkter och okända kostnader hindrar verifierad lönsamhet. Inga externa affärsåtgärder utförs."
        return httpx.Response(200, json={"id": "v14-mock-" + str(len(calls)), "usage": {
            "cost": "0.001", "prompt_tokens": 100, "completion_tokens": 70},
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
                "action": action, "text": content, "reason": REASON})}}]})

    original = ai.OpenRouterAdapter

    class OfflineAdapter(original):
        def __init__(self, key):
            super().__init__(key, transport=httpx.MockTransport(provider))

    monkeypatch.setattr(ai, "OpenRouterAdapter", OfflineAdapter)
    mission = post(client, "/api/missions", headers, {"idempotency_key": "v14-recovery-mission",
        "goal": "Analysera godkänt marknadsunderlag och skapa en intern affärsplan med dokumenterade risker och preliminär ekonomi.",
        "source_ids": [SOURCE], "deliverable_kind": "business_plan", "max_cost_minor": 2000,
        "deadline_seconds": 3600})
    for _ in range(4):
        assert post(client, "/api/jobs/run", headers, {})["job"]["status"] == "completed"
    assert post(client, "/api/jobs/run", headers, {})["job"]["status"] == "paused"
    detail = client.get("/api/missions/" + mission["id"]).json()
    assert detail["mission"]["completed_steps"] == 4 and detail["mission"]["total_steps"] == 7
    assert detail["mission"]["status"] == "REVIEW_REQUIRED"
    assert detail["mission"]["blocker"]["code"] == "ai_output_truncated"
    assert len(calls) == 5
    completed = {item["id"]: item for item in detail["tasks"] if item["status"] == "COMPLETED"}
    return headers, mission["id"], detail, completed, calls


def retry_payload(detail, key="v14-reviewed-retry"):
    return {"reason": REASON, "idempotency_key": key,
            "expected_task_id": detail["recovery"]["failed_task_id"],
            "expected_trace_id": detail["recovery"]["failed_trace_id"]}


def approve_larger_output(client, headers):
    status = post(client, "/api/ai/config", headers, {
        "max_output_tokens": 4096, "enabled": True, "reason": REASON})
    assert status["config"]["enabled"] is False and status["config"]["activation_required"] is True
    return status


def activate_current(client, headers, status):
    return post(client, "/api/ai/activation", headers, {"statement": ai.ACTIVATE,
        "config_revision": status["config"]["revision"], "reason": REASON})


def test_http_recovers_only_finance_then_unfinished_steps_with_new_approval(running, monkeypatch):
    client, database = running
    headers, mission_id, detail, completed, calls = prepare_failed_mission(client, database, monkeypatch)
    path = "/api/missions/" + mission_id + "/retry-failed-step"
    payload = retry_payload(detail)
    with database.transaction() as tx:
        old_traces = {item["id"]: item for item in tx.list("ai_trace")}
        old_entries = {item["id"]: item for item in tx.list("budget_entry")}
        old_results = {item["id"]: item for item in tx.list("agent_result")}
        old_audits = {item["id"]: item for item in tx.audits(limit=500)}
    status = approve_larger_output(client, headers)
    blocked = client.post(path, headers=headers, json=payload)
    assert blocked.status_code == 409 and blocked.json()["error"] == "ai_activation_required"
    with database.transaction() as tx:
        assert tx.list("mission_retry") == [] and len(tx.list("ai_trace")) == 5
    activate_current(client, headers, status)
    retry = post(client, path, headers, payload)
    assert retry["id"] == mission_id and retry["idempotent"] is False
    duplicate = post(client, path, headers, payload)
    assert duplicate["retry_id"] == retry["retry_id"] and duplicate["idempotent"] is True
    for _ in range(3):
        assert post(client, "/api/jobs/run", headers, {})["job"]["status"] == "completed"
    assert jobs.run_once(database) is None
    final = client.get("/api/missions/" + mission_id).json()
    assert final["mission"]["completed_steps"] == 7
    assert final["mission"]["status"] == "REVIEW_REQUIRED"  # Final human output review remains required.
    assert len(calls) == 8 and [item["contract"] for item in calls[5:]] == [
        "jarvis_finance_analysis", "jarvis_compliance_qa", "jarvis_ceo_summary"]
    assert all(item["max_tokens"] == 4096 for item in calls[5:])
    assert {item["id"]: item for item in final["tasks"] if item["id"] in completed} == completed
    assert final["recovery"]["additional_cost_minor"] == 12
    assert len(final["recovery"]["retries"]) == 1
    assert final["recovery"]["retries"][0]["input_tokens"] == 100
    assert final["recovery"]["retries"][0]["output_tokens"] == 70
    with database.transaction() as tx:
        assert len(tx.list("ai_trace")) == len(tx.list("budget_entry")) == 8
        for kind, previous in (("ai_trace", old_traces), ("budget_entry", old_entries), ("agent_result", old_results)):
            assert {key: tx.get(kind, key) for key in previous} == previous
        audits = {item["id"]: item for item in tx.audits(limit=500)}
        assert {key: audits[key] for key in old_audits} == old_audits
        assert tx.list("email_message") == tx.list("payment") == []
        assert all(item["state"] == "SETTLED" for item in tx.list("budget_entry"))
    overview = client.get("/api/v1.4/overview").json()
    assert overview["version"] == "1.4.0" and overview["metrics"]["actual_cost_minor"] == 96
    assert overview["metrics"]["verified_revenue_minor"] == 0
    # A late repeat of the original click returns its persisted request, even after successful execution.
    assert post(client, path, headers, payload)["retry_id"] == retry["retry_id"]


def test_preupgrade_changed_token_config_needs_current_revision_paid_approval(running, monkeypatch):
    """v1.3 retained enabled=true on a bound change; upgrading cannot treat that as renewal."""
    client, database = running
    headers, mission_id, detail, completed, calls = prepare_failed_mission(client, database, monkeypatch)
    path = "/api/missions/" + mission_id + "/retry-failed-step"
    payload = retry_payload(detail, "v14-legacy-4096-review")
    with database.transaction() as tx:
        legacy = tx.get("ai_config", "global")
        legacy.update(max_output_tokens=4096, enabled=True)
        legacy.pop("activation_required", None)
        legacy["revision"] = fingerprint({name: legacy[name] for name in ai.DEFAULTS})
        # Synthetic stored state models an already completed v1.3 admin change;
        # this never accesses or edits the real mission/database named by the user.
        tx.put("ai_config", "global", legacy)
        ai._fence(tx, ADMIN)
    rejected = client.post(path, headers=headers, json=payload)
    assert rejected.status_code == 409 and rejected.json()["error"] == "ai_activation_required"
    assert len(calls) == 5
    with database.transaction() as tx:
        assert tx.list("mission_retry") == [] and len(tx.list("ai_trace")) == 5
    state = client.get("/api/v1.4/overview").json()["ai"]
    assert state["config"]["revision"] == legacy["revision"]
    activate_current(client, headers, state)
    authorized = post(client, path, headers, payload)
    assert authorized["id"] == mission_id
    for _ in range(3):
        assert post(client, "/api/jobs/run", headers, {})["job"]["status"] == "completed"
    final = client.get("/api/missions/" + mission_id).json()
    assert final["mission"]["completed_steps"] == 7 and len(calls) == 8
    assert calls[5]["contract"] == "jarvis_finance_analysis" and calls[5]["max_tokens"] == 4096
    assert {item["id"]: item for item in final["tasks"] if item["id"] in completed} == completed


def test_retry_http_requires_admin_csrf_and_server_owned_bounds(running, monkeypatch):
    client, database = running
    headers, mission_id, detail, _, calls = prepare_failed_mission(client, database, monkeypatch)
    path = "/api/missions/" + mission_id + "/retry-failed-step"
    payload = retry_payload(detail)
    assert client.post(path, json=payload).status_code == 403
    for role in ("viewer", "operator"):
        role_headers = login(client, role)
        assert client.post(path, headers=role_headers, json=payload).status_code == 403
    headers = login(client)
    for override in ({"max_cost_minor": 50000}, {"max_output_tokens": 4096}, {"deadline_at": 10**12}):
        assert client.post(path, headers=headers, json={**payload, **override}).status_code == 422
    client.post("/api/auth/logout", headers=headers)
    assert client.post(path, headers=headers, json=payload).status_code == 401
    assert len(calls) == 5
    with database.transaction() as tx:
        assert tx.list("mission_retry") == [] and len(tx.list("ai_trace")) == 5
