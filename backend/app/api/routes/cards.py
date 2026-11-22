"""GET /api/v1/cards/{oracle_id}/printings."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.schemas.printings import PrintingsResponse
from app.services.printings import load_printings

router = APIRouter(prefix="/api/v1", tags=["cards"])


@router.get("/cards/{oracle_id}/printings", response_model=PrintingsResponse)
async def card_printings(
    oracle_id: uuid.UUID,
    stores: str = "card_kingdom,mana_pool,card_trader",
    session: AsyncSession = Depends(get_session),
) -> PrintingsResponse:
    """List every printing of a card and the median in-stock price at each store."""

    chosen = _store_ids(stores)
    if not chosen:
        raise HTTPException(
            status_code=422,
            detail={"code": "no_stores", "message": "Select at least one store."},
        )
    found = await load_printings(session, oracle_id, chosen)
    if found is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "unknown_card", "message": "That card isn't in the catalog yet."},
        )
    return found


def _store_ids(raw: str) -> list[str]:
    chosen: list[str] = []
    for part in raw.split(","):
        cleaned = part.strip()
        if not cleaned or len(cleaned) > 32 or cleaned in chosen:
            continue
        chosen.append(cleaned)
    return chosen
