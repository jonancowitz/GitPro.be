"""Printings and per-store median prices for the deck picker."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel

PickerFinish = Literal["nonfoil", "foil"]


class VendorMedian(BaseModel):
    store_id: str
    store_name: str
    median_cents: int | None


class FinishPrices(BaseModel):
    finish: PickerFinish
    vendors: list[VendorMedian]


class CardPrinting(BaseModel):
    scryfall_id: uuid.UUID
    oracle_id: uuid.UUID
    name: str
    set_code: str
    collector_number: str
    image_uri: str | None = None
    finishes: list[str]
    prices: list[FinishPrices]


class PrintingsResponse(BaseModel):
    oracle_id: uuid.UUID
    name: str
    printings: list[CardPrinting]
