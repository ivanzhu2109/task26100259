"""Public data types for the three-way matching component."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Hashable


@dataclass(frozen=True)
class POLine:
    """A purchase order line. All data is immutable for the matcher's lifetime.

    Quantities are non-negative integers, ``unit_price`` is a positive integer
    in cents and ``tax_rate`` is an integer in basis points.
    """

    id: Hashable
    ordered_qty: int
    accepted_qty: int
    unit_price: int
    tax_rate: int


@dataclass(frozen=True)
class InvoiceLine:
    """One invoice line. ``candidate_po_ids`` already encodes substitution rules."""

    id: Hashable
    qty: int
    unit_price: int
    tax_rate: int
    candidate_po_ids: frozenset


@dataclass(frozen=True)
class Allocation:
    """Quantity of one invoice line assigned to one PO line."""

    invoice_line_id: Hashable
    po_line_id: Hashable
    qty: int


@dataclass(frozen=True)
class MatchSuccess:
    """Result of a successful ``match`` call."""

    allocations: tuple[Allocation, ...]
    total_price_diff: int


@dataclass(frozen=True)
class InfeasibleEvidence:
    """Verifiable Hall-style certificate of infeasibility.

    ``invoice_line_ids`` is a non-empty set S of invoice lines,
    ``candidate_po_ids`` is N(S), the union of all eligible candidates of S
    (including zero-capacity ones), and ``deficit`` equals the total demand of
    S minus the total remaining capacity of N(S) at the time of the call.
    """

    invoice_line_ids: frozenset
    candidate_po_ids: frozenset
    deficit: int


class InfeasibleMatchError(Exception):
    """Raised by ``match`` when the invoice cannot be fully matched.

    Carries an :class:`InfeasibleEvidence`; the matcher state is unchanged.
    """

    def __init__(self, evidence: InfeasibleEvidence):
        self.evidence = evidence
        super().__init__(
            f"infeasible: {len(evidence.invoice_line_ids)} invoice line(s) "
            f"lack {evidence.deficit} unit(s) of candidate capacity"
        )


@dataclass(frozen=True)
class Snapshot:
    """Point-in-time view: remaining capacity per PO line and past allocations.

    ``remaining_capacity`` is a fresh dict on every call; mutating it (or any
    part of the snapshot) never affects the matcher's internal state.
    """

    remaining_capacity: dict
    allocations: tuple[Allocation, ...]
