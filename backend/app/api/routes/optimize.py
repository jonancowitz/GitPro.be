"""POST /api/v1/optimize."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.redis import create_redis
from app.schemas.optimize import OptimizeRequest, OptimizeResponse
from app.services.optimizer import OptimizeError, optimize_deck

router = APIRouter(prefix="/api/v1", tags=["optimize"])


@router.post("/optimize", response_model=OptimizeResponse)
async def optimize(
    body: OptimizeRequest,
    session: AsyncSession = Depends(get_session),
) -> OptimizeResponse:
    """Split a deck across enabled stores and price the result."""

    redis = create_redis()
    client = httpx.AsyncClient()
    try:
        return await optimize_deck(session, redis, body, client=client)
    except OptimizeError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    finally:
        await client.aclose()
        await redis.aclose()
