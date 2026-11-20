"""Seed Card Kingdom and Mana Pool.

Revision ID: 0002_seed_stores
Revises: 0001_initial
Create Date: 2026-11-20

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.services.stores.base import build_store_seed_upsert

revision: str = "0002_seed_stores"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(build_store_seed_upsert())


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM store_prices WHERE store_id IN ('card_kingdom', 'mana_pool')"))
    op.execute(sa.text("DELETE FROM stores WHERE id IN ('card_kingdom', 'mana_pool')"))
