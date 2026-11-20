# gitpro.be — Project Blueprint & Cursor Build Spec

> **Tagline:** *Peek at every store's inventory before you buy — zero TCGplayer.*  
> **Instructions for Cursor:** Treat this document as the single source of truth for architecture, data models, API contracts, and implementation milestones. Build incrementally by milestone (Milestone 1 -> 2 -> 3 -> 4 -> 5). Do not add authentication, user accounts, or TCGplayer integrations in v1.

---

## 1. Product Summary & Core Constraints

**What we are building (`gitpro.be`):** A fast, **stateless** web application that takes a Magic: The Gathering decklist (from Moxfield, Archidekt, ManaBox, or plain text), lets the user lock specific printings/conditions, and mathematically partitions the deck across **non-TCGplayer** storefronts to minimize total landed cost (card price + shipping + a user-configurable "package penalty").

**Strict v1 Scope:**

* **Stateless:** Zero user accounts, zero auth, zero server-side user deck storage. Persist active workspace state in browser `localStorage`.
* **Canonical Card Identity:** Always normalize cards to **Scryfall `oracle_id`** (for "any printing" grouping) and **Scryfall `scryfall_id`** (for exact printing/set/collector number/finish).
* **Stores (Phase 1 Launch):**
  1. **Card Kingdom** — Single-warehouse US retailer via public JSON pricelist.
  2. **Mana Pool** — US P2P marketplace via open REST API + native live cart optimizer endpoint.
* **Stores (Phase 1.5 / Phase 2 — Extensible Adapter Pattern):** CardTrader Zero (Phase 1.5), Star City Games (SCG), CoolStuffInc (CSI). Architect the store layer behind a clean `StoreAdapter` interface so adding new stores requires zero changes to the core solver.
* **Excluded:** TCGplayer (explicitly excluded by design).

---

## 2. Tech Stack

* **Backend:** Python 3.12+, **FastAPI**, Pydantic v2, `httpx` (async HTTP), SQLAlchemy 2.0 (async) + Alembic.
* **Optimization Solver:** `scipy` (`scipy.optimize.milp`, which wraps the HiGHS C++ MILP solver natively) or `highspy`.
* **Database:** **PostgreSQL 16** (canonical Scryfall card catalog + periodically synced store price/stock snapshots).
* **Cache:** **Redis 7** (5–15 minute TTL cache for live API quotes and Archidekt deck fetches).
* **Frontend:** **Next.js (App Router)**, TypeScript (strict), Tailwind CSS, Lucide icons.
* **Local Dev:** `docker-compose.yml` running Postgres 16 and Redis 7.

---

## 3. Repository Structure (Monorepo)

```text
gitpro.be/
├── CURSOR_BLUEPRINT.md
├── docker-compose.yml
├── .env.example
├── backend/
│   ├── pyproject.toml
│   ├── alembic.ini
│   ├── alembic/
│   ├── app/
│   │   ├── main.py                  # FastAPI entrypoint + CORS
│   │   ├── core/
│   │   │   ├── config.py            # Pydantic BaseSettings
│   │   │   ├── database.py          # Async SQLAlchemy engine/session
│   │   │   └── redis.py             # Async Redis client
│   │   ├── models/                  # SQLAlchemy ORM models
│   │   │   ├── card.py
│   │   │   ├── store.py
│   │   │   └── store_price.py
│   │   ├── schemas/                 # Pydantic request/response DTOs
│   │   │   ├── deck.py
│   │   │   └── optimize.py
│   │   ├── services/
│   │   │   ├── parser.py            # Moxfield/Archidekt text, ManaBox CSV, Archidekt URL
│   │   │   ├── catalog.py           # Candidate printing resolution & Stage 1 pruning
│   │   │   ├── optimizer.py         # Stage 2 MILP solver + Stage 3 Mana Pool live verification
│   │   │   └── stores/
│   │   │       ├── base.py          # Abstract StoreAdapter interface
│   │   │       ├── card_kingdom.py  # CK JSON pricelist sync + checkout URL builder
│   │   │       └── mana_pool.py     # Mana Pool API client + /cart-optimizer + checkout builder
│   │   ├── workers/
│   │   │   ├── sync_scryfall.py     # Daily Scryfall default_cards bulk sync
│   │   │   └── sync_stores.py       # Periodic store price/stock sync runner
│   │   └── api/
│   │       └── routes/
│   │           ├── deck.py          # POST /api/v1/deck/parse
│   │           ├── optimize.py      # POST /api/v1/optimize
│   │           └── health.py        # GET /api/v1/health
│   └── tests/
└── frontend/
    ├── package.json
    ├── next.config.ts
    └── src/
        ├── app/
        │   ├── layout.tsx
        │   └── page.tsx             # Single-page reactive workspace
        ├── components/
        │   ├── Header.tsx           # gitpro.be domain-hack logo + tagline
        │   ├── ImportBar.tsx        # Paste text, upload CSV, or Archidekt URL
        │   ├── OptimizerControls.tsx# Package penalty slider, store toggles, exclude basics
        │   ├── TriageGrid.tsx       # Deck table, printing locks, condition/finish controls
        │   ├── ResultsDashboard.tsx # Savings banner + store bucket cards
        │   └── StoreBucketCard.tsx  # Per-store list, shipping progress bar, checkout/copy CTA
        ├── lib/
        │   ├── api.ts               # Typed FastAPI client
        │   └── storage.ts           # localStorage persistence helpers
        └── types/
            └── deck.ts              # Shared TypeScript interfaces
```

---

## 4. Database Schema (PostgreSQL)

Even though users are stateless, Postgres powers fast offline candidate pruning.

### `cards`

| Column | Type | Notes |
| :--- | :--- | :--- |
| `scryfall_id` | `UUID` (PK) | Scryfall card UUID |
| `oracle_id` | `UUID` (INDEX) | Groups all printings of the same functional card |
| `name` | `TEXT` (INDEX) | Canonical card name (handle DFC `Front // Back` lookup cleanly) |
| `set_code` | `VARCHAR(16)` | Lowercase set code (e.g., `ltc`, `mh3`) |
| `collector_number` | `VARCHAR(32)` | Exact collector number |
| `is_basic_land` | `BOOLEAN` | True for Plains, Island, Swamp, Mountain, Forest, Wastes |
| `image_uri` | `TEXT` | Small/normal Scryfall image URI for UI hover/thumbnails |
| `finishes` | `JSONB` | Array of valid finishes, e.g. `["nonfoil", "foil", "etched"]` |
| `updated_at` | `TIMESTAMPTZ` | Last Scryfall sync timestamp |

### `stores`

| Column | Type | Notes |
| :--- | :--- | :--- |
| `id` | `VARCHAR(32)` (PK) | e.g., `card_kingdom`, `mana_pool`, `card_trader` |
| `name` | `TEXT` | Display name |
| `store_type` | `VARCHAR(32)` | `single_warehouse`, `p2p`, or `consolidated` |
| `base_shipping_cents` | `INTEGER` | Base shipping cost in cents (e.g., `599` for CK) |
| `free_shipping_threshold_cents` | `INTEGER` (NULLABLE) | e.g., `7500` for CK ($75), `NULL` if none |
| `affiliate_param` | `TEXT` (NULLABLE) | Query param/tag injected into outbound links |
| `enabled` | `BOOLEAN` | Default `true` |

### `store_prices`

| Column | Type | Notes |
| :--- | :--- | :--- |
| `id` | `BIGSERIAL` (PK) | |
| `store_id` | `VARCHAR(32)` (FK -> `stores.id`) | |
| `scryfall_id` | `UUID` (FK -> `cards.scryfall_id`) | |
| `finish` | `VARCHAR(16)` | `nonfoil`, `foil`, or `etched` |
| `condition` | `VARCHAR(8)` | Normalized: `NM`, `LP`, `MP`, `HP` |
| `price_cents` | `INTEGER` | Unit price in USD cents |
| `stock_qty` | `INTEGER` | Available quantity (`0` = out of stock) |
| `store_sku` | `TEXT` (NULLABLE) | Store-specific SKU/ID if needed for checkout |
| `updated_at` | `TIMESTAMPTZ` | |

* **Composite Unique Index:** `(store_id, scryfall_id, finish, condition)`
* **Query Index:** `(scryfall_id, finish, condition, stock_qty)` for fast Stage 1 pruning.

---

## 5. Data Ingestion & Store Adapters

1. **Scryfall Bulk Sync (`sync_scryfall.py`):**
   * Fetch bulk metadata from `https://api.scryfall.com/bulk-data/default-cards`.
   * Stream-parse the JSON and upsert into `cards` (filter out digital-only cards where `"paper" not in games`, plus tokens/art series unless needed).
   * Support a `--limit` CLI flag for fast local dev seeding.
2. **Card Kingdom (`stores/card_kingdom.py` — Phase 1):**
   * Fetch `https://api.cardkingdom.com/api/v2/pricelist` (include a descriptive `User-Agent: gitpro.be/1.0` header).
   * Map CK's `scryfall_id` + `is_foil` + condition (`NM`, `EX` -> `LP`, `VG` -> `MP`, `G` -> `HP`) into `store_prices` via bulk `INSERT ... ON CONFLICT DO UPDATE`.
   * Checkout handoff: mass-entry URL with affiliate param + copy-paste fallback.
3. **Mana Pool (`stores/mana_pool.py` — Phase 1):**
   * Ingest daily/periodic price snapshots from Mana Pool's open REST API keyed by `scryfall_id` into `store_prices` for Stage 1 pruning.
   * For Stage 3 live verification, call Mana Pool's live `POST /cart-optimizer` endpoint for the exact subset of cards routed to Mana Pool.
   * Cache live Mana Pool optimizer responses in Redis (TTL: 5–10 minutes) keyed by hash of `(sorted card list + condition + finish)`.
   * Checkout handoff: `manapool.com/add-deck` mass-entry URL with affiliate tag + copy-paste fallback.
4. **Future Adapters (Phase 1.5 / 2):**
   * **CardTrader Zero** (`consolidated` via API v2), followed by **Star City Games** and **CoolStuffInc** once v1 traffic validates demand.

---

## 6. Decklist Parser (`services/parser.py`)

Normalize all inputs into a unified list of `ParsedDeckItem`:

```python
class ParsedDeckItem(BaseModel):
    raw_line: str
    quantity: int
    name: str
    set_code: str | None = None
    collector_number: str | None = None
    scryfall_id: str | None = None
    oracle_id: str | None = None
    finish: Literal["nonfoil", "foil", "etched"] = "nonfoil"
    printing_locked: bool = False
    is_basic_land: bool = False
    match_status: Literal["matched", "ambiguous", "unmatched"]
```

**Supported Input Formats:**

1. **Standard Text (Moxfield, Archidekt, EDHREC, MTGO):**
   * Regex support for `1 Sol Ring (LTC) 284 *F*`, `1x Command Tower [CMM]`, `4 Lightning Bolt`, cleanly handling section headers (`Deck`, `Commander`, `Sideboard`, `Maybeboard`).
2. **ManaBox CSV:**
   * Parse CSV headers (`Name`, `Set code`, `Collector number`, `Foil`, `Quantity`, `Scryfall ID`). Prefer `Scryfall ID` when present for exact printing resolution.
3. **Direct URL Import:**
   * **Archidekt:** Detect `archidekt.com/decks/{deck_id}` and fetch the public JSON from `https://archidekt.com/api/decks/{deck_id}/` (cache in Redis for 5 min).
   * **Moxfield:** Do *not* attempt headless browser scraping against Cloudflare. If a user submits a `moxfield.com` URL, return a structured `422` / `unsupported_url` response instructing the UI to display: *"Moxfield blocks direct server imports — click Export -> Copy for MTGO/Text on Moxfield and paste here."*

---

## 7. Optimization Engine (`services/optimizer.py`)

Target end-to-end latency: **under 3 seconds** for a 100-card Commander deck.

### Stage 1: Offline Candidate Pruning (Postgres — < 50ms)

For each requested card $i$ (after excluding basic lands if toggled):

* If `printing_locked == True`: query `store_prices` strictly for `scryfall_id == item.scryfall_id`, matching `finish`, `condition >= min_condition`, and `stock_qty >= item.quantity`.
* If `printing_locked == False`: query `store_prices` across all `scryfall_id`s sharing the same `oracle_id`. Keep the **single cheapest in-stock printing per enabled store** that satisfies `condition >= min_condition` (and `finish` if user selected `match_deck`, or cheapest finish if `cheapest`).
* Any card with zero available options across all enabled stores is placed in `unmatched_cards`.

### Stage 2: Cross-Store MILP Formulation (`scipy.optimize.milp` / `highspy` — < 300ms)

Let $I$ be the set of available cards, $S$ be the set of enabled stores, and $P \ge 0$ be the user's **Package Penalty** in cents (from UI slider, `$0.00` to `$15.00`, default `300`).

**Decision Variables:**

* $x_{i,s} \in \{0, 1\}$: 1 if card $i$ (all requested copies) is assigned to store $s$.
* $y_s \in \{0, 1\}$: 1 if at least one card is assigned to store $s$.
* $z_s \in \{0, 1\}$: 1 if store $s$ qualifies for free shipping (only for stores with a non-null threshold $T_s$).

**Parameters:**

* $c_{i,s}$: Total line cost (`unit_price_cents * quantity`) for card $i$ at store $s$. For P2P stores like Mana Pool in Stage 2, add a small heuristic shipping weight per low-value line item (e.g., `35` cents if line total $< \$2.00$) to discourage routing cheap bulk singles to P2P sellers.
* $B_s$: Base shipping cost in cents for store $s$.
* $T_s$: Free shipping threshold in cents for store $s$.

**Objective Function (Minimize):**

$$
\sum_{i \in I} \sum_{s \in S} c_{i,s} x_{i,s} + \sum_{s \in S} B_s (y_s - z_s) + \sum_{s \in S} P \cdot y_s
$$

**Constraints:**

1. **Exact Coverage:** Every available card must be bought from exactly one store:
   $$
   \sum_{s \in S} x_{i,s} = 1 \quad \forall i \in I
   $$
2. **Store Activation:** If any card is assigned to store $s$, store $s$ must be active ($y_s = 1$):
   $$
   x_{i,s} \le y_s \quad \forall i \in I, s \in S
   $$
3. **Free Shipping Threshold:** Free shipping ($z_s = 1$) is only allowed if store $s$ is active and subtotal meets $T_s$:
   $$
   z_s \le y_s \quad \forall s \in S
   $$
   $$
   \sum_{i \in I} c_{i,s} x_{i,s} \ge T_s \cdot z_s \quad \forall s \in S
   $$

### Stage 3: Live P2P Verification (Mana Pool)

1. Extract the subset of cards assigned to `mana_pool` by Stage 2.
2. If non-empty, call Mana Pool's live cart optimizer API to obtain the **actual** live card subtotal, shipping cost, and estimated package count (gracefully fall back to Stage 2 estimates with a UI notice if the live API times out).
3. Return the final `OptimizeResponse` containing:
   * Per-store buckets (cards, chosen printing, condition, unit price, subtotal, shipping cost, estimated packages, free-shipping delta, checkout URL + copyable mass-entry text).
   * Single-store baseline comparison (e.g., *"Saves $28.40 vs. buying everything at Card Kingdom"*).
   * Out-of-stock / unmatched cards list.

---

## 8. FastAPI Endpoints

* `GET /api/v1/health` — Returns DB, Redis, and last store sync timestamps.
* `POST /api/v1/deck/parse` — Accepts `{ text?: string, url?: string }` or multipart CSV upload. Returns normalized `ParsedDeckItem[]` with Scryfall metadata (image URI, set codes, available printings count).
* `POST /api/v1/optimize` — Accepts:
  ```json
  {
    "items": [
      {
        "oracle_id": "uuid",
        "scryfall_id": "uuid",
        "quantity": 1,
        "finish": "nonfoil",
        "printing_locked": false
      }
    ],
    "min_condition": "LP",
    "finish_mode": "cheapest",
    "exclude_basic_lands": true,
    "enabled_stores": ["card_kingdom", "mana_pool"],
    "package_penalty_cents": 300
  }
  ```
  Returns `OptimizeResponse` with store buckets, savings summary, checkout handoff payloads, and unmatched items.

---

## 9. Frontend UX & Branding (`gitpro.be`)

* **Brand Identity:** Dark-mode-first workspace. Style the navbar logo to clarify the domain hack visually—e.g., `git` in primary text + `pro.be` in an accent color—paired with the subtitle: *"Peek at every store's inventory before you buy — zero TCGplayer."*
* **Zero Login / Local Persistence:** Automatically save the current parsed decklist and user settings to `localStorage` so page refreshes never lose work.
* **Single-Page Workspace Flow:**
  1. **Import Bar:** Textarea for copy-paste, drag-and-drop `.csv`/`.txt`, and Archidekt URL input. Clear inline hint if a Moxfield URL is pasted.
  2. **Optimizer Controls Bar:**
     * Min Condition selector (`NM` / `LP` / `MP`).
     * Finish toggle (`Cheapest Legal` vs. `Match Decklist`).
     * Checkbox: `Exclude Basic Lands` (default ON).
     * Store toggles (`Card Kingdom`, `Mana Pool`).
     * **Package Penalty Slider:** Labeled *"Lowest Price"* ($0) $\longleftrightarrow$ *"Fewer Packages"* ($15), default $3.00.
  3. **Deck Triage Grid:** Compact list showing quantity, card name (with Scryfall image hover preview), set/finish badge, and a **Lock Printing** padlock toggle.
  4. **Results Dashboard:**
     * Top savings banner comparing Optimized Split total vs. Single-Store baseline.
     * Side-by-side **Store Bucket Cards** with itemized table, free-shipping progress bar (e.g., *"$8.20 away from Free Shipping at Card Kingdom"*), primary **"Open Cart on [Store]"** CTA (mass-entry URL with affiliate param), and secondary **"Copy List"** CTA.
     * **Unmatched / Out-of-Stock Tray** at the bottom so no card silently vanishes.

---

## 10. Environment Variables (`.env.example`)

```env
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/gitprobe
REDIS_URL=redis://localhost:6379/0
MANAPOOL_API_KEY=your_manapool_api_key_here
MANAPOOL_AFFILIATE_TAG=
CARDKINGDOM_AFFILIATE_TAG=
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

---

## 11. Step-by-Step Build Plan for Cursor

Work through these milestones sequentially. Confirm tests/checks pass before moving to the next milestone.

* **Milestone 1 — Project Scaffold & Infrastructure:**
  * Create `docker-compose.yml` (Postgres 16 + Redis 7), `.env.example`, backend FastAPI skeleton (`pyproject.toml`, SQLAlchemy async setup, Alembic initial migration for `cards`, `stores`, `store_prices`), and `GET /api/v1/health`.
* **Milestone 2 — Data Sync Workers:**
  * Implement `sync_scryfall.py` (with `--limit` flag for fast local dev testing).
  * Implement `StoreAdapter` base class, `card_kingdom.py` pricelist sync, and `mana_pool.py` price sync.
  * Seed `stores` table (`card_kingdom`, `mana_pool`).
* **Milestone 3 — Decklist Parser & `POST /api/v1/deck/parse`:**
  * Implement Moxfield/Archidekt text parser, ManaBox CSV parser, Archidekt URL fetcher, Moxfield friendly fallback, and Scryfall DB resolution (including DFC `//` handling). Write unit tests for all formats.
* **Milestone 4 — MILP Optimization Engine & `POST /api/v1/optimize`:**
  * Implement Stage 1 Postgres pruning, Stage 2 `scipy.optimize.milp` solver, and Stage 3 Mana Pool live cart optimizer verification + timeout fallback.
  * Write unit tests verifying free-shipping thresholds, package penalty trade-offs, printing locks, and out-of-stock handling.
* **Milestone 5 — Next.js Frontend (`gitpro.be`):**
  * Scaffold Next.js App Router + Tailwind frontend, build `Header`, `ImportBar`, `OptimizerControls`, `TriageGrid`, and `ResultsDashboard` with `localStorage` persistence and affiliate-tagged checkout handoffs.