# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Classical reference implementation of multiplicative order."""

from math import gcd


def multiplicative_order(base: int, modulus: int) -> int:
    """Return the smallest positive ``r`` where ``base**r == 1 mod modulus``.

    Args:
        base: Integer whose order is required.
        modulus: Modulus greater than two.

    Returns:
        The multiplicative order.

    Raises:
        ValueError: If the inputs do not define an element of the multiplicative group.
    """
    if modulus <= 2:
        raise ValueError("modulus must be greater than two")
    if not 1 < base < modulus:
        raise ValueError("base must satisfy 1 < base < modulus")
    if gcd(base, modulus) != 1:
        raise ValueError("base and modulus must be coprime")

    value = 1
    for order in range(1, modulus + 1):
        value = value * base % modulus
        if value == 1:
            return order
    raise ValueError("multiplicative order was not found")
