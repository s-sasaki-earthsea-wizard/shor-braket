# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Expected and observed joint distributions for order finding."""

from collections.abc import Mapping

import numpy as np
from numpy.typing import NDArray


def expected_joint_probabilities(
    *, modulus: int, base: int, count_qubit_count: int, work_qubit_count: int
) -> NDArray[np.float64]:
    """Calculate the ideal joint distribution of count and work registers."""
    count_dimension = 1 << count_qubit_count
    work_dimension = 1 << work_qubit_count
    probabilities = np.zeros(count_dimension * work_dimension, dtype=np.float64)

    work_preimages: dict[int, list[int]] = {}
    for exponent in range(count_dimension):
        work_value = pow(base, exponent, modulus)
        work_preimages.setdefault(work_value, []).append(exponent)

    for count_value in range(count_dimension):
        for work_value, exponents in work_preimages.items():
            amplitudes = [
                np.exp(-2j * np.pi * exponent * count_value / count_dimension)
                for exponent in exponents
            ]
            amplitude = sum(amplitudes) / count_dimension
            index = count_value * work_dimension + work_value
            probabilities[index] = abs(amplitude) ** 2
    return probabilities


def total_variation_distance(
    actual: NDArray[np.float64], expected: NDArray[np.float64]
) -> float:
    """Return the total variation distance between equal-shaped distributions."""
    if actual.shape != expected.shape:
        raise ValueError("probability vectors must have the same shape")
    return float(0.5 * np.abs(actual - expected).sum())


def sampled_probability_vector(
    measurement_counts: Mapping[str, int], *, total_qubit_count: int
) -> NDArray[np.float64]:
    """Convert Braket measurement counts into a dense probability vector."""
    probabilities = np.zeros(1 << total_qubit_count, dtype=np.float64)
    shots = sum(measurement_counts.values())
    if shots <= 0:
        raise ValueError("measurement counts must contain at least one shot")
    for state, count in measurement_counts.items():
        probabilities[int(state, 2)] = count / shots
    return probabilities
