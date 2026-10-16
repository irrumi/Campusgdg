"""Durable, token-scoped SQLite storage and single-use action approvals.

Each Store owns a connection protected by a lock. SQLite transactions also
serialize writers across Store instances and processes. An executing action is
deliberately left executing after a restart: its remote outcome may be unknown.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Iterator
from urllib.parse import urlsplit

from campuspulse.models import (
    ActionProposal,
    AnalysisResult,
    Assignment,
    AuditEvent,
    Document,
    IssueReceipt,
    ProposedAction,
    RepositoryRef,
)

_SCHEMA_VERSION = 1
_INVALID_TOKEN_HASH = hashlib.sha256(b"").hexdigest()
_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "unknown"})


def _proposal_hash(repository: RepositoryRef, title: str, body: str) -> str:
    """Canonical wire format shared with security.proposal_hash."""
    content = json.dumps(
        {"repository": repository.model_dump(mode="json"), "title": title, "body": body},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _token_hash(token: str) -> str:
    # Tokens have 256 bits of random entropy; they are not human passwords.
    if not isinstance(token, str):
        return _INVALID_TOKEN_HASH
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _valid_receipt(action: ProposedAction, receipt: IssueReceipt) -> bool:
    url = urlsplit(receipt.url)
    expected_path = f"/{action.repository.owner}/{action.repository.name}/issues/{receipt.number}"
    return (
        receipt.verified
        and receipt.title == action.title
        and receipt.body == action.body
        and url.scheme == "https"
        and url.netloc.lower() == "github.com"
        and url.path.casefold() == expected_path.casefold()
        and not url.query
        and not url.fragment
    )


class Store:
    """Synchronous store; callers must close it when their application stops."""

    def __init__(self, path: str | Path):
        database = str(path)
        if database != ":memory:":
            Path(database).parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(
            database, timeout=10.0, isolation_level=None, check_same_thread=False
        )
        self._connection.row_factory = sqlite3.Row
        try:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA busy_timeout = 10000")
            self._connection.execute("PRAGMA synchronous = FULL")
            self._migrate()
        except BaseException:
            self._connection.close()
            raise

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise

    def _migrate(self) -> None:
        with self._transaction() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > _SCHEMA_VERSION:
                raise ValueError("Database schema is newer than this application")
            if version == 0:
                # Individual statements keep DDL and version changes in the
                # same transaction; executescript would commit prematurely.
                connection.execute(
                    """CREATE TABLE assignments (
                        id TEXT PRIMARY KEY,
                        token_hash TEXT NOT NULL,
                        assignment_json TEXT NOT NULL
                    )"""
                )
                connection.execute(
                    """CREATE TABLE actions (
                        id TEXT PRIMARY KEY,
                        assignment_id TEXT NOT NULL REFERENCES assignments(id),
                        proposal_hash TEXT NOT NULL,
                        proposal_json TEXT NOT NULL,
                        status TEXT NOT NULL CHECK (
                            status IN ('pending', 'executing', 'succeeded', 'failed', 'unknown')
                        ),
                        receipt_json TEXT,
                        error TEXT
                    )"""
                )
                connection.execute(
                    """CREATE TRIGGER immutable_action_proposal
                    BEFORE UPDATE OF id, assignment_id, proposal_hash, proposal_json ON actions
                    BEGIN SELECT RAISE(ABORT, 'Action proposals are immutable'); END"""
                )
                connection.execute(
                    "CREATE INDEX actions_assignment ON actions(assignment_id)"
                )
                connection.execute(
                    """CREATE TABLE audit_events (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        id TEXT NOT NULL UNIQUE,
                        assignment_id TEXT NOT NULL REFERENCES assignments(id),
                        event_json TEXT NOT NULL
                    )"""
                )
                connection.execute(
                    "CREATE INDEX events_assignment ON audit_events(assignment_id, sequence)"
                )
                connection.execute("PRAGMA user_version = 1")

    def _authorized(self, assignment_id: str, token: str) -> sqlite3.Row:
        row = self._connection.execute(
            "SELECT token_hash, assignment_json FROM assignments WHERE id = ?",
            (assignment_id,),
        ).fetchone()
        expected = row["token_hash"] if row is not None else _INVALID_TOKEN_HASH
        matches = hmac.compare_digest(_token_hash(token), expected)
        if row is None or not matches or not isinstance(token, str):
            raise KeyError("Resource not found")
        return row

    def _action(self, assignment_id: str, action_id: str) -> ProposedAction:
        row = self._connection.execute(
            "SELECT * FROM actions WHERE id = ? AND assignment_id = ?",
            (action_id, assignment_id),
        ).fetchone()
        if row is None:
            raise KeyError("Resource not found")
        data = json.loads(row["proposal_json"])
        data.update(
            status=row["status"],
            receipt=json.loads(row["receipt_json"]) if row["receipt_json"] is not None else None,
            error=row["error"],
        )
        return ProposedAction.model_validate(data)

    def create_assignment(self, document: Document, repository: RepositoryRef) -> tuple[Assignment, str]:
        assignment = Assignment(document=document, repository=repository)
        serialized = assignment.model_dump_json()
        token = secrets.token_urlsafe(32)
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO assignments(id, token_hash, assignment_json) VALUES (?, ?, ?)",
                (assignment.id, _token_hash(token), serialized),
            )
        return Assignment.model_validate_json(serialized), token

    def get_assignment(self, id: str, token: str) -> Assignment:
        with self._lock:
            row = self._authorized(id, token)
            return Assignment.model_validate_json(row["assignment_json"])

    def save_analysis(self, id: str, token: str, result: AnalysisResult) -> Assignment:
        with self._transaction() as connection:
            row = self._authorized(id, token)
            assignment = Assignment.model_validate_json(row["assignment_json"])
            assignment.analysis = AnalysisResult.model_validate_json(result.model_dump_json())
            serialized = assignment.model_dump_json()
            connection.execute(
                "UPDATE assignments SET assignment_json = ? WHERE id = ?", (serialized, id)
            )
        return Assignment.model_validate_json(serialized)

    def propose_action(self, id: str, token: str, proposal: ActionProposal) -> ProposedAction:
        with self._transaction() as connection:
            row = self._authorized(id, token)
            assignment = Assignment.model_validate_json(row["assignment_json"])
            # Never trust caller-supplied repository, hash, ID, or state from a
            # ProposedAction subclass. Those are assigned by this store.
            draft = ActionProposal.model_validate(
                proposal.model_dump(include={"requirement_id", "title", "body"})
            )
            if assignment.analysis is None:
                raise ValueError("Analyze the assignment before proposing an action")
            requirement_exists = any(
                requirement.id == draft.requirement_id for requirement in assignment.analysis.requirements
            )
            has_recommendation = any(
                finding.requirement_id == draft.requirement_id
                and finding.recommendation
                and finding.recommendation.strip()
                for finding in assignment.analysis.findings
            )
            if not requirement_exists or not has_recommendation:
                raise ValueError("Requirement has no stored recommendation")
            action = ProposedAction(
                **draft.model_dump(),
                assignment_id=id,
                repository=assignment.repository,
                proposal_hash=_proposal_hash(assignment.repository, draft.title, draft.body),
            )
            connection.execute(
                """INSERT INTO actions(
                    id, assignment_id, proposal_hash, proposal_json, status
                ) VALUES (?, ?, ?, ?, ?)""",
                (action.id, id, action.proposal_hash, action.model_dump_json(), "pending"),
            )
            return self._action(id, action.id)

    def get_action(self, assignment_id: str, token: str, action_id: str) -> ProposedAction:
        with self._lock:
            self._authorized(assignment_id, token)
            return self._action(assignment_id, action_id)

    def claim_action(
        self, assignment_id: str, token: str, action_id: str, proposal_hash: str
    ) -> ProposedAction:
        with self._transaction() as connection:
            self._authorized(assignment_id, token)
            action = self._action(assignment_id, action_id)
            expected = _proposal_hash(action.repository, action.title, action.body)
            supplied = proposal_hash.encode("utf-8") if isinstance(proposal_hash, str) else b""
            if not hmac.compare_digest(action.proposal_hash.encode("utf-8"), supplied):
                raise ValueError("Proposal hash does not match")
            if not hmac.compare_digest(action.proposal_hash, expected):
                raise ValueError("Stored proposal integrity check failed")
            updated = connection.execute(
                """UPDATE actions SET status = 'executing'
                WHERE id = ? AND assignment_id = ? AND status = 'pending' AND proposal_hash = ?""",
                (action_id, assignment_id, proposal_hash),
            )
            if updated.rowcount != 1:
                raise ValueError("Action has already been claimed")
            return self._action(assignment_id, action_id)

    def finish_action(
        self,
        assignment_id: str,
        token: str,
        action_id: str,
        status: str,
        receipt: IssueReceipt | None = None,
        error: str | None = None,
    ) -> ProposedAction:
        with self._transaction() as connection:
            self._authorized(assignment_id, token)
            action = self._action(assignment_id, action_id)
            if status not in _TERMINAL_STATUSES:
                raise ValueError("Action must finish with a terminal status")
            if action.status != "executing":
                raise ValueError("Only an executing action can be finished")
            if status == "succeeded" and receipt is None:
                raise ValueError("Successful actions require a receipt")
            if status == "succeeded" and not _valid_receipt(action, receipt):
                raise ValueError("Receipt does not verify the approved proposal")
            receipt_json = receipt.model_dump_json() if receipt is not None else None
            connection.execute(
                """UPDATE actions SET status = ?, receipt_json = ?, error = ?
                WHERE id = ? AND assignment_id = ? AND status = 'executing'""",
                (status, receipt_json, error, action_id, assignment_id),
            )
            return self._action(assignment_id, action_id)

    def add_event(self, assignment_id: str, token: str, kind: str, message: str) -> AuditEvent:
        with self._transaction() as connection:
            self._authorized(assignment_id, token)
            event = AuditEvent(assignment_id=assignment_id, kind=kind, message=message)
            connection.execute(
                "INSERT INTO audit_events(id, assignment_id, event_json) VALUES (?, ?, ?)",
                (event.id, assignment_id, event.model_dump_json()),
            )
            return event

    def list_events(self, assignment_id: str, token: str) -> list[AuditEvent]:
        with self._lock:
            self._authorized(assignment_id, token)
            rows = self._connection.execute(
                "SELECT event_json FROM audit_events WHERE assignment_id = ? ORDER BY sequence",
                (assignment_id,),
            ).fetchall()
            return [AuditEvent.model_validate_json(row["event_json"]) for row in rows]
