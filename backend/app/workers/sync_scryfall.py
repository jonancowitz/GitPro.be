"""Daily Scryfall ``default_cards`` bulk sync.

Fetches bulk metadata, then stream-parses the catalog. Digital-only cards,
tokens, and art-series cards are skipped. ``--limit`` stops after that many
included cards so local seeding does not download the whole catalog into the
database.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import logging
import uuid
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import SessionLocal
from app.models.card import Card
from app.services.streams import ByteChunkReader, iter_json_items

logger = logging.getLogger(__name__)

BULK_METADATA_URL = "https://api.scryfall.com/bulk-data/default-cards"
USER_AGENT = "gitpro.be/1.0"
CARD_BATCH_SIZE = 500

EXCLUDED_LAYOUTS = frozenset({"token", "double_faced_token", "art_series", "emblem"})
EXCLUDED_SET_TYPES = frozenset({"token", "memorabilia"})
BASIC_LAND_NAMES = frozenset({"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes"})


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync Scryfall default_cards into the cards table.")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of included paper cards to upsert, for local seeding.",
    )
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be greater than or equal to 0")
    return args


def is_syncable_card(card: dict[str, Any]) -> bool:
    """Keep paper cards and drop digital-only, token, and art-series objects."""

    games = card.get("games") or []
    if "paper" not in games:
        return False
    if card.get("layout") in EXCLUDED_LAYOUTS:
        return False
    if card.get("set_type") in EXCLUDED_SET_TYPES:
        return False
    type_line = card.get("type_line") or ""
    if type_line.startswith("Token"):
        return False
    if not card.get("id") or not card.get("oracle_id") or not card.get("name") or not card.get("set"):
        return False
    if card.get("collector_number") in (None, ""):
        return False
    return True


def iter_cards_for_sync(cards: Iterable[dict[str, Any]], limit: int | None) -> Iterator[dict[str, Any]]:
    """Yield included cards, stopping once ``limit`` of them have been kept."""

    if limit is not None and limit < 0:
        raise ValueError("--limit must be greater than or equal to 0")
    kept = 0
    for card in cards:
        if not is_syncable_card(card):
            continue
        yield card
        kept += 1
        if limit is not None and kept >= limit:
            return


def _image_uri(card: dict[str, Any]) -> str | None:
    images = card.get("image_uris") or {}
    if not images:
        faces = card.get("card_faces") or []
        if faces:
            images = faces[0].get("image_uris") or {}
    uri = images.get("small") or images.get("normal")
    return str(uri) if uri else None


def card_record(card: dict[str, Any], updated_at: datetime) -> dict[str, Any]:
    return {
        "scryfall_id": uuid.UUID(str(card["id"])),
        "oracle_id": uuid.UUID(str(card["oracle_id"])),
        "name": card["name"],
        "set_code": str(card["set"]).lower(),
        "collector_number": str(card["collector_number"]),
        "is_basic_land": card["name"] in BASIC_LAND_NAMES,
        "image_uri": _image_uri(card),
        "finishes": list(card.get("finishes") or []),
        "updated_at": updated_at,
    }


def bulk_download(metadata: dict[str, Any]) -> tuple[str, str]:
    """Return ``(url, format)`` from Scryfall bulk metadata.

    The live default-cards feed publishes ``jsonl_download_uri``. Older
    metadata used a JSON-array ``download_uri``.
    """

    jsonl_uri = metadata.get("jsonl_download_uri")
    if jsonl_uri:
        return str(jsonl_uri), "jsonl"
    download_uri = metadata.get("download_uri")
    if download_uri:
        return str(download_uri), "json"
    raise ValueError("Scryfall bulk metadata did not include a download URI")


def _iter_jsonl_gzip(chunks: Iterable[bytes]) -> Iterator[dict[str, Any]]:
    with gzip.GzipFile(fileobj=ByteChunkReader(chunks)) as handle:
        for line in handle:
            stripped = line.strip()
            if stripped:
                yield json.loads(stripped)


def iter_default_cards() -> Iterator[dict[str, Any]]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    timeout = httpx.Timeout(60.0, read=600.0)
    with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
        metadata_response = client.get(BULK_METADATA_URL)
        metadata_response.raise_for_status()
        url, feed_format = bulk_download(metadata_response.json())
        with client.stream("GET", url) as response:
            response.raise_for_status()
            chunks = response.iter_bytes()
            if feed_format == "jsonl":
                yield from _iter_jsonl_gzip(chunks)
            else:
                yield from iter_json_items(chunks, "item")


def build_card_upsert(rows: list[dict[str, Any]]):
    statement = pg_insert(Card).values(rows)
    excluded = statement.excluded
    return statement.on_conflict_do_update(
        index_elements=["scryfall_id"],
        set_={
            "oracle_id": excluded.oracle_id,
            "name": excluded.name,
            "set_code": excluded.set_code,
            "collector_number": excluded.collector_number,
            "is_basic_land": excluded.is_basic_land,
            "image_uri": excluded.image_uri,
            "finishes": excluded.finishes,
            "updated_at": excluded.updated_at,
        },
    )


async def upsert_card_rows(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    by_id: dict[uuid.UUID, dict[str, Any]] = {}
    for row in rows:
        by_id[row["scryfall_id"]] = row
    if by_id:
        await session.execute(build_card_upsert(list(by_id.values())))


async def sync_scryfall(
    session: AsyncSession,
    *,
    limit: int | None = None,
    cards: Iterable[dict[str, Any]] | None = None,
    batch_size: int = CARD_BATCH_SIZE,
) -> int:
    updated_at = datetime.now(timezone.utc)
    source = cards if cards is not None else iter_default_cards()
    batch: list[dict[str, Any]] = []
    kept = 0
    for card in iter_cards_for_sync(source, limit):
        batch.append(card_record(card, updated_at))
        kept += 1
        if len(batch) >= batch_size:
            await upsert_card_rows(session, batch)
            batch.clear()
            await session.commit()
    if batch:
        await upsert_card_rows(session, batch)
        await session.commit()
    return kept


async def _run(limit: int | None) -> int:
    async with SessionLocal() as session:
        kept = await sync_scryfall(session, limit=limit)
    logger.info("Upserted %s Scryfall cards", kept)
    return kept


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args(argv)
    asyncio.run(_run(args.limit))


if __name__ == "__main__":
    main()
