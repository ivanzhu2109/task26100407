"""Public data types for the inventory reconciliation library."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

#: The three sales channels, in a fixed canonical order.
CHANNELS: tuple[str, str, str] = ("pos", "ecom", "platform")


@dataclass(frozen=True)
class SalesRevision:
    """One channel-sequenced sales revision message.

    ``qty`` is the order's current cumulative net outbound units
    (not a delta).  It is a non-negative integer and may go up,
    down, or back to zero.
    """

    seq: int
    order_id: str
    qty: int


@dataclass(frozen=True)
class Stocktake:
    """A stocktake report.

    ``actual`` already includes every inbound/outbound change with
    channel sequence numbers up to the per-channel ``watermarks``.
    """

    version: int
    actual: int
    watermarks: dict[str, int]


class InventorySnapshot(NamedTuple):
    """Result of a stock query.

    ``projected`` is the current stock projection (may be negative).
    ``pending`` is True while any channel's accountable position lags
    its stocktake watermark or the highest received sequence number.
    """

    projected: int
    pending: bool
