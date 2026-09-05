import base64
import json
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.photogrammetry import PhotogrammetryJob

ACTIVE_STATUSES = ("pending", "queued", "processing")


class PhotogrammetryRepository:

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_job(
        self, job_id: UUID, user_id: str, name: str, image_count: int, input_prefix: str,
        remove_background: bool = False,
    ) -> PhotogrammetryJob:
        job = PhotogrammetryJob(
            id=job_id,
            user_id=user_id,
            name=name,
            status="pending",
            image_count=image_count,
            input_prefix=input_prefix,
            remove_background=remove_background,
        )
        self.db.add(job)
        await self.db.flush()
        return job

    async def get_job(self, job_id: UUID, user_id: str) -> Optional[PhotogrammetryJob]:
        result = await self.db.execute(
            select(PhotogrammetryJob).where(
                PhotogrammetryJob.id == job_id,
                PhotogrammetryJob.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_job_any(self, job_id: UUID) -> Optional[PhotogrammetryJob]:
        """Lookup by id only — for background tasks that have no user context."""
        result = await self.db.execute(
            select(PhotogrammetryJob).where(PhotogrammetryJob.id == job_id)
        )
        return result.scalar_one_or_none()

    async def count_active_jobs(self, user_id: str) -> int:
        result = await self.db.execute(
            select(func.count()).where(
                PhotogrammetryJob.user_id == user_id,
                PhotogrammetryJob.status.in_(ACTIVE_STATUSES),
            )
        )
        return result.scalar_one()

    async def update_job_status(
        self,
        job_id: UUID,
        status: str,
        *,
        stage: Optional[str] = None,
        mesh_s3_key: Optional[str] = None,
        preview_s3_key: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        result = await self.db.execute(
            select(PhotogrammetryJob).where(PhotogrammetryJob.id == job_id)
        )
        job = result.scalar_one_or_none()
        if job is None:
            return
        job.status = status
        job.stage = stage if status == "processing" else None
        if mesh_s3_key is not None:
            job.mesh_s3_key = mesh_s3_key
        if preview_s3_key is not None:
            job.preview_s3_key = preview_s3_key
        if error_message is not None:
            job.error_message = error_message
        if status == "complete":
            job.completed_at = datetime.now(timezone.utc)

    async def list_jobs(
        self, user_id: str, cursor: Optional[str], limit: int
    ) -> tuple[List[PhotogrammetryJob], Optional[str]]:
        query = select(PhotogrammetryJob).where(PhotogrammetryJob.user_id == user_id)
        if cursor:
            cursor_data = json.loads(base64.b64decode(cursor).decode())
            cursor_dt = datetime.fromisoformat(cursor_data["created_at"])
            cursor_id = UUID(cursor_data["id"])
            query = query.where(
                or_(
                    PhotogrammetryJob.created_at < cursor_dt,
                    and_(
                        PhotogrammetryJob.created_at == cursor_dt,
                        PhotogrammetryJob.id < cursor_id,
                    ),
                )
            )
        query = (
            query.order_by(PhotogrammetryJob.created_at.desc(), PhotogrammetryJob.id.desc())
            .limit(limit + 1)
        )
        result = await self.db.execute(query)
        items = list(result.scalars().all())
        next_cursor = None
        if len(items) > limit:
            items.pop()
            last_returned = items[-1]
            next_cursor = base64.b64encode(
                json.dumps(
                    {
                        "created_at": last_returned.created_at.isoformat(),
                        "id": str(last_returned.id),
                    }
                ).encode()
            ).decode()
        return items, next_cursor

    async def delete_job(self, job_id: UUID) -> None:
        result = await self.db.execute(
            select(PhotogrammetryJob).where(PhotogrammetryJob.id == job_id)
        )
        job = result.scalar_one_or_none()
        if job:
            await self.db.delete(job)

    async def list_public_jobs(self, limit: int = 20) -> List[PhotogrammetryJob]:
        result = await self.db.execute(
            select(PhotogrammetryJob)
            .where(PhotogrammetryJob.is_public.is_(True))
            .order_by(PhotogrammetryJob.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars())

    async def get_public_job(self, job_id: UUID) -> Optional[PhotogrammetryJob]:
        result = await self.db.execute(
            select(PhotogrammetryJob).where(
                PhotogrammetryJob.id == job_id,
                PhotogrammetryJob.is_public.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def set_is_public(self, job_id: UUID, user_id: str, value: bool) -> Optional[PhotogrammetryJob]:
        job = await self.get_job(job_id, user_id)
        if job is None:
            return None
        job.is_public = value
        await self.db.flush()
        return job
