"""In-memory invoice-to-PO three-way matcher.

Feasibility and optimality for each invoice are decided by an exact integer
min-cost max-flow over the whole invoice (never line-by-line greedy). On
infeasibility a Hall-style certificate (S, N(S), deficit) is derived from the
min cut of the same network.
"""

from __future__ import annotations

from typing import Iterable

from ._flow import MinCostFlow
from .models import (
    Allocation,
    InfeasibleEvidence,
    InfeasibleMatchError,
    InvoiceLine,
    MatchSuccess,
    POLine,
    Snapshot,
)


class ThreeWayMatcher:
    """Matches invoices against accepted quantities of one supplier's PO lines."""

    def __init__(self, po_lines: Iterable[POLine]) -> None:
        self._po_lines = {}
        self._remaining = {}
        for line in po_lines:
            if line.id in self._po_lines:
                raise ValueError(f"duplicate PO line id: {line.id!r}")
            capacity = min(line.accepted_qty, line.ordered_qty * 105 // 100)
            self._po_lines[line.id] = line
            self._remaining[line.id] = capacity
        self._history: list[Allocation] = []

    def _eligible_candidates(self, line: InvoiceLine) -> list[tuple[POLine, int]]:
        """Eligible PO lines for ``line`` with their per-unit price diff.

        Eligibility: listed as candidate, identical tax rate, and
        ``abs(price diff) * 100 <= po unit price * 2`` (2% boundary included).
        Zero-remaining-capacity PO lines stay eligible.
        """
        eligible = []
        for po_id in line.candidate_po_ids:
            po = self._po_lines[po_id]
            diff = line.unit_price - po.unit_price
            if diff < 0:
                diff = -diff
            if line.tax_rate == po.tax_rate and diff * 100 <= po.unit_price * 2:
                eligible.append((po, diff))
        return eligible

    def match(self, invoice_lines: Iterable[InvoiceLine]) -> MatchSuccess:
        """Match a whole invoice optimally or raise InfeasibleMatchError.

        On success the capacity is consumed and a MatchSuccess with the
        per-line allocations and the minimal total price diff is returned.
        On failure the matcher state is left completely unchanged.
        """
        lines = list(invoice_lines)
        num_lines = len(lines)
        po_ids = list(self._po_lines)
        po_index = {po_id: i for i, po_id in enumerate(po_ids)}
        num_pos = len(po_ids)

        source = 0
        line_node = [1 + i for i in range(num_lines)]
        po_node = [1 + num_lines + j for j in range(num_pos)]
        sink = 1 + num_lines + num_pos

        total_demand = sum(line.qty for line in lines)
        flow = MinCostFlow(sink + 1)
        for i, line in enumerate(lines):
            flow.add_edge(source, line_node[i], line.qty, 0)
        eligible_per_line = []
        assignment_edges = []
        for i, line in enumerate(lines):
            eligible = self._eligible_candidates(line)
            eligible_per_line.append(eligible)
            edges = []
            for po, diff in eligible:
                idx = flow.add_edge(
                    line_node[i], po_node[po_index[po.id]], total_demand, diff)
                edges.append((idx, po.id))
            assignment_edges.append(edges)
        for j, po_id in enumerate(po_ids):
            flow.add_edge(po_node[j], sink, self._remaining[po_id], 0)

        value, total_cost = flow.min_cost_max_flow(source, sink)

        if value < total_demand:
            evidence = self._build_evidence(
                flow, source, lines, line_node, eligible_per_line)
            raise InfeasibleMatchError(evidence)

        allocations = []
        for i, line in enumerate(lines):
            for idx, po_id in assignment_edges[i]:
                qty = flow.flow_on(line_node[i], idx)
                if qty > 0:
                    allocations.append(Allocation(line.id, po_id, qty))
        # Only mutate state once the full optimum is known.
        for alloc in allocations:
            self._remaining[alloc.po_line_id] -= alloc.qty
        self._history.extend(allocations)
        return MatchSuccess(tuple(allocations), total_cost)

    def _build_evidence(self, flow, source, lines, line_node,
                        eligible_per_line) -> InfeasibleEvidence:
        reachable = flow.reachable_from(source)
        s_ids = frozenset(
            lines[i].id for i in range(len(lines)) if reachable[line_node[i]])
        n_ids = set()
        for i, line in enumerate(lines):
            if reachable[line_node[i]]:
                for po, _diff in eligible_per_line[i]:
                    n_ids.add(po.id)
        deficit = (
            sum(line.qty for line in lines if line.id in s_ids)
            - sum(self._remaining[po_id] for po_id in n_ids)
        )
        return InfeasibleEvidence(s_ids, frozenset(n_ids), deficit)

    def snapshot(self) -> Snapshot:
        """Remaining capacities and history; safe for the caller to mutate."""
        return Snapshot(dict(self._remaining), tuple(self._history))
