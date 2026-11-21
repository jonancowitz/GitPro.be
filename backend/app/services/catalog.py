"""Stage 1 candidate pruning.

Postgres returns inventory for the requested cards. Printing locks, finish
mode, quantity, and the single cheapest in-stock offer per store are applied
to those rows.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.card import Card
from app.models.store_price import StorePrice
from app.schemas.optimize import OptimizeItem

CONDITION_RANK = {"NM": 0, "LP": 1, "MP": 2, "HP": 3}
FINISH_RANK = {"nonfoil": 0, "foil": 1, "etched": 2}


@dataclass(frozen=True, slots=True)
class CardFact:
    scryfall_id: uuid.UUID
    oracle_id: uuid.UUID
    name: str
    set_code: str
    collector_number: str
    is_basic_land: bool
    image_uri: str | None


@dataclass(frozen=True, slots=True)
class InventoryRow:
    store_id: str
    scryfall_id: uuid.UUID
    oracle_id: uuid.UUID
    name: str
    set_code: str
    collector_number: str
    finish: str
    condition: str
    unit_price_cents: int
    stock_qty: int
    store_sku: str | None
    image_uri: str | None


@dataclass(frozen=True, slots=True)
class SelectedLine:
    quantity: int
    offers: dict[str, InventoryRow]


@dataclass(frozen=True, slots=True)
class RejectedCard:
    oracle_id: uuid.UUID | None
    scryfall_id: uuid.UUID | None
    name: str | None
    quantity: int
    finish: str
    reason: str


def acceptable_conditions(min_condition: str) -> tuple[str, ...]:
    limit = CONDITION_RANK[min_condition]
    return tuple(name for name, rank in CONDITION_RANK.items() if rank <= limit)


def build_card_statement(scryfall_ids: Sequence[uuid.UUID], oracle_ids: Sequence[uuid.UUID]):
    """Cards named by the request, before expanding to every printing."""

    clauses = []
    if scryfall_ids:
        clauses.append(Card.scryfall_id.in_(list(scryfall_ids)))
    if oracle_ids:
        clauses.append(Card.oracle_id.in_(list(oracle_ids)))
    if not clauses:
        return select(Card).where(false())
    return select(Card).where(or_(*clauses))


def build_offer_statement(
    oracle_ids: Sequence[uuid.UUID],
    store_ids: Sequence[str],
    conditions: Sequence[str],
):
    """Inventory rows for these printings, including zero-stock rows.

    Stock, finish, and printing locks stay out of SQL so an empty shelf can
    be reported as out of stock instead of an unknown card. The composite
    price index still limits this to the requested printings.
    """

    if not oracle_ids or not store_ids or not conditions:
        return select(StorePrice, Card).where(false())
    return (
        select(StorePrice, Card)
        .join(Card, Card.scryfall_id == StorePrice.scryfall_id)
        .where(
            Card.oracle_id.in_(list(oracle_ids)),
            StorePrice.store_id.in_(list(store_ids)),
            StorePrice.condition.in_(list(conditions)),
        )
    )


def card_fact_from_row(card: Card) -> CardFact:
    return CardFact(
        scryfall_id=card.scryfall_id,
        oracle_id=card.oracle_id,
        name=card.name,
        set_code=card.set_code,
        collector_number=card.collector_number,
        is_basic_land=card.is_basic_land,
        image_uri=card.image_uri,
    )


async def load_card_facts(session: AsyncSession, items: Sequence[OptimizeItem]) -> list[CardFact]:
    scryfall_ids: list[uuid.UUID] = []
    oracle_ids: list[uuid.UUID] = []
    seen_scryfall: set[uuid.UUID] = set()
    seen_oracle: set[uuid.UUID] = set()
    for item in items:
        if item.scryfall_id is not None and item.scryfall_id not in seen_scryfall:
            seen_scryfall.add(item.scryfall_id)
            scryfall_ids.append(item.scryfall_id)
        if item.oracle_id is not None and item.oracle_id not in seen_oracle:
            seen_oracle.add(item.oracle_id)
            oracle_ids.append(item.oracle_id)
    if not scryfall_ids and not oracle_ids:
        return []

    matched = await session.execute(build_card_statement(scryfall_ids, oracle_ids))
    oracles = {card.oracle_id for card in matched.scalars().all()}
    oracles.update(oracle_ids)
    if not oracles:
        return []
    printings = await session.execute(select(Card).where(Card.oracle_id.in_(list(oracles))))
    return [card_fact_from_row(card) for card in printings.scalars().all()]


async def load_inventory_rows(
    session: AsyncSession,
    oracle_ids: Sequence[uuid.UUID],
    store_ids: Sequence[str],
    min_condition: str,
) -> list[InventoryRow]:
    statement = build_offer_statement(oracle_ids, store_ids, acceptable_conditions(min_condition))
    result = await session.execute(statement)
    rows: list[InventoryRow] = []
    for price, card in result.all():
        rows.append(
            InventoryRow(
                store_id=price.store_id,
                scryfall_id=price.scryfall_id,
                oracle_id=card.oracle_id,
                name=card.name,
                set_code=card.set_code,
                collector_number=card.collector_number,
                finish=price.finish,
                condition=price.condition,
                unit_price_cents=price.price_cents,
                stock_qty=price.stock_qty,
                store_sku=price.store_sku,
                image_uri=card.image_uri,
            )
        )
    return rows


def select_candidates(
    items: Sequence[OptimizeItem],
    facts: Sequence[CardFact],
    rows: Sequence[InventoryRow],
    *,
    enabled_store_ids: Sequence[str],
    min_condition: str,
    finish_mode: str,
    exclude_basic_lands: bool,
) -> tuple[list[SelectedLine], list[RejectedCard]]:
    """Keep one cheapest in-stock offer per enabled store, or reject the line."""

    by_scryfall = {fact.scryfall_id: fact for fact in facts}
    by_oracle: dict[uuid.UUID, list[CardFact]] = defaultdict(list)
    for fact in facts:
        by_oracle[fact.oracle_id].append(fact)
    enabled = set(enabled_store_ids)
    allowed_conditions = set(acceptable_conditions(min_condition))
    selected: list[SelectedLine] = []
    rejected: list[RejectedCard] = []

    for item in items:
        fact = _resolve_fact(item, by_scryfall, by_oracle)
        if fact is None:
            rejected.append(
                RejectedCard(
                    oracle_id=item.oracle_id,
                    scryfall_id=item.scryfall_id,
                    name=None,
                    quantity=item.quantity,
                    finish=item.finish,
                    reason="unmatched",
                )
            )
            continue
        if exclude_basic_lands and fact.is_basic_land:
            continue

        offers, blocked = _offers_for_item(
            item,
            fact,
            rows,
            enabled=enabled,
            allowed_conditions=allowed_conditions,
            finish_mode=finish_mode,
        )
        if not offers:
            rejected.append(
                RejectedCard(
                    oracle_id=fact.oracle_id,
                    scryfall_id=item.scryfall_id or fact.scryfall_id,
                    name=fact.name,
                    quantity=item.quantity,
                    finish=item.finish,
                    reason="out_of_stock" if blocked else "unmatched",
                )
            )
            continue
        selected.append(SelectedLine(quantity=item.quantity, offers=offers))
    return selected, rejected


def _resolve_fact(
    item: OptimizeItem,
    by_scryfall: dict[uuid.UUID, CardFact],
    by_oracle: dict[uuid.UUID, list[CardFact]],
) -> CardFact | None:
    if item.printing_locked:
        if item.scryfall_id is None:
            return None
        return by_scryfall.get(item.scryfall_id)
    if item.scryfall_id is not None and item.scryfall_id in by_scryfall:
        return by_scryfall[item.scryfall_id]
    if item.oracle_id is None:
        return None
    group = by_oracle.get(item.oracle_id) or []
    return group[0] if group else None


def _offers_for_item(
    item: OptimizeItem,
    fact: CardFact,
    rows: Sequence[InventoryRow],
    *,
    enabled: set[str],
    allowed_conditions: set[str],
    finish_mode: str,
) -> tuple[dict[str, InventoryRow], bool]:
    best: dict[str, InventoryRow] = {}
    blocked = False
    lock_finish = item.printing_locked or finish_mode == "match_deck"
    for row in rows:
        if row.store_id not in enabled:
            continue
        if item.printing_locked:
            if row.scryfall_id != item.scryfall_id:
                continue
        elif row.oracle_id != fact.oracle_id:
            continue
        if lock_finish and row.finish != item.finish:
            continue
        if row.condition not in allowed_conditions or row.finish not in FINISH_RANK:
            continue
        if row.stock_qty < item.quantity:
            blocked = True
            continue
        current = best.get(row.store_id)
        if current is None or _offer_key(row) < _offer_key(current):
            best[row.store_id] = row
    return best, blocked


def _offer_key(row: InventoryRow) -> tuple[int, int, int, str]:
    return (
        row.unit_price_cents,
        CONDITION_RANK[row.condition],
        FINISH_RANK[row.finish],
        str(row.scryfall_id),
    )
