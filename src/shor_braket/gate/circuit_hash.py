# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Hash a circuit from a normalized intermediate representation.

The validated record keyed by this hash is what lets a circuit reach a paid QPU task, so the
hash has to answer one question exactly: *is this the same program that the emulator accepted?*

OpenQASM text is not usable for that. Whitespace, comments and the SDK's formatting all change
the text without changing the program, and an SDK upgrade can rewrite the output wholesale. The
canonical form here is built from the SDK's own instruction objects instead:

* one entry per instruction, in circuit order, holding the operator name and its qubits;
* **target order is preserved**, because it carries meaning. ``cnot(0, 1)`` and ``cnot(1, 0)``
  are different programs, and sorting the targets would give them the same hash. (The sketch in
  ``docs/03-execution-gate.md`` §3.2 said ``sorted(targets)``; that would have collided.)
* angles rounded to :data:`ANGLE_DECIMALS` decimals, which absorbs float representation noise
  without merging angles this project can tell apart;
* compiler directives kept, so a verbatim box is part of the identity of the program;
* result types kept, since they decide what the device returns;
* **shots excluded**, so one validated circuit can be submitted at several shot counts. Shots are
  guarded separately, by the cost preflight.

Two circuits that differ only in the order of a control modifier's control qubits hash
differently. That is the safe direction: the gate re-validates rather than wrongly accepting.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from braket.circuits import Circuit, Instruction
from braket.circuits.result_type import ResultType

CANONICAL_FORM_VERSION = 1
ANGLE_DECIMALS = 12


def _normalize_number(value: object) -> object:
    """Round a float parameter and keep everything else recognizable.

    Integers pass through unchanged so that discrete parameters, such as the feedback key of
    ``measure_ff`` and ``cc_prx``, never look like an angle. Unbound free parameters fall back to
    their name.
    """
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        # ``+ 0.0`` collapses -0.0 onto 0.0; the two serialize differently otherwise.
        return round(value, ANGLE_DECIMALS) + 0.0
    return str(value)


def _control_state(instruction: Instruction) -> list[int] | str | None:
    """Return the control state of an instruction as plain data, or ``None`` when unset."""
    state = getattr(instruction, "control_state", None)
    if state is None:
        return None
    try:
        values = [int(bit) for bit in state]
    except (TypeError, ValueError):
        return str(state)
    return values or None


def _canonical_instruction(instruction: Instruction) -> dict[str, Any]:
    """Convert one instruction into its canonical dictionary."""
    operator = instruction.operator
    entry: dict[str, Any] = {
        "op": str(operator.name),
        "targets": [int(qubit) for qubit in instruction.target],
    }

    parameters = getattr(operator, "parameters", None)
    if parameters:
        entry["params"] = [_normalize_number(parameter) for parameter in parameters]

    controls = [int(qubit) for qubit in (getattr(instruction, "control", None) or [])]
    if controls:
        entry["controls"] = controls
        control_state = _control_state(instruction)
        if control_state is not None:
            entry["control_state"] = control_state

    power = getattr(instruction, "power", 1)
    if power != 1:
        entry["power"] = _normalize_number(power)

    return entry


def _canonical_result_type(result_type: ResultType) -> dict[str, Any]:
    """Convert one result type into its canonical dictionary."""
    entry: dict[str, Any] = {"type": type(result_type).__name__}
    targets = getattr(result_type, "target", None)
    if targets:
        entry["targets"] = [int(qubit) for qubit in targets]
    observable = getattr(result_type, "observable", None)
    if observable is not None:
        entry["observable"] = str(observable)
    return entry


def canonical_form(circuit: Circuit) -> dict[str, Any]:
    """Build the normalized representation that :func:`circuit_hash` hashes.

    Args:
        circuit: The circuit to normalize. It may be a verbatim program or a logical one.

    Returns:
        A JSON-serializable dictionary with the canonical form version, the instruction list and
        the result types. Shot count is deliberately absent.
    """
    return {
        "canonical_form_version": CANONICAL_FORM_VERSION,
        "instructions": [
            _canonical_instruction(instruction) for instruction in circuit.instructions
        ],
        "result_types": [
            _canonical_result_type(result_type) for result_type in circuit.result_types
        ],
    }


def canonical_json(circuit: Circuit) -> str:
    """Serialize the canonical form deterministically.

    Args:
        circuit: The circuit to normalize.

    Returns:
        Compact JSON with sorted keys, which is the exact byte string that gets hashed.
    """
    return json.dumps(
        canonical_form(circuit), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )


def circuit_hash(circuit: Circuit) -> str:
    """Hash the normalized representation of a circuit.

    Args:
        circuit: The circuit to identify.

    Returns:
        The digest as ``"sha256:<hex>"``.
    """
    return f"sha256:{hashlib.sha256(canonical_json(circuit).encode()).hexdigest()}"


def hash_digest(circuit_hash_value: str) -> str:
    """Strip the algorithm prefix so a hash can be used as a file name.

    Args:
        circuit_hash_value: A hash in the ``"sha256:<hex>"`` form.

    Returns:
        The hex digest on its own.

    Raises:
        ValueError: If the value is not a SHA-256 hash in the expected form.
    """
    prefix = "sha256:"
    if not circuit_hash_value.startswith(prefix):
        raise ValueError(f"expected a {prefix!r} hash, got {circuit_hash_value!r}")
    digest = circuit_hash_value[len(prefix) :]
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError(f"{circuit_hash_value!r} is not a lowercase hex SHA-256 digest")
    return digest
