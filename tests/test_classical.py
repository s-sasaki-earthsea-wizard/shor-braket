# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

import pytest

from shor_braket.classical.order import multiplicative_order
from shor_braket.classical.postprocess import factors_from_order, recover_factors


def test_order_and_factors_for_fifteen():
    assert multiplicative_order(7, 15) == 4
    assert factors_from_order(modulus=15, base=7, order=4) == (3, 5)


def test_continued_fractions_recover_reduced_order_multiple():
    orders, factors = recover_factors(
        {0, 4, 8, 12},
        modulus=15,
        base=7,
        count_qubit_count=4,
    )

    assert orders == [4]
    assert factors == (3, 5)


def test_six_has_no_nontrivial_factors_from_its_order():
    assert multiplicative_order(5, 6) == 2
    assert factors_from_order(modulus=6, base=5, order=2) is None


@pytest.mark.parametrize(
    ("base", "modulus"),
    [(1, 15), (15, 15), (6, 15)],
)
def test_invalid_order_inputs_are_rejected(base, modulus):
    with pytest.raises(ValueError):
        multiplicative_order(base, modulus)
