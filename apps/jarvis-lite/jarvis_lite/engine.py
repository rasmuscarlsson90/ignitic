"""Durable internal agent delegation. Model output never grants authority.

Every executable tool and dependency is selected by this module. A model may
propose objectives for six bounded roles, never executable code, tools, contact,
payments, prices or new infrastructure. Failed/unknown calls retain their stable
request identity and require review rather than obtaining a fresh spend key.
"""
from __future__ import annotations

import hashlib
import json

from . import ai, budget, jobs, policy
from .policy import PolicyError
from .store import fingerprint


ROLE_TOOLS = {
    "ceo": frozenset({"ai", "plan", "delegate", "summarize"}),
    "lead_research": frozenset({"ai", "approved_research"}),
    "sales": frozenset({"ai", "sales_draft"}),
    "delivery": frozenset({"ai", "service_factory"}),
    "finance": frozenset({"ai", "budget_read"}),
    "compliance": frozenset({"ai", "policy_review"}),
}
SPECIALISTS = tuple(role for role in ROLE_TOOLS if role != "ceo")
DELIVERABLES = frozenset({"market_analysis", "seo_analysis", "content_calendar",
                          "marketing_strategy", "prospecting_report"})
TERMINAL = frozenset({"COMPLETED", "BLOCKED", "UNCERTAIN", "FAILED"})
SCHEDULE_FIELDS = ("schedule_id", "schedule_revision", "schedule_run_id")
MAX_TASKS = 7


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _role(role):
    if not isinstance(role, str) or role not in ROLE_TOOLS:
        raise PolicyError("agent_role_invalid", "Select one of the six approved internal roles", 422)
    return role


def _config(tx, role):
    _role(role)
    return {"role": role, "enabled": True, "priority": 50, "per_call_limit_minor": 200,
            "daily_budget_minor": 10000, "max_calls_per_day": 30,
            "tools": sorted(ROLE_TOOLS[role]),
            **(tx.get("agent_config", role) or {})}


def _allowed(tx, role, tool):
    config = _config(tx, role)
    if not config["enabled"]:
        raise PolicyError("agent_paused", "This internal agent is paused", 423)
    if tool not in ROLE_TOOLS[role] or tool not in config["tools"]:
        raise PolicyError("agent_tool_denied", "The agent does not have this internal capability", 403)
    return config


def _amount(entry):
    if entry.get("state") == "RESERVED":
        return max(0, int(entry.get("reserved_sek_minor", 0)))
    if entry.get("state") == "SETTLED":
        return max(0, int(entry.get("actual_sek_minor", 0)))
    return 0


def _mission_cost(tx, mission_id):
    prefix = "mission:" + mission_id + ":"
    return sum(_amount(entry) for entry in tx.list("budget_entry")
               if entry.get("mission_id") == mission_id
               or str(entry.get("workflow_id", "")).startswith(prefix))


def guard_ai_budget(tx, role, estimate, missionid=None, *, reservation_active=False):
    """Additional serialized caps used by every real AI call, including direct APIs.

    Pending liabilities never age out. Before dispatch the current reservation is
    already in the ledger: the second check adds zero, avoiding double charging.
    Global financial mandate checks remain in budget.reserve/settle.
    """
    config = _allowed(tx, role, "ai")
    if not _integer(estimate, 0, 200000):
        raise PolicyError("agent_budget_invalid", "A nonnegative integer SEK-minor reserve is required", 422)
    if estimate > config["per_call_limit_minor"]:
        raise PolicyError("agent_call_budget", "The AI reservation exceeds this agent's per-call limit")
    context = getattr(tx, "agent_context", None) or {}
    if context and (context.get("role") != role or context.get("mission_id") != missionid):
        raise PolicyError("agent_context_invalid", "Agent role and mission authority do not match", 403)
    traces = tx.list("ai_trace")
    role_traces = [trace for trace in traces if trace.get("role") == role and not trace.get("simulated")]
    reservations = {trace.get("reservation_id") for trace in role_traces}
    entries = [entry for entry in tx.list("budget_entry")
               if entry.get("id") in reservations or entry.get("agent_role") == role or entry.get("role") == role]
    daily = sum(_amount(entry) for entry in entries
                if entry.get("state") == "RESERVED" or entry.get("created_at", 0) >= tx.now - 86400)
    addition = 0 if reservation_active else estimate
    if daily + addition > config["daily_budget_minor"]:
        raise PolicyError("agent_daily_budget", "The agent's daily budget includes all pending liabilities")
    calls = sum(1 for trace in role_traces if trace.get("created_at", 0) >= tx.now - 86400
                or trace.get("status") in {"ATTEMPTED", "UNKNOWN"})
    if calls + (0 if reservation_active else 1) > config["max_calls_per_day"]:
        raise PolicyError("agent_daily_calls", "The bounded daily AI call count is exhausted")
    if missionid:
        mission = tx.get("business_mission", missionid)
        if not mission or mission.get("status") in {"PAUSED", "CANCELLED", "UNCERTAIN", "COMPLETED"}:
            raise PolicyError("mission_not_active", "The mission cannot authorize paid work", 423)
        if _mission_cost(tx, missionid) + addition > mission["max_cost_minor"]:
            raise PolicyError("mission_budget", "Actual costs and pending reservations exhaust the mission budget")


def configure_agent(tx, role, config, actor):
    policy.require_admin(actor)
    _role(role)
    fields = {"enabled", "priority", "per_call_limit_minor", "daily_budget_minor", "max_calls_per_day", "tools"}
    if not isinstance(config, dict) or set(config) - (fields | {"reason"}):
        raise PolicyError("agent_config_invalid", "Only supported agent controls may be configured", 422)
    reason = config.get("reason", "")
    if not isinstance(reason, str) or not 12 <= len(reason.strip()) <= 500:
        raise PolicyError("agent_review_required", "Document the administrator's capability/budget review", 422)
    previous = _config(tx, role)
    value = {name: config.get(name, previous[name]) for name in fields}
    if (type(value["enabled"]) is not bool or not _integer(value["priority"], 0, 100)
            or not _integer(value["per_call_limit_minor"], 1, 50000)
            or not _integer(value["daily_budget_minor"], 0, 50000)
            or not _integer(value["max_calls_per_day"], 1, 100)
            or not isinstance(value["tools"], list)
            or any(not isinstance(tool, str) or tool not in ROLE_TOOLS[role] for tool in value["tools"])
            or len(set(value["tools"])) != len(value["tools"])):
        raise PolicyError("agent_config_invalid", "Use bounded budgets and a subset of this role's internal tools", 422)
    value.update(role=role, revision=fingerprint(value), reviewed_at=tx.now,
                 reviewed_by=policy.actor_id(actor))
    tx.put("agent_config", role, value)
    # Revoke running leases even when only a budget/tool changed. Enabling an
    # agent never implicitly resumes a previously paused job.
    for job in tx.list("job"):
        if job.get("kind") == "internal_agent" and job.get("payload", {}).get("role") == role and job.get("status") in jobs.ACTIVE:
            jobs.pause(tx, job["id"], actor=actor, reason="Agent capability or budget configuration changed")
    tx.audit(actor, "agent.configured", {"role": role, "config": value, "reason": reason})
    return value


def pause_agent(tx, role, actor, reason="Administrator paused this internal agent"):
    return configure_agent(tx, role, {"enabled": False, "reason": reason}, actor)


def _ids(value, name, maximum=10):
    if (not isinstance(value, list) or len(value) > maximum
            or any(not isinstance(item, str) or not 1 <= len(item) <= 160 for item in value)
            or len(set(value)) != len(value)):
        raise PolicyError("mission_invalid", name + " must be a bounded list of unique record IDs", 422)
    return value


def _save(tx, kind, record):
    record["updated_at"] = tx.now
    return tx.put(kind, record["id"], record)


def _enqueue(tx, mission, task):
    payload = {"mission_id": mission["id"], "task_id": task["id"], "role": task["role"],
               **{field: mission[field] for field in SCHEDULE_FIELDS if field in mission}}
    job = jobs.enqueue(tx, "internal_agent", payload, idempotency_key="agent:" + task["id"],
                       actor=mission["requested_by"])
    job["priority"] = min(mission["priority"], _config(tx, task["role"])["priority"])
    tx.put("job", job["id"], job)
    task["job_id"] = job["id"]
    return _save(tx, "agent_task", task)


def _task_id(mission_id, step):
    return hashlib.sha256((mission_id + ":" + step).encode()).hexdigest()


def _new_task(tx, mission, role, step, objective, dependencies):
    task_id = _task_id(mission["id"], step)
    return tx.get("agent_task", task_id) or tx.create("agent_task", {
        "mission_id": mission["id"], "role": role, "step": step, "objective": objective,
        "dependencies": dependencies, "status": "QUEUED", "job_id": None,
        "output": None, "error_code": None, "updated_at": tx.now,
    }, id=task_id)


def create_mission(tx, payload, actor, key, *, schedule_context=None):
    if not isinstance(actor, dict) or actor.get("role") not in {"admin", "operator"}:
        raise PolicyError("operator_required", "An operator must request an internal mission", 403)
    fields = {"goal", "source_ids", "lead_ids", "deliverable_kind", "max_cost_minor", "priority", "refresh_sources"}
    if not isinstance(payload, dict) or set(payload) - fields:
        raise PolicyError("mission_invalid", "Only supported internal mission fields are accepted", 422)
    goal = payload.get("goal")
    if not isinstance(goal, str) or not 12 <= len(goal.strip()) <= 4000:
        raise PolicyError("mission_invalid", "Provide a concrete 12–4000 character internal goal", 422)
    value = {"goal": goal.strip(), "source_ids": _ids(payload.get("source_ids", []), "source_ids"),
             "lead_ids": _ids(payload.get("lead_ids", []), "lead_ids"),
             "deliverable_kind": payload.get("deliverable_kind", "market_analysis"),
             "max_cost_minor": payload.get("max_cost_minor", 2000),
             "priority": payload.get("priority", 50), "refresh_sources": payload.get("refresh_sources", False)}
    if (not isinstance(value["deliverable_kind"], str) or value["deliverable_kind"] not in DELIVERABLES or not _integer(value["max_cost_minor"], 1, 50000)
            or not _integer(value["priority"], 0, 100) or type(value["refresh_sources"]) is not bool):
        raise PolicyError("mission_invalid", "Choose an approved deliverable and bounded internal mission budget", 422)
    context = schedule_context or {}
    if not isinstance(context, dict) or set(context) - set(SCHEDULE_FIELDS):
        raise PolicyError("mission_schedule_invalid", "Invalid trusted scheduling context", 422)
    request = {**value, **context}
    previous = tx.idempotency_get("engine.mission", key, request)
    if previous:
        return tx.get("business_mission", previous["mission_id"])
    policy.guard(tx, "job", actor=actor)
    _allowed(tx, "ceo", "delegate")
    limits = budget.summary(tx)
    if value["max_cost_minor"] > min(limits["remaining_minor"]["total"], limits["remaining_minor"]["tools_30d"]):
        raise PolicyError("mission_budget", "Mission cost ceiling must fit the remaining financial mandate")
    mission = tx.create("business_mission", {**value, **context, "status": "QUEUED", "data_mode": "REAL",
        "requested_by": policy.actor_id(actor), "plan_version": 0, "task_limit": MAX_TASKS,
        "contact_authorized": False, "payment_authorized": False, "updated_at": tx.now})
    task = _new_task(tx, mission, "ceo", "plan", "Prepare a bounded evidence-based internal plan", [])
    _enqueue(tx, mission, task)
    tx.idempotency_put("engine.mission", key, request, {"mission_id": mission["id"]})
    tx.audit(actor, "mission.created", {"mission_id": mission["id"], "max_cost_minor": mission["max_cost_minor"]})
    return mission


def _real_ai(tx, mission, task, prompt):
    state = ai.status(tx)
    if not state.get("effective_paid_enabled"):
        raise PolicyError("ai_not_ready", state.get("reason") or "Real reviewed paid AI is not enabled", 423)
    if len(prompt) > state.get("config", {}).get("max_input_chars", 12000):
        return {"status": "BLOCKED", "code": "agent_input_limit",
                "reason": "The actual evidence exceeds the reviewed model input limit; narrow the mission or review model bounds"}
    result = ai.task(tx, role=task["role"], text=prompt,
                     workflow_id="mission:" + mission["id"] + ":" + task["role"],
                     idempotency_key="agent:" + task["id"],
                     actor={"id": "agent:" + task["role"], "role": "operator"})
    if result.get("status") in {"UNKNOWN", "UNCERTAIN", "BLOCKED", "REJECTED", "REVIEW_REQUIRED"}:
        return result
    if result.get("simulated") is not False or result.get("provider") != "openrouter" or result.get("status") != "COMPLETED":
        return {"status": "BLOCKED", "code": "real_ai_required", "reason": "Simulated output cannot satisfy internal agent work"}
    return result


def _plan(tx, mission, task):
    _allowed(tx, "ceo", "plan")
    result = _real_ai(tx, mission, task, json.dumps({"goal": mission["goal"],
        "task": "Return outer action=recommend and text containing JSON {tasks:[{role,objective}]}. Include exactly these five distinct specialist roles. Objectives are internal preparation only; no contact, purchases, deployment, payment or invented evidence.",
        "roles": list(SPECIALISTS), "source_ids": mission["source_ids"],
        "deliverable_kind": mission["deliverable_kind"], "mission_budget_sek_minor": mission["max_cost_minor"]}))
    if result.get("status") != "COMPLETED":
        return result
    try:
        plan = json.loads(result["output"]["text"])
        tasks = plan["tasks"]
        if (not isinstance(plan, dict) or set(plan) != {"tasks"} or not isinstance(tasks, list)
                or len(tasks) != len(SPECIALISTS)
                or any(not isinstance(item, dict) or set(item) != {"role", "objective"}
                       or item["role"] not in SPECIALISTS or not isinstance(item["objective"], str)
                       or not 8 <= len(item["objective"]) <= 500 for item in tasks)
                or {item["role"] for item in tasks} != set(SPECIALISTS)):
            raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError):
        return {"status": "REVIEW_REQUIRED", "code": "agent_plan_invalid", "ai_result": result,
                "reason": "The actual model plan did not meet the bounded delegation schema"}
    mission["plan_version"] += 1
    saved = tx.create("business_plan", {"mission_id": mission["id"], "version": mission["plan_version"],
        "tasks": tasks, "ai_result_id": result["id"], "trace_id": result.get("trace_id"),
        "status": "INTERNAL_ONLY", "data_mode": "REAL"})
    mission["plan_id"] = saved["id"]
    ids = {role: _task_id(mission["id"], role) for role in SPECIALISTS}
    deps = {"lead_research": [task["id"]], "compliance": [ids["lead_research"]],
            "sales": [ids["compliance"]], "delivery": [ids["compliance"]],
            "finance": [ids["sales"], ids["delivery"]]}
    for item in tasks:
        _new_task(tx, mission, item["role"], item["role"], item["objective"], deps[item["role"]])
    _new_task(tx, mission, "ceo", "summary", "Summarize actual internal outputs, costs and unresolved risks", [ids["finance"]])
    return {"status": "COMPLETED", "plan_id": saved["id"], "trace_id": result.get("trace_id"), "ai_result_id": result["id"]}


def _output_reference(output):
    """Preserve report identities and a bounded excerpt; no generated new facts."""
    if not isinstance(output, dict):
        return None
    fields = ("id", "status", "plan_id", "report_id", "trace_id", "ai_result_id", "source_ids", "lead_ids", "version")
    value = {name: output[name] for name in fields if name in output}
    if isinstance(output.get("output"), dict):
        value["analysis_excerpt"] = str(output["output"].get("text", ""))[:900]
    elif isinstance(output.get("analysis"), str):
        value["analysis_excerpt"] = output["analysis"][:900]
    if output.get("offers"):
        value["offers"] = [{name: offer[name] for name in ("id", "status", "lead_id", "price") if name in offer}
                           for offer in output["offers"][:3]]
    if output.get("citations"):
        value["citations"] = [{name: citation[name] for name in ("source_id", "source_url") if name in citation}
                              for citation in output["citations"][:3]]
    return value


def _facts(tx, mission):
    return {"mission_id": mission["id"], "goal": mission["goal"], "source_ids": mission["source_ids"],
            "lead_ids": mission["lead_ids"], "actual_and_pending_cost_minor": _mission_cost(tx, mission["id"]),
            "tasks": [{"role": item["role"], "step": item["step"], "status": item["status"],
                       "output": _output_reference(item.get("output"))} for item in tx.list("agent_task")
                      if item["mission_id"] == mission["id"] and item["status"] == "COMPLETED"]}


def _handler(tx, mission, task):
    role = task["role"]
    actor = {"id": "agent:" + role, "role": "operator"}
    key = "agent:" + task["id"]
    if role == "ceo" and task["step"] == "plan":
        return _plan(tx, mission, task)
    if role == "lead_research":
        _allowed(tx, role, "approved_research")
        from . import intelligence
        report = intelligence.run_research(tx, mission, actor, key)
        if report.get("status") == "COMPLETED":
            mission["research_report_id"] = report["id"]
            mission["lead_ids"] = report.get("lead_ids", [])[:10]
        return report
    if role == "sales":
        _allowed(tx, role, "sales_draft")
        from . import intelligence
        if not mission["lead_ids"]:
            raise PolicyError("NO_RESEARCH_DATA", "Sales preparation requires actual cited leads")
        offers: list[dict] = []
        # One paid call per leased sales job. Provider retries are independently
        # bounded; multiple leads must not consume successive lease windows.
        for lead_id in mission["lead_ids"][:1]:
            offer = intelligence.prepare_sales(tx, lead_id, actor, key + ":" + str(len(offers)), mission_id=mission["id"])
            offers.append(offer)
            if offer.get("status") != "DRAFT":
                return {"status": offer.get("status", "REVIEW_REQUIRED"), "offers": offers}
        return {"status": "COMPLETED", "offers": offers, "external_contact": False,
                "deferred_lead_ids": mission["lead_ids"][1:]}
    if role == "delivery":
        _allowed(tx, role, "service_factory")
        from . import factory
        return factory.generate_for_mission(tx, mission, actor, key)
    facts = _facts(tx, mission)
    if role == "compliance":
        _allowed(tx, role, "policy_review")
        report = tx.get("research_report", mission.get("research_report_id", "")) or {}
        facts["research_report"] = _output_reference(report)
        facts["research_citation_evidence"] = [{"source_id": citation.get("source_id"),
            "source_url": citation.get("source_url"), "evidence": str(citation.get("evidence", ""))[:500]}
            for citation in report.get("citations", [])[:3]]
        instruction = "Identify evidence gaps, privacy risks and review requirements for internal drafts. No legal approval, contact permission or payment authority can be granted. Return actual evidence and unresolved limitations."
    elif role == "finance":
        _allowed(tx, role, "budget_read")
        summary = budget.summary(tx)
        facts["budget"] = {name: summary[name] for name in ("currency", "actual_sek_minor", "reserved_sek_minor", "usage", "limits_minor", "remaining_minor", "blocked_windows") if name in summary}
        facts["mandate"] = {"minimum_price_sek_minor": 500000, "maximum_discount_bps": 1000,
                            "tools_30d_ceiling_sek_minor": 50000, "startup_ceiling_sek_minor": 200000}
        facts["offer_economics"] = [{"offer_id": offer["id"], "net_offer_minor": offer["price"]["amount_minor"],
            "recorded_mission_cost_and_liability_minor": facts["actual_and_pending_cost_minor"],
            "preliminary_remainder_minor": offer["price"]["amount_minor"] - facts["actual_and_pending_cost_minor"],
            "basis": "Unaccepted net offer less recorded mission costs/liabilities only; excludes unrecorded labour, hosting and delivery costs. This is neither realized profit nor a verified full margin."}
            for offer in tx.list("sales_offer") if offer.get("mission_id") == mission["id"]
            and isinstance(offer.get("price"), dict) and type(offer["price"].get("amount_minor")) is int][:3]
        instruction = "Explain actual costs, pending liabilities, remaining budget and the preliminary offered margin using only the supplied ledger and explicit incomplete cost basis. Drafts/offers are not revenue; unknown costs prevent a verified full margin. Do not invent revenue, customers, payment confirmation or change the financial mandate."
    else:
        _allowed(tx, role, "summarize")
        instruction = "Summarize actual internal task outputs, costs, cited evidence and unresolved risks for administrator review. No production deployment, delivery or customer contact has occurred. Propose next internal steps only."
    result = _real_ai(tx, mission, task, json.dumps({"task": instruction, "objective": task["objective"], "facts": facts}, default=str))
    if role in {"finance", "compliance"} and result.get("status") == "COMPLETED":
        report = tx.create(role + "_report", {"mission_id": mission["id"], "status": "NEEDS_REVIEW",
            "data_mode": "REAL", "facts": facts, "analysis": result["output"],
            "trace_id": result.get("trace_id"), "ai_result_id": result["id"],
            "authorizes_external_actions": False})
        return {"status": "COMPLETED", "report_id": report["id"], "trace_id": result.get("trace_id")}
    return result


def _review(tx, mission, reason):
    action_id = "mission-review:" + mission["id"]
    return tx.get("agent_action", action_id) or tx.create("agent_action", {
        "mission_id": mission["id"], "type": "internal_output_review", "status": "PENDING",
        "reason": reason, "title": "Review actual internal agent outputs",
        "external_contact": False, "payment_action": False,
    }, id=action_id)


def _ready_tasks(tx, mission):
    tasks = {task["id"]: task for task in tx.list("agent_task") if task["mission_id"] == mission["id"]}
    if len(tasks) > MAX_TASKS:
        raise PolicyError("mission_graph_limit", "The durable task graph exceeded its hard limit")
    for task in tasks.values():
        if task["status"] == "QUEUED" and not task.get("job_id") and all(
                tasks[dep]["status"] == "COMPLETED" for dep in task["dependencies"]):
            _enqueue(tx, mission, task)


def execute_agent(tx, job):
    payload = job.get("payload", {})
    mission = tx.get("business_mission", payload.get("mission_id", ""))
    task = tx.get("agent_task", payload.get("task_id", ""))
    if (not mission or not task or task["mission_id"] != mission["id"]
            or task.get("job_id") != job["id"] or payload.get("role") != task["role"]):
        raise PolicyError("agent_job_invalid", "The job is not a persisted authorized agent task", 403)
    if task["status"] in TERMINAL:
        return {"status": "UNCERTAIN" if task["status"] == "UNCERTAIN" else task["status"],
                "task_id": task["id"], "output": task.get("output"), "idempotent": True}
    if mission["status"] not in {"QUEUED", "RUNNING"}:
        raise PolicyError("mission_not_active", "This mission requires explicit administrator review", 423)
    for dependency in task["dependencies"]:
        if (tx.get("agent_task", dependency) or {}).get("status") != "COMPLETED":
            raise PolicyError("agent_dependency_pending", "Persisted prerequisite work has not completed")
    policy.guard(tx, "job", actor={"id": "agent:" + task["role"], "role": "operator"})
    previous_context = getattr(tx, "agent_context", None)
    tx.agent_context = {"mission_id": mission["id"], "task_id": task["id"], "role": task["role"]}
    task.update(status="RUNNING", started_at=task.get("started_at", tx.now))
    mission["status"] = "RUNNING"
    _save(tx, "agent_task", task)
    _save(tx, "business_mission", mission)
    try:
        _allowed(tx, task["role"], "ai")
        # Never call downstream modules in disabled/demo AI mode: they must not
        # create a simulated business result that looks like completed work.
        state = ai.status(tx)
        if not state.get("effective_paid_enabled"):
            raise PolicyError("ai_not_ready", state.get("reason") or "Reviewed real AI is not enabled", 423)
        result = _handler(tx, mission, task)
    except PolicyError as error:
        result = {"status": "BLOCKED", "code": error.code, "reason": error.message}
    finally:
        tx.agent_context = previous_context
    # Provider checkpoints release the database lock. Preserve any competing
    # administrator stop instead of overwriting its mission/task state or
    # enqueueing delegation from the stale pre-checkpoint objects.
    policy.guard(tx, "job", actor={"id": "agent:" + task["role"], "role": "operator"})
    status = result.get("status", "REVIEW_REQUIRED")
    if status in {"UNKNOWN", "UNCERTAIN"}:
        task["status"] = mission["status"] = "UNCERTAIN"
    elif status in {"COMPLETED", "NEEDS_REVIEW"}:
        task["status"] = "COMPLETED"
        if task["step"] == "summary":
            mission["status"] = "REVIEW_REQUIRED"
            _review(tx, mission, "Review real generated drafts, citations, incurred costs and unresolved risks")
    else:
        task["status"] = "BLOCKED"
        mission["status"] = "REVIEW_REQUIRED" if status == "REVIEW_REQUIRED" else "BLOCKED"
        task["error_code"] = result.get("code", status)
        _review(tx, mission, result.get("reason", "Internal work requires review; no external action is authorized"))
    task.update(output=result, finished_at=tx.now)
    _save(tx, "agent_task", task)
    mission["actual_and_pending_cost_minor"] = _mission_cost(tx, mission["id"])
    _save(tx, "business_mission", mission)
    if task["status"] == "COMPLETED" and mission["status"] == "RUNNING":
        _ready_tasks(tx, mission)
    tx.audit("agent:" + task["role"], "agent.task_finished", {"mission_id": mission["id"],
             "task_id": task["id"], "role": task["role"], "status": task["status"]})
    return {"status": "UNCERTAIN" if task["status"] == "UNCERTAIN" else task["status"],
            "mission_id": mission["id"], "task_id": task["id"], "output": result,
            "code": task.get("error_code"), "reason": result.get("reason")}


def review_action(tx, action_id, decision, reason, actor):
    policy.require_admin(actor)
    if decision not in {"approve", "reject"} or not isinstance(reason, str) or not 12 <= len(reason.strip()) <= 1000:
        raise PolicyError("agent_review_invalid", "Approve or reject with a meaningful administrator review", 422)
    action = tx.get("agent_action", action_id)
    if not action:
        raise PolicyError("agent_action_not_found", "Internal review does not exist", 404)
    if action["status"] != "PENDING":
        raise PolicyError("agent_action_reviewed", "This internal output review already has a recorded decision")
    mission = tx.get("business_mission", action["mission_id"])
    completed = [task for task in tx.list("agent_task") if task["mission_id"] == mission["id"]]
    if decision == "approve" and (mission["status"] != "REVIEW_REQUIRED" or len(completed) != MAX_TASKS
                                  or any(task["status"] != "COMPLETED" for task in completed)):
        raise PolicyError("mission_incomplete", "Blocked or uncertain work cannot be marked successfully completed")
    action.update(status="APPROVED" if decision == "approve" else "REJECTED",
                  reviewed_by=policy.actor_id(actor), reviewed_at=tx.now, review_reason=reason.strip())
    _save(tx, "agent_action", action)
    if decision == "approve":
        mission["status"] = "COMPLETED"
        _save(tx, "business_mission", mission)
    tx.audit(actor, "agent.action_reviewed", {"action_id": action_id, "decision": decision,
             "reason": reason.strip(), "external_authority_granted": False})
    return action


def record_job_failure(tx, job, code):
    """Worker exception hook; called in its separate failure transaction."""
    task = tx.get("agent_task", job.get("payload", {}).get("task_id", ""))
    if not task or task.get("job_id") != job["id"] or task["status"] in {"COMPLETED", "UNCERTAIN"}:
        return
    mission = tx.get("business_mission", task["mission_id"])
    uncertain = job.get("status") == "uncertain" or code in {"UNKNOWN", "unknown_outcome", "provider_unknown"}
    retrying = job.get("status") == "queued" and not uncertain
    task.update(status="QUEUED" if retrying else "UNCERTAIN" if uncertain else "FAILED" if job.get("status") == "failed" else "BLOCKED",
                error_code=code, finished_at=tx.now)
    _save(tx, "agent_task", task)
    if mission and mission["status"] != "CANCELLED":
        mission["status"] = "RUNNING" if retrying else "UNCERTAIN" if uncertain else "BLOCKED"
        _save(tx, "business_mission", mission)
        if not retrying:
            _review(tx, mission, "Review interrupted internal work: " + str(code)[:200])


def record_job_cancel(tx, job):
    task = tx.get("agent_task", job.get("payload", {}).get("task_id", ""))
    if not task or task.get("job_id") != job["id"] or task["status"] == "UNCERTAIN":
        return
    task.update(status="FAILED", error_code="job_cancelled", finished_at=tx.now)
    _save(tx, "agent_task", task)
    mission = tx.get("business_mission", task["mission_id"])
    if mission:
        mission["status"] = "CANCELLED"
        _save(tx, "business_mission", mission)


def pause_mission(tx, mission_id, actor, reason="Administrator paused this internal mission"):
    policy.require_admin(actor)
    mission = tx.get("business_mission", mission_id)
    if not mission:
        raise PolicyError("mission_not_found", "Internal mission does not exist", 404)
    if mission["status"] in {"UNCERTAIN", "COMPLETED", "CANCELLED"}:
        raise PolicyError("mission_not_active", "Uncertain/completed/cancelled work cannot be paused")
    for job in tx.list("job"):
        if job.get("payload", {}).get("mission_id") == mission_id and job.get("status") in jobs.ACTIVE:
            jobs.pause(tx, job["id"], actor=actor, reason=reason)
    mission["status"] = "PAUSED"
    _save(tx, "business_mission", mission)
    tx.audit(actor, "mission.paused", {"mission_id": mission_id, "reason": reason[:500]})
    return mission


def resume_mission(tx, mission_id, actor, reason):
    """Resume the same requests after review; never mint replacement AI keys."""
    policy.require_admin(actor)
    if not isinstance(reason, str) or not 12 <= len(reason.strip()) <= 1000:
        raise PolicyError("mission_review_required", "Document why this same internal work may resume", 422)
    mission = tx.get("business_mission", mission_id)
    if not mission:
        raise PolicyError("mission_not_found", "Internal mission does not exist", 404)
    own = [task for task in tx.list("agent_task") if task["mission_id"] == mission_id]
    if (mission["status"] in {"UNCERTAIN", "COMPLETED", "CANCELLED", "REVIEW_REQUIRED"}
            or any(task["status"] == "UNCERTAIN" for task in own)):
        raise PolicyError("mission_reconciliation_required", "Unknown outcomes or finished output review cannot be bypassed by resuming")
    policy.guard(tx, "job", actor=actor)
    state = ai.status(tx)
    if not state.get("effective_paid_enabled"):
        raise PolicyError("ai_not_ready", state.get("reason") or "Reviewed paid AI is not ready", 423)
    resumed = 0
    for task in own:
        if task["status"] == "COMPLETED":
            continue
        _allowed(tx, task["role"], "ai")
        if not task.get("job_id"):
            continue
        job = tx.get("job", task["job_id"])
        if job and job["status"] == "uncertain":
            raise PolicyError("mission_reconciliation_required", "A provider outcome still requires reconciliation")
        if job and job["status"] in {"paused", "failed"}:
            # A completed/rejected actual provider result is deliberately not
            # retried with a replacement key. Administrative resume preserves
            # the original cache and therefore cannot incur a duplicate call.
            jobs.resume(tx, job["id"], actor=actor, reason=reason)
            task.update(status="QUEUED", error_code=None)
            _save(tx, "agent_task", task)
            resumed += 1
    mission.update(status="RUNNING", last_resume_reason=reason.strip(), last_resumed_at=tx.now)
    _save(tx, "business_mission", mission)
    _ready_tasks(tx, mission)
    tx.audit(actor, "mission.resumed", {"mission_id": mission_id, "reason": reason.strip(), "resumed_jobs": resumed})
    return mission


def agent_status(tx):
    tasks, all_jobs = tx.list("agent_task"), tx.list("job")
    roles = []
    for role in ROLE_TOOLS:
        own = [task for task in tasks if task["role"] == role]
        active = [job for job in all_jobs if job.get("kind") == "internal_agent"
                  and job.get("payload", {}).get("role") == role and job.get("status") in jobs.ACTIVE]
        roles.append({**_config(tx, role), "allowlisted_tools": sorted(ROLE_TOOLS[role]),
            "tasks": len(own), "completed": sum(task["status"] == "COMPLETED" for task in own),
            "blocked": sum(task["status"] in {"BLOCKED", "UNCERTAIN", "FAILED"} for task in own),
            "active_jobs": len(active), "last_task": own[-1] if own else None})
    missions = [{**mission, "actual_and_pending_cost_minor": _mission_cost(tx, mission["id"])}
                for mission in tx.list("business_mission")]
    return {"roles": roles, "missions": missions, "tasks": tasks,
            "proposals": tx.list("agent_action"), "internal_only": True}
