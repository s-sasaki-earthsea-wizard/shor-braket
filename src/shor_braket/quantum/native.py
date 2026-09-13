# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Hand-written native-gate circuits for the approved QPU families.

The Braket SDK ships no transpiler: a verbatim circuit must already use the device's native gates
on physically adjacent qubits. These helpers cover the two gate families of the approved devices,
IQM (``prx`` + ``cz``) and AQT (``prx`` + ``xx`` + ``rz``), for the smoke circuits used by the
local emulator compatibility report. They are not Shor circuits.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from math import pi

from braket.circuits import Circuit, Gate

PRX_CZ = "prx-cz"
PRX_XX = "prx-xx"
GATE_FAMILIES: dict[str, frozenset[str]] = {
    PRX_CZ: frozenset({"prx", "cz"}),
    PRX_XX: frozenset({"prx", "xx"}),
}


def gate_family(native_gate_set: Iterable[str]) -> str:
    """Pick the decomposition family for a device's native gate set.

    Raises:
        NotImplementedError: If no hand-written decomposition covers the gate set.
    """
    gates = {gate.lower() for gate in native_gate_set}
    for family, required in GATE_FAMILIES.items():
        if required <= gates:
            return family
    raise NotImplementedError(f"no hand-written native decomposition for gate set {sorted(gates)}")


def add_hadamard(circuit: Circuit, qubit: int) -> Circuit:
    """Append H as ``X · Ry(pi/2)`` using two ``prx`` rotations (equal up to global phase)."""
    return circuit.prx(qubit, pi / 2, pi / 2).prx(qubit, pi, 0)


def add_cnot(circuit: Circuit, control: int, target: int, family: str) -> Circuit:
    """Append CNOT in the given native family.

    ``prx-cz`` uses ``H · CZ · H`` on the target. ``prx-xx`` uses the Mølmer–Sørensen identity
    ``Ry(-pi/2)_c · Rx(-pi/2)_t · Rx(-pi/2)_c · XX(pi/2) · Ry(pi/2)_c`` (right to left in time).
    Both were checked numerically against ``Circuit().cnot`` up to global phase.
    """
    if family == PRX_CZ:
        add_hadamard(circuit, target)
        circuit.cz(control, target)
        add_hadamard(circuit, target)
    elif family == PRX_XX:
        circuit.prx(control, pi / 2, pi / 2)
        circuit.xx(control, target, pi / 2)
        circuit.prx(control, -pi / 2, 0)
        circuit.prx(target, -pi / 2, 0)
        circuit.prx(control, -pi / 2, pi / 2)
    else:
        raise ValueError(f"unknown gate family {family!r}")
    return circuit


def bell_circuit(qubits: Sequence[int], family: str) -> Circuit:
    """Bell pair ``(|00> + |11>) / sqrt(2)`` on two physical qubits."""
    if len(qubits) != 2:
        raise ValueError("a Bell circuit needs exactly two qubits")
    circuit = add_hadamard(Circuit(), qubits[0])
    return add_cnot(circuit, qubits[0], qubits[1], family)


def ghz_circuit(path: Sequence[int], family: str) -> Circuit:
    """GHZ state along a path of physically adjacent qubits."""
    if len(path) < 2:
        raise ValueError("a GHZ circuit needs at least two qubits")
    circuit = add_hadamard(Circuit(), path[0])
    for control, target in zip(path, path[1:], strict=False):
        add_cnot(circuit, control, target, family)
    return circuit


def cnot_ladder_circuit(qubits: Sequence[int], family: str, pairs: int) -> Circuit:
    """Bell pair followed by ``pairs`` identity-equivalent CNOT·CNOT blocks.

    The extra blocks change nothing in the ideal state but add two two-qubit gates each, which
    makes the emulator's gate noise scale with depth in a controlled way.
    """
    if pairs < 0:
        raise ValueError("pairs must be non-negative")
    circuit = bell_circuit(qubits, family)
    for _ in range(pairs):
        add_cnot(circuit, qubits[0], qubits[1], family)
        add_cnot(circuit, qubits[0], qubits[1], family)
    return circuit


def verbatim(circuit: Circuit) -> Circuit:
    """Wrap a circuit in a verbatim box so the emulator treats it as hardware-native."""
    return Circuit().add_verbatim_box(circuit)


def two_qubit_gate_count(circuit: Circuit) -> int:
    """Count two-qubit gate instructions (verbatim markers and result types excluded)."""
    return sum(
        1
        for instruction in circuit.instructions
        if isinstance(instruction.operator, Gate) and len(instruction.target) == 2
    )
