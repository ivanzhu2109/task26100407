"""Scale test: 2M events, 10k stocktakes, 100k queries.

Asserts the processing stays within the 30 s budget and that the
internal structures respect the workload bounds (held-back messages,
not-yet-absorbed events, orders), which is what caps module memory.
Also verifies the final projection of every account against the
independently tracked expected value, including revisions of orders
that earlier stocktakes have already absorbed.
"""

import random
import time
import unittest
from collections import deque

from inventory_recon import CHANNELS, InventoryProjector, SalesRevision

N_ACCOUNTS = 4
ORDERS_PER_ACCOUNT = 5000  # 20k orders in total
TOTAL_EVENTS = 2_000_000
STOCKTAKE_INTERVAL = 200  # -> 10k stocktakes
QUERY_INTERVAL = 20  # -> 100k queries
TIME_BUDGET_S = 30.0
MAX_HELD = 2000
MAX_UNCOVERED = 20000
TAIL_TRACK = 512


class ScaleTest(unittest.TestCase):
    def test_streaming_scale(self):
        rng = random.Random(20261004)
        accounts = [("store%d" % i, "sku%d" % i) for i in range(N_ACCOUNTS)]
        streams = [(ai, c) for ai in range(N_ACCOUNTS) for c in CHANNELS]
        per_stream = -(-TOTAL_EVENTS // len(streams))  # ceil, >= 2M in total

        projector = InventoryProjector()
        for store, sku in accounts:
            projector.open_account(store, sku, 1_000_000)

        # Per-stream generation state.
        order_qty = [dict() for _ in streams]
        next_seq = [1] * len(streams)
        held = [[] for _ in streams]  # (release_after, SalesRevision)
        tail = [deque() for _ in streams]  # (seq, delta) of recent events
        last_actual = [1_000_000] * N_ACCOUNTS
        last_wm = [{c: 0 for c in CHANNELS} for _ in accounts]

        def generate(si):
            seq = next_seq[si]
            next_seq[si] = seq + 1
            order_idx = rng.randrange(ORDERS_PER_ACCOUNT)
            qtys = order_qty[si]
            prev = qtys.get(order_idx, 0)
            if prev and rng.random() < 0.3:
                qty = max(0, prev + rng.randint(-prev, 3))
            else:
                qty = prev + rng.randint(0, 4)
            qtys[order_idx] = qty
            delta = prev - qty
            tail[si].append((seq, delta))
            if len(tail[si]) > TAIL_TRACK:
                tail[si].popleft()
            return SalesRevision(seq, "o%d" % order_idx, qty)

        def internal_totals():
            held_n = applied_n = 0
            for store, sku in accounts:
                account = projector._accounts[(store, sku)]
                for name in CHANNELS:
                    ch = account._channels[name]
                    held_n += len(ch.held)
                    applied_n += len(ch.applied)
            return held_n, applied_n

        max_held = max_applied = 0
        n_stocktakes = n_queries = 0
        delivered = 0
        stocktake_account = 0
        query_account = 0

        start = time.perf_counter()
        active = [i for i in range(len(streams))]
        while active:
            for si in list(active):
                if next_seq[si] > per_stream:
                    # Flush anything still held for this stream.
                    for _, revision in held[si]:
                        ai, channel = streams[si]
                        store, sku = accounts[ai]
                        projector.apply_sales_revisions(
                            store, sku, channel, [revision]
                        )
                    held[si] = []
                    active.remove(si)
                    continue
                revision = generate(si)
                ai, channel = streams[si]
                store, sku = accounts[ai]
                if held[si] and held[si][0][0] <= delivered:
                    _, old = held[si].pop(0)
                    projector.apply_sales_revisions(store, sku, channel, [old])
                if rng.random() < 0.002:
                    held[si].append((delivered + rng.randint(2, 8), revision))
                    max_held = max(max_held, sum(len(h) for h in held))
                else:
                    projector.apply_sales_revisions(store, sku, channel, [revision])
                    if rng.random() < 0.02:
                        projector.apply_sales_revisions(
                            store, sku, channel, [revision]
                        )
                delivered += 1
                if delivered % STOCKTAKE_INTERVAL == 0:
                    ai = stocktake_account % N_ACCOUNTS
                    stocktake_account += 1
                    store, sku = accounts[ai]
                    watermarks = {}
                    for c in CHANNELS:
                        si2 = streams.index((ai, c))
                        watermarks[c] = max(0, next_seq[si2] - 1 - 5)
                    last_wm[ai] = watermarks
                    actual = 900_000 + rng.randint(-1000, 1000)
                    last_actual[ai] = actual
                    projector.apply_stocktake(
                        store, sku, stocktake_account, actual, watermarks
                    )
                    n_stocktakes += 1
                if delivered % QUERY_INTERVAL == 0:
                    store, sku = accounts[query_account % N_ACCOUNTS]
                    query_account += 1
                    projector.query(store, sku)
                    n_queries += 1
                if delivered % 100_000 == 0:
                    held_n, applied_n = internal_totals()
                    max_held = max(max_held, held_n)
                    max_applied = max(max_applied, applied_n)
        elapsed = time.perf_counter() - start

        # Final correctness: projection == last stocktake actual plus the
        # exact tail of changes beyond its watermarks.
        for ai, (store, sku) in enumerate(accounts):
            expected = last_actual[ai]
            for c in CHANNELS:
                si = streams.index((ai, c))
                watermark = last_wm[ai][c]
                for seq, delta in tail[si]:
                    if seq > watermark:
                        expected += delta
            snap = projector.query(store, sku)
            self.assertEqual(snap.projected, expected, "account %r" % (ai,))
            self.assertFalse(snap.pending, "account %r" % (ai,))

        held_n, applied_n = internal_totals()
        max_held = max(max_held, held_n)
        max_applied = max(max_applied, applied_n)
        print(
            "\nprocessed %d events, %d stocktakes, %d queries in %.2fs; "
            "max held=%d, max uncovered=%d"
            % (delivered, n_stocktakes, n_queries, elapsed, max_held, max_applied)
        )
        self.assertEqual(delivered, len(streams) * per_stream)
        self.assertGreaterEqual(n_stocktakes, 10_000)
        self.assertGreaterEqual(n_queries, 100_000)
        self.assertLess(elapsed, TIME_BUDGET_S)
        self.assertLessEqual(max_held, MAX_HELD)
        self.assertLessEqual(max_applied, MAX_UNCOVERED)


if __name__ == "__main__":
    unittest.main()
