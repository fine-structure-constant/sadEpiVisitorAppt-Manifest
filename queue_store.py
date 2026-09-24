"""Persistent SQLite queue for scheduled visitor appointments."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any


ACTIVE_STATUSES = ("pending", "retrying", "running")
TERMINAL_STATUSES = ("completed", "failed", "cancelled")


class AppointmentQueue:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        now = time.time()
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS appointment_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    payload_json TEXT NOT NULL,
                    scheduled_at REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    next_attempt_at REAL,
                    remote_id TEXT,
                    result_json TEXT,
                    last_error TEXT,
                    email_status TEXT,
                    email_error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    started_at REAL,
                    finished_at REAL
                )
                """
            )
            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(appointment_jobs)"
                ).fetchall()
            }
            if "email_status" not in columns:
                connection.execute(
                    "ALTER TABLE appointment_jobs ADD COLUMN email_status TEXT"
                )
            if "email_error" not in columns:
                connection.execute(
                    "ALTER TABLE appointment_jobs ADD COLUMN email_error TEXT"
                )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_appointment_jobs_due
                ON appointment_jobs(status, scheduled_at, next_attempt_at)
                """
            )
            connection.execute(
                """
                UPDATE appointment_jobs
                SET status = 'retrying',
                    next_attempt_at = ?,
                    last_error = CASE
                        WHEN last_error IS NULL OR last_error = ''
                        THEN '应用重启，任务等待重新执行'
                        ELSE last_error
                    END,
                    updated_at = ?
                WHERE status = 'running'
                """,
                (now, now),
            )

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        raw_result = item.pop("result_json")
        item["result"] = json.loads(raw_result) if raw_result else None
        return item

    def add(
        self,
        payload: dict[str, Any],
        scheduled_at: float,
        *,
        max_attempts: int = 3,
        remote_id: str | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                INSERT INTO appointment_jobs (
                    payload_json, scheduled_at, status, attempts, max_attempts,
                    remote_id, created_at, updated_at
                ) VALUES (?, ?, 'pending', 0, ?, ?, ?, ?)
                """,
                (
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    scheduled_at,
                    max(1, min(int(max_attempts), 10)),
                    remote_id,
                    now,
                    now,
                ),
            )
            job_id = cursor.lastrowid
        return self.get(job_id)

    def active_for_remote(self, remote_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                """
                SELECT * FROM appointment_jobs
                WHERE remote_id = ? AND status IN ('pending', 'retrying', 'running')
                ORDER BY id DESC LIMIT 1
                """,
                (remote_id,),
            ).fetchone()
        return self._decode(row)

    def get(self, job_id: int) -> dict[str, Any] | None:
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT * FROM appointment_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return self._decode(row)

    def list(self, *, limit: int = 200) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                """
                SELECT * FROM appointment_jobs
                ORDER BY
                    CASE status
                        WHEN 'running' THEN 0
                        WHEN 'pending' THEN 1
                        WHEN 'retrying' THEN 2
                        ELSE 3
                    END,
                    COALESCE(next_attempt_at, scheduled_at) ASC,
                    id DESC
                LIMIT ?
                """,
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [self._decode(row) for row in rows]

    def counts(self) -> dict[str, int]:
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM appointment_jobs GROUP BY status"
            ).fetchall()
        return {row["status"]: row["count"] for row in rows}

    def claim_due(self, now: float | None = None) -> dict[str, Any] | None:
        now = time.time() if now is None else now
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM appointment_jobs
                WHERE status IN ('pending', 'retrying')
                  AND scheduled_at <= ?
                  AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                ORDER BY COALESCE(next_attempt_at, scheduled_at), id
                LIMIT 1
                """,
                (now, now),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            changed = connection.execute(
                """
                UPDATE appointment_jobs
                SET status = 'running', attempts = attempts + 1,
                    started_at = ?, updated_at = ?, next_attempt_at = NULL,
                    last_error = NULL
                WHERE id = ? AND status = ?
                """,
                (now, now, row["id"], row["status"]),
            ).rowcount
            connection.commit()
            return self.get(row["id"]) if changed else None
        finally:
            connection.close()

    def complete(
        self,
        job_id: int,
        *,
        remote_id: str,
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        now = time.time()
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                UPDATE appointment_jobs
                SET status = 'completed', remote_id = ?, result_json = ?,
                    finished_at = ?, updated_at = ?, last_error = NULL,
                    email_status = 'pending', email_error = NULL
                WHERE id = ? AND status = 'running'
                """,
                (
                    remote_id,
                    json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                    now,
                    now,
                    job_id,
                ),
            )
        return self.get(job_id)

    def set_remote_id(self, job_id: int, remote_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                UPDATE appointment_jobs
                SET remote_id = ?, updated_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (remote_id, time.time(), job_id),
            )
        return self.get(job_id)

    def set_email_result(
        self, job_id: int, status: str, error: str | None = None
    ) -> dict[str, Any] | None:
        if status not in {"sent", "failed", "skipped"}:
            raise ValueError("invalid email status")
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                UPDATE appointment_jobs
                SET email_status = ?, email_error = ?, updated_at = ?
                WHERE id = ? AND status = 'completed'
                """,
                (status, str(error)[:2000] if error else None, time.time(), job_id),
            )
        return self.get(job_id)

    def fail(self, job_id: int, error: str) -> dict[str, Any] | None:
        now = time.time()
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT attempts, max_attempts FROM appointment_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                return None
            exhausted = row["attempts"] >= row["max_attempts"]
            delay = min(60 * (2 ** max(0, row["attempts"] - 1)), 600)
            connection.execute(
                """
                UPDATE appointment_jobs
                SET status = ?, next_attempt_at = ?, last_error = ?,
                    finished_at = ?, updated_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (
                    "failed" if exhausted else "retrying",
                    None if exhausted else now + delay,
                    str(error)[:2000],
                    now if exhausted else None,
                    now,
                    job_id,
                ),
            )
        return self.get(job_id)

    def cancel(self, job_id: int) -> bool:
        now = time.time()
        with closing(self._connect()) as connection, connection:
            changed = connection.execute(
                """
                UPDATE appointment_jobs
                SET status = 'cancelled', finished_at = ?, updated_at = ?,
                    next_attempt_at = NULL
                WHERE id = ? AND status IN ('pending', 'retrying')
                """,
                (now, now, job_id),
            ).rowcount
        return bool(changed)

    def retry(self, job_id: int, scheduled_at: float | None = None) -> bool:
        now = time.time()
        run_at = now if scheduled_at is None else scheduled_at
        with closing(self._connect()) as connection, connection:
            changed = connection.execute(
                """
                UPDATE appointment_jobs
                SET status = 'pending', scheduled_at = ?, attempts = 0,
                    next_attempt_at = NULL, last_error = NULL,
                    result_json = NULL, finished_at = NULL, updated_at = ?,
                    email_status = NULL, email_error = NULL
                WHERE id = ? AND status IN ('failed', 'cancelled')
                """,
                (run_at, now, job_id),
            ).rowcount
        return bool(changed)

    def reschedule(self, job_id: int, scheduled_at: float) -> bool:
        now = time.time()
        with closing(self._connect()) as connection, connection:
            changed = connection.execute(
                """
                UPDATE appointment_jobs
                SET scheduled_at = ?, status = 'pending', next_attempt_at = NULL,
                    updated_at = ?
                WHERE id = ? AND status IN ('pending', 'retrying')
                """,
                (scheduled_at, now, job_id),
            ).rowcount
        return bool(changed)

    def delete(self, job_id: int) -> bool:
        with closing(self._connect()) as connection, connection:
            changed = connection.execute(
                """
                DELETE FROM appointment_jobs
                WHERE id = ? AND status IN ('completed', 'failed', 'cancelled')
                """,
                (job_id,),
            ).rowcount
        return bool(changed)
