# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Expected and observed joint distributions for order finding."""

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
from numpy.typing import NDArray

# Pass criteria decided on 2026-09-14 (issue #7).
EXACT_TVD_LIMIT = 1e-9  # noiseless simulator, shots = 0: anything larger is a wiring bug
SAMPLED_FLOOR_FACTOR = 1.5  # noiseless simulator, sampled run: TVD <= factor * sampling floor
SIGNAL_FRACTION_PASS = 0.5  # emulator / QPU: at least half of the period signal survives
SIGNAL_DETECTION_SIGMAS = 3.0  # emulator / QPU: "signal present" if lambda > sigmas * SE


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


def total_variation_distance(actual: NDArray[np.float64], expected: NDArray[np.float64]) -> float:
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


def hellinger_fidelity(actual: NDArray[np.float64], expected: NDArray[np.float64]) -> float:
    """Return the Hellinger fidelity ``(sum sqrt(p q))**2`` between equal-shaped distributions."""
    if actual.shape != expected.shape:
        raise ValueError("probability vectors must have the same shape")
    return float(np.sum(np.sqrt(np.clip(actual, 0.0, None) * np.clip(expected, 0.0, None))) ** 2)


def sampling_floor(probabilities: NDArray[np.float64], shots: int) -> float:
    """Expected TVD between an ``shots``-shot empirical distribution and its own source.

    Normal approximation: each bin deviates by ``sqrt(p (1 - p) / shots)`` on average times
    ``sqrt(2 / pi)``, and TVD halves the sum of absolute deviations. This is the distance a
    perfect device still shows, so a sampled TVD below it is not a better result.
    """
    if shots < 1:
        raise ValueError("shots must be positive")
    p = np.asarray(probabilities, dtype=np.float64)
    return float(np.sqrt(p * (1.0 - p) / (2.0 * np.pi * shots)).sum())


def signal_fraction(tvd: float, tvd_ideal_vs_uniform: float) -> float:
    """Signal fraction ``1 - TVD / TVD(ideal, uniform)`` (uniform-mixture model)."""
    return 1.0 - tvd / tvd_ideal_vs_uniform


def support_signal_fraction(support_mass: float, support_fraction: float) -> float:
    """Signal fraction from the mass on the ideal support under the uniform-mixture model.

    If ``p = lam * p_ideal + (1 - lam) * uniform`` then the mass on the ideal support is
    ``lam + (1 - lam) * support_fraction``. Unlike the TVD estimate this one is linear in the
    counts, so its sampled value is unbiased whatever the number of shots.
    """
    return (support_mass - support_fraction) / (1.0 - support_fraction)


def support_signal_fraction_error(
    support_mass: float, support_fraction: float, shots: int
) -> float:
    """Standard error of the support-mass signal fraction for a multinomial sample."""
    if shots < 1:
        raise ValueError("shots must be positive")
    mass = min(max(support_mass, 0.0), 1.0)
    return float(np.sqrt(mass * (1.0 - mass) / shots) / (1.0 - support_fraction))


def noiseless_verdict(
    *, exact_tvd: float, sampled_tvd: float, expected: NDArray[np.float64], shots: int
) -> dict[str, Any]:
    """Pass / fail of a noiseless simulator run: exact TVD and sampled TVD against the floor."""
    floor = sampling_floor(expected, shots)
    exact_passed = exact_tvd <= EXACT_TVD_LIMIT
    sampled_passed = sampled_tvd <= SAMPLED_FLOOR_FACTOR * floor
    return {
        "exact_tvd": exact_tvd,
        "exact_limit": EXACT_TVD_LIMIT,
        "exact_passed": exact_passed,
        "sampled_tvd": sampled_tvd,
        "sampling_floor": floor,
        "sampled_limit": SAMPLED_FLOOR_FACTOR * floor,
        "sampled_passed": sampled_passed,
        "passed": exact_passed and sampled_passed,
    }


def noisy_verdict(
    *,
    signal_fraction_exact: float,
    support_mass_sampled: float,
    support_fraction: float,
    shots: int,
) -> dict[str, Any]:
    """Pass / fail of an emulated or hardware run.

    The pass line uses the exact emulated signal fraction; the detection test uses the sampled
    support-mass estimate against its standard error (both are recorded, per issue #7).
    """
    sampled = support_signal_fraction(support_mass_sampled, support_fraction)
    error = support_signal_fraction_error(support_mass_sampled, support_fraction, shots)
    return {
        "pass_line": SIGNAL_FRACTION_PASS,
        "signal_fraction_exact": signal_fraction_exact,
        "passed": signal_fraction_exact >= SIGNAL_FRACTION_PASS,
        "signal_fraction_sampled": sampled,
        "standard_error": error,
        "detection_sigmas": SIGNAL_DETECTION_SIGMAS,
        "signal_detected": sampled > SIGNAL_DETECTION_SIGMAS * error,
    }


def _summary(values: NDArray[np.float64]) -> dict[str, float]:
    return {
        "mean": float(values.mean()),
        "sd": float(values.std(ddof=1)) if values.size > 1 else 0.0,
        "p025": float(np.percentile(values, 2.5)),
        "p975": float(np.percentile(values, 97.5)),
    }


def sampling_sweep(
    exact: NDArray[np.float64],
    expected: NDArray[np.float64],
    *,
    shots_list: Sequence[int],
    repetitions: int = 200,
    seed: int = 0,
) -> dict[str, Any]:
    """Monte Carlo of what ``shots``-shot samples of ``exact`` look like against ``expected``.

    For each shot count, ``repetitions`` multinomial samples are drawn from ``exact`` and their
    TVD to ``expected``, TVD to ``exact`` (the pure sampling floor), and the two signal-fraction
    estimates are summarised. ``exact`` is clipped to non-negative values and renormalised.
    """
    if repetitions < 2:
        raise ValueError("repetitions must be at least two")
    source = np.clip(np.asarray(exact, dtype=np.float64), 0.0, None)
    source = source / source.sum()
    support = expected > 1e-9
    support_fraction = float(support.mean())
    uniform = np.full_like(expected, 1.0 / expected.size)
    tvd_ideal_uniform = total_variation_distance(expected, uniform)
    rng = np.random.default_rng(seed)
    rows: list[dict[str, Any]] = []
    for shots in shots_list:
        samples = rng.multinomial(shots, source, size=repetitions) / shots
        tvd_expected = 0.5 * np.abs(samples - expected).sum(axis=1)
        tvd_source = 0.5 * np.abs(samples - source).sum(axis=1)
        mass = samples[:, support].sum(axis=1)
        rows.append(
            {
                "shots": int(shots),
                "tvd_to_expected": _summary(tvd_expected),
                "tvd_to_exact": _summary(tvd_source),
                "floor_formula": sampling_floor(source, shots),
                "signal_fraction_tvd": _summary(1.0 - tvd_expected / tvd_ideal_uniform),
                "signal_fraction_support": _summary(
                    (mass - support_fraction) / (1.0 - support_fraction)
                ),
                "ideal_support_mass": _summary(mass),
            }
        )
    exact_tvd = total_variation_distance(source, expected)
    return {
        "repetitions": repetitions,
        "seed": seed,
        "support_fraction": support_fraction,
        "tvd_ideal_vs_uniform": tvd_ideal_uniform,
        "exact_tvd": exact_tvd,
        "exact_signal_fraction_tvd": signal_fraction(exact_tvd, tvd_ideal_uniform),
        "exact_signal_fraction_support": support_signal_fraction(
            float(source[support].sum()), support_fraction
        ),
        "rows": rows,
    }
