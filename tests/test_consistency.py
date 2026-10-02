"""随机乱序/重复/盘点交错输入下，与独立参考实现的全量对比。"""

import random
import unittest

from inventory_recon import (
    Channel,
    InventoryReconciler,
    SalesRevision,
    Stocktake,
)

CHANNELS = (Channel.POS, Channel.EC, Channel.PLATFORM)


class ReferenceAccount:
    """独立参考实现：保存全部事件，查询时按渠道序号完整重放。"""

    def __init__(self, initial: int) -> None:
        self.base_version = 0
        self.base_actual = initial
        self.watermarks = (0, 0, 0)
        self.events = ({}, {}, {})  # 每渠道 seq -> (order_id, qty)

    def add_sale(self, channel: int, seq: int, order_id: str, qty: int) -> None:
        self.events[channel].setdefault(seq, (order_id, qty))

    def add_take(self, version: int, actual: int, watermarks) -> None:
        if version > self.base_version:
            self.base_version = version
            self.base_actual = actual
            self.watermarks = tuple(watermarks)

    def state(self):
        total = self.base_actual
        pending = False
        for ch in range(3):
            seqs = self.events[ch]
            contiguous = 0
            while contiguous + 1 in seqs:
                contiguous += 1
            orders = {}
            for seq in range(1, contiguous + 1):
                order_id, qty = seqs[seq]
                prev = orders.get(order_id, 0)
                if seq > self.watermarks[ch]:
                    total += prev - qty
                orders[order_id] = qty
            max_received = max(seqs) if seqs else 0
            if contiguous < self.watermarks[ch] or contiguous < max_received:
                pending = True
        return total, pending


class ConsistencyTest(unittest.TestCase):
    def run_scenario(self, seed: int) -> None:
        rng = random.Random(seed)
        recon = InventoryReconciler()
        refs = {}
        accounts = [(f"store-{i % 2}", f"sku-{i % 3}") for i in range(3)]
        for store, sku in accounts:
            initial = rng.randint(0, 500)
            recon.set_initial_stock(store, sku, initial)
            refs[(store, sku)] = ReferenceAccount(initial)

        # 预生成每账户每渠道的完整事件流（序号 1..N），订单会被反复修订。
        streams = {}
        order_qty = {}
        for key in accounts:
            for ch_idx, ch in enumerate(CHANNELS):
                length = rng.randint(0, 120)
                events = []
                for seq in range(1, length + 1):
                    order_id = f"o-{ch_idx}-{rng.randint(0, 15)}"
                    okey = (key, ch_idx, order_id)
                    prev = order_qty.get(okey, 0)
                    qty = max(0, prev + rng.randint(-6, 8))
                    order_qty[okey] = qty
                    events.append(SalesRevision(key[0], key[1], ch, seq, order_id, qty))
                streams[(key, ch_idx)] = events

        # 生成盘点：版本递增，水位不下降，偶尔领先于已投递销售。
        takes = {key: [] for key in accounts}
        for key in accounts:
            watermarks = [0, 0, 0]
            for version in range(1, rng.randint(2, 8)):
                for ch_idx in range(3):
                    high = len(streams[(key, ch_idx)]) + rng.randint(0, 3)
                    high = max(high, watermarks[ch_idx])
                    watermarks[ch_idx] = rng.randint(watermarks[ch_idx], high)
                actual = rng.randint(0, 2000)
                takes[key].append(
                    Stocktake(key[0], key[1], version, actual, tuple(watermarks))
                )

        # 投递计划：事件整体乱序 + 重复，盘点乱序（新版可能先到），穿插查询。
        plan = []
        for key in accounts:
            for ch_idx in range(3):
                for event in streams[(key, ch_idx)]:
                    plan.append(("sale", event))
                    if rng.random() < 0.15:
                        plan.append(("sale", event))  # 重复投递
            for take in takes[key]:
                plan.append(("take", take))
        rng.shuffle(plan)
        for _ in range(40):
            plan.insert(rng.randint(0, len(plan)), ("query", rng.choice(accounts)))

        sales_batch, take_batch = [], []

        def flush():
            if sales_batch:
                recon.apply_sales(sales_batch)
                for e in sales_batch:
                    refs[(e.store, e.sku)].add_sale(
                        CHANNELS.index(e.channel), e.seq, e.order_id, e.qty
                    )
                sales_batch.clear()
            if take_batch:
                recon.apply_stocktakes(take_batch)
                for t in take_batch:
                    refs[(t.store, t.sku)].add_take(t.version, t.actual, t.watermarks)
                take_batch.clear()

        for kind, payload in plan:
            if kind == "sale":
                sales_batch.append(payload)
            elif kind == "take":
                take_batch.append(payload)
            else:
                flush()
                store, sku = payload
                expected = refs[(store, sku)].state()
                got = recon.query(store, sku)
                self.assertEqual(
                    (got.projected, got.pending), expected, f"seed={seed} key={payload}"
                )
        flush()
        for key in accounts:
            expected = refs[key].state()
            got = recon.query(*key)
            self.assertEqual(
                (got.projected, got.pending), expected, f"seed={seed} final key={key}"
            )

    def test_randomized_scenarios(self) -> None:
        for seed in range(30):
            with self.subTest(seed=seed):
                self.run_scenario(seed)

    def test_revision_after_absorbed_by_stocktake(self) -> None:
        """历史被盘点吸收后，旧订单再次退货/补出库仍按整个渠道历史计算差额。"""
        recon = InventoryReconciler()
        recon.set_initial_stock("s", "k", 1000)
        # 序号 1..50：订单 o 累计到 200 件，随后盘点全部吸收。
        recon.apply_sales(
            [SalesRevision("s", "k", Channel.POS, seq, "o", seq * 4) for seq in range(1, 51)]
        )
        recon.apply_stocktakes([Stocktake("s", "k", 1, 800, (50, 0, 0))])
        self.assertEqual(recon.query("s", "k").projected, 800)
        # 序号 51：退货修订为 150 件 -> 库存 +50；序号 52：又补出库到 170 -> -20。
        recon.apply_sales(
            [
                SalesRevision("s", "k", Channel.POS, 51, "o", 150),
                SalesRevision("s", "k", Channel.POS, 52, "o", 170),
            ]
        )
        state = recon.query("s", "k")
        self.assertEqual(state.projected, 830)
        self.assertFalse(state.pending)
        # 更高版本盘点（水位 52，实际 850）吸收上述变化后，再退货到 100 -> +70。
        recon.apply_stocktakes([Stocktake("s", "k", 2, 850, (52, 0, 0))])
        recon.apply_sales([SalesRevision("s", "k", Channel.POS, 53, "o", 100)])
        self.assertEqual(recon.query("s", "k").projected, 920)


if __name__ == "__main__":
    unittest.main()
