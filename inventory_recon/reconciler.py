"""对账库门面：批量销售修订、盘点接入与库存查询。"""

from __future__ import annotations

from typing import Iterable

from .account import Account
from .types import InventoryState, SalesRevision, Stocktake


class InventoryReconciler:
    """多渠道库存对账器。调用串行，数据保存在进程内存中。"""

    def __init__(self) -> None:
        self._accounts: dict[tuple[str, str], Account] = {}

    def set_initial_stock(self, store: str, sku: str, quantity: int) -> None:
        """设定某 (门店, SKU) 的初始库存（等价于版本 0、水位全 0 的盘点）。

        必须在该组合收到任何销售或盘点消息之前调用，否则抛出 ValueError。
        """
        key = (store, sku)
        account = self._accounts.get(key)
        if account is None:
            self._accounts[key] = Account(quantity)
            return
        if account.base_version != 0 or any(
            channel.max_received for channel in account.channels
        ):
            raise ValueError(f"initial stock already locked for {key!r}")
        account.base_actual = quantity

    def apply_sales(self, events: Iterable[SalesRevision]) -> None:
        """批量接收销售修订，允许乱序与重复。"""
        accounts = self._accounts
        for event in events:
            key = (event.store, event.sku)
            account = accounts.get(key)
            if account is None:
                account = accounts[key] = Account(0)
            account.apply_sales(event.channel, event.seq, event.order_id, event.qty)

    def apply_stocktakes(self, takes: Iterable[Stocktake]) -> None:
        """接收盘点结果，只采用每个 (门店, SKU) 的最高版本。"""
        accounts = self._accounts
        for take in takes:
            key = (take.store, take.sku)
            account = accounts.get(key)
            if account is None:
                account = accounts[key] = Account(0)
            account.apply_stocktake(take.version, take.actual, take.watermarks)

    def query(self, store: str, sku: str) -> InventoryState:
        """查询库存投影与是否待补齐。未知组合视为初始库存 0 的全新组合。"""
        account = self._accounts.get((store, sku))
        if account is None:
            return InventoryState(0, False)
        return InventoryState(account.projected(), account.pending())
