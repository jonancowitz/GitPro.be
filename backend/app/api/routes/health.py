from datetime import datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from app.core.database import engine
from app.core.redis import create_redis

router = APIRouter(prefix="/api/v1", tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "unavailable"]
    redis: Literal["ok", "unavailable"]
    last_store_sync: datetime | None
    last_store_syncs: dict[str, datetime]


async def _database_status() -> tuple[Literal["ok", "unavailable"], datetime | None, dict[str, datetime]]:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
            try:
                result = await connection.execute(
                    text(
                        "SELECT store_id, MAX(updated_at) AS last_sync "
                        "FROM store_prices GROUP BY store_id"
                    )
                )
                rows = result.all()
            except Exception:
                await connection.rollback()
                return "ok", None, {}
    except Exception:
        return "unavailable", None, {}

    syncs = {row.store_id: row.last_sync for row in rows}
    last_sync = max(syncs.values()) if syncs else None
    return "ok", last_sync, syncs


async def _redis_status() -> Literal["ok", "unavailable"]:
    client = create_redis()
    try:
        pong = await client.ping()
    except Exception:
        return "unavailable"
    finally:
        await client.aclose()
    return "ok" if pong else "unavailable"


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    database, last_store_sync, last_store_syncs = await _database_status()
    redis_status = await _redis_status()
    status: Literal["ok", "degraded"] = "ok" if database == "ok" and redis_status == "ok" else "degraded"
    return HealthResponse(
        status=status,
        database=database,
        redis=redis_status,
        last_store_sync=last_store_sync,
        last_store_syncs=last_store_syncs,
    )
