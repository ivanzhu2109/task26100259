"""In-memory three-way matching (invoice -> accepted PO lines).

Standard-library-only implementation. Feasibility and optimality are decided
by a min-cost max-flow over an aggregated bipartite graph (one node per
invoice line / PO line), never per unit of goods.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class POLine:
    line_id: str
    ordered_qty: int
    accepted_qty: int
    unit_price: int  # cents, positive
    tax_rate: int    # basis points


@dataclass(frozen=True)
class InvoiceLine:
    line_id: str
    qty: int
    unit_price: int
    tax_rate: int
    candidate_po_ids: frozenset


@dataclass(frozen=True)
class Allocation:
    invoice_id: str
    po_id: str
    qty: int


@dataclass(frozen=True)
class MatchSuccess:
    allocations: tuple
    total_price_diff: int


@dataclass(frozen=True)
class MatchFailure:
    deficit_invoice_ids: frozenset  # S
    candidate_po_ids: frozenset     # N(S), includes zero-capacity candidates
    deficit: int                    # demand(S) - remaining_capacity(N(S)) > 0


@dataclass(frozen=True)
class Snapshot:
    remaining_capacity: dict
    records: tuple  # tuple of tuples of Allocation, one per successful match


def _capacity(po: POLine) -> int:
    """Lifetime capacity: min(accepted, floor(ordered * 105 / 100))."""
    return min(po.accepted_qty, po.ordered_qty * 105 // 100)


def _eligible(inv: InvoiceLine, po: POLine) -> bool:
    """Candidate + same tax rate + |price diff| * 100 <= po price * 2."""
    if po.line_id not in inv.candidate_po_ids:
        return False
    if inv.tax_rate != po.tax_rate:
        return False
    return abs(inv.unit_price - po.unit_price) * 100 <= po.unit_price * 2


# ---------------------------------------------------------------------------
# Min-cost max-flow (successive shortest paths, Dijkstra + potentials).
# Edge costs are non-negative (absolute price differences), so zero initial
# potentials are valid. Quantities stay on aggregated arcs: no per-unit work.
# ---------------------------------------------------------------------------

_INF = 1 << 62


class _FlowNetwork:
    __slots__ = ("graph",)

    def __init__(self, num_nodes: int) -> None:
        self.graph: list[list[list]] = [[] for _ in range(num_nodes)]

    def add_edge(self, frm: int, to: int, cap: int, cost: int):
        fwd = [to, cap, cost, None]
        rev = [frm, 0, -cost, fwd]
        fwd[3] = rev
        self.graph[frm].append(fwd)
        self.graph[to].append(rev)
        return fwd

    def min_cost_flow(self, source: int, sink: int, max_flow: int):
        """Augment along cheapest paths until max_flow reached or impossible.

        Returns (flow, cost). Destructive: callers build a fresh network per
        invoice, so a failed match leaves matcher state untouched.
        """
        graph = self.graph
        n = len(graph)
        potential = [0] * n
        flow = 0
        cost = 0
        while flow < max_flow:
            dist = [_INF] * n
            dist[source] = 0
            prev_node = [-1] * n
            prev_edge = [None] * n
            heap = [(0, source)]
            while heap:
                d, v = heapq.heappop(heap)
                if d > dist[v]:
                    continue
                for edge in graph[v]:
                    if edge[1] <= 0:
                        continue
                    to = edge[0]
                    nd = d + edge[2] + potential[v] - potential[to]
                    if nd < dist[to]:
                        dist[to] = nd
                        prev_node[to] = v
                        prev_edge[to] = edge
                        heapq.heappush(heap, (nd, to))
            if dist[sink] == _INF:
                break
            for v in range(n):
                if dist[v] < _INF:
                    potential[v] += dist[v]
            add = max_flow - flow
            v = sink
            while v != source:
                add = min(add, prev_edge[v][1])
                v = prev_node[v]
            flow += add
            cost += add * potential[sink]  # potential[source] stays 0
            v = sink
            while v != source:
                edge = prev_edge[v]
                edge[1] -= add
                edge[3][1] += add
                v = prev_node[v]
        return flow, cost

    def reachable_from(self, source: int) -> list[bool]:
        seen = [False] * len(self.graph)
        stack = [source]
        seen[source] = True
        while stack:
            v = stack.pop()
            for edge in self.graph[v]:
                if edge[1] > 0 and not seen[edge[0]]:
                    seen[edge[0]] = True
                    stack.append(edge[0])
        return seen


class ThreeWayMatcher:
    def __init__(self, po_lines: Iterable[POLine]) -> None:
        self._po: dict[str, POLine] = {}
        self._remaining: dict[str, int] = {}
        for po in po_lines:
            if po.line_id in self._po:
                raise ValueError(f"duplicate PO line id: {po.line_id!r}")
            self._po[po.line_id] = po
            self._remaining[po.line_id] = _capacity(po)
        self._records: list[tuple[Allocation, ...]] = []

    def match(self, invoice: Iterable[InvoiceLine]):
        lines = list(invoice)
        ids = [ln.line_id for ln in lines]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate invoice line id")

        # Eligible candidates per line + deduplicated union of involved POs.
        eligible: list[list[POLine]] = []
        involved: dict[str, POLine] = {}
        for ln in lines:
            cands = [
                self._po[pid]
                for pid in sorted(ln.candidate_po_ids)
                if pid in self._po and _eligible(ln, self._po[pid])
            ]
            eligible.append(cands)
            for po in cands:
                involved[po.line_id] = po

        po_ids = sorted(involved)
        po_node = {pid: 1 + len(lines) + j for j, pid in enumerate(po_ids)}
        source = 0
        sink = 1 + len(lines) + len(po_ids)
        net = _FlowNetwork(sink + 1)

        total_demand = 0
        for i, ln in enumerate(lines):
            net.add_edge(source, 1 + i, ln.qty, 0)
            total_demand += ln.qty
        inv_edges: list[list[tuple[str, list]]] = []
        for i, ln in enumerate(lines):
            edges = []
            for po in eligible[i]:
                cost = abs(ln.unit_price - po.unit_price)
                fwd = net.add_edge(1 + i, po_node[po.line_id], _INF, cost)
                edges.append((po.line_id, fwd))
            inv_edges.append(edges)
        for pid in po_ids:
            net.add_edge(po_node[pid], sink, self._remaining[pid], 0)

        flow, cost = net.min_cost_flow(source, sink, total_demand)

        if flow < total_demand:
            return self._failure(lines, eligible, net, source)

        allocations: list[Allocation] = []
        used: dict[str, int] = {}
        for i, ln in enumerate(lines):
            for pid, fwd in inv_edges[i]:
                sent = fwd[3][1]  # reverse residual capacity == flow sent
                if sent > 0:
                    allocations.append(Allocation(ln.line_id, pid, sent))
                    used[pid] = used.get(pid, 0) + sent
        for pid, qty in used.items():
            self._remaining[pid] -= qty
        record = tuple(allocations)
        self._records.append(record)
        return MatchSuccess(allocations=record, total_price_diff=cost)

    def _failure(self, lines, eligible, net, source) -> MatchFailure:
        seen = net.reachable_from(source)
        s_idx = [i for i in range(len(lines)) if seen[1 + i]]
        # N(S): full eligible union of S, including zero-capacity candidates.
        n_ids = frozenset(po.line_id for i in s_idx for po in eligible[i])
        demand = sum(lines[i].qty for i in s_idx)
        capacity = sum(self._remaining[pid] for pid in n_ids)
        return MatchFailure(
            deficit_invoice_ids=frozenset(lines[i].line_id for i in s_idx),
            candidate_po_ids=n_ids,
            deficit=demand - capacity,
        )

    def snapshot(self) -> Snapshot:
        # Fresh dict + immutable tuples: callers cannot affect internal state.
        return Snapshot(
            remaining_capacity=dict(self._remaining),
            records=tuple(self._records),
        )
