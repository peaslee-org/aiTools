"""photogrammetry_jobs as the worker sees it. The API owns the schema (chat-api Alembic l2m3n4o5p6q7)."""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, func, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JOB_STATUSES = ("pending", "queued", "processing", "complete", "failed")
STAGES = ("sfm", "dense", "mesh", "texture")


class Base(DeclarativeBase):
    pass


class PhotogrammetryJob(Base):
    __tablename__ = "photogrammetry_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(
        SAEnum(*JOB_STATUSES, name="photogrammetry_job_status", create_type=False), nullable=False
    )
    stage: Mapped[Optional[str]] = mapped_column(String(20))
    image_count: Mapped[int] = mapped_column(Integer, nullable=False)
    input_prefix: Mapped[str] = mapped_column(String(1024), nullable=False)
    mesh_s3_key: Mapped[Optional[str]] = mapped_column(String(1024))
    preview_s3_key: Mapped[Optional[str]] = mapped_column(String(1024))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    warnings: Mapped[Optional[list]] = mapped_column(JSONB)
    photo_status: Mapped[Optional[dict]] = mapped_column(JSONB)   # {filename: registered|unregistered|skipped:<why>}
    # Mask the backdrop out of the dense stage (pipeline/masks.py). Set by the API at create.
    remove_background: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    # First claim by a worker — the start of the job's billable GPU time (survives resumes).
    processing_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
