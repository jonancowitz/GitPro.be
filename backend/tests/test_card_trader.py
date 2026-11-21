"""CardTrader Zero mapping, missing-token skip, and US weight-step shipping."""

from __future__ import annotations

import asyncio
import logging
import uuid

import httpx
import pytest

from app.core.config import settings
from app.schemas.optimize import OptimizeItem
from app.services.catalog import CardFact, InventoryRow
from app.services.optimizer import StoreTerms, plan_purchase
from app.services.stores.card_trader import (
    CARD_TRADER_ZERO_URL,
    USER_AGENT,
    CardTraderAdapter,
    card_trader_price_rows,
    card_trader_shipping_cents,
    map_card_trader_condition,
)
from app.services.stores.card_kingdom import CardKingdomAdapter
from app.services.stores.mana_pool import ManaPoolAdapter
from app.services.stores.seed import SEED_STORES
from app.workers import sync_stores as sync_stores_module
from tests.test_milestone2_sync import UPDATED_AT

SCRYFALL_ID = "a363bc91-8278-448e-9d5c-564e4b51eb62"


def _listing(**overrides: object) -> dict:
    listing: dict = {
        "id": 101862104,
        "blueprint_id": 89,
        "name_en": "Sol Ring",
        "quantity": 4,
        "price": {"cents": 250, "currency": "USD"},
        "properties_hash": {
            "condition": "Near Mint",
            "mtg_foil": False,
            "mtg_language": "en",
            "signed": False,
            "altered": False,
        },
        "user": {"id": 1, "username": "hub-seller", "can_sell_via_hub": True},
        "graded": False,
        "on_vacation": False,
        "bundle_size": 1,
        "scryfall_id": SCRYFALL_ID,
    }
    listing.update(overrides)
    return listing


def test_condition_and_price_mapping() -> None:
    assert map_card_trader_condition("Near Mint") == "NM"
    assert map_card_trader_condition("Slightly Played") == "LP"
    assert map_card_trader_condition("Moderately Played") == "MP"
    assert map_card_trader_condition("Heavily Played") == "HP"
    assert map_card_trader_condition("Played") == "HP"
    assert map_card_trader_condition("Poor") is None

    near_mint = card_trader_price_rows(_listing(), UPDATED_AT)
    lightly = card_trader_price_rows(
        _listing(
            id=2,
            quantity=12,
            price={"cents": 80, "currency": "USD"},
            properties_hash={"condition": "Slightly Played", "mtg_foil": False, "mtg_language": "en"},
        ),
        UPDATED_AT,
    )
    moderate_foil = card_trader_price_rows(
        _listing(
            id=3,
            quantity=7,
            price={"cents": 125, "currency": "USD"},
            properties_hash={"condition": "Moderately Played", "mtg_foil": True, "mtg_language": "en"},
        ),
        UPDATED_AT,
    )
    heavy = card_trader_price_rows(
        _listing(
            id=4,
            quantity=0,
            price={"cents": 40, "currency": "USD"},
            properties_hash={"condition": "Heavily Played", "mtg_foil": False, "mtg_language": "en"},
        ),
        UPDATED_AT,
    )

    assert near_mint == [
        {
            "store_id": "card_trader",
            "scryfall_id": uuid.UUID(SCRYFALL_ID),
            "finish": "nonfoil",
            "condition": "NM",
            "price_cents": 250,
            "stock_qty": 4,
            "store_sku": "101862104",
            "updated_at": UPDATED_AT,
        }
    ]
    assert lightly[0]["condition"] == "LP"
    assert lightly[0]["price_cents"] == 80
    assert lightly[0]["stock_qty"] == 12
    assert moderate_foil[0]["condition"] == "MP"
    assert moderate_foil[0]["finish"] == "foil"
    assert moderate_foil[0]["price_cents"] == 125
    assert heavy[0]["condition"] == "HP"
    assert heavy[0]["stock_qty"] == 0
    assert card_trader_price_rows(_listing(properties_hash={"condition": "Poor", "mtg_language": "en"}), UPDATED_AT) == []
    assert card_trader_price_rows(_listing(user={"can_sell_via_hub": False}), UPDATED_AT) == []
    assert card_trader_price_rows(_listing(price={"cents": 250, "currency": "EUR"}), UPDATED_AT) == []
    assert card_trader_price_rows(_listing(scryfall_id=None), UPDATED_AT) == []


def test_marketplace_read_joins_scryfall_id_and_keeps_hub_listings() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-token"
        assert request.headers["User-Agent"] == USER_AGENT
        assert request.url.host == "api.cardtrader.com"
        assert request.url.path.startswith("/api/v2/")
        if request.url.path.endswith("/expansions"):
            return httpx.Response(
                200,
                json=[
                    {"id": 8, "game_id": 1, "code": "c18", "name": "Commander 2018"},
                    {"id": 99, "game_id": 99, "code": "other", "name": "Not Magic"},
                ],
            )
        if request.url.path.endswith("/blueprints/export"):
            assert request.url.params["expansion_id"] == "8"
            return httpx.Response(200, json=[{"id": 89, "name": "Sol Ring", "scryfall_id": SCRYFALL_ID}])
        if request.url.path.endswith("/marketplace/products"):
            calls.append(request.url.params["expansion_id"])
            assert request.url.params["language"] == "en"
            direct = _listing(id=2, user={"can_sell_via_hub": False}, price={"cents": 10, "currency": "USD"})
            direct.pop("scryfall_id")
            hub = _listing()
            hub.pop("scryfall_id")
            return httpx.Response(200, json={"89": [direct, hub]})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = CardTraderAdapter(token="test-token", client=client, pause_seconds=0)
    rows = []
    for item in adapter.iter_items():
        rows.extend(card_trader_price_rows(item, UPDATED_AT))

    assert calls == ["8"]
    assert rows == [
        {
            "store_id": "card_trader",
            "scryfall_id": uuid.UUID(SCRYFALL_ID),
            "finish": "nonfoil",
            "condition": "NM",
            "price_cents": 250,
            "stock_qty": 4,
            "store_sku": "101862104",
            "updated_at": UPDATED_AT,
        }
    ]


def test_missing_token_skips_without_http(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    def fail_client(*_args: object, **_kwargs: object) -> httpx.Client:
        raise AssertionError("CardTrader sync should not open an HTTP client without a token")

    monkeypatch.setattr("app.services.stores.card_trader.httpx.Client", fail_client)
    adapter = CardTraderAdapter(token="")
    with caplog.at_level(logging.WARNING):
        written = asyncio.run(adapter.sync(None))  # type: ignore[arg-type]

    assert written == 0
    assert "CARDTRADER_API_TOKEN is not set; skipping CardTrader Zero price sync." in caplog.text


def test_missing_token_does_not_stop_other_stores(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    class _Session:
        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *_args: object) -> bool:
            return False

        async def commit(self) -> None:
            return None

    seen: list[str] = []

    async def fail_kingdom(self, session: object, *, batch_size: int = 500) -> int:
        raise RuntimeError("card kingdom down")

    async def mana_pool(self, session: object, *, batch_size: int = 500) -> int:
        seen.append(self.store_id)
        return 4

    monkeypatch.setattr(sync_stores_module, "SessionLocal", lambda: _Session())

    async def _seed(_session: object) -> None:
        return None

    monkeypatch.setattr(sync_stores_module, "seed_stores", _seed)
    monkeypatch.setattr(CardKingdomAdapter, "sync", fail_kingdom)
    monkeypatch.setattr(ManaPoolAdapter, "sync", mana_pool)
    monkeypatch.setattr(settings, "cardtrader_api_token", "")

    with caplog.at_level(logging.WARNING):
        counts = asyncio.run(sync_stores_module.sync_stores())

    assert seen == ["mana_pool"]
    assert counts == {"card_kingdom": 0, "mana_pool": 4, "card_trader": 0}
    assert "CARDTRADER_API_TOKEN is not set" in caplog.text
    assert "Price sync failed for card_kingdom" in caplog.text


def test_seed_row_is_card_trader_zero() -> None:
    stores = {row["id"]: row for row in SEED_STORES}
    card_trader = stores["card_trader"]
    assert card_trader["name"] == "CardTrader Zero"
    assert card_trader["store_type"] == "consolidated"
    assert card_trader["base_shipping_cents"] == 0
    assert card_trader["free_shipping_threshold_cents"] is None
    assert card_trader["enabled"] is True


def test_weight_step_rates_for_12_94_and_100_cards() -> None:
    assert card_trader_shipping_cents(12, 2748) == 329
    assert card_trader_shipping_cents(12, 2749) == 1366
    assert card_trader_shipping_cents(94, 50_000) == 1366
    assert card_trader_shipping_cents(95, 100) == 1739
    assert card_trader_shipping_cents(100, 100) == 1739


def _fact(scryfall_id: uuid.UUID, oracle_id: uuid.UUID, name: str, collector_number: str) -> CardFact:
    return CardFact(
        scryfall_id=scryfall_id,
        oracle_id=oracle_id,
        name=name,
        set_code="ltc",
        collector_number=collector_number,
        is_basic_land=False,
        image_uri=None,
    )


def _row(
    store_id: str,
    scryfall_id: uuid.UUID,
    oracle_id: uuid.UUID,
    price: int,
    stock: int,
    *,
    name: str,
    collector_number: str,
) -> InventoryRow:
    return InventoryRow(
        store_id=store_id,
        scryfall_id=scryfall_id,
        oracle_id=oracle_id,
        name=name,
        set_code="ltc",
        collector_number=collector_number,
        finish="nonfoil",
        condition="NM",
        unit_price_cents=price,
        stock_qty=stock,
        store_sku="sku",
        image_uri=None,
    )


def _item(oracle_id: uuid.UUID, scryfall_id: uuid.UUID) -> OptimizeItem:
    return OptimizeItem(oracle_id=oracle_id, scryfall_id=scryfall_id, quantity=1, finish="nonfoil")


def _deck(count: int, prices: dict[str, int]):
    facts = []
    rows = []
    items = []
    for index in range(count):
        oracle_id = uuid.UUID(int=index + 1)
        scryfall_id = uuid.UUID(int=index + 10_000)
        name = f"Card {index + 1}"
        number = str(index + 1)
        facts.append(_fact(scryfall_id, oracle_id, name, number))
        for store_id, price in prices.items():
            rows.append(
                _row(store_id, scryfall_id, oracle_id, price, count, name=name, collector_number=number)
            )
        items.append(_item(oracle_id, scryfall_id))
    return items, facts, rows


def _zero(**overrides: object) -> StoreTerms:
    data: dict = {
        "store_id": "card_trader",
        "name": "CardTrader Zero",
        "store_type": "consolidated",
        "base_shipping_cents": 9999,
        "free_shipping_threshold_cents": 100,
    }
    data.update(overrides)
    return StoreTerms(**data)


def _kingdom(price_shipping: int = 0) -> StoreTerms:
    return StoreTerms(
        store_id="card_kingdom",
        name="Card Kingdom",
        store_type="single_warehouse",
        base_shipping_cents=price_shipping,
        free_shipping_threshold_cents=None,
    )


def _bucket(response, store_id: str):
    matches = [bucket for bucket in response.buckets if bucket.store_id == store_id]
    assert len(matches) == 1
    return matches[0]


def test_optimizer_charges_weight_steps_for_12_94_and_100_cards() -> None:
    dozen = plan_purchase(*_deck(12, {"card_trader": 100}), [_zero()], package_penalty_cents=0)
    dozen_bucket = _bucket(dozen, "card_trader")
    assert sum(card.quantity for card in dozen_bucket.cards) == 12
    assert dozen_bucket.subtotal_cents == 1200
    assert dozen_bucket.shipping_cents == 329
    assert dozen_bucket.free_shipping_delta_cents is None
    assert dozen_bucket.estimated_packages == 1

    over_cap = plan_purchase(*_deck(12, {"card_trader": 230}), [_zero()], package_penalty_cents=0)
    assert _bucket(over_cap, "card_trader").subtotal_cents == 2760
    assert _bucket(over_cap, "card_trader").shipping_cents == 1366

    ninety_four = plan_purchase(*_deck(94, {"card_trader": 50}), [_zero()], package_penalty_cents=0)
    assert sum(card.quantity for card in _bucket(ninety_four, "card_trader").cards) == 94
    assert _bucket(ninety_four, "card_trader").shipping_cents == 1366

    hundred = plan_purchase(*_deck(100, {"card_trader": 40}), [_zero()], package_penalty_cents=0)
    hundred_bucket = _bucket(hundred, "card_trader")
    assert len(hundred_bucket.cards) == 100
    assert sum(card.quantity for card in hundred_bucket.cards) == 100
    assert hundred_bucket.shipping_cents == 1739
    assert hundred_bucket.checkout_url == CARD_TRADER_ZERO_URL
    assert "tcgplayer" not in hundred_bucket.checkout_url.lower()
    assert hundred_bucket.mass_entry_text.startswith("1 Card 1 (LTC) 1\n")
    assert "tcgplayer" not in hundred_bucket.mass_entry_text.lower()


def test_weight_step_changes_which_store_wins() -> None:
    envelope_wins = plan_purchase(
        *_deck(12, {"card_trader": 100, "card_kingdom": 140}),
        [_zero(base_shipping_cents=0, free_shipping_threshold_cents=None), _kingdom()],
        package_penalty_cents=0,
    )
    assert [bucket.store_id for bucket in envelope_wins.buckets] == ["card_trader"]
    assert _bucket(envelope_wins, "card_trader").shipping_cents == 329

    over_value_cap = plan_purchase(
        *_deck(12, {"card_trader": 230, "card_kingdom": 320}),
        [_zero(base_shipping_cents=0, free_shipping_threshold_cents=None), _kingdom()],
        package_penalty_cents=0,
    )
    covered = sum(card.quantity for bucket in over_value_cap.buckets for card in bucket.cards)
    zero_bucket = next(bucket for bucket in over_value_cap.buckets if bucket.store_id == "card_trader")
    assert covered == 12
    assert sum(card.quantity for card in zero_bucket.cards) < 12
    assert zero_bucket.subtotal_cents <= 2748
    assert zero_bucket.shipping_cents == 329

    parcel_would_lose = plan_purchase(
        *_deck(12, {"card_trader": 100, "card_kingdom": 110}),
        [_zero(base_shipping_cents=0, free_shipping_threshold_cents=None), _kingdom()],
        package_penalty_cents=0,
    )
    assert [bucket.store_id for bucket in parcel_would_lose.buckets] == ["card_kingdom"]
    assert _bucket(parcel_would_lose, "card_kingdom").shipping_cents == 0
