# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

import numpy as np
import pytest
from braket.circuits import Circuit

from shor_braket.quantum.native import (
    PRX_CZ,
    PRX_XX,
    add_cnot,
    add_hadamard,
    bell_circuit,
    cnot_ladder_circuit,
    gate_family,
    ghz_circuit,
    two_qubit_gate_count,
    verbatim,
)


def _same_up_to_phase(first: Circuit, second: Circuit) -> bool:
    a, b = first.to_unitary(), second.to_unitary()
    return abs(abs(np.trace(a.conj().T @ b)) - a.shape[0]) < 1e-9


def test_gate_family_detection():
    assert gate_family(["cz", "prx", "cc_prx", "measure_ff", "barrier"]) == PRX_CZ
    assert gate_family(["prx", "xx", "rz"]) == PRX_XX
    with pytest.raises(NotImplementedError):
        gate_family(["rx", "rz", "cz"])


def test_hadamard_decomposition_matches_the_sdk_gate():
    assert _same_up_to_phase(add_hadamard(Circuit(), 0), Circuit().h(0))


@pytest.mark.parametrize("family", [PRX_CZ, PRX_XX])
def test_cnot_decomposition_matches_the_sdk_gate(family):
    assert _same_up_to_phase(add_cnot(Circuit(), 0, 1, family), Circuit().cnot(0, 1))
    assert _same_up_to_phase(add_cnot(Circuit(), 1, 0, family), Circuit().cnot(1, 0))


@pytest.mark.parametrize("family", [PRX_CZ, PRX_XX])
def test_bell_and_ghz_use_only_native_gates(family):
    allowed = {"PRx", "CZ"} if family == PRX_CZ else {"PRx", "XX"}
    for circuit in (bell_circuit((3, 4), family), ghz_circuit((3, 4, 5), family)):
        assert {instruction.operator.name for instruction in circuit.instructions} <= allowed
    assert _same_up_to_phase(bell_circuit((0, 1), family), Circuit().h(0).cnot(0, 1))
    assert _same_up_to_phase(ghz_circuit((0, 1, 2), family), Circuit().h(0).cnot(0, 1).cnot(1, 2))


def test_cnot_ladder_adds_identity_pairs():
    ladder = cnot_ladder_circuit((0, 1), PRX_CZ, pairs=3)
    assert two_qubit_gate_count(ladder) == 7
    assert _same_up_to_phase(ladder, Circuit().h(0).cnot(0, 1))
    boxed = verbatim(ladder)
    assert boxed.instructions[0].operator.name == "StartVerbatimBox"
    assert boxed.instructions[-1].operator.name == "EndVerbatimBox"
    assert two_qubit_gate_count(boxed) == 7
