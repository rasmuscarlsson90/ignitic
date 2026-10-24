"""Real PostgreSQL row-lock regressions; never operate on the supplied database.

JARVIS_TEST_DATABASE_URL is a maintenance connection with CREATE DATABASE
permission. Each test creates and drops only its own random temporary database.
CI explicitly supplies this URL; a local SQLite-only run reports these skips.
"""

from concurrent.futures import ThreadPoolExecutor
import os
import secrets
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql
import pytest

from jarvis_lite import budget, jobs, policy
from jarvis_lite.policy import PolicyError
from jarvis_lite.store import Database


pytestmark = pytest.mark.skipif(
    not os.getenv("JARVIS_TEST_DATABASE_URL"),
    reason="Real PostgreSQL tests require JARVIS_TEST_DATABASE_URL with CREATE DATABASE permission",
)
ADMIN = {"id": "postgres-test-admin", "role": "admin"}


@pytest.fixture
def postgres_url():
    supplied = os.environ["JARVIS_TEST_DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    parsed = urlsplit(supplied)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
        pytest.fail("JARVIS_TEST_DATABASE_URL must be a PostgreSQL maintenance URL")
    name = "jarvis_lite_test_" + secrets.token_hex(10)
    with psycopg.connect(supplied, autocommit=True, connect_timeout=10) as maintenance:
        maintenance.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            yield urlunsplit((parsed.scheme, parsed.netloc, "/" + name, parsed.query, parsed.fragment))
        finally:
            maintenance.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def test_postgres_workers_claim_exactly_once_across_independent_connections(postgres_url):
    engines = [Database(postgres_url) for _ in range(12)]
    try:
        with engines[0].transaction() as tx:
            policy.initialize(tx)
            job = jobs.enqueue(tx, "daily_report", {}, idempotency_key="postgres-once", actor=ADMIN)
        with ThreadPoolExecutor(max_workers=len(engines)) as executor:
            results = list(executor.map(jobs.claim, engines))
        claimed = [item for item in results if item]
        assert len(claimed) == 1
        assert claimed[0]["id"] == job["id"]
        with engines[0].transaction() as tx:
            stored = tx.get("job", job["id"])
            assert stored["status"] == "leased"
            assert stored["attempts"] == 1
    finally:
        for engine in engines:
            engine.close()


def test_postgres_budget_reservations_cannot_race_past_daily_cap(postgres_url):
    engines = [Database(postgres_url) for _ in range(20)]
    try:
        with engines[0].transaction() as tx:
            policy.initialize(tx)
            settings = policy.get_settings(tx)
            settings["daily_budget_minor"] = 100
            tx.put("settings", "global", settings)

        def reserve(index):
            try:
                with engines[index].transaction() as tx:
                    budget.reserve(tx, vendor="postgres-fixture", workflow_id="concurrency",
                                   amount_minor=10, currency="SEK", category="ai",
                                   idempotency_key=f"concurrent-action-{index}", actor=ADMIN)
                return "reserved"
            except PolicyError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=len(engines)) as executor:
            outcomes = list(executor.map(reserve, range(len(engines))))
        assert outcomes.count("reserved") == 10
        assert outcomes.count("budget_exhausted") == 10
        with engines[0].transaction() as tx:
            assert budget.summary(tx)["usage"]["daily"] == 100
            assert len(tx.list("budget_entry")) == 10
    finally:
        for engine in engines:
            engine.close()
