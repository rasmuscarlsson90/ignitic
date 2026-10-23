"""Durable records and serialized safety transactions on PostgreSQL or demo SQLite.

Every mutating operation takes the same database lock, including budget checks,
job claims and kill-switch changes. This is deliberate single-operator MVP
serialization, not an in-memory concurrency guarantee.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import threading
import time
import uuid
from typing import Any

from sqlalchemy import (JSON, Column, Float, Integer, MetaData, String, Table,
                        create_engine, delete, insert, select, text, update)
from sqlalchemy.pool import StaticPool


metadata = MetaData()
records = Table("lite_records", metadata,
    Column("kind", String(64), primary_key=True),
    Column("id", String(160), primary_key=True),
    Column("data", JSON, nullable=False), Column("updated_at", Float, nullable=False))
audit_rows = Table("lite_audit", metadata,
    Column("id", String(36), primary_key=True), Column("actor", String(160), nullable=False),
    Column("action", String(100), nullable=False), Column("details", JSON, nullable=False),
    Column("created_at", Float, nullable=False))
lock_row = Table("lite_control_lock", metadata, Column("id", Integer, primary_key=True))
idempotency = Table("lite_idempotency", metadata,
    Column("scope", String(100), primary_key=True), Column("key", String(200), primary_key=True),
    Column("fingerprint", String(64), nullable=False), Column("result", JSON, nullable=False))


def fingerprint(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


class Database:
    def __init__(self, url):
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://"):]
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        self.url = url
        self.sqlite = url.startswith("sqlite")
        options: dict[str, Any] = {"pool_pre_ping": True}
        if self.sqlite:
            options["connect_args"] = {"check_same_thread": False, "timeout": 30}
            if ":memory:" in url or url == "sqlite://":
                options["poolclass"] = StaticPool
        self.engine = create_engine(url, **options)
        self.mutex = threading.RLock()
        metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            conn.execute(text("INSERT INTO lite_control_lock (id) VALUES (1) ON CONFLICT (id) DO NOTHING"))

    @contextmanager
    def transaction(self):
        # SQLite shared-memory connections also need a process-local mutex.
        with self.mutex, self.engine.connect() as conn:
            if self.sqlite:
                conn.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                conn.begin()
                conn.execute(select(lock_row).where(lock_row.c.id == 1).with_for_update())
            try:
                yield Transaction(conn)
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def close(self):
        self.engine.dispose()


class Transaction:
    def __init__(self, conn):
        self.conn = conn
        self.now = time.time()

    def checkpoint(self):
        """Persist a provider attempt BEFORE network I/O, then re-lock policy.

        Only durable external outbox dispatch uses this boundary. A process
        failure after this commit leaves an attempted intent for manual
        reconciliation instead of authorizing an automatic duplicate send.
        """
        self.conn.commit()
        if self.conn.dialect.name == "sqlite":
            self.conn.exec_driver_sql("BEGIN IMMEDIATE")
        else:
            self.conn.begin()
            self.conn.execute(select(lock_row).where(lock_row.c.id == 1).with_for_update())
        self.now = time.time()

    def get(self, kind, id, default=None):
        row = self.conn.execute(select(records.c.data).where(records.c.kind == kind, records.c.id == str(id))).first()
        return deepcopy(row[0]) if row else deepcopy(default)

    def list(self, kind):
        return [deepcopy(row[0]) for row in self.conn.execute(
            select(records.c.data).where(records.c.kind == kind).order_by(records.c.updated_at, records.c.id))]

    def put(self, kind, id, data):
        value = deepcopy(data)
        value["id"] = str(id)
        criteria = (records.c.kind == kind) & (records.c.id == str(id))
        if self.conn.execute(select(records.c.id).where(criteria)).first():
            self.conn.execute(update(records).where(criteria).values(data=value, updated_at=self.now))
        else:
            self.conn.execute(insert(records).values(kind=kind, id=str(id), data=value, updated_at=self.now))
        return deepcopy(value)

    def create(self, kind, data, id=None):
        id = str(id or uuid.uuid4())
        if self.get(kind, id) is not None:
            from .policy import PolicyError
            raise PolicyError("duplicate_record", "Record already exists")
        return self.put(kind, id, {"created_at": self.now, **data})

    def delete(self, kind, id):
        self.conn.execute(delete(records).where(records.c.kind == kind, records.c.id == str(id)))

    def audit(self, actor, action, details):
        from .policy import actor_id
        def redact(value):
            if isinstance(value, dict):
                return {k: "[redacted]" if any(s in k.lower() for s in ("password", "secret", "token", "authorization")) else redact(v)
                        for k, v in value.items()}
            if isinstance(value, list):
                return [redact(v) for v in value]
            return value
        self.conn.execute(insert(audit_rows).values(id=str(uuid.uuid4()), actor=actor_id(actor),
            action=action, details=redact(details), created_at=self.now))

    def audits(self, limit=100):
        return [dict(r._mapping) for r in self.conn.execute(
            select(audit_rows).order_by(audit_rows.c.created_at.desc()).limit(limit))]

    def idempotency_get(self, scope, key, payload):
        from .policy import PolicyError
        if not isinstance(key, str) or not key.strip() or len(key) > 200:
            raise PolicyError("idempotency_key", "A nonempty idempotency key (max 200 characters) is required", 422)
        row = self.conn.execute(select(idempotency).where(idempotency.c.scope == scope, idempotency.c.key == key)).first()
        if not row:
            return None
        if row.fingerprint != fingerprint(payload):
            raise PolicyError("idempotency_conflict", "This idempotency key belongs to different input")
        return deepcopy(row.result)

    def idempotency_put(self, scope, key, payload, result):
        cached = self.idempotency_get(scope, key, payload)
        if cached is None:
            self.conn.execute(insert(idempotency).values(scope=scope, key=key,
                fingerprint=fingerprint(payload), result=result))
        return result
