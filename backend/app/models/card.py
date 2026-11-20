import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Card(Base):
    __tablename__ = "cards"
    __table_args__ = (
        Index("ix_cards_oracle_id", "oracle_id"),
        Index("ix_cards_name", "name"),
    )

    scryfall_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    oracle_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    set_code: Mapped[str] = mapped_column(String(16), nullable=False)
    collector_number: Mapped[str] = mapped_column(String(32), nullable=False)
    is_basic_land: Mapped[bool] = mapped_column(Boolean, nullable=False)
    image_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    finishes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
