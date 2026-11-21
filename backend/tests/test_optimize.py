"""Milestone 4 optimizer: pruning, free shipping, package penalty, live Mana Pool."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from app.core.database import get_session
from app.main import app
from app.models.card import Card
from app.models.store import Store
from app.models.store_price import StorePrice
from app.schemas.optimize import OptimizeItem, OptimizeRequest
from app.services.catalog import CardFact, InventoryRow, build_offer_statement
from app.services.optimizer import plan_purchase, verify_live_cart
from app.services.optimizer import optimize_deck
from app.services.stores.mana_pool import (
    LIVE_CACHE_TTL_SECONDS,
    PARTIAL_NOTICE,
    TIMEOUT_NOTICE,
    UNAVAILABLE_NOTICE,
    LiveCartLine,
    LiveQuote,
    LiveQuoteError,
    live_cache_key,
    parse_optimizer_payload,
)
from app.services.optimizer import StoreTerms

ORACLE = uuid.UUID("11111111-1111-4111-8111-111111111111")
PRINT_A = uuid.UUID("22222222-2222-4222-8222-222222222222")
PRINT_B = uuid.UUID("33333333-3333-4333-8333-333333333333")
OTHER_ORACLE = uuid.UUID("44444444-4444-4444-8444-444444444444")
OTHER_PRINT = uuid.UUID("55555555-5555-4555-8555-555555555555")
BASIC_ORACLE = uuid.UUID("66666666-6666-4666-8666-666666666666")
BASIC_PRINT = uuid.UUID("77777777-7777-4777-8777-777777777777")
UNKNOWN = uuid.UUID("88888888-8888-4888-8888-888888888888")


def _fact(
    scryfall_id: uuid.UUID,
    oracle_id: uuid.UUID,
    name: str,
    *,
    set_code: str = "ltc",
    collector_number: str = "284",
    is_basic_land: bool = False,
) -> CardFact:
    return CardFact(
        scryfall_id=scryfall_id,
        oracle_id=oracle_id,
        name=name,
        set_code=set_code,
        collector_number=collector_number,
        is_basic_land=is_basic_land,
        image_uri=None,
    )


def _row(
    store_id: str,
    scryfall_id: uuid.UUID,
    oracle_id: uuid.UUID,
    price: int,
    stock: int,
    *,
    name: str = "Sol Ring",
    set_code: str = "ltc",
    collector_number: str = "284",
    finish: str = "nonfoil",
    condition: str = "NM",
) -> InventoryRow:
    return InventoryRow(
        store_id=store_id,
        scryfall_id=scryfall_id,
        oracle_id=oracle_id,
        name=name,
        set_code=set_code,
        collector_number=collector_number,
        finish=finish,
        condition=condition,
        unit_price_cents=price,
        stock_qty=stock,
        store_sku=None,
        image_uri=None,
    )


def _item(
    oracle_id: uuid.UUID | None,
    scryfall_id: uuid.UUID | None,
    *,
    quantity: int = 1,
    finish: str = "nonfoil",
    printing_locked: bool = False,
) -> OptimizeItem:
    return OptimizeItem(
        oracle_id=oracle_id,
        scryfall_id=scryfall_id,
        quantity=quantity,
        finish=finish,  # type: ignore[arg-type]
        printing_locked=printing_locked,
    )


def _store(
    store_id: str,
    name: str,
    *,
    store_type: str = "single_warehouse",
    shipping: int = 0,
    threshold: int | None = None,
) -> StoreTerms:
    return StoreTerms(
        store_id=store_id,
        name=name,
        store_type=store_type,
        base_shipping_cents=shipping,
        free_shipping_threshold_cents=threshold,
    )


def _ck(**overrides: object) -> StoreTerms:
    data = {
        "store_id": "card_kingdom",
        "name": "Card Kingdom",
        "store_type": "single_warehouse",
        "shipping": 599,
        "threshold": 7500,
    }
    data.update(overrides)
    return _store(**data)  # type: ignore[arg-type]


def _mp(**overrides: object) -> StoreTerms:
    data = {
        "store_id": "mana_pool",
        "name": "Mana Pool",
        "store_type": "p2p",
        "shipping": 0,
        "threshold": None,
    }
    data.update(overrides)
    return _store(**data)  # type: ignore[arg-type]


def _bucket(response, store_id: str):
    matches = [bucket for bucket in response.buckets if bucket.store_id == store_id]
    assert len(matches) == 1
    return matches[0]


def test_free_shipping_threshold_pulls_cards_into_one_store() -> None:
    kingdom = _store("card_kingdom", "Card Kingdom", shipping=500, threshold=1000)
    bulk = _store("bulk", "Bulk Shop", shipping=0, threshold=None)
    facts = [
        _fact(PRINT_A, ORACLE, "Sol Ring"),
        _fact(OTHER_PRINT, OTHER_ORACLE, "Lightning Bolt", set_code="lea", collector_number="161"),
    ]
    rows = [
        _row("card_kingdom", PRINT_A, ORACLE, 800, 4),
        _row("card_kingdom", OTHER_PRINT, OTHER_ORACLE, 400, 4, name="Lightning Bolt", set_code="lea", collector_number="161"),
        _row("bulk", OTHER_PRINT, OTHER_ORACLE, 100, 4, name="Lightning Bolt", set_code="lea", collector_number="161"),
    ]
    items = [
        _item(ORACLE, PRINT_A),
        _item(OTHER_ORACLE, OTHER_PRINT),
    ]
    bundled = plan_purchase(items, facts, rows, [kingdom, bulk], package_penalty_cents=0)
    bucket = _bucket(bundled, "card_kingdom")
    assert [card.name for card in bucket.cards] == ["Sol Ring", "Lightning Bolt"]
    assert bucket.subtotal_cents == 1200
    assert bucket.shipping_cents == 0
    assert bucket.free_shipping_delta_cents == 0
    assert bundled.buckets == [bucket]

    open_shipping = _store("card_kingdom", "Card Kingdom", shipping=500, threshold=None)
    split = plan_purchase(items, facts, rows, [open_shipping, bulk], package_penalty_cents=0)
    assert _bucket(split, "card_kingdom").subtotal_cents == 800
    assert _bucket(split, "card_kingdom").shipping_cents == 500
    assert _bucket(split, "bulk").subtotal_cents == 100
    assert _bucket(split, "bulk").shipping_cents == 0


def test_free_shipping_is_not_granted_below_the_threshold() -> None:
    kingdom = _store("card_kingdom", "Card Kingdom", shipping=500, threshold=1000)
    response = plan_purchase(
        [_item(ORACLE, PRINT_A)],
        [_fact(PRINT_A, ORACLE, "Sol Ring")],
        [_row("card_kingdom", PRINT_A, ORACLE, 500, 2)],
        [kingdom],
        package_penalty_cents=0,
    )
    bucket = _bucket(response, "card_kingdom")
    assert bucket.subtotal_cents == 500
    assert bucket.shipping_cents == 500
    assert bucket.free_shipping_delta_cents == 500
    assert response.savings.optimized_total_cents == 1000
    assert response.savings.message == "Same total as buying everything at Card Kingdom"


def test_package_penalty_prefers_fewer_stores() -> None:
    kingdom = _store("card_kingdom", "Card Kingdom", shipping=500, threshold=None)
    pool = _mp(shipping=0)
    facts = [
        _fact(PRINT_A, ORACLE, "Sol Ring"),
        _fact(OTHER_PRINT, OTHER_ORACLE, "Lightning Bolt", set_code="lea", collector_number="161"),
    ]
    rows = [
        _row("card_kingdom", PRINT_A, ORACLE, 250, 4),
        _row("mana_pool", PRINT_A, ORACLE, 2000, 4),
        _row("card_kingdom", OTHER_PRINT, OTHER_ORACLE, 2000, 4, name="Lightning Bolt", set_code="lea", collector_number="161"),
        _row("mana_pool", OTHER_PRINT, OTHER_ORACLE, 250, 4, name="Lightning Bolt", set_code="lea", collector_number="161"),
    ]
    items = [_item(ORACLE, PRINT_A), _item(OTHER_ORACLE, OTHER_PRINT)]
    split = plan_purchase(items, facts, rows, [kingdom, pool], package_penalty_cents=0)
    assert {bucket.store_id for bucket in split.buckets} == {"card_kingdom", "mana_pool"}

    consolidated = plan_purchase(items, facts, rows, [kingdom, pool], package_penalty_cents=1500)
    assert [bucket.store_id for bucket in consolidated.buckets] == ["mana_pool"]
    assert consolidated.savings.optimized_total_cents == 2250


def test_cheap_peer_to_peer_lines_carry_a_solver_weight_only() -> None:
    response = plan_purchase(
        [_item(ORACLE, PRINT_A)],
        [_fact(PRINT_A, ORACLE, "Sol Ring")],
        [
            _row("card_kingdom", PRINT_A, ORACLE, 220, 4),
            _row("mana_pool", PRINT_A, ORACLE, 190, 4),
        ],
        [_ck(shipping=0, threshold=None), _mp()],
        package_penalty_cents=0,
    )
    bucket = _bucket(response, "card_kingdom")
    assert bucket.subtotal_cents == 220
    assert bucket.shipping_cents == 0


def test_printing_lock_ignores_a_cheaper_printing() -> None:
    facts = [
        _fact(PRINT_A, ORACLE, "Sol Ring", set_code="ltc", collector_number="284"),
        _fact(PRINT_B, ORACLE, "Sol Ring", set_code="cmm", collector_number="398"),
    ]
    rows = [
        _row("card_kingdom", PRINT_A, ORACLE, 500, 4, set_code="ltc", collector_number="284"),
        _row("card_kingdom", PRINT_A, ORACLE, 80, 4, finish="foil", set_code="ltc", collector_number="284"),
        _row("mana_pool", PRINT_B, ORACLE, 40, 4, set_code="cmm", collector_number="398"),
        _row("card_kingdom", PRINT_B, ORACLE, 100, 4, set_code="cmm", collector_number="398"),
    ]
    locked = plan_purchase(
        [_item(ORACLE, PRINT_A, printing_locked=True)],
        facts,
        rows,
        [_ck(shipping=0, threshold=None), _mp()],
        finish_mode="cheapest",
        package_penalty_cents=0,
    )
    card = _bucket(locked, "card_kingdom").cards[0]
    assert card.scryfall_id == PRINT_A
    assert card.finish == "nonfoil"
    assert card.unit_price_cents == 500

    unlocked = plan_purchase(
        [_item(ORACLE, PRINT_A, printing_locked=False)],
        facts,
        rows,
        [_ck(shipping=0, threshold=None), _mp()],
        finish_mode="cheapest",
        package_penalty_cents=0,
    )
    chosen = _bucket(unlocked, "mana_pool").cards[0]
    assert chosen.scryfall_id == PRINT_B
    assert chosen.unit_price_cents == 40


def test_locked_printing_out_of_stock_does_not_substitute() -> None:
    response = plan_purchase(
        [_item(ORACLE, PRINT_A, printing_locked=True)],
        [
            _fact(PRINT_A, ORACLE, "Sol Ring"),
            _fact(PRINT_B, ORACLE, "Sol Ring", set_code="cmm", collector_number="398"),
        ],
        [
            _row("card_kingdom", PRINT_A, ORACLE, 100, 0),
            _row("mana_pool", PRINT_B, ORACLE, 10, 8, set_code="cmm", collector_number="398"),
        ],
        [_ck(shipping=0, threshold=None), _mp()],
        package_penalty_cents=0,
    )
    assert response.buckets == []
    assert len(response.unmatched) == 1
    assert response.unmatched[0].reason == "out_of_stock"
    assert response.unmatched[0].name == "Sol Ring"


def test_out_of_stock_quantity_keeps_the_store_that_can_fill_it() -> None:
    facts = [
        _fact(PRINT_A, ORACLE, "Sol Ring"),
        _fact(OTHER_PRINT, OTHER_ORACLE, "Lightning Bolt", set_code="lea", collector_number="161"),
    ]
    rows = [
        _row("card_kingdom", PRINT_A, ORACLE, 10, 3),
        _row("mana_pool", PRINT_A, ORACLE, 50, 4),
        _row("card_kingdom", OTHER_PRINT, OTHER_ORACLE, 25, 1, name="Lightning Bolt", set_code="lea", collector_number="161"),
    ]
    response = plan_purchase(
        [
            _item(ORACLE, PRINT_A, quantity=4),
            _item(OTHER_ORACLE, OTHER_PRINT, quantity=4),
            _item(UNKNOWN, None),
        ],
        facts,
        rows,
        [_ck(shipping=0, threshold=None), _mp()],
        package_penalty_cents=0,
        exclude_basic_lands=True,
    )
    assert _bucket(response, "mana_pool").cards[0].scryfall_id == PRINT_A
    assert _bucket(response, "mana_pool").cards[0].quantity == 4
    reasons = {card.name: card.reason for card in response.unmatched}
    assert reasons["Lightning Bolt"] == "out_of_stock"
    assert reasons[None] == "unmatched"


def test_exclude_basic_lands_drops_them_without_calling_them_unmatched() -> None:
    facts = [
        _fact(BASIC_PRINT, BASIC_ORACLE, "Island", set_code="znr", collector_number="380", is_basic_land=True),
        _fact(PRINT_A, ORACLE, "Sol Ring"),
    ]
    rows = [
        _row("card_kingdom", BASIC_PRINT, BASIC_ORACLE, 5, 40, name="Island", set_code="znr", collector_number="380"),
        _row("card_kingdom", PRINT_A, ORACLE, 100, 4),
    ]
    items = [
        _item(BASIC_ORACLE, BASIC_PRINT, quantity=10),
        _item(ORACLE, PRINT_A),
    ]
    excluded = plan_purchase(items, facts, rows, [_ck(shipping=0, threshold=None)], package_penalty_cents=0)
    assert [card.name for card in excluded.unmatched] == []
    assert [card.name for card in _bucket(excluded, "card_kingdom").cards] == ["Sol Ring"]

    included = plan_purchase(
        items,
        facts,
        rows,
        [_ck(shipping=0, threshold=None)],
        package_penalty_cents=0,
        exclude_basic_lands=False,
    )
    assert [card.name for card in _bucket(included, "card_kingdom").cards] == ["Island", "Sol Ring"]


def test_finish_mode_matches_the_deck_or_takes_the_cheapest() -> None:
    facts = [_fact(PRINT_A, ORACLE, "Sol Ring")]
    rows = [
        _row("card_kingdom", PRINT_A, ORACLE, 300, 2, finish="nonfoil"),
        _row("card_kingdom", PRINT_A, ORACLE, 100, 2, finish="foil"),
    ]
    item = _item(ORACLE, PRINT_A, finish="nonfoil")
    stores = [_ck(shipping=0, threshold=None)]
    matched = plan_purchase([item], facts, rows, stores, finish_mode="match_deck", package_penalty_cents=0)
    assert _bucket(matched, "card_kingdom").cards[0].finish == "nonfoil"
    assert _bucket(matched, "card_kingdom").cards[0].unit_price_cents == 300

    cheapest = plan_purchase([item], facts, rows, stores, finish_mode="cheapest", package_penalty_cents=0)
    assert _bucket(cheapest, "card_kingdom").cards[0].finish == "foil"
    assert _bucket(cheapest, "card_kingdom").cards[0].unit_price_cents == 100


def test_min_condition_rejects_a_worse_copy() -> None:
    rows = [
        _row("card_kingdom", PRINT_A, ORACLE, 50, 2, condition="HP"),
        _row("card_kingdom", PRINT_A, ORACLE, 400, 2, condition="NM"),
    ]
    near_mint = plan_purchase(
        [_item(ORACLE, PRINT_A)],
        [_fact(PRINT_A, ORACLE, "Sol Ring")],
        rows,
        [_ck(shipping=0, threshold=None)],
        min_condition="NM",
        package_penalty_cents=0,
    )
    assert _bucket(near_mint, "card_kingdom").cards[0].condition == "NM"
    assert _bucket(near_mint, "card_kingdom").cards[0].unit_price_cents == 400


def test_savings_against_the_single_store_baseline() -> None:
    response = plan_purchase(
        [_item(ORACLE, PRINT_A), _item(OTHER_ORACLE, OTHER_PRINT)],
        [
            _fact(PRINT_A, ORACLE, "Sol Ring"),
            _fact(OTHER_PRINT, OTHER_ORACLE, "Lightning Bolt", set_code="lea", collector_number="161"),
        ],
        [
            _row("card_kingdom", PRINT_A, ORACLE, 500, 2),
            _row("mana_pool", PRINT_A, ORACLE, 250, 2),
            _row("card_kingdom", OTHER_PRINT, OTHER_ORACLE, 500, 2, name="Lightning Bolt", set_code="lea", collector_number="161"),
            _row("mana_pool", OTHER_PRINT, OTHER_ORACLE, 9000, 2, name="Lightning Bolt", set_code="lea", collector_number="161"),
        ],
        [_ck(shipping=599, threshold=None), _mp()],
        package_penalty_cents=0,
    )
    assert response.savings.baseline_store_id == "card_kingdom"
    assert response.savings.baseline_total_cents == 1599
    assert response.savings.optimized_total_cents == 1349
    assert response.savings.savings_cents == 250
    assert response.savings.message == "Saves $2.50 vs. buying everything at Card Kingdom"


def test_checkout_url_and_copyable_mass_entry() -> None:
    response = plan_purchase(
        [_item(ORACLE, PRINT_A, finish="foil")],
        [_fact(PRINT_A, ORACLE, "Sol Ring")],
        [_row("card_kingdom", PRINT_A, ORACLE, 800, 1, finish="foil")],
        [_ck(shipping=599, threshold=7500)],
        finish_mode="match_deck",
        package_penalty_cents=0,
        affiliate_tags={"card_kingdom": "ck-tag"},
    )
    bucket = _bucket(response, "card_kingdom")
    assert bucket.mass_entry_text == "1 Sol Ring (LTC) 284 *F*"
    parsed = parse_qs(urlparse(bucket.checkout_url).query)
    assert parsed["c"] == ["1 Sol Ring (LTC) 284 *F*"]
    assert parsed["partner"] == ["ck-tag"]
    assert urlparse(bucket.checkout_url).netloc == "www.cardkingdom.com"


def test_mana_pool_checkout_uses_the_affiliate_tag() -> None:
    response = plan_purchase(
        [_item(ORACLE, PRINT_A)],
        [_fact(PRINT_A, ORACLE, "Sol Ring")],
        [_row("mana_pool", PRINT_A, ORACLE, 300, 1)],
        [_mp()],
        package_penalty_cents=0,
        affiliate_tags={"mana_pool": "mp-tag"},
    )
    bucket = _bucket(response, "mana_pool")
    assert bucket.mass_entry_text == "1 Sol Ring (LTC) 284"
    assert bucket.checkout_url == "https://manapool.com/add-deck?ref=mp-tag"
    assert bucket.estimated_packages == 1
    assert bucket.price_source == "snapshot"


def _two_store_snapshot():
    response = plan_purchase(
        [_item(ORACLE, PRINT_A), _item(OTHER_ORACLE, OTHER_PRINT)],
        [
            _fact(PRINT_A, ORACLE, "Sol Ring"),
            _fact(OTHER_PRINT, OTHER_ORACLE, "Lightning Bolt", set_code="lea", collector_number="161"),
        ],
        [
            _row("mana_pool", PRINT_A, ORACLE, 300, 2),
            _row("card_kingdom", OTHER_PRINT, OTHER_ORACLE, 300, 2, name="Lightning Bolt", set_code="lea", collector_number="161"),
        ],
        [_ck(shipping=0, threshold=None), _mp()],
        package_penalty_cents=0,
    )
    return response


def test_live_quote_replaces_mana_pool_totals_and_is_cached() -> None:
    snapshot = _two_store_snapshot()
    calls = {"count": 0}

    async def fetch(lines: list[LiveCartLine]) -> LiveQuote:
        calls["count"] += 1
        assert lines[0].name == "Sol Ring"
        assert lines[0].finish == "nonfoil"
        return LiveQuote(subtotal_cents=111, shipping_cents=40, package_count=4)

    redis = _FakeRedis()
    stores = [_ck(shipping=0, threshold=None), _mp()]

    async def run() -> None:
        first = await verify_live_cart(
            snapshot,
            stores,
            redis=redis,
            min_condition="LP",
            email="buyer@example.com",
            access_token="mpat_test",
            live_fetch=fetch,
        )
        second = await verify_live_cart(
            snapshot,
            stores,
            redis=redis,
            min_condition="LP",
            email="buyer@example.com",
            access_token="mpat_test",
            live_fetch=fetch,
        )
        pool = _bucket(first, "mana_pool")
        assert pool.subtotal_cents == 111
        assert pool.shipping_cents == 40
        assert pool.estimated_packages == 4
        assert pool.price_source == "live"
        assert pool.notice is None
        assert pool.cards[0].unit_price_cents == 300
        assert _bucket(first, "card_kingdom").subtotal_cents == 300
        assert first.savings.optimized_total_cents == 451
        assert first.notices == []
        assert calls["count"] == 1
        assert redis.sets[0][2] == LIVE_CACHE_TTL_SECONDS
        assert redis.sets[0][0].startswith("manapool:cart-optimizer:")
        assert _bucket(second, "mana_pool").subtotal_cents == 111

    asyncio.run(run())


def test_live_timeout_falls_back_to_stage_two_with_a_notice() -> None:
    snapshot = _two_store_snapshot()

    async def fetch(_lines: list[LiveCartLine]) -> LiveQuote:
        raise TimeoutError("timed out")

    async def run():
        return await verify_live_cart(
            snapshot,
            [_ck(shipping=0, threshold=None), _mp()],
            redis=_FakeRedis(),
            min_condition="LP",
            email="buyer@example.com",
            access_token="mpat_test",
            live_fetch=fetch,
        )

    result = asyncio.run(run())
    pool = _bucket(result, "mana_pool")
    assert pool.subtotal_cents == 300
    assert pool.shipping_cents == 0
    assert pool.price_source == "snapshot"
    assert pool.notice == TIMEOUT_NOTICE
    assert result.notices == [TIMEOUT_NOTICE]
    assert _bucket(result, "card_kingdom").notice is None
    assert result.savings.optimized_total_cents == 600


def test_missing_mana_pool_credentials_keep_the_snapshot() -> None:
    async def run():
        return await verify_live_cart(
            _two_store_snapshot(),
            [_mp()],
            redis=_FakeRedis(),
            min_condition="LP",
            email="",
            access_token="",
            client=None,
            live_fetch=None,
        )

    result = asyncio.run(run())
    assert _bucket(result, "mana_pool").subtotal_cents == 300
    assert _bucket(result, "mana_pool").notice == UNAVAILABLE_NOTICE
    assert result.notices == [UNAVAILABLE_NOTICE]


def test_optimizer_payload_uses_the_final_streamed_line() -> None:
    body = "\n".join(
        [
            json.dumps({"totals": {"subtotal_cents": 1, "shipping_cents": 1, "seller_count": 9}}),
            json.dumps(
                {
                    "totals": {"subtotal_cents": 80, "shipping_cents": 20, "seller_count": 2},
                    "cart": [{"inventory_id": "abc", "quantity_selected": 1}],
                }
            ),
        ]
    )
    quote = parse_optimizer_payload(body, requested_quantity=1)
    assert quote == LiveQuote(subtotal_cents=80, shipping_cents=20, package_count=2)

    short = json.dumps(
        {
            "totals": {"subtotal_cents": 10, "shipping_cents": 1, "seller_count": 1},
            "cart": [{"quantity_selected": 0}],
        }
    )
    with pytest.raises(LiveQuoteError) as caught:
        parse_optimizer_payload(short, requested_quantity=1)
    assert caught.value.notice == PARTIAL_NOTICE


def test_live_cache_key_changes_with_finish_and_condition() -> None:
    nonfoil = live_cache_key([LiveCartLine("Sol Ring", str(PRINT_A), 1, "nonfoil", "NM")], "LP")
    foil = live_cache_key([LiveCartLine("Sol Ring", str(PRINT_A), 1, "foil", "NM")], "LP")
    played = live_cache_key([LiveCartLine("Sol Ring", str(PRINT_A), 1, "nonfoil", "LP")], "LP")
    assert len({nonfoil, foil, played}) == 3


def test_offer_query_keeps_zero_stock_rows() -> None:
    statement = build_offer_statement([ORACLE], ["card_kingdom", "mana_pool"], ["NM", "LP"])
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled).casefold()
    assert "join" in sql
    assert "store_prices" in sql
    assert "cards" in sql
    assert "oracle_id" in sql
    assert "store_id" in sql
    assert "condition" in sql
    assert "stock_qty >" not in sql
    assert "stock_qty >=" not in sql
    blob = " ".join(str(value) for value in compiled.params.values())
    assert "card_kingdom" in blob
    assert "mana_pool" in blob
    assert "NM" in blob


def test_optimize_route_validates_the_request() -> None:
    client = TestClient(app)
    locked = client.post(
        "/api/v1/optimize",
        json={"items": [{"oracle_id": str(ORACLE), "quantity": 1, "printing_locked": True}]},
    )
    assert locked.status_code == 422
    empty_stores = client.post(
        "/api/v1/optimize",
        json={"items": [{"oracle_id": str(ORACLE), "quantity": 1}], "enabled_stores": []},
    )
    assert empty_stores.status_code == 422
    penalty = client.post(
        "/api/v1/optimize",
        json={
            "items": [{"oracle_id": str(ORACLE), "quantity": 1}],
            "package_penalty_cents": 1600,
        },
    )
    assert penalty.status_code == 422
    assert "post" in app.openapi()["paths"]["/api/v1/optimize"]


def test_optimize_deck_loads_prices_from_the_session() -> None:
    card = Card(
        scryfall_id=PRINT_A,
        oracle_id=ORACLE,
        name="Sol Ring",
        set_code="ltc",
        collector_number="284",
        is_basic_land=False,
        image_uri=None,
        finishes=["nonfoil"],
        updated_at=datetime(2026, 11, 21, tzinfo=timezone.utc),
    )
    store = Store(
        id="card_kingdom",
        name="Card Kingdom",
        store_type="single_warehouse",
        base_shipping_cents=0,
        free_shipping_threshold_cents=None,
        affiliate_param="shelf",
        enabled=True,
    )
    price = StorePrice(
        id=1,
        store_id="card_kingdom",
        scryfall_id=PRINT_A,
        finish="nonfoil",
        condition="NM",
        price_cents=125,
        stock_qty=2,
        store_sku="ck-1",
        updated_at=datetime(2026, 11, 21, tzinfo=timezone.utc),
    )
    session = _FakeSession(
        [
            _ScalarResult([store]),
            _ScalarResult([card]),
            _ScalarResult([card]),
            _PairResult([(price, card)]),
        ]
    )
    request = OptimizeRequest(
        items=[OptimizeItem(oracle_id=ORACLE, scryfall_id=PRINT_A, quantity=2)],
        enabled_stores=["card_kingdom"],
        min_condition="NM",
        exclude_basic_lands=False,
        package_penalty_cents=0,
    )

    async def run():
        return await optimize_deck(session, _FakeRedis(), request)  # type: ignore[arg-type]

    response = asyncio.run(run())
    assert len(session.statements) == 4
    bucket = _bucket(response, "card_kingdom")
    assert bucket.cards[0].unit_price_cents == 125
    assert bucket.cards[0].line_total_cents == 250
    assert bucket.cards[0].store_sku == "ck-1"
    assert bucket.mass_entry_text == "2 Sol Ring (LTC) 284"
    parsed = parse_qs(urlparse(bucket.checkout_url).query)
    assert parsed["partner"] == ["shelf"]
    compiled = str(session.statements[-1].compile(dialect=postgresql.dialect())).casefold()
    assert "store_prices" in compiled


def test_optimize_route_returns_a_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    card = Card(
        scryfall_id=PRINT_A,
        oracle_id=ORACLE,
        name="Sol Ring",
        set_code="ltc",
        collector_number="284",
        is_basic_land=False,
        image_uri=None,
        finishes=["nonfoil"],
        updated_at=datetime(2026, 11, 21, tzinfo=timezone.utc),
    )
    store = Store(
        id="card_kingdom",
        name="Card Kingdom",
        store_type="single_warehouse",
        base_shipping_cents=0,
        free_shipping_threshold_cents=None,
        affiliate_param=None,
        enabled=True,
    )
    price = StorePrice(
        id=1,
        store_id="card_kingdom",
        scryfall_id=PRINT_A,
        finish="nonfoil",
        condition="NM",
        price_cents=125,
        stock_qty=1,
        store_sku=None,
        updated_at=datetime(2026, 11, 21, tzinfo=timezone.utc),
    )
    session = _FakeSession(
        [
            _ScalarResult([store]),
            _ScalarResult([card]),
            _ScalarResult([card]),
            _PairResult([(price, card)]),
        ]
    )

    async def override_session():
        yield session

    monkeypatch.setattr("app.api.routes.optimize.create_redis", lambda: _FakeRedis())
    app.dependency_overrides[get_session] = override_session
    try:
        response = TestClient(app).post(
            "/api/v1/optimize",
            json={
                "items": [
                    {
                        "oracle_id": str(ORACLE),
                        "scryfall_id": str(PRINT_A),
                        "quantity": 1,
                        "finish": "nonfoil",
                        "printing_locked": True,
                    }
                ],
                "min_condition": "LP",
                "finish_mode": "match_deck",
                "exclude_basic_lands": True,
                "enabled_stores": ["card_kingdom"],
                "package_penalty_cents": 300,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["buckets"][0]["store_name"] == "Card Kingdom"
    assert body["buckets"][0]["cards"][0]["name"] == "Sol Ring"
    assert body["buckets"][0]["mass_entry_text"] == "1 Sol Ring (LTC) 284"
    assert body["unmatched"] == []
    assert body["savings"]["optimized_total_cents"] == 125


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.sets: list[tuple[str, str, int | None]] = []

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.values[key] = value
        self.sets.append((key, value, ex))
        return True

    async def aclose(self) -> None:
        return None


class _ScalarResult:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def scalars(self) -> _ScalarResult:
        return self

    def all(self) -> list:
        return self._rows


class _PairResult:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def all(self) -> list:
        return self._rows


class _FakeSession:
    def __init__(self, results: list) -> None:
        self._results = results
        self.statements: list = []

    async def execute(self, statement: object):
        self.statements.append(statement)
        if not self._results:
            raise AssertionError("unexpected query")
        return self._results.pop(0)


def test_fetch_live_quote_posts_the_cart_optimizer() -> None:
    captured: dict = {}

    class _Client:
        async def post(self, url: str, json: dict, headers: dict, timeout: httpx.Timeout) -> httpx.Response:
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            captured["timeout"] = timeout
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                json={
                    "totals": {"subtotal_cents": 42, "shipping_cents": 7, "seller_count": 1},
                    "cart": [{"quantity_selected": 1}],
                },
                request=request,
            )

    async def run() -> LiveQuote:
        return await _fetch(_Client())

    quote = asyncio.run(run())
    assert quote.subtotal_cents == 42
    assert captured["url"] == "https://manapool.com/api/v1/buyer/optimizer"
    assert captured["json"]["cart"][0]["name"] == "Sol Ring"
    assert captured["json"]["cart"][0]["finish_ids"] == ["NF"]
    assert captured["json"]["cart"][0]["condition_ids"] == ["NM", "LP"]
    assert captured["headers"]["X-ManaPool-Email"] == "buyer@example.com"
    assert captured["headers"]["X-ManaPool-Access-Token"] == "mpat_test"
    assert captured["timeout"].read == 2.5


async def _fetch(client) -> LiveQuote:
    from app.services.stores.mana_pool import fetch_live_quote

    return await fetch_live_quote(
        [LiveCartLine("Sol Ring", str(PRINT_A), 1, "nonfoil", "NM")],
        client,
        email="buyer@example.com",
        access_token="mpat_test",
        conditions=("NM", "LP"),
    )
