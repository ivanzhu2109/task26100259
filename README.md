# task26100259

内存中的采购三方匹配组件（纯 Python 标准库，无第三方依赖）：把一张发票
分配到已验收的采购单行，保证整张发票的可行性与最小总价差，失败时给出
可验证的 Hall 式不可行证据。

## 匹配规则

- PO 行可用容量 = `min(累计验收合格数量, 订购数量 * 105 // 100)`，5% 容差
  是 PO 行终身累计额度，不按发票重复授予；未验收数量不参与匹配。
- 发票行的合格候选：在候选集合内、税率完全相同、且
  `|单价差| * 100 <= PO锁价 * 2`（2% 边界含等号）。
- 发票行可拆分到多个候选，PO 行可供多张发票使用；每条发票行必须足量
  分配，PO 行累计占用不得超过容量。
- 总价差 = `Σ 分配数量 * |单价差|`，只优化当前发票，历史分配不可重排。
- 容差、容量、成本全部使用整数运算，无浮点。

可行性及最优性由整张发票上的精确整数最小费用最大流判定（原始-对偶 +
Johnson 势能），失败证据来自同一网络的最小割：非空发票行集合 S、其全部
合格候选并集 N(S)（含零容量候选）、以及正缺口
`demand(S) - remaining_capacity(N(S))`。调用方可仅凭输入与调用前快照验
证证据。

## 用法

```python
from three_way_match import (
    ThreeWayMatcher, POLine, InvoiceLine, InfeasibleMatchError,
)

matcher = ThreeWayMatcher([
    POLine(id="PO-1", ordered_qty=100, accepted_qty=105,
           unit_price=5000, tax_rate=1300),
])

try:
    result = matcher.match([
        InvoiceLine(id="INV-1", qty=60, unit_price=5050, tax_rate=1300,
                    candidate_po_ids=frozenset({"PO-1"})),
    ])
except InfeasibleMatchError as err:
    ev = err.evidence          # S、N(S)、deficit；matcher 状态未变
    print(ev.invoice_line_ids, ev.candidate_po_ids, ev.deficit)
else:
    print(result.allocations)       # 每条发票行到各 PO 行的分配数量
    print(result.total_price_diff)  # 最小总价差（整数分）

snap = matcher.snapshot()      # 剩余容量 + 历史分配；返回值可随意修改
```

## 运行测试

```sh
python3 -m unittest discover -s tests
```

测试覆盖：当前发票内需改派才能完整匹配与多解不同成本取最优；300 个随机
小实例与独立穷举核对可行性/最优成本/证据；局部瓶颈证据逐项验证与失败前
后状态一致、连续发票容量不重复使用、5% 容差不重复授予；1000 PO 行 ×
1000 发票行 × 20000 候选关系、数量 10^9 量级且预期最优成本可独立核对的
大规模用例。
