"""Database tables: Profile, Document, Fact, Application, Decision, AuditEvent.

* A Fact is one observation of one field on one document. Overlapping observations
  from different documents are all kept, so conflicts stay visible. A fact's history
  is its audit events.
* Decisions and audit events are append-only. The ORM refuses to update or delete
  them, and on SQLite a trigger refuses it at the database level too. A correction
  or resubmission adds a new Decision that points at the one it supersedes.
* ``id_last4`` facts must be exactly four digits (or empty when unreadable). Full ID
  numbers never reach the database.
* A fact's ``verification_status`` starts as ``unverified``. Deterministic checks
  (``backend.verification``) move it to ``verified`` or ``flagged``. An officer may
  set ``verified`` or ``rejected``; ``reviewed_by`` records who, and automatic checks
  never overwrite an officer's decision.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    DDL,
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column, object_session, relationship

from backend.db import Base
from backend.fields import FIELD_TYPES, from_json_value

VERIFICATION_STATUSES = ("unverified", "verified", "flagged", "rejected")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ImmutableRecordError(RuntimeError):
    """Raised when code tries to change or delete a Decision or AuditEvent."""


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    documents: Mapped[list[Document]] = relationship(back_populates="profile", order_by="Document.id")
    applications: Mapped[list[Application]] = relationship(
        back_populates="profile", order_by="Application.id"
    )


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id"), index=True)
    document_type: Mapped[str] = mapped_column(String(50))
    filename: Mapped[str | None] = mapped_column(String(255))
    sha256: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(20), default="upload")
    extraction_status: Mapped[str | None] = mapped_column(String(20))  # ok / failed; None for seeds
    extraction_source: Mapped[str | None] = mapped_column(String(10))  # live / cache
    extraction_model: Mapped[str | None] = mapped_column(String(100))
    extraction_error: Mapped[str | None] = mapped_column(Text)
    image_quality: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # measurements + ok
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    profile: Mapped[Profile] = relationship(back_populates="documents")
    facts: Mapped[list[Fact]] = relationship(back_populates="document", order_by="Fact.id")


class Fact(Base):
    __tablename__ = "facts"
    __table_args__ = (UniqueConstraint("document_id", "field", name="uq_fact_document_field"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    field: Mapped[str] = mapped_column(String(50))
    value_type: Mapped[str] = mapped_column(String(10))
    value_json: Mapped[Any] = mapped_column(JSON, nullable=True)  # None: unreadable or failed validation
    confidence: Mapped[float] = mapped_column(Float)
    verification_status: Mapped[str] = mapped_column(String(20), default="unverified")
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    reviewed_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    document: Mapped[Document] = relationship(back_populates="facts")
    history: Mapped[list[AuditEvent]] = relationship(order_by="AuditEvent.id", viewonly=True)

    @property
    def value(self) -> Any:
        return from_json_value(self.field, self.value_json)

    @property
    def problem(self) -> str | None:
        """Why the value is unusable (first failed validator), or None."""
        failed = [c for c in self.checks or [] if c.get("kind") == "validator" and not c.get("passed")]
        return failed[0]["message"] if failed else None

    @property
    def low_quality_image(self) -> bool:
        """True when the document image failed the deterministic quality check."""
        return any(c.get("kind") == "image_quality" and not c.get("passed") for c in self.checks or [])


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("profile_id", "scheme_id", name="uq_application_profile_scheme"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id"), index=True)
    scheme_id: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    profile: Mapped[Profile] = relationship(back_populates="applications")
    decisions: Mapped[list[Decision]] = relationship(order_by="Decision.sequence", viewonly=True)


class Decision(Base):
    __tablename__ = "decisions"
    __table_args__ = (UniqueConstraint("application_id", "sequence", name="uq_decision_sequence"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    supersedes_decision_id: Mapped[int | None] = mapped_column(ForeignKey("decisions.id"))
    scheme_id: Mapped[str] = mapped_column(String(100))
    ruleset_version: Mapped[str] = mapped_column(String(50))
    confidence_cutoff: Mapped[float] = mapped_column(Float)
    verdict: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str | None] = mapped_column(Text)
    rule_outcomes: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    evaluated_values: Mapped[dict[str, Any]] = mapped_column(JSON)
    source_facts: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    mismatches: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    review_reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    decided_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    audit_events: Mapped[list[AuditEvent]] = relationship(order_by="AuditEvent.id", viewonly=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(50))
    profile_id: Mapped[str | None] = mapped_column(ForeignKey("profiles.id"), index=True)
    application_id: Mapped[int | None] = mapped_column(ForeignKey("applications.id"), index=True)
    decision_id: Mapped[int | None] = mapped_column(ForeignKey("decisions.id"), index=True)
    fact_id: Mapped[int | None] = mapped_column(ForeignKey("facts.id"), index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


# --- Guards -----------------------------------------------------------------------------


@event.listens_for(Fact, "before_insert")
@event.listens_for(Fact, "before_update")
def _check_fact(_mapper, _connection, fact: Fact) -> None:
    if fact.field not in FIELD_TYPES:
        raise ValueError(f"Unknown fact field {fact.field!r}")
    if fact.field == "id_last4" and fact.value_json is not None and not re.fullmatch(r"\d{4}", str(fact.value_json)):
        raise ValueError("id_last4 must be exactly 4 digits; full ID numbers are never stored")
    if not 0.0 <= fact.confidence <= 1.0:
        raise ValueError("confidence must be between 0 and 1")
    if fact.verification_status not in VERIFICATION_STATUSES:
        raise ValueError(f"verification_status must be one of {VERIFICATION_STATUSES}")


def _refuse_update(_mapper, _connection, target: Any) -> None:
    session = object_session(target)
    if session is None or session.is_modified(target, include_collections=False):
        raise ImmutableRecordError(
            f"{type(target).__name__} {target.id} is immutable; record a new one instead"
        )


def _refuse_delete(_mapper, _connection, target: Any) -> None:
    raise ImmutableRecordError(f"{type(target).__name__} {target.id} is immutable and cannot be deleted")


for _model in (Decision, AuditEvent):
    event.listen(_model, "before_update", _refuse_update)
    event.listen(_model, "before_delete", _refuse_delete)
    _table = _model.__table__.name
    for _op in ("UPDATE", "DELETE"):
        event.listen(
            _model.__table__,
            "after_create",
            DDL(
                f"CREATE TRIGGER IF NOT EXISTS {_table}_no_{_op.lower()} BEFORE {_op} ON {_table} "
                f"BEGIN SELECT RAISE(ABORT, '{_table} rows are immutable'); END"
            ).execute_if(dialect="sqlite"),
        )
