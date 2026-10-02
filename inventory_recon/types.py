"""公共类型定义：渠道常量与三类操作的输入/输出数据结构。"""

from __future__ import annotations

from typing import NamedTuple


class Channel:
    """三个销售渠道的标识常量。"""

    POS = "pos"
    EC = "ec"
    PLATFORM = "platform"

    ALL = (POS, EC, PLATFORM)


class SalesRevision(NamedTuple):
    """一条销售修订消息。

    qty 是该订单在所属渠道内的当前累计净出库件数（非负整数），
    不是本次新增量。
    """

    store: str
    sku: str
    channel: str
    seq: int
    order_id: str
    qty: int


class Stocktake(NamedTuple):
    """一份盘点结果。

    watermarks 按 (POS, EC, PLATFORM) 顺序给出各渠道水位；
    actual 为实际库存，已包含各渠道序号不超过对应水位的全部出入库变化。
    """

    store: str
    sku: str
    version: int
    actual: int
    watermarks: tuple[int, int, int]


class InventoryState(NamedTuple):
    """查询结果：库存投影与是否待补齐。"""

    projected: int
    pending: bool
