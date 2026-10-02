"""规定规模负载测试：200 万事件、1 万盘点、10 万查询。

- 总耗时（加载阶段）不超过 30 秒；
- 库结构带来的额外峰值内存不超过 128MiB；
- 全程与在线维护的参考值核对，结束后逐账户校验投影与对齐状态。
"""

import random
import resource
import sys
import time
import unittest
from collections import deque

from inventory_recon import (
    Channel,
    InventoryReconciler,
    SalesRevision,
    Stocktake,
)

CHANNELS = (Channel.POS, Channel.EC, Channel.PLATFORM)
N_ACCOUNTS = 50
N_ORDERS = 20_000
STREAM_LEN = 13_000  # 150 条流 x 13000 = 195 万唯一事件
TOTAL_DELIVERIES = 2_000_000  # 其余为重复投递
N_TAKES = 10_000
N_QUERIES = 100_000
MAX_EXTRA_RSS = 128 * 1024 * 1024


def _rss_bytes() -> int:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux 单位为 KiB，macOS 单位为字节。
    return rss if sys.platform == "darwin" else rss * 1024


class Stream:
    __slots__ = ("account", "channel", "next_seq", "order_qty", "orders",
                 "recent", "held", "cum_delta", "delta_at_take")

    def __init__(self, account: int, channel: int, orders: list) -> None:
        self.account = account
        self.channel = channel
        self.next_seq = 1
        self.order_qty = {}
        self.orders = orders
        self.recent = deque(maxlen=16)
        self.held = None
        self.cum_delta = 0
        self.delta_at_take = 0


class PerformanceTest(unittest.TestCase):
    def test_full_load(self) -> None:
        rng = random.Random(7)
        recon = InventoryReconciler()
        accounts = [(f"store-{i}", f"sku-{i}") for i in range(N_ACCOUNTS)]
        for store, sku in accounts:
            recon.set_initial_stock(store, sku, 1_000_000)

        # 订单分配到 150 条 (账户 x 渠道) 流。
        stream_orders = [[] for _ in range(N_ACCOUNTS * 3)]
        for i in range(N_ORDERS):
            stream_orders[i % len(stream_orders)].append(f"order-{i}")
        streams = [
            Stream(a, c, stream_orders[a * 3 + c])
            for a in range(N_ACCOUNTS)
            for c in range(3)
        ]
        version = [0] * N_ACCOUNTS
        last_actual = [0] * N_ACCOUNTS

        def make_event(st: Stream, seq: int) -> SalesRevision:
            order_id = rng.choice(st.orders)
            prev = st.order_qty.get(order_id, 0)
            qty = max(0, prev + rng.randint(-4, 6))
            st.order_qty[order_id] = qty
            st.cum_delta += prev - qty
            store, sku = accounts[st.account]
            return SalesRevision(store, sku, CHANNELS[st.channel], seq, order_id, qty)

        def emit_take(account: int) -> None:
            version[account] += 1
            watermarks = []
            for c in range(3):
                st = streams[account * 3 + c]
                st.delta_at_take = st.cum_delta
                watermarks.append(st.next_seq - 1)
            last_actual[account] = rng.randint(0, 2_000_000)
            store, sku = accounts[account]
            recon.apply_stocktakes(
                [Stocktake(store, sku, version[account], last_actual[account],
                           tuple(watermarks))]
            )

        rss_before = _rss_bytes()
        start = time.perf_counter()

        deliveries = takes = queries = 0
        remaining = TOTAL_DELIVERIES
        while remaining > 0:
            st = streams[rng.randrange(len(streams))]
            batch = []
            # 已滞留的事件优先补投，控制全局滞留规模。
            if st.held is not None and rng.random() < 0.5:
                batch.append(st.held)
                st.held = None
            roll = rng.random()
            if roll < 0.03 and st.recent:
                batch.append(rng.choice(tuple(st.recent)))  # 重复投递
            elif st.next_seq <= STREAM_LEN:
                if roll < 0.06 and st.held is None and st.next_seq < STREAM_LEN:
                    st.held = make_event(st, st.next_seq)  # 滞留，先投下一条
                    st.next_seq += 1
                event = make_event(st, st.next_seq)
                st.next_seq += 1
                st.recent.append(event)
                batch.append(event)
            if not batch:
                continue
            recon.apply_sales(batch)
            remaining -= len(batch)
            deliveries += len(batch)
            if deliveries % 200 == 0 and takes < N_TAKES:
                emit_take((deliveries // 200) % N_ACCOUNTS)
                takes += 1
            if deliveries % 20 == 0 and queries < N_QUERIES:
                recon.query(*accounts[rng.randrange(N_ACCOUNTS)])
                queries += 1
        # 收尾：补投滞留事件，补齐盘点与查询次数。
        for st in streams:
            if st.held is not None:
                recon.apply_sales([st.held])
                st.held = None
        while takes < N_TAKES:
            emit_take(takes % N_ACCOUNTS)
            takes += 1
        while queries < N_QUERIES:
            recon.query(*accounts[rng.randrange(N_ACCOUNTS)])
            queries += 1

        elapsed = time.perf_counter() - start
        extra_rss = _rss_bytes() - rss_before

        self.assertEqual(deliveries, TOTAL_DELIVERIES)
        self.assertLess(elapsed, 30.0, f"load took {elapsed:.1f}s")
        self.assertLess(extra_rss, MAX_EXTRA_RSS,
                        f"extra rss {extra_rss / 2**20:.1f} MiB")

        # 全量校验：投影 == 末次盘点实际库存 + 各流水位之后的净变化。
        for a in range(N_ACCOUNTS):
            expected = last_actual[a]
            for c in range(3):
                st = streams[a * 3 + c]
                expected += st.cum_delta - st.delta_at_take
            state = recon.query(*accounts[a])
            self.assertEqual(state.projected, expected, f"account {a}")
            self.assertFalse(state.pending, f"account {a}")
        print(f"\nperf: {elapsed:.1f}s, extra rss {extra_rss / 2**20:.1f} MiB, "
              f"{deliveries} events, {takes} takes, {queries} queries")


if __name__ == "__main__":
    unittest.main()
