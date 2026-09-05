from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.photogrammetry import (
    MIN_IMAGES,
    JobCreateRequest,
    JobStatusResponse,
    extension_of,
)


def test_extension_of_lowercases_and_strips():
    assert extension_of("IMG_0001.JPG") == "jpg"
    assert extension_of("a.b.jpeg") == "jpeg"
    assert extension_of("noext") == ""


def test_create_request_rejects_fewer_than_min_images():
    with pytest.raises(ValidationError):
        JobCreateRequest(filenames=[f"{i}.jpg" for i in range(MIN_IMAGES - 1)])


def test_create_request_rejects_unsupported_extension():
    with pytest.raises(ValidationError) as exc:
        JobCreateRequest(filenames=[f"{i}.jpg" for i in range(4)] + ["notes.txt"])
    assert "notes.txt" in str(exc.value)


def test_create_request_accepts_mixed_case_extensions():
    req = JobCreateRequest(filenames=["a.JPG", "b.png", "c.jpeg", "d.jpg", "e.PNG"])
    assert len(req.filenames) == 5
    assert req.name is None


def test_status_response_warnings_default_to_empty_list():
    now = datetime.now(timezone.utc)
    r = JobStatusResponse(job_id=uuid4(), name="n", status="queued", image_count=5, created_at=now, updated_at=now)
    assert r.warnings == []


def test_photo_listing_schemas_shape():
    from app.schemas.photogrammetry import JobPhotosResponse, PhotoItem, SamplePhotosResponse

    item = PhotoItem(filename="0001.jpg", url="https://dl/0001.jpg", thumb_url="https://dl/t.jpg")
    assert JobPhotosResponse(photos=[item]).model_dump()["photos"][0]["thumb_url"] == "https://dl/t.jpg"
    sample = SamplePhotosResponse(name="Sample scan", image_count=1, photos=[item])
    assert sample.model_dump() == {
        "name": "Sample scan", "image_count": 1,
        "photos": [{"filename": "0001.jpg", "url": "https://dl/0001.jpg", "thumb_url": "https://dl/t.jpg", "status": None, "mask_url": None}],
    }


def test_job_photos_response_carries_status_matched_total():
    from app.schemas.photogrammetry import JobPhotosResponse, PhotoItem
    r = JobPhotosResponse(photos=[PhotoItem(filename="a.jpg", url="u", thumb_url="t", status="registered")],
                          matched=1, total=1)
    assert r.photos[0].status == "registered" and r.matched == 1 and r.total == 1
    assert PhotoItem(filename="a.jpg", url="u", thumb_url="t").status is None


def test_gpu_schemas_carry_kind_stages_and_medians():
    from app.schemas.gpu import GpuSessionSummary, GpuStateResponse, GpuUsageResponse, StartupStages
    st = StartupStages(capacity=1, boot=None, pull=3, container=4, init=5)
    assert st.boot is None
    now = datetime(2026, 9, 10, 15, 0, tzinfo=timezone.utc)
    s = GpuSessionSummary(started_at=now, ended_at=None, reason="job", started_by="u", end_reason=None,
                          hours=0.0, kind="warm", stages=st)
    assert s.kind == "warm" and s.stages.pull == 3
    assert GpuStateResponse(worker_state="off", estimated_wait_seconds=1).start_kind == "cold"
    u = GpuUsageResponse(today_hours=0, month_hours=0, daily_cap_hours=1, monthly_cap_hours=1,
                         warms_today_for_user=0, warm_cap_per_user_per_day=1, estimated_month_cost_usd=0,
                         hourly_rate_usd=0, sessions=[], cold_median_seconds=400, cold_samples=3,
                         warm_median_seconds=None, warm_samples=1)
    assert u.warm_median_seconds is None and u.cold_samples == 3


def test_create_request_remove_background_defaults_false_and_round_trips():
    files = [f"{i}.jpg" for i in range(MIN_IMAGES)]
    assert JobCreateRequest(filenames=files).remove_background is False
    assert JobCreateRequest(filenames=files, remove_background=True).remove_background is True


def test_status_response_remove_background_defaults_false():
    now = datetime.now(timezone.utc)
    r = JobStatusResponse(job_id=uuid4(), name="n", status="queued", image_count=5, created_at=now, updated_at=now)
    assert r.remove_background is False


def test_photo_item_mask_url_defaults_none():
    from app.schemas.photogrammetry import PhotoItem
    assert PhotoItem(filename="0001.jpg", url="u").mask_url is None
