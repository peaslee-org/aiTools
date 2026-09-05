from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

MIN_IMAGES = 5
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png"}


def extension_of(filename: str) -> str:
    """Lower-cased extension without the dot; '' when there is none."""
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[1].lower()


class JobCreateRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=200)
    filenames: List[str] = Field(..., min_length=MIN_IMAGES)
    # Turntable scans: keep the backdrop for SfM, mask it out of the dense stage.
    remove_background: bool = False

    @field_validator("filenames")
    @classmethod
    def _supported_images(cls, filenames: List[str]) -> List[str]:
        bad = [f for f in filenames if extension_of(f) not in ALLOWED_EXTENSIONS]
        if bad:
            raise ValueError(f"unsupported image type: {', '.join(bad[:3])}")
        return filenames


class UploadTarget(BaseModel):
    filename: str
    key: str
    url: str


class JobCreateResponse(BaseModel):
    job_id: UUID
    uploads: List[UploadTarget]


class JobStatusResponse(BaseModel):
    job_id: UUID
    name: str
    status: str
    stage: Optional[str] = None
    image_count: int
    preview_url: Optional[str] = None
    error_message: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    mock: bool = False
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None
    worker_state: Optional[str] = None
    estimated_wait_seconds: Optional[int] = None
    gpu_notice: Optional[str] = None
    is_public: bool = False
    remove_background: bool = False


class JobListResponse(BaseModel):
    items: List[JobStatusResponse]
    next_cursor: Optional[str] = None


class SampleJobResponse(BaseModel):
    job_id: UUID


class PhotoItem(BaseModel):
    filename: str
    url: str        # presigned GET of the original
    # Presigned GET of the 256 px thumbnail; None while it hasn't been generated yet (generation
    # runs in the background — kicked at confirm and by this listing — so clients refetch).
    thumb_url: Optional[str] = None
    # "registered" | "unregistered" | "skipped:<reason>" from the worker's SfM pass; None before
    # it ran (and always for the sample set listing).
    status: Optional[str] = None
    # Presigned GET of the worker's mask overlay (…/<job>/masks/<name>.jpg) once the dense stage
    # has run with remove_background; None otherwise (and always for the sample set).
    mask_url: Optional[str] = None


class JobPhotosResponse(BaseModel):
    photos: List[PhotoItem]
    matched: Optional[int] = None   # photos SfM registered; None until the worker reported
    total: int = 0


class SamplePhotosResponse(BaseModel):
    name: str
    image_count: int
    photos: List[PhotoItem]


class MeshUrlResponse(BaseModel):
    url: str                                    # plain GET — what <model-viewer> loads
    download_url: str                           # same object, Content-Disposition: attachment
    preview_download_url: Optional[str] = None  # preview.png as an attachment, when it exists
    expires_at: datetime
