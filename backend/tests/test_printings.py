"""Printing picker: catalog printings and median in-stock prices."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from app.core.database import get_session
from app.main import app
from app.models.card import Card
from app.models.store import Store
from app.models.store_price import StorePrice
from app.services.printings import build_printings_response, median_cents

ORACLE = uuid.UUID("11111111-1111-4111-8111-111111111111")
PRINT_A = uuid.UUID("22222222-2222-4222-8222-222222222222")
PRINT_B = uuid.UUID("33333333-3333-4333-8333-333333333333")
WHEN = datetime(2026, 11, 21, tzinfo=timezone.utc)


def test_median_cents_uses_the_middle_listing() -> None:
    assert median_cents([]) is None
    assert median_cents([125]) == 125
    assert median_cents([400, 100, 200]) == 200
    assert median_cents([100, 300]) == 200
    assert median_cents([100, 101]) == 101


def test_build_printings_prices_foil_and_nonfoil_separately() -> None:
    promo = _card(PRINT_A, "cmm", "398", "https://cards.scryfall.io/cmm.jpg", ["nonfoil", "foil"])
    pack = _card(PRINT_B, "ltc", "284", None, ["nonfoil"])
    stores = [
        _store("mana_pool", "Mana Pool"),
        _store("card_kingdom", "Card Kingdom"),
        _store("card_trader", "CardTrader Zero", enabled=False),
    ]
    prices = [
        _price(1, "card_kingdom", PRINT_A, "nonfoil", "NM", 100, 4),
        _price(2, "card_kingdom", PRINT_A, "nonfoil", "LP", 300, 2),
        _price(3, "card_kingdom", PRINT_A, "nonfoil", "MP", 1, 0),
        _price(4, "card_kingdom", PRINT_A, "foil", "NM", 500, 1),
        _price(5, "mana_pool", PRINT_A, "nonfoil", "NM", 150, 3),
        _price(6, "mana_pool", PRINT_A, "foil", "NM", 700, 1),
        _price(7, "mana_pool", PRINT_A, "foil", "LP", 900, 1),
        _price(8, "card_trader", PRINT_A, "nonfoil", "NM", 50, 9),
        _price(9, "card_kingdom", PRINT_B, "nonfoil", "NM", 80, 1),
        _price(10, "card_kingdom", PRINT_A, "etched", "NM", 40, 1),
    ]

    response = build_printings_response([pack, promo], stores, prices)

    assert response is not None
    assert [printing.set_code for printing in response.printings] == ["cmm", "ltc"]
    promo_prices = {entry.finish: entry.vendors for entry in response.printings[0].prices}
    assert [vendor.store_id for vendor in promo_prices["nonfoil"]] == ["mana_pool", "card_kingdom"]
    assert [vendor.median_cents for vendor in promo_prices["nonfoil"]] == [150, 200]
    assert [vendor.median_cents for vendor in promo_prices["foil"]] == [800, 500]
    assert promo_prices["nonfoil"][0].store_name == "Mana Pool"
    pack_prices = {entry.finish: entry.vendors for entry in response.printings[1].prices}
    assert [vendor.median_cents for vendor in pack_prices["nonfoil"]] == [None, 80]
    assert [vendor.median_cents for vendor in pack_prices["foil"]] == [None, None]
    assert response.printings[0].image_uri == "https://cards.scryfall.io/cmm.jpg"
    assert response.printings[0].finishes == ["nonfoil", "foil"]


def test_printings_route_returns_medians(monkeypatch: pytest.MonkeyPatch) -> None:
    promo = _card(PRINT_A, "cmm", "398", "https://cards.scryfall.io/cmm.jpg", ["nonfoil", "foil"])
    kingdom = _store("card_kingdom", "Card Kingdom")
    pool = _store("mana_pool", "Mana Pool")
    session = _FakeSession(
        [
            _ScalarResult([promo]),
            _ScalarResult([kingdom, pool]),
            _ScalarResult(
                [
                    _price(1, "card_kingdom", PRINT_A, "nonfoil", "NM", 100, 1),
                    _price(2, "card_kingdom", PRINT_A, "nonfoil", "LP", 300, 1),
                    _price(3, "mana_pool", PRINT_A, "foil", "NM", 640, 2),
                ]
            ),
        ]
    )
    _override_session(monkeypatch, session)
    try:
        response = TestClient(app).get(
            f"/api/v1/cards/{ORACLE}/printings",
            params={"stores": "mana_pool,card_kingdom"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Sol Ring"
    assert body["printings"][0]["scryfall_id"] == str(PRINT_A)
    prices = {entry["finish"]: entry["vendors"] for entry in body["printings"][0]["prices"]}
    assert [vendor["store_id"] for vendor in prices["nonfoil"]] == ["mana_pool", "card_kingdom"]
    assert [vendor["median_cents"] for vendor in prices["nonfoil"]] == [None, 200]
    assert [vendor["median_cents"] for vendor in prices["foil"]] == [640, None]
    compiled = str(session.statements[-1].compile(dialect=postgresql.dialect())).casefold()
    assert "store_prices" in compiled
    assert "stock_qty" in compiled
    assert "get" in app.openapi()["paths"]["/api/v1/cards/{oracle_id}/printings"]


def test_printings_route_hides_unknown_and_empty_stores(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession([_ScalarResult([])])
    _override_session(monkeypatch, session)
    try:
        missing = TestClient(app).get(f"/api/v1/cards/{ORACLE}/printings")
        empty = TestClient(app).get(f"/api/v1/cards/{ORACLE}/printings", params={"stores": " , "})
        invalid = TestClient(app).get("/api/v1/cards/not-a-card/printings")
    finally:
        app.dependency_overrides.clear()

    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "unknown_card"
    assert empty.status_code == 422
    assert empty.json()["detail"]["code"] == "no_stores"
    assert invalid.status_code == 422
    assert len(session.statements) == 1


def _override_session(monkeypatch: pytest.MonkeyPatch, session: _FakeSession) -> None:
    async def override_session():
        yield session

    monkeypatch.setattr("app.api.routes.cards.get_session", override_session)
    app.dependency_overrides[get_session] = override_session


def _card(scryfall_id: uuid.UUID, set_code: str, collector_number: str, image: str | None, finishes: list[str]) -> Card:
    return Card(
        scryfall_id=scryfall_id,
        oracle_id=ORACLE,
        name="Sol Ring",
        set_code=set_code,
        collector_number=collector_number,
        is_basic_land=False,
        image_uri=image,
        finishes=finishes,
        updated_at=WHEN,
    )


def _store(store_id: str, name: str, *, enabled: bool = True) -> Store:
    return Store(
        id=store_id,
        name=name,
        store_type="single_warehouse",
        base_shipping_cents=0,
        free_shipping_threshold_cents=None,
        affiliate_param=None,
        enabled=enabled,
    )


def _price(
    row_id: int,
    store_id: str,
    scryfall_id: uuid.UUID,
    finish: str,
    condition: str,
    price_cents: int,
    stock_qty: int,
) -> StorePrice:
    return StorePrice(
        id=row_id,
        store_id=store_id,
        scryfall_id=scryfall_id,
        finish=finish,
        condition=condition,
        price_cents=price_cents,
        stock_qty=stock_qty,
        store_sku=None,
        updated_at=WHEN,
    )


class _ScalarResult:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def scalars(self) -> _ScalarResult:
        return self

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
