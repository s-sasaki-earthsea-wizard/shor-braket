# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Feed-forward building blocks for the IQM dynamic-circuit capability.

IQM Garnet and Emerald expose ``measure_ff`` (a mid-circuit measurement stored under an integer
feedback key) and ``cc_prx`` (a ``prx`` applied only when the stored bit is 1). The SDK models
them as experimental operators that can only be instantiated inside
``EnableExperimentalCapability``; this module wraps that context and adds three things the order
finding circuit needs:

* a conditional Z rotation built from two conditional pi pulses,
  ``prx(pi, phi) . prx(pi, 0) = -Rz(2 phi)``;
* the deferred-measurement transform that turns a feed-forward circuit into an ordinary circuit
  (measurement -> CNOT onto a fresh ancilla, conditional gate -> quantum-controlled gate) so the
  exact output distribution can be computed with a state-vector or density-matrix simulator;
* a checker for the constraints the Braket developer guide places on dynamic circuits. The
  qubit-group constraint cannot be checked offline because the device capabilities do not
  publish the groups.

The guide also states that mid-circuit measurement outcomes are not returned in the task result,
which is why the circuit copies each outcome into a record qubit with ``cc_prx``.
"""

from __future__ import annotations

import warnings
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from math import pi

import numpy as np
from braket.circuits import Circuit, Gate
from braket.circuits.measure import Measure
from braket.experimental_capabilities import EnableExperimentalCapability
from braket.experimental_capabilities.iqm.classical_control import (
    CCPRx,
    ExperimentalQuantumOperator,
    MeasureFF,
)
from numpy.typing import NDArray

FEED_FORWARD_GATES = frozenset({"cc_prx", "measure_ff"})
CCPRX = "ccprx"
MEASURE_FF = "measureff"
UNCHECKED_CONSTRAINTS = (
    "cc_prx control must stay inside one of the device's qubit groups; the groups are only "
    "published as an image in the developer guide, not in the device capabilities",
)


@contextmanager
def experimental_capabilities() -> Iterator[None]:
    """Enable the SDK's experimental operators without the per-entry warning."""
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="You are enabling experimental capabilities")
        with EnableExperimentalCapability():
            yield


def supports_feed_forward(native_gate_set: Iterable[str]) -> bool:
    """Whether a device's native gate set includes both feed-forward operations."""
    return {gate.lower() for gate in native_gate_set} >= FEED_FORWARD_GATES


def is_feed_forward(operator: object) -> bool:
    """Whether an instruction operator is one of the experimental feed-forward operators."""
    return isinstance(operator, ExperimentalQuantumOperator)


def add_conditional_rz(circuit: Circuit, qubit: int, angle: float, feedback_key: int) -> Circuit:
    """Append ``Rz(angle)`` on ``qubit`` applied only when the stored bit is 1.

    Two pi pulses about axes ``angle / 2`` apart compose to ``Rz(angle)`` up to a global phase;
    both carry the same feedback key so either both or neither are applied.
    """
    circuit.cc_prx(qubit, pi, 0.0, feedback_key)
    circuit.cc_prx(qubit, pi, angle / 2, feedback_key)
    return circuit


def add_conditional_x(circuit: Circuit, qubit: int, feedback_key: int) -> Circuit:
    """Append an X on ``qubit`` applied only when the stored bit is 1 (reset or copy)."""
    circuit.cc_prx(qubit, pi, 0.0, feedback_key)
    return circuit


def _controlled(matrix: NDArray[np.complex128]) -> NDArray[np.complex128]:
    """Two-qubit unitary applying ``matrix`` to the second qubit when the first is ``|1>``.

    Written as an explicit unitary rather than a control modifier: the density-matrix simulator's
    large-circuit kernels reject the non-contiguous view a control on a trailing axis produces.
    """
    controlled = np.eye(4, dtype=np.complex128)
    controlled[2:, 2:] = matrix
    return controlled


@dataclass(frozen=True)
class DeferredCircuit:
    """A feed-forward circuit rewritten with the deferred-measurement principle."""

    circuit: Circuit
    ancillas: dict[int, int]


def deferred_measurement_circuit(circuit: Circuit, first_ancilla: int) -> DeferredCircuit:
    """Rewrite ``measure_ff`` / ``cc_prx`` as CNOTs onto ancillas and quantum-controlled gates.

    ``measure_ff(key) q`` becomes ``cnot(q, ancilla_key)`` with a fresh ancilla in ``|0>``; the
    qubit is then correlated with the ancilla exactly as it would be with the classical record,
    and a later conditional reset ``cc_prx(pi, 0, key) q`` (now a CNOT from the ancilla) returns
    it to ``|0>``. ``cc_prx(theta, phi, key) q`` becomes the two-qubit unitary that applies
    ``prx(theta, phi)`` when the ancilla is ``|1>``. Final ``measure`` instructions are dropped so
    the result can be read with the ``Probability`` result type; noise instructions are kept
    where they are.

    Args:
        circuit: Circuit that may contain the experimental operators and noise.
        first_ancilla: Label of the first ancilla; keys get consecutive labels from it.

    Returns:
        The rewritten circuit and the key -> ancilla map.
    """
    ancillas: dict[int, int] = {}
    result = Circuit()
    for instruction in circuit.instructions:
        operator = instruction.operator
        if isinstance(operator, MeasureFF):
            key = int(operator.parameters[0])
            if key in ancillas:
                raise ValueError(f"feedback key {key} is measured twice")
            ancillas[key] = first_ancilla + len(ancillas)
            result.cnot(int(instruction.target[0]), ancillas[key])
        elif isinstance(operator, CCPRx):
            theta, phi, key = operator.parameters
            if int(key) not in ancillas:
                raise ValueError(f"cc_prx uses feedback key {int(key)} before it is measured")
            result.unitary(
                matrix=_controlled(Gate.PRx(float(theta), float(phi)).to_matrix()),
                targets=[ancillas[int(key)], int(instruction.target[0])],
            )
        elif isinstance(operator, Measure):
            continue
        else:
            result.add_instruction(instruction)
    for result_type in circuit.result_types:
        result.add_result_type(result_type)
    return DeferredCircuit(circuit=result, ancillas=ancillas)


def feed_forward_violations(circuit: Circuit) -> list[str]:
    """Check the developer-guide constraints that can be checked without the device.

    Checked: feedback keys are unique, every ``cc_prx`` follows the ``measure_ff`` of its key,
    and each qubit is conditioned on at most one measured qubit (itself or one other).
    Not checked: the qubit-group constraint (see ``UNCHECKED_CONSTRAINTS``).
    """
    violations: list[str] = []
    measured_by_key: dict[int, int] = {}
    controllers: dict[int, set[int]] = {}
    for instruction in circuit.instructions:
        operator = instruction.operator
        qubit = int(instruction.target[0]) if len(instruction.target) == 1 else -1
        if isinstance(operator, MeasureFF):
            key = int(operator.parameters[0])
            if key in measured_by_key:
                violations.append(f"feedback key {key} is used by more than one measure_ff")
            measured_by_key[key] = qubit
        elif isinstance(operator, CCPRx):
            key = int(operator.parameters[2])
            if key not in measured_by_key:
                violations.append(f"cc_prx on qubit {qubit} uses key {key} before measure_ff")
                continue
            controllers.setdefault(qubit, set()).add(measured_by_key[key])
    for qubit, sources in sorted(controllers.items()):
        if len(sources) > 1:
            violations.append(
                f"qubit {qubit} is conditioned on {sorted(sources)}; only one controller is allowed"
            )
    return violations


def feed_forward_counts(circuit: Circuit) -> dict[str, int]:
    """Number of ``measure_ff`` and ``cc_prx`` instructions in a circuit."""
    counts = {"measure_ff": 0, "cc_prx": 0}
    for instruction in circuit.instructions:
        if isinstance(instruction.operator, MeasureFF):
            counts["measure_ff"] += 1
        elif isinstance(instruction.operator, CCPRx):
            counts["cc_prx"] += 1
    return counts
