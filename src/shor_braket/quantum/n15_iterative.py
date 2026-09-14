# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Iterative (Kitaev / semiclassical-QFT) order finding for N = 15 on one reused count qubit.

Round ``i`` (``i = 0 .. t-1``) prepares the count qubit in ``|+>``, applies the controlled
multiplication by ``a**(2**(t-1-i)) mod 15`` to the work register, corrects the phase by
``-2 pi (y_{i-1} ... y_0) / 2**(i+1)`` using the bits already measured, applies H and measures
bit ``y_i`` of the phase estimate ``y``. The correction and the reset that makes the count qubit
reusable are ``cc_prx`` gates conditioned on the earlier ``measure_ff`` results, and each result
is also copied into a record qubit because the hardware does not return mid-circuit outcomes.

The joint distribution of ``(y, work)`` is the same as for the standard circuit in ``n15.py``;
what changes is the hardware footprint: one count qubit next to the work register instead of
``t``, no inverse-QFT phases, and record qubits that need no coupler at all. The modular
multiplications are still the N = 15 swap network, so the hint level is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import gcd, pi

from braket.circuits import Circuit

from shor_braket.quantum.feedforward import (
    add_conditional_rz,
    add_conditional_x,
    experimental_capabilities,
    feed_forward_counts,
)
from shor_braket.quantum.n15 import (
    MODULUS,
    ORACLE_GENERIC_CONSTANT,
    ORACLE_MODES,
    WORK_QUBIT_COUNT,
    add_controlled_multiplication,
    two_qubit_gate_count,
)

METHOD = "iterative-qpe"


@dataclass(frozen=True)
class RoundRecord:
    """What one round of the iterative estimation did."""

    index: int
    exponent: int
    multiplier: int
    feedback_key: int | None
    corrections: tuple[tuple[int, float], ...]


@dataclass(frozen=True)
class IterativeCircuit:
    """Feed-forward order-finding circuit on logical qubits, plus its bookkeeping.

    ``count_qubits`` lists, most significant bit first, the qubits that hold the phase estimate
    at the end: the count qubit itself (last round) followed by the record qubits of the earlier
    rounds. ``record_qubits[i]`` holds bit ``i`` of ``y`` for ``i < t - 1``.
    """

    circuit: Circuit
    modulus: int
    base: int
    count_qubit: int
    record_qubits: tuple[int, ...]
    count_qubits: tuple[int, ...]
    work_qubits: tuple[int, ...]
    oracle_mode: str
    multipliers: tuple[int, ...]
    controlled_applications: tuple[tuple[int, int], ...]
    rounds: tuple[RoundRecord, ...]
    feedback_keys: tuple[int, ...]
    two_qubit_gates: int
    feed_forward: dict[str, int]
    method: str = METHOD
    execution_class: str = "qpu-logical-iterative"
    n_specific_decomposition: str = field(
        default=(
            "15 = 2^4 - 1: multiplication by 2^k is a bit rotation, "
            "by 15 - 2^k a rotation followed by NOT"
        )
    )

    @property
    def core_qubits(self) -> tuple[int, ...]:
        """Qubits that take part in two-qubit gates (count and work)."""
        return (self.count_qubit, *self.work_qubits)


def build_n15_iterative_circuit(
    *, base: int = 7, count_qubit_count: int = 2, oracle_mode: str = ORACLE_GENERIC_CONSTANT
) -> IterativeCircuit:
    """Build the iterative order-finding circuit for N = 15.

    Args:
        base: Base ``a`` coprime to 15.
        count_qubit_count: ``t``, the number of phase bits estimated (one per round). ``t = 2``
            represents the phases ``s/4`` exactly and, as for the standard circuit, uses the
            knowledge that ``r <= 4``; larger ``t`` adds identity multipliers for ``r = 4``.
        oracle_mode: ``generic-constant`` or ``generic-repeated`` (see ``n15.py``).

    Returns:
        The feed-forward circuit and its bookkeeping.
    """
    if not 1 < base < MODULUS or gcd(base, MODULUS) != 1:
        raise ValueError("base must be a unit modulo 15 greater than one")
    if count_qubit_count < 1:
        raise ValueError("count_qubit_count must be positive")
    if oracle_mode not in ORACLE_MODES:
        raise ValueError(f"oracle_mode must be one of {ORACLE_MODES}")

    count = 0
    work = tuple(range(1, 1 + WORK_QUBIT_COUNT))
    records = tuple(range(1 + WORK_QUBIT_COUNT, 1 + WORK_QUBIT_COUNT + count_qubit_count - 1))
    keys = tuple(range(count_qubit_count - 1))

    multipliers: list[int] = []
    applications: list[tuple[int, int]] = []
    rounds: list[RoundRecord] = []
    with experimental_capabilities():
        circuit = Circuit().x(work[-1])
        for index in range(count_qubit_count):
            exponent = 1 << (count_qubit_count - index - 1)
            constant = pow(base, exponent, MODULUS)
            multipliers.append(constant)
            circuit.h(count)
            if oracle_mode == ORACLE_GENERIC_CONSTANT:
                add_controlled_multiplication(circuit, count, work, constant)
                applications.append((count, constant))
            else:
                for _ in range(exponent):
                    add_controlled_multiplication(circuit, count, work, base)
                    applications.append((count, base))
            corrections: list[tuple[int, float]] = []
            for earlier in range(index):
                angle = -pi / 2 ** (index - earlier)
                add_conditional_rz(circuit, count, angle, keys[earlier])
                corrections.append((keys[earlier], angle))
            circuit.h(count)
            key: int | None = None
            if index < count_qubit_count - 1:
                key = keys[index]
                circuit.measure_ff(count, key)
                add_conditional_x(circuit, count, key)  # active reset for the next round
                add_conditional_x(circuit, records[index], key)  # keep the outcome readable
            rounds.append(
                RoundRecord(
                    index=index,
                    exponent=exponent,
                    multiplier=constant,
                    feedback_key=key,
                    corrections=tuple(corrections),
                )
            )

    return IterativeCircuit(
        circuit=circuit,
        modulus=MODULUS,
        base=base,
        count_qubit=count,
        record_qubits=records,
        count_qubits=(count, *reversed(records)),
        work_qubits=work,
        oracle_mode=oracle_mode,
        multipliers=tuple(multipliers),
        controlled_applications=tuple(applications),
        rounds=tuple(rounds),
        feedback_keys=keys,
        two_qubit_gates=two_qubit_gate_count(circuit),
        feed_forward=feed_forward_counts(circuit),
    )
