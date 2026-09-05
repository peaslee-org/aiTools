"""Photogrammetry jobs: one row per photo set submitted for reconstruction.

Input images are not rows — `input_prefix` + `image_count` describe them; keys are
`<input_prefix>0001.<ext>` … . Outputs are written by the worker under `…/output/`.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDMixin

JOB_STATUSES = ("pending", "queued", "processing", "complete", "failed")
STAGES = ("sfm", "dense", "mesh", "texture")


class PhotogrammetryJob(UUIDMixin, Base):
    __tablename__ = "photogrammetry_jobs"

    user_id: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        SAEnum(*JOB_STATUSES, name="photogrammetry_job_status"),
        nullable=False,
        default="pending",
    )
    stage: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    image_count: Mapped[int] = mapped_column(Integer, nullable=False)
    input_prefix: Mapped[str] = mapped_column(String(1024), nullable=False)
    mesh_s3_key: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    preview_s3_key: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    warnings: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    # {filename: "registered" | "unregistered" | "skipped:<reason>"} — written by the worker after
    # SfM (success and the "only N of M matched" failure alike) so the photo grid can show which.
    photo_status: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    # First claim by a worker — starts the job's billable GPU time (survives resumes; written
    # by the photogrammetry worker, read by the usage panel's cost-per-job).
    processing_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_public: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # Mask the backdrop out of the dense stage (worker: pipeline/masks.py). Off by default;
    # the sample scan and the mock never set it.
    remove_background: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
