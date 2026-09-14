# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""QPU-oriented order-finding circuit for N = 15 built from one- and two-qubit gates.

The modular multiplications are the *N = 15 specific* swap network: because ``15 = 2**4 - 1``,
multiplying by ``2**k`` is a cyclic rotation of the four work bits, and multiplying by
``15 - 2**k`` is that rotation followed by a NOT on every bit. This is the ``generic-constant``
oracle of ``docs/02-architecture.md``: the constants ``a**(2**j) mod N`` are computed classically,
the order ``r`` is never used, but the arithmetic only collapses to swaps because N is 15.

Everything is expanded down to ``h``, ``x``, ``rz``, ``t``/``ti`` and ``cnot`` so that a native
compilation only has to know how to write those.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import gcd, pi

from braket.circuits import Circuit, Gate

MODULUS = 15
WORK_QUBIT_COUNT = 4
ORACLE_GENERIC_CONSTANT = "generic-constant"
ORACLE_GENERIC_REPEATED = "generic-repeated"
ORACLE_MODES = (ORACLE_GENERIC_CONSTANT, ORACLE_GENERIC_REPEATED)

# Multiplier -> (left rotation of the 4 work bits, NOT every bit afterwards)
_MULTIPLIER_TABLE: dict[int, tuple[int, bool]] = {
    1: (0, False),
    2: (1, False),
    4: (2, False),
    8: (3, False),
    14: (0, True),
    13: (1, True),
    11: (2, True),
    7: (3, True),
}


@dataclass(frozen=True)
class LogicalCircuit:
    """Order-finding circuit on logical qubits, plus the bookkeeping the report needs.

    ``count_qubits`` lists the count register in *value order* (most significant bit first) for
    reading the phase ``y``; because the inverse QFT omits its final swaps this is the reverse of
    ``count_qubits_physical``, the order in which the qubits control ``U^(2^j)``.
    """

    circuit: Circuit
    modulus: int
    base: int
    count_qubits: tuple[int, ...]
    count_qubits_physical: tuple[int, ...]
    work_qubits: tuple[int, ...]
    oracle_mode: str
    multipliers: tuple[int, ...]
    controlled_applications: tuple[tuple[int, int], ...]
    two_qubit_gates: int
    execution_class: str = "qpu-logical"
    n_specific_decomposition: str = field(
        default=(
            "15 = 2^4 - 1: multiplication by 2^k is a bit rotation, "
            "by 15 - 2^k a rotation followed by NOT"
        )
    )


def rotation_swaps(bit_count: int, shift: int) -> list[tuple[int, int]]:
    """Transpositions implementing a cyclic left rotation by ``shift`` (bit 0 is the MSB)."""
    swaps: list[tuple[int, int]] = []
    seen: set[int] = set()
    for start in range(bit_count):
        if start in seen:
            continue
        cycle: list[int] = []
        index = start
        while index not in seen:
            seen.add(index)
            cycle.append(index)
            index = (index + shift) % bit_count
        for position in range(len(cycle) - 1, 0, -1):
            swaps.append((cycle[0], cycle[position]))
    return swaps


def add_toffoli(circuit: Circuit, a: int, b: int, c: int) -> Circuit:
    """Append CCNOT(a, b -> c) as the standard six-CNOT decomposition."""
    circuit.h(c)
    circuit.cnot(b, c)
    circuit.ti(c)
    circuit.cnot(a, c)
    circuit.t(c)
    circuit.cnot(b, c)
    circuit.ti(c)
    circuit.cnot(a, c)
    circuit.t(b)
    circuit.t(c)
    circuit.h(c)
    circuit.cnot(a, b)
    circuit.t(a)
    circuit.ti(b)
    circuit.cnot(a, b)
    return circuit


def add_fredkin(circuit: Circuit, control: int, first: int, second: int) -> Circuit:
    """Append a controlled swap as CNOT · CCNOT · CNOT (eight CNOTs)."""
    circuit.cnot(second, first)
    add_toffoli(circuit, control, first, second)
    circuit.cnot(second, first)
    return circuit


def add_controlled_phase(circuit: Circuit, control: int, target: int, angle: float) -> Circuit:
    """Append controlled-phase(angle) as Rz rotations around two CNOTs."""
    circuit.rz(control, angle / 2)
    circuit.rz(target, angle / 2)
    circuit.cnot(control, target)
    circuit.rz(target, -angle / 2)
    circuit.cnot(control, target)
    return circuit


def add_controlled_multiplication(
    circuit: Circuit, control: int, work: tuple[int, ...], multiplier: int
) -> Circuit:
    """Append controlled ``x -> multiplier * x mod 15`` on the four work qubits (MSB first)."""
    if len(work) != WORK_QUBIT_COUNT:
        raise ValueError("the N = 15 swap network needs exactly four work qubits")
    if multiplier not in _MULTIPLIER_TABLE:
        raise ValueError(f"multiplier {multiplier} is not a unit modulo 15")
    shift, negate = _MULTIPLIER_TABLE[multiplier]
    for first, second in rotation_swaps(WORK_QUBIT_COUNT, shift):
        add_fredkin(circuit, control, work[first], work[second])
    if negate:
        for qubit in work:
            circuit.cnot(control, qubit)
    return circuit


def add_inverse_qft(circuit: Circuit, qubits: tuple[int, ...]) -> Circuit:
    """Append the inverse QFT without the final bit-reversal swaps.

    ``qubits[0]`` holds the most significant bit of the input value. For each qubit from the most
    significant down, apply H and then the negative controlled phases from the less significant
    qubits. The output value is then read with the bit order reversed (``qubits[-1]`` is the most
    significant bit of the result); this matches ``reference._inverse_fourier_matrix`` and saves
    the swap network that a QFT with swaps would need.
    """
    count = len(qubits)
    for position in range(count):
        target = qubits[position]
        circuit.h(target)
        for later in range(position + 1, count):
            distance = later - position
            add_controlled_phase(circuit, qubits[later], target, -2 * pi / 2 ** (distance + 1))
    return circuit


def two_qubit_gate_count(circuit: Circuit) -> int:
    """Number of two-qubit gate instructions in a circuit."""
    return sum(
        1
        for instruction in circuit.instructions
        if isinstance(instruction.operator, Gate) and len(instruction.target) == 2
    )


def build_n15_circuit(
    *, base: int = 7, count_qubit_count: int = 2, oracle_mode: str = ORACLE_GENERIC_CONSTANT
) -> LogicalCircuit:
    """Build the QPE order-finding circuit for N = 15 from one- and two-qubit gates.

    Args:
        base: Base ``a`` coprime to 15.
        count_qubit_count: ``t``. Two count qubits represent the phases ``s/4`` exactly and avoid
            the identity multipliers that larger ``t`` produces for ``r = 4``; the choice uses
            knowledge that ``r <= 4`` and is recorded as such.
        oracle_mode: ``generic-constant`` applies one controlled multiplication by
            ``a**(2**j) mod 15`` per count qubit; ``generic-repeated`` applies controlled
            multiplication by ``a`` ``2**j`` times instead (same unitary, more gates).

    Returns:
        The logical circuit and its bookkeeping.
    """
    if not 1 < base < MODULUS or gcd(base, MODULUS) != 1:
        raise ValueError("base must be a unit modulo 15 greater than one")
    if count_qubit_count < 1:
        raise ValueError("count_qubit_count must be positive")
    if oracle_mode not in ORACLE_MODES:
        raise ValueError(f"oracle_mode must be one of {ORACLE_MODES}")

    count = tuple(range(count_qubit_count))
    work = tuple(range(count_qubit_count, count_qubit_count + WORK_QUBIT_COUNT))
    circuit = Circuit().x(work[-1])
    for qubit in count:
        circuit.h(qubit)

    multipliers: list[int] = []
    applications: list[tuple[int, int]] = []
    for index, control in enumerate(count):
        exponent = 1 << (count_qubit_count - index - 1)
        constant = pow(base, exponent, MODULUS)
        multipliers.append(constant)
        if oracle_mode == ORACLE_GENERIC_CONSTANT:
            add_controlled_multiplication(circuit, control, work, constant)
            applications.append((control, constant))
        else:
            for _ in range(exponent):
                add_controlled_multiplication(circuit, control, work, base)
                applications.append((control, base))
    add_inverse_qft(circuit, count)

    return LogicalCircuit(
        circuit=circuit,
        modulus=MODULUS,
        base=base,
        count_qubits=tuple(reversed(count)),
        count_qubits_physical=count,
        work_qubits=work,
        oracle_mode=oracle_mode,
        multipliers=tuple(multipliers),
        controlled_applications=tuple(applications),
        two_qubit_gates=two_qubit_gate_count(circuit),
    )
