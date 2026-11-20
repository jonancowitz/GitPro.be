"""Mana Pool price-snapshot sync.

The public variants feed is the daily snapshot used for later offline pruning.
Stage 3 live ``POST /cart-optimizer`` verification is Milestone 4 and is not
implemented here.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from app.services.stores.base import PriceRow, StoreAdapter

VARIANTS_URL = "https://manapool.com/api/v1/prices/variants"
USER_AGENT = "gitpro.be/1.0"

FINISH_BY_CODE = {
    "NF": "nonfoil",
    "FO": "foil",
    "EF": "etched",
}
CONDITIONS = frozenset({"NM", "LP", "MP", "HP"})


def mana_pool_price_rows(item: dict[str, Any], updated_at: datetime) -> list[PriceRow]:
    """Map one variants-feed record onto a single ``store_prices`` row."""

    product_type = item.get("product_type")
    if product_type not in (None, "mtg_single"):
        return []

    raw_id = item.get("scryfall_id")
    if not raw_id:
        return []
    try:
        scryfall_id = uuid.UUID(str(raw_id))
    except ValueError:
        return []

    condition = str(item.get("condition_id") or "").strip().upper()
    finish = FINISH_BY_CODE.get(str(item.get("finish_id") or "").strip().upper())
    if condition not in CONDITIONS or finish is None:
        return []

    low_price = item.get("low_price")
    if low_price in (None, ""):
        return []
    price_cents = int(low_price)
    if price_cents <= 0:
        return []

    quantity = item.get("available_quantity")
    stock_qty = 0 if quantity in (None, "") else max(0, int(quantity))
    sku = item.get("product_id")
    return [
        {
            "store_id": "mana_pool",
            "scryfall_id": scryfall_id,
            "finish": finish,
            "condition": condition,
            "price_cents": price_cents,
            "stock_qty": stock_qty,
            "store_sku": None if sku in (None, "") else str(sku),
            "updated_at": updated_at,
        }
    ]


class ManaPoolAdapter(StoreAdapter):
    store_id = "mana_pool"
    feed_url = VARIANTS_URL
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }

    def map_item(self, item: dict[str, Any], updated_at: datetime) -> list[PriceRow]:
        return mana_pool_price_rows(item, updated_at)
