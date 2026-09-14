# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Run and validate the local-only matrix reference circuit."""

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from fractions import Fraction
from importlib.metadata import version
from pathlib import Path
from typing import Any

import numpy as np
from braket.circuits.serialization import IRType
from numpy.typing import NDArray

from shor_braket.analysis.distribution import (
    EXACT_TVD_LIMIT,
    expected_joint_probabilities,
    noiseless_verdict,
    sampled_probability_vector,
    total_variation_distance,
)
from shor_braket.classical.order import multiplicative_order
from shor_braket.classical.postprocess import (
    factors_from_order,
    recover_factors,
    recover_orders,
)
from shor_braket.cost import qpu_cost_estimates
from shor_braket.quantum.reference import ReferenceCircuit, build_reference_circuit
from shor_braket.runner.local import run_local
from shor_braket.visualization import generate_walkthrough

EXACT_TVD_TOLERANCE = EXACT_TVD_LIMIT


def _circuit_hash(reference: ReferenceCircuit) -> str:
    program = reference.circuit.to_ir(ir_type=IRType.OPENQASM)
    return f"sha256:{hashlib.sha256(program.source.encode()).hexdigest()}"


def _support(
    probabilities: NDArray[np.float64],
    *,
    count_qubit_count: int,
    work_qubit_count: int,
    tolerance: float = 1e-12,
) -> list[dict[str, object]]:
    total_qubits = count_qubit_count + work_qubit_count
    work_dimension = 1 << work_qubit_count
    states: list[dict[str, object]] = []
    for index in np.flatnonzero(probabilities > tolerance):
        value = int(index)
        states.append(
            {
                "state": format(value, f"0{total_qubits}b"),
                "count_value": value // work_dimension,
                "work_value": value % work_dimension,
                "probability": float(probabilities[value]),
            }
        )
    return states


def _count_values(measurement_counts: Mapping[str, int], count_qubit_count: int) -> set[int]:
    return {int(state[:count_qubit_count], 2) for state in measurement_counts}


def _phase_candidates(
    count_values: set[int], *, modulus: int, base: int, count_qubit_count: int
) -> list[dict[str, Any]]:
    scale = 1 << count_qubit_count
    candidates: list[dict[str, Any]] = []
    for count_value in sorted(count_values):
        phase = Fraction(count_value, scale)
        approximation = phase.limit_denominator(modulus)
        orders = recover_orders(
            {count_value},
            modulus=modulus,
            base=base,
            count_qubits=count_qubit_count,
        )
        order = orders[0] if orders else None
        half_power = (
            pow(base, order // 2, modulus) if order is not None and order % 2 == 0 else None
        )
        factors = (
            factors_from_order(modulus=modulus, base=base, order=order)
            if order is not None
            else None
        )
        if count_value == 0:
            reason = "zero phase contains no period denominator"
        elif order is None:
            reason = "continued fraction did not yield a valid order"
        elif factors is not None:
            reason = "valid order yields two non-trivial gcd factors"
        elif order % 2:
            reason = "order is odd"
        elif half_power in (1, modulus - 1):
            reason = "half power is congruent to +1 or -1 modulo N"
        else:
            reason = "gcd step did not yield non-trivial factors"
        candidates.append(
            {
                "count_value": count_value,
                "binary": format(count_value, f"0{count_qubit_count}b"),
                "phase_fraction": str(phase),
                "continued_fraction": str(approximation),
                "denominator": approximation.denominator,
                "order_candidate": order,
                "half_power": half_power,
                "factorization": list(factors) if factors else None,
                "reason": reason,
            }
        )
    return candidates


def run_reference_simulation(
    *,
    modulus: int = 15,
    base: int = 7,
    count_qubit_count: int = 8,
    shots: int = 1000,
    output_dir: Path | None = Path("runs/raw"),
    visualize: bool = True,
) -> dict[str, Any]:
    """Run, validate, and visualize the local reference simulation."""
    if shots < 1:
        raise ValueError("shots must be positive")

    reference = build_reference_circuit(
        modulus=modulus,
        base=base,
        count_qubit_count=count_qubit_count,
    )
    analytic_circuit = reference.circuit.copy().probability(
        target=[*reference.count_qubits, *reference.work_qubits]
    )
    analytic_result = run_local(analytic_circuit, shots=0)
    actual_probabilities = np.asarray(analytic_result.values[0], dtype=np.float64)
    expected_probabilities = expected_joint_probabilities(
        modulus=modulus,
        base=base,
        count_qubit_count=count_qubit_count,
        work_qubit_count=len(reference.work_qubits),
    )
    exact_tvd = total_variation_distance(actual_probabilities, expected_probabilities)

    sample_result = run_local(reference.circuit, shots=shots)
    measurement_counts = dict(sample_result.measurement_counts)
    sampled_probabilities = sampled_probability_vector(
        measurement_counts,
        total_qubit_count=reference.circuit.qubit_count,
    )
    sampled_tvd = total_variation_distance(sampled_probabilities, expected_probabilities)
    verdict = noiseless_verdict(
        exact_tvd=exact_tvd,
        sampled_tvd=sampled_tvd,
        expected=expected_probabilities,
        shots=shots,
    )

    work_dimension = 1 << len(reference.work_qubits)
    exact_count_values = {
        int(index) // work_dimension for index in np.flatnonzero(actual_probabilities > 1e-12)
    }
    exact_orders, exact_factors = recover_factors(
        exact_count_values,
        modulus=modulus,
        base=base,
        count_qubit_count=count_qubit_count,
    )
    sampled_orders, sampled_factors = recover_factors(
        _count_values(measurement_counts, count_qubit_count),
        modulus=modulus,
        base=base,
        count_qubit_count=count_qubit_count,
    )

    circuit_hash = _circuit_hash(reference)
    created_at = datetime.now(UTC)
    classical_order = multiplicative_order(base, modulus)
    report: dict[str, Any] = {
        "schema_version": 2,
        "created_at": created_at.isoformat(),
        "execution": {
            "backend": "braket_sv",
            "class": reference.execution_class,
            "aws_requests": False,
            "estimated_cost_usd": "0.00",
            "shots": shots,
        },
        "problem": {
            "modulus": modulus,
            "base": base,
            "classical_order": classical_order,
            "factorization": list(exact_factors) if exact_factors else None,
            "quantum_contributed": exact_factors is not None,
        },
        "education": {
            "base": base,
            "modular_orbit": [
                {"exponent": exponent, "value": pow(base, exponent, modulus)}
                for exponent in range(classical_order + 1)
            ],
            "phase_scale": 1 << count_qubit_count,
            "phase_candidates": _phase_candidates(
                exact_count_values,
                modulus=modulus,
                base=base,
                count_qubit_count=count_qubit_count,
            ),
        },
        "circuit": {
            "circuit_hash": circuit_hash,
            "oracle": "matrix-reference",
            "qpu_eligible": reference.qpu_eligible,
            "qpu_ineligible_reason": "Dense unitary matrices are a local reference only.",
            "count_qubits": count_qubit_count,
            "work_qubits": len(reference.work_qubits),
            "total_qubits": reference.circuit.qubit_count,
            "depth": reference.circuit.depth,
            "instruction_count": len(reference.circuit.instructions),
        },
        "validation": {
            "distribution": "count-work-joint",
            "exact_tvd": exact_tvd,
            "exact_tvd_tolerance": EXACT_TVD_TOLERANCE,
            "passed": verdict["exact_passed"] and classical_order in exact_orders,
            "factoring_succeeded": exact_factors is not None,
            "exact_support": _support(
                actual_probabilities,
                count_qubit_count=count_qubit_count,
                work_qubit_count=len(reference.work_qubits),
            ),
            "recovered_orders": exact_orders,
        },
        "sampled": {
            "measurement_counts": dict(sorted(measurement_counts.items())),
            "joint_tvd": sampled_tvd,
            "sampling_floor": verdict["sampling_floor"],
            "joint_tvd_limit": verdict["sampled_limit"],
            "passed": verdict["sampled_passed"],
            "recovered_orders": sampled_orders,
            "factorization": list(sampled_factors) if sampled_factors else None,
        },
        "future_qpu_costs": {
            "price_checked_at": "2026-09-14",
            "source": "https://aws.amazon.com/braket/pricing/",
            "estimates": qpu_cost_estimates(shots),
        },
        "environment": {
            "amazon_braket_sdk": version("amazon-braket-sdk"),
            "amazon_braket_default_simulator": version("amazon-braket-default-simulator"),
            "numpy": version("numpy"),
            "matplotlib": version("matplotlib"),
        },
    }

    if output_dir is not None:
        run_id = f"local-n{modulus}-a{base}-{created_at:%Y%m%dT%H%M%S%fZ}-{circuit_hash[7:19]}"
        artifact_dir = output_dir / run_id
        artifact_dir.mkdir(parents=True, exist_ok=False)
        artifact_path = artifact_dir / "result.json"
        report["artifact_path"] = str(artifact_path)
        if visualize:
            report["visualizations"] = generate_walkthrough(report, artifact_dir)
        artifact_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    return report
