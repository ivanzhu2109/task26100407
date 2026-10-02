"""单个 (门店, SKU) 组合的核算状态。

核心不变式（对每个渠道）：
- contiguous：从序号 1 起无缺口的最大已收到序号。
- 投影 = 最高版本盘点实际库存 + 各渠道 (水位, contiguous] 区间内的库存变化之和。
- 事件的库存变化 = -(该订单本次累计件数 - 该订单上一状态累计件数)，
  上一状态按整个渠道历史中的渠道序号确定，与到达顺序无关，也不因盘点重置。
"""

from __future__ import annotations

from collections import deque

from .types import Channel

_CHANNEL_INDEX = {Channel.POS: 0, Channel.EC: 1, Channel.PLATFORM: 2}


class _ChannelState:
    """单渠道状态。"""

    __slots__ = (
        "contiguous",
        "max_received",
        "watermark",
        "applied",
        "gap",
        "window",
        "orders",
    )

    def __init__(self) -> None:
        self.contiguous = 0
        self.max_received = 0
        self.watermark = 0
        # (watermark, contiguous] 区间内事件对库存的净影响之和。
        self.applied = 0
        # 缺口滞留消息：seq -> (order_id, qty)，只含 seq > contiguous。
        self.gap: dict[int, tuple[str, int]] = {}
        # 已连续但尚未被盘点覆盖的事件：按序号升序的 (seq, 库存变化)。
        self.window: deque[tuple[int, int]] = deque()
        # 订单最近已核算状态：order_id -> 最新累计件数（按渠道序号）。
        self.orders: dict[str, int] = {}

    def apply_sales(self, seq: int, order_id: str, qty: int) -> None:
        if seq <= self.contiguous or seq in self.gap:
            return  # 重复事件，内容保证一致，直接忽略
        self.gap[seq] = (order_id, qty)
        if seq > self.max_received:
            self.max_received = seq
        while self.contiguous + 1 in self.gap:
            self.contiguous += 1
            oid, q = self.gap.pop(self.contiguous)
            self._account(self.contiguous, oid, q)

    def _account(self, seq: int, order_id: str, qty: int) -> None:
        prev = self.orders.get(order_id, 0)
        self.orders[order_id] = qty
        if seq <= self.watermark:
            return  # 已被盘点覆盖，不得重复扣减，但仍需更新订单状态
        delta = prev - qty  # 出库增加则库存减少
        self.applied += delta
        self.window.append((seq, delta))

    def advance_watermark(self, watermark: int) -> None:
        self.watermark = watermark
        window = self.window
        applied = 0
        while window and window[0][0] <= watermark:
            applied += window.popleft()[1]
        self.applied -= applied

    def lagging(self) -> bool:
        return self.contiguous < self.watermark or self.contiguous < self.max_received


class Account:
    """一个 (store, sku) 的独立核算单元。"""

    __slots__ = ("base_version", "base_actual", "channels")

    def __init__(self, initial_stock: int) -> None:
        self.base_version = 0
        self.base_actual = initial_stock
        self.channels = [_ChannelState(), _ChannelState(), _ChannelState()]

    def apply_sales(self, channel: str, seq: int, order_id: str, qty: int) -> None:
        self.channels[_CHANNEL_INDEX[channel]].apply_sales(seq, order_id, qty)

    def apply_stocktake(
        self, version: int, actual: int, watermarks: tuple[int, int, int]
    ) -> None:
        if version <= self.base_version:
            return  # 旧盘点不能回退库存
        self.base_version = version
        self.base_actual = actual
        for index, watermark in enumerate(watermarks):
            if watermark > self.channels[index].watermark:
                self.channels[index].advance_watermark(watermark)

    def projected(self) -> int:
        total = self.base_actual
        for channel in self.channels:
            total += channel.applied
        return total

    def pending(self) -> bool:
        return any(channel.lagging() for channel in self.channels)
