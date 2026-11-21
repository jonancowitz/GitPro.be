"""Launch stores from the blueprint.

Card Kingdom is a single warehouse: $5.99 base shipping, free at $75.
Mana Pool is a peer-to-peer marketplace with no flat base shipping and no
free-shipping threshold. Per-line shipping is a later optimizer concern.
CardTrader Zero is one consolidated US package. Its rate is a weight step,
not the flat base and threshold columns, so those stay at zero here. The
steps live in ``card_trader.py`` and the optimizer applies them for this id.
"""

from typing import Any

SEED_STORES: list[dict[str, Any]] = [
    {
        "id": "card_kingdom",
        "name": "Card Kingdom",
        "store_type": "single_warehouse",
        "base_shipping_cents": 599,
        "free_shipping_threshold_cents": 7500,
        "affiliate_param": None,
        "enabled": True,
    },
    {
        "id": "mana_pool",
        "name": "Mana Pool",
        "store_type": "p2p",
        "base_shipping_cents": 0,
        "free_shipping_threshold_cents": None,
        "affiliate_param": None,
        "enabled": True,
    },
    {
        "id": "card_trader",
        "name": "CardTrader Zero",
        "store_type": "consolidated",
        "base_shipping_cents": 0,
        "free_shipping_threshold_cents": None,
        "affiliate_param": None,
        "enabled": True,
    },
]
