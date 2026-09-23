# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Tests for the added decoherence model and the metrics that separate network from coherence."""

import numpy as np
import pytest

from shor_braket.analysis.distribution import (
    expected_joint_probabilities,
    low_bit_visibility,
    orbit_mass,
)
from shor_braket.runner.decoherence import (
    SCENARIOS,
    QubitDecoherence,
    apply_readout,
    damping_parameters,
    decoherence_study,
    qubit_decoherence,
)

QUBIT = QubitDecoherence(
    t1=40e-6, t2=10e-6, t2_echo=25e-6, readout_0_to_1=0.01, readout_1_to_0=0.04
)


def _dephased_count(t: int) -> np.ndarray:
    """What an ideal network gives when the count register has lost its phase: y uniform."""
    table = np.zeros((1 << t, 16))
    table[:, [1, 4, 7, 13]] = 1.0 / ((1 << t) * 4)
    return table.reshape(-1)


# --- the metrics ---------------------------------------------------------------------------


def test_at_t2_the_ideal_and_the_dephased_distributions_are_the_same():
    """The reason the first hardware run could not see coherence at all."""
    ideal = expected_joint_probabilities(
        modulus=15, base=7, count_qubit_count=2, work_qubit_count=4
    )
    assert np.allclose(ideal, _dephased_count(2))
    assert low_bit_visibility(ideal, count_qubit_count=2, work_qubit_count=4, order=4) is None


def test_at_t3_the_low_bits_tell_coherence_from_dephasing():
    ideal = expected_joint_probabilities(
        modulus=15, base=7, count_qubit_count=3, work_qubit_count=4
    )
    dephased = _dephased_count(3)
    assert low_bit_visibility(ideal, count_qubit_count=3, work_qubit_count=4, order=4) == (
        pytest.approx(1.0)
    )
    assert low_bit_visibility(dephased, count_qubit_count=3, work_qubit_count=4, order=4) == (
        pytest.approx(0.0)
    )
    # The multiplication network did its job in both cases, and the orbit mass says so.
    for joint in (ideal, dephased):
        assert orbit_mass(joint, modulus=15, base=7, work_qubit_count=4) == pytest.approx(1.0)


def test_a_uniform_device_is_off_the_orbit_three_quarters_of_the_time():
    uniform = np.full(64, 1 / 64)
    assert orbit_mass(uniform, modulus=15, base=7, work_qubit_count=4) == pytest.approx(0.25)


# --- the decay model -----------------------------------------------------------------------


def test_no_idle_time_means_no_decay():
    assert damping_parameters(0.0, QUBIT, "T2") == (0.0, 0.0)


def test_echo_t2_dephases_less_than_ramsey_t2():
    _, ramsey = damping_parameters(1e-6, QUBIT, "T2")
    _, echo = damping_parameters(1e-6, QUBIT, "T2_echo")
    assert 0 < echo < ramsey


def test_pure_dephasing_is_never_negative():
    long_t2 = QubitDecoherence(
        t1=10e-6, t2=30e-6, t2_echo=30e-6, readout_0_to_1=0, readout_1_to_0=0
    )
    assert damping_parameters(1e-6, long_t2, "T2")[1] == 0.0


def test_asymmetric_readout_leans_towards_zero():
    one = np.array([0.0, 1.0])  # the qubit is in |1>
    read = apply_readout(one, [5], {5: QUBIT})
    assert read == pytest.approx([0.04, 0.96])


def test_aqt_snapshots_are_refused(ibex_snapshot):
    with pytest.raises(ValueError, match="one_qubit"):
        qubit_decoherence(ibex_snapshot)


@pytest.mark.slow
def test_idle_decay_lowers_the_prediction_and_leans_towards_zero(snapshot_dir):
    study = decoherence_study(
        device_key="garnet",
        oracle_mode="generic-constant",
        snapshot_dir=snapshot_dir,
        scenarios=SCENARIOS[:1],
        max_permutations=1,
    )
    emulator, readout_only, decay = study["models"]
    assert emulator["signal_fraction_support_mass"] > 0.4
    # Asymmetric readout alone barely moves it; idle decay moves it a lot, and towards |0...0>.
    base = emulator["signal_fraction_support_mass"]
    assert abs(readout_only["signal_fraction_support_mass"] - base) < 0.05
    assert decay["signal_fraction_support_mass"] < base - 0.1
    assert decay["count_all_zero"] > decay["count_all_one"]


def test_a_qubit_without_a_reported_t2_fails_only_when_that_t2_is_needed():
    partial = QubitDecoherence(
        t1=40e-6, t2=None, t2_echo=25e-6, readout_0_to_1=0.01, readout_1_to_0=0.04
    )
    assert damping_parameters(1e-6, partial, "T2_echo")[1] > 0
    with pytest.raises(ValueError, match="no T2"):
        damping_parameters(1e-6, partial, "T2")
