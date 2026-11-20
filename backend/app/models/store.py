from sqlalchemy import Boolean, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Store(Base):
    __tablename__ = "stores"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    store_type: Mapped[str] = mapped_column(String(32), nullable=False)
    base_shipping_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    free_shipping_threshold_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    affiliate_param: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )
