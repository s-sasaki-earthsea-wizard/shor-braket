# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Static preflight prices for the approved QPU candidates."""

from dataclasses import asdict, dataclass
from decimal import Decimal


@dataclass(frozen=True)
class QpuCandidate:
    """A QPU allowed by the project design."""

    name: str
    arn: str
    region: str
    qubits: int
    min_shots: int
    max_shots: int
    price_per_task_usd: Decimal
    price_per_shot_usd: Decimal


QPU_CANDIDATES: dict[str, QpuCandidate] = {
    "garnet": QpuCandidate(
        name="IQM Garnet",
        arn="arn:aws:braket:eu-north-1::device/qpu/iqm/Garnet",
        region="eu-north-1",
        qubits=20,
        min_shots=1,
        max_shots=20_000,
        price_per_task_usd=Decimal("0.30"),
        price_per_shot_usd=Decimal("0.00145"),
    ),
    "emerald": QpuCandidate(
        name="IQM Emerald",
        arn="arn:aws:braket:eu-north-1::device/qpu/iqm/Emerald",
        region="eu-north-1",
        qubits=54,
        min_shots=1,
        max_shots=20_000,
        price_per_task_usd=Decimal("0.30"),
        price_per_shot_usd=Decimal("0.00160"),
    ),
    "ibex": QpuCandidate(
        name="AQT IBEX-Q1",
        arn="arn:aws:braket:eu-north-1::device/qpu/aqt/Ibex-Q1",
        region="eu-north-1",
        qubits=12,
        min_shots=1,
        max_shots=2_000,
        price_per_task_usd=Decimal("0.30"),
        price_per_shot_usd=Decimal("0.02350"),
    ),
}


def qpu_cost_estimates(shots: int, *, tasks: int = 1) -> list[dict[str, object]]:
    """Return task cost estimates for all approved QPU candidates."""
    if shots < 1:
        raise ValueError("shots must be positive")
    if tasks < 1:
        raise ValueError("tasks must be positive")

    estimates: list[dict[str, object]] = []
    for key, candidate in QPU_CANDIDATES.items():
        candidate_data = asdict(candidate)
        candidate_data["price_per_task_usd"] = str(candidate.price_per_task_usd)
        candidate_data["price_per_shot_usd"] = str(candidate.price_per_shot_usd)
        estimates.append(
            {
                "device": key,
                **candidate_data,
                "tasks": tasks,
                "shots_per_task": shots,
                "total_shots": tasks * shots,
                "shots_valid": candidate.min_shots <= shots <= candidate.max_shots,
                "estimated_cost_usd": str(
                    tasks
                    * (candidate.price_per_task_usd + shots * candidate.price_per_shot_usd)
                ),
            }
        )
    return estimates
