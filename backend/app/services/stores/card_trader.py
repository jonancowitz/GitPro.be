"""CardTrader Zero marketplace sync and US weight-step shipping.

Prices come from the official read API at ``https://api.cardtrader.com/api/v2``.
Blueprints supply the Scryfall id. ``GET /marketplace/products`` supplies the
hub listings. Only sellers with ``can_sell_via_hub`` are CardTrader Zero: one
consolidated US package, not a direct seller shipment.

The token is ``CARDTRADER_API_TOKEN`` (``Authorization: Bearer``). The account
currency must be USD; other currencies are skipped so euro cents are not stored
as dollars. A missing token skips this store and leaves the other syncs running.

US shipping is not a flat base plus a free threshold. It is the cheapest
published Zero method that can carry the card count. The plain-envelope rate
also requires singles (this adapter only ingests singles) and a contents value
of at most $27.48. Rates are the en-US Zero table: about 12 cards is 329 cents,
about 94 is 1366, about 194 is 1739. A 100-card deck falls in the 1739 bracket.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.stores.base import PRICE_BATCH_SIZE, PriceRow, StoreAdapter

logger = logging.getLogger(__name__)

CARD_TRADER_STORE_ID = "card_trader"
API_ROOT = "https://api.cardtrader.com/api/v2"
CARD_TRADER_ZERO_URL = "https://www.cardtrader.com/en-US/pages/zero"
USER_AGENT = "gitpro.be/1.0"
MAGIC_GAME_ID = 1
ENVELOPE_MAX_CARDS = 12
ENVELOPE_MAX_SUBTOTAL_CENTS = 2748
MARKETPLACE_PAUSE_SECONDS = 0.12

@dataclass(frozen=True, slots=True)
class ShippingStep:
    """One US Zero method. ``max_subtotal_cents`` is the envelope value cap."""

    max_cards: int
    shipping_cents: int
    max_subtotal_cents: int | None = None


# Cheapest non-dominated US Zero method at each published card-count cap.
# Heavier orders stay on the last rate so a large list can still be covered.
CARD_TRADER_US_SHIPPING_STEPS: tuple[ShippingStep, ...] = (
    ShippingStep(ENVELOPE_MAX_CARDS, 329, ENVELOPE_MAX_SUBTOTAL_CENTS),
    ShippingStep(94, 1366),
    ShippingStep(194, 1739),
    ShippingStep(644, 2608),
    ShippingStep(1244, 3353),
    ShippingStep(1744, 3726),
    ShippingStep(2494, 5836),
    ShippingStep(3494, 7203),
    ShippingStep(4994, 11674),
    ShippingStep(7494, 25235),
    ShippingStep(9994, 33319),
    ShippingStep(14994, 48470),
    ShippingStep(19994, 75369),
    ShippingStep(100_000, 111408),
)

# CardTrader grades mapped onto the blueprint's NM / LP / MP / HP scale.
# "Played" is worse than Moderately Played, so it is HP. Poor is below HP and
# is dropped rather than sold as Heavily Played.
CT_CONDITION_MAP = {
    "mint": "NM",
    "near mint": "NM",
    "nm": "NM",
    "slightly played": "LP",
    "lightly played": "LP",
    "sp": "LP",
    "lp": "LP",
    "moderately played": "MP",
    "mp": "MP",
    "played": "HP",
    "pl": "HP",
    "heavily played": "HP",
    "hp": "HP",
}


def map_card_trader_condition(raw: str | None) -> str | None:
    if raw is None:
        return None
    return CT_CONDITION_MAP.get(raw.strip().lower())


def shipping_cents_for_steps(
    steps: tuple[ShippingStep, ...],
    card_count: int,
    subtotal_cents: int,
) -> int:
    """Cheapest step that can carry this many cards at this merchandise value."""

    if card_count <= 0 or not steps:
        return 0
    eligible = [
        step.shipping_cents
        for step in steps
        if card_count <= step.max_cards
        and (step.max_subtotal_cents is None or subtotal_cents <= step.max_subtotal_cents)
    ]
    if not eligible:
        return steps[-1].shipping_cents
    return min(eligible)


def card_trader_shipping_cents(card_count: int, subtotal_cents: int) -> int:
    return shipping_cents_for_steps(CARD_TRADER_US_SHIPPING_STEPS, card_count, subtotal_cents)


def card_trader_checkout_url() -> str:
    """Public CardTrader Zero page. The copy-paste list is the mass-entry handoff."""

    return CARD_TRADER_ZERO_URL


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


def _price_cents(item: dict[str, Any]) -> int | None:
    price = item.get("price")
    if isinstance(price, dict):
        currency = str(price.get("currency") or "USD").strip().upper()
        if currency != "USD":
            return None
        raw = price.get("cents")
    else:
        raw = item.get("price_cents")
        currency = str(item.get("price_currency") or item.get("currency") or "USD").strip().upper()
        if currency != "USD":
            return None
    if raw in (None, ""):
        return None
    cents = int(str(raw).strip())
    if cents <= 0:
        return None
    return cents


def _finish(properties: dict[str, Any]) -> str | None:
    if _as_bool(properties.get("signed")) or _as_bool(properties.get("altered")):
        return None
    if _as_bool(properties.get("mtg_etched")) or _as_bool(properties.get("etched")):
        return "etched"
    if _as_bool(properties.get("mtg_foil")):
        return "foil"
    return "nonfoil"


def card_trader_price_rows(item: dict[str, Any], updated_at: datetime) -> list[PriceRow]:
    """Map one marketplace product into a ``store_prices`` row.

    ``scryfall_id`` is joined from the blueprint before this is called. The
    product id is the SKU used by the cart API. Direct (non-hub) listings,
    graded cards, bundles, and non-English copies are left out.
    """

    user = item.get("user") if isinstance(item.get("user"), dict) else {}
    if not _as_bool(user.get("can_sell_via_hub")):
        return []
    if _as_bool(item.get("on_vacation")) or _as_bool(item.get("graded")):
        return []
    bundle = item.get("bundle_size")
    if bundle not in (None, "", 1, "1"):
        return []

    raw_id = item.get("scryfall_id")
    if not raw_id:
        return []
    try:
        scryfall_id = uuid.UUID(str(raw_id))
    except ValueError:
        return []

    properties = item.get("properties_hash") if isinstance(item.get("properties_hash"), dict) else {}
    language = str(properties.get("mtg_language") or "en").strip().lower()
    if language != "en":
        return []
    raw_condition = properties.get("condition")
    condition = map_card_trader_condition(None if raw_condition is None else str(raw_condition))
    finish = _finish(properties)
    price_cents = _price_cents(item)
    if condition is None or finish is None or price_cents is None:
        return []

    sku = item.get("id")
    return [
        {
            "store_id": CARD_TRADER_STORE_ID,
            "scryfall_id": scryfall_id,
            "finish": finish,
            "condition": condition,
            "price_cents": price_cents,
            "stock_qty": _as_int(item.get("quantity")),
            "store_sku": None if sku in (None, "") else str(sku),
            "updated_at": updated_at,
        }
    ]


class CardTraderAdapter(StoreAdapter):
    store_id = CARD_TRADER_STORE_ID
    feed_url = f"{API_ROOT}/marketplace/products"
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }

    def __init__(
        self,
        *,
        token: str | None = None,
        client: httpx.Client | None = None,
        pause_seconds: float = MARKETPLACE_PAUSE_SECONDS,
    ) -> None:
        self._token = token
        self._client = client
        self.pause_seconds = pause_seconds

    @property
    def token(self) -> str:
        if self._token is not None:
            return self._token
        return settings.cardtrader_api_token

    def map_item(self, item: dict[str, Any], updated_at: datetime) -> list[PriceRow]:
        return card_trader_price_rows(item, updated_at)

    async def sync(self, session: AsyncSession, *, batch_size: int = PRICE_BATCH_SIZE) -> int:
        if not self.token.strip():
            logger.warning("CARDTRADER_API_TOKEN is not set; skipping CardTrader Zero price sync.")
            return 0
        return await super().sync(session, batch_size=batch_size)

    def iter_items(self) -> Iterator[dict[str, Any]]:
        token = self.token.strip()
        if not token:
            return
        yield from self._iter_marketplace(token)

    def _iter_marketplace(self, token: str) -> Iterator[dict[str, Any]]:
        owns_client = self._client is None
        client = self._client or httpx.Client(
            timeout=httpx.Timeout(60.0, read=120.0),
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
            },
            follow_redirects=True,
        )
        try:
            expansions = _expect_list(self._get_json(client, "/expansions"), "expansions")
            for expansion in expansions:
                if not isinstance(expansion, dict) or expansion.get("game_id") != MAGIC_GAME_ID:
                    continue
                expansion_id = expansion.get("id")
                if expansion_id in (None, ""):
                    continue
                yield from self._iter_expansion(client, expansion_id)
        finally:
            if owns_client:
                client.close()

    def _iter_expansion(self, client: httpx.Client, expansion_id: object) -> Iterator[dict[str, Any]]:
        blueprints = self._get_json(client, "/blueprints/export", {"expansion_id": expansion_id})
        if blueprints is None:
            return
        scryfall_by_blueprint: dict[str, str] = {}
        for blueprint in _expect_list(blueprints, "blueprints"):
            if not isinstance(blueprint, dict):
                continue
            raw_id = blueprint.get("scryfall_id")
            blueprint_id = blueprint.get("id")
            if raw_id in (None, "") or blueprint_id in (None, ""):
                continue
            scryfall_by_blueprint[str(blueprint_id)] = str(raw_id)
        if not scryfall_by_blueprint:
            return

        payload = self._get_json(
            client,
            "/marketplace/products",
            {"expansion_id": expansion_id, "language": "en"},
        )
        if self.pause_seconds > 0:
            time.sleep(self.pause_seconds)
        if payload is None:
            return
        if not isinstance(payload, dict):
            raise ValueError("CardTrader marketplace response was not an object")
        if "error" in payload:
            raise RuntimeError(f"CardTrader marketplace error: {payload['error']}")
        for blueprint_key, listings in payload.items():
            scryfall_id = scryfall_by_blueprint.get(str(blueprint_key))
            if not scryfall_id or not isinstance(listings, list):
                continue
            for listing in listings:
                if isinstance(listing, dict):
                    yield {**listing, "scryfall_id": scryfall_id}

    def _get_json(self, client: httpx.Client, path: str, params: dict[str, Any] | None = None) -> Any:
        response = client.get(
            f"{API_ROOT}{path}",
            params=params,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Authorization": f"Bearer {self.token.strip()}",
            },
        )
        if response.status_code == 404:
            logger.info("CardTrader %s returned 404; skipping.", path)
            return None
        response.raise_for_status()
        return response.json()


def _expect_list(payload: Any, label: str) -> list[Any]:
    if not isinstance(payload, list):
        raise ValueError(f"CardTrader {label} response was not a list")
    return payload
