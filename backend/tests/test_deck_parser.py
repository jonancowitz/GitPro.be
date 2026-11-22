"""Milestone 3 deck parser: text formats, ManaBox CSV, Archidekt, Moxfield, DFCs."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import httpx
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from app.api.routes import deck as deck_routes
from app.main import app
from app.models.card import Card
from app.schemas.deck import ParsedDeckItem
from app.services.parser import (
    MOXFIELD_UNSUPPORTED_MESSAGE,
    CatalogCard,
    DeckImportError,
    ParsedLine,
    build_catalog_statement,
    create_archidekt_client,
    fetch_archidekt_deck,
    load_archidekt_deck,
    parse_archidekt_payload,
    parse_manabox_csv,
    parse_text,
    query_catalog_cards,
    resolve_items,
)

MOXFIELD_MESSAGE = (
    "Moxfield blocks direct server imports \u2014 click Export -> Copy for MTGO/Text "
    "on Moxfield and paste here."
)
SOL_RING_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
SOL_RING_ORACLE = uuid.UUID("22222222-2222-4222-8222-222222222222")
SOL_RING_OTHER = uuid.UUID("33333333-3333-4333-8333-333333333333")
DELVER_ORACLE = uuid.UUID("44444444-4444-4444-8444-444444444444")
DELVER_SOI = uuid.UUID("55555555-5555-4555-8555-555555555555")
DELVER_MID = uuid.UUID("66666666-6666-4666-8666-666666666666")
FIRE_ID = uuid.UUID("77777777-7777-4777-8777-777777777777")
FIRE_ORACLE = uuid.UUID("88888888-8888-4888-8888-888888888888")
SPLIT_ID = uuid.UUID("99999999-9999-4999-8999-999999999999")
SPLIT_ORACLE = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
SHARD_LEFT = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
SHARD_RIGHT = uuid.UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
LEFT_ORACLE = uuid.UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
RIGHT_ORACLE = uuid.UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
ISLAND_ID = uuid.UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")
ISLAND_ORACLE = uuid.UUID("12121212-1212-4212-8212-121212121212")
BOLT_ORACLE = uuid.UUID("13131313-1313-4313-8313-131313131313")
BOLT_LEA = uuid.UUID("14141414-1414-4414-8414-141414141414")
BOLT_M10 = uuid.UUID("15151515-1515-4515-8515-151515151515")
BOLT_2XM = uuid.UUID("16161616-1616-4616-8616-161616161616")
TOWER_ORACLE = uuid.UUID("17171717-1717-4717-8717-171717171717")
TOWER_CMM = uuid.UUID("18181818-1818-4818-8818-181818181818")
TOWER_LTC = uuid.UUID("19191919-1919-4919-8919-191919191919")

DELVER_NAME = "Delver of Secrets // Insectile Aberration"
DELVER_IMAGE = "https://cards.scryfall.io/small/front/d/e/delver-soi.jpg"
SOL_RING_IMAGE = "https://cards.scryfall.io/small/front/s/o/sol-ring-ltc.jpg"


def _card(
    scryfall_id: uuid.UUID,
    oracle_id: uuid.UUID,
    name: str,
    set_code: str,
    collector_number: str,
    *,
    image_uri: str | None = "https://cards.scryfall.io/small/front/x/y/card.jpg",
    is_basic_land: bool = False,
) -> CatalogCard:
    return CatalogCard(
        scryfall_id=scryfall_id,
        oracle_id=oracle_id,
        name=name,
        set_code=set_code,
        collector_number=collector_number,
        is_basic_land=is_basic_land,
        image_uri=image_uri,
    )


def _line(
    name: str,
    *,
    quantity: int = 1,
    set_code: str | None = None,
    collector_number: str | None = None,
    scryfall_id: str | None = None,
    finish: str = "nonfoil",
) -> ParsedLine:
    return ParsedLine(
        raw_line=f"{quantity} {name}",
        quantity=quantity,
        name=name,
        set_code=set_code,
        collector_number=collector_number,
        scryfall_id=scryfall_id,
        finish=finish,  # type: ignore[arg-type]
    )


def _catalog() -> list[CatalogCard]:
    return [
        _card(SOL_RING_ID, SOL_RING_ORACLE, "Sol Ring", "ltc", "284", image_uri=SOL_RING_IMAGE),
        _card(
            SOL_RING_OTHER,
            SOL_RING_ORACLE,
            "Sol Ring",
            "cmm",
            "398",
            image_uri="https://cards.scryfall.io/small/front/s/o/sol-ring-cmm.jpg",
        ),
        _card(DELVER_SOI, DELVER_ORACLE, DELVER_NAME, "soi", "51", image_uri=DELVER_IMAGE),
        _card(DELVER_MID, DELVER_ORACLE, DELVER_NAME, "mid", "52", image_uri=None),
        _card(FIRE_ID, FIRE_ORACLE, "Fire", "lea", "1", image_uri="https://cards.scryfall.io/small/front/f/i/fire.jpg"),
        _card(SPLIT_ID, SPLIT_ORACLE, "Fire // Ice", "mh2", "123"),
        _card(SHARD_LEFT, LEFT_ORACLE, "Shard // Left", "dis", "1"),
        _card(SHARD_RIGHT, RIGHT_ORACLE, "Shard // Right", "dis", "2"),
        _card(ISLAND_ID, ISLAND_ORACLE, "Island", "znr", "380", is_basic_land=True),
        _card(BOLT_LEA, BOLT_ORACLE, "Lightning Bolt", "lea", "161", image_uri=None),
        _card(
            BOLT_M10,
            BOLT_ORACLE,
            "Lightning Bolt",
            "m10",
            "146",
            image_uri="https://cards.scryfall.io/small/front/b/o/bolt-m10.jpg",
        ),
        _card(BOLT_2XM, BOLT_ORACLE, "Lightning Bolt", "2xm", "1", image_uri=None),
        _card(TOWER_CMM, TOWER_ORACLE, "Command Tower", "cmm", "420"),
        _card(TOWER_LTC, TOWER_ORACLE, "Command Tower", "ltc", "333"),
    ]


def _names(text: str) -> list[str]:
    return [item.name for item in parse_text(text)]


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.sets: list[tuple[str, str, int | None]] = []

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.values[key] = value
        self.sets.append((key, value, ex))
        return True

    async def aclose(self) -> None:
        return None


class RecordingClient:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.urls: list[str] = []

    async def get(self, url: str) -> httpx.Response:
        self.urls.append(url)
        return httpx.Response(self.status_code, json=self.payload)

    async def aclose(self) -> None:
        return None


class FakeResult:
    def __init__(self, rows: list[Card]) -> None:
        self._rows = rows

    def scalars(self) -> FakeResult:
        return self

    def all(self) -> list[Card]:
        return self._rows


class FakeSession:
    def __init__(self, batches: list[list[Card]]) -> None:
        self.batches = batches
        self.statements: list[object] = []
        self.calls = 0

    async def execute(self, statement: object) -> FakeResult:
        self.statements.append(statement)
        rows = self.batches[min(self.calls, len(self.batches) - 1)]
        self.calls += 1
        return FakeResult(rows)


def _orm_card(**overrides: object) -> Card:
    data: dict[str, object] = {
        "scryfall_id": DELVER_SOI,
        "oracle_id": DELVER_ORACLE,
        "name": DELVER_NAME,
        "set_code": "soi",
        "collector_number": "51",
        "is_basic_land": False,
        "image_uri": DELVER_IMAGE,
        "finishes": ["nonfoil"],
        "updated_at": datetime(2026, 11, 20, tzinfo=timezone.utc),
    }
    data.update(overrides)
    return Card(**data)  # type: ignore[arg-type]


def _use_catalog(monkeypatch, cards: list[CatalogCard]) -> None:
    async def resolve(lines: list[ParsedLine]) -> list[ParsedDeckItem]:
        return resolve_items(lines, cards)

    monkeypatch.setattr(deck_routes, "resolve_with_database", resolve)


def test_moxfield_archidekt_edhrec_and_mtgo_text() -> None:
    moxfield = parse_text("1 Sol Ring (LTC) 284 *F*\n1 The One Ring (LTR) 451 *E*\n")
    assert moxfield[0].quantity == 1
    assert moxfield[0].name == "Sol Ring"
    assert moxfield[0].set_code == "ltc"
    assert moxfield[0].collector_number == "284"
    assert moxfield[0].finish == "foil"
    assert moxfield[1].name == "The One Ring"
    assert moxfield[1].set_code == "ltr"
    assert moxfield[1].collector_number == "451"
    assert moxfield[1].finish == "etched"

    archidekt = parse_text("1x Command Tower [CMM]\n2x Thought Vessel [40K] 123\n1 Card (MH2) 220a\n")
    assert [(item.quantity, item.name, item.set_code, item.collector_number) for item in archidekt] == [
        (1, "Command Tower", "cmm", None),
        (2, "Thought Vessel", "40k", "123"),
        (1, "Card", "mh2", "220a"),
    ]
    assert archidekt[0].finish == "nonfoil"

    edhrec = _names("Creatures (1)\n1 Birds of Paradise\n\nInstants (1)\n1 Swords to Plowshares\n")
    assert edhrec == ["Birds of Paradise", "Swords to Plowshares"]

    mtgo = parse_text("4 Lightning Bolt\nSB: 2 Nature's Claim\nSideboard\n3 Smash to Smithereens\n")
    assert [(item.quantity, item.name) for item in mtgo] == [
        (4, "Lightning Bolt"),
        (2, "Nature's Claim"),
        (3, "Smash to Smithereens"),
    ]


def test_section_headers_finishes_and_double_faced_text() -> None:
    text = """
    Deck
    1 Sol Ring (LTC) 284 *F*
    Commander
    1x Command Tower [CMM]
    Sideboard
    4 Lightning Bolt
    Maybeboard
    1 Swords to Plowshares
    // comment
    1 Delver of Secrets // Insectile Aberration (SOI) 51
    1 Delver of Secrets / Insectile Aberration
    1 Sol Ring [foil]
    """
    items = parse_text(text)
    assert [item.name for item in items] == [
        "Sol Ring",
        "Command Tower",
        "Lightning Bolt",
        "Swords to Plowshares",
        DELVER_NAME,
        DELVER_NAME,
        "Sol Ring",
    ]
    assert items[0].finish == "foil"
    assert items[0].set_code == "ltc"
    assert items[0].collector_number == "284"
    assert items[4].set_code == "soi"
    assert items[4].collector_number == "51"
    assert items[5].set_code is None
    assert items[6].finish == "foil"
    assert "Deck" not in {item.name for item in items}
    assert "Maybeboard" not in {item.name for item in items}


def test_manabox_csv_prefers_scryfall_id() -> None:
    wrong_name_id = str(SOL_RING_ID)
    csv_text = (
        "Name,Set code,Collector number,Foil,Quantity,Scryfall ID\n"
        f'"Atraxa, Praetors\' Voice",cmm,28,etched,1,{wrong_name_id}\n'
        "Lightning Bolt,lea,161,normal,4,\n"
        "Sol Ring,zzz,9,foil,2,\n"
    )
    items = parse_manabox_csv(csv_text)
    assert items[0].name == "Atraxa, Praetors' Voice"
    assert items[0].scryfall_id == wrong_name_id
    assert items[0].finish == "etched"
    assert items[0].quantity == 1
    assert items[0].set_code == "cmm"
    assert items[0].collector_number == "28"
    assert items[1].scryfall_id is None
    assert items[1].finish == "nonfoil"
    assert items[1].set_code == "lea"
    assert items[1].collector_number == "161"
    assert items[1].quantity == 4
    assert items[2].scryfall_id is None
    assert items[2].finish == "foil"

    resolved = resolve_items(
        [
            ParsedLine(
                raw_line=items[0].raw_line,
                quantity=1,
                name="Lightning Bolt",
                set_code="lea",
                collector_number="161",
                scryfall_id=wrong_name_id,
                finish="nonfoil",
            )
        ],
        _catalog(),
    )
    assert resolved[0].name == "Sol Ring"
    assert resolved[0].scryfall_id == wrong_name_id
    assert resolved[0].set_code == "ltc"
    assert resolved[0].collector_number == "284"
    assert resolved[0].printing_locked is True
    assert resolved[0].image_uri == SOL_RING_IMAGE
    assert resolved[0].available_printings == 2

    by_printing = resolve_items([items[1]], _catalog())
    assert by_printing[0].name == "Lightning Bolt"
    assert by_printing[0].scryfall_id == str(BOLT_LEA)
    assert by_printing[0].printing_locked is True
    assert by_printing[0].set_code == "lea"
    assert by_printing[0].available_printings == 3


def test_archidekt_payload_uses_scryfall_uid_and_skips_maybeboard() -> None:
    payload = {
        "categories": [
            {"name": "Commander", "includedInDeck": True},
            {"name": "Maybeboard", "includedInDeck": False},
        ],
        "cards": [
            {
                "quantity": 1,
                "categories": ["Commander"],
                "modifier": "Foil",
                "card": {
                    "uid": str(SOL_RING_ID),
                    "collectorNumber": "284",
                    "edition": {"editioncode": "LTC"},
                    "oracleCard": {"name": "Sol Ring"},
                },
            },
            {
                "quantity": 1,
                "categories": ["Maybeboard"],
                "modifier": "Etched",
                "card": {
                    "uid": "not-a-uuid",
                    "collectorNumber": "1",
                    "edition": {"editioncode": "lea"},
                    "oracleCard": {"name": "Black Lotus"},
                },
            },
            {
                "quantity": 1,
                "categories": ["Commander", "Maybeboard"],
                "modifier": "Normal",
                "card": {
                    "uid": str(DELVER_SOI),
                    "collectorNumber": "51",
                    "edition": {"editioncode": "soi"},
                    "oracleCard": {"name": "Delver of Secrets / Insectile Aberration"},
                },
            },
        ],
    }
    items = parse_archidekt_payload(payload)
    assert [item.name for item in items] == ["Sol Ring", DELVER_NAME]
    assert items[0].finish == "foil"
    assert items[0].scryfall_id == str(SOL_RING_ID)
    assert items[0].set_code == "ltc"
    assert items[0].collector_number == "284"
    assert items[1].scryfall_id == str(DELVER_SOI)
    assert items[1].finish == "nonfoil"

    resolved = resolve_items(items, _catalog())
    assert resolved[0].printing_locked is True
    assert resolved[0].image_uri == SOL_RING_IMAGE
    assert resolved[0].available_printings == 2
    assert resolved[1].name == DELVER_NAME
    assert resolved[1].match_status == "matched"


def test_double_faced_name_resolution() -> None:
    catalog = _catalog()
    front = resolve_items([_line("Delver of Secrets")], catalog)[0]
    full = resolve_items([_line(DELVER_NAME)], catalog)[0]
    slash = resolve_items([_line("Delver of Secrets / Insectile Aberration")], catalog)[0]
    back = resolve_items([_line("Insectile Aberration")], catalog)[0]

    for item in (front, full, slash, back):
        assert item.match_status == "matched"
        assert item.name == DELVER_NAME
        assert item.oracle_id == str(DELVER_ORACLE)
        assert item.available_printings == 2
        assert item.image_uri
        assert item.printing_locked is False

    locked = resolve_items(
        [_line(DELVER_NAME, set_code="soi", collector_number="51")],
        catalog,
    )[0]
    assert locked.printing_locked is True
    assert locked.scryfall_id == str(DELVER_SOI)
    assert locked.set_code == "soi"
    assert locked.collector_number == "51"
    assert locked.image_uri == DELVER_IMAGE
    assert locked.available_printings == 2

    case_collector = resolve_items(
        [_line("Lightning Bolt", set_code="M10", collector_number="146")],
        catalog,
    )[0]
    assert case_collector.printing_locked is True
    assert case_collector.collector_number == "146"
    assert case_collector.set_code == "m10"


def test_exact_name_beats_face_and_shared_faces_are_ambiguous() -> None:
    catalog = _catalog()
    fire = resolve_items([_line("Fire")], catalog)[0]
    assert fire.match_status == "matched"
    assert fire.name == "Fire"
    assert fire.oracle_id == str(FIRE_ORACLE)

    ice = resolve_items([_line("Ice")], catalog)[0]
    assert ice.match_status == "matched"
    assert ice.name == "Fire // Ice"
    assert ice.oracle_id == str(SPLIT_ORACLE)

    shard = resolve_items([_line("Shard")], catalog)[0]
    assert shard.match_status == "ambiguous"
    assert shard.scryfall_id is None
    assert shard.image_uri is None
    assert shard.available_printings == 0

    specific = resolve_items([_line("Shard // Left")], catalog)[0]
    assert specific.match_status == "matched"
    assert specific.oracle_id == str(LEFT_ORACLE)


def test_printing_identity_does_not_replace_a_different_card() -> None:
    trash_id = uuid.UUID("20202020-2020-4020-8020-202020202020")
    trash_oracle = uuid.UUID("21212121-2121-4121-8121-212121212121")
    catalog = [
        *_catalog(),
        _card(trash_id, trash_oracle, "Trash for Treasure", "2xm", "148"),
    ]

    bolt = resolve_items(
        [_line("Lightning Bolt", set_code="2XM", collector_number="148")],
        catalog,
    )[0]
    assert bolt.match_status == "matched"
    assert bolt.name == "Lightning Bolt"
    assert bolt.oracle_id == str(BOLT_ORACLE)
    assert bolt.printing_locked is False
    assert bolt.collector_number != "148"

    still_locked = resolve_items(
        [_line("Lightning Bolt", set_code="2xm", collector_number="1")],
        catalog,
    )[0]
    assert still_locked.match_status == "matched"
    assert still_locked.name == "Lightning Bolt"
    assert still_locked.printing_locked is True
    assert still_locked.collector_number == "1"

    face = resolve_items(
        [_line("Insectile Aberration", set_code="soi", collector_number="51")],
        catalog,
    )[0]
    assert face.match_status == "matched"
    assert face.name == DELVER_NAME
    assert face.printing_locked is True
    assert face.collector_number == "51"

    unknown = resolve_items(
        [_line("Not a Real Card", set_code="2xm", collector_number="148")],
        catalog,
    )[0]
    assert unknown.match_status == "unmatched"
    assert unknown.name == "Not a Real Card"
    assert unknown.scryfall_id is None


def test_unmatched_basic_lands_and_printing_metadata() -> None:
    catalog = _catalog()
    missing = resolve_items([_line("Not a Real Card", set_code="ltr", collector_number="1")], catalog)[0]
    assert missing.match_status == "unmatched"
    assert missing.image_uri is None
    assert missing.available_printings == 0
    assert missing.scryfall_id is None
    assert missing.oracle_id is None
    assert missing.set_code == "ltr"
    assert missing.is_basic_land is False

    plains = resolve_items([_line("Plains")], catalog)[0]
    assert plains.match_status == "unmatched"
    assert plains.is_basic_land is True

    snow = resolve_items([_line("Snow-Covered Island")], catalog)[0]
    assert snow.is_basic_land is False

    island = resolve_items([_line("Island")], catalog)[0]
    assert island.match_status == "matched"
    assert island.is_basic_land is True
    assert island.available_printings == 1

    bolt = resolve_items([_line("Lightning Bolt")], catalog)[0]
    assert bolt.match_status == "matched"
    assert bolt.printing_locked is False
    assert bolt.available_printings == 3
    assert bolt.set_code == "m10"
    assert bolt.image_uri == "https://cards.scryfall.io/small/front/b/o/bolt-m10.jpg"
    assert bolt.oracle_id == str(BOLT_ORACLE)

    tower = resolve_items([_line("Command Tower", set_code="cmm")], catalog)[0]
    assert tower.printing_locked is True
    assert tower.set_code == "cmm"
    assert tower.scryfall_id == str(TOWER_CMM)
    assert tower.available_printings == 2


def test_catalog_query_matches_double_faced_front_name() -> None:
    statement = build_catalog_statement([_line("Delver of Secrets")])
    compiled = statement.compile(dialect=postgresql.dialect())
    blob = " ".join(str(value).casefold() for value in compiled.params.values())
    assert "delver of secrets // %" in blob
    assert "% // delver of secrets" in blob
    sql = str(compiled).casefold()
    assert "like" in sql


def test_loader_expands_to_every_printing() -> None:
    first = _orm_card()
    second = _orm_card(
        scryfall_id=DELVER_MID,
        set_code="mid",
        collector_number="52",
        image_uri="https://cards.scryfall.io/small/front/d/e/delver-mid.jpg",
    )
    session = FakeSession([[first], [first, second]])
    cards = asyncio.run(query_catalog_cards(session, [_line("Delver of Secrets")]))  # type: ignore[arg-type]
    assert {card.scryfall_id for card in cards} == {DELVER_SOI, DELVER_MID}
    assert cards[0].name == DELVER_NAME
    assert len(session.statements) == 2
    assert "oracle_id" in str(session.statements[1]).casefold()


def test_archidekt_fetch_url_cache_and_missing_deck() -> None:
    payload = {"cards": [], "categories": []}
    client = RecordingClient(payload)
    redis = FakeRedis()

    async def load_twice() -> None:
        first = await load_archidekt_deck("99", redis, client)  # type: ignore[arg-type]
        second = await load_archidekt_deck("99", redis, client)  # type: ignore[arg-type]
        assert first == payload
        assert second == payload

    asyncio.run(load_twice())
    assert client.urls == ["https://archidekt.com/api/decks/99/"]
    assert redis.sets[0][0] == "archidekt:deck:99"
    assert redis.sets[0][2] == 300

    missing = RecordingClient({"detail": "missing"}, status_code=404)

    async def missing_deck() -> None:
        await fetch_archidekt_deck("5", missing)  # type: ignore[arg-type]

    try:
        asyncio.run(missing_deck())
    except DeckImportError as exc:
        assert exc.status_code == 422
        assert exc.code == "deck_not_found"
    else:
        raise AssertionError("missing Archidekt deck should fail")


def test_archidekt_client_sends_user_agent() -> None:
    client = create_archidekt_client()
    try:
        assert client.headers["user-agent"] == "gitpro.be/1.0"
    finally:
        asyncio.run(client.aclose())


def test_parse_route_returns_scryfall_metadata(monkeypatch) -> None:
    _use_catalog(monkeypatch, _catalog())
    text = "\n".join(
        [
            "Deck",
            "1 Sol Ring (LTC) 284 *F*",
            "Commander",
            "1x Command Tower [CMM]",
            "Sideboard",
            "4 Lightning Bolt",
            "Maybeboard",
            "1 Delver of Secrets",
        ]
    )
    with TestClient(app) as client:
        response = client.post("/api/v1/deck/parse", json={"text": text})
    assert response.status_code == 200
    body = response.json()
    by_name = {item["name"]: item for item in body}
    assert by_name["Sol Ring"]["finish"] == "foil"
    assert by_name["Sol Ring"]["set_code"] == "ltc"
    assert by_name["Sol Ring"]["image_uri"] == SOL_RING_IMAGE
    assert by_name["Sol Ring"]["available_printings"] == 2
    assert by_name["Sol Ring"]["printing_locked"] is True
    assert by_name["Command Tower"]["set_code"] == "cmm"
    assert by_name["Command Tower"]["available_printings"] == 2
    assert by_name["Lightning Bolt"]["available_printings"] == 3
    assert by_name["Lightning Bolt"]["image_uri"]
    assert by_name[DELVER_NAME]["match_status"] == "matched"
    assert by_name[DELVER_NAME]["available_printings"] == 2
    assert by_name[DELVER_NAME]["image_uri"] == DELVER_IMAGE


def test_parse_route_accepts_manabox_csv_upload(monkeypatch) -> None:
    _use_catalog(monkeypatch, _catalog())
    csv_text = (
        "Name,Set code,Collector number,Foil,Quantity,Scryfall ID\n"
        f"Wrong Name,zzz,1,foil,2,{SOL_RING_ID}\n"
    )
    with TestClient(app) as client:
        pasted = client.post("/api/v1/deck/parse", json={"text": csv_text})
        uploaded = client.post(
            "/api/v1/deck/parse",
            files={"file": ("manabox.csv", csv_text.encode(), "text/csv")},
        )
    assert pasted.status_code == 200
    assert uploaded.status_code == 200
    for response in (pasted, uploaded):
        item = response.json()[0]
        assert item["name"] == "Sol Ring"
        assert item["scryfall_id"] == str(SOL_RING_ID)
        assert item["finish"] == "foil"
        assert item["quantity"] == 2
        assert item["printing_locked"] is True
        assert item["set_code"] == "ltc"
        assert item["image_uri"] == SOL_RING_IMAGE
        assert item["available_printings"] == 2


def test_moxfield_url_returns_paste_instruction(monkeypatch) -> None:
    async def fail(_lines: list[ParsedLine]) -> list[ParsedDeckItem]:
        raise AssertionError("Moxfield URLs must not hit the catalog")

    monkeypatch.setattr(deck_routes, "resolve_with_database", fail)
    urls = [
        "https://www.moxfield.com/decks/abc123",
        "https://moxfield.com/decks/abc123",
        "http://moxfield.com/decks/abc123/",
        "moxfield.com/decks/abc123",
    ]
    with TestClient(app) as client:
        for url in urls:
            for payload in ({"url": url}, {"text": url}):
                response = client.post("/api/v1/deck/parse", json=payload)
                assert response.status_code == 422
                body = response.json()
                assert body["detail"]["code"] == "unsupported_url"
                assert body["detail"]["message"] == MOXFIELD_MESSAGE
    assert MOXFIELD_UNSUPPORTED_MESSAGE == MOXFIELD_MESSAGE


def test_archidekt_route_caches_deck_for_five_minutes(monkeypatch) -> None:
    _use_catalog(monkeypatch, _catalog())
    payload = {
        "categories": [
            {"name": "Commander", "includedInDeck": True},
            {"name": "Maybeboard", "includedInDeck": False},
        ],
        "cards": [
            {
                "quantity": 1,
                "categories": ["Commander"],
                "modifier": "Foil",
                "card": {
                    "uid": str(SOL_RING_ID),
                    "collectorNumber": "284",
                    "edition": {"editioncode": "ltc"},
                    "oracleCard": {"name": "Sol Ring"},
                },
            },
            {
                "quantity": 1,
                "categories": ["Maybeboard"],
                "modifier": "Normal",
                "card": {
                    "collectorNumber": "1",
                    "edition": {"editioncode": "lea"},
                    "oracleCard": {"name": "Black Lotus"},
                },
            },
        ],
    }
    redis = FakeRedis()
    client = RecordingClient(payload)
    monkeypatch.setattr(deck_routes, "create_redis", lambda: redis)
    monkeypatch.setattr(deck_routes, "create_archidekt_client", lambda: client)
    with TestClient(app) as api:
        first = api.post("/api/v1/deck/parse", json={"url": "https://archidekt.com/decks/99/my-deck"})
        second = api.post(
            "/api/v1/deck/parse",
            json={"text": "https://www.archidekt.com/decks/99/"},
        )
    assert first.status_code == 200
    assert second.json() == first.json()
    assert client.urls == ["https://archidekt.com/api/decks/99/"]
    assert redis.sets[0][2] == 300
    item = first.json()[0]
    assert item["name"] == "Sol Ring"
    assert item["finish"] == "foil"
    assert item["image_uri"] == SOL_RING_IMAGE
    assert item["set_code"] == "ltc"
    assert item["available_printings"] == 2
    assert len(first.json()) == 1


def test_other_urls_and_empty_input_are_rejected() -> None:
    with TestClient(app) as client:
        other = client.post("/api/v1/deck/parse", json={"url": "https://example.com/decks/1"})
        empty = client.post("/api/v1/deck/parse", json={})
        headers = client.post("/api/v1/deck/parse", json={"text": "Deck\nCommander\nSideboard\nMaybeboard\n"})
    assert other.status_code == 422
    assert other.json()["detail"]["code"] == "invalid_url"
    assert empty.status_code == 422
    assert empty.json()["detail"]["code"] == "empty_deck"
    assert headers.status_code == 200
    assert headers.json() == []


def test_parse_route_is_registered() -> None:
    operation = app.openapi()["paths"]["/api/v1/deck/parse"]
    assert "post" in operation
