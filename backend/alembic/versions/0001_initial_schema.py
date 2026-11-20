"""Initial schema for cards, stores, and store_prices.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-20

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cards",
        sa.Column("scryfall_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("oracle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("set_code", sa.String(length=16), nullable=False),
        sa.Column("collector_number", sa.String(length=32), nullable=False),
        sa.Column("is_basic_land", sa.Boolean(), nullable=False),
        sa.Column("image_uri", sa.Text(), nullable=True),
        sa.Column("finishes", postgresql.JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_cards_oracle_id", "cards", ["oracle_id"])
    op.create_index("ix_cards_name", "cards", ["name"])

    op.create_table(
        "stores",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("store_type", sa.String(length=32), nullable=False),
        sa.Column("base_shipping_cents", sa.Integer(), nullable=False),
        sa.Column("free_shipping_threshold_cents", sa.Integer(), nullable=True),
        sa.Column("affiliate_param", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )

    op.create_table(
        "store_prices",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("store_id", sa.String(length=32), nullable=False),
        sa.Column("scryfall_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("finish", sa.String(length=16), nullable=False),
        sa.Column("condition", sa.String(length=8), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column("stock_qty", sa.Integer(), nullable=False),
        sa.Column("store_sku", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["stores.id"],
            name="fk_store_prices_store_id_stores",
        ),
        sa.ForeignKeyConstraint(
            ["scryfall_id"],
            ["cards.scryfall_id"],
            name="fk_store_prices_scryfall_id_cards",
        ),
        sa.UniqueConstraint(
            "store_id",
            "scryfall_id",
            "finish",
            "condition",
            name="uq_store_prices_store_id_scryfall_id_finish_condition",
        ),
    )
    op.create_index(
        "ix_store_prices_scryfall_id_finish_condition_stock_qty",
        "store_prices",
        ["scryfall_id", "finish", "condition", "stock_qty"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_store_prices_scryfall_id_finish_condition_stock_qty",
        table_name="store_prices",
    )
    op.drop_table("store_prices")
    op.drop_table("stores")
    op.drop_index("ix_cards_name", table_name="cards")
    op.drop_index("ix_cards_oracle_id", table_name="cards")
    op.drop_table("cards")
