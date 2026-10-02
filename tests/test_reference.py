"""Randomized differential tests against an independent reference model.

The reference computes the final state directly from the complete
input: per channel it replays events in seq order to derive each
order's delta chain, then adds only the changes beyond the highest
stocktake's watermarks to that stocktake's actual count.
"""

import random
import unittest

from inventory_recon import CHANNELS, InventoryProjector, SalesRevision


def reference_result(initial, events, stocktakes):
    """Compute (projected, pending) from a full input log.

    events: list of (channel, seq, order_id, qty) in arrival order.
    stocktakes: list of (version, actual, watermarks) in arrival order.
    """
    per_channel = {c: {} for c in CHANNELS}
    for channel, seq, order_id, qty in events:
        per_channel[channel][seq] = (order_id, qty)

    base = initial
    watermarks = {c: 0 for c in CHANNELS}
    if stocktakes:
        _, base, watermarks = max(stocktakes, key=lambda s: s[0])

    projected = base
    pending = False
    for channel in CHANNELS:
        received = per_channel[channel]
        watermark = watermarks[channel]
        last_qty = {}
        delta_sum = 0
        for seq in sorted(received):
            order_id, qty = received[seq]
            if seq > watermark:
                delta_sum += last_qty.get(order_id, 0) - qty
            last_qty[order_id] = qty
        projected += delta_sum
        accountable = 0
        while accountable + 1 in received:
            accountable += 1
        max_seq = max(received, default=0)
        if accountable < watermark or accountable < max_seq:
            pending = True
    return projected, pending


def generate_input(rng, n_events, n_orders, n_stocktakes):
    """Build a random event log plus stocktakes honouring the contracts."""
    order_qty = {c: {} for c in CHANNELS}
    channel_events = {c: [] for c in CHANNELS}
    per_channel_n = max(1, n_events // len(CHANNELS))
    for channel in CHANNELS:
        for seq in range(1, per_channel_n + 1):
            order_id = "o%d" % rng.randrange(n_orders)
            current = order_qty[channel].get(order_id, 0)
            if current and rng.random() < 0.3:
                current = max(0, current + rng.randint(-current, 3))
            else:
                current += rng.randint(0, 4)
            order_qty[channel][order_id] = current
            channel_events[channel].append((channel, seq, order_id, current))

    stocktakes = []
    watermarks = {c: 0 for c in CHANNELS}
    actual = 1000
    step = max(1, per_channel_n // max(1, n_stocktakes))
    for version in range(1, n_stocktakes + 1):
        for channel in CHANNELS:
            watermarks[channel] = min(
                per_channel_n, watermarks[channel] + rng.randint(0, step)
            )
        actual += rng.randint(-50, 50)
        stocktakes.append((version, actual, dict(watermarks)))

    delivery = []
    for channel in CHANNELS:
        for event in channel_events[channel]:
            delivery.append(("event", event))
            if rng.random() < 0.1:
                delivery.append(("event", event))
    for stocktake in stocktakes:
        delivery.append(("stocktake", stocktake))
    rng.shuffle(delivery)
    return delivery


class DifferentialTest(unittest.TestCase):
    def run_case(self, seed, n_events=600, n_orders=40, n_stocktakes=6):
        rng = random.Random(seed)
        delivery = generate_input(rng, n_events, n_orders, n_stocktakes)

        projector = InventoryProjector()
        projector.open_account("s", "k", 1000)
        events, stocktakes = [], []
        for kind, payload in delivery:
            if kind == "event":
                channel, seq, order_id, qty = payload
                events.append(payload)
                projector.apply_sales_revisions(
                    "s", "k", channel, [SalesRevision(seq, order_id, qty)]
                )
            else:
                stocktakes.append(payload)
                version, actual, watermarks = payload
                projector.apply_stocktake("s", "k", version, actual, watermarks)

        snap = projector.query("s", "k")
        expected = reference_result(1000, events, stocktakes)
        self.assertEqual(
            (snap.projected, snap.pending),
            expected,
            "seed=%r: got %r, want %r"
            % (seed, (snap.projected, snap.pending), expected),
        )

    def test_randomized_against_reference(self):
        for seed in range(60):
            with self.subTest(seed=seed):
                self.run_case(seed)

    def test_dense_stocktakes_and_tiny_streams(self):
        for seed in range(1000, 1040):
            with self.subTest(seed=seed):
                self.run_case(seed, n_events=90, n_orders=8, n_stocktakes=12)


if __name__ == "__main__":
    unittest.main()
