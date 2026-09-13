# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Vendor-neutral calibration views built on the SDK's emulator property model.

IQM publishes per-qubit and per-edge calibration; AQT publishes device-level values that the
SDK expands to every qubit and pair. Reading through ``DeviceEmulatorProperties`` gives both the
same shape, which is also exactly what the local emulator's noise model consumes.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from statistics import median
from typing import Any

from braket.circuits.translations import BRAKET_GATES
from braket.emulation.device_emulator_properties import DeviceEmulatorProperties

from shor_braket.devices.snapshot import DeviceSnapshot

ONE_QUBIT_DEPOLARIZING_FACTOR = 1.5
TWO_QUBIT_DEPOLARIZING_FACTOR = 1.25
NOT_MODELLED = ("T1/T2 decay", "crosstalk", "leakage", "gate duration", "idle errors")


@dataclass(frozen=True)
class QubitCalibration:
    """Calibration of one physical qubit."""

    qubit: int
    rb_fidelity: float
    readout_fidelity: float
    t1_seconds: float | None
    t2_seconds: float | None

    @property
    def depolarizing_rate(self) -> float:
        """Depolarizing rate the emulator derives from the RB fidelity."""
        return (1.0 - self.rb_fidelity) * ONE_QUBIT_DEPOLARIZING_FACTOR

    @property
    def readout_flip_rate(self) -> float:
        """Bit-flip rate the emulator applies at measurement."""
        return 1.0 - self.readout_fidelity


@dataclass(frozen=True)
class EdgeCalibration:
    """Calibration of one coupler."""

    qubits: tuple[int, int]
    gate: str
    fidelity: float

    @property
    def depolarizing_rate(self) -> float:
        """Two-qubit depolarizing rate the emulator derives from the gate fidelity."""
        return (1.0 - self.fidelity) * TWO_QUBIT_DEPOLARIZING_FACTOR


@dataclass(frozen=True)
class CalibrationSummary:
    """Everything the emulation report needs to know about one device."""

    qubit_count: int
    native_gate_set: tuple[str, ...]
    fully_connected: bool
    qubit_labels: tuple[int, ...]
    edges: tuple[tuple[int, int], ...]
    qubits: dict[int, QubitCalibration]
    edge_fidelities: dict[tuple[int, int], EdgeCalibration]
    two_qubit_uniform: bool
    shots_range: tuple[int, int] | None
    price_per_shot_usd: float | None
    execution_windows: list[dict[str, str]]
    calibration_updated_at: str | None

    def edge(self, qubits: tuple[int, int]) -> EdgeCalibration | None:
        """Look up a coupler in either orientation."""
        return self.edge_fidelities.get(qubits) or self.edge_fidelities.get((qubits[1], qubits[0]))

    def is_adjacent(self, first: int, second: int) -> bool:
        """Return whether a two-qubit gate between the qubits is allowed verbatim."""
        return self.fully_connected or self.edge((first, second)) is not None

    def best_edge(self) -> tuple[int, int]:
        """Coupler with the highest two-qubit gate fidelity (first on ties)."""
        return max(self.edges, key=lambda edge: (self._fidelity(edge), -edge[0], -edge[1]))

    def worst_edge(self) -> tuple[int, int]:
        """Coupler with the lowest two-qubit gate fidelity (last on ties)."""
        return min(self.edges, key=lambda edge: (self._fidelity(edge), -edge[0], -edge[1]))

    def best_path(self, length: int) -> tuple[int, ...]:
        """Simple path of ``length`` qubits maximising the product of coupler fidelities."""
        if length < 2:
            raise ValueError("path length must be at least two")
        neighbours: dict[int, set[int]] = {label: set() for label in self.qubit_labels}
        for first, second in self.edges:
            neighbours[first].add(second)
            neighbours[second].add(first)

        best: tuple[float, tuple[int, ...]] | None = None

        def extend(path: tuple[int, ...], score: float) -> None:
            nonlocal best
            if len(path) == length:
                if best is None or score > best[0]:
                    best = (score, path)
                return
            for candidate in sorted(neighbours[path[-1]]):
                if candidate not in path:
                    extend((*path, candidate), score * self._fidelity((path[-1], candidate)))

        for start in self.qubit_labels:
            extend((start,), 1.0)
        if best is None:
            raise ValueError(f"the device has no simple path of {length} qubits")
        return best[1]

    def non_adjacent_pair(self) -> tuple[int, int] | None:
        """Two qubits that share no coupler, or None on a fully connected device."""
        if self.fully_connected:
            return None
        for first, second in combinations(self.qubit_labels, 2):
            if not self.is_adjacent(first, second):
                return (first, second)
        return None

    def statistics(self) -> dict[str, dict[str, float]]:
        """Min / median / max of the three calibration numbers the noise model uses."""
        rb = [q.rb_fidelity for q in self.qubits.values()]
        readout = [q.readout_fidelity for q in self.qubits.values()]
        two = [e.fidelity for e in self.edge_fidelities.values()]
        return {
            "one_qubit_rb_fidelity": _min_median_max(rb),
            "readout_fidelity": _min_median_max(readout),
            "two_qubit_gate_fidelity": _min_median_max(two),
        }

    def to_dict(self) -> dict[str, Any]:
        """Serialize for the run report."""
        return {
            "qubit_count": self.qubit_count,
            "native_gate_set": list(self.native_gate_set),
            "fully_connected": self.fully_connected,
            "qubit_labels": list(self.qubit_labels),
            "edges": [list(edge) for edge in self.edges],
            "one_qubit": {
                str(q.qubit): {
                    "rb_fidelity": q.rb_fidelity,
                    "readout_fidelity": q.readout_fidelity,
                    "t1_seconds": q.t1_seconds,
                    "t2_seconds": q.t2_seconds,
                    "depolarizing_rate": q.depolarizing_rate,
                    "readout_flip_rate": q.readout_flip_rate,
                }
                for q in self.qubits.values()
            },
            "two_qubit": {
                f"{e.qubits[0]}-{e.qubits[1]}": {
                    "gate": e.gate,
                    "fidelity": e.fidelity,
                    "depolarizing_rate": e.depolarizing_rate,
                }
                for e in self.edge_fidelities.values()
            },
            "two_qubit_uniform": self.two_qubit_uniform,
            "statistics": self.statistics(),
            "shots_range": list(self.shots_range) if self.shots_range else None,
            "price_per_shot_usd": self.price_per_shot_usd,
            "execution_windows": self.execution_windows,
            "calibration_updated_at": self.calibration_updated_at,
            "noise_model_rates": {
                "one_qubit_depolarizing": f"(1 - f_rb) * {ONE_QUBIT_DEPOLARIZING_FACTOR}",
                "readout_bit_flip": "1 - f_readout",
                "two_qubit_depolarizing": f"(1 - f_2q) * {TWO_QUBIT_DEPOLARIZING_FACTOR}",
            },
            "not_modelled": list(NOT_MODELLED),
        }

    def _fidelity(self, edge: tuple[int, int]) -> float:
        calibration = self.edge(edge)
        return calibration.fidelity if calibration else 0.0


def _min_median_max(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    return {"min": min(values), "median": float(median(values)), "max": max(values)}


def _rb_fidelity(entries: list[Any]) -> float:
    names = {entry.fidelityType.name: entry.fidelity for entry in entries}
    for name in ("RANDOMIZED_BENCHMARKING", "SIMULTANEOUS_RANDOMIZED_BENCHMARKING"):
        if name in names:
            return float(names[name])
    raise ValueError("no randomized benchmarking fidelity in one-qubit properties")


def _readout_fidelity(entries: list[Any]) -> float:
    for entry in entries:
        if entry.fidelityType.name == "READOUT":
            return float(entry.fidelity)
    raise ValueError("no READOUT fidelity in one-qubit properties")


def _coherence_seconds(value: Any) -> float | None:  # noqa: ANN401 - untyped SDK model
    if value is None:
        return None
    unit = str(getattr(value, "unit", "S")).upper()
    scale = {"S": 1.0, "MS": 1e-3, "US": 1e-6, "NS": 1e-9}.get(unit, 1.0)
    return float(value.value) * scale


def _edge_calibration(
    edge: tuple[int, int],
    data: Any,  # noqa: ANN401 - untyped SDK model
) -> EdgeCalibration:
    for entry in data.twoQubitGateFidelity:
        name = str(entry.gateName).lower()
        if name in BRAKET_GATES:
            return EdgeCalibration(qubits=edge, gate=name, fidelity=float(entry.fidelity))
    raise ValueError(f"no Braket-native two-qubit gate fidelity for edge {edge}")


def summarize_snapshot(snapshot: DeviceSnapshot) -> CalibrationSummary:
    """Reduce a snapshot to the numbers the emulator and the report use."""
    properties = DeviceEmulatorProperties.from_json(snapshot.capabilities_json)
    labels = tuple(properties.qubit_labels)

    qubits: dict[int, QubitCalibration] = {}
    for label, data in properties.one_qubit_properties.items():
        qubit = int(label)
        qubits[qubit] = QubitCalibration(
            qubit=qubit,
            rb_fidelity=_rb_fidelity(data.oneQubitFidelity),
            readout_fidelity=_readout_fidelity(data.oneQubitFidelity),
            t1_seconds=_coherence_seconds(getattr(data, "T1", None)),
            t2_seconds=_coherence_seconds(getattr(data, "T2", None)),
        )

    edge_fidelities: dict[tuple[int, int], EdgeCalibration] = {}
    for key, data in properties.two_qubit_properties.items():
        first, second = (int(part) for part in key.split("-"))
        edge = (min(first, second), max(first, second))
        edge_fidelities[edge] = _edge_calibration(edge, data)

    if properties.fully_connected:
        edges = tuple(combinations(labels, 2))
    else:
        undirected = {
            (min(int(node), int(peer)), max(int(node), int(peer)))
            for node, peers in properties.connectivity_graph.items()
            for peer in peers
        }
        edges = tuple(sorted(undirected))
    edges = tuple(edge for edge in edges if edge in edge_fidelities)

    fidelities = {e.fidelity for e in edge_fidelities.values()}
    service = snapshot.capabilities.get("service") or {}
    cost = service.get("deviceCost") or {}
    shots_range = service.get("shotsRange")
    return CalibrationSummary(
        qubit_count=int(properties.qubit_count),
        native_gate_set=tuple(str(gate) for gate in properties.native_gate_set),
        fully_connected=bool(properties.fully_connected),
        qubit_labels=labels,
        edges=edges,
        qubits=qubits,
        edge_fidelities=edge_fidelities,
        two_qubit_uniform=len(fidelities) <= 1,
        shots_range=(int(shots_range[0]), int(shots_range[1])) if shots_range else None,
        price_per_shot_usd=float(cost["price"]) if cost.get("unit") == "shot" else None,
        execution_windows=list(service.get("executionWindows") or []),
        calibration_updated_at=snapshot.calibration_updated_at,
    )
