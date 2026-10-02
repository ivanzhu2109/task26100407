# inventory_recon — 多渠道库存对账库

门店 POS、电商、平台三渠道共用库存的进程内对账库。处理乱序、重复、
退货修订与盘点吸收，为补货系统提供持续收敛的库存投影。

## 安装与运行

纯标准库实现，Python 3.10+，无第三方依赖。将 `inventory_recon/` 包
放入项目即可直接 `import`。

```bash
python3 -m unittest discover -s tests   # 运行全部测试（含性能测试）
```

## 快速上手

```python
from inventory_recon import (
    Channel, InventoryReconciler, SalesRevision, Stocktake,
)

recon = InventoryReconciler()
recon.set_initial_stock("store-1", "sku-1", 100)   # 初始库存，须先于任何消息

# 批量接收销售修订：qty 是该订单在渠道内的当前累计净出库件数。
recon.apply_sales([
    SalesRevision("store-1", "sku-1", Channel.POS, seq=1, order_id="o-1", qty=5),
    SalesRevision("store-1", "sku-1", Channel.POS, seq=2, order_id="o-1", qty=3),  # 退货修订
])

# 接收盘点：watermarks 按 (POS, EC, PLATFORM) 顺序；只采用最高版本。
recon.apply_stocktakes([
    Stocktake("store-1", "sku-1", version=1, actual=92, watermarks=(1, 0, 0)),
])

state = recon.query("store-1", "sku-1")
state.projected  # 库存投影（允许为负，不截断）
state.pending    # 是否待补齐（任一渠道存在缺口或落后于盘点水位）
```

## 语义约定

- **核算单元**：每个 `(store, sku)` 独立核算；三渠道各自维护从 1 开始的
  连续事件序号；订单标识只在 `(store, sku, channel)` 内唯一。
- **销售修订**：`qty` 为订单当前累计净出库件数（非负，可增可减可归零）。
  库存变化按"与上一状态的差额"计算，上一状态由渠道序号决定，与到达
  顺序无关，也不会因盘点而重置。相同渠道序号的重复消息自动去重。
- **盘点**：`actual` 已包含各渠道序号不超过对应水位的全部出入库变化。
  只采用最高版本；旧版本不能回退库存。被盘点覆盖的迟到事件不会重复
  扣减，但仍会更新订单状态，保证后续修订差额正确。
- **可核算位置**：每渠道从序号 1 起无缺口的最大已收到序号。缺口之后的
  消息暂存，不影响库存；缺口补齐后立即反映。
- **待补齐**：任一渠道的可核算位置低于其盘点水位或已收到的最大序号。

## 接口

| 方法 | 说明 |
| --- | --- |
| `set_initial_stock(store, sku, qty)` | 设定初始库存（等价于版本 0、水位全 0 的盘点）；已有消息后调用会抛 `ValueError` |
| `apply_sales(events)` | 批量接收 `SalesRevision`，允许任意乱序与重复 |
| `apply_stocktakes(takes)` | 批量接收 `Stocktake`，只采用每个组合的最高版本 |
| `query(store, sku)` | 返回 `InventoryState(projected, pending)` |

## 实现要点

- 每渠道维护：连续序号指针、缺口暂存字典、订单最新累计件数、
  `(水位, 连续位置]` 区间的净变化之和及其事件队列。
- 投影 = 最高版本盘点实际库存 + 各渠道水位之后的净变化，查询为 O(1)。
- 盘点推进水位时，从队列头部弹出被覆盖的事件并回冲其净变化；
  订单状态独立保存，历史被盘点吸收后再次修订仍按整个渠道历史计算差额。
- 已被盘点覆盖的事件出队后即丢弃，内存占用只与"缺口滞留消息数 +
  未被盘点覆盖的连续事件数 + 订单数"相关，与历史总量无关。

## 性能

`tests/test_performance.py` 验证规定规模：200 万事件（含重复与缺口
滞留）、1 万次盘点、10 万次查询。本机实测加载阶段约 4 秒，库结构
额外峰值内存约 3 MiB，远低于 30 秒 / 128 MiB 的限制。
