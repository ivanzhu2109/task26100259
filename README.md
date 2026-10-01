# task26100259

内存中的采购三方匹配组件（纯 Python 标准库，无第三方依赖）。

## 原理

每张发票构成一个二分图最小费用最大流问题：源点 → 发票行（容量=需求量，费用 0）
→ 合格 PO 行（容量=∞，费用=单价差绝对值）→ 汇点（容量=PO 剩余容量，费用 0）。
采用连续最短路（Dijkstra + 势函数）求解，数量全部承载在聚合弧上，
不按件创建对象或迭代。合格性、容量（`min(验收数, ⌊订购数×105/100⌋)`）、
2% 价格带与成本全部使用整数运算。失败时取残余网络中从源点可达的发票行
集合 S，返回 S、其全部合格候选并集 N(S)（含零容量候选）以及缺口
`需求(S) − 剩余容量(N(S)) > 0`，且失败调用不改动任何内部状态。

## 用法

```python
from procurement_matcher import POLine, InvoiceLine, ThreeWayMatcher, MatchSuccess

matcher = ThreeWayMatcher([
    POLine("PO-1", ordered_qty=100, accepted_qty=110, unit_price=1000, tax_rate=200),
])

result = matcher.match([
    InvoiceLine("INV-1", qty=60, unit_price=1010, tax_rate=200,
                candidate_po_ids=frozenset({"PO-1"})),
])

if isinstance(result, MatchSuccess):
    print(result.allocations, result.total_price_diff)
else:
    print(result.deficit_invoice_ids, result.candidate_po_ids, result.deficit)

snap = matcher.snapshot()  # 剩余容量 + 历史分配记录（返回值与内部状态隔离）
```

## 测试

```sh
python3 -m unittest test_procurement_matcher -v
```

覆盖：改派必要性、多完整解取最小总价差、小规模随机实例对独立穷举核对、
瓶颈失败证据逐项验证与失败前后状态一致、连续发票容量不复用与 5% 容差终身制、
1000 PO 行 × 1000 发票行 × 20000 候选关系（单行 1e9 量级）对手工可算最优成本。
