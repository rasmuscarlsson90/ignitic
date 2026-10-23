"""Authenticated durable v1.2 work through the real AI adapter, offline HTTP only."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from jarvis_lite import ai, auth, budget, engine, jobs, policy, research, workflow
from jarvis_lite.app import create_app
from jarvis_lite.store import Database, Transaction


ADMIN = {"id": "v12-integration-admin", "role": "admin"}
PASSWORD = "Synthetic-v12-integration-password-42"
REASON = "Reviewed synthetic internal work and test-only budget bounds"


@pytest.fixture
def running(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_ENV", "test")
    monkeypatch.setenv("JARVIS_SESSION_SECRET", "v12-integration-test-only-session-key")
    monkeypatch.delenv("JARVIS_ENCRYPTION_KEY", raising=False)
    for name in ("JARVIS_ALLOW_LIVE", "JARVIS_ALLOW_LIVE_CONTACT", "JARVIS_ALLOW_LIVE_PAYMENTS",
                 "JARVIS_ALLOW_PAID_AI", "JARVIS_ALLOW_RESEARCH_NETWORK"):
        monkeypatch.setenv(name, "false")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    db = Database(f"sqlite:///{tmp_path / 'v12-integration.db'}")
    with db.transaction() as tx:
        for role in ("admin", "operator", "viewer"):
            auth.create_user(tx, role + "@example.com", PASSWORD, role, actor=ADMIN, bootstrap=role == "admin")
    with TestClient(create_app(db, worker_enabled=False)) as client:
        yield client, db
    db.close()


def login(client, role="admin"):
    response = client.post("/api/auth/login", json={"email": role + "@example.com", "password": PASSWORD})
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def test_readiness_roles_empty_queue_and_blocked_work_are_truthful(running):
    client, db = running
    assert client.get("/api/v1.2/overview").status_code == 401
    viewer = login(client, "viewer")
    assert client.get("/agents").status_code == 200
    assert client.get("/api/v1.2/overview").json()["business_actions_enabled"] is False
    assert client.post("/api/missions", headers=viewer, json={"goal": REASON, "idempotency_key": "viewer"}).status_code == 403
    operator = login(client, "operator")
    for path in ("/api/ai/activation", "/api/agents/ceo/config", "/api/internal/schedules/internal-report"):
        assert client.post(path, headers=operator, json={}).status_code == 403
    assert client.post("/api/ai/test", headers=operator, json={"idempotency_key": "operator-test"}).status_code == 403
    assert client.post("/api/jobs/run", headers=operator).json()["job"] is None
    body = {"goal": "Prepare a source-based internal business review", "idempotency_key": "blocked-mission"}
    assert client.post("/api/missions", json=body).status_code == 403
    created = client.post("/api/missions", headers=operator, json=body)
    assert created.status_code == 200, created.text
    assert client.post("/api/missions", headers=operator, json=body).json()["id"] == created.json()["id"]
    job = client.post("/api/jobs/run", headers=operator).json()["job"]
    assert job["status"] == "paused"
    overview = client.get("/api/v1.2/overview").json()
    assert not overview["ai"]["effective_paid_enabled"]
    assert overview["agents"]["missions"][0]["status"] == "PAUSED"
    with db.transaction() as tx:
        assert not tx.list("ai_trace") and not tx.list("budget_entry") and not tx.list("business_plan")


def test_all_roles_real_adapter_durable_jobs_sources_and_admin_output_review(running, monkeypatch):
    client, db = running
    monkeypatch.setenv("JARVIS_ALLOW_PAID_AI", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-key-no-real-provider")
    with db.transaction() as tx:
        budget.set_fx_rate(tx, currency="USD", sek_per_unit="12", source=REASON, observed_at=tx.now, actor=ADMIN)
        ai.configure(tx, {"enabled": False, "max_output_tokens": 4096, "reason": REASON}, ADMIN)
        research.configure_source(tx, "synthetic-directory", {"name": "Synthetic licensed directory", "kind": "upload",
            "url": "https://directory.example.com/companies", "approved": True, "license_reviewed": True,
            "legal_review_reference": REASON, "cost_evidence": "Synthetic free licensed upload", "data_mode": "REAL"}, ADMIN)
        research.ingest(tx, "synthetic-directory", [{"company": "Synthetic Enquiry AB", "email": "info@synthetic.example.com",
            "evidence": "Published company material describes incoming support enquiries.", "industry": "Services",
            "geography": "Stockholm", "entity_type": "legal_person", "address_type": "generic"}], ADMIN, "synthetic-import")
    calls = []

    def provider(request):
        assert str(request.url) == ai.ENDPOINT
        data = json.loads(request.content)
        wrapped = json.loads(data["messages"][-1]["content"])
        prompt = json.loads(wrapped["untrusted_content"])
        calls.append(prompt)
        if "roles" in prompt:
            content = {"tasks": [{"role": role, "objective": "Prepare internally reviewed " + role + " evidence"}
                                 for role in engine.SPECIALISTS]}
        elif "candidates" in prompt:
            content = {"summary": "Only attributed synthetic records were ranked.", "candidates": [
                {"lead_id": item["lead_id"], "score": 88, "reason": "Published material describes incoming enquiries.",
                 "possible_need": "May benefit from enquiry reply drafts.", "suggested_service": workflow.SERVICE_ID,
                 "next_action": "Review the cited evidence internally"} for item in prompt["candidates"]]}
        elif "approved_services" in prompt:
            content = {"service_id": workflow.SERVICE_ID, "package_name": "Inbound enquiry assistant",
                "fit_reason": "The supplied source describes an enquiry workload.", "email_subject": "Internal enquiry draft",
                "email_body": "A source-based internal proposal draft requiring administrator review.",
                "follow_up_plan": ["Review evidence internally; customer contact is disabled"]}
        elif "required_section_keys" in prompt:
            content = {"title": "Internal cited market review", "sections": [
                {"key": key, "heading": key, "content": "Review the supplied company evidence and measure enquiry handling needs.",
                 "source_ids": [prompt["source_evidence"][0]["id"]]} for key in prompt["required_section_keys"]],
                "next_actions": ["Review the cited material internally"],
                "limitations": ["Only supplied synthetic evidence was analyzed; no website inspection occurred."],
                "company_ids": [item["id"] for item in prompt["known_companies"]]}
        elif "verdict:pass|revise|review" in prompt.get("task", ""):
            # New missions also validate actual post-delivery Compliance QA.
            content = {"verdict": "pass", "findings": [], "summary": "Actual supplied synthetic evidence and internal draft were reviewed.",
                       "source_ids": ["synthetic-directory"]}
        else:
            content = "Actual synthetic provider analysis of supplied internal facts; no authority is granted."
        return httpx.Response(200, json={"id": "synthetic-completion-" + str(len(calls)),
            "usage": {"cost": "0.001", "prompt_tokens": 100, "completion_tokens": 70},
            "choices": [{"message": {"content": json.dumps({"action": "draft" if "required_section_keys" in prompt else "recommend",
                "text": json.dumps(content) if isinstance(content, dict) else content, "reason": "Synthetic offline integration fixture"})}}]})

    original = ai.OpenRouterAdapter

    class OfflineAdapter(original):
        def __init__(self, key):
            super().__init__(key, transport=httpx.MockTransport(provider))

    monkeypatch.setattr(ai, "OpenRouterAdapter", OfflineAdapter)
    headers = login(client)
    state = client.get("/api/v1.2/overview").json()["ai"]
    assert {b["code"] for b in state["blockers"]} == {"ai_admin_disabled"}
    activated = client.post("/api/ai/activation", headers=headers, json={"statement": ai.ACTIVATE,
        "config_revision": state["config"]["revision"], "reason": REASON})
    assert activated.status_code == 200 and activated.json()["effective_paid_enabled"], activated.text
    created = client.post("/api/missions", headers=headers, json={"idempotency_key": "actual-offline-mission",
        "goal": "Analyze supplied company evidence for an internal incoming enquiry assistant offer",
        "source_ids": ["synthetic-directory"], "max_cost_minor": 2000, "priority": "high"})
    assert created.status_code == 200, created.text
    for _ in range(7):
        response = client.post("/api/jobs/run", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["job"]["status"] == "completed", response.text
    assert client.post("/api/jobs/run", headers=headers).json()["queue_empty"] is True
    overview = client.get("/api/v1.2/overview").json()
    assert len(calls) == 7 and len(overview["ai_traces"]) == 7
    assert {trace["role"] for trace in overview["ai_traces"]} == set(engine.ROLE_TOOLS)
    assert all(trace["mission_id"] == created.json()["id"] for trace in overview["ai_traces"])
    crm = client.get("/api/crm/leads?q=Enquiry&industry=Services&geography=Stockholm&sort=score")
    assert crm.status_code == 200, crm.text
    assert crm.json()["total"] == 1 and crm.json()["items"][0]["ai_score"] == 88
    result = overview["services"]["results"][0]
    assert result["status"] == "NEEDS_REVIEW" and result["version"] == 1
    reviewed = client.post(f"/api/factory/results/{result['id']}/review", headers=headers,
        json={"decision": "approve", "reason": REASON})
    assert reviewed.status_code == 200, reviewed.text
    exported = client.get(f"/api/factory/results/{result['id']}/export?format=markdown")
    assert exported.status_code == 200 and "Internal cited market review" in exported.text
    action = overview["agents"]["proposals"][0]
    assert client.post(f"/api/agents/actions/{action['id']}/review", headers=headers,
        json={"decision": "approve", "reason": REASON}).status_code == 200
    with db.transaction() as tx:
        assert tx.get("business_mission", created.json()["id"])["status"] == "COMPLETED"
        # Each fractional USD cent is reserved/settled conservatively as one cent.
        assert budget.summary(tx)["actual_sek_minor"] == 84
        assert all(entry["state"] == "SETTLED" for entry in tx.list("budget_entry"))
        assert tx.list("email_message") == tx.list("payment") == []


@pytest.mark.parametrize("action", ["contact", "email", "send_email", "proposal", "external_write", "payment_request"])
def test_release_lock_cannot_be_lifted_by_old_runtime_permissions(running, monkeypatch, action):
    _, db = running
    for name in ("JARVIS_ALLOW_LIVE", "JARVIS_ALLOW_LIVE_CONTACT", "JARVIS_ALLOW_LIVE_PAYMENTS"):
        monkeypatch.setenv(name, "true")
    with db.transaction() as tx:
        settings = policy.get_settings(tx)
        tx.put("settings", "global", {**settings, "mode": "LIVE", "live_approved": True, "legal_reviewed": True})
        with pytest.raises(policy.PolicyError) as blocked:
            policy.guard(tx, action, actor=ADMIN)
        assert blocked.value.code == "internal_only_release"
        assert tx.list("external_intent") == tx.list("email_message") == tx.list("payment") == []


def test_live_approval_endpoint_remains_release_locked(running, monkeypatch):
    client, _ = running
    monkeypatch.setenv("JARVIS_ALLOW_LIVE", "true")
    response = client.post("/api/live/approve", headers=login(client), json={"statement": "I APPROVE LIVE BUSINESS ACTIONS",
        "legal_review_reference": REASON, "reason": REASON})
    assert response.status_code == 403 and response.json()["error"] == "internal_only_release"


@pytest.mark.parametrize("stop", ["kill", "epoch", "lease_exhausted", "pause", "cancel"])
def test_job_controls_update_persisted_agent_and_mission_status(running, stop):
    client, db = running
    headers = login(client)
    created = client.post("/api/missions", headers=headers, json={"idempotency_key": "stopped-mission",
        "goal": "Prepare a bounded internal business review"}).json()
    with db.transaction() as tx:
        job = tx.list("job")[0]
        if stop == "kill":
            policy.set_kill_switch(tx, True, REASON, ADMIN)
        elif stop == "epoch":
            settings = policy.get_settings(tx)
            tx.put("settings", "global", {**settings, "safety_epoch": settings["safety_epoch"] + 1})
        elif stop == "lease_exhausted":
            tx.put("job", job["id"], {**job, "attempts": jobs.MAX_ATTEMPTS})
    if stop in {"epoch", "lease_exhausted"}:
        assert jobs.claim(db) is None
    elif stop in {"pause", "cancel"}:
        path = f"/api/jobs/{job['id']}/pause" if stop == "pause" else f"/api/internal/jobs/{job['id']}/cancel"
        response = client.post(path, headers=headers, json={"reason": REASON})
        assert response.status_code == 200, response.text
    with db.transaction() as tx:
        task = tx.list("agent_task")[0]
        assert task["status"] == ("CANCELLED" if stop == "cancel" else "FAILED" if stop == "lease_exhausted" else "PAUSED")
        assert tx.get("business_mission", created["id"])["status"] == ("CANCELLED" if stop == "cancel" else "FAILED" if stop == "lease_exhausted" else "PAUSED")
        assert tx.list("ai_trace") == []


@pytest.mark.parametrize("control", ["cancel", "pause_mission"])
def test_stop_during_durable_ai_handoff_cannot_be_overwritten(running, monkeypatch, control):
    _, db = running
    monkeypatch.setenv("JARVIS_ALLOW_PAID_AI", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-cancel-fence-key")
    with db.transaction() as tx:
        budget.set_fx_rate(tx, currency="USD", sek_per_unit="12", source=REASON, observed_at=tx.now, actor=ADMIN)
        ai.configure(tx, {"enabled": True, "reason": REASON}, ADMIN)
        created = engine.create_mission(tx, {"goal": "Prepare a bounded internal business review"}, ADMIN, "checkpoint-mission")
    original_checkpoint = Transaction.checkpoint
    interrupted = []

    def checkpoint(tx):
        if interrupted or not tx.execution_fence:
            return original_checkpoint(tx)
        original_commit = tx.conn.commit

        def commit_then_stop():
            original_commit()
            job_id, _ = tx.execution_fence
            with db.transaction() as review:
                if control == "cancel":
                    jobs.cancel(review, job_id, actor=ADMIN, reason=REASON)
                else:
                    engine.pause_mission(review, created["id"], ADMIN, REASON)
            interrupted.append(job_id)

        with monkeypatch.context() as race:
            race.setattr(tx.conn, "commit", commit_then_stop)
            return original_checkpoint(tx)

    monkeypatch.setattr(Transaction, "checkpoint", checkpoint)
    original_adapter = ai.OpenRouterAdapter

    class NeverCalledAdapter(original_adapter):
        def __init__(self, key):
            super().__init__(key, transport=httpx.MockTransport(lambda request: pytest.fail("Stopped work reached provider")))

    monkeypatch.setattr(ai, "OpenRouterAdapter", NeverCalledAdapter)
    result = jobs.run_once(db)
    assert interrupted and result["status"] == ("cancelled" if control == "cancel" else "paused")
    with db.transaction() as tx:
        assert tx.get("business_mission", created["id"])["status"] == ("CANCELLED" if control == "cancel" else "PAUSED")
        assert tx.list("agent_task")[0]["status"] == ("CANCELLED" if control == "cancel" else "PAUSED")
        assert tx.list("business_plan") == [] and len(tx.list("job")) == 1
        # A definitive pre-dispatch stop releases its liability even while
        # preserving the administrator's mission/task state across checkpoint.
        assert tx.list("budget_entry")[0]["state"] == "RELEASED"
        assert tx.list("ai_trace")[0]["status"] == "BLOCKED"
