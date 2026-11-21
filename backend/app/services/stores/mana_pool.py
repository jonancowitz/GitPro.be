"""Mana Pool price sync, live cart optimizer, and checkout handoff.

The variants feed is the snapshot used for offline pruning. Stage 3 calls
Mana Pool's buyer cart optimizer (``POST /api/v1/buyer/optimizer``) for the
cards a solution routes here. The response is cached in Redis for 10 minutes.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import httpx

from app.services.stores.base import PriceRow, StoreAdapter

VARIANTS_URL = "https://manapool.com/api/v1/prices/variants"
CART_OPTIMIZER_URL = "https://manapool.com/api/v1/buyer/optimizer"
ADD_DECK_URL = "https://manapool.com/add-deck"
USER_AGENT = "gitpro.be/1.0"
LIVE_CACHE_TTL_SECONDS = 600
LIVE_TIMEOUT_SECONDS = 2.5

FINISH_TO_ID = {"nonfoil": "NF", "foil": "FO", "etched": "EF"}
TIMEOUT_NOTICE = (
    "Mana Pool's live cart optimizer timed out, so these prices are from the last inventory sync."
)
UNAVAILABLE_NOTICE = (
    "Mana Pool live prices are unavailable, so these prices are from the last inventory sync."
)
PARTIAL_NOTICE = (
    "Mana Pool's live cart could not cover every card, so these prices are from the last inventory sync."
)

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


@dataclass(frozen=True, slots=True)
class LiveCartLine:
    name: str
    scryfall_id: str
    quantity: int
    finish: str
    condition: str


@dataclass(frozen=True, slots=True)
class LiveQuote:
    subtotal_cents: int
    shipping_cents: int
    package_count: int

    def to_json(self) -> str:
        return json.dumps(
            {
                "subtotal_cents": self.subtotal_cents,
                "shipping_cents": self.shipping_cents,
                "package_count": self.package_count,
            },
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, raw: str) -> LiveQuote:
        payload = json.loads(raw)
        return cls(
            subtotal_cents=int(payload["subtotal_cents"]),
            shipping_cents=int(payload["shipping_cents"]),
            package_count=int(payload["package_count"]),
        )


class LiveQuoteError(Exception):
    """The live cart response cannot replace the snapshot prices."""

    def __init__(self, notice: str) -> None:
        super().__init__(notice)
        self.notice = notice


def mana_pool_checkout_url(affiliate_tag: str | None) -> str:
    if affiliate_tag:
        return f"{ADD_DECK_URL}?{urlencode({'ref': affiliate_tag})}"
    return ADD_DECK_URL


def live_cache_key(lines: Sequence[LiveCartLine], min_condition: str) -> str:
    """Hash the sorted card list plus condition and finish."""

    payload = {
        "min_condition": min_condition,
        "lines": [
            {
                "condition": line.condition,
                "finish": line.finish,
                "name": line.name,
                "quantity": line.quantity,
                "scryfall_id": line.scryfall_id,
            }
            for line in sorted(
                lines,
                key=lambda line: (line.name, line.scryfall_id, line.finish, line.condition, line.quantity),
            )
        ],
    }
    digest = hashlib.sha256(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()).hexdigest()
    return f"manapool:cart-optimizer:{digest}"


def build_optimizer_body(lines: Sequence[LiveCartLine], conditions: Sequence[str]) -> dict[str, Any]:
    grouped: dict[tuple[str, str], int] = {}
    for line in lines:
        key = (line.name, line.finish)
        grouped[key] = grouped.get(key, 0) + line.quantity
    cart = []
    for name, finish in sorted(grouped):
        cart.append(
            {
                "type": "mtg_single",
                "name": name,
                "quantity_requested": grouped[(name, finish)],
                "finish_ids": [FINISH_TO_ID[finish]],
                "condition_ids": list(conditions),
            }
        )
    return {"cart": cart}


def parse_optimizer_payload(text: str, *, requested_quantity: int) -> LiveQuote:
    """Read a JSON object or the last JSON line of a streamed optimizer body."""

    data = _decode_optimizer_body(text)
    totals = data.get("totals") if isinstance(data.get("totals"), dict) else data
    try:
        subtotal = int(totals["subtotal_cents"])
        shipping = int(totals["shipping_cents"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LiveQuoteError(UNAVAILABLE_NOTICE) from exc
    package_count = totals.get("seller_count", totals.get("package_count", 1))
    try:
        packages = int(package_count)
    except (TypeError, ValueError) as exc:
        raise LiveQuoteError(UNAVAILABLE_NOTICE) from exc

    cart = data.get("cart")
    if isinstance(cart, list):
        selected = 0
        for item in cart:
            if not isinstance(item, dict):
                continue
            try:
                selected += int(item.get("quantity_selected") or 0)
            except (TypeError, ValueError) as exc:
                raise LiveQuoteError(UNAVAILABLE_NOTICE) from exc
        if selected < requested_quantity:
            raise LiveQuoteError(PARTIAL_NOTICE)
    return LiveQuote(subtotal_cents=subtotal, shipping_cents=shipping, package_count=packages)


def _decode_optimizer_body(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise LiveQuoteError(UNAVAILABLE_NOTICE)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        return data
    for line in reversed(stripped.splitlines()):
        candidate = line.strip()
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise LiveQuoteError(UNAVAILABLE_NOTICE)


async def fetch_live_quote(
    lines: Sequence[LiveCartLine],
    client: httpx.AsyncClient,
    *,
    email: str,
    access_token: str,
    conditions: Sequence[str],
) -> LiveQuote:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-ManaPool-Email": email,
        "X-ManaPool-Access-Token": access_token,
    }
    timeout = httpx.Timeout(LIVE_TIMEOUT_SECONDS)
    try:
        response = await client.post(
            CART_OPTIMIZER_URL,
            json=build_optimizer_body(lines, conditions),
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise TimeoutError(TIMEOUT_NOTICE) from exc
    return parse_optimizer_payload(response.text, requested_quantity=sum(line.quantity for line in lines))


async def cached_live_quote(lines: Sequence[LiveCartLine], redis: Any, fetch: Any, min_condition: str) -> LiveQuote:
    key = live_cache_key(lines, min_condition)
    if redis is not None:
        cached = await _redis_get(redis, key)
        if cached:
            try:
                return LiveQuote.from_json(cached)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                pass
    quote = await fetch(lines)
    if redis is not None:
        await _redis_set(redis, key, quote.to_json(), LIVE_CACHE_TTL_SECONDS)
    return quote


async def _redis_get(redis: Any, key: str) -> str | None:
    try:
        return await redis.get(key)
    except Exception:
        return None


async def _redis_set(redis: Any, key: str, value: str, ttl: int) -> None:
    try:
        await redis.set(key, value, ex=ttl)
    except Exception:
        return None
