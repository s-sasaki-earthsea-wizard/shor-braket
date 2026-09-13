# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Recover periods and factors from phase-estimation outcomes."""

from fractions import Fraction
from math import gcd


def recover_orders(
    count_values: set[int], *, modulus: int, base: int, count_qubits: int
) -> list[int]:
    """Recover valid order candidates using continued fractions.

    Multiples of the continued-fraction denominator are checked because a sampled phase can
    reduce ``s/r`` to a denominator that divides the true order.
    """
    scale = 1 << count_qubits
    orders: set[int] = set()

    for value in count_values:
        if value <= 0 or value >= scale:
            continue
        denominator = Fraction(value, scale).limit_denominator(modulus).denominator
        if denominator == 1:
            continue
        for multiplier in range(1, modulus // denominator + 1):
            candidate = denominator * multiplier
            if pow(base, candidate, modulus) == 1:
                orders.add(candidate)
                break

    return sorted(orders)


def factors_from_order(*, modulus: int, base: int, order: int) -> tuple[int, int] | None:
    """Return non-trivial factors implied by an even order, if they exist."""
    if order <= 0 or order % 2:
        return None

    root = pow(base, order // 2, modulus)
    if root in (1, modulus - 1):
        return None

    factors = sorted((gcd(root - 1, modulus), gcd(root + 1, modulus)))
    if factors[0] in (1, modulus) or factors[1] in (1, modulus):
        return None
    if factors[0] * factors[1] != modulus:
        return None
    return factors[0], factors[1]


def recover_factors(
    count_values: set[int], *, modulus: int, base: int, count_qubit_count: int
) -> tuple[list[int], tuple[int, int] | None]:
    """Recover candidate orders and the first valid factor pair."""
    orders = recover_orders(
        count_values,
        modulus=modulus,
        base=base,
        count_qubits=count_qubit_count,
    )
    for order in orders:
        factors = factors_from_order(modulus=modulus, base=base, order=order)
        if factors is not None:
            return orders, factors
    return orders, None
