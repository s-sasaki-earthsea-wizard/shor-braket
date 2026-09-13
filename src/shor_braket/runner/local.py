# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Execute circuits with the Braket local state vector simulator."""

from braket.circuits import Circuit
from braket.devices import LocalSimulator
from braket.tasks import GateModelQuantumTaskResult


def run_local(circuit: Circuit, *, shots: int = 1000) -> GateModelQuantumTaskResult:
    """Run a circuit locally without AWS credentials or remote tasks.

    Args:
        circuit: Circuit to execute. Analytic runs require a result type.
        shots: Number of samples, or zero for an analytic result.

    Returns:
        The completed local simulation result.

    Raises:
        ValueError: If shots is negative.
    """
    if shots < 0:
        raise ValueError("shots must be non-negative")
    return LocalSimulator("braket_sv").run(circuit, shots=shots).result()
