# task26100407

## inventory_recon：多渠道库存对账库

纯 Python、进程内存中的库存投影库。门店 POS、电商、平台三个渠道共用
库存，销售修订与盘点消息可任意乱序、重复到达，库会持续收敛出库存
投影，供上层补货系统调用。

### 概念

- 每个 `(store_id, sku)` 一个独立账户，初始库存由调用方给定。
- 三个渠道固定为 `pos`、`ecom`、`platform`（`inventory_recon.CHANNELS`），
  各自维护从 1 开始的连续事件序号；订单标识只在账户与渠道内唯一。
- 销售修订携带订单的**当前累计净出库件数**（非新增量）。同一订单从
  5 件修订为 3 件，库存回增 2 件；差额依据该订单在渠道序号历史上的
  上一状态计算，与消息到达顺序无关。
- 盘点携带递增版本、实际库存与各渠道水位。实际库存已包含水位以内的
  全部出入库变化；只采用最高版本，旧盘点到达不会回退库存。
- 每渠道只有从序号 1 起无缺口的部分可核算；缺口之后的消息暂存，补齐
  后立即生效。水位之后、可核算位置以内的变化追加到盘点数量上。

### 使用

```python
from inventory_recon import InventoryProjector, SalesRevision

projector = InventoryProjector()
projector.open_account("store-1", "sku-9", initial_stock=100)

# 批量接收某渠道的销售修订（可乱序、可重复）
projector.apply_sales_revisions("store-1", "sku-9", "pos", [
    SalesRevision(seq=3, order_id="A100", qty=0),
    SalesRevision(seq=1, order_id="A100", qty=5),
    SalesRevision(seq=2, order_id="A100", qty=3),
])

# 接收盘点：版本、实际库存、三个渠道各自的水位
projector.apply_stocktake(
    "store-1", "sku-9",
    version=7,
    actual=92,
    watermarks={"pos": 1, "ecom": 0, "platform": 0},
)

# 查询投影；pending=True 表示仍有渠道待补齐
snap = projector.query("store-1", "sku-9")
snap.projected  # 库存投影，允许为负，不会截断为 0
snap.pending    # 任一渠道可核算位置落后于水位或已收最大序号
```

重复事件、被盘点覆盖的迟到事件都不会重复扣减；已被盘点吸收的订单
之后再次修订，仍按其渠道历史上的上一状态计算差额。

### 测试

```bash
python3 -m unittest discover -s tests -v
```

- `tests/test_basic.py`：验收场景与逐条业务规则。
- `tests/test_reference.py`：随机乱序/重复/盘点交错输入，与按渠道序号
  完整重放的独立参考实现对照。
- `tests/test_performance.py`：200 万事件 + 1 万盘点 + 10 万查询
  （本机约 5 秒，预算 30 秒），并校验滞留与未吸收事件规模上界。
- `tests/test_memory.py`：tracemalloc 校验模块峰值内存（约 17 MiB，
  预算 128 MiB）。
