"""Seed launch stores and refresh their price snapshots."""

from __future__ import annotations

import argparse
import asyncio
import logging

from app.core.database import SessionLocal
from app.services.stores.base import seed_stores
from app.services.stores.card_kingdom import CardKingdomAdapter
from app.services.stores.card_trader import CardTraderAdapter
from app.services.stores.mana_pool import ManaPoolAdapter

logger = logging.getLogger(__name__)


def store_adapters() -> tuple[CardKingdomAdapter, ManaPoolAdapter, CardTraderAdapter]:
    return (CardKingdomAdapter(), ManaPoolAdapter(), CardTraderAdapter())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed stores and sync Card Kingdom, Mana Pool, and CardTrader Zero prices.",
    )
    return parser.parse_args(argv)


async def sync_stores() -> dict[str, int]:
    async with SessionLocal() as session:
        await seed_stores(session)
        await session.commit()
        counts: dict[str, int] = {}
        for adapter in store_adapters():
            try:
                written = await adapter.sync(session)
            except Exception:
                logger.exception(
                    "Price sync failed for %s; continuing with the other stores.",
                    adapter.store_id,
                )
                counts[adapter.store_id] = 0
                continue
            counts[adapter.store_id] = written
            logger.info("Upserted %s %s price rows", written, adapter.store_id)
        return counts


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parse_args(argv)
    asyncio.run(sync_stores())


if __name__ == "__main__":
    main()
