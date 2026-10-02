"""Multi-account facade used by upper layers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .account import Account
from .types import InventorySnapshot, SalesRevision, Stocktake


class InventoryProjector:
    """Holds one reconciliation account per (store, sku) pair."""

    def __init__(self) -> None:
        self._accounts: dict[tuple[str, str], Account] = {}

    def open_account(self, store_id: str, sku: str, initial_stock: int) -> None:
        """Create an account with caller-given initial stock."""
        key = (store_id, sku)
        if key in self._accounts:
            raise ValueError(f"account already exists: {key!r}")
        self._accounts[key] = Account(initial_stock)

    def _account(self, store_id: str, sku: str) -> Account:
        try:
            return self._accounts[(store_id, sku)]
        except KeyError:
            raise KeyError(f"unknown account: {(store_id, sku)!r}") from None

    def apply_sales_revisions(
        self,
        store_id: str,
        sku: str,
        channel: str,
        revisions: Iterable[SalesRevision],
    ) -> None:
        """Batch-apply sales revisions for one channel."""
        account = self._account(store_id, sku)
        for revision in revisions:
            account.apply_revision(channel, revision)

    def apply_stocktake(
        self,
        store_id: str,
        sku: str,
        version: int,
        actual: int,
        watermarks: Mapping[str, int],
    ) -> None:
        """Apply a stocktake report to an account."""
        self._account(store_id, sku).apply_stocktake(
            Stocktake(version=version, actual=actual, watermarks=dict(watermarks))
        )

    def query(self, store_id: str, sku: str) -> InventorySnapshot:
        """Return the current projection and pending flag."""
        return self._account(store_id, sku).snapshot()
