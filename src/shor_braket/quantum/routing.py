# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Place a logical circuit on a device graph and insert SWAPs where couplers are missing.

This is a deliberately small greedy router: gates are processed in program order, and when a
two-qubit gate spans non-adjacent physical qubits the first operand is walked along a shortest
path (swapping with whatever sits there) until it is adjacent. Layouts are chosen by trying every
assignment of the logical qubits onto compact neighbourhoods of the device and keeping the one
with the smallest estimated error budget. The Braket SDK ships no router, and the project does
not add a Qiskit conversion layer, so this is what the emulator report uses.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import permutations

from braket.circuits import Circuit, Gate, Instruction

from shor_braket.devices.calibration import CalibrationSummary

_LogicalOp = tuple[str, tuple[int, ...], float | None]
ONE_QUBIT_OPS_PER_CNOT = 4  # prx gates a CNOT costs on the target after lowering


@dataclass(frozen=True)
class RoutedCircuit:
    """Circuit on physical qubits with the layout that produced it."""

    circuit: Circuit
    initial_layout: dict[int, int]
    final_layout: dict[int, int]
    swap_count: int
    two_qubit_gates: int
    error_budget: float
    physical_qubits: tuple[int, ...] = field(default_factory=tuple)
    used_qubits: tuple[int, ...] = field(default_factory=tuple)


def adjacency(summary: CalibrationSummary) -> dict[int, set[int]]:
    """Undirected adjacency of the couplers that have calibration data."""
    graph: dict[int, set[int]] = {label: set() for label in summary.qubit_labels}
    for first, second in summary.edges:
        graph[first].add(second)
        graph[second].add(first)
    return graph


def shortest_path(graph: Mapping[int, set[int]], start: int, goal: int) -> list[int]:
    """Breadth-first shortest path between two physical qubits (inclusive)."""
    if start == goal:
        return [start]
    previous: dict[int, int | None] = {start: None}
    queue: deque[int] = deque([start])
    while queue:
        node = queue.popleft()
        for neighbour in sorted(graph[node]):
            if neighbour in previous:
                continue
            previous[neighbour] = node
            if neighbour == goal:
                path = [goal]
                while previous[path[-1]] is not None:
                    path.append(previous[path[-1]])  # type: ignore[arg-type]
                return path[::-1]
            queue.append(neighbour)
    raise ValueError(f"no path between physical qubits {start} and {goal}")


def _logical_ops(circuit: Circuit) -> list[_LogicalOp]:
    ops: list[_LogicalOp] = []
    for instruction in circuit.instructions:
        operator = instruction.operator
        if not isinstance(operator, Gate):
            raise ValueError(f"cannot route non-gate instruction {operator}")
        qubits = tuple(int(qubit) for qubit in instruction.target)
        if len(qubits) > 2:
            raise ValueError("route circuits that use only one- and two-qubit gates")
        angle = float(operator.angle) if hasattr(operator, "angle") else None
        ops.append((operator.name.lower(), qubits, angle))
    return ops


def _route_ops(
    ops: Sequence[_LogicalOp], graph: Mapping[int, set[int]], layout: Mapping[int, int]
) -> tuple[list[_LogicalOp], dict[int, int], int]:
    """Route on the lightweight op representation; returns physical ops, final map, swaps."""
    mapping = dict(layout)
    occupant = {physical: logical for logical, physical in mapping.items()}
    routed: list[_LogicalOp] = []
    swaps = 0
    for name, qubits, angle in ops:
        if len(qubits) == 1:
            routed.append((name, (mapping[qubits[0]],), angle))
            continue
        a, b = qubits
        while mapping[b] not in graph[mapping[a]]:
            path = shortest_path(graph, mapping[a], mapping[b])
            here, there = path[0], path[1]
            routed.append(("swap", (here, there), None))
            swaps += 1
            other = occupant.get(there)
            mapping[a] = there
            occupant[there] = a
            if other is not None:
                mapping[other] = here
                occupant[here] = other
            else:
                del occupant[here]
        routed.append((name, (mapping[a], mapping[b]), angle))
    return routed, mapping, swaps


def estimate_error_budget(
    routed: Sequence[_LogicalOp], summary: CalibrationSummary, measured: Iterable[int]
) -> float:
    """Sum of calibration error rates the routed ops will touch (lower is better).

    Two-qubit gates add ``1 - f`` of their coupler (a SWAP counts three times), one-qubit gates
    add ``1 - f_rb`` of their qubit (a CNOT adds four one-qubit gates on its target after
    lowering), and each measured qubit adds its readout error.
    """
    budget = 0.0
    for name, qubits, _ in routed:
        if len(qubits) == 2:
            edge = summary.edge((qubits[0], qubits[1]))
            two_qubit_error = 1.0 - (edge.fidelity if edge else 0.0)
            if name == "swap":
                budget += 3 * two_qubit_error
                budget += 3 * ONE_QUBIT_OPS_PER_CNOT * _one_qubit_error(summary, qubits)
            else:
                budget += two_qubit_error
                budget += ONE_QUBIT_OPS_PER_CNOT * (1.0 - summary.qubits[qubits[1]].rb_fidelity)
        else:
            budget += 1.0 - summary.qubits[qubits[0]].rb_fidelity
    for qubit in measured:
        budget += summary.qubits[qubit].readout_flip_rate
    return budget


def _one_qubit_error(summary: CalibrationSummary, qubits: Sequence[int]) -> float:
    return sum(1.0 - summary.qubits[q].rb_fidelity for q in qubits) / len(qubits)


def neighbourhoods(graph: Mapping[int, set[int]], size: int) -> list[tuple[int, ...]]:
    """Compact connected sets of ``size`` physical qubits, one per breadth-first start."""
    found: set[tuple[int, ...]] = set()
    result: list[tuple[int, ...]] = []
    for start in sorted(graph):
        order: list[int] = [start]
        queue: deque[int] = deque([start])
        while queue and len(order) < size:
            node = queue.popleft()
            for neighbour in sorted(graph[node]):
                if neighbour not in order:
                    order.append(neighbour)
                    queue.append(neighbour)
                    if len(order) == size:
                        break
        if len(order) < size:
            continue
        key = tuple(sorted(order))
        if key not in found:
            found.add(key)
            result.append(tuple(order))
    return result


def _materialize(routed: Sequence[_LogicalOp], template: Circuit) -> Circuit:
    """Rebuild a Braket circuit from routed ops, reusing the original gate operators."""
    circuit = Circuit()
    originals = [
        instruction.operator
        for instruction in template.instructions
        if isinstance(instruction.operator, Gate)
    ]
    index = 0
    for name, qubits, _ in routed:
        if name == "swap" and (index >= len(originals) or originals[index].name.lower() != "swap"):
            circuit.swap(*qubits)
            continue
        circuit.add_instruction(Instruction(originals[index], list(qubits)))
        index += 1
    if index != len(originals):
        raise RuntimeError("routed gate count does not match the template")
    return circuit


def route_circuit(
    circuit: Circuit, summary: CalibrationSummary, layout: Mapping[int, int]
) -> RoutedCircuit:
    """Route a logical circuit with a fixed initial layout (logical -> physical)."""
    graph = adjacency(summary)
    ops = _logical_ops(circuit)
    routed, final, swaps = _route_ops(ops, graph, layout)
    two_qubit = sum(3 if name == "swap" else 1 for name, qubits, _ in routed if len(qubits) == 2)
    return RoutedCircuit(
        circuit=_materialize(routed, circuit),
        initial_layout=dict(layout),
        final_layout=final,
        swap_count=swaps,
        two_qubit_gates=two_qubit,
        error_budget=estimate_error_budget(routed, summary, final.values()),
        physical_qubits=tuple(sorted(final.values())),
        used_qubits=tuple(sorted({qubit for _, qubits, _ in routed for qubit in qubits})),
    )


def choose_layout(
    circuit: Circuit,
    summary: CalibrationSummary,
    logical_qubits: Sequence[int],
    *,
    max_permutations: int | None = None,
) -> RoutedCircuit:
    """Try every assignment of the logical qubits onto compact neighbourhoods; keep the cheapest.

    Args:
        circuit: Logical circuit.
        summary: Device calibration.
        logical_qubits: Logical qubits to place.
        max_permutations: Optional cap on assignments tried per neighbourhood (for tests).

    Returns:
        The routed circuit with the smallest estimated error budget (fewest SWAPs on ties).
    """
    graph = adjacency(summary)
    ops = _logical_ops(circuit)
    best: tuple[float, int, dict[int, int]] | None = None
    for region in neighbourhoods(graph, len(logical_qubits)):
        for count, assignment in enumerate(permutations(region)):
            if max_permutations is not None and count >= max_permutations:
                break
            layout = dict(zip(logical_qubits, assignment, strict=True))
            routed, final, swaps = _route_ops(ops, graph, layout)
            budget = estimate_error_budget(routed, summary, final.values())
            candidate = (budget, swaps, layout)
            if best is None or (budget, swaps) < (best[0], best[1]):
                best = candidate
    if best is None:
        raise ValueError("the device has no connected region large enough for the circuit")
    return route_circuit(circuit, summary, best[2])


def relabel(circuit: Circuit, mapping: Mapping[int, int]) -> Circuit:
    """Apply a qubit relabelling to a circuit without routing (for verification only)."""
    result = Circuit()
    for instruction in circuit.instructions:
        result.add_instruction(
            Instruction(instruction.operator, [mapping[int(q)] for q in instruction.target])
        )
    return result
