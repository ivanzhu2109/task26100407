"""Per-(store, sku) reconciliation account.

An account tracks three independent channel streams plus the highest
stocktake version seen, and derives a continuously converging stock
projection from them.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import deque

from .types import CHANNELS, InventorySnapshot, SalesRevision, Stocktake


class _ChannelState:
    """Mutable state for one channel of one account.

    ``accountable`` is the gap-free received prefix length.  ``held``
    keeps received messages beyond that prefix (keyed by seq, values
    ``[order_id, qty, delta]``).  ``orders`` maps an order id to
    ``[final_seq, final_qty, held_seqs]`` where ``final_*`` describe
    the order's highest-seq event at or below ``accountable`` and
    ``held_seqs`` is the sorted list of the order's seqs in ``held``.
    ``delta`` of an event is its inventory effect, i.e. the previous
    cumulative qty (in channel seq order) minus its own qty.
    """

    __slots__ = (
        "accountable",
        "watermark",
        "max_seq",
        "applied",
        "applied_sum",
        "held",
        "orders",
    )

    def __init__(self) -> None:
        self.accountable = 0
        self.watermark = 0
        self.max_seq = 0
        self.applied: deque[tuple[int, int]] = deque()
        self.applied_sum = 0
        self.held: dict[int, list] = {}
        self.orders: dict[str, list] = {}


class Account:
    """Reconciliation state for one (store, sku) pair."""

    def __init__(self, initial_stock: int) -> None:
        self._base = initial_stock
        self._version = 0
        self._channels = {name: _ChannelState() for name in CHANNELS}

    def apply_revision(self, channel: str, revision: SalesRevision) -> None:
        """Apply one sales revision message on ``channel``."""
        ch = self._channels[channel]
        seq = revision.seq
        if seq <= ch.accountable or seq in ch.held:
            return  # duplicate delivery; content is identical by contract
        if seq > ch.max_seq:
            ch.max_seq = seq
        order_id = revision.order_id
        qty = revision.qty
        order = ch.orders.get(order_id)
        if order is None:
            order = ch.orders[order_id] = [0, 0, []]
        held_seqs = order[2]
        pos = bisect_left(held_seqs, seq)
        if pos:
            pred_qty = ch.held[held_seqs[pos - 1]][1]
        elif order[0]:
            pred_qty = order[1]
        else:
            pred_qty = 0
        delta = pred_qty - qty
        if pos < len(held_seqs):
            # The successor's predecessor becomes this event; it cannot
            # have been applied yet because its seq is beyond the
            # accountable prefix.
            ch.held[held_seqs[pos]][2] -= delta
        if seq == ch.accountable + 1:
            next_seq, next_order, next_qty, next_delta = seq, order_id, qty, delta
            while True:
                ch.accountable = next_seq
                entry_order = ch.orders[next_order]
                entry_order[0] = next_seq
                entry_order[1] = next_qty
                if next_seq > ch.watermark:
                    ch.applied.append((next_seq, next_delta))
                    ch.applied_sum += next_delta
                entry = ch.held.pop(next_seq + 1, None)
                if entry is None:
                    break
                next_seq += 1
                next_order, next_qty, next_delta = entry
                ch.orders[next_order][2].remove(next_seq)
        else:
            held_seqs.insert(pos, seq)
            ch.held[seq] = [order_id, qty, delta]

    def apply_stocktake(self, stocktake: Stocktake) -> None:
        """Adopt ``stocktake`` if its version is the highest seen."""
        if stocktake.version <= self._version:
            return
        self._version = stocktake.version
        self._base = stocktake.actual
        for name in CHANNELS:
            ch = self._channels[name]
            watermark = stocktake.watermarks[name]
            if watermark <= ch.watermark:
                continue
            ch.watermark = watermark
            applied = ch.applied
            while applied and applied[0][0] <= watermark:
                ch.applied_sum -= applied.popleft()[1]

    def snapshot(self) -> InventorySnapshot:
        """Return the current projection and pending flag."""
        projected = self._base
        pending = False
        for name in CHANNELS:
            ch = self._channels[name]
            projected += ch.applied_sum
            if ch.accountable < ch.watermark or ch.accountable < ch.max_seq:
                pending = True
        return InventorySnapshot(projected, pending)
