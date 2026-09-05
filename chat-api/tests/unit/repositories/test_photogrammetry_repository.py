"""PhotogrammetryRepository — statement shape / mutation verified against a mocked AsyncSession."""
import base64
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.repositories.photogrammetry import PhotogrammetryRepository


def make_repo(scalar=0, one_or_none=None):
    db = MagicMock()
    result = MagicMock()
    result.scalar_one = MagicMock(return_value=scalar)
    result.scalar_one_or_none = MagicMock(return_value=one_or_none)
    db.execute = AsyncMock(return_value=result)
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.delete = AsyncMock()
    return PhotogrammetryRepository(db), db


def compiled(db) -> str:
    stmt = db.execute.await_args.args[0]
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


async def test_create_job_uses_given_id_and_pending_status():
    repo, db = make_repo()
    job_id = uuid4()
    input_prefix = f"photogrammetry/user1/{job_id}/input/"
    job = await repo.create_job(job_id, "user1", "Scan", 12, input_prefix)
    assert job.id == job_id
    assert job.status == "pending"
    assert job.image_count == 12
    db.add.assert_called_once_with(job)
    db.flush.assert_awaited_once()


async def test_count_active_jobs_counts_pending_queued_processing():
    repo, db = make_repo(scalar=2)
    assert await repo.count_active_jobs("user1") == 2
    sql = compiled(db)
    assert "photogrammetry_jobs" in sql
    for s in ("pending", "queued", "processing"):
        assert f"'{s}'" in sql
    assert "'complete'" not in sql


async def test_update_job_status_sets_stage_keys_and_completed_at():
    job = MagicMock()
    job.completed_at = None
    repo, db = make_repo(one_or_none=job)
    await repo.update_job_status(uuid4(), "processing", stage="dense")
    assert job.status == "processing"
    assert job.stage == "dense"
    assert job.completed_at is None

    await repo.update_job_status(
        uuid4(),
        "complete",
        mesh_s3_key="k/mesh.glb",
        preview_s3_key="k/preview.png",
    )
    assert job.status == "complete"
    assert job.stage is None
    assert job.mesh_s3_key == "k/mesh.glb"
    assert job.preview_s3_key == "k/preview.png"
    assert job.completed_at is not None


async def test_get_job_scopes_by_user():
    repo, db = make_repo(one_or_none=None)
    assert await repo.get_job(uuid4(), "user1") is None
    sql = compiled(db)
    assert "user_id = 'user1'" in sql


async def test_list_jobs_cursor_points_at_last_returned_item():
    job1 = MagicMock()
    job1.created_at = datetime(2026, 8, 26, 12, 0, 0)
    job1.id = uuid4()

    job2 = MagicMock()
    job2.created_at = datetime(2026, 8, 26, 11, 0, 0)
    job2.id = uuid4()

    job3 = MagicMock()
    job3.created_at = datetime(2026, 8, 26, 10, 0, 0)
    job3.id = uuid4()

    db = MagicMock()
    result = MagicMock()
    scalars = MagicMock()
    scalars.all = MagicMock(return_value=[job1, job2, job3])
    result.scalars = MagicMock(return_value=scalars)
    db.execute = AsyncMock(return_value=result)

    repo = PhotogrammetryRepository(db)
    items, next_cursor = await repo.list_jobs("user1", None, 2)

    assert len(items) == 2
    assert items[0] == job1
    assert items[1] == job2
    cursor_data = json.loads(base64.b64decode(next_cursor).decode())
    assert cursor_data["created_at"] == job2.created_at.isoformat()
    assert cursor_data["id"] == str(job2.id)


async def test_list_jobs_no_cursor_when_page_not_full():
    job1 = MagicMock()
    job1.created_at = datetime(2026, 8, 26, 12, 0, 0)
    job1.id = uuid4()

    job2 = MagicMock()
    job2.created_at = datetime(2026, 8, 26, 11, 0, 0)
    job2.id = uuid4()

    db = MagicMock()
    result = MagicMock()
    scalars = MagicMock()
    scalars.all = MagicMock(return_value=[job1, job2])
    result.scalars = MagicMock(return_value=scalars)
    db.execute = AsyncMock(return_value=result)

    repo = PhotogrammetryRepository(db)
    items, next_cursor = await repo.list_jobs("user1", None, 2)

    assert len(items) == 2
    assert next_cursor is None


async def test_list_jobs_with_cursor_filters_older_than_cursor():
    cursor_dt = datetime(2026, 8, 26, 10, 30, 0)
    cursor_id = uuid4()
    cursor = base64.b64encode(
        json.dumps({"created_at": cursor_dt.isoformat(), "id": str(cursor_id)}).encode()
    ).decode()

    db = MagicMock()
    result = MagicMock()
    scalars = MagicMock()
    scalars.all = MagicMock(return_value=[])
    result.scalars = MagicMock(return_value=scalars)
    db.execute = AsyncMock(return_value=result)

    repo = PhotogrammetryRepository(db)
    await repo.list_jobs("user1", cursor, 2)

    sql = compiled(db)
    assert "created_at <" in sql


async def test_delete_job_deletes_when_found():
    job = MagicMock()
    repo, db = make_repo(one_or_none=job)
    await repo.delete_job(uuid4())
    db.delete.assert_awaited_once_with(job)


async def test_delete_job_noop_when_missing():
    repo, db = make_repo(one_or_none=None)
    await repo.delete_job(uuid4())
    db.delete.assert_not_awaited()


async def test_create_job_stores_remove_background():
    repo, db = make_repo()
    job = await repo.create_job(job_id=uuid4(), user_id="u", name="n", image_count=5,
                                input_prefix="p/", remove_background=True)
    assert job.remove_background is True
    default = await repo.create_job(job_id=uuid4(), user_id="u", name="n", image_count=5, input_prefix="p/")
    assert default.remove_background is False
