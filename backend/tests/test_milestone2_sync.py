import gzip
import uuid
from datetime import datetime, timezone

from sqlalchemy.dialects import postgresql

from app.services.streams import iter_json_items
from app.services.stores.base import (
    PRICE_CONFLICT_COLUMNS,
    build_store_price_upsert,
    build_store_seed_upsert,
    dedupe_price_rows,
)
from app.services.stores.card_kingdom import USER_AGENT, CardKingdomAdapter, ck_price_rows, map_ck_condition
from app.services.stores.mana_pool import mana_pool_price_rows
from app.services.stores.seed import SEED_STORES
from app.workers.sync_scryfall import _iter_jsonl_gzip, bulk_download, iter_cards_for_sync, parse_args

SCRYFALL_ID = "a363bc91-8278-448e-9d5c-564e4b51eb62"
UPDATED_AT = datetime(2026, 11, 20, tzinfo=timezone.utc)


def _paper(name: str, **overrides: object) -> dict:
    card: dict = {
        "id": "11111111-1111-1111-1111-111111111111",
        "oracle_id": "22222222-2222-2222-2222-222222222222",
        "name": name,
        "set": "mh3",
        "collector_number": "1",
        "games": ["paper"],
        "layout": "normal",
        "set_type": "expansion",
        "type_line": "Creature",
        "finishes": ["nonfoil"],
    }
    card.update(overrides)
    return card


def test_card_kingdom_condition_mapping() -> None:
    assert map_ck_condition("NM") == "NM"
    assert map_ck_condition("EX") == "LP"
    assert map_ck_condition("VG") == "MP"
    assert map_ck_condition("G") == "HP"
    assert map_ck_condition("PL") is None

    rows = ck_price_rows(
        {
            "id": 10000,
            "sku": "4ED-117",
            "scryfall_id": SCRYFALL_ID,
            "is_foil": "false",
            "condition_values": {
                "nm_price": "1.00",
                "nm_qty": 3,
                "ex_price": "0.80",
                "ex_qty": 12,
                "vg_price": "0.25",
                "vg_qty": 7,
                "g_price": "0.40",
                "g_qty": 0,
            },
        },
        UPDATED_AT,
    )
    by_condition = {row["condition"]: row for row in rows}

    assert set(by_condition) == {"NM", "LP", "MP", "HP"}
    assert by_condition["LP"]["price_cents"] == 80
    assert by_condition["LP"]["stock_qty"] == 12
    assert by_condition["MP"]["price_cents"] == 25
    assert by_condition["MP"]["stock_qty"] == 7
    assert by_condition["HP"]["price_cents"] == 40
    assert by_condition["HP"]["stock_qty"] == 0
    assert by_condition["NM"]["finish"] == "nonfoil"
    assert by_condition["NM"]["store_id"] == "card_kingdom"
    assert all(row["store_sku"] == "4ED-117" for row in rows)

    foil_rows = ck_price_rows(
        {
            "sku": "4ED-117",
            "scryfall_id": SCRYFALL_ID,
            "is_foil": "true",
            "condition": "EX",
            "price_retail": "1.50",
            "qty_retail": 2,
        },
        UPDATED_AT,
    )
    assert foil_rows == [
        {
            "store_id": "card_kingdom",
            "scryfall_id": uuid.UUID(SCRYFALL_ID),
            "finish": "foil",
            "condition": "LP",
            "price_cents": 150,
            "stock_qty": 2,
            "store_sku": "4ED-117",
            "updated_at": UPDATED_AT,
        }
    ]
    assert USER_AGENT == "gitpro.be/1.0"
    assert CardKingdomAdapter.headers["User-Agent"] == USER_AGENT


def test_store_price_upsert_conflict_key() -> None:
    cheaper = {
        "store_id": "card_kingdom",
        "scryfall_id": uuid.UUID(SCRYFALL_ID),
        "finish": "nonfoil",
        "condition": "LP",
        "price_cents": 80,
        "stock_qty": 2,
        "store_sku": "4ED-117",
        "updated_at": UPDATED_AT,
    }
    duplicate = {**cheaper, "price_cents": 120, "stock_qty": 9, "store_sku": "other"}
    rows = dedupe_price_rows([duplicate, cheaper])
    assert len(rows) == 1
    assert rows[0]["price_cents"] == 80

    statement = build_store_price_upsert(rows)
    conflict = statement._post_values_clause
    assert list(conflict.inferred_target_elements) == list(PRICE_CONFLICT_COLUMNS)

    compiled = str(statement.compile(dialect=postgresql.dialect()))
    normalized = compiled.replace('"', "")
    assert "ON CONFLICT (store_id, scryfall_id, finish, condition) DO UPDATE" in normalized
    assert "price_cents" in normalized
    assert "stock_qty" in normalized


def test_scryfall_jsonl_download_and_stream() -> None:
    url, feed_format = bulk_download(
        {
            "jsonl_download_uri": "https://data.scryfall.io/default-cards/default-cards.jsonl.gz",
            "download_uri": "https://data.scryfall.io/default-cards/default-cards.json",
        }
    )
    assert url.endswith(".jsonl.gz")
    assert feed_format == "jsonl"

    raw = gzip.compress(b'{"name":"Lightning Bolt"}\n{"name":"Sol Ring"}\n')
    cards = list(_iter_jsonl_gzip([raw[:5], raw[5:]]))
    assert [card["name"] for card in cards] == ["Lightning Bolt", "Sol Ring"]

    payload = b'{"data":[{"name":"Bolt"},{"name":"Ring"}]}'
    items = list(iter_json_items([payload[:8], payload[8:]], "data.item"))
    assert [item["name"] for item in items] == ["Bolt", "Ring"]


def test_scryfall_limit_skips_filtered_cards() -> None:
    args = parse_args(["--limit", "2"])
    cards = [
        _paper("Arena Only", games=["arena"], id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1"),
        _paper("Treasure", layout="token", type_line="Token Artifact — Treasure", id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2"),
        _paper("Art Card", layout="art_series", set_type="memorabilia", id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa3"),
        _paper("Lightning Bolt", id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa4"),
        _paper("Sol Ring", id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa5"),
        _paper("Birds of Paradise", id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa6"),
    ]

    selected = list(iter_cards_for_sync(cards, args.limit))

    assert args.limit == 2
    assert [card["name"] for card in selected] == ["Lightning Bolt", "Sol Ring"]

    unlimited = parse_args([])
    assert unlimited.limit is None
    assert [card["name"] for card in iter_cards_for_sync(cards, unlimited.limit)] == [
        "Lightning Bolt",
        "Sol Ring",
        "Birds of Paradise",
    ]


def test_launch_store_shipping_matches_blueprint() -> None:
    stores = {row["id"]: row for row in SEED_STORES}
    card_kingdom = stores["card_kingdom"]
    mana_pool = stores["mana_pool"]

    assert card_kingdom["name"] == "Card Kingdom"
    assert card_kingdom["store_type"] == "single_warehouse"
    assert card_kingdom["base_shipping_cents"] == 599
    assert card_kingdom["free_shipping_threshold_cents"] == 7500
    assert card_kingdom["enabled"] is True

    assert mana_pool["name"] == "Mana Pool"
    assert mana_pool["store_type"] == "p2p"
    assert mana_pool["base_shipping_cents"] == 0
    assert mana_pool["free_shipping_threshold_cents"] is None
    assert mana_pool["enabled"] is True
    seed_sql = str(build_store_seed_upsert().compile(dialect=postgresql.dialect())).replace('"', "")
    assert "ON CONFLICT (id) DO UPDATE" in seed_sql


def test_mana_pool_snapshot_maps_finish_and_condition() -> None:
    rows = mana_pool_price_rows(
        {
            "product_type": "mtg_single",
            "product_id": "00001827-a9f4-44a9-9347-457e9c339862",
            "scryfall_id": SCRYFALL_ID,
            "condition_id": "MP",
            "finish_id": "FO",
            "low_price": 709,
            "available_quantity": 20,
        },
        UPDATED_AT,
    )

    assert rows == [
        {
            "store_id": "mana_pool",
            "scryfall_id": uuid.UUID(SCRYFALL_ID),
            "finish": "foil",
            "condition": "MP",
            "price_cents": 709,
            "stock_qty": 20,
            "store_sku": "00001827-a9f4-44a9-9347-457e9c339862",
            "updated_at": UPDATED_AT,
        }
    ]
    assert mana_pool_price_rows(
        {
            "product_type": "mtg_single",
            "scryfall_id": SCRYFALL_ID,
            "condition_id": "DMG",
            "finish_id": "NF",
            "low_price": 10,
            "available_quantity": 1,
        },
        UPDATED_AT,
    ) == []
