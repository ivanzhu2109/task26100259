import random
import unittest

from procurement_matcher import (
    Allocation,
    InvoiceLine,
    MatchFailure,
    MatchSuccess,
    POLine,
    Snapshot,
    ThreeWayMatcher,
)


def po(pid, qty, price=1000, tax=0, ordered=None):
    return POLine(pid, ordered if ordered is not None else qty, qty, price, tax)


def inv(lid, qty, price=1000, tax=0, cands=()):
    return InvoiceLine(lid, qty, price, tax, frozenset(cands))


def alloc_map(result):
    out = {}
    for a in result.allocations:
        out[(a.invoice_id, a.po_id)] = out.get((a.invoice_id, a.po_id), 0) + a.qty
    return out


class TestOptimality(unittest.TestCase):
    def test_early_assignment_must_be_reassigned(self):
        # Greedy L1->A (first candidate) makes L2 impossible; only the
        # reassigned plan L1->B, L2->A is complete.
        m = ThreeWayMatcher([po("A", 5), po("B", 5)])
        r = m.match([
            inv("L1", 5, 1010, cands={"A", "B"}),
            inv("L2", 5, 1005, cands={"A"}),
        ])
        self.assertIsInstance(r, MatchSuccess)
        self.assertEqual(alloc_map(r), {("L1", "B"): 5, ("L2", "A"): 5})

    def test_min_cost_among_complete_solutions(self):
        # A cap 10 cost 10/unit for L1; B cap 5 cost 1/unit for L1.
        # Complete plans: L1->A (75 total) vs L1->B (30 total). Must pick 30.
        m = ThreeWayMatcher([po("A", 10, price=1000), po("B", 5, price=1009)])
        r = m.match([
            inv("L1", 5, 1010, cands={"A", "B"}),  # cost 10 on A, 1 on B
            inv("L2", 5, 1005, cands={"A"}),       # cost 5 on A only
        ])
        self.assertIsInstance(r, MatchSuccess)
        self.assertEqual(r.total_price_diff, 30)
        self.assertEqual(alloc_map(r), {("L1", "B"): 5, ("L2", "A"): 5})


class TestEligibility(unittest.TestCase):
    def setUp(self):
        self.m = ThreeWayMatcher([
            po("P", 10, price=1000, tax=200),
            po("Q", 10, price=1000, tax=300),
        ])

    def test_price_band_boundary_inclusive(self):
        # 2% of 1000 = 20 cents; diff 20 passes, diff 21 fails.
        r = self.m.match([inv("I", 3, price=1020, tax=200, cands={"P"})])
        self.assertIsInstance(r, MatchSuccess)
        r2 = self.m.match([inv("J", 3, price=1021, tax=200, cands={"P"})])
        self.assertIsInstance(r2, MatchFailure)

    def test_lower_boundary_inclusive(self):
        r = self.m.match([inv("I", 3, price=980, tax=200, cands={"P"})])
        self.assertIsInstance(r, MatchSuccess)

    def test_tax_mismatch_excluded(self):
        r = self.m.match([inv("I", 3, price=1000, tax=300, cands={"P"})])
        self.assertIsInstance(r, MatchFailure)
        self.assertEqual(r.candidate_po_ids, frozenset())

    def test_not_in_candidates_excluded(self):
        r = self.m.match([inv("I", 3, price=1000, tax=200, cands={"Q"})])
        self.assertIsInstance(r, MatchFailure)  # Q has tax 300


class TestFailureEvidence(unittest.TestCase):
    def test_bottleneck_despite_enough_global_capacity(self):
        # Global capacity 5+5=10 >= demand 8, but both lines only reach A.
        m = ThreeWayMatcher([po("A", 5), po("B", 5, tax=1)])
        before = m.snapshot()
        r = m.match([
            inv("X", 4, cands={"A"}),
            inv("Y", 4, cands={"A"}),
        ])
        self.assertIsInstance(r, MatchFailure)
        self.assertEqual(r.deficit_invoice_ids, frozenset({"X", "Y"}))
        self.assertEqual(r.candidate_po_ids, frozenset({"A"}))
        self.assertEqual(r.deficit, 8 - 5)
        self.assertGreater(r.deficit, 0)
        # State fully unchanged after failure.
        after = m.snapshot()
        self.assertEqual(before.remaining_capacity, after.remaining_capacity)
        self.assertEqual(before.records, after.records)

    def test_zero_capacity_candidate_included_in_witness(self):
        # Z is an eligible candidate of X but has zero capacity; it must
        # still appear in N(S).
        m = ThreeWayMatcher([po("A", 2), po("Z", 0)])
        r = m.match([inv("X", 5, cands={"A", "Z"})])
        self.assertIsInstance(r, MatchFailure)
        self.assertEqual(r.candidate_po_ids, frozenset({"A", "Z"}))
        self.assertEqual(r.deficit, 5 - 2)

    def test_deficit_verifiable_from_inputs_and_snapshot(self):
        m = ThreeWayMatcher([po("A", 3), po("B", 4), po("C", 100, tax=9)])
        invoice = [
            inv("X", 5, cands={"A", "B"}),
            inv("Y", 4, cands={"B"}),
            inv("Z", 2, cands={"A"}),
        ]
        snap = m.snapshot()
        r = m.match(invoice)
        self.assertIsInstance(r, MatchFailure)
        # Re-verify evidence using only inputs + pre-call snapshot.
        lines = {l.line_id: l for l in invoice}
        demand = sum(lines[i].qty for i in r.deficit_invoice_ids)
        cap = sum(snap.remaining_capacity[p] for p in r.candidate_po_ids)
        self.assertEqual(r.deficit, demand - cap)
        self.assertGreater(r.deficit, 0)
        self.assertEqual(r.deficit_invoice_ids, frozenset({"X", "Y", "Z"}))
        self.assertEqual(r.candidate_po_ids, frozenset({"A", "B"}))


class TestSequentialInvoices(unittest.TestCase):
    def test_capacity_not_reused_and_tolerance_not_regranted(self):
        # ordered 100, accepted 110 -> lifetime capacity min(110, 105) = 105.
        m = ThreeWayMatcher([po("A", 110, ordered=100)])
        self.assertEqual(m.snapshot().remaining_capacity["A"], 105)
        r1 = m.match([inv("I1", 60, cands={"A"})])
        self.assertIsInstance(r1, MatchSuccess)
        r2 = m.match([inv("I2", 45, cands={"A"})])
        self.assertIsInstance(r2, MatchSuccess)
        # 105 used up; the 5% tolerance is lifetime, not per-invoice.
        r3 = m.match([inv("I3", 1, cands={"A"})])
        self.assertIsInstance(r3, MatchFailure)
        self.assertEqual(r3.deficit, 1)
        self.assertEqual(m.snapshot().remaining_capacity["A"], 0)
        # Records accumulate; capacities of both invoices stayed occupied.
        self.assertEqual(len(m.snapshot().records), 2)

    def test_unaccepted_qty_never_matchable(self):
        # ordered 100, accepted 40 -> capacity 40, not 100.
        m = ThreeWayMatcher([po("A", 40, ordered=100)])
        r = m.match([inv("I", 41, cands={"A"})])
        self.assertIsInstance(r, MatchFailure)
        self.assertEqual(r.deficit, 1)


class TestSnapshotIsolation(unittest.TestCase):
    def test_mutating_snapshot_does_not_affect_matcher(self):
        m = ThreeWayMatcher([po("A", 10), po("B", 10)])
        m.match([inv("I", 4, cands={"A"})])
        snap = m.snapshot()
        snap.remaining_capacity["A"] = 0
        snap.remaining_capacity["B"] = 0
        snap.remaining_capacity["GHOST"] = 999
        # Records are immutable tuples of frozen dataclasses.
        self.assertIsInstance(snap.records, tuple)
        self.assertIsInstance(snap.records[0], tuple)
        r = m.match([inv("J", 6, cands={"A"})])
        self.assertIsInstance(r, MatchSuccess)  # A still has 6 left
        self.assertEqual(m.snapshot().remaining_capacity["A"], 0)
        self.assertNotIn("GHOST", m.snapshot().remaining_capacity)


def eligible_ids(inv_line, po_map):
    """Straightforward spec restatement used as the brute-force oracle."""
    out = []
    for pid in inv_line.candidate_po_ids:
        p = po_map[pid]
        if p.tax_rate != inv_line.tax_rate:
            continue
        if abs(inv_line.unit_price - p.unit_price) * 100 > p.unit_price * 2:
            continue
        out.append(pid)
    return out


def brute_force(lines, po_map, capacity):
    """Exhaustively enumerate every complete allocation; return min cost or None."""
    elig = [eligible_ids(l, po_map) for l in lines]
    remaining = dict(capacity)
    best = [None]

    def rec(i, cost):
        if best[0] is not None and cost >= best[0]:
            return
        if i == len(lines):
            best[0] = cost
            return
        line = lines[i]

        def distribute(j, left, c):
            if j == len(elig[i]):
                if left == 0:
                    rec(i + 1, c)
                return
            pid = elig[i][j]
            unit = abs(line.unit_price - po_map[pid].unit_price)
            for take in range(min(left, remaining[pid]) + 1):
                remaining[pid] -= take
                distribute(j + 1, left - take, c + take * unit)
                remaining[pid] += take

        distribute(0, line.qty, cost)

    rec(0, 0)
    return best[0]


class TestRandomBruteForce(unittest.TestCase):
    def test_random_small_instances_against_exhaustive_search(self):
        rng = random.Random(20261002)
        for trial in range(300):
            n_po = rng.randint(1, 3)
            po_lines = []
            for j in range(n_po):
                qty = rng.randint(0, 4)
                po_lines.append(POLine(
                    f"P{j}", qty, qty,
                    unit_price=rng.choice([100, 100, 102, 130]),
                    tax_rate=rng.choice([0, 0, 1]),
                ))
            po_map = {p.line_id: p for p in po_lines}
            capacity = {p.line_id: p.accepted_qty for p in po_lines}
            n_inv = rng.randint(1, 3)
            lines = []
            for i in range(n_inv):
                cands = frozenset(
                    pid for pid in po_map if rng.random() < 0.7
                )
                lines.append(InvoiceLine(
                    f"I{i}", rng.randint(0, 4),
                    unit_price=rng.choice([100, 101, 103, 140]),
                    tax_rate=rng.choice([0, 0, 1]),
                    candidate_po_ids=cands,
                ))
            matcher = ThreeWayMatcher(po_lines)
            before = matcher.snapshot()
            result = matcher.match(lines)
            expected = brute_force(lines, po_map, capacity)
            if expected is None:
                self.assertIsInstance(result, MatchFailure, (trial, lines))
                self.assertTrue(result.deficit_invoice_ids)
                self.assertGreater(result.deficit, 0)
                dem = sum(l.qty for l in lines
                          if l.line_id in result.deficit_invoice_ids)
                cap = sum(capacity[p] for p in result.candidate_po_ids)
                self.assertEqual(result.deficit, dem - cap)
                union = set()
                for l in lines:
                    if l.line_id in result.deficit_invoice_ids:
                        union |= set(eligible_ids(l, po_map))
                self.assertEqual(result.candidate_po_ids, frozenset(union))
                after = matcher.snapshot()
                self.assertEqual(before.remaining_capacity,
                                 after.remaining_capacity)
                self.assertEqual(before.records, after.records)
            else:
                self.assertIsInstance(result, MatchSuccess, (trial, lines))
                self.assertEqual(result.total_price_diff, expected, (trial, lines))
                per_inv = {}
                per_po = {}
                cost = 0
                for a in result.allocations:
                    self.assertGreater(a.qty, 0)
                    line = next(l for l in lines if l.line_id == a.invoice_id)
                    self.assertIn(a.po_id, eligible_ids(line, po_map))
                    per_inv[a.invoice_id] = per_inv.get(a.invoice_id, 0) + a.qty
                    per_po[a.po_id] = per_po.get(a.po_id, 0) + a.qty
                    cost += a.qty * abs(line.unit_price
                                        - po_map[a.po_id].unit_price)
                for l in lines:
                    self.assertEqual(per_inv.get(l.line_id, 0), l.qty)
                for pid, used in per_po.items():
                    self.assertLessEqual(used, capacity[pid])
                self.assertEqual(cost, result.total_price_diff)
                after = matcher.snapshot()
                for pid in capacity:
                    self.assertEqual(after.remaining_capacity[pid],
                                     capacity[pid] - per_po.get(pid, 0))


class TestLargeScale(unittest.TestCase):
    """1000 PO lines, 1000 invoice lines, 20000 candidate relations,
    single-line quantities around 1e9, with a hand-computable optimum.

    500 independent groups g (tax rate g isolates them). Per group:
      PO A_g: capacity 1.5e9, price P_g       (cost 0 for I1, 4 for I2)
      PO B_g: capacity 1.0e9, price P_g + 5   (cost 5 for I1, 1 for I2)
      I1: demand 1e9, price P_g
      I2: demand 1e9, price P_g + 4
    Optimum per group: I1->A 1e9 (cost 0), I2->B 1e9 (cost 1) => 1e9.
    Any other complete plan costs strictly more, so the expected minimum
    total is 500 * 1e9 = 500_000_000_000.
    """

    GROUPS = 500
    Q = 10 ** 9
    DUMMIES_PER_INVOICE = 18

    def _build(self):
        po_lines = []
        for g in range(self.GROUPS):
            price = 1000 + g
            po_lines.append(POLine(f"A{g}", 15 * self.Q // 10,
                                   15 * self.Q // 10, price, g))
            po_lines.append(POLine(f"B{g}", self.Q, self.Q, price + 5, g))
        po_map = {p.line_id: p for p in po_lines}
        rng = random.Random(99)
        all_po_ids = list(po_map)
        lines = []
        for g in range(self.GROUPS):
            price = 1000 + g
            own = {f"A{g}", f"B{g}"}
            others = [pid for pid in all_po_ids
                      if po_map[pid].tax_rate != g]
            for k, p in enumerate((price, price + 4)):
                dummies = set()
                while len(dummies) < self.DUMMIES_PER_INVOICE:
                    dummies.add(others[rng.randrange(len(others))])
                lines.append(InvoiceLine(
                    f"I{g}_{k}", self.Q, p, g, own | dummies,
                ))
        return po_lines, po_map, lines

    def test_large_instance_matches_hand_computed_optimum(self):
        po_lines, po_map, lines = self._build()
        n_relations = sum(len(l.candidate_po_ids) for l in lines)
        self.assertEqual(len(po_lines), 1000)
        self.assertEqual(len(lines), 1000)
        self.assertEqual(n_relations, 20000)
        # Dummies are all tax-mismatched: only the 2 useful edges survive.
        for l in lines:
            self.assertEqual(len(eligible_ids(l, po_map)), 2)

        matcher = ThreeWayMatcher(po_lines)
        result = matcher.match(lines)
        self.assertIsInstance(result, MatchSuccess)
        self.assertEqual(result.total_price_diff,
                         self.GROUPS * self.Q)  # 500_000_000_000

        # Verify the actual allocation against demands and capacities.
        per_inv = {}
        per_po = {}
        cost = 0
        inv_by_id = {l.line_id: l for l in lines}
        for a in result.allocations:
            self.assertGreater(a.qty, 0)
            line = inv_by_id[a.invoice_id]
            self.assertIn(a.po_id, eligible_ids(line, po_map))
            per_inv[a.invoice_id] = per_inv.get(a.invoice_id, 0) + a.qty
            per_po[a.po_id] = per_po.get(a.po_id, 0) + a.qty
            cost += a.qty * abs(line.unit_price - po_map[a.po_id].unit_price)
        for l in lines:
            self.assertEqual(per_inv.get(l.line_id, 0), l.qty)
        capacity = {p.line_id: min(p.accepted_qty, p.ordered_qty * 105 // 100)
                    for p in po_lines}
        for pid, used in per_po.items():
            self.assertLessEqual(used, capacity[pid])
        self.assertEqual(cost, result.total_price_diff)

        # Spot-check remaining capacity: every B fully used, A keeps 0.5e9.
        snap = matcher.snapshot()
        for g in range(self.GROUPS):
            self.assertEqual(snap.remaining_capacity[f"B{g}"], 0)
            self.assertEqual(snap.remaining_capacity[f"A{g}"],
                             15 * self.Q // 10 - self.Q)


if __name__ == "__main__":
    unittest.main()
