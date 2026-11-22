"""Decklist parsing and Scryfall name resolution.

Text sources are Moxfield, Archidekt, EDHREC, and MTGO. ManaBox CSV prefers a
Scryfall id. Archidekt deck URLs are fetched from the public deck API and
cached in Redis. Moxfield URLs are refused because Cloudflare blocks them.
"""

from __future__ import annotations

import csv
import io
import json
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from redis.asyncio import Redis
from sqlalchemy import and_, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import SessionLocal
from app.models.card import Card
from app.schemas.deck import Finish, MatchStatus, ParsedDeckItem

USER_AGENT = "gitpro.be/1.0"
ARCHIDEKT_CACHE_TTL_SECONDS = 300
MOXFIELD_UNSUPPORTED_MESSAGE = (
    "Moxfield blocks direct server imports \u2014 click Export -> Copy for MTGO/Text "
    "on Moxfield and paste here."
)
BASIC_LAND_NAMES = frozenset({"plains", "island", "swamp", "mountain", "forest", "wastes"})

_CARD_LINE = re.compile(
    r"^\s*(?:SB:\s*)?(?P<quantity>\d+)\s*x?\s+(?P<body>\S.*?)\s*$",
    re.IGNORECASE,
)
_FINISH_SUFFIX = re.compile(
    r"^(?P<body>.*?)\s+(?:\*(?P<star>nonfoil|etched|foil|e|f)\*|\[(?P<bracket>nonfoil|etched|foil)\])\s*$",
    re.IGNORECASE,
)
_PRINTING_SUFFIX = re.compile(
    r"^(?P<name>.*\S)\s+[\(\[](?P<set>(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{2,8})[\)\]]"
    r"(?:\s+(?P<collector>(?=[A-Za-z0-9\u2605*-]*\d)[A-Za-z0-9\u2605*-]{1,16}))?\s*$"
)
_MOXFIELD_URL = re.compile(
    r"^(?:https?://)?(?:www\.)?moxfield\.com(?:[/?#].*)?$",
    re.IGNORECASE,
)
_ARCHIDEKT_URL = re.compile(
    r"^(?:https?://)?(?:www\.)?archidekt\.com/decks/(?P<id>\d+)(?:[/?#].*)?$",
    re.IGNORECASE,
)
_FINISH_FROM_TOKEN: dict[str, Finish] = {
    "f": "foil",
    "foil": "foil",
    "e": "etched",
    "etched": "etched",
    "nonfoil": "nonfoil",
}


class DeckImportError(Exception):
    """A deck import the API should return as a structured HTTP error."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ParsedLine:
    raw_line: str
    quantity: int
    name: str
    set_code: str | None = None
    collector_number: str | None = None
    scryfall_id: str | None = None
    finish: Finish = "nonfoil"


@dataclass(frozen=True, slots=True)
class CatalogCard:
    scryfall_id: uuid.UUID
    oracle_id: uuid.UUID
    name: str
    set_code: str
    collector_number: str
    is_basic_land: bool
    image_uri: str | None


@dataclass(frozen=True, slots=True)
class TextSource:
    text: str


@dataclass(frozen=True, slots=True)
class CsvSource:
    text: str


@dataclass(frozen=True, slots=True)
class ArchidektSource:
    deck_id: str


ImportSource = TextSource | CsvSource | ArchidektSource


def clean_card_name(name: str) -> str:
    """Collapse whitespace and normalize ``Front / Back`` to ``Front // Back``."""

    collapsed = re.sub(r"\s+", " ", name.replace("\u00a0", " ").strip())
    if " // " in collapsed or "/" not in collapsed:
        return collapsed
    parts = [part.strip() for part in re.split(r"\s*/\s*", collapsed) if part.strip()]
    if len(parts) >= 2:
        return " // ".join(parts)
    return collapsed


def canonicalize_name(name: str) -> str:
    return clean_card_name(name).casefold()


def canonical_scryfall_id(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return str(uuid.UUID(text))
    except (ValueError, AttributeError):
        return None


def archidekt_deck_api_url(deck_id: str) -> str:
    if not deck_id.isdigit():
        raise DeckImportError(422, "invalid_url", "Paste a decklist or an Archidekt deck URL.")
    return f"https://archidekt.com/api/decks/{deck_id}/"


def archidekt_cache_key(deck_id: str) -> str:
    return f"archidekt:deck:{deck_id}"


def classify_import(text: str | None, url: str | None) -> ImportSource:
    """Choose text, ManaBox CSV, or an Archidekt fetch. Moxfield URLs raise."""

    chosen_url = (url or "").strip()
    body = text if text and text.strip() else None
    if not chosen_url and body is not None and _is_lone_url(body):
        chosen_url = body.strip()
    if chosen_url:
        return _classify_url(chosen_url)
    if body is None:
        raise DeckImportError(
            422,
            "empty_deck",
            "Provide decklist text, a CSV file, or an Archidekt URL.",
        )
    if looks_like_manabox_csv(body):
        return CsvSource(body)
    return TextSource(body)


def looks_like_manabox_csv(text: str) -> bool:
    sample = text.lstrip("\ufeff")
    try:
        header = next(csv.reader(io.StringIO(sample)), None)
    except csv.Error:
        return False
    if not header or len(header) < 2:
        return False
    normalized = {cell.strip().casefold() for cell in header}
    has_name = "name" in normalized or "card name" in normalized
    has_quantity = bool(normalized & {"quantity", "qty", "count"})
    return has_name and has_quantity


def parse_text(text: str) -> list[ParsedLine]:
    """Parse Moxfield, Archidekt, EDHREC, and MTGO text lines.

    Section headers (``Deck``, ``Commander``, ``Sideboard``, ``Maybeboard``,
    and category lines such as ``Creatures (20)``) have no leading quantity
    and are skipped. Card lines under them are kept.
    """

    items: list[ParsedLine] = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("//") or stripped.startswith("#"):
            continue
        uncommented = re.sub(r"\s+#.*$", "", stripped).strip()
        match = _CARD_LINE.match(uncommented)
        if not match:
            continue
        quantity = int(match.group("quantity"))
        if quantity <= 0:
            continue
        body, finish = _split_finish(match.group("body").strip())
        name, set_code, collector_number = _split_printing(body)
        name = clean_card_name(name)
        if not name:
            continue
        items.append(
            ParsedLine(
                raw_line=stripped,
                quantity=quantity,
                name=name,
                set_code=set_code,
                collector_number=collector_number,
                finish=finish,
            )
        )
    return items


def parse_manabox_csv(text: str) -> list[ParsedLine]:
    """Parse a ManaBox CSV export. A Scryfall ID cell wins over name and set."""

    sample = text.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(sample))
    if not reader.fieldnames:
        raise DeckImportError(422, "invalid_csv", "ManaBox CSV must include Name and Quantity columns.")
    headers = _header_map(list(reader.fieldnames))
    name_key = _header(headers, "name", "card name")
    quantity_key = _header(headers, "quantity", "qty", "count")
    if name_key is None or quantity_key is None:
        raise DeckImportError(422, "invalid_csv", "ManaBox CSV must include Name and Quantity columns.")
    set_key = _header(headers, "set code", "set", "set_code")
    collector_key = _header(headers, "collector number", "collector_number", "collector", "number")
    foil_key = _header(headers, "foil", "finish")
    id_key = _header(headers, "scryfall id", "scryfall_id", "scryfallid")

    items: list[ParsedLine] = []
    for row in reader:
        name = clean_card_name(str(row.get(name_key) or ""))
        quantity = _positive_int(row.get(quantity_key))
        if not name or quantity is None:
            continue
        set_code = _clean_set(row.get(set_key) if set_key else None)
        collector_number = _clean_optional(row.get(collector_key) if collector_key else None)
        finish = _csv_finish(row.get(foil_key) if foil_key else None)
        scryfall_id = canonical_scryfall_id(row.get(id_key) if id_key else None)
        items.append(
            ParsedLine(
                raw_line=_csv_raw_line(quantity, name, set_code, collector_number, finish, scryfall_id),
                quantity=quantity,
                name=name,
                set_code=set_code,
                collector_number=collector_number,
                scryfall_id=scryfall_id,
                finish=finish,
            )
        )
    return items


def parse_archidekt_payload(payload: dict[str, Any]) -> list[ParsedLine]:
    """Normalize ``GET /api/decks/{id}/``.

    Cards that sit only in categories with ``includedInDeck`` false, such as
    Maybeboard, are omitted. ``card.uid`` is the Scryfall id when present.
    """

    included = _included_category_names(payload)
    items: list[ParsedLine] = []
    for entry in _archidekt_entries(payload):
        if not _entry_in_deck(entry, included):
            continue
        card = entry.get("card")
        if not isinstance(card, dict):
            continue
        name = _archidekt_name(card)
        quantity = _positive_int(entry.get("quantity", entry.get("qty", 1)))
        if not name or quantity is None:
            continue
        set_code = _archidekt_set(card)
        collector_number = _clean_optional(card.get("collectorNumber") or card.get("collector_number"))
        scryfall_id = canonical_scryfall_id(
            card.get("uid") or card.get("scryfallId") or card.get("scryfall_id")
        )
        finish = _modifier_finish(entry.get("modifier"))
        items.append(
            ParsedLine(
                raw_line=_csv_raw_line(quantity, name, set_code, collector_number, finish, scryfall_id),
                quantity=quantity,
                name=name,
                set_code=set_code,
                collector_number=collector_number,
                scryfall_id=scryfall_id,
                finish=finish,
            )
        )
    return items


def create_archidekt_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=httpx.Timeout(15.0),
        follow_redirects=True,
    )


async def fetch_archidekt_deck(deck_id: str, client: httpx.AsyncClient) -> dict[str, Any]:
    url = archidekt_deck_api_url(deck_id)
    try:
        response = await client.get(url)
    except httpx.HTTPError as exc:
        raise DeckImportError(
            502,
            "archidekt_unavailable",
            "Archidekt could not be reached. Paste the decklist instead.",
        ) from exc
    if response.status_code == 404:
        raise DeckImportError(422, "deck_not_found", "Archidekt deck was not found.")
    if response.status_code in {401, 403}:
        raise DeckImportError(422, "deck_unavailable", "That Archidekt deck is private or unavailable.")
    if response.status_code >= 400:
        raise DeckImportError(
            502,
            "archidekt_unavailable",
            "Archidekt could not be reached. Paste the decklist instead.",
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise DeckImportError(
            502,
            "archidekt_unavailable",
            "Archidekt could not be reached. Paste the decklist instead.",
        ) from exc
    if not isinstance(payload, dict):
        raise DeckImportError(
            502,
            "archidekt_unavailable",
            "Archidekt could not be reached. Paste the decklist instead.",
        )
    return payload


async def load_archidekt_deck(
    deck_id: str,
    redis: Redis,
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    """Return the Archidekt deck JSON, using a 5 minute Redis cache."""

    key = archidekt_cache_key(deck_id)
    cached = await _redis_get(redis, key)
    if cached:
        try:
            payload = json.loads(cached)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            return payload
    payload = await fetch_archidekt_deck(deck_id, client)
    await _redis_set(redis, key, json.dumps(payload), ARCHIDEKT_CACHE_TTL_SECONDS)
    return payload


def build_catalog_statement(lines: Sequence[ParsedLine]):
    """Select card rows that could resolve these lines, including DFC faces."""

    clauses: list[Any] = []
    ids: list[uuid.UUID] = []
    seen_ids: set[str] = set()
    for line in lines:
        if line.scryfall_id and line.scryfall_id not in seen_ids:
            seen_ids.add(line.scryfall_id)
            ids.append(uuid.UUID(line.scryfall_id))
    if ids:
        clauses.append(Card.scryfall_id.in_(ids))

    pair_clauses = []
    seen_pairs: set[tuple[str, str]] = set()
    for line in lines:
        if line.set_code and line.collector_number:
            pair = (line.set_code.lower(), line.collector_number.casefold())
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                pair_clauses.append(
                    and_(
                        Card.set_code == pair[0],
                        func.lower(Card.collector_number) == pair[1],
                    )
                )
    if pair_clauses:
        clauses.append(or_(*pair_clauses))

    exact_names, face_names = _name_lookup_keys(lines)
    name_clauses = []
    if exact_names:
        name_clauses.append(func.lower(Card.name).in_(sorted(exact_names)))
    lowered_name = func.lower(Card.name)
    for face in sorted(face_names):
        escaped = _like_escape(face)
        name_clauses.append(lowered_name.like(f"{escaped} // %", escape="\\"))
        name_clauses.append(lowered_name.like(f"% // {escaped}", escape="\\"))
        name_clauses.append(lowered_name.like(f"% // {escaped} // %", escape="\\"))
    if name_clauses:
        clauses.append(or_(*name_clauses))

    if not clauses:
        return select(Card).where(false())
    return select(Card).where(or_(*clauses))


async def query_catalog_cards(session: AsyncSession, lines: Sequence[ParsedLine]) -> list[CatalogCard]:
    """Load candidate rows, then every printing of each matched oracle id."""

    if not lines:
        return []
    result = await session.execute(build_catalog_statement(lines))
    matched = list(result.scalars().all())
    oracle_ids = {row.oracle_id for row in matched}
    if not oracle_ids:
        return []
    printings = await session.execute(select(Card).where(Card.oracle_id.in_(list(oracle_ids))))
    deduped: dict[uuid.UUID, CatalogCard] = {}
    for row in printings.scalars().all():
        card = _to_catalog(row)
        deduped[card.scryfall_id] = card
    return list(deduped.values())


def resolve_items(lines: Sequence[ParsedLine], cards: Sequence[CatalogCard]) -> list[ParsedDeckItem]:
    """Resolve parsed lines against an in-memory slice of the Scryfall catalog."""

    catalog = list(cards)
    return [_resolve_item(line, catalog) for line in lines]


async def resolve_with_database(lines: Sequence[ParsedLine]) -> list[ParsedDeckItem]:
    if not lines:
        return []
    async with SessionLocal() as session:
        cards = await query_catalog_cards(session, lines)
    return resolve_items(lines, cards)


def _resolve_item(line: ParsedLine, cards: Sequence[CatalogCard]) -> ParsedDeckItem:
    if line.scryfall_id:
        for card in cards:
            if str(card.scryfall_id) == line.scryfall_id:
                return _matched(line, card, cards, locked=True)

    wanted_set = line.set_code.lower() if line.set_code else None
    wanted_collector = line.collector_number.casefold() if line.collector_number else None
    if wanted_set and wanted_collector:
        printing_matches = [
            card
            for card in cards
            if card.set_code == wanted_set and card.collector_number.casefold() == wanted_collector
        ]
        named_matches = [card for card in printing_matches if _match_kind(line.name, card.name) != "none"]
        oracle_ids = {card.oracle_id for card in named_matches}
        if len(oracle_ids) > 1:
            return _unresolved(line, "ambiguous")
        if len(oracle_ids) == 1:
            return _matched(line, _prefer_display(named_matches), cards, locked=True)

    matched_oracles = _matching_oracles(line.name, cards)
    if len(matched_oracles) > 1:
        return _unresolved(line, "ambiguous")
    if len(matched_oracles) == 1:
        oracle_cards = [card for card in cards if card.oracle_id == matched_oracles[0]]
        chosen = _choose_display(line, oracle_cards)
        return _matched(line, chosen, cards, locked=_unique_set_lock(line, oracle_cards))
    return _unresolved(line, "unmatched")


def _matching_oracles(name: str, cards: Sequence[CatalogCard]) -> list[uuid.UUID]:
    exact: list[uuid.UUID] = []
    faces: list[uuid.UUID] = []
    seen_exact: set[uuid.UUID] = set()
    seen_face: set[uuid.UUID] = set()
    for card in cards:
        kind = _match_kind(name, card.name)
        if kind == "exact" and card.oracle_id not in seen_exact:
            seen_exact.add(card.oracle_id)
            exact.append(card.oracle_id)
        elif kind == "face" and card.oracle_id not in seen_face:
            seen_face.add(card.oracle_id)
            faces.append(card.oracle_id)
    return exact or faces


def _match_kind(query: str, card_name: str) -> Literal["exact", "face", "none"]:
    wanted = canonicalize_name(query)
    actual = canonicalize_name(card_name)
    if not wanted or not actual:
        return "none"
    if wanted == actual:
        return "exact"
    if wanted in _face_names(actual):
        return "face"
    return "none"


def _choose_display(line: ParsedLine, oracle_cards: Sequence[CatalogCard]) -> CatalogCard:
    wanted_set = line.set_code.lower() if line.set_code else None
    wanted_collector = line.collector_number.casefold() if line.collector_number else None
    if wanted_set and wanted_collector:
        exact = [
            card
            for card in oracle_cards
            if card.set_code == wanted_set and card.collector_number.casefold() == wanted_collector
        ]
        if exact:
            return _prefer_display(exact)
    if wanted_set:
        in_set = [card for card in oracle_cards if card.set_code == wanted_set]
        if in_set:
            return _prefer_display(in_set)
    return _prefer_display(oracle_cards)


def _unique_set_lock(line: ParsedLine, oracle_cards: Sequence[CatalogCard]) -> bool:
    if not line.set_code or line.collector_number:
        return False
    wanted_set = line.set_code.lower()
    return sum(1 for card in oracle_cards if card.set_code == wanted_set) == 1


def _prefer_display(cards: Sequence[CatalogCard]) -> CatalogCard:
    return min(
        cards,
        key=lambda card: (
            0 if card.image_uri else 1,
            card.set_code,
            card.collector_number,
            str(card.scryfall_id),
        ),
    )


def _matched(
    line: ParsedLine,
    card: CatalogCard,
    cards: Sequence[CatalogCard],
    *,
    locked: bool,
) -> ParsedDeckItem:
    printing_ids = {candidate.scryfall_id for candidate in cards if candidate.oracle_id == card.oracle_id}
    return ParsedDeckItem(
        raw_line=line.raw_line,
        quantity=line.quantity,
        name=card.name,
        set_code=card.set_code,
        collector_number=card.collector_number,
        scryfall_id=str(card.scryfall_id),
        oracle_id=str(card.oracle_id),
        finish=line.finish,
        printing_locked=locked,
        is_basic_land=card.is_basic_land,
        match_status="matched",
        image_uri=card.image_uri,
        available_printings=len(printing_ids),
    )


def _unresolved(line: ParsedLine, status: MatchStatus) -> ParsedDeckItem:
    return ParsedDeckItem(
        raw_line=line.raw_line,
        quantity=line.quantity,
        name=clean_card_name(line.name),
        set_code=line.set_code.lower() if line.set_code else None,
        collector_number=line.collector_number,
        scryfall_id=None,
        oracle_id=None,
        finish=line.finish,
        printing_locked=False,
        is_basic_land=canonicalize_name(line.name) in BASIC_LAND_NAMES,
        match_status=status,
        image_uri=None,
        available_printings=0,
    )


def _to_catalog(row: Card) -> CatalogCard:
    return CatalogCard(
        scryfall_id=row.scryfall_id,
        oracle_id=row.oracle_id,
        name=row.name,
        set_code=row.set_code.lower(),
        collector_number=str(row.collector_number),
        is_basic_land=bool(row.is_basic_land),
        image_uri=row.image_uri,
    )


def _name_lookup_keys(lines: Sequence[ParsedLine]) -> tuple[set[str], set[str]]:
    exact: set[str] = set()
    faces: set[str] = set()
    for line in lines:
        canonical = canonicalize_name(line.name)
        if not canonical:
            continue
        exact.add(canonical)
        for face in _face_names(canonical):
            exact.add(face)
            faces.add(face)
        if " // " not in canonical:
            faces.add(canonical)
    return exact, faces


def _face_names(canonical: str) -> list[str]:
    if " // " not in canonical:
        return []
    return [part for part in (piece.strip() for piece in canonical.split(" // ")) if part]


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _split_finish(body: str) -> tuple[str, Finish]:
    match = _FINISH_SUFFIX.match(body)
    if not match:
        return body, "nonfoil"
    token = (match.group("star") or match.group("bracket") or "").casefold()
    finish = _FINISH_FROM_TOKEN.get(token, "nonfoil")
    return match.group("body").strip(), finish


def _split_printing(body: str) -> tuple[str, str | None, str | None]:
    match = _PRINTING_SUFFIX.match(body.strip())
    if not match:
        return body.strip(), None, None
    return match.group("name").strip(), match.group("set").lower(), match.group("collector")


def _is_lone_url(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != 1:
        return False
    value = lines[0]
    if any(character.isspace() for character in value):
        return False
    lowered = value.casefold()
    return (
        lowered.startswith("http://")
        or lowered.startswith("https://")
        or "archidekt.com/" in lowered
        or lowered.startswith("archidekt.com")
        or "moxfield.com/" in lowered
        or lowered.startswith("moxfield.com")
    )


def _classify_url(value: str) -> ImportSource:
    trimmed = value.strip()
    if _MOXFIELD_URL.match(trimmed):
        raise DeckImportError(422, "unsupported_url", MOXFIELD_UNSUPPORTED_MESSAGE)
    match = _ARCHIDEKT_URL.match(trimmed)
    if match:
        return ArchidektSource(match.group("id"))
    raise DeckImportError(422, "invalid_url", "Paste a decklist or an Archidekt deck URL.")


def _header_map(fieldnames: list[str | None]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for field in fieldnames:
        if not field:
            continue
        mapping[field.strip().lstrip("\ufeff").casefold()] = field
    return mapping


def _header(headers: dict[str, str], *names: str) -> str | None:
    for name in names:
        if name in headers:
            return headers[name]
    return None


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float) and value.is_integer():
        parsed = int(value)
        return parsed if parsed > 0 else None
    text = str(value).strip()
    if text.isdigit():
        parsed = int(text)
        return parsed if parsed > 0 else None
    return None


def _clean_optional(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_set(value: object) -> str | None:
    text = _clean_optional(value)
    return text.lower() if text else None


def _csv_finish(value: object) -> Finish:
    token = str(value or "").strip().casefold()
    if token in {"etched", "etch", "e"}:
        return "etched"
    if token in {"foil", "foiled", "true", "yes", "y", "1", "f"}:
        return "foil"
    return "nonfoil"


def _modifier_finish(value: object) -> Finish:
    token = str(value or "").strip().casefold()
    if "etch" in token:
        return "etched"
    if "foil" in token:
        return "foil"
    return "nonfoil"


def _csv_raw_line(
    quantity: int,
    name: str,
    set_code: str | None,
    collector_number: str | None,
    finish: Finish,
    scryfall_id: str | None,
) -> str:
    line = f"{quantity} {name}"
    if set_code:
        line += f" ({set_code.upper()})"
        if collector_number:
            line += f" {collector_number}"
    if finish == "foil":
        line += " *F*"
    elif finish == "etched":
        line += " *E*"
    if scryfall_id:
        line += f" #{scryfall_id}"
    return line


def _included_category_names(payload: dict[str, Any]) -> set[str] | None:
    categories = payload.get("categories")
    if not isinstance(categories, list) or not categories:
        return None
    included: set[str] = set()
    for category in categories:
        if not isinstance(category, dict):
            continue
        name = category.get("name")
        if isinstance(name, str) and category.get("includedInDeck", True):
            included.add(name)
    return included


def _archidekt_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    cards = payload.get("cards")
    if isinstance(cards, list) and cards:
        return [entry for entry in cards if isinstance(entry, dict)]
    entries: list[dict[str, Any]] = []
    categories = payload.get("categories")
    if not isinstance(categories, list):
        return entries
    for category in categories:
        if not isinstance(category, dict):
            continue
        nested = category.get("cards")
        if isinstance(nested, list):
            entries.extend(entry for entry in nested if isinstance(entry, dict))
    return entries


def _entry_in_deck(entry: dict[str, Any], included: set[str] | None) -> bool:
    if included is None:
        return True
    categories = entry.get("categories") or []
    if not isinstance(categories, list) or not categories:
        return True
    return any(isinstance(name, str) and name in included for name in categories)


def _archidekt_name(card: dict[str, Any]) -> str:
    oracle = card.get("oracleCard") or card.get("oracle_card") or {}
    if isinstance(oracle, dict):
        name = oracle.get("name")
        if isinstance(name, str) and name.strip():
            return clean_card_name(name)
    for key in ("displayName", "display_name", "name"):
        value = card.get(key)
        if isinstance(value, str) and value.strip():
            return clean_card_name(value)
    return ""


def _archidekt_set(card: dict[str, Any]) -> str | None:
    edition = card.get("edition")
    set_code = None
    if isinstance(edition, dict):
        set_code = edition.get("editioncode") or edition.get("edition_code")
    if not set_code:
        set_code = card.get("setCode") or card.get("set_code")
    return _clean_set(set_code)


async def _redis_get(redis: Redis, key: str) -> str | None:
    try:
        value = await redis.get(key)
    except Exception:
        return None
    if value is None:
        return None
    return str(value)


async def _redis_set(redis: Redis, key: str, value: str, ttl_seconds: int) -> None:
    try:
        await redis.set(key, value, ex=ttl_seconds)
    except Exception:
        return
