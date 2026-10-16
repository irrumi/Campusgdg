"""Shared contracts. Changes must be coordinated across all workers."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def new_id() -> str:
    return uuid4().hex


def now() -> datetime:
    return datetime.now(timezone.utc)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DocumentPage(Model):
    number: int = Field(ge=1)
    text: str = Field(max_length=100_000)


class Document(Model):
    filename: str
    pages: list[DocumentPage] = Field(min_length=1, max_length=100)
    sha256: str


class SourceReference(Model):
    page: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=4000)


class Requirement(Model):
    id: str = Field(default_factory=new_id)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    source: SourceReference
    ambiguity: str | None = None


class RepositoryRef(Model):
    owner: str
    name: str
    url: str


class Evidence(Model):
    id: str = Field(default_factory=new_id)
    tool: str
    source_url: str
    summary: str = Field(max_length=20_000)
    data: dict[str, Any] = Field(default_factory=dict)
    retrieved_at: datetime = Field(default_factory=now)


class RequirementStatus(StrEnum):
    SATISFIED = "satisfied"
    NOT_SATISFIED = "not_satisfied"
    UNVERIFIED = "unverified"


class Finding(Model):
    requirement_id: str
    status: RequirementStatus
    explanation: str
    evidence_ids: list[str] = Field(default_factory=list)
    recommendation: str | None = None


class AgentStep(Model):
    id: str = Field(default_factory=new_id)
    kind: Literal["extract", "decision", "tool", "verification", "error"]
    message: str
    tool: str | None = None
    timestamp: datetime = Field(default_factory=now)


class AnalysisResult(Model):
    requirements: list[Requirement]
    findings: list[Finding]
    evidence: list[Evidence]
    steps: list[AgentStep]
    model: str
    live: bool = True
    warnings: list[str] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)


class Assignment(Model):
    id: str = Field(default_factory=new_id)
    document: Document
    repository: RepositoryRef
    analysis: AnalysisResult | None = None
    created_at: datetime = Field(default_factory=now)


class IssueReceipt(Model):
    number: int = Field(gt=0)
    url: str
    title: str
    body: str
    verified: bool = False


class ActionProposal(Model):
    requirement_id: str
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20_000)


class ProposedAction(ActionProposal):
    id: str = Field(default_factory=new_id)
    assignment_id: str
    repository: RepositoryRef
    proposal_hash: str
    status: Literal["pending", "executing", "succeeded", "failed", "unknown"] = "pending"
    receipt: IssueReceipt | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=now)


class AuditEvent(Model):
    id: str = Field(default_factory=new_id)
    assignment_id: str
    kind: str
    message: str
    timestamp: datetime = Field(default_factory=now)
