"""Random small instances cross-checked against an independent brute force."""

import random
import unittest

from three_way_match import (
    InfeasibleMatchError,
    InvoiceLine,
    POLine,
    ThreeWayMatcher,
)


def capacity_of(line):
    return min(line.accepted_qty, line.ordered_qty * 105 // 100)


def eligible(line, po):
    diff = abs(line.unit_price - po.unit_price)
    return (line.tax_rate == po.tax_rate
            and diff * 100 <= po.unit_price * 2)


def brute_force_min_cost(lines, pos, caps):
    """Exact min cost by enumerating all quantity splits; None if infeasible."""
    elig = [[(j, abs(l.unit_price - p.unit_price))
             for j, p in enumerate(pos) if p.id in l.candidate_po_ids
             and eligible(l, p)]
            for l in lines]
    used = [0] * len(pos)
    best = [None]

    def assign_line(i, cost):
        if best[0] is not None and cost >= best[0]:
            return
        if i == len(lines):
            best[0] = cost
            return
        qty = lines[i].qty
        options = elig[i]

        def split(k, remaining, c):
            if k == len(options):
                if remaining == 0:
                    assign_line(i + 1, c)
                return
            j, unit = options[k]
            for q in range(min(remaining, caps[j] - used[j]) + 1):
                used[j] += q
                split(k + 1, remaining - q, c + q * unit)
                used[j] -= q

        split(0, qty, cost)

    assign_line(0, 0)
    return best[0]


class RandomizedCrossCheckTest(unittest.TestCase):
    def test_against_brute_force(self):
        rng = random.Random(20261002)
        num_infeasible = 0
        for trial in range(300):
            num_pos = rng.randint(1, 4)
            pos = []
            for j in range(num_pos):
                ordered = rng.randint(0, 6)
                accepted = rng.randint(0, ordered + 2)
                pos.append(POLine(f"p{j}", ordered, accepted,
                                  rng.choice([100, 101, 102]),
                                  rng.choice([0, 0, 0, 250])))
            caps = [capacity_of(p) for p in pos]
            lines = []
            for i in range(rng.randint(1, 4)):
                cands = {p.id for p in pos if rng.random() < 0.7}
                lines.append(InvoiceLine(
                    f"i{i}", rng.randint(0, 5), rng.choice([100, 101, 102]),
                    rng.choice([0, 0, 0, 250]), frozenset(cands)))
            expected = brute_force_min_cost(lines, pos, caps)
            matcher = ThreeWayMatcher(pos)
            if expected is None:
                num_infeasible += 1
                before = matcher.snapshot()
                with self.assertRaises(InfeasibleMatchError,
                                       msg=f"trial {trial}") as ctx:
                    matcher.match(lines)
                self._check_evidence(ctx.exception.evidence, lines, pos,
                                     before.remaining_capacity, trial)
                after = matcher.snapshot()
                self.assertEqual(before.remaining_capacity,
                                 after.remaining_capacity)
                self.assertEqual(before.allocations, after.allocations)
            else:
                try:
                    result = matcher.match(lines)
                except InfeasibleMatchError:
                    self.fail(f"trial {trial}: feasible instance rejected")
                self.assertEqual(result.total_price_diff, expected,
                                 f"trial {trial}")
                self._check_allocations(result, lines, pos, caps, trial)
        # Both branches must be exercised by the fixed seed.
        self.assertGreater(num_infeasible, 20)
        self.assertLess(num_infeasible, 280)

    def _check_evidence(self, ev, lines, pos, remaining, trial):
        pos_by_id = {p.id: p for p in pos}
        self.assertTrue(ev.invoice_line_ids, f"trial {trial}: empty S")
        # N(S) must equal the union of eligible candidates of S, including
        # zero-capacity ones.
        union = set()
        for l in lines:
            if l.id in ev.invoice_line_ids:
                for pid in l.candidate_po_ids:
                    if eligible(l, pos_by_id[pid]):
                        union.add(pid)
        self.assertEqual(ev.candidate_po_ids, frozenset(union),
                         f"trial {trial}")
        demand = sum(l.qty for l in lines if l.id in ev.invoice_line_ids)
        cap = sum(remaining[pid] for pid in ev.candidate_po_ids)
        self.assertEqual(ev.deficit, demand - cap, f"trial {trial}")
        self.assertGreater(ev.deficit, 0, f"trial {trial}")

    def _check_allocations(self, result, lines, pos, caps, trial):
        pos_by_id = {p.id: p for p in pos}
        per_line = {l.id: 0 for l in lines}
        per_po = {p.id: 0 for p in pos}
        cost = 0
        for a in result.allocations:
            line = next(l for l in lines if l.id == a.invoice_line_id)
            po = pos_by_id[a.po_line_id]
            self.assertIn(a.po_line_id, line.candidate_po_ids,
                          f"trial {trial}")
            self.assertTrue(eligible(line, po), f"trial {trial}")
            self.assertGreater(a.qty, 0, f"trial {trial}")
            per_line[a.invoice_line_id] += a.qty
            per_po[a.po_line_id] += a.qty
            cost += a.qty * abs(line.unit_price - po.unit_price)
        for l in lines:
            self.assertEqual(per_line[l.id], l.qty, f"trial {trial}")
        for j, p in enumerate(pos):
            self.assertLessEqual(per_po[p.id], caps[j], f"trial {trial}")
        self.assertEqual(cost, result.total_price_diff, f"trial {trial}")


if __name__ == "__main__":
    unittest.main()
