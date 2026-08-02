"""SQLite persistence with optimistic task updates and append-only audit events."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ai_dev_platform.domain.models import (
    ApprovalRecord,
    ConversationSession,
    TaskRecord,
    WorkflowState,
)
from ai_dev_platform.security.scanner import ensure_safe_to_persist


class StateConflictError(RuntimeError):
    """A task was modified by another writer."""


class TaskNotFoundError(LookupError):
    """The requested task does not exist."""


class SQLiteStateStore:
    """Store task state, explicit approvals, and sanitized audit events."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    issue_number INTEGER NOT NULL UNIQUE,
                    state TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    result TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    issue_number INTEGER NOT NULL,
                    stage TEXT NOT NULL,
                    commit_sha TEXT NOT NULL,
                    approver TEXT NOT NULL,
                    approved INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    pull_request_number INTEGER,
                    conditions_json TEXT NOT NULL DEFAULT '[]',
                    github_record_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversation_sessions (
                    session_id TEXT PRIMARY KEY,
                    project_name TEXT NOT NULL,
                    issue_number INTEGER,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_task ON audit_events(task_id, event_id);
                CREATE INDEX IF NOT EXISTS idx_approval_target
                    ON approvals(issue_number, stage, commit_sha, approval_id);
                """
            )

            columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(approvals)").fetchall()
            }
            migrations = {
                "pull_request_number": (
                    "ALTER TABLE approvals ADD COLUMN pull_request_number INTEGER"
                ),
                "conditions_json": (
                    "ALTER TABLE approvals ADD COLUMN conditions_json TEXT NOT NULL DEFAULT '[]'"
                ),
                "github_record_id": (
                    "ALTER TABLE approvals ADD COLUMN github_record_id TEXT NOT NULL DEFAULT ''"
                ),
            }
            for column, statement in migrations.items():
                if column not in columns:
                    connection.execute(statement)

    @staticmethod
    def _serialize_task(task: TaskRecord) -> str:
        payload = json.dumps(task.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        ensure_safe_to_persist(payload)
        return payload

    def create_task(self, task: TaskRecord) -> TaskRecord:
        """Create a new task and initial audit event."""
        payload = self._serialize_task(task)
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO tasks("
                "task_id, issue_number, state, payload_json, version, updated_at"
                ") "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    task.task_id,
                    task.issue_number,
                    task.state.value,
                    payload,
                    task.version,
                    task.updated_at.isoformat(),
                ),
            )
        self.append_event(task.task_id, "system", "task_created", "success", {"state": task.state})
        return task

    def get_task(self, task_id: str) -> TaskRecord:
        """Load a task by stable task identifier."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        if row is None:
            raise TaskNotFoundError(task_id)
        return TaskRecord.model_validate_json(row["payload_json"])

    def get_task_by_issue(self, issue_number: int) -> TaskRecord:
        """Load a task by GitHub Issue number."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM tasks WHERE issue_number = ?", (issue_number,)
            ).fetchone()
        if row is None:
            raise TaskNotFoundError(str(issue_number))
        return TaskRecord.model_validate_json(row["payload_json"])

    def list_tasks(self) -> list[TaskRecord]:
        """List tasks ordered by most recent update."""
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM tasks ORDER BY updated_at DESC"
            ).fetchall()
        return [TaskRecord.model_validate_json(row["payload_json"]) for row in rows]

    def save_task(self, task: TaskRecord) -> TaskRecord:
        """Atomically save a task if its version is still current."""
        now = datetime.now(UTC)
        updated = task.model_copy(update={"version": task.version + 1, "updated_at": now})
        payload = self._serialize_task(updated)
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE tasks SET state = ?, payload_json = ?, version = ?, updated_at = ? "
                "WHERE task_id = ? AND version = ?",
                (
                    updated.state.value,
                    payload,
                    updated.version,
                    now.isoformat(),
                    updated.task_id,
                    task.version,
                ),
            )
            if cursor.rowcount != 1:
                raise StateConflictError(task.task_id)
        return updated

    def append_event(
        self,
        task_id: str,
        actor: str,
        action: str,
        result: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Append a sanitized audit event."""
        details_json = json.dumps(details or {}, ensure_ascii=False, default=str, sort_keys=True)
        ensure_safe_to_persist(details_json)
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO audit_events("
                "task_id, occurred_at, actor, action, result, details_json"
                ") "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    task_id,
                    datetime.now(UTC).isoformat(),
                    actor,
                    action,
                    result,
                    details_json,
                ),
            )

    def list_events(self, task_id: str) -> list[dict[str, Any]]:
        """Return sanitized audit events in chronological order."""
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT event_id, occurred_at, actor, action, result, details_json "
                "FROM audit_events WHERE task_id = ? ORDER BY event_id",
                (task_id,),
            ).fetchall()
        return [
            {
                "event_id": row["event_id"],
                "occurred_at": row["occurred_at"],
                "actor": row["actor"],
                "action": row["action"],
                "result": row["result"],
                "details": json.loads(row["details_json"]),
            }
            for row in rows
        ]

    def add_approval(self, approval: ApprovalRecord) -> None:
        """Persist an approval only after a formal GitHub record exists."""
        ensure_safe_to_persist(approval.reason)
        conditions_json = json.dumps(approval.conditions, ensure_ascii=False)
        ensure_safe_to_persist(conditions_json)
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO approvals(issue_number, stage, commit_sha, approver, approved, "
                "reason, pull_request_number, conditions_json, github_record_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    approval.issue_number,
                    approval.stage,
                    approval.commit_sha,
                    approval.approver,
                    int(approval.approved),
                    approval.reason,
                    approval.pull_request_number,
                    conditions_json,
                    approval.github_record_id,
                    approval.created_at.isoformat(),
                ),
            )

    def has_current_approval(self, issue_number: int, stage: str, commit_sha: str) -> bool:
        """Check the latest decision for the exact issue, stage, and commit."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT approved FROM approvals WHERE issue_number = ? AND stage = ? "
                "AND commit_sha = ? AND github_record_id <> '' "
                "ORDER BY approval_id DESC LIMIT 1",
                (issue_number, stage, commit_sha),
            ).fetchone()
        return bool(row and row["approved"])

    def save_conversation_session(self, session: ConversationSession) -> ConversationSession:
        """Upsert a sanitized project- or Issue-scoped conversation session."""
        now = datetime.now(UTC)
        updated = session.model_copy(update={"updated_at": now})
        payload = json.dumps(updated.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        ensure_safe_to_persist(payload)
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO conversation_sessions("
                "session_id, project_name, issue_number, payload_json, updated_at"
                ") VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET "
                "project_name = excluded.project_name, issue_number = excluded.issue_number, "
                "payload_json = excluded.payload_json, updated_at = excluded.updated_at",
                (
                    updated.session_id,
                    updated.project_name,
                    updated.issue_number,
                    payload,
                    now.isoformat(),
                ),
            )
        return updated

    def get_conversation_session(self, session_id: str) -> ConversationSession:
        """Load one conversation session."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM conversation_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            raise TaskNotFoundError(session_id)
        return ConversationSession.model_validate_json(row["payload_json"])

    def request_control(self, issue_number: int, action: str) -> TaskRecord:
        """Request pause/cancel or restore a paused task."""
        task = self.get_task_by_issue(issue_number)
        if action == "pause":
            task = task.model_copy(update={"pause_requested": True})
        elif action == "cancel":
            task = task.model_copy(update={"cancel_requested": True})
        elif action == "resume":
            if task.state != WorkflowState.PAUSED or task.resume_state is None:
                raise ValueError("task is not paused")
            task = task.model_copy(
                update={
                    "state": task.resume_state,
                    "resume_state": None,
                    "pause_requested": False,
                    "cancel_requested": False,
                }
            )
        else:
            raise ValueError("unknown control action")
        updated = self.save_task(task)
        self.append_event(updated.task_id, "human", action, "requested", {"state": updated.state})
        return updated
