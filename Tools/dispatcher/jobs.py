"""Phase 1c-i: durable SQLite job store for the dispatcher (shadow only).

The store is the hand-off point a later scheduler phase will use to hand work
to runners.  This phase only provides the schema and the CAS/lease primitives;
``dispatcher.py run`` / ``once`` do not enqueue or claim jobs yet, so existing
scheduling behaviour is unchanged.

Job ids are derived from the immutable job identity
``(role, ticket, pr, base_sha, prompt_hash)``.  A retry reuses the same row and
job id; the per-row ``attempt`` counter is the only thing that changes.  Lease
timestamps are UTC epoch seconds so the CAS predicate is a simple numeric
comparison; the CLI renders them as ISO-8601.

SQLite is used in WAL mode so a reader (``jobs list``) never blocks the writer
that is heartbeating a lease.

The reducer's ``derive_job_id(role, issue, base_sha, branch)`` names the
per-job operation log under ``.agent/jobs/<id>/operations.jsonl`` and is a
different namespace: it includes the branch but not ``pr`` / ``prompt_hash``.
A later scheduler integration must choose one canonical id (or persist an
explicit mapping); this module intentionally does not reuse the reducer id so
the two schemes cannot silently collide.
"""

from __future__ import annotations

import hashlib
import math
import re
import sqlite3
import time
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

JOBS_ROOT = Path(".agent/jobs")
JOBS_DB = JOBS_ROOT / "jobs.db"

JOB_STATES = ("queued", "leased", "running", "reducing", "done", "failed", "stale")
ACTIVE_STATES = ("leased", "running", "reducing")
TERMINAL_STATES = ("done", "failed")

DEFAULT_BUSY_TIMEOUT_MS = 5000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id       TEXT PRIMARY KEY,
    role         TEXT NOT NULL,
    ticket       INTEGER NOT NULL,
    pr           INTEGER,
    base_sha     TEXT NOT NULL DEFAULT '',
    head_sha     TEXT NOT NULL DEFAULT '',
    prompt_hash  TEXT NOT NULL DEFAULT '',
    state        TEXT NOT NULL DEFAULT 'queued'
                 CHECK (state IN ('queued', 'leased', 'running', 'reducing',
                                  'done', 'failed', 'stale')),
    attempt      INTEGER NOT NULL DEFAULT 0,
    lease_owner  TEXT,
    lease_until  REAL,
    heartbeat_at REAL,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL,
    error        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_jobs_state_lease ON jobs (state, lease_until);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs (created_at);
"""


def _role_slug(role: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", role.lower()).strip("-") or "job"


def derive_job_id(
    role: str,
    ticket: int,
    pr: int | None = None,
    base_sha: str = "",
    prompt_hash: str = "",
) -> str:
    """Return the stable id for one job identity.

    The attempt number is deliberately absent: a retry after a failed or stale
    lease must resume the same row instead of creating a duplicate.  ``head_sha``
    is absent for the same reason (it changes when the worker commits).
    """
    role = str(role or "").strip().lower()
    if not role:
        raise ValueError("role 不能为空")
    ticket = int(ticket)
    if ticket <= 0:
        raise ValueError("ticket 必须是正整数")
    pr_value = int(pr) if pr not in (None, "") else 0
    if pr_value < 0:
        raise ValueError("pr 不能为负数")
    key = "|".join(
        (role, str(ticket), str(pr_value), str(base_sha or ""), str(prompt_hash or ""))
    )
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"{_role_slug(role)}-{ticket}-{digest}"


def _as_epoch(value: float | int | datetime | None) -> float:
    if value is None:
        raise ValueError("timestamp 不能为空")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()
    return float(value)


class JobStore:
    """SQLite-backed job queue with atomic claim and lease reaping."""

    def __init__(
        self, db_path: str | Path | None = None, *, now: Callable[[], float] | None = None,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    ) -> None:
        self.db_path = Path(db_path) if db_path is not None else JOBS_DB
        self._clock = now or time.time
        self.busy_timeout_ms = max(0, int(busy_timeout_ms))
        self._init_db()

    # -- connection / schema -------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self.db_path), timeout=self.busy_timeout_ms / 1000.0, isolation_level=None
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            # WAL is persistent on the database file; set it once at creation.
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(_SCHEMA)

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.execute("COMMIT")
            except BaseException:
                connection.rollback()
                raise

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        with closing(self._connect()) as connection:
            yield connection

    def _now(self, value: float | int | datetime | None = None) -> float:
        return _as_epoch(value) if value is not None else float(self._clock())

    # -- queue operations ----------------------------------------------------

    def enqueue(
        self,
        role: str,
        ticket: int,
        pr: int | None = None,
        base_sha: str = "",
        head_sha: str = "",
        prompt_hash: str = "",
        *,
        job_id: str | None = None,
        now: float | int | datetime | None = None,
    ) -> dict[str, Any]:
        """Insert a queued job; a repeated enqueue leaves the existing row alone.

        The stable id makes this an idempotent upsert: callers may safely retry
        after a crash without creating a second job for the same work identity.
        An explicit ``job_id`` is only accepted when it does not already belong
        to a different identity, so a caller cannot silently receive another
        job's row.
        """
        explicit_id = bool(job_id)
        job_id = job_id or derive_job_id(role, ticket, pr, base_sha, prompt_hash)
        current = self._now(now)
        role = str(role or "").strip().lower()
        if not role:
            raise ValueError("role 不能为空")
        ticket = int(ticket)
        if ticket <= 0:
            raise ValueError("ticket 必须是正整数")
        pr_value = int(pr) if pr not in (None, "") else None
        if pr_value is not None and pr_value < 0:
            raise ValueError("pr 不能为负数")
        base_value = str(base_sha or "")
        prompt_value = str(prompt_hash or "")
        with self._write() as connection:
            if explicit_id:
                existing = connection.execute(
                    "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
                ).fetchone()
                if existing is not None:
                    identity = (
                        str(existing["role"]),
                        int(existing["ticket"]),
                        existing["pr"],
                        str(existing["base_sha"]),
                        str(existing["prompt_hash"]),
                    )
                    if identity != (role, ticket, pr_value, base_value, prompt_value):
                        raise ValueError(f"job_id {job_id} 已存在且身份不匹配")
                    return dict(existing)
            connection.execute(
                """
                INSERT OR IGNORE INTO jobs (
                    job_id, role, ticket, pr, base_sha, head_sha, prompt_hash,
                    state, attempt, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?)
                """,
                (
                    job_id,
                    role,
                    ticket,
                    pr_value,
                    base_value,
                    str(head_sha or ""),
                    prompt_value,
                    current,
                    current,
                ),
            )
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None:  # pragma: no cover - the INSERT above guarantees a row
            raise RuntimeError(f"job 写入后无法回读：{job_id}")
        return dict(row)

    def claim(
        self,
        owner: str,
        ttl: float,
        *,
        now: float | int | datetime | None = None,
    ) -> dict[str, Any] | None:
        """Atomically lease the oldest claimable job, or return ``None``.

        A job is claimable when it is ``queued`` or when its lease has expired
        (an expired worker is presumed dead).  ``stale`` jobs were reaped from an
        expired lease and are therefore claimable again; ``attempt`` records how
        many times the job has been leased.  ``BEGIN IMMEDIATE`` plus the
        state/lease predicate makes the read-update-read sequence a single
        writer critical section, so concurrent claimers cannot both win.
        """
        owner = str(owner or "").strip()
        if not owner:
            raise ValueError("owner 不能为空")
        ttl_value = float(ttl)
        if not math.isfinite(ttl_value) or ttl_value <= 0:
            raise ValueError("ttl 必须大于 0")
        current = self._now(now)
        deadline = current + ttl_value
        expired = ", ".join(f"'{state}'" for state in ACTIVE_STATES + ("stale",))
        claimable = (
            f"state = 'queued' OR (state IN ({expired}) "
            "AND (lease_until IS NULL OR lease_until <= ?))"
        )
        with self._write() as connection:
            row = connection.execute(
                f"SELECT job_id FROM jobs WHERE {claimable} "
                "ORDER BY created_at ASC, job_id ASC LIMIT 1",
                (current,),
            ).fetchone()
            if row is None:
                return None
            updated = connection.execute(
                f"""
                UPDATE jobs
                SET state = 'leased', lease_owner = ?, lease_until = ?,
                    heartbeat_at = ?, attempt = attempt + 1,
                    updated_at = ?, error = ''
                WHERE job_id = ? AND ({claimable})
                """,
                (owner, deadline, current, current, row["job_id"], current),
            )
            if updated.rowcount != 1:
                return None
            claimed = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (row["job_id"],)
            ).fetchone()
        return dict(claimed) if claimed is not None else None

    def heartbeat(
        self,
        job_id: str,
        owner: str,
        ttl: float,
        *,
        now: float | int | datetime | None = None,
    ) -> bool:
        """Renew a lease held by ``owner``; stale-owner heartbeats are rejected."""
        owner = str(owner or "").strip()
        if not owner:
            raise ValueError("owner 不能为空")
        ttl_value = float(ttl)
        if not math.isfinite(ttl_value) or ttl_value <= 0:
            raise ValueError("ttl 必须大于 0")
        current = self._now(now)
        active = ", ".join(f"'{state}'" for state in ACTIVE_STATES)
        with self._write() as connection:
            updated = connection.execute(
                f"""
                UPDATE jobs
                SET lease_until = ?, heartbeat_at = ?, updated_at = ?
                WHERE job_id = ? AND lease_owner = ? AND state IN ({active})
                """,
                (current + ttl_value, current, current, str(job_id), owner),
            )
            return updated.rowcount == 1

    def finish(
        self,
        job_id: str,
        state: str,
        error: str = "",
        owner: str | None = None,
        *,
        attempt: int | None = None,
        now: float | int | datetime | None = None,
    ) -> bool:
        """Move a job to a terminal state, fenced by its current lease owner.

        A leased/running/reducing job can only be finished by the ``owner``
        that currently holds its lease; a stale owner whose lease was already
        re-claimed returns ``False`` and cannot clear the new owner's state.
        Pass ``attempt`` too for a stronger fencing token (the store increments
        it on every claim).  A queued job has no lease and is cancelled with
        ``owner=None``; finishing a leased job without an owner is rejected.
        """
        if state not in TERMINAL_STATES:
            raise ValueError(f"finish 只接受 {TERMINAL_STATES}，收到 {state!r}")
        owner_value = None if owner is None else str(owner).strip()
        if owner_value == "":
            raise ValueError("owner 不能为空")
        attempt_value: int | None = None
        if attempt is not None:
            attempt_value = int(attempt)
            if attempt_value < 0:
                raise ValueError("attempt 不能为负数")
        current = self._now(now)
        error_text = str(error or "")
        active = ", ".join(f"'{item}'" for item in ACTIVE_STATES)
        with self._write() as connection:
            if owner_value is None:
                # Explicit no-lease path: only a queued or reaped job can be
                # cancelled without fencing against a live worker.
                updated = connection.execute(
                    """
                    UPDATE jobs
                    SET state = ?, error = ?, updated_at = ?,
                        lease_owner = NULL, lease_until = NULL, heartbeat_at = NULL
                    WHERE job_id = ? AND lease_owner IS NULL
                      AND state IN ('queued', 'stale')
                    """,
                    (state, error_text, current, str(job_id)),
                )
            else:
                conditions = ["job_id = ?", "lease_owner = ?", f"state IN ({active})"]
                parameters: list[Any] = [
                    state, error_text, current, str(job_id), owner_value
                ]
                if attempt_value is not None:
                    conditions.append("attempt = ?")
                    parameters.append(attempt_value)
                updated = connection.execute(
                    f"""
                    UPDATE jobs
                    SET state = ?, error = ?, updated_at = ?,
                        lease_owner = NULL, lease_until = NULL, heartbeat_at = NULL
                    WHERE {' AND '.join(conditions)}
                    """,
                    parameters,
                )
            return updated.rowcount == 1

    def requeue(
        self,
        job_id: str,
        owner: str,
        *,
        attempt: int | None = None,
        error: str = "",
        now: float | int | datetime | None = None,
    ) -> bool:
        """Return a leased job to ``queued`` without marking it failed.

        A scheduler tick uses this when the delegated command could not start
        because another dispatcher process holds the per-PR lock (exit 2): the
        work was not attempted, so the row must stay claimable instead of
        becoming terminal.  The update is fenced by ``owner`` (and optionally
        ``attempt``) exactly like :meth:`finish`, so a stale worker whose lease
        was already reclaimed cannot reset the new owner's row.  The attempt
        counter is not rewound because the lease did happen.
        """
        owner_value = str(owner or "").strip()
        if not owner_value:
            raise ValueError("owner 不能为空")
        attempt_value: int | None = None
        if attempt is not None:
            attempt_value = int(attempt)
            if attempt_value < 0:
                raise ValueError("attempt 不能为负数")
        current = self._now(now)
        active = ", ".join(f"'{state}'" for state in ACTIVE_STATES)
        conditions = ["job_id = ?", "lease_owner = ?", f"state IN ({active})"]
        parameters: list[Any] = [str(error or ""), current, str(job_id), owner_value]
        if attempt_value is not None:
            conditions.append("attempt = ?")
            parameters.append(attempt_value)
        with self._write() as connection:
            updated = connection.execute(
                f"""
                UPDATE jobs
                SET state = 'queued', error = ?, updated_at = ?,
                    lease_owner = NULL, lease_until = NULL, heartbeat_at = NULL
                WHERE {' AND '.join(conditions)}
                """,
                parameters,
            )
            return updated.rowcount == 1

    def reap_stale(
        self,
        now: float | int | datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Mark expired active leases ``stale`` and return the reaped jobs."""
        current = self._now(now)
        active = ", ".join(f"'{state}'" for state in ACTIVE_STATES)
        with self._write() as connection:
            rows = connection.execute(
                f"""
                SELECT job_id FROM jobs
                WHERE state IN ({active})
                  AND (lease_until IS NULL OR lease_until <= ?)
                """,
                (current,),
            ).fetchall()
            job_ids = [row["job_id"] for row in rows]
            if not job_ids:
                return []
            placeholders = ", ".join("?" for _ in job_ids)
            connection.execute(
                f"""
                UPDATE jobs
                SET state = 'stale', lease_owner = NULL, lease_until = NULL,
                    updated_at = ?,
                    error = CASE WHEN error = '' THEN 'lease expired' ELSE error END
                WHERE job_id IN ({placeholders})
                  AND state IN ({active})
                  AND (lease_until IS NULL OR lease_until <= ?)
                """,
                (current, *job_ids, current),
            )
            reaped = connection.execute(
                f"SELECT * FROM jobs WHERE job_id IN ({placeholders}) "
                "ORDER BY created_at ASC, job_id ASC",
                job_ids,
            ).fetchall()
        return [dict(row) for row in reaped]

    # -- reads ---------------------------------------------------------------

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        """Return one job, or ``None`` when the id is unknown."""
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (str(job_id),)
            ).fetchone()
        return dict(row) if row is not None else None

    def list_jobs(
        self,
        state: str | None = None,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return jobs newest first, optionally filtered by state."""
        if state is not None and state not in JOB_STATES:
            raise ValueError(f"未知 state：{state!r}")
        if limit is not None and int(limit) < 0:
            raise ValueError("limit 不能为负数")
        query = "SELECT * FROM jobs"
        parameters: list[Any] = []
        if state is not None:
            query += " WHERE state = ?"
            parameters.append(state)
        query += " ORDER BY created_at DESC, job_id ASC"
        if limit is not None:
            query += " LIMIT ?"
            parameters.append(int(limit))
        with self._read() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [dict(row) for row in rows]


__all__ = [
    "ACTIVE_STATES", "DEFAULT_BUSY_TIMEOUT_MS", "JOB_STATES", "JOBS_DB",
    "JOBS_ROOT", "JobStore", "TERMINAL_STATES", "derive_job_id",
]
