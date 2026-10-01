"""Large-scale test: 1000 PO lines, 1000 invoice lines, 20000 candidate
relations, quantities around 1e9, with an independently computed optimum."""

import unittest

from three_way_match import InvoiceLine, POLine, ThreeWayMatcher

GROUPS = 100          # 100 groups x (10 POs + 10 invoices) = 1000 + 1000
SIZE = 10
TOTAL_CANDIDATES = 20000


def build_instance():
    """Build the instance plus the independently computed expected optimum.

    Even groups: staircase candidates (invoice k -> POs 0..k) with demand
    equal to capacity on every prefix, forcing a unique feasible flow.
    Odd groups: complete bipartite, per-unit cost depends only on the PO, so
    the optimum fills POs cheapest-first (computable by sorting).
    Candidate lists are padded with cross-group, price-ineligible POs up to
    exactly TOTAL_CANDIDATES relations.
    """
    pos = {}          # (g, j) -> POLine
    lines = {}        # (g, k) -> InvoiceLine
    expected_cost = 0
    forced = {}       # (g, k) -> {(g, j): qty} for even groups
    for g in range(GROUPS):
        base = 1_000_000 + g * 100_000
        if g % 2 == 0:
            caps = [10**9 + g * 1000 + j * 7 for j in range(SIZE)]
            for j in range(SIZE):
                pos[(g, j)] = POLine((g, j), caps[j], caps[j],
                                     base + j * 1000, 0)
            for k in range(SIZE):
                lines[(g, k)] = InvoiceLine(
                    (g, k), caps[k], base, 0,
                    frozenset((g, j) for j in range(k + 1)))
                forced[(g, k)] = {(g, k): caps[k]}
                expected_cost += caps[k] * k * 1000
        else:
            demands = [10**9 + g + i * 13 for i in range(SIZE)]
            caps = [10**9 + g + 100 + j * 11 for j in range(SIZE)]
            for j in range(SIZE):
                pos[(g, j)] = POLine((g, j), caps[j], caps[j],
                                     base + j * 500, 0)
            for k in range(SIZE):
                lines[(g, k)] = InvoiceLine(
                    (g, k), demands[k], base, 0,
                    frozenset((g, j) for j in range(SIZE)))
            # Cheapest-PO-first fill is optimal: cost depends only on the PO.
            remaining = sum(demands)
            for j in sorted(range(SIZE), key=lambda j: j * 500):
                use = min(caps[j], remaining)
                expected_cost += use * j * 500
                remaining -= use
            assert remaining == 0
    # Pad candidate lists with ineligible cross-group POs up to 20000.
    listed = sum(len(l.candidate_po_ids) for l in lines.values())
    padding = TOTAL_CANDIDATES - listed
    assert padding >= 0
    per_line, extra = divmod(padding, len(lines))
    new_lines = {}
    for idx, (key, line) in enumerate(sorted(lines.items())):
        g, k = key
        cands = set(line.candidate_po_ids)
        count = per_line + (1 if idx < extra else 0)
        for t in range(count):
            g2 = (g + 1 + t // 10) % GROUPS
            cands.add((g2, (t + k) % SIZE))
        new_lines[key] = InvoiceLine(line.id, line.qty, line.unit_price,
                                     line.tax_rate, frozenset(cands))
    return list(pos.values()), list(new_lines.values()), expected_cost, forced


class LargeScaleTest(unittest.TestCase):
    def test_thousand_by_thousand(self):
        pos, lines, expected_cost, forced = build_instance()
        self.assertEqual(len(pos), 1000)
        self.assertEqual(len(lines), 1000)
        self.assertEqual(
            sum(len(l.candidate_po_ids) for l in lines), TOTAL_CANDIDATES)
        self.assertGreater(max(l.qty for l in lines), 10**9)

        matcher = ThreeWayMatcher(pos)
        result = matcher.match(lines)

        # The headline check: optimum matches the independent computation.
        self.assertEqual(result.total_price_diff, expected_cost)

        # Structural verification of the returned allocation.
        po_by_id = {p.id: p for p in pos}
        line_by_id = {l.id: l for l in lines}
        per_line = {l.id: 0 for l in lines}
        per_po = {p.id: 0 for p in pos}
        recomputed = 0
        for a in result.allocations:
            line = line_by_id[a.invoice_line_id]
            po = po_by_id[a.po_line_id]
            self.assertIn(a.po_line_id, line.candidate_po_ids)
            self.assertEqual(line.tax_rate, po.tax_rate)
            diff = abs(line.unit_price - po.unit_price)
            self.assertLessEqual(diff * 100, po.unit_price * 2)
            self.assertGreater(a.qty, 0)
            per_line[a.invoice_line_id] += a.qty
            per_po[a.po_line_id] += a.qty
            recomputed += a.qty * diff
        self.assertEqual(recomputed, result.total_price_diff)
        for l in lines:
            self.assertEqual(per_line[l.id], l.qty)
        for p in pos:
            cap = min(p.accepted_qty, p.ordered_qty * 105 // 100)
            self.assertLessEqual(per_po[p.id], cap)
            self.assertEqual(
                matcher.snapshot().remaining_capacity[p.id],
                cap - per_po[p.id])

        # Even groups have a unique feasible flow: check it allocation by
        # allocation, not just the aggregate cost.
        got = {}
        for a in result.allocations:
            got.setdefault(a.invoice_line_id, {})[a.po_line_id] = a.qty
        for key, want in forced.items():
            self.assertEqual(got[key], want)


if __name__ == "__main__":
    unittest.main()
