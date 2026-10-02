"""规格书中的验收场景。"""

import unittest

from inventory_recon import (
    Channel,
    InventoryReconciler,
    SalesRevision,
    Stocktake,
)

STORE, SKU = "store-1", "sku-1"


class AcceptanceTest(unittest.TestCase):
    def test_out_of_order_revision_around_stocktake(self) -> None:
        recon = InventoryReconciler()
        recon.set_initial_stock(STORE, SKU, 100)

        # 同一订单依次报告累计 5、3、0 件（序号 1、2、3）。
        # 盘点版本 1：水位 1（已含序号 1 的 5 件出库），实际库存 92。
        recon.apply_stocktakes(
            [Stocktake(STORE, SKU, version=1, actual=92, watermarks=(1, 0, 0))]
        )
        # 先收到序号 3（累计 0 件），存在缺口，暂不影响库存。
        recon.apply_sales(
            [SalesRevision(STORE, SKU, Channel.POS, 3, "order-1", 0)]
        )
        # 再收到序号 1（累计 5 件）：被盘点覆盖，投影仍为 92，仍待补齐。
        recon.apply_sales(
            [SalesRevision(STORE, SKU, Channel.POS, 1, "order-1", 5)]
        )
        state = recon.query(STORE, SKU)
        self.assertEqual(state.projected, 92)
        self.assertTrue(state.pending)

        # 收到序号 2（累计 3 件）后缺口补齐：92 + 2 + 3 = 97，且已对齐。
        recon.apply_sales(
            [SalesRevision(STORE, SKU, Channel.POS, 2, "order-1", 3)]
        )
        state = recon.query(STORE, SKU)
        self.assertEqual(state.projected, 97)
        self.assertFalse(state.pending)

    def test_duplicate_events_not_double_counted(self) -> None:
        recon = InventoryReconciler()
        recon.set_initial_stock(STORE, SKU, 50)
        event = SalesRevision(STORE, SKU, Channel.EC, 1, "o-1", 4)
        recon.apply_sales([event, event, event])
        state = recon.query(STORE, SKU)
        self.assertEqual(state.projected, 46)
        self.assertFalse(state.pending)

    def test_negative_projection_not_truncated(self) -> None:
        recon = InventoryReconciler()
        recon.set_initial_stock(STORE, SKU, 2)
        recon.apply_sales([SalesRevision(STORE, SKU, Channel.POS, 1, "o-1", 5)])
        self.assertEqual(recon.query(STORE, SKU).projected, -3)

    def test_same_order_id_in_different_channels_isolated(self) -> None:
        recon = InventoryReconciler()
        recon.set_initial_stock(STORE, SKU, 100)
        recon.apply_sales(
            [
                SalesRevision(STORE, SKU, Channel.POS, 1, "o-1", 5),
                SalesRevision(STORE, SKU, Channel.EC, 1, "o-1", 7),
                SalesRevision(STORE, SKU, Channel.PLATFORM, 1, "o-1", 3),
            ]
        )
        self.assertEqual(recon.query(STORE, SKU).projected, 85)

    def test_old_stocktake_cannot_rollback(self) -> None:
        recon = InventoryReconciler()
        recon.set_initial_stock(STORE, SKU, 100)
        recon.apply_stocktakes(
            [Stocktake(STORE, SKU, version=5, actual=80, watermarks=(3, 3, 3))]
        )
        recon.apply_stocktakes(
            [Stocktake(STORE, SKU, version=2, actual=999, watermarks=(1, 1, 1))]
        )
        self.assertEqual(recon.query(STORE, SKU).projected, 80)


if __name__ == "__main__":
    unittest.main()
