from datetime import datetime
from sqlalchemy import create_engine, String, Integer, Float, DateTime, Boolean, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


class KBEntry(Base):
    __tablename__ = "kb_entries"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    product: Mapped[str] = mapped_column(String)
    owner_team: Mapped[str] = mapped_column(String)
    severity: Mapped[str] = mapped_column(String)
    pattern_description: Mapped[str] = mapped_column(Text)
    symptoms: Mapped[str] = mapped_column(Text)
    # "execute": run resolution_steps (list of echo commands) in sequence.
    # "investigate": launch the investigator agent against scenario_slug.
    action_type: Mapped[str] = mapped_column(String, default="execute")
    # JSON-encoded list of echo step strings, e.g. ["echo Step 1...", "echo Step 2..."]
    resolution_steps: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_summary: Mapped[str] = mapped_column(Text)
    scenario_slug: Mapped[str | None] = mapped_column(String, nullable=True)
    auto_execute: Mapped[bool] = mapped_column(Boolean, default=False)


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    log_line: Mapped[str] = mapped_column(Text)
    is_known: Mapped[bool] = mapped_column(Boolean)
    matched_kb_id: Mapped[str | None] = mapped_column(String, nullable=True)
    product: Mapped[str] = mapped_column(String)
    owner_team: Mapped[str] = mapped_column(String)
    severity: Mapped[str] = mapped_column(String)
    confidence: Mapped[float] = mapped_column(Float)
    reasoning: Mapped[str] = mapped_column(Text)
    recommended_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_steps: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list of echo steps
    action_type: Mapped[str] = mapped_column(String, default="execute")  # execute | investigate
    scenario_slug: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String)  # auto_resolved | pending_approval | approved | rejected | alerted_new | failed | investigating | investigated
    action_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    investigation_report: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: {root_cause, confidence, recommended_action, page_team}
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


engine = create_engine(f"sqlite:///{settings.db_path}", echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def init_db():
    Base.metadata.create_all(engine)


def get_session():
    return SessionLocal()
