"""Seed CardTrader Zero.

Revision ID: 0003_card_trader
Revises: 0002_seed_stores
Create Date: 2026-11-21

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.services.stores.base import build_store_seed_upsert
from app.services.stores.seed import SEED_STORES

revision: str = "0003_card_trader"
down_revision: Union[str, None] = "0002_seed_stores"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    card_trader = next(row for row in SEED_STORES if row["id"] == "card_trader")
    op.execute(build_store_seed_upsert([card_trader]))


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM store_prices WHERE store_id = 'card_trader'"))
    op.execute(sa.text("DELETE FROM stores WHERE id = 'card_trader'"))
