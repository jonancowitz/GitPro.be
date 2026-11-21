"""Optimize request and the store-bucket response."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.deck import Finish

Condition = Literal["NM", "LP", "MP", "HP"]
FinishMode = Literal["cheapest", "match_deck"]
UnmatchedReason = Literal["unmatched", "out_of_stock"]
PriceSource = Literal["snapshot", "live"]


class OptimizeItem(BaseModel):
    oracle_id: uuid.UUID | None = None
    scryfall_id: uuid.UUID | None = None
    quantity: int = Field(ge=1)
    finish: Finish = "nonfoil"
    printing_locked: bool = False

    @model_validator(mode="after")
    def require_identity(self) -> OptimizeItem:
        if self.printing_locked and self.scryfall_id is None:
            raise ValueError("Locked printings need a scryfall_id")
        if self.oracle_id is None and self.scryfall_id is None:
            raise ValueError("Each item needs an oracle_id or a scryfall_id")
        return self


class OptimizeRequest(BaseModel):
    items: list[OptimizeItem]
    min_condition: Condition = "LP"
    finish_mode: FinishMode = "cheapest"
    exclude_basic_lands: bool = True
    enabled_stores: list[str] = Field(default_factory=lambda: ["card_kingdom", "mana_pool"])
    package_penalty_cents: int = Field(default=300, ge=0, le=1500)

    @field_validator("enabled_stores")
    @classmethod
    def require_stores(cls, stores: list[str]) -> list[str]:
        chosen: list[str] = []
        for store_id in stores:
            cleaned = store_id.strip()
            if cleaned and cleaned not in chosen:
                chosen.append(cleaned)
        if not chosen:
            raise ValueError("enabled_stores must include at least one store")
        return chosen


class BucketCard(BaseModel):
    name: str
    quantity: int
    scryfall_id: uuid.UUID
    oracle_id: uuid.UUID
    set_code: str
    collector_number: str
    finish: Finish
    condition: Condition
    unit_price_cents: int
    line_total_cents: int
    store_sku: str | None = None
    image_uri: str | None = None


class StoreBucket(BaseModel):
    store_id: str
    store_name: str
    cards: list[BucketCard]
    subtotal_cents: int
    shipping_cents: int
    estimated_packages: int
    free_shipping_delta_cents: int | None = Field(
        description="Cents still needed for free shipping. 0 means the order qualifies. Null when the store has no threshold.",
    )
    checkout_url: str
    mass_entry_text: str
    price_source: PriceSource
    notice: str | None = None


class SavingsSummary(BaseModel):
    optimized_total_cents: int
    baseline_store_id: str | None
    baseline_store_name: str | None
    baseline_total_cents: int | None
    savings_cents: int | None
    message: str | None


class UnmatchedCard(BaseModel):
    oracle_id: uuid.UUID | None
    scryfall_id: uuid.UUID | None
    name: str | None
    quantity: int
    finish: Finish
    reason: UnmatchedReason


class OptimizeResponse(BaseModel):
    buckets: list[StoreBucket]
    savings: SavingsSummary
    unmatched: list[UnmatchedCard]
    notices: list[str]
