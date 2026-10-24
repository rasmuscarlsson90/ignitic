"""A real internal business goal through HTTP, durable work and offline provider."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from jarvis_lite import ai, auth, budget, engine, jobs, policy
from jarvis_lite.app import create_app
from jarvis_lite.store import Database


ADMIN = {"id": "v13-integration-admin", "role": "admin"}
PASSWORD = "Synthetic-v13-integration-password-42"
REASON = "Granskat syntetiskt internt uppdrag och uttryckliga testkostnadsgränser"
SOURCE = "market-evidence"
EVIDENCE = "Syntetiskt underlag beskriver ett behov av snabbare svar på kundförfrågningar."


@pytest.fixture
def running(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_ENV", "test")
    monkeypatch.setenv("JARVIS_SESSION_SECRET", "v13-offline-integration-session-secret")
    monkeypatch.delenv("JARVIS_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("KILL_SWITCH", "false")
    for name in ("JARVIS_ALLOW_LIVE", "JARVIS_ALLOW_LIVE_CONTACT", "JARVIS_ALLOW_LIVE_PAYMENTS",
                 "JARVIS_ALLOW_PAID_AI", "JARVIS_ALLOW_RESEARCH_NETWORK"):
        monkeypatch.setenv(name, "false")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    database = Database(f"sqlite:///{tmp_path / 'integration-v13.sqlite'}")
    with database.transaction() as tx:
        for role in ("admin", "operator", "viewer"):
            auth.create_user(tx, role + "@example.com", PASSWORD, role, actor=ADMIN, bootstrap=role == "admin")
    with TestClient(create_app(database, worker_enabled=False)) as client:
        yield client, database
    database.close()


def login(client, role="admin"):
    response = client.post("/api/auth/login", json={"email": role + "@example.com", "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def post(client, path, headers, payload):
    response = client.post(path, headers=headers, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_workspace_deep_links_auth_roles_and_default_no_activation(running):
    client, database = running
    for path in ("/", "/agents/workflows", "/missions/active", "/research/sources", "/deliverables/list",
                 "/finance/costs", "/settings/security", "/settings/logs"):
        assert client.get(path).status_code == 200
        assert "frame-ancestors 'none'" in client.get(path).headers["Content-Security-Policy"]
    assert client.get("/api/v1.3/overview").status_code == 401
    viewer = login(client, "viewer")
    state = client.get("/api/v1.3/overview")
    assert state.status_code == 200, state.text
    assert state.json()["version"] == "1.3.0" and state.json()["business_actions_enabled"] is False
    assert not state.json()["ai"]["effective_paid_enabled"]
    assert len(state.json()["agents"]["roles"]) == 6
    assert not state.json()["documents"]
    assert client.post("/api/missions", headers=viewer, json={"goal": REASON, "idempotency_key": "no-viewer-write"}).status_code == 403
    assert client.post("/api/budget/ai-limits", headers=viewer, json={}).status_code == 403
    operator = login(client, "operator")
    for path in ("/api/budget/ai-limits", "/api/agents/ceo/config", "/api/internal/schedules/weekly-report"):
        assert client.post(path, headers=operator, json={}).status_code == 403
    with database.transaction() as tx:
        assert tx.list("budget_entry") == tx.list("ai_trace") == []
        assert tx.get("upgrade", "jarvis-lite-v1.3")["data_reset"] is False


def test_business_goal_documents_six_agents_qa_deliverable_and_booked_cost(running, monkeypatch):
    client, database = running
    headers = login(client)
    post(client, "/api/research/sources/" + SOURCE, headers, {"name": "Syntetiskt marknadsunderlag",
        "kind": "upload", "url": "https://market.example.com/evidence", "approved": True, "license_reviewed": True,
        "legal_review_reference": REASON, "cost_evidence": "Syntetiskt kostnadsfritt underlag för regressionstest", "data_mode": "REAL"})
    payload = {"idempotency_key": "literal-source", "documents": [{"title": "Behov av intern AI-assistans",
        "content": EVIDENCE + " Priser och lönsamhet är inte verifierade i detta underlag.",
        "facts": [{"subject": "Syntetiska småföretag", "predicate": "behov", "value": "snabbare svar",
                   "kind": "fact", "evidence": EVIDENCE}]}]}
    imported = post(client, "/api/research/sources/" + SOURCE + "/documents", headers, payload)
    assert imported["accepted"] == 1
    assert post(client, "/api/research/sources/" + SOURCE + "/documents", headers, payload)["id"] == imported["id"]
    assert len(client.get("/api/research/documents").json()["items"]) == 1
    monkeypatch.setenv("JARVIS_ALLOW_PAID_AI", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-v13-no-real-provider-key")
    with database.transaction() as tx:
        budget.set_fx_rate(tx, currency="USD", sek_per_unit="12", source=REASON, observed_at=tx.now, actor=ADMIN)
        ai.configure(tx, {"enabled": True, "max_output_tokens": 4096, "reason": REASON}, ADMIN)
    requests = []

    def provider(request):
        assert str(request.url) == ai.ENDPOINT
        body = json.loads(request.content)
        wrapper = json.loads(body["messages"][-1]["content"])
        prompt = json.loads(wrapper["untrusted_content"])
        contract = body.get("response_format", {}).get("json_schema", {}).get("name", "")
        requests.append((contract, prompt))
        action = "recommend"
        if "roles" in prompt:
            # The previously rejected CEO plan alias + typed payload must work.
            action = "plan"
            content = {"tasks": [{"role": role, "objective": "Granska och bearbeta internt " + role + " underlag"}
                                 for role in engine.SPECIALISTS]}
        elif "required_section_keys" in prompt:
            action = "draft"
            content = {"title": "Affärsplan för intern AI-assistans", "sections": [
                {"key": key, "heading": key, "content": "Underlaget beskriver ett behov av snabbare svar. Lösningsförslaget är en hypotes som måste valideras innan kommersiell användning.",
                 "source_ids": [prompt["source_evidence"][0]["id"]]} for key in prompt["required_section_keys"]],
                "next_actions": ["Granska underlaget och validera de preliminära affärsantagandena internt"],
                "limitations": ["Ett syntetiskt dokument har analyserats. Marknadens storlek, betalningsvilja och verkliga leveranskostnader har inte verifierats."],
                "company_ids": []}
        elif "compliance_qa" in contract or "verdict:pass|revise|review" in prompt.get("task", ""):
            action = "classify"
            content = {"verdict": "pass", "findings": [], "summary": "Interna leverabler har källor och uttryckliga osäkerheter; inga externa handlingar godkänns.", "source_ids": [SOURCE]}
        elif "sales_strategy" in contract or "opportunities" in prompt.get("task", ""):
            content = {"summary": "Fem preliminära tillämpningar av det dokumenterade behovet för intern validering.",
                "opportunities": [{"title": name, "target_group": "Småföretag med inkommande kundförfrågningar",
                    "problem": "Svar på återkommande frågor kräver tid enligt underlaget", "solution": "Interna AI-utkast med mänsklig granskning",
                    "price_estimate_minor": 500000, "cost_estimate_minor": 100000, "competition": "Konkurrenternas faktiska erbjudanden är inte verifierade",
                    "risks": ["Dataskydd, kvalitet och betalningsvilja måste valideras"], "source_ids": [SOURCE],
                    "next_steps": ["Granska och pröva hypotesen internt"], "assumptions": ["Pris och kostnad är obevisade estimat, inte intäkt"]}
                    for name in ("Svarsutkast", "Klassificering", "Sammanfattning", "Prioritering", "Kunskapsunderlag")]}
        elif "research_report" in contract or "documents" in prompt or "candidates" in prompt:
            content = {"summary": "Källan beskriver ett behov av snabbare svar; inga företag eller marknadsstorlekar har uppfunnits.",
                "candidates": [], "findings": [{"claim": EVIDENCE, "source_ids": [SOURCE], "kind": "fact"}],
                "uncertainties": ["Underlaget är begränsat och ger ingen verifierad efterfrågan eller betalningsvilja"]}
        else:
            content = "Sammanställningen gäller enbart faktisk intern bearbetning av ett syntetiskt källunderlag. Kostnader framgår av den bokförda huvudboken. Preliminära prisidéer är inte intäkter. Inga kunder har kontaktats."
        return httpx.Response(200, json={"id": "v13-mock-" + str(len(requests)), "usage": {
            "cost": "0.001", "prompt_tokens": 100, "completion_tokens": 70}, "choices": [{"finish_reason": "stop",
                "message": {"content": json.dumps({"action": action, "text": content, "reason": "Syntetiskt offline-test av giltigt internt uppdrag"})}}]})

    original_adapter = ai.OpenRouterAdapter

    class OfflineAdapter(original_adapter):
        def __init__(self, key):
            super().__init__(key, transport=httpx.MockTransport(provider))

    monkeypatch.setattr(ai, "OpenRouterAdapter", OfflineAdapter)
    body = {"idempotency_key": "business-goal-v13", "goal": "Analysera godkänt marknadsunderlag, identifiera fem interna affärsmöjligheter och skapa en konkret affärsplan med risker och preliminär ekonomi.",
            "source_ids": [SOURCE], "deliverable_kind": "business_plan", "max_cost_minor": 2000,
            "max_steps": 9, "max_retries": 1, "max_revisions": 1, "deadline_seconds": 3600}
    mission = post(client, "/api/missions", headers, body)
    assert post(client, "/api/missions", headers, body)["id"] == mission["id"]
    for _ in range(9):
        result = post(client, "/api/jobs/run", headers, {})
        if result["job"] is None:
            break
        assert result["job"]["status"] == "completed", result
    state = client.get("/api/v1.3/overview").json()
    assert len(requests) == 7 and len(state["ai_traces"]) == 7
    assert len(state["agents"]["tasks"]) == 7
    assert {task["role"] for task in state["agents"]["tasks"]} == set(engine.ROLE_TOOLS)
    assert all(task["status"] == "COMPLETED" for task in state["agents"]["tasks"])
    assert state["agents"]["missions"][0]["status"] == "REVIEW_REQUIRED"
    assert len(state["reports"]["opportunities"][0]["opportunities"]) == 5
    assert len(state["services"]["results"]) == 1
    artifact = state["services"]["results"][0]
    assert artifact["mission_id"] == mission["id"] and artifact["simulated"] is False
    assert client.get("/api/deliverables/" + artifact["id"]).status_code == 200
    assert client.get("/api/missions/" + mission["id"]).status_code == 200
    post(client, f"/api/factory/results/{artifact['id']}/review", headers, {"decision": "approve", "reason": REASON})
    action = state["agents"]["proposals"][0]
    post(client, f"/api/agents/actions/{action['id']}/review", headers, {"decision": "approve", "reason": REASON})
    final = client.get("/api/v1.3/overview").json()
    assert final["agents"]["missions"][0]["status"] == "COMPLETED"
    assert final["metrics"]["verified_revenue_minor"] == 0
    assert final["metrics"]["actual_cost_minor"] == 84
    with database.transaction() as tx:
        assert tx.list("email_message") == tx.list("payment") == []
        assert all(entry["state"] == "SETTLED" for entry in tx.list("budget_entry"))


def test_configured_job_attempt_limit_survives_worker_claim(running):
    _, database = running
    with database.transaction() as tx:
        job = jobs.enqueue(tx, "daily_report", {}, idempotency_key="one-attempt-only", actor=ADMIN)
        tx.put("job", job["id"], {**job, "max_attempts": 1, "attempts": 1})
    assert jobs.claim(database) is None
    with database.transaction() as tx:
        assert tx.get("job", job["id"])["status"] == "failed"


def test_http_retry_of_persisted_v12_mission_keeps_original_request_identity(running):
    client, database = running
    headers = login(client)
    goal = "Prepare the existing internally reviewed business mission"
    legacy_request = {"goal": goal, "source_ids": [], "lead_ids": [], "deliverable_kind": "market_analysis",
        "max_cost_minor": 2000, "priority": 50, "refresh_sources": False}
    with database.transaction() as tx:
        original = tx.create("business_mission", {**legacy_request, "status": "PAUSED", "requested_by": ADMIN["id"],
            "plan_version": 0, "task_limit": 7, "contact_authorized": False, "payment_authorized": False})
        tx.idempotency_put("engine.mission", "old-http-request", legacy_request, {"mission_id": original["id"]})
    replay = post(client, "/api/missions", headers, {"goal": goal, "idempotency_key": "old-http-request"})
    assert replay == original and "workflow_version" not in replay
    changed = client.post("/api/missions", headers=headers, json={"goal": goal + " with changed scope", "idempotency_key": "old-http-request"})
    assert changed.status_code == 409 and changed.json()["error"] == "idempotency_conflict"
    with database.transaction() as tx:
        assert len(tx.list("business_mission")) == 1 and tx.list("job") == []


def test_budget_limits_csrf_and_release_lock_are_server_owned(running, monkeypatch):
    client, database = running
    headers = login(client)
    assert client.post("/api/budget/ai-limits", json={}).status_code == 403
    post(client, "/api/budget/ai-limits", headers, {"daily_limit_minor": 100, "monthly_limit_minor": 200, "reason": REASON})
    assert client.get("/api/finance").status_code == 200
    for name in ("JARVIS_ALLOW_LIVE", "JARVIS_ALLOW_LIVE_CONTACT", "JARVIS_ALLOW_LIVE_PAYMENTS"):
        monkeypatch.setenv(name, "true")
    with database.transaction() as tx:
        tx.put("settings", "global", {**policy.get_settings(tx), "mode": "LIVE", "live_approved": True, "legal_reviewed": True})
        for action in ("contact", "payment_request", "external_write"):
            with pytest.raises(policy.PolicyError) as blocked:
                policy.guard(tx, action, actor=ADMIN)
            assert blocked.value.code == "internal_only_release"
