# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Lower a logical circuit to a device's native gates with virtual-Z phase tracking.

The logical gate set is ``h``, ``x``, ``y``, ``z``, ``s``, ``si``, ``t``, ``ti``, ``rx``, ``ry``,
``rz``, ``phaseshift``, ``cnot``, ``cz`` and ``swap``. Z rotations are never emitted for the
``prx``/``cz`` family: they are accumulated per qubit and folded into the ``phi`` of later ``prx``
gates (``prx(theta, phi) · Rz(a) = Rz(a) · prx(theta, phi - a)``), and ``cz`` commutes with them.
The ``prx``/``xx`` family flushes pending phases as native ``rz`` gates before each ``xx`` because
``XX`` does not commute with Z rotations. Phases left over at the end do not change Z-basis
measurement statistics and are dropped, which the report records.

The IQM feed-forward operators pass through: ``cc_prx`` gets the pending phase folded into its
``phi`` like any ``prx`` (both pulses of a conditional pair shift together, so their difference
and hence the conditional rotation are unchanged), and ``measure_ff`` clears the pending phase
of its qubit because a Z rotation before a Z-basis measurement is unobservable and the qubit is
left in a computational basis state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import pi

from braket.circuits import Circuit, Gate

from shor_braket.quantum.feedforward import (
    CCPRX,
    MEASURE_FF,
    experimental_capabilities,
    is_feed_forward,
)
from shor_braket.quantum.native import PRX_CZ, PRX_XX

_Op = tuple[str, tuple[int, ...], tuple[float, ...]]


@dataclass(frozen=True)
class CompiledCircuit:
    """Native circuit on the same qubit labels as its input, plus gate statistics."""

    circuit: Circuit
    family: str
    gate_counts: dict[str, int]
    two_qubit_gates: int
    dropped_final_z: dict[int, float] = field(default_factory=dict)


def _angle(operator: Gate) -> float:
    return float(operator.angle)


def _lower_instruction(
    name: str, qubits: tuple[int, ...], operator: Gate, family: str
) -> list[_Op]:
    """Translate one logical gate to prx / rz / cz / xx / feed-forward operations."""
    q = qubits[0]
    if name in (CCPRX, MEASURE_FF):
        if family != PRX_CZ:
            raise NotImplementedError(f"feed-forward is only lowered for {PRX_CZ!r}")
        return [(name, (q,), tuple(float(p) for p in operator.parameters))]
    if name == "h":
        return [("prx", (q,), (pi / 2, pi / 2)), ("prx", (q,), (pi, 0.0))]
    if name == "x":
        return [("prx", (q,), (pi, 0.0))]
    if name == "y":
        return [("prx", (q,), (pi, pi / 2))]
    if name == "z":
        return [("rz", (q,), (pi,))]
    if name == "s":
        return [("rz", (q,), (pi / 2,))]
    if name == "si":
        return [("rz", (q,), (-pi / 2,))]
    if name == "t":
        return [("rz", (q,), (pi / 4,))]
    if name == "ti":
        return [("rz", (q,), (-pi / 4,))]
    if name == "rx":
        return [("prx", (q,), (_angle(operator), 0.0))]
    if name == "ry":
        return [("prx", (q,), (_angle(operator), pi / 2))]
    if name in ("rz", "phaseshift"):
        return [("rz", (q,), (_angle(operator),))]
    if name == "cnot":
        control, target = qubits
        if family == PRX_CZ:
            return [
                ("prx", (target,), (pi / 2, pi / 2)),
                ("prx", (target,), (pi, 0.0)),
                ("cz", (control, target), ()),
                ("prx", (target,), (pi / 2, pi / 2)),
                ("prx", (target,), (pi, 0.0)),
            ]
        return [
            ("prx", (control,), (pi / 2, pi / 2)),
            ("xx", (control, target), (pi / 2,)),
            ("prx", (control,), (-pi / 2, 0.0)),
            ("prx", (target,), (-pi / 2, 0.0)),
            ("prx", (control,), (-pi / 2, pi / 2)),
        ]
    if name == "cz":
        control, target = qubits
        if family == PRX_CZ:
            return [("cz", (control, target), ())]
        hadamard = [("prx", (target,), (pi / 2, pi / 2)), ("prx", (target,), (pi, 0.0))]
        return [*hadamard, *_lower_instruction("cnot", qubits, operator, family), *hadamard]
    if name == "swap":
        a, b = qubits
        return [
            *_lower_instruction("cnot", (a, b), operator, family),
            *_lower_instruction("cnot", (b, a), operator, family),
            *_lower_instruction("cnot", (a, b), operator, family),
        ]
    raise NotImplementedError(f"no native lowering for gate {name!r}")


def compile_to_native(circuit: Circuit, family: str) -> CompiledCircuit:
    """Lower a logical circuit to the given native family on the same qubit labels.

    Args:
        circuit: Circuit using the logical gate set described in the module docstring.
        family: ``prx-cz`` (IQM) or ``prx-xx`` (AQT).

    Returns:
        The native circuit and its gate statistics.

    Raises:
        NotImplementedError: If a gate has no lowering or the family is unknown.
    """
    if family not in (PRX_CZ, PRX_XX):
        raise NotImplementedError(f"unknown native family {family!r}")

    operations: list[_Op] = []
    for instruction in circuit.instructions:
        operator = instruction.operator
        if not isinstance(operator, Gate) and not is_feed_forward(operator):
            raise NotImplementedError(f"cannot lower non-gate instruction {operator}")
        qubits = tuple(int(qubit) for qubit in instruction.target)
        operations.extend(_lower_instruction(operator.name.lower(), qubits, operator, family))

    pending: dict[int, float] = {}
    native = Circuit()
    counts: dict[str, int] = {"prx": 0, "rz": 0, "cz": 0, "xx": 0, "cc_prx": 0, "measure_ff": 0}

    def flush(qubit: int) -> None:
        phase = pending.pop(qubit, 0.0)
        if abs(phase) > 1e-12:
            native.rz(qubit, phase)
            counts["rz"] += 1

    for kind, qubits, angles in operations:
        if kind == "rz":
            pending[qubits[0]] = pending.get(qubits[0], 0.0) + angles[0]
        elif kind == "prx":
            theta, phi = angles
            native.prx(qubits[0], theta, phi - pending.get(qubits[0], 0.0))
            counts["prx"] += 1
        elif kind == CCPRX:
            theta, phi, key = angles
            with experimental_capabilities():
                native.cc_prx(qubits[0], theta, phi - pending.get(qubits[0], 0.0), int(key))
            counts["cc_prx"] += 1
        elif kind == MEASURE_FF:
            pending.pop(qubits[0], None)
            with experimental_capabilities():
                native.measure_ff(qubits[0], int(angles[0]))
            counts["measure_ff"] += 1
        elif kind == "cz":
            native.cz(*qubits)
            counts["cz"] += 1
        elif kind == "xx":
            for qubit in qubits:
                flush(qubit)
            native.xx(qubits[0], qubits[1], angles[0])
            counts["xx"] += 1
        else:  # pragma: no cover - guarded by _lower_instruction
            raise NotImplementedError(kind)

    dropped = {qubit: phase for qubit, phase in pending.items() if abs(phase) > 1e-12}
    return CompiledCircuit(
        circuit=native,
        family=family,
        gate_counts={name: count for name, count in counts.items() if count},
        two_qubit_gates=counts["cz"] + counts["xx"],
        dropped_final_z=dropped,
    )
