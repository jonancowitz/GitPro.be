"""Deck parse request and normalized card rows."""

from typing import Literal

from pydantic import BaseModel

Finish = Literal["nonfoil", "foil", "etched"]
MatchStatus = Literal["matched", "ambiguous", "unmatched"]


class ParseDeckRequest(BaseModel):
    text: str | None = None
    url: str | None = None


class ParsedDeckItem(BaseModel):
    raw_line: str
    quantity: int
    name: str
    set_code: str | None = None
    collector_number: str | None = None
    scryfall_id: str | None = None
    oracle_id: str | None = None
    finish: Finish = "nonfoil"
    printing_locked: bool = False
    is_basic_land: bool = False
    match_status: MatchStatus
    image_uri: str | None = None
    available_printings: int = 0
