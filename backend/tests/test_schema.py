from app.core.database import Base
from app.models import Card, Store, StorePrice


def test_metadata_registers_milestone_tables() -> None:
    assert set(Base.metadata.tables) == {"cards", "stores", "store_prices"}
    assert Card.__tablename__ == "cards"
    assert Store.__tablename__ == "stores"
    assert StorePrice.__tablename__ == "store_prices"


def test_cards_columns_match_blueprint() -> None:
    columns = Base.metadata.tables["cards"].columns
    assert [column.name for column in columns] == [
        "scryfall_id",
        "oracle_id",
        "name",
        "set_code",
        "collector_number",
        "is_basic_land",
        "image_uri",
        "finishes",
        "updated_at",
    ]
    assert columns["scryfall_id"].primary_key
    assert columns["image_uri"].nullable
    assert not columns["oracle_id"].nullable
    assert not columns["finishes"].nullable
    index_names = {index.name for index in Base.metadata.tables["cards"].indexes}
    assert index_names == {"ix_cards_oracle_id", "ix_cards_name"}


def test_stores_columns_match_blueprint() -> None:
    columns = Base.metadata.tables["stores"].columns
    assert [column.name for column in columns] == [
        "id",
        "name",
        "store_type",
        "base_shipping_cents",
        "free_shipping_threshold_cents",
        "affiliate_param",
        "enabled",
    ]
    assert columns["id"].primary_key
    assert columns["free_shipping_threshold_cents"].nullable
    assert columns["affiliate_param"].nullable
    assert not columns["enabled"].nullable
    assert columns["base_shipping_cents"].type.python_type is int


def test_store_prices_columns_indexes_and_foreign_keys() -> None:
    table = Base.metadata.tables["store_prices"]
    columns = table.columns
    assert [column.name for column in columns] == [
        "id",
        "store_id",
        "scryfall_id",
        "finish",
        "condition",
        "price_cents",
        "stock_qty",
        "store_sku",
        "updated_at",
    ]
    assert columns["id"].primary_key
    assert columns["store_sku"].nullable
    assert columns["price_cents"].type.python_type is int
    assert columns["stock_qty"].type.python_type is int

    index_names = {index.name for index in table.indexes}
    assert "ix_store_prices_scryfall_id_finish_condition_stock_qty" in index_names
    unique_names = {constraint.name for constraint in table.constraints if constraint.__class__.__name__ == "UniqueConstraint"}
    assert "uq_store_prices_store_id_scryfall_id_finish_condition" in unique_names

    foreign_keys = {fk.name: fk for column in columns for fk in column.foreign_keys}
    assert foreign_keys["fk_store_prices_store_id_stores"].column.table.name == "stores"
    assert foreign_keys["fk_store_prices_scryfall_id_cards"].column.table.name == "cards"
