"""Card Kingdom public pricelist sync."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from app.services.stores.base import PriceRow, StoreAdapter

PRICELIST_URL = "https://api.cardkingdom.com/api/v2/pricelist"
USER_AGENT = "gitpro.be/1.0"

# Card Kingdom grades mapped onto the blueprint's normalized conditions.
CK_CONDITION_MAP = {
    "NM": "NM",
    "EX": "LP",
    "VG": "MP",
    "G": "HP",
}

_CONDITION_FIELDS = (
    ("nm", "NM"),
    ("ex", "EX"),
    ("vg", "VG"),
    ("g", "G"),
)


def map_ck_condition(raw: str | None) -> str | None:
    if raw is None:
        return None
    return CK_CONDITION_MAP.get(raw.strip().upper())


def dollars_to_cents(value: object) -> int:
    amount = Decimal(str(value).strip())
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def _as_int(value: object) -> int:
    if value in (None, ""):
        return 0
    return max(0, int(str(value).strip()))


def _price_row(
    *,
    scryfall_id: uuid.UUID,
    finish: str,
    condition: str,
    price_cents: int,
    stock_qty: int,
    store_sku: str | None,
    updated_at: datetime,
) -> PriceRow:
    return {
        "store_id": "card_kingdom",
        "scryfall_id": scryfall_id,
        "finish": finish,
        "condition": condition,
        "price_cents": price_cents,
        "stock_qty": stock_qty,
        "store_sku": store_sku,
        "updated_at": updated_at,
    }


def ck_price_rows(item: dict[str, Any], updated_at: datetime) -> list[PriceRow]:
    """Expand one pricelist product into normalized ``store_prices`` rows."""

    raw_id = item.get("scryfall_id")
    if not raw_id:
        return []
    try:
        scryfall_id = uuid.UUID(str(raw_id))
    except ValueError:
        return []

    finish = "foil" if _as_bool(item.get("is_foil")) else "nonfoil"
    sku = item.get("sku")
    if sku in (None, ""):
        sku = item.get("id")
    store_sku = None if sku in (None, "") else str(sku)
    rows: list[PriceRow] = []
    condition_values = item.get("condition_values") or {}
    if condition_values:
        for prefix, raw_condition in _CONDITION_FIELDS:
            condition = map_ck_condition(raw_condition)
            price = condition_values.get(f"{prefix}_price")
            if condition is None or price in (None, ""):
                continue
            price_cents = dollars_to_cents(price)
            if price_cents <= 0:
                continue
            rows.append(
                _price_row(
                    scryfall_id=scryfall_id,
                    finish=finish,
                    condition=condition,
                    price_cents=price_cents,
                    stock_qty=_as_int(condition_values.get(f"{prefix}_qty")),
                    store_sku=store_sku,
                    updated_at=updated_at,
                )
            )
        return rows

    condition = map_ck_condition(str(item.get("condition") or ""))
    price = item.get("price_retail")
    if condition is None or price in (None, ""):
        return []
    price_cents = dollars_to_cents(price)
    if price_cents <= 0:
        return []
    rows.append(
        _price_row(
            scryfall_id=scryfall_id,
            finish=finish,
            condition=condition,
            price_cents=price_cents,
            stock_qty=_as_int(item.get("qty_retail")),
            store_sku=store_sku,
            updated_at=updated_at,
        )
    )
    return rows


class CardKingdomAdapter(StoreAdapter):
    store_id = "card_kingdom"
    feed_url = PRICELIST_URL
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }

    def map_item(self, item: dict[str, Any], updated_at: datetime) -> list[PriceRow]:
        return ck_price_rows(item, updated_at)
