"""Deterministic core tests for ThreeWayMatcher."""

import unittest

from three_way_match import (
    InfeasibleMatchError,
    InvoiceLine,
    POLine,
    ThreeWayMatcher,
)


def po(pid, ordered, accepted, price, tax=0):
    return POLine(pid, ordered, accepted, price, tax)


def inv(iid, qty, price, candidates, tax=0):
    return InvoiceLine(iid, qty, price, tax, frozenset(candidates))


def capacity_of(line):
    return min(line.accepted_qty, line.ordered_qty * 105 // 100)


class CapacityRulesTest(unittest.TestCase):
    def test_capacity_is_min_of_accepted_and_105_percent_of_ordered(self):
        lines = [
            po("a", 100, 100, 100),   # min(100, 105) = 100
            po("b", 100, 105, 100),   # min(105, 105) = 105
            po("c", 100, 200, 100),   # min(200, 105) = 105, tolerance capped
            po("d", 100, 40, 100),    # unaccepted qty cannot match: 40
            po("e", 101, 101, 100),   # min(101, 106) = 101
            po("f", 3, 3, 100),       # min(3, 3) = 3 (3*105//100 = 3)
        ]
        m = ThreeWayMatcher(lines)
        snap = m.snapshot()
        self.assertEqual(snap.remaining_capacity,
                         {"a": 100, "b": 105, "c": 105, "d": 40, "e": 101, "f": 3})

    def test_tolerance_granted_once_not_per_invoice(self):
        m = ThreeWayMatcher([po("p", 100, 200, 100)])  # capacity 105
        r1 = m.match([inv("i1", 100, 100, {"p"})])
        self.assertEqual(r1.total_price_diff, 0)
        r2 = m.match([inv("i2", 5, 100, {"p"})])
        self.assertEqual(m.snapshot().remaining_capacity["p"], 0)
        # The 5% tolerance is a lifetime budget: a third invoice gets nothing.
        with self.assertRaises(InfeasibleMatchError) as ctx:
            m.match([inv("i3", 1, 100, {"p"})])
        self.assertEqual(ctx.exception.evidence.deficit, 1)
        self.assertEqual(m.snapshot().remaining_capacity["p"], 0)

    def test_capacity_not_reused_across_invoices(self):
        m = ThreeWayMatcher([po("p", 10, 10, 100), po("q", 10, 10, 100)])
        m.match([inv("i1", 7, 100, {"p", "q"})])
        m.match([inv("i2", 7, 100, {"p", "q"})])
        remaining = m.snapshot().remaining_capacity
        self.assertEqual(sum(remaining.values()), 6)
        with self.assertRaises(InfeasibleMatchError):
            m.match([inv("i3", 7, 100, {"p", "q"})])
        self.assertEqual(m.snapshot().remaining_capacity, remaining)


class EligibilityTest(unittest.TestCase):
    def setUp(self):
        self.m = ThreeWayMatcher([
            po("base", 10, 10, 100, tax=0),
            po("tax", 10, 10, 100, tax=250),
            po("far", 10, 10, 200, tax=0),
        ])

    def test_tax_rate_must_match_exactly(self):
        with self.assertRaises(InfeasibleMatchError) as ctx:
            self.m.match([inv("i", 1, 100, {"tax"}, tax=0)])
        self.assertEqual(ctx.exception.evidence.candidate_po_ids, frozenset())

    def test_two_percent_price_boundary_is_inclusive(self):
        # |102 - 100| * 100 == 100 * 2 -> eligible.
        r = self.m.match([inv("lo", 2, 102, {"base"})])
        self.assertEqual(r.total_price_diff, 2 * 2)
        # |97 - 100| * 100 > 100 * 2 -> not eligible.
        with self.assertRaises(InfeasibleMatchError):
            self.m.match([inv("hi", 1, 97, {"base"})])
        # Boundary against the other PO: |196 - 200| * 100 == 200 * 2.
        r = self.m.match([inv("far", 3, 196, {"far"})])
        self.assertEqual(r.total_price_diff, 3 * 4)

    def test_unlisted_po_is_never_a_candidate(self):
        with self.assertRaises(InfeasibleMatchError) as ctx:
            self.m.match([inv("i", 1, 100, {"far"})])
        # 'far' is price-ineligible for price 100, 'base' was not listed.
        self.assertEqual(ctx.exception.evidence.candidate_po_ids, frozenset())


class OptimalityTest(unittest.TestCase):
    def test_early_allocation_must_be_rerouted_for_full_match(self):
        # L1 prefers P2 (cost 0 vs 2) but only L1->P1, L2->P2 is feasible.
        m = ThreeWayMatcher([po("P1", 5, 5, 100), po("P2", 5, 5, 102)])
        r = m.match([
            inv("L1", 5, 102, {"P1", "P2"}),  # costs: P1=2, P2=0
            inv("L2", 5, 104, {"P1", "P2"}),  # only P2 eligible (cost 2)
        ])
        self.assertEqual(r.total_price_diff, 5 * 2 + 5 * 2)
        got = {(a.invoice_line_id, a.po_line_id): a.qty for a in r.allocations}
        self.assertEqual(got, {("L1", "P1"): 5, ("L2", "P2"): 5})

    def test_multiple_complete_solutions_min_cost_wins(self):
        # Demand equals capacity on both POs, so every feasible flow uses all
        # capacity; costs differ and the optimum is 5 (L1->P1, L2->P2).
        m = ThreeWayMatcher([po("P1", 5, 5, 100), po("P2", 5, 5, 102)])
        r = m.match([
            inv("L1", 5, 100, {"P1", "P2"}),  # costs: P1=0, P2=2
            inv("L2", 5, 101, {"P1", "P2"}),  # costs: P1=1, P2=1
        ])
        self.assertEqual(r.total_price_diff, 5)
        got = {(a.invoice_line_id, a.po_line_id): a.qty for a in r.allocations}
        self.assertEqual(got, {("L1", "P1"): 5, ("L2", "P2"): 5})

    def test_split_across_candidates(self):
        m = ThreeWayMatcher([po("P1", 3, 3, 100), po("P2", 4, 4, 100)])
        r = m.match([inv("L", 6, 100, {"P1", "P2"})])
        self.assertEqual(sum(a.qty for a in r.allocations), 6)
        remaining = m.snapshot().remaining_capacity
        self.assertEqual(sum(remaining.values()), 1)


class EvidenceTest(unittest.TestCase):
    def test_local_bottleneck_despite_globally_sufficient_capacity(self):
        m = ThreeWayMatcher([
            po("small", 5, 5, 100),
            po("big", 100, 100, 100),
            po("empty", 10, 0, 100),  # zero capacity, still eligible
        ])
        before = m.snapshot()
        lines = [
            inv("A", 10, 100, {"small", "empty"}),
            inv("B", 10, 100, {"small", "big"}),
        ]
        with self.assertRaises(InfeasibleMatchError) as ctx:
            m.match(lines)
        ev = ctx.exception.evidence
        # S is exactly the bottlenecked set; N(S) includes the zero-capacity PO.
        self.assertEqual(ev.invoice_line_ids, frozenset({"A"}))
        self.assertEqual(ev.candidate_po_ids, frozenset({"small", "empty"}))
        self.assertEqual(ev.deficit, 10 - (5 + 0))
        self.assertGreater(ev.deficit, 0)
        # Failure leaves the state completely unchanged.
        after = m.snapshot()
        self.assertEqual(before.remaining_capacity, after.remaining_capacity)
        self.assertEqual(before.allocations, after.allocations)

    def test_evidence_verifiable_from_inputs_and_prior_snapshot(self):
        m = ThreeWayMatcher([po("p", 4, 4, 100), po("q", 6, 6, 100)])
        m.match([inv("used", 1, 100, {"p"})])  # consume 1 of p
        prior = m.snapshot()
        lines = [inv("x", 2, 100, {"p"}), inv("y", 5, 100, {"p", "q"})]
        try:
            m.match(lines)
        except InfeasibleMatchError as err:
            self.fail("feasible invoice rejected")
        # Now force infeasibility and verify the evidence purely from inputs.
        lines2 = [inv("z", 8, 100, {"p", "q"})]
        prior = m.snapshot()
        with self.assertRaises(InfeasibleMatchError) as ctx:
            m.match(lines2)
        ev = ctx.exception.evidence
        demand_s = sum(l.qty for l in lines2 if l.id in ev.invoice_line_ids)
        cap_n = sum(prior.remaining_capacity[p] for p in ev.candidate_po_ids)
        self.assertEqual(ev.deficit, demand_s - cap_n)
        self.assertGreater(ev.deficit, 0)


class SnapshotIsolationTest(unittest.TestCase):
    def test_mutating_snapshot_does_not_corrupt_state(self):
        m = ThreeWayMatcher([po("p", 10, 10, 100)])
        m.match([inv("i", 4, 100, {"p"})])
        snap = m.snapshot()
        snap.remaining_capacity["p"] = 999
        snap.remaining_capacity.clear()
        hacked = list(snap.allocations)
        hacked.clear()
        fresh = m.snapshot()
        self.assertEqual(fresh.remaining_capacity, {"p": 6})
        self.assertEqual(len(fresh.allocations), 1)
        r = m.match([inv("j", 6, 100, {"p"})])
        self.assertEqual(sum(a.qty for a in r.allocations), 6)


if __name__ == "__main__":
    unittest.main()
