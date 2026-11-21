"""Store adapter interface and shared price-snapshot upserts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.card import Card
from app.models.store import Store
from app.models.store_price import StorePrice
from app.services.streams import iter_json_items
from app.services.stores.seed import SEED_STORES

PriceRow = dict[str, Any]
PRICE_CONFLICT_COLUMNS = ("store_id", "scryfall_id", "finish", "condition")
PRICE_BATCH_SIZE = 500


class StoreAdapter(ABC):
    """Pull one store's public price snapshot into ``store_prices``."""

    store_id: str
    feed_url: str
    headers: dict[str, str]
    item_prefix: str = "data.item"

    @abstractmethod
    def map_item(self, item: dict[str, Any], updated_at: datetime) -> list[PriceRow]:
        """Normalize one feed record into price rows."""

    def iter_items(self) -> Iterator[dict[str, Any]]:
        timeout = httpx.Timeout(60.0, read=600.0)
        with httpx.Client(timeout=timeout, headers=self.headers, follow_redirects=True) as client:
            with client.stream("GET", self.feed_url) as response:
                response.raise_for_status()
                yield from iter_json_items(response.iter_bytes(), self.item_prefix)

    async def sync(self, session: AsyncSession, *, batch_size: int = PRICE_BATCH_SIZE) -> int:
        updated_at = datetime.now(timezone.utc)
        pending: list[PriceRow] = []
        written = 0
        for item in self.iter_items():
            pending.extend(self.map_item(item, updated_at))
            if len(pending) >= batch_size:
                written += await upsert_price_rows(session, pending)
                pending.clear()
                await session.commit()
        if pending:
            written += await upsert_price_rows(session, pending)
            await session.commit()
        return written


def price_conflict_key(row: PriceRow) -> tuple[Any, ...]:
    return tuple(row[column] for column in PRICE_CONFLICT_COLUMNS)


def dedupe_price_rows(rows: Iterable[PriceRow]) -> list[PriceRow]:
    """Collapse rows that share the store-price conflict key.

    Postgres rejects an INSERT that would update the same conflict target twice.
    The cheaper price wins. An equal price keeps the larger stock count.
    """

    chosen: dict[tuple[Any, ...], PriceRow] = {}
    for row in rows:
        key = price_conflict_key(row)
        current = chosen.get(key)
        if current is None or (row["price_cents"], -row["stock_qty"]) < (
            current["price_cents"],
            -current["stock_qty"],
        ):
            chosen[key] = row
    return list(chosen.values())


def build_store_price_upsert(rows: list[PriceRow]):
    statement = pg_insert(StorePrice).values(rows)
    excluded = statement.excluded
    return statement.on_conflict_do_update(
        index_elements=list(PRICE_CONFLICT_COLUMNS),
        set_={
            "price_cents": excluded.price_cents,
            "stock_qty": excluded.stock_qty,
            "store_sku": excluded.store_sku,
            "updated_at": excluded.updated_at,
        },
    )


async def upsert_price_rows(session: AsyncSession, rows: list[PriceRow]) -> int:
    unique_rows = dedupe_price_rows(rows)
    if not unique_rows:
        return 0
    identifiers = list({row["scryfall_id"] for row in unique_rows})
    result = await session.execute(select(Card.scryfall_id).where(Card.scryfall_id.in_(identifiers)))
    known = set(result.scalars().all())
    storable = [row for row in unique_rows if row["scryfall_id"] in known]
    if not storable:
        return 0
    await session.execute(build_store_price_upsert(storable))
    return len(storable)


def build_store_seed_upsert():
    statement = pg_insert(Store).values(SEED_STORES)
    excluded = statement.excluded
    return statement.on_conflict_do_update(
        index_elements=["id"],
        set_={
            "name": excluded.name,
            "store_type": excluded.store_type,
            "base_shipping_cents": excluded.base_shipping_cents,
            "free_shipping_threshold_cents": excluded.free_shipping_threshold_cents,
            "affiliate_param": excluded.affiliate_param,
            "enabled": excluded.enabled,
        },
    )


async def seed_stores(session: AsyncSession) -> None:
    await session.execute(build_store_seed_upsert())
