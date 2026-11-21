"""POST /api/v1/deck/parse."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from app.core.redis import create_redis
from app.schemas.deck import ParseDeckRequest, ParsedDeckItem
from app.services.parser import (
    ArchidektSource,
    CsvSource,
    DeckImportError,
    classify_import,
    create_archidekt_client,
    load_archidekt_deck,
    parse_archidekt_payload,
    parse_manabox_csv,
    parse_text,
    resolve_with_database,
)

router = APIRouter(prefix="/api/v1", tags=["deck"])


@dataclass(frozen=True, slots=True)
class _Incoming:
    text: str | None
    url: str | None
    from_file: bool


@router.post("/deck/parse", response_model=list[ParsedDeckItem])
async def parse_deck(request: Request) -> list[ParsedDeckItem]:
    """Parse pasted text, a ManaBox CSV upload, or an Archidekt deck URL."""

    try:
        incoming = await _read_incoming(request)
        source = classify_import(incoming.text, None if incoming.from_file else incoming.url)
        if isinstance(source, ArchidektSource):
            payload = await _load_archidekt(source.deck_id)
            lines = parse_archidekt_payload(payload)
        elif isinstance(source, CsvSource):
            lines = parse_manabox_csv(source.text)
        else:
            lines = parse_text(source.text)
        if not lines:
            return []
        return await resolve_with_database(lines)
    except DeckImportError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc


async def _load_archidekt(deck_id: str) -> dict:
    redis = create_redis()
    client = create_archidekt_client()
    try:
        return await load_archidekt_deck(deck_id, redis, client)
    finally:
        await client.aclose()
        await redis.aclose()


async def _read_incoming(request: Request) -> _Incoming:
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type:
        form = await request.form()
        try:
            url = _form_text(form.get("url"))
            text = _form_text(form.get("text"))
            upload = None
            for key in ("file", "csv", "deck"):
                value = form.get(key)
                if value is not None and hasattr(value, "read"):
                    upload = value
                    break
            if upload is None:
                return _Incoming(text=text, url=url, from_file=False)
            raw = await upload.read()
            try:
                file_text = raw.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise DeckImportError(422, "invalid_csv", "Deck file must be UTF-8 text or CSV.") from exc
            return _Incoming(text=file_text, url=url, from_file=True)
        finally:
            await form.close()

    if content_type.startswith("text/csv") or content_type.startswith("text/plain"):
        return _Incoming(text=_decode_body(await request.body()), url=None, from_file=True)

    body = await request.body()
    if not body.strip():
        raise DeckImportError(
            422,
            "empty_deck",
            "Provide decklist text, a CSV file, or an Archidekt URL.",
        )
    try:
        payload = ParseDeckRequest.model_validate_json(body)
    except ValidationError as exc:
        raise DeckImportError(
            422,
            "invalid_body",
            "Request body must be JSON with text and/or url.",
        ) from exc
    return _Incoming(text=payload.text, url=payload.url, from_file=False)


def _decode_body(raw: bytes) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DeckImportError(422, "invalid_csv", "Deck file must be UTF-8 text or CSV.") from exc


def _form_text(value: object) -> str | None:
    if value is None or hasattr(value, "read"):
        return None
    text = str(value).strip()
    return text or None
