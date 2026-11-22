"""Printings of one card and the median in-stock price at each store."""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.card import Card
from app.models.store import Store
from app.models.store_price import StorePrice
from app.schemas.printings import CardPrinting, FinishPrices, PrintingsResponse, VendorMedian

PickerFinish = Literal["nonfoil", "foil"]
PICKER_FINISHES: tuple[PickerFinish, ...] = ("nonfoil", "foil")


def median_cents(prices: Sequence[int]) -> int | None:
    """Median listing price in cents. An even count rounds half up."""

    if not prices:
        return None
    ordered = sorted(prices)
    midpoint = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint] + 1) // 2


def build_printings_response(
    cards: Sequence[Card],
    stores: Sequence[Store],
    prices: Sequence[StorePrice],
) -> PrintingsResponse | None:
    """Group in-stock listings into one median per printing, finish, and store."""

    if not cards:
        return None
    ordered_cards = sorted(
        cards,
        key=lambda card: (card.set_code, card.collector_number, str(card.scryfall_id)),
    )
    enabled = [store for store in stores if store.enabled]
    grouped: dict[tuple[uuid.UUID, str, str], list[int]] = defaultdict(list)
    for price in prices:
        if price.stock_qty <= 0 or price.finish not in PICKER_FINISHES:
            continue
        grouped[(price.scryfall_id, price.store_id, price.finish)].append(price.price_cents)

    printings: list[CardPrinting] = []
    for card in ordered_cards:
        finish_prices = [
            FinishPrices(
                finish=finish,
                vendors=[
                    VendorMedian(
                        store_id=store.id,
                        store_name=store.name,
                        median_cents=median_cents(grouped.get((card.scryfall_id, store.id, finish), [])),
                    )
                    for store in enabled
                ],
            )
            for finish in PICKER_FINISHES
        ]
        printings.append(
            CardPrinting(
                scryfall_id=card.scryfall_id,
                oracle_id=card.oracle_id,
                name=card.name,
                set_code=card.set_code,
                collector_number=card.collector_number,
                image_uri=card.image_uri,
                finishes=_finish_names(card.finishes),
                prices=finish_prices,
            )
        )
    first = ordered_cards[0]
    return PrintingsResponse(oracle_id=first.oracle_id, name=first.name, printings=printings)


async def load_printings(
    session: AsyncSession,
    oracle_id: uuid.UUID,
    store_ids: Sequence[str],
) -> PrintingsResponse | None:
    """Load catalog printings and median prices for the requested stores."""

    card_result = await session.execute(
        select(Card)
        .where(Card.oracle_id == oracle_id)
        .order_by(Card.set_code, Card.collector_number, Card.scryfall_id)
    )
    cards = list(card_result.scalars().all())
    if not cards:
        return None
    enabled: list[Store] = []
    prices: list[StorePrice] = []
    if store_ids:
        store_result = await session.execute(select(Store).where(Store.id.in_(list(store_ids))))
        found = {store.id: store for store in store_result.scalars().all()}
        enabled = [found[store_id] for store_id in store_ids if store_id in found and found[store_id].enabled]
        if enabled:
            price_result = await session.execute(
                select(StorePrice).where(
                    StorePrice.scryfall_id.in_([card.scryfall_id for card in cards]),
                    StorePrice.store_id.in_([store.id for store in enabled]),
                    StorePrice.finish.in_(PICKER_FINISHES),
                    StorePrice.stock_qty > 0,
                )
            )
            prices = list(price_result.scalars().all())
    return build_printings_response(cards, enabled, prices)


def _finish_names(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        if isinstance(item, str) and item not in names:
            names.append(item)
    return names
