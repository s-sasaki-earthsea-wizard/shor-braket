# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Matrix-based reference circuit for Shor order finding."""

from dataclasses import dataclass
from math import gcd

import numpy as np
from braket.circuits import Circuit
from numpy.typing import NDArray


@dataclass(frozen=True)
class ReferenceCircuit:
    """A local-only order-finding circuit and its register layout."""

    circuit: Circuit
    modulus: int
    base: int
    count_qubits: tuple[int, ...]
    work_qubits: tuple[int, ...]
    execution_class: str = "local-reference"
    qpu_eligible: bool = False


def _controlled_modular_multiplication(
    multiplier: int, *, modulus: int, work_qubit_count: int
) -> NDArray[np.complex128]:
    """Build a controlled permutation for ``x -> multiplier*x mod modulus``.

    Basis states outside the modular register domain are fixed so the extension remains unitary.
    """
    work_dimension = 1 << work_qubit_count
    matrix = np.zeros((2 * work_dimension, 2 * work_dimension), dtype=np.complex128)

    for control in range(2):
        for value in range(work_dimension):
            output = multiplier * value % modulus if control and value < modulus else value
            source_index = control * work_dimension + value
            target_index = control * work_dimension + output
            matrix[target_index, source_index] = 1
    return matrix


def _inverse_fourier_matrix(qubit_count: int) -> NDArray[np.complex128]:
    """Return the inverse discrete Fourier transform in big-endian basis order."""
    dimension = 1 << qubit_count
    indices = np.arange(dimension)
    phase = -2j * np.pi * np.outer(indices, indices) / dimension
    return np.asarray(np.exp(phase) / np.sqrt(dimension), dtype=np.complex128)


def build_reference_circuit(
    *, modulus: int = 15, base: int = 7, count_qubit_count: int = 8
) -> ReferenceCircuit:
    """Build a matrix-based QPE reference circuit.

    The dense unitary representation is intentionally local-only. A later QPU implementation
    must construct reversible arithmetic from gates instead of submitting these matrices.
    """
    if modulus <= 2:
        raise ValueError("modulus must be greater than two")
    if not 1 < base < modulus:
        raise ValueError("base must satisfy 1 < base < modulus")
    if gcd(base, modulus) != 1:
        raise ValueError("base and modulus must be coprime")
    if count_qubit_count < 1:
        raise ValueError("count_qubit_count must be positive")

    work_qubit_count = (modulus - 1).bit_length()
    count_qubits = tuple(range(count_qubit_count))
    work_qubits = tuple(range(count_qubit_count, count_qubit_count + work_qubit_count))

    circuit = Circuit().x(work_qubits[-1])
    for qubit in count_qubits:
        circuit.h(qubit)

    for index, control in enumerate(count_qubits):
        exponent = 1 << (count_qubit_count - index - 1)
        multiplier = pow(base, exponent, modulus)
        matrix = _controlled_modular_multiplication(
            multiplier,
            modulus=modulus,
            work_qubit_count=work_qubit_count,
        )
        circuit.unitary(matrix=matrix, targets=[control, *work_qubits])

    circuit.unitary(matrix=_inverse_fourier_matrix(count_qubit_count), targets=count_qubits)
    return ReferenceCircuit(
        circuit=circuit,
        modulus=modulus,
        base=base,
        count_qubits=count_qubits,
        work_qubits=work_qubits,
    )
