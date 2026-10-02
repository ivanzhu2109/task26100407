"""Multi-channel inventory reconciliation library.

Keeps a continuously converging in-memory stock projection per
(store, sku) across three sales channels, tolerating duplicated and
out-of-order sales revisions and stocktake reports.
"""

from .projector import InventoryProjector
from .types import CHANNELS, InventorySnapshot, SalesRevision, Stocktake

__all__ = [
    "CHANNELS",
    "InventoryProjector",
    "InventorySnapshot",
    "SalesRevision",
    "Stocktake",
]
