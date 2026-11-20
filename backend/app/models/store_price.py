import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class StorePrice(Base):
    __tablename__ = "store_prices"
    __table_args__ = (
        UniqueConstraint(
            "store_id",
            "scryfall_id",
            "finish",
            "condition",
            name="uq_store_prices_store_id_scryfall_id_finish_condition",
        ),
        Index(
            "ix_store_prices_scryfall_id_finish_condition_stock_qty",
            "scryfall_id",
            "finish",
            "condition",
            "stock_qty",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    store_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("stores.id", name="fk_store_prices_store_id_stores"),
        nullable=False,
    )
    scryfall_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cards.scryfall_id", name="fk_store_prices_scryfall_id_cards"),
        nullable=False,
    )
    finish: Mapped[str] = mapped_column(String(16), nullable=False)
    condition: Mapped[str] = mapped_column(String(8), nullable=False)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    stock_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    store_sku: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
