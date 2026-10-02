"""Unit tests for the core reconciliation rules."""

import unittest

from inventory_recon import InventoryProjector, SalesRevision


def make_projector(initial=100):
    projector = InventoryProjector()
    projector.open_account("s1", "k1", initial)
    return projector


def rev(seq, order, qty):
    return SalesRevision(seq=seq, order_id=order, qty=qty)


class AcceptanceScenarioTest(unittest.TestCase):
    def test_revision_chain_with_leading_stocktake(self):
        # Order reports cumulative 5, then 3, then 0 on seqs 1..3.
        # Stocktake v1: watermark pos=1, actual 92.
        p = make_projector()
        p.apply_stocktake("s1", "k1", 1, 92, {"pos": 1, "ecom": 0, "platform": 0})
        p.apply_sales_revisions("s1", "k1", "pos", [rev(3, "o1", 0)])
        p.apply_sales_revisions("s1", "k1", "pos", [rev(1, "o1", 5)])
        snap = p.query("s1", "k1")
        self.assertEqual(snap.projected, 92)
        self.assertTrue(snap.pending)
        p.apply_sales_revisions("s1", "k1", "pos", [rev(2, "o1", 3)])
        snap = p.query("s1", "k1")
        self.assertEqual(snap.projected, 97)
        self.assertFalse(snap.pending)


class RevisionRuleTest(unittest.TestCase):
    def test_cumulative_not_delta(self):
        p = make_projector()
        p.apply_sales_revisions("s1", "k1", "pos", [rev(1, "o1", 5)])
        self.assertEqual(p.query("s1", "k1").projected, 95)
        # 5 -> 3 gives 2 units back, not -3.
        p.apply_sales_revisions("s1", "k1", "pos", [rev(2, "o1", 3)])
        self.assertEqual(p.query("s1", "k1").projected, 97)
        # 3 -> 0 gives the remaining 3 back.
        p.apply_sales_revisions("s1", "k1", "pos", [rev(3, "o1", 0)])
        self.assertEqual(p.query("s1", "k1").projected, 100)

    def test_duplicate_events_ignored(self):
        p = make_projector()
        batch = [rev(1, "o1", 5), rev(2, "o2", 2)]
        p.apply_sales_revisions("s1", "k1", "pos", batch)
        p.apply_sales_revisions("s1", "k1", "pos", batch)
        p.apply_sales_revisions("s1", "k1", "pos", [rev(1, "o1", 5)])
        snap = p.query("s1", "k1")
        self.assertEqual(snap.projected, 93)
        self.assertFalse(snap.pending)

    def test_gap_blocks_then_flushes(self):
        p = make_projector()
        p.apply_sales_revisions("s1", "k1", "pos", [rev(3, "o3", 1)])
        snap = p.query("s1", "k1")
        self.assertEqual(snap.projected, 100)
        self.assertTrue(snap.pending)
        p.apply_sales_revisions("s1", "k1", "pos", [rev(1, "o1", 2)])
        self.assertEqual(p.query("s1", "k1").projected, 98)
        self.assertTrue(p.query("s1", "k1").pending)
        p.apply_sales_revisions("s1", "k1", "pos", [rev(2, "o2", 4)])
        snap = p.query("s1", "k1")
        self.assertEqual(snap.projected, 93)
        self.assertFalse(snap.pending)

    def test_negative_projection_not_truncated(self):
        p = make_projector(initial=2)
        p.apply_sales_revisions("s1", "k1", "pos", [rev(1, "o1", 5)])
        self.assertEqual(p.query("s1", "k1").projected, -3)

    def test_same_order_id_isolated_per_channel(self):
        p = make_projector()
        p.apply_sales_revisions("s1", "k1", "pos", [rev(1, "o1", 5)])
        p.apply_sales_revisions("s1", "k1", "ecom", [rev(1, "o1", 2)])
        p.apply_sales_revisions("s1", "k1", "platform", [rev(1, "o1", 3)])
        self.assertEqual(p.query("s1", "k1").projected, 90)

    def test_accounts_isolated(self):
        p = make_projector()
        p.open_account("s1", "k2", 50)
        p.apply_sales_revisions("s1", "k1", "pos", [rev(1, "o1", 5)])
        self.assertEqual(p.query("s1", "k2").projected, 50)


class StocktakeRuleTest(unittest.TestCase):
    def test_old_stocktake_cannot_regress(self):
        p = make_projector()
        p.apply_stocktake("s1", "k1", 2, 80, {"pos": 0, "ecom": 0, "platform": 0})
        p.apply_stocktake("s1", "k1", 1, 95, {"pos": 0, "ecom": 0, "platform": 0})
        self.assertEqual(p.query("s1", "k1").projected, 80)

    def test_covered_late_events_not_double_counted(self):
        p = make_projector()
        p.apply_sales_revisions("s1", "k1", "pos", [rev(1, "o1", 5), rev(2, "o2", 3)])
        self.assertEqual(p.query("s1", "k1").projected, 92)
        # Stocktake already includes seqs 1..2 (actual 90).
        p.apply_stocktake("s1", "k1", 1, 90, {"pos": 2, "ecom": 0, "platform": 0})
        self.assertEqual(p.query("s1", "k1").projected, 90)
        # Late duplicates of covered events must not re-apply.
        p.apply_sales_revisions("s1", "k1", "pos", [rev(1, "o1", 5), rev(2, "o2", 3)])
        snap = p.query("s1", "k1")
        self.assertEqual(snap.projected, 90)
        self.assertFalse(snap.pending)

    def test_stocktake_ahead_of_sales(self):
        # Watermark leads the received stream: pending until caught up,
        # covered events are skipped when they finally arrive.
        p = make_projector()
        p.apply_stocktake("s1", "k1", 1, 88, {"pos": 3, "ecom": 0, "platform": 0})
        snap = p.query("s1", "k1")
        self.assertEqual(snap.projected, 88)
        self.assertTrue(snap.pending)
        p.apply_sales_revisions(
            "s1", "k1", "pos", [rev(1, "o1", 4), rev(2, "o2", 1), rev(3, "o3", 2)]
        )
        snap = p.query("s1", "k1")
        self.assertEqual(snap.projected, 88)
        self.assertFalse(snap.pending)

    def test_covered_order_revised_again_uses_full_history(self):
        # Order absorbed by a stocktake, then revised again afterwards:
        # the delta is against its last channel state, not against zero.
        p = make_projector()
        p.apply_sales_revisions("s1", "k1", "pos", [rev(1, "o1", 5)])
        p.apply_stocktake("s1", "k1", 1, 95, {"pos": 1, "ecom": 0, "platform": 0})
        # Return: 5 -> 2, so 3 units come back.
        p.apply_sales_revisions("s1", "k1", "pos", [rev(2, "o1", 2)])
        self.assertEqual(p.query("s1", "k1").projected, 98)
        # Absorb that too, then increase outbound: 2 -> 4 removes 2.
        p.apply_stocktake("s1", "k1", 2, 98, {"pos": 2, "ecom": 0, "platform": 0})
        p.apply_sales_revisions("s1", "k1", "pos", [rev(3, "o1", 4)])
        snap = p.query("s1", "k1")
        self.assertEqual(snap.projected, 96)
        self.assertFalse(snap.pending)

    def test_gap_beyond_watermark_still_pending(self):
        p = make_projector()
        p.apply_stocktake("s1", "k1", 1, 90, {"pos": 2, "ecom": 0, "platform": 0})
        # seq 4 arrives while 2..3 are missing; it is covered by the
        # watermark? no: 4 > 2, so it is held and the account is pending.
        p.apply_sales_revisions("s1", "k1", "pos", [rev(4, "o4", 1)])
        self.assertTrue(p.query("s1", "k1").pending)
        p.apply_sales_revisions(
            "s1", "k1", "pos", [rev(1, "o1", 1), rev(2, "o2", 1), rev(3, "o3", 1)]
        )
        snap = p.query("s1", "k1")
        self.assertFalse(snap.pending)
        # seqs 1..2 are covered by the watermark; only 3 and 4 apply.
        self.assertEqual(snap.projected, 88)


if __name__ == "__main__":
    unittest.main()
