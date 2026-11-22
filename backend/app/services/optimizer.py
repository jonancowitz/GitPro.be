"""Stage 2 MILP assignment and Stage 3 Mana Pool verification.

The solver minimizes card cost, shipping, and a package penalty. Free shipping
is a binary choice that is legal only when a store's merchandise subtotal
reaches its threshold. A small per-line weight on cheap peer-to-peer singles
is part of the objective only; it is not added to the price shown to the buyer
and it does not count toward a free-shipping threshold.

CardTrader Zero has no flat fee. Its US rate is a weight step on card count,
with a value cap on the plain-envelope step. Those steps are extra binaries
for that store only. The coverage constraints are unchanged.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

import httpx
import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.store import Store
from app.schemas.optimize import (
    BucketCard,
    OptimizeItem,
    OptimizeRequest,
    OptimizeResponse,
    SavingsSummary,
    StoreBucket,
    UnmatchedCard,
)
from app.services.catalog import (
    CardFact,
    InventoryRow,
    SelectedLine,
    acceptable_conditions,
    load_card_facts,
    load_inventory_rows,
    select_candidates,
)
from app.services.stores.card_kingdom import card_kingdom_checkout_url
from app.services.stores.card_trader import (
    CARD_TRADER_STORE_ID,
    CARD_TRADER_US_SHIPPING_STEPS,
    ShippingStep,
    card_trader_checkout_url,
    shipping_cents_for_steps,
)
from app.services.stores.mana_pool import (
    TIMEOUT_NOTICE,
    UNAVAILABLE_NOTICE,
    LiveCartLine,
    LiveQuote,
    LiveQuoteError,
    cached_live_quote,
    fetch_live_quote,
    mana_pool_checkout_url,
)

P2P_HEURISTIC_CENTS = 35
P2P_HEURISTIC_BELOW_CENTS = 200
MANA_POOL_STORE_ID = "mana_pool"

LiveFetch = Callable[[Sequence[LiveCartLine]], Awaitable[LiveQuote]]


class OptimizeError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class StoreTerms:
    store_id: str
    name: str
    store_type: str
    base_shipping_cents: int
    free_shipping_threshold_cents: int | None
    affiliate_param: str | None = None
    shipping_steps: tuple[ShippingStep, ...] | None = None


def plan_purchase(
    items: Sequence[OptimizeItem],
    facts: Sequence[CardFact],
    rows: Sequence[InventoryRow],
    stores: Sequence[StoreTerms],
    *,
    min_condition: str = "LP",
    finish_mode: str = "cheapest",
    exclude_basic_lands: bool = True,
    package_penalty_cents: int = 300,
    affiliate_tags: dict[str, str] | None = None,
) -> OptimizeResponse:
    """Prune offers, solve the assignment, and build the snapshot response."""

    if not stores:
        raise OptimizeError("no_stores", "None of the selected stores are available.")
    selected, rejected = select_candidates(
        items,
        facts,
        rows,
        enabled_store_ids=[store.store_id for store in stores],
        min_condition=min_condition,
        finish_mode=finish_mode,
        exclude_basic_lands=exclude_basic_lands,
    )
    return assemble_plan(
        selected,
        rejected,
        stores,
        package_penalty_cents=package_penalty_cents,
        affiliate_tags=affiliate_tags or {},
    )


def assemble_plan(
    lines: Sequence[SelectedLine],
    rejected: Sequence,
    stores: Sequence[StoreTerms],
    *,
    package_penalty_cents: int,
    affiliate_tags: dict[str, str],
) -> OptimizeResponse:
    unmatched = [_unmatched(card) for card in rejected]
    if not lines:
        return OptimizeResponse(
            buckets=[],
            savings=_savings(0, None),
            unmatched=unmatched,
            notices=[],
            alternatives=[],
        )

    assignment = solve_assignment(lines, stores, package_penalty_cents)
    buckets = _buckets(lines, assignment, stores, affiliate_tags)
    baseline = _baseline(lines, stores)
    optimized = _cash_total(buckets)
    return OptimizeResponse(
        buckets=buckets,
        savings=_savings(optimized, baseline),
        unmatched=unmatched,
        notices=[],
        alternatives=_single_store_alternatives(lines, stores, affiliate_tags),
    )


def solve_assignment(
    lines: Sequence[SelectedLine],
    stores: Sequence[StoreTerms],
    package_penalty_cents: int,
) -> list[str]:
    """Assign each line to exactly one store.

    Variables are ``x[line, store]``, ``y[store]``, and ``z[store]``. The
    objective is line cost plus base shipping on active stores that do not
    qualify for free shipping, plus the package penalty on every active store.
    CardTrader Zero adds one binary per weight step instead of that flat fee.
    """

    card_count = len(lines)
    store_count = len(stores)
    if card_count == 0 or store_count == 0:
        raise OptimizeError("empty_plan", "Nothing to optimize.")
    offer_cost = np.zeros((card_count, store_count), dtype=float)
    merchandise = np.zeros((card_count, store_count), dtype=float)
    available = np.zeros((card_count, store_count), dtype=bool)
    for card_index, line in enumerate(lines):
        for store_index, store in enumerate(stores):
            offer = line.offers.get(store.store_id)
            if offer is None:
                continue
            line_cents = offer.unit_price_cents * line.quantity
            weight = _p2p_weight(store, line_cents)
            merchandise[card_index, store_index] = line_cents
            offer_cost[card_index, store_index] = line_cents + weight
            available[card_index, store_index] = True
        if not available[card_index].any():
            raise OptimizeError("no_offer", "A selected card has no store offer.")

    resolved_steps = [shipping_steps_for(store) for store in stores]
    x_count = card_count * store_count
    step_total = sum(len(steps) for steps in resolved_steps if steps)
    variable_count = x_count + store_count + store_count + step_total
    objective = np.zeros(variable_count, dtype=float)
    lower = np.zeros(variable_count, dtype=float)
    upper = np.zeros(variable_count, dtype=float)
    for card_index in range(card_count):
        for store_index in range(store_count):
            slot = card_index * store_count + store_index
            if not available[card_index, store_index]:
                continue
            objective[slot] = offer_cost[card_index, store_index]
            upper[slot] = 1
    for store_index, store in enumerate(stores):
        active = x_count + store_index
        free = x_count + store_count + store_index
        if resolved_steps[store_index]:
            objective[active] = package_penalty_cents
        else:
            objective[active] = store.base_shipping_cents + package_penalty_cents
        upper[active] = 1
        if resolved_steps[store_index] or store.free_shipping_threshold_cents is None:
            continue
        upper[free] = 1
        objective[free] = -store.base_shipping_cents

    constraints: list[np.ndarray] = []
    lower_bounds: list[float] = []
    upper_bounds: list[float] = []

    def add_row(row: np.ndarray, low: float, high: float) -> None:
        constraints.append(row)
        lower_bounds.append(low)
        upper_bounds.append(high)

    for card_index in range(card_count):
        row = np.zeros(variable_count, dtype=float)
        for store_index in range(store_count):
            row[card_index * store_count + store_index] = 1
        add_row(row, 1, 1)
        for store_index in range(store_count):
            if not available[card_index, store_index]:
                continue
            row = np.zeros(variable_count, dtype=float)
            row[card_index * store_count + store_index] = 1
            row[x_count + store_index] = -1
            add_row(row, -np.inf, 0)

    for store_index, store in enumerate(stores):
        threshold = store.free_shipping_threshold_cents
        if threshold is None or resolved_steps[store_index]:
            continue
        free = x_count + store_count + store_index
        active_row = np.zeros(variable_count, dtype=float)
        active_row[free] = 1
        active_row[x_count + store_index] = -1
        add_row(active_row, -np.inf, 0)
        threshold_row = np.zeros(variable_count, dtype=float)
        for card_index in range(card_count):
            threshold_row[card_index * store_count + store_index] = merchandise[card_index, store_index]
        threshold_row[free] = -threshold
        add_row(threshold_row, 0, np.inf)

    _add_shipping_step_constraints(
        add_row,
        variable_count,
        x_count,
        lines,
        merchandise,
        resolved_steps,
        objective,
        upper,
    )

    result = milp(
        objective,
        integrality=np.ones(variable_count, dtype=np.int64),
        bounds=Bounds(lower, upper),
        constraints=LinearConstraint(
            np.vstack(constraints),
            np.asarray(lower_bounds, dtype=float),
            np.asarray(upper_bounds, dtype=float),
        ),
        options={"disp": False, "time_limit": 5.0 if step_total else 1.0, "mip_rel_gap": 0.0},
    )
    if not result.success or result.x is None:
        raise OptimizeError(
            "solver_failed",
            "The optimizer could not price this list.",
            status_code=503,
        )

    chosen: list[str] = []
    for card_index in range(card_count):
        picks = [
            store_index
            for store_index in range(store_count)
            if result.x[card_index * store_count + store_index] > 0.5
        ]
        if len(picks) != 1:
            raise OptimizeError(
                "solver_failed",
                "The optimizer could not price this list.",
                status_code=503,
            )
        chosen.append(stores[picks[0]].store_id)
    return chosen


def _p2p_weight(store: StoreTerms, line_cents: int) -> int:
    if store.store_type == "p2p" and line_cents < P2P_HEURISTIC_BELOW_CENTS:
        return P2P_HEURISTIC_CENTS
    return 0


def shipping_steps_for(store: StoreTerms) -> tuple[ShippingStep, ...] | None:
    """Weight steps for this store. CardTrader Zero uses the US Zero table."""

    if store.shipping_steps is not None:
        return store.shipping_steps or None
    if store.store_id == CARD_TRADER_STORE_ID:
        return CARD_TRADER_US_SHIPPING_STEPS
    return None


def _add_shipping_step_constraints(
    add_row,
    variable_count: int,
    x_count: int,
    lines: Sequence[SelectedLine],
    merchandise: np.ndarray,
    resolved_steps: Sequence[tuple[ShippingStep, ...] | None],
    objective: np.ndarray,
    upper: np.ndarray,
) -> None:
    """Pay exactly one feasible weight step when a stepped store is used.

    Coverage rows are left alone. A step is feasible when the assigned card
    count is within its cap and, for the envelope, the merchandise value is too.
    """

    store_count = len(resolved_steps)
    card_count = len(lines)
    total_cards = float(sum(line.quantity for line in lines))
    cursor = x_count + store_count + store_count
    for store_index, steps in enumerate(resolved_steps):
        if not steps:
            continue
        slots: list[int] = []
        for step in steps:
            objective[cursor] = step.shipping_cents
            upper[cursor] = 1
            slots.append(cursor)
            cursor += 1
        choice = np.zeros(variable_count, dtype=float)
        for slot in slots:
            choice[slot] = 1
        choice[x_count + store_index] = -1
        add_row(choice, 0, 0)
        column_value = float(sum(merchandise[card_index, store_index] for card_index in range(card_count)))
        for step, slot in zip(steps, slots, strict=True):
            card_row = np.zeros(variable_count, dtype=float)
            for card_index, line in enumerate(lines):
                card_row[card_index * store_count + store_index] = line.quantity
            card_row[slot] = total_cards
            add_row(card_row, -np.inf, step.max_cards + total_cards)
            if step.max_subtotal_cents is None:
                continue
            value_row = np.zeros(variable_count, dtype=float)
            for card_index in range(card_count):
                value_row[card_index * store_count + store_index] = merchandise[card_index, store_index]
            value_row[slot] = column_value
            add_row(value_row, -np.inf, step.max_subtotal_cents + column_value)


def shipping_for(
    store: StoreTerms,
    subtotal_cents: int,
    *,
    card_count: int = 0,
) -> tuple[int, int | None]:
    steps = shipping_steps_for(store)
    if steps:
        if card_count <= 0:
            return 0, None
        return shipping_cents_for_steps(steps, card_count, subtotal_cents), None
    threshold = store.free_shipping_threshold_cents
    if threshold is None:
        return store.base_shipping_cents, None
    if subtotal_cents >= threshold:
        return 0, 0
    return store.base_shipping_cents, threshold - subtotal_cents


def _buckets(
    lines: Sequence[SelectedLine],
    assignment: Sequence[str],
    stores: Sequence[StoreTerms],
    affiliate_tags: dict[str, str],
) -> list[StoreBucket]:
    grouped: dict[str, list[BucketCard]] = {store.store_id: [] for store in stores}
    for line, store_id in zip(lines, assignment, strict=True):
        offer = line.offers[store_id]
        grouped[store_id].append(_bucket_card(offer, line.quantity))
    buckets: list[StoreBucket] = []
    for store in stores:
        cards = grouped[store.store_id]
        if not cards:
            continue
        subtotal = sum(card.line_total_cents for card in cards)
        card_count = sum(card.quantity for card in cards)
        shipping, delta = shipping_for(store, subtotal, card_count=card_count)
        text_lines = [_mass_entry_line(card) for card in cards]
        buckets.append(
            StoreBucket(
                store_id=store.store_id,
                store_name=store.name,
                cards=cards,
                subtotal_cents=subtotal,
                shipping_cents=shipping,
                estimated_packages=1,
                free_shipping_delta_cents=delta,
                checkout_url=_checkout_url(store, text_lines, affiliate_tags),
                mass_entry_text="\n".join(text_lines),
                price_source="snapshot",
            )
        )
    return buckets


def _bucket_card(offer: InventoryRow, quantity: int) -> BucketCard:
    return BucketCard(
        name=offer.name,
        quantity=quantity,
        scryfall_id=offer.scryfall_id,
        oracle_id=offer.oracle_id,
        set_code=offer.set_code,
        collector_number=offer.collector_number,
        finish=offer.finish,  # type: ignore[arg-type]
        condition=offer.condition,  # type: ignore[arg-type]
        unit_price_cents=offer.unit_price_cents,
        line_total_cents=offer.unit_price_cents * quantity,
        store_sku=offer.store_sku,
        image_uri=offer.image_uri,
    )


def _mass_entry_line(card: BucketCard) -> str:
    text = f"{card.quantity} {card.name} ({card.set_code.upper()}) {card.collector_number}"
    if card.finish == "foil":
        text += " *F*"
    elif card.finish == "etched":
        text += " *E*"
    return text


def _checkout_url(store: StoreTerms, lines: Sequence[str], affiliate_tags: dict[str, str]) -> str:
    tag = _affiliate_tag(store, affiliate_tags)
    if store.store_id == "card_kingdom":
        return card_kingdom_checkout_url(lines, tag)
    if store.store_id == MANA_POOL_STORE_ID:
        return mana_pool_checkout_url(tag)
    if store.store_id == CARD_TRADER_STORE_ID:
        return card_trader_checkout_url()
    return ""


def _affiliate_tag(store: StoreTerms, affiliate_tags: dict[str, str]) -> str | None:
    override = affiliate_tags.get(store.store_id, "").strip()
    if override:
        return override
    if store.affiliate_param and store.affiliate_param.strip():
        return store.affiliate_param.strip()
    return None


def _baseline(lines: Sequence[SelectedLine], stores: Sequence[StoreTerms]) -> tuple[StoreTerms, int] | None:
    candidates: list[tuple[int, int, str, StoreTerms]] = []
    for store in stores:
        if any(store.store_id not in line.offers for line in lines):
            continue
        subtotal = sum(line.offers[store.store_id].unit_price_cents * line.quantity for line in lines)
        card_count = sum(line.quantity for line in lines)
        shipping, _delta = shipping_for(store, subtotal, card_count=card_count)
        preference = 0 if store.store_id == "card_kingdom" else 1
        candidates.append((subtotal + shipping, preference, store.store_id, store))
    if not candidates:
        return None
    _total, _preference, _store_id, store = min(candidates)
    subtotal = sum(line.offers[store.store_id].unit_price_cents * line.quantity for line in lines)
    card_count = sum(line.quantity for line in lines)
    shipping, _delta = shipping_for(store, subtotal, card_count=card_count)
    return store, subtotal + shipping


def _single_store_alternatives(
    lines: Sequence[SelectedLine],
    stores: Sequence[StoreTerms],
    affiliate_tags: dict[str, str],
) -> list[StoreBucket]:
    """Price buying the whole list at each store that can fill every line.

    The baseline comparison already ranks these landed totals and keeps the
    cheapest. The buckets themselves are what the results pane shows beside
    the winning split.
    """

    priced: list[tuple[int, int, StoreBucket]] = []
    for index, store in enumerate(stores):
        if any(store.store_id not in line.offers for line in lines):
            continue
        buckets = _buckets(lines, [store.store_id] * len(lines), stores, affiliate_tags)
        if len(buckets) != 1:
            continue
        bucket = buckets[0]
        priced.append((bucket.subtotal_cents + bucket.shipping_cents, index, bucket))
    priced.sort(key=lambda item: (item[0], item[1]))
    return [bucket for _total, _index, bucket in priced]


def _cash_total(buckets: Sequence[StoreBucket]) -> int:
    return sum(bucket.subtotal_cents + bucket.shipping_cents for bucket in buckets)


def _savings(optimized: int, baseline: tuple[StoreTerms, int] | None) -> SavingsSummary:
    if baseline is None:
        return SavingsSummary(
            optimized_total_cents=optimized,
            baseline_store_id=None,
            baseline_store_name=None,
            baseline_total_cents=None,
            savings_cents=None,
            message=None,
        )
    store, total = baseline
    savings = total - optimized
    return SavingsSummary(
        optimized_total_cents=optimized,
        baseline_store_id=store.store_id,
        baseline_store_name=store.name,
        baseline_total_cents=total,
        savings_cents=savings,
        message=_savings_message(savings, store.name),
    )


def _savings_message(savings: int, store_name: str) -> str:
    if savings > 0:
        return f"Saves {_format_cents(savings)} vs. buying everything at {store_name}"
    if savings < 0:
        return f"Costs {_format_cents(abs(savings))} more than buying everything at {store_name}"
    return f"Same total as buying everything at {store_name}"


def _format_cents(cents: int) -> str:
    return f"${cents // 100}.{cents % 100:02d}"


def _unmatched(card) -> UnmatchedCard:
    return UnmatchedCard(
        oracle_id=card.oracle_id,
        scryfall_id=card.scryfall_id,
        name=card.name,
        quantity=card.quantity,
        finish=card.finish,
        reason=card.reason,
    )


def refresh_savings(response: OptimizeResponse) -> OptimizeResponse:
    optimized = _cash_total(response.buckets)
    baseline_total = response.savings.baseline_total_cents
    # A live quote replaces the snapshot total of a cart that is already the
    # single-store baseline. That purchase is not a savings against itself.
    if _priced_as_baseline_store(response) and baseline_total is not None:
        baseline_total = optimized
    savings = None if baseline_total is None else baseline_total - optimized
    message = None
    if savings is not None and response.savings.baseline_store_name is not None:
        message = _savings_message(savings, response.savings.baseline_store_name)
    return response.model_copy(
        update={
            "savings": response.savings.model_copy(
                update={
                    "optimized_total_cents": optimized,
                    "baseline_total_cents": baseline_total,
                    "savings_cents": savings,
                    "message": message,
                }
            )
        }
    )


def _priced_as_baseline_store(response: OptimizeResponse) -> bool:
    return len(response.buckets) == 1 and response.buckets[0].store_id == response.savings.baseline_store_id


def with_notice(response: OptimizeResponse, notice: str) -> OptimizeResponse:
    notices = list(response.notices)
    if notice not in notices:
        notices.append(notice)
    buckets = []
    for bucket in response.buckets:
        if bucket.store_id == MANA_POOL_STORE_ID:
            buckets.append(bucket.model_copy(update={"notice": notice, "price_source": "snapshot"}))
        else:
            buckets.append(bucket)
    return response.model_copy(update={"buckets": buckets, "notices": notices})


def apply_live_quote(
    response: OptimizeResponse,
    quote: LiveQuote,
    stores: Sequence[StoreTerms],
) -> OptimizeResponse:
    by_id = {store.store_id: store for store in stores}
    buckets = []
    for bucket in response.buckets:
        if bucket.store_id != MANA_POOL_STORE_ID:
            buckets.append(bucket)
            continue
        store = by_id.get(MANA_POOL_STORE_ID)
        delta = bucket.free_shipping_delta_cents
        if store is not None and store.free_shipping_threshold_cents is not None:
            gap = store.free_shipping_threshold_cents - quote.subtotal_cents
            delta = max(0, gap)
        buckets.append(
            bucket.model_copy(
                update={
                    "subtotal_cents": quote.subtotal_cents,
                    "shipping_cents": quote.shipping_cents,
                    "estimated_packages": max(1, quote.package_count),
                    "free_shipping_delta_cents": delta,
                    "price_source": "live",
                    "notice": None,
                }
            )
        )
    return refresh_savings(response.model_copy(update={"buckets": buckets}))


def mana_pool_lines(response: OptimizeResponse) -> list[LiveCartLine]:
    for bucket in response.buckets:
        if bucket.store_id != MANA_POOL_STORE_ID:
            continue
        return [
            LiveCartLine(
                name=card.name,
                scryfall_id=str(card.scryfall_id),
                quantity=card.quantity,
                finish=card.finish,
                condition=card.condition,
                set_code=card.set_code,
                collector_number=card.collector_number,
            )
            for card in bucket.cards
        ]
    return []


async def verify_live_cart(
    response: OptimizeResponse,
    stores: Sequence[StoreTerms],
    *,
    redis,
    min_condition: str,
    email: str,
    access_token: str,
    client: httpx.AsyncClient | None = None,
    live_fetch: LiveFetch | None = None,
) -> OptimizeResponse:
    """Replace the Mana Pool bucket with a live quote, or keep the snapshot."""

    lines = mana_pool_lines(response)
    if not lines:
        return response
    fetch = live_fetch
    if fetch is None:
        if not email.strip() or not access_token.strip() or client is None:
            return with_notice(response, UNAVAILABLE_NOTICE)
        conditions = acceptable_conditions(min_condition)

        async def fetch(live_lines: Sequence[LiveCartLine]) -> LiveQuote:
            return await fetch_live_quote(
                live_lines,
                client,
                email=email,
                access_token=access_token,
                conditions=conditions,
            )

    try:
        quote = await cached_live_quote(lines, redis, fetch, min_condition)
    except TimeoutError:
        return with_notice(response, TIMEOUT_NOTICE)
    except LiveQuoteError as exc:
        return with_notice(response, exc.notice)
    except Exception:
        return with_notice(response, UNAVAILABLE_NOTICE)
    return apply_live_quote(response, quote, stores)


async def load_store_terms(session: AsyncSession, store_ids: Sequence[str]) -> list[StoreTerms]:
    if not store_ids:
        return []
    result = await session.execute(select(Store).where(Store.id.in_(list(store_ids))))
    found = {row.id: row for row in result.scalars().all()}
    terms: list[StoreTerms] = []
    for store_id in store_ids:
        row = found.get(store_id)
        if row is None or not row.enabled:
            continue
        terms.append(
            StoreTerms(
                store_id=row.id,
                name=row.name,
                store_type=row.store_type,
                base_shipping_cents=row.base_shipping_cents,
                free_shipping_threshold_cents=row.free_shipping_threshold_cents,
                affiliate_param=row.affiliate_param,
                shipping_steps=CARD_TRADER_US_SHIPPING_STEPS if row.id == CARD_TRADER_STORE_ID else None,
            )
        )
    return terms


def affiliate_tags_from_settings() -> dict[str, str]:
    return {
        "card_kingdom": settings.cardkingdom_affiliate_tag,
        "mana_pool": settings.manapool_affiliate_tag,
    }


async def optimize_deck(
    session: AsyncSession,
    redis,
    request: OptimizeRequest,
    *,
    client: httpx.AsyncClient | None = None,
    live_fetch: LiveFetch | None = None,
) -> OptimizeResponse:
    stores = await load_store_terms(session, request.enabled_stores)
    if not stores:
        raise OptimizeError("no_stores", "None of the selected stores are available.")
    facts = await load_card_facts(session, request.items)
    rows: list[InventoryRow] = []
    oracle_ids = list(
        {
            fact.oracle_id
            for fact in facts
            if not (request.exclude_basic_lands and fact.is_basic_land)
        }
    )
    if oracle_ids:
        rows = await load_inventory_rows(
            session,
            oracle_ids,
            [store.store_id for store in stores],
            request.min_condition,
        )
    response = plan_purchase(
        request.items,
        facts,
        rows,
        stores,
        min_condition=request.min_condition,
        finish_mode=request.finish_mode,
        exclude_basic_lands=request.exclude_basic_lands,
        package_penalty_cents=request.package_penalty_cents,
        affiliate_tags=affiliate_tags_from_settings(),
    )
    return await verify_live_cart(
        response,
        stores,
        redis=redis,
        min_condition=request.min_condition,
        email=settings.manapool_email,
        access_token=settings.manapool_api_key,
        client=client,
        live_fetch=live_fetch,
    )
