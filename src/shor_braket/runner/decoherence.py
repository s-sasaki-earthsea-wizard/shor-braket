# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Add the decoherence the emulator leaves out, and see how much of the hardware gap it explains.

The SDK's emulator derives its noise from randomized-benchmarking fidelities and a symmetric
readout flip. On the first real Garnet run (2026-09-23, N = 15, t = 2, 3000 shots) it predicted a
signal fraction of 0.564 and the device delivered 0.272 +- 0.012. The device also leaned towards
0: work values with one set bit came out more often than those with three, and ``y = 00`` more
often than ``y = 11``. That is the signature of relaxation, which the emulator does not model.

This module adds two things the calibration snapshot already contains, and one it does not:

* **asymmetric readout** from ``readout_error_0_to_1`` / ``readout_error_1_to_0``. Measured on
  that run, it explains almost none of the gap;
* **T1 / T2 decay on idle qubits**, applied per time slice to every qubit the program uses that
  is not busy in that slice. Busy qubits are left alone because a gate's benchmarked fidelity
  already includes the decay during the gate, and charging it twice undershoots badly;
* **gate durations**, which the snapshot does not publish. They are an assumption, so the study
  runs a small set of named scenarios and reports the spread instead of one tuned number.

Two scenarios bracketed the t = 2 measurement: Ramsey ``T2`` with 20 / 40 ns gates, and echo
``T2`` with 40 / 80 ns gates. Choosing gate times and a T2 flavour from a small grid is two knobs,
so agreement with one run is suggestive, not proof. What the study does establish is the
direction and the order of magnitude: idle dephasing over a few hundred layers is the missing
term, and readout asymmetry is not.

The t = 3 run (2026-09-23, 3000 shots) then showed the model's limit. Its idle count qubit came
back with a low-bit visibility of 0.087 +- 0.018 against 0.25 from the lightest scenario, while
the orbit mass (0.424) matched that same scenario. No uniform choice of gate times fits both:
the idle qubit, which the router had parked on the physical qubit with the shortest Ramsey T2 in
the program, lost phase faster than its isolated T2 predicts. Neighbouring gates acting on an
idle spectator is the obvious candidate, and it is not in this model.

Only IQM snapshots carry the per-qubit T1 / T2 / readout-error fields this reads.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from braket.circuits import Circuit
from braket.devices import LocalSimulator
from braket.emulation.local_emulator import LocalEmulator
from numpy.typing import NDArray

from shor_braket.analysis.distribution import (
    expected_joint_probabilities,
    low_bit_visibility,
    orbit_mass,
    support_signal_fraction,
)
from shor_braket.classical.order import multiplicative_order
from shor_braket.devices.calibration import summarize_snapshot
from shor_braket.devices.snapshot import DEFAULT_SNAPSHOT_DIR, DeviceSnapshot, load_snapshot
from shor_braket.quantum.n15 import MODULUS, WORK_QUBIT_COUNT
from shor_braket.runner.emulator import build_emulator
from shor_braket.runner.n15 import reorder_probabilities
from shor_braket.runner.submit import build_submission_program

BACKEND = "braket_dm"
VERBATIM_MARKERS = frozenset({"StartVerbatimBox", "EndVerbatimBox"})
PROBABILITY_TOLERANCE = 1e-9


@dataclass(frozen=True)
class DecayScenario:
    """Assumed gate durations and which T2 to use. None of these come from the snapshot."""

    name: str
    one_qubit_seconds: float
    two_qubit_seconds: float
    t2_key: str
    """``T2`` (Ramsey, includes slow noise) or ``T2_echo`` (with a refocusing pulse)."""


# Four named assumptions. The first two bracketed the t = 2 measurement; the heavier two were
# needed to reach the t = 3 low-bit visibility (0.087), which no single scenario reproduces
# together with the orbit mass: the idle count qubit dephased faster than its Ramsey T2 says.
SCENARIOS: tuple[DecayScenario, ...] = (
    DecayScenario("ramsey-20-40ns", 20e-9, 40e-9, "T2"),
    DecayScenario("echo-40-80ns", 40e-9, 80e-9, "T2_echo"),
    DecayScenario("ramsey-40-80ns", 40e-9, 80e-9, "T2"),
    DecayScenario("echo-80-160ns", 80e-9, 160e-9, "T2_echo"),
)


@dataclass(frozen=True)
class QubitDecoherence:
    """What the snapshot says about one qubit's decay and readout."""

    t1: float
    t2: float | None
    t2_echo: float | None
    readout_0_to_1: float
    readout_1_to_0: float

    def t2_for(self, key: str) -> float:
        """Return the T2 flavour a scenario asks for.

        Raises:
            ValueError: If the flavour is unknown or the calibration did not report it for this
                qubit (Emerald's qubit 35 had no Ramsey T2 on 2026-09-23).
        """
        if key not in ("T2", "T2_echo"):
            raise ValueError(f"unknown T2 flavour {key!r}; expected 'T2' or 'T2_echo'")
        value = self.t2 if key == "T2" else self.t2_echo
        if value is None:
            raise ValueError(f"the calibration reports no {key} for this qubit")
        return value


def qubit_decoherence(snapshot: DeviceSnapshot) -> dict[int, QubitDecoherence]:
    """Read per-qubit T1, T2 and readout asymmetry from a snapshot.

    Args:
        snapshot: A calibration snapshot.

    Returns:
        The values, keyed by physical qubit.

    Raises:
        ValueError: If the snapshot does not carry the IQM provider fields.
    """
    provider = snapshot.capabilities.get("provider") or {}
    one_qubit = (provider.get("properties") or {}).get("one_qubit")
    if not one_qubit:
        raise ValueError(
            f"{snapshot.key} has no provider.properties.one_qubit block; the decoherence study "
            "reads IQM's per-qubit T1 / T2 / readout-error fields"
        )
    values: dict[int, QubitDecoherence] = {}
    for qubit, entry in one_qubit.items():
        values[int(qubit)] = QubitDecoherence(
            t1=float(entry["T1"]),
            t2=float(entry["T2"]) if "T2" in entry else None,
            t2_echo=float(entry["T2_echo"]) if "T2_echo" in entry else None,
            readout_0_to_1=float(entry["readout_error_0_to_1"]),
            readout_1_to_0=float(entry["readout_error_1_to_0"]),
        )
    return values


def damping_parameters(
    duration: float, qubit: QubitDecoherence, t2_key: str
) -> tuple[float, float]:
    """Amplitude- and phase-damping strengths for one qubit idling for ``duration`` seconds.

    Pure dephasing takes whatever part of ``1 / T2`` relaxation does not already account for,
    ``1 / T_phi = 1 / T2 - 1 / (2 T1)``, clipped at zero for qubits where T2 exceeds 2 T1 within
    the error bars.

    Args:
        duration: Idle time in seconds.
        qubit: The qubit's decay constants.
        t2_key: Which T2 to use.

    Returns:
        ``(gamma_amplitude, gamma_phase)`` for Braket's amplitude- and phase-damping channels.
    """
    gamma_amplitude = 1.0 - math.exp(-duration / qubit.t1)
    rate_phi = max(1.0 / qubit.t2_for(t2_key) - 1.0 / (2.0 * qubit.t1), 0.0)
    gamma_phase = 1.0 - math.exp(-2.0 * duration * rate_phi)
    return gamma_amplitude, gamma_phase


def strip_verbatim(program: Circuit) -> Circuit:
    """Return the body of a verbatim program, without the box markers."""
    return Circuit(
        [ins for ins in program.instructions if ins.operator.name not in VERBATIM_MARKERS]
    )


def idle_decay_circuit(
    body: Circuit,
    emulator: LocalEmulator,
    decoherence: dict[int, QubitDecoherence],
    scenario: DecayScenario,
) -> Circuit:
    """Add the emulator's gate noise and idle-qubit decay, slice by slice.

    Args:
        body: The native program without its verbatim box.
        emulator: The emulator whose gate noise model to keep.
        decoherence: Per-qubit decay constants.
        scenario: Assumed gate durations and T2 flavour.

    Returns:
        A noisy circuit for the density-matrix backend.
    """
    used = sorted({int(q) for ins in body.instructions for q in ins.target})
    noisy = Circuit()
    for _, instructions in sorted(body.moments.time_slices().items()):
        noisy.add(emulator.noise_model.apply(Circuit(instructions)))
        duration = max(
            scenario.two_qubit_seconds if len(ins.target) == 2 else scenario.one_qubit_seconds
            for ins in instructions
        )
        busy = {int(q) for ins in instructions for q in ins.target}
        for qubit in used:
            if qubit in busy:
                continue
            try:
                gamma_amplitude, gamma_phase = damping_parameters(
                    duration, decoherence[qubit], scenario.t2_key
                )
            except ValueError as error:
                raise ValueError(f"qubit {qubit}: {error}") from error
            noisy.amplitude_damping(qubit, gamma_amplitude).phase_damping(qubit, gamma_phase)
    return noisy


def register_distribution(
    circuit: Circuit, used: Sequence[int], register: Sequence[int]
) -> NDArray[np.float64]:
    """Exact distribution of the register qubits, with every other used qubit traced out."""
    result = LocalSimulator(BACKEND).run(circuit.probability(target=list(used)), shots=0).result()
    tensor = np.asarray(result.values[0], dtype=np.float64).reshape([2] * len(used))
    dropped = tuple(used.index(q) for q in used if q not in register)
    if dropped:
        tensor = tensor.sum(axis=dropped)
    kept = [q for q in used if q in register]
    return reorder_probabilities(tensor.reshape(-1), kept, list(register))


def apply_readout(
    probabilities: NDArray[np.float64],
    register: Sequence[int],
    decoherence: dict[int, QubitDecoherence] | None,
    symmetric_rates: dict[int, float] | None = None,
) -> NDArray[np.float64]:
    """Apply per-qubit readout confusion, asymmetric from the snapshot or symmetric as before.

    Args:
        probabilities: Distribution over ``register``, first qubit most significant.
        register: Physical qubits in the order of the distribution's bits.
        decoherence: Per-qubit asymmetric error rates; used when given.
        symmetric_rates: Per-qubit symmetric flip rates, used when ``decoherence`` is None.

    Returns:
        The distribution as the readout would report it.
    """
    tensor = np.asarray(probabilities, dtype=np.float64).reshape([2] * len(register))
    for axis, qubit in enumerate(register):
        if decoherence is not None:
            flip_up = decoherence[qubit].readout_0_to_1
            flip_down = decoherence[qubit].readout_1_to_0
        else:
            if symmetric_rates is None:
                raise ValueError("either decoherence or symmetric_rates is required")
            flip_up = flip_down = symmetric_rates[qubit]
        confusion = np.array([[1.0 - flip_up, flip_down], [flip_up, 1.0 - flip_down]])
        tensor = np.moveaxis(np.tensordot(confusion, tensor, axes=([1], [axis])), 0, axis)
    return tensor.reshape(-1)


def distribution_metrics(
    joint: NDArray[np.float64], *, count_qubit_count: int, base: int
) -> dict[str, float | None]:
    """The numbers the hardware comparison looks at, for one joint distribution."""
    expected = expected_joint_probabilities(
        modulus=MODULUS,
        base=base,
        count_qubit_count=count_qubit_count,
        work_qubit_count=WORK_QUBIT_COUNT,
    )
    support = expected > PROBABILITY_TOLERANCE
    table = np.asarray(joint).reshape(1 << count_qubit_count, 1 << WORK_QUBIT_COUNT)
    count_marginal = table.sum(axis=1)
    return {
        "signal_fraction_support_mass": support_signal_fraction(
            float(joint[support].sum()), float(support.mean())
        ),
        "orbit_mass": orbit_mass(
            joint, modulus=MODULUS, base=base, work_qubit_count=WORK_QUBIT_COUNT
        ),
        "low_bit_visibility": low_bit_visibility(
            joint,
            count_qubit_count=count_qubit_count,
            work_qubit_count=WORK_QUBIT_COUNT,
            order=multiplicative_order(base, MODULUS),
        ),
        "count_all_zero": float(count_marginal[0]),
        "count_all_one": float(count_marginal[-1]),
    }


def decoherence_study(
    *,
    device_key: str,
    oracle_mode: str,
    count_qubit_count: int = 2,
    base: int = 7,
    snapshot_dir: Path = DEFAULT_SNAPSHOT_DIR,
    scenarios: Sequence[DecayScenario] = SCENARIOS,
    max_permutations: int | None = None,
) -> dict[str, Any]:
    """Predict the hardware metrics under the emulator's model and under the added decay.

    The program is rebuilt exactly as a submission would build it, so the numbers belong to the
    circuit a validated record would name.

    Args:
        device_key: Logical device name (IQM only).
        oracle_mode: Which oracle to build.
        count_qubit_count: t.
        base: The base whose order is being found.
        snapshot_dir: Where committed calibration snapshots live.
        scenarios: Gate-time and T2 assumptions to try.
        max_permutations: Cap on the layout search, for fast tests.

    Returns:
        One row per model, with the program's size and the assumptions spelled out.
    """
    snapshot = load_snapshot(device_key, snapshot_dir)
    decoherence = qubit_decoherence(snapshot)
    emulator = build_emulator(snapshot)
    program = build_submission_program(
        device_key=device_key,
        oracle_mode=oracle_mode,
        count_qubit_count=count_qubit_count,
        base=base,
        snapshot_dir=snapshot_dir,
        max_permutations=max_permutations,
    )
    body = strip_verbatim(program.circuit)
    register = [*program.count_physical, *program.work_physical]
    used = sorted({int(q) for ins in body.instructions for q in ins.target})
    summary = summarize_snapshot(snapshot)
    symmetric = {q: summary.qubits[q].readout_flip_rate for q in register}

    gate_only = register_distribution(emulator.noise_model.apply(body.copy()), used, register)
    rows: list[dict[str, Any]] = [
        {
            "model": "emulator (RB depolarizing, symmetric readout)",
            "assumptions": None,
            **distribution_metrics(
                apply_readout(gate_only, register, None, symmetric),
                count_qubit_count=count_qubit_count,
                base=base,
            ),
        },
        {
            "model": "emulator + asymmetric readout",
            "assumptions": None,
            **distribution_metrics(
                apply_readout(gate_only, register, decoherence),
                count_qubit_count=count_qubit_count,
                base=base,
            ),
        },
    ]
    for scenario in scenarios:
        noisy = idle_decay_circuit(body, emulator, decoherence, scenario)
        rows.append(
            {
                "model": f"+ asymmetric readout + idle T1/T2 ({scenario.name})",
                "assumptions": {
                    "one_qubit_gate_ns": scenario.one_qubit_seconds * 1e9,
                    "two_qubit_gate_ns": scenario.two_qubit_seconds * 1e9,
                    "t2": scenario.t2_key,
                },
                **distribution_metrics(
                    apply_readout(
                        register_distribution(noisy, used, register), register, decoherence
                    ),
                    count_qubit_count=count_qubit_count,
                    base=base,
                ),
            }
        )
    two_qubit = sum(1 for ins in body.instructions if len(ins.target) == 2)
    return {
        "device": device_key,
        "oracle_mode": oracle_mode,
        "count_qubit_count": count_qubit_count,
        "calibration_updated_at": snapshot.calibration_updated_at,
        "register_physical": register,
        "used_qubits": used,
        "native_two_qubit": two_qubit,
        "depth": body.depth,
        "gate_durations_note": (
            "Gate durations are not in the calibration snapshot; the scenarios are assumptions "
            "and bracket the measurement rather than pin it."
        ),
        "models": rows,
    }
