"""Memory test: module peak stays far below the 128 MiB budget.

Internal structures are bounded by the workload constraints (held
messages, not-yet-absorbed events, orders), not by the event count,
so a 500k-event run reaches the same steady-state footprint as the
full 2M-event load while keeping tracemalloc overhead affordable.
"""

import random
import tracemalloc
import unittest

from inventory_recon import CHANNELS, InventoryProjector, SalesRevision

MEMORY_BUDGET = 128 * 2**20


class MemoryTest(unittest.TestCase):
    def test_peak_memory(self):
        rng = random.Random(7)
        n_accounts = 4
        accounts = [("store%d" % i, "sku%d" % i) for i in range(n_accounts)]
        streams = [(ai, c) for ai in range(n_accounts) for c in CHANNELS]
        per_stream = 500_000 // len(streams)

        projector = InventoryProjector()
        for store, sku in accounts:
            projector.open_account(store, sku, 1_000_000)

        order_qty = [dict() for _ in streams]
        next_seq = [1] * len(streams)

        tracemalloc.start()
        delivered = 0
        stocktake_account = 0
        for _round in range(per_stream):
            for si, (ai, channel) in enumerate(streams):
                seq = next_seq[si]
                next_seq[si] = seq + 1
                store, sku = accounts[ai]
                order_idx = rng.randrange(5000)
                qtys = order_qty[si]
                prev = qtys.get(order_idx, 0)
                if prev and rng.random() < 0.3:
                    qty = max(0, prev + rng.randint(-prev, 3))
                else:
                    qty = prev + rng.randint(0, 4)
                qtys[order_idx] = qty
                projector.apply_sales_revisions(
                    store, sku, channel, [SalesRevision(seq, "o%d" % order_idx, qty)]
                )
                delivered += 1
                if delivered % 200 == 0:
                    ai2 = stocktake_account % n_accounts
                    stocktake_account += 1
                    store2, sku2 = accounts[ai2]
                    watermarks = {}
                    for c in CHANNELS:
                        si2 = streams.index((ai2, c))
                        watermarks[c] = max(0, next_seq[si2] - 6)
                    projector.apply_stocktake(
                        store2, sku2, stocktake_account, 900_000, watermarks
                    )
                if delivered % 20 == 0:
                    store3, sku3 = accounts[(delivered // 20) % n_accounts]
                    projector.query(store3, sku3)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        print("\npeak module memory: %.1f MiB" % (peak / 2**20))
        self.assertLess(peak, MEMORY_BUDGET)


if __name__ == "__main__":
    unittest.main()
